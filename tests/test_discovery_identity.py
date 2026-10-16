"""Synthetic discovery identity fixtures; never probe receivers or pair Wi-Fi."""
import pathlib
import contextlib
import io
import json
import sys
import unittest
from dataclasses import replace
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from cast_devices import device_from_receiver, devices_from_receivers, receiver_from_device
from cast_identity import IdentityEvidence
from omarchy_cast_protocol import (
    Receiver, discover_mice_receivers, enrich_receiver_details,
    parse_mice_identity, parse_roku_device_info,
)


GUID = "015e5108-9000-1046-8035-b0a737964dfb"
OTHER_GUID = "12345678-1234-5678-9abc-123456789abc"


def device_info(extra="", resolution="720p"):
    return (f"<device-info><ui-resolution>{resolution}</ui-resolution>"
            f"<model-name>32S331</model-name><vendor-name>TCL</vendor-name>"
            f"{extra}</device-info>")


def avahi_line(txt="", name=r"32\034\032Roku\032TV", address="192.0.2.10"):
    return (f"=;wlan0;IPv4;{name};_display._tcp;local;roku.local;"
            f"{address};7250;{txt}\n").encode()


class DiscoveryIdentityTest(unittest.TestCase):
    def test_mice_container_guid_normalization_and_provenance(self):
        evidence = parse_mice_identity(f'"other=ignored" "container_id={{{GUID.upper()}}}"')
        self.assertEqual(evidence, (IdentityEvidence("mice-container-id", GUID, "mice-mdns"),))

    def test_decimal_escape_is_decoded_after_txt_tokenization(self):
        escaped = GUID.replace("-", r"\045")
        self.assertEqual(parse_mice_identity(f'"container_id={escaped}"')[0].value, GUID)

    def test_literal_backslash_cannot_be_reinterpreted_as_a_decimal_escape(self):
        escaped = GUID.replace("-", r"\\045")
        self.assertEqual(parse_mice_identity(f'"container_id={escaped}"'), ())
        self.assertEqual(parse_mice_identity(f'"container_id={GUID}" "note=a\\\"b;c"')[0].value, GUID)

    def test_missing_malformed_and_nil_container_ids_are_ignored(self):
        for txt in ("", '"model=Roku"', '"container_id="', '"container_id=not-a-guid"',
                    '"container_id=00000000-0000-0000-0000-000000000000"',
                    f'"container_id={GUID}', f'container_id={GUID}',
                    f'"container_id={GUID}" garbage',
                    f'"container_id={GUID}""container_id={OTHER_GUID}"',
                    '"container_id=' + "x" * 5000 + '"'):
            with self.subTest(txt=txt[:80]):
                self.assertEqual(parse_mice_identity(txt), ())

    def test_conflicting_txt_claims_remain_distinct(self):
        evidence = parse_mice_identity(
            f'"container_id={GUID}" "container_id={OTHER_GUID}" "container_id={GUID.upper()}"')
        self.assertEqual([claim.value for claim in evidence], [GUID, OTHER_GUID])

    def test_excessive_distinct_claims_do_not_become_a_truncated_identity(self):
        txt = " ".join(f'"container_id={index:08x}-1234-5678-9abc-123456789abc"'
                       for index in range(1, 11))
        self.assertEqual(parse_mice_identity(txt), ())

    @mock.patch("omarchy_cast_protocol._read_avahi_output")
    def test_service_identity_and_evidence_survive_legacy_discovery(self, output):
        output.return_value = avahi_line(f'"container_id={GUID}"')
        receiver = discover_mice_receivers()[0]
        service = receiver.discovery_service
        self.assertEqual((service.name, service.service_type, service.domain, service.hostname,
                          service.provider), ('32" Roku TV', "_display._tcp", "local",
                                              "roku.local", "mice-mdns"))
        self.assertEqual(receiver.identity_evidence[0].value, GUID)
        self.assertEqual(receiver.json(), {
            "name": '32" Roku TV', "address": "192.0.2.10", "port": 7250,
            "interface": "wlan0", "protocol": "miracast-mice",
            "native_width": None, "native_height": None, "model": "", "vendor": "",
        })

    @mock.patch("omarchy_cast_protocol._read_avahi_output")
    def test_bad_txt_does_not_remove_a_previously_usable_receiver(self, output):
        output.return_value = avahi_line('"container_id=bad"')
        receiver = discover_mice_receivers()[0]
        self.assertEqual(receiver.address, "192.0.2.10")
        self.assertEqual(receiver.identity_evidence, ())

    @mock.patch("omarchy_cast_protocol._read_avahi_output")
    def test_invalid_service_metadata_does_not_remove_receiver(self, output):
        output.return_value = avahi_line().replace(b"roku.local", b"roku\\010.local")
        receiver = discover_mice_receivers()[0]
        self.assertIsNone(receiver.discovery_service)
        self.assertEqual(receiver.address, "192.0.2.10")

    def test_roku_ids_are_typed_and_not_conflated_with_container_id(self):
        info = parse_roku_device_info(device_info(
            f"<udn>{GUID.upper()}</udn><serial-number>1GU48T017973</serial-number>"
            "<device-id>1GU48T017973</device-id>"))
        self.assertEqual(info["identity_evidence"], (
            IdentityEvidence("roku-udn", GUID, "roku-ecp"),
            IdentityEvidence("roku-serial-number", "1GU48T017973", "roku-ecp"),
            IdentityEvidence("roku-device-id", "1GU48T017973", "roku-ecp"),
        ))
        self.assertEqual((info["native_width"], info["native_height"]), (1280, 720))

    def test_missing_roku_ids_keep_original_metadata_shape(self):
        self.assertEqual(parse_roku_device_info(device_info()), {
            "native_width": 1280, "native_height": 720, "model": "32S331", "vendor": "TCL",
        })

    def test_bad_roku_ids_do_not_reject_usable_resolution(self):
        extras = (
            "<udn>bad</udn>", "<udn>00000000-0000-0000-0000-000000000000</udn>",
            "<device-id>unknown</device-id>", "<serial-number>0000</serial-number>",
            "<serial-number>bad id</serial-number>", "<device-id>bad\nvalue</device-id>",
            "<device-id>" + "a" * 129 + "</device-id>",
            "<serial-number><nested>ABC123</nested></serial-number>",
        )
        for extra in extras:
            with self.subTest(extra=extra):
                info = parse_roku_device_info(device_info(extra))
                self.assertNotIn("identity_evidence", info)
                self.assertEqual(info["native_height"], 720)

    def test_conflicting_xml_ids_are_preserved_not_first_wins(self):
        info = parse_roku_device_info(device_info(
            "<device-id>ABC123</device-id><device-id>XYZ789</device-id>"
            "<device-id>ABC123</device-id>"))
        self.assertEqual([claim.value for claim in info["identity_evidence"]], ["ABC123", "XYZ789"])

    def test_excessive_xml_claims_are_ignored_without_changing_compatibility(self):
        info = parse_roku_device_info(device_info(
            "".join(f"<device-id>ID{index}</device-id>" for index in range(10))))
        self.assertNotIn("identity_evidence", info)
        self.assertEqual(info["native_height"], 720)

    def test_case_of_opaque_roku_ids_is_not_silently_normalized(self):
        info = parse_roku_device_info(device_info("<device-id>AbC123</device-id>"))
        self.assertEqual(info["identity_evidence"][0].value, "AbC123")

    def test_identity_is_not_a_way_around_existing_resolution_filter(self):
        for payload in (device_info(f"<udn>{GUID}</udn>", "unknown"), "<device-info>"):
            self.assertEqual(parse_roku_device_info(payload), {})

    @mock.patch("omarchy_cast_protocol.probe_roku_device_info")
    @mock.patch("omarchy_cast_protocol._read_avahi_output")
    def test_discovery_enrichment_and_device_adapter_preserve_all_evidence(self, output, probe):
        output.return_value = avahi_line(f'"container_id={GUID}"')
        probe.return_value = parse_roku_device_info(device_info(
            f"<udn>{OTHER_GUID}</udn><serial-number>ABC123</serial-number>"))
        receiver = enrich_receiver_details(discover_mice_receivers())[0]
        device = device_from_receiver(receiver)
        self.assertEqual([claim.namespace for claim in device.endpoints[0].identity_evidence],
                         ["mice-container-id", "roku-udn", "roku-serial-number"])
        self.assertEqual(device.endpoints[0].discovery_service, receiver.discovery_service)
        self.assertEqual(receiver_from_device(device), receiver)
        self.assertNotIn("identity_evidence", receiver.json())
        self.assertNotIn("discovery_service", receiver.json())

    @mock.patch("omarchy_cast_protocol.probe_roku_device_info")
    def test_enrichment_without_ecp_ids_keeps_mdns_evidence(self, probe):
        evidence = parse_mice_identity(f'"container_id={GUID}"')
        receiver = Receiver("TV", "192.0.2.10", 7250, "wlan0", identity_evidence=evidence)
        probe.return_value = parse_roku_device_info(device_info())
        self.assertEqual(enrich_receiver_details([receiver])[0].identity_evidence, evidence)

    def test_claims_do_not_change_provisional_locator_ids_or_merge_devices(self):
        original = Receiver("TV", "192.0.2.10", 7250, "wlan0")
        observed = replace(original, identity_evidence=parse_mice_identity(f'"container_id={GUID}"'))
        devices = devices_from_receivers([observed, replace(observed, address="192.0.2.11")])
        self.assertEqual(len(devices), 2)
        self.assertNotEqual(devices[0].id, devices[1].id)
        self.assertEqual(device_from_receiver(original).id, devices[0].id)
        self.assertTrue(all(device.provisional for device in devices))

    def test_legacy_cli_payload_does_not_expose_identifiers(self):
        from omarchy_cast import run_discover

        observed = Receiver(
            "TV", "192.0.2.10", 7250, "wlan0",
            identity_evidence=(IdentityEvidence("roku-serial-number", "PRIVATE123", "roku-ecp"),),
        )
        capture = io.StringIO()
        with mock.patch("omarchy_cast.discover_mice_receivers", return_value=[observed]), \
             mock.patch("omarchy_cast.enrich_receiver_details", return_value=[observed]), \
             mock.patch("omarchy_cast.discover_p2p_receivers", return_value=[]), \
             mock.patch("chromecast_transport.discover_receivers", return_value=[]), \
             mock.patch("airplay_transport.discover_receivers", return_value=[]), \
             contextlib.redirect_stdout(capture):
            self.assertEqual(run_discover(None), 0)
        payload = capture.getvalue()
        self.assertEqual(json.loads(payload), {"receivers": [observed.json()]})
        self.assertNotIn("PRIVATE123", payload)


if __name__ == "__main__":
    unittest.main()
