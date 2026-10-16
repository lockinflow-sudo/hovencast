import sys
from pathlib import Path
import unittest
from dataclasses import replace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cast_devices import device_from_receiver
from cast_identity import DiscoveryService, IdentityEvidence
from device_reconciliation import EndpointObservation, propose_mice_association
from mice_vendor import parse_mice_vendor_extensions
from omarchy_cast_protocol import Receiver


def attribute(kind, value):
    return kind.to_bytes(2, "big") + len(value).to_bytes(2, "big") + value


def payload(host=b"TEST123", address=b"192.0.2.10", caps=b"\x05"):
    return (b"\x00\x01\x37" + attribute(0x2001, caps)
            + attribute(0x2002, host) + attribute(0x2005, address))


class VendorParserTests(unittest.TestCase):
    def test_observed_shape_sanitized(self):
        result = parse_mice_vendor_extensions([payload()])
        self.assertEqual(result.hostname, "test123")
        self.assertEqual(result.addresses, ("192.0.2.10",))
        self.assertTrue(result.supported)

    def test_unknown_vendor_and_attribute(self):
        result = parse_mice_vendor_extensions([
            b"\x12\x34\x56ignored", payload() + attribute(0x9999, b"unknown")])
        self.assertTrue(result.supported)

    def test_missing_mandatory(self):
        for block in (b"\x00\x01\x37", b"\x00\x01\x37" + attribute(0x2001, b"\x05")):
            self.assertIsNone(parse_mice_vendor_extensions([block]))

    def test_ip_optional_in_parser(self):
        block = b"\x00\x01\x37" + attribute(0x2001, b"\x05") + attribute(0x2002, b"TEST123")
        self.assertEqual(parse_mice_vendor_extensions([block]).addresses, ())

    def test_invalid_hosts(self):
        for host in (b"", b"a.local", b"-a", b"a-", b"a b", b"a\x00", b"a_", b"\xff", b"a" * 64):
            with self.subTest(host=host):
                self.assertIsNone(parse_mice_vendor_extensions([payload(host=host)]))

    def test_invalid_addresses(self):
        for address in (b"", b"bad", b"0.0.0.0", b"127.0.0.1", b"224.0.0.1",
                        b"169.254.1.1", b"255.255.255.255", b"fe80::1", b"fe80::1%wlan0", b"\xff"):
            with self.subTest(address=address):
                self.assertIsNone(parse_mice_vendor_extensions([payload(address=address)]))

    def test_ipv6_and_duplicate_ips(self):
        result = parse_mice_vendor_extensions([
            payload(address=b"2001:db8::1") + attribute(0x2005, b"2001:db8::1")])
        self.assertEqual(result.addresses, ("2001:db8::1",))

    def test_truncation_never_partially_accepted(self):
        for tail in (b"x", b"\x20\x05\x00\x10short"):
            self.assertIsNone(parse_mice_vendor_extensions([payload() + tail]))

    def test_duplicate_mandatory_or_vendor_rejected(self):
        for extra in (attribute(0x2001, b"\x05"), attribute(0x2002, b"TEST123")):
            self.assertIsNone(parse_mice_vendor_extensions([payload() + extra]))
        self.assertIsNone(parse_mice_vendor_extensions([payload(), payload()]))

    def test_capability_size_and_support(self):
        self.assertIsNone(parse_mice_vendor_extensions([payload(caps=b"\x05\x05")]))
        for caps in (b"\x04", b"\x01", b"\x09"):
            self.assertFalse(parse_mice_vendor_extensions([payload(caps=caps)]).supported)

    def test_bounds(self):
        for blocks in ([b"x" * 4097], [b"x"] * 17, [b"x" * 4096] * 5,
                       [payload() + attribute(0x9999, b"") * 62],
                       [payload() + attribute(0x2005, b"192.0.2.10") * 8]):
            self.assertIsNone(parse_mice_vendor_extensions(blocks))

    def test_not_full_wsc_wrapper_or_wrong_input_type(self):
        self.assertIsNone(parse_mice_vendor_extensions([attribute(0x1049, payload())]))
        self.assertIsNone(parse_mice_vendor_extensions([bytearray(payload())]))


CONTAINER = "11111111-2222-3333-4444-555555555555"


def lan_observation(serial="TEST123", ip="192.0.2.10", device_id="DEVICE123"):
    receiver = Receiver(
        name="Same friendly name", address=ip, port=7236, interface="wlan0",
        protocol="miracast-mice",
        discovery_service=DiscoveryService("service", "_display._tcp", "local", serial + ".local"),
        identity_evidence=(
            IdentityEvidence("roku-serial-number", serial, "roku-ecp"),
            IdentityEvidence("roku-device-id", device_id, "roku-ecp"),
            IdentityEvidence("mice-container-id", CONTAINER, "mice-mdns")))
    return EndpointObservation(device_from_receiver(receiver).endpoints[0], "network-epoch-1", 95)


class AssociationTests(unittest.TestCase):
    def setUp(self):
        receiver = Receiver(name="Same friendly name", address="02:00:00:00:00:01",
                            port=7236, interface="p2p0", protocol="miracast-p2p")
        self.peer = EndpointObservation(device_from_receiver(receiver).endpoints[0], "network-epoch-1", 95)
        self.lan = lan_observation()
        self.advertisement = parse_mice_vendor_extensions([payload()])

    def match(self, lan=None, advertisement=None, peer=None, **kwargs):
        return propose_mice_association(peer or self.peer, advertisement or self.advertisement,
                                        [self.lan] if lan is None else lan, now=100, **kwargs)

    def test_fresh_exact_match(self):
        self.assertEqual(self.match().lan_endpoint_id, self.lan.endpoint.id)

    def test_reused_container_is_not_an_anchor_or_veto(self):
        other = lan_observation("OTHER456", "192.0.2.11", "DEVICE456")
        result = self.match([self.lan, other])
        self.assertEqual(result.lan_endpoint_id, self.lan.endpoint.id)
        self.assertEqual(result.colliding_container_ids, (CONTAINER,))
        second = parse_mice_vendor_extensions([payload(b"OTHER456", b"192.0.2.11")])
        self.assertEqual(self.match([self.lan, other], second).lan_endpoint_id, other.endpoint.id)

    def test_crossed_host_ip_does_not_match(self):
        other = lan_observation("OTHER456", "192.0.2.11", "DEVICE456")
        crossed = parse_mice_vendor_extensions([payload(b"TEST123", b"192.0.2.11")])
        self.assertIsNone(self.match([self.lan, other], crossed).lan_endpoint_id)

    def test_missing_or_unsupported_vendor(self):
        for advertisement in (None, parse_mice_vendor_extensions([payload(caps=b"\x04")])):
            result = propose_mice_association(self.peer, advertisement, [self.lan], now=100)
            self.assertIsNone(result.lan_endpoint_id)

    def test_names_and_mac_are_not_identity(self):
        wrong = parse_mice_vendor_extensions([payload(b"OTHER456", b"192.0.2.11")])
        self.assertIsNone(self.match(advertisement=wrong).lan_endpoint_id)

    def test_stale_future_and_network_scope(self):
        for observation in (replace(self.lan, observed_at=60), replace(self.lan, observed_at=101),
                            replace(self.lan, network_scope="network-epoch-2")):
            self.assertIsNone(self.match([observation]).lan_endpoint_id)
        self.assertIsNone(self.match(peer=replace(self.peer, observed_at=60)).lan_endpoint_id)

    def test_wrong_transport(self):
        self.assertIsNone(self.match(peer=self.lan).lan_endpoint_id)
        self.assertIsNone(self.match([self.peer]).lan_endpoint_id)

    def test_invalid_clocks(self):
        for age in (0, -1, float("nan"), float("inf")):
            self.assertIsNone(self.match(max_age=age).lan_endpoint_id)
        self.assertIsNone(propose_mice_association(self.peer, self.advertisement, [self.lan],
                                                  now=float("nan")).lan_endpoint_id)

    def test_reused_ecp_id_rejected_even_if_other_row_conflicting(self):
        other = lan_observation("OTHER456", "192.0.2.11", "DEVICE123")
        evidence = other.endpoint.identity_evidence + (
            IdentityEvidence("roku-device-id", "CONFLICT456", "roku-ecp"),)
        other = replace(other, endpoint=replace(other.endpoint, identity_evidence=evidence))
        self.assertEqual(self.match([self.lan, other]).reason, "reused-ecp-anchor")

    def test_udn_can_supply_second_anchor(self):
        endpoint = replace(self.lan.endpoint, identity_evidence=(
            IdentityEvidence("roku-serial-number", "TEST123", "roku-ecp"),
            IdentityEvidence("roku-udn", "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee", "roku-ecp")))
        self.assertEqual(self.match([replace(self.lan, endpoint=endpoint)]).lan_endpoint_id, endpoint.id)

    def test_host_without_ip_cannot_associate(self):
        self.assertIsNone(self.match(advertisement=replace(self.advertisement, addresses=())).lan_endpoint_id)

    def test_dns_host_case_and_trailing_dot(self):
        service = replace(self.lan.endpoint.discovery_service, hostname="test123.LOCAL.", domain="LOCAL.")
        endpoint = replace(self.lan.endpoint, discovery_service=service)
        self.assertEqual(self.match([replace(self.lan, endpoint=endpoint)]).lan_endpoint_id, endpoint.id)

    def test_conflicting_missing_invalid_or_untrusted_ecp(self):
        evidence = self.lan.endpoint.identity_evidence
        variants = ((), evidence[:1],
                    evidence + (IdentityEvidence("roku-serial-number", "OTHER456", "roku-ecp"),),
                    tuple(replace(e, provider="untrusted") for e in evidence),
                    (IdentityEvidence("roku-serial-number", "bad*id", "roku-ecp"), evidence[1]))
        for claims in variants:
            endpoint = replace(self.lan.endpoint, identity_evidence=claims)
            self.assertIsNone(self.match([replace(self.lan, endpoint=endpoint)]).lan_endpoint_id)

    def test_serial_hostname_disagreement(self):
        endpoint = replace(self.lan.endpoint, identity_evidence=(
            IdentityEvidence("roku-serial-number", "OTHER456", "roku-ecp"),
            IdentityEvidence("roku-device-id", "DEVICE123", "roku-ecp")))
        self.assertIsNone(self.match([replace(self.lan, endpoint=endpoint)]).lan_endpoint_id)

    def test_duplicate_locator_on_other_interface_is_ambiguous(self):
        other = replace(self.lan, endpoint=replace(self.lan.endpoint, id="another", interface="wlan1"))
        self.assertEqual(self.match([self.lan, other]).reason, "ambiguous-or-missing-locator")

    def test_identical_observation_dedup_but_conflicting_id_rejected(self):
        self.assertEqual(self.match([self.lan, self.lan]).lan_endpoint_id, self.lan.endpoint.id)
        other = replace(self.lan, endpoint=replace(self.lan.endpoint, address="192.0.2.11"))
        self.assertEqual(self.match([self.lan, other]).reason, "conflicting-endpoint-observations")

    def test_observation_bound(self):
        self.assertEqual(self.match([self.lan] * 257).reason, "excessive-observations")

    def test_service_provenance_and_type(self):
        service = self.lan.endpoint.discovery_service
        for modified in (None, replace(service, provider="unknown"),
                         replace(service, service_type="_other._tcp"), replace(service, domain="remote")):
            endpoint = replace(self.lan.endpoint, discovery_service=modified)
            self.assertIsNone(self.match([replace(self.lan, endpoint=endpoint)]).lan_endpoint_id)


if __name__ == "__main__":
    unittest.main()
