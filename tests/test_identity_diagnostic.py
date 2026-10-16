import importlib.util
import json
from pathlib import Path
import sys
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
spec = importlib.util.spec_from_file_location("metadata_reader", ROOT / "scripts/read_p2p_metadata.py")
reader = importlib.util.module_from_spec(spec)
spec.loader.exec_module(reader)
from identity_diagnostic import decode_reply, network_snapshot, run
from omarchy_cast_protocol import Receiver
from cast_identity import DiscoveryService, IdentityEvidence

IFACE = "p2p-dev-wlan0"
MAC = "02:00:00:00:00:01"
PATH = reader.ROOT + "/Interfaces/1"
PEER = PATH + "/Peers/020000000001"
BLOCK = "00013720010001052002000754455354313233200500093139322e302e322e31"


class ReaderTests(unittest.TestCase):
    def make_call(self, peers=None, address=None, blocks=None):
        address = bytes.fromhex("020000000001") if address is None else address
        def call(path, interface, method, signature, args):
            if method == "GetInterface":
                return (PATH,)
            self.assertEqual(interface, reader.PROPERTIES)
            self.assertEqual(method, "Get")
            if args[1] == "Peers":
                return ([PEER] if peers is None else peers,)
            if args[1] == "DeviceAddress":
                return (address,)
            self.assertEqual(args[1], "VendorExtension")
            return ([bytes.fromhex(BLOCK)] if blocks is None else blocks,)
        return mock.Mock(side_effect=call)

    def test_selected_read_is_only_fixed_properties(self):
        call = self.make_call()
        result = reader.read_selected(call, IFACE, [MAC])
        self.assertEqual(result, {"version": 1, "interface": IFACE, "peers": {MAC: [BLOCK]},
                                  "observed_at": {MAC: None}})
        self.assertEqual(call.call_args_list[0].args[-1], ("wlan0",))
        self.assertEqual([c.args[4][-1] for c in call.call_args_list[1:]],
                         ["Peers", "DeviceAddress", "VendorExtension", "DeviceAddress"])

    def test_invalid_requests_rejected_before_dbus(self):
        for interface, selected in (("wlan0", [MAC]), ("p2p-dev-../../x", [MAC]),
                                    (IFACE, []), (IFACE, [MAC] * 2),
                                    (IFACE, ["invalid"]), (IFACE, [MAC] * 17)):
            call = self.make_call()
            with self.assertRaises(ValueError):
                reader.read_selected(call, interface, selected)
            call.assert_not_called()

    def test_no_unselected_metadata_reads(self):
        call = self.make_call(address=bytes.fromhex("020000000002"))
        self.assertEqual(reader.read_selected(call, IFACE, [MAC])["peers"], {})
        self.assertFalse(any(c.args[4][-1] == "VendorExtension" for c in call.call_args_list))

    def test_peer_outside_selected_interface(self):
        with self.assertRaises(ValueError):
            reader.read_selected(self.make_call(peers=[reader.ROOT + "/Interfaces/2/Peers/1"]), IFACE, [MAC])

    def test_peer_count_bound(self):
        with self.assertRaises(ValueError):
            reader.read_selected(self.make_call(peers=[PEER] * 129), IFACE, [MAC])

    def test_duplicate_selected_address(self):
        with self.assertRaises(ValueError):
            reader.read_selected(self.make_call(peers=[PEER, PEER + "2"]), IFACE, [MAC])

    def test_address_changes_during_read(self):
        call = self.make_call()
        original = call.side_effect
        reads = 0
        def changed(*args):
            nonlocal reads
            if args[-1][-1] == "DeviceAddress":
                reads += 1
                if reads == 2:
                    return (bytes.fromhex("020000000002"),)
            return original(*args)
        call.side_effect = changed
        with self.assertRaises(ValueError):
            reader.read_selected(call, IFACE, [MAC])

    def test_bounded_vendor_data(self):
        for blocks in ([b"x"] * 17, [b"x" * 4097], [b"x" * 4096] * 5,
                       [[-1]], [[256]], [[True]], "not-array"):
            with self.assertRaises(ValueError):
                reader.bounded_blocks(blocks)

    def test_empty_vendor_is_valid_absence(self):
        self.assertEqual(reader.read_selected(self.make_call(blocks=[]), IFACE, [MAC])["peers"], {MAC: []})

    def test_fixed_age_read_rounds_conservatively(self):
        response = mock.Mock(returncode=0, stdout=(MAC + "\nage=2\nsecret-not-exported=unused\n").encode())
        with mock.patch("subprocess.run", return_value=response) as command, \
             mock.patch.object(reader.time, "monotonic", return_value=100):
            self.assertEqual(reader.peer_observed_at(IFACE, MAC), 97)
            self.assertEqual(command.call_args.args[0],
                             ["/usr/bin/wpa_cli", "-i", "wlan0", "p2p_peer", MAC])

    def test_invalid_age_record_never_becomes_fresh(self):
        for text in ("FAIL", MAC + "\nage=-1\n", MAC + "\nage=0\nage=1\n",
                     "02:00:00:00:00:02\nage=0\n", MAC + "\n", "x" * 8193):
            with mock.patch("subprocess.run", return_value=mock.Mock(returncode=0, stdout=text.encode())):
                self.assertIsNone(reader.peer_observed_at(IFACE, MAC))

    def test_installed_reader_rejects_foreign_or_root_caller_before_bus_access(self):
        with mock.patch.object(reader.os, "geteuid", return_value=0), \
             mock.patch.object(reader, "caller_identity", return_value=(1234, "birth")):
            for uid in ("0", "9999"):
                with mock.patch.dict(reader.os.environ, {"PKEXEC_UID": uid}), self.assertRaises(ValueError):
                    reader.main(["--caller", "123", IFACE, MAC])

    def test_installed_reader_rejects_unprivileged_and_option_injection(self):
        for argv in (["--caller", "-I", IFACE, MAC], ["--caller", "123", IFACE, MAC]):
            with mock.patch.object(reader.os, "geteuid", return_value=1000), self.assertRaises(ValueError):
                reader.main(argv)

    def test_caller_identity_is_pid_and_birth_not_only_pid(self):
        import os
        self.assertEqual(reader.caller_identity(os.getpid())[0], os.getuid())
        for pid in (0, -1, 4194305):
            with self.assertRaises(ValueError):
                reader.caller_identity(pid)


class ReplyTests(unittest.TestCase):
    def reply(self, **fields):
        return json.dumps(dict(version=1, interface=IFACE, peers={MAC: [BLOCK]}, **fields)).encode()

    def test_decodes_sanitized_fixture(self):
        result, times = decode_reply(self.reply(), IFACE, [MAC])
        self.assertEqual(result[MAC].hostname, "test123")
        self.assertEqual(result[MAC].addresses, ("192.0.2.1",))

    def test_envelope_validation(self):
        data = json.loads(self.reply())
        for key, value in (("version", 2), ("interface", "wrong"),
                           ("peers", {"02:00:00:00:00:02": []}), ("peers", [])):
            with self.assertRaises(ValueError):
                decode_reply(json.dumps(dict(data, **{key: value})).encode(), IFACE, [MAC])

    def test_excessive_and_invalid_encoding(self):
        for blocks in ([BLOCK] * 17, ["00" * 4097], ["00" * 4096] * 5, [123], ["ZZ"]):
            with self.assertRaises(ValueError):
                decode_reply(json.dumps(dict(version=1, interface=IFACE, peers={MAC: blocks})).encode(), IFACE, [MAC])
        with self.assertRaises(ValueError):
            decode_reply(b" " * 540001, IFACE, [MAC])

    def test_malformed_vendor_and_missing_vendor_remain_unmatched(self):
        for blocks in ([], ["000137"]):
            result, _ = decode_reply(json.dumps(dict(version=1, interface=IFACE, peers={MAC: blocks})).encode(), IFACE, [MAC])
            self.assertIsNone(result[MAC])

    def test_timestamp_validation(self):
        for value in (-1, float("inf"), float("nan"), True, "100", time_future()):
            data = dict(version=1, interface=IFACE, peers={MAC: [BLOCK]}, observed_at={MAC: value})
            with self.assertRaises(ValueError):
                decode_reply(json.dumps(data).encode(), IFACE, [MAC])


class DiagnosticTests(unittest.TestCase):
    def test_network_snapshot_captures_activation_not_only_name(self):
        device = mock.Mock()
        device.get_iface.return_value = "wlan0"
        device.get_state.return_value = 100
        device.get_active_connection.return_value.get_uuid.return_value = "first"
        client = mock.Mock()
        client.get_devices.return_value = [device]
        first = network_snapshot(client)
        device.get_active_connection.return_value.get_uuid.return_value = "second"
        self.assertNotEqual(first, network_snapshot(client))

    def fake_link(self, seen=95):
        link = mock.Mock()
        receiver = Receiver("Test TV", "p2p:" + MAC, 7236, IFACE, protocol="miracast-p2p")
        link.discover.return_value = [receiver]
        peer = mock.Mock()
        peer.get_hw_address.return_value = MAC
        peer.get_last_seen.return_value = seen
        device = mock.Mock()
        device.get_iface.return_value = IFACE
        device.get_peers.return_value = [peer]
        link.devices.return_value = [device]
        return link

    def diagnostic(self, link, authorize=False, network_changed=False, response=None, lan=None):
        with mock.patch("identity_diagnostic.P2PLink", return_value=link), \
             mock.patch("identity_diagnostic.time.clock_gettime", return_value=100), \
             mock.patch("identity_diagnostic.time.monotonic", return_value=100), \
             mock.patch("identity_diagnostic.network_snapshot", side_effect=[(), (1,) if network_changed else ()]), \
             mock.patch("identity_diagnostic.discover_mice_receivers", return_value=[]), \
             mock.patch("identity_diagnostic.enrich_receiver_details", return_value=lan or []), \
             mock.patch("identity_diagnostic.subprocess.run", return_value=response or mock.Mock(returncode=1, stderr=b"")) as command:
            result = run(authorize)
            return result, command

    def test_unavailable_read_never_auto_escalates(self):
        result, command = self.diagnostic(self.fake_link())
        self.assertEqual(result["metadata_read"], "read-unavailable")
        self.assertEqual(command.call_args.args[0][0:2], ["/usr/bin/python", "-I"])
        self.assertIsNone(result["results"][0]["lan_receiver"])
        self.assertNotIn(MAC, json.dumps(result))

    def test_explicit_authorization_only(self):
        _, command = self.diagnostic(self.fake_link(), authorize=True)
        self.assertEqual(command.call_args.args[0][0], "/usr/bin/pkexec")

    def test_unknown_or_future_last_seen_excluded(self):
        for seen in (-1, 0, 101):
            result, command = self.diagnostic(self.fake_link(seen))
            self.assertEqual(result["fresh_peer_count"], 0)
            command.assert_not_called()

    def test_network_change_prevents_proposal(self):
        result, _ = self.diagnostic(self.fake_link(), network_changed=True)
        self.assertFalse(result["network_unchanged"])
        self.assertEqual(result["results"][0]["reason"], "network-changed")

    def successful_inputs(self, timestamp=95):
        response = mock.Mock(returncode=0, stdout=json.dumps(dict(
            version=1, interface=IFACE, peers={MAC: [BLOCK]}, observed_at={MAC: timestamp})).encode())
        lan = [Receiver("Test LAN TV", "192.0.2.1", 7236, "wlan0",
                        discovery_service=DiscoveryService("service", "_display._tcp", "local", "TEST123.local"),
                        identity_evidence=(IdentityEvidence("roku-serial-number", "TEST123", "roku-ecp"),
                                           IdentityEvidence("roku-device-id", "DEVICE123", "roku-ecp")))]
        return response, lan

    def test_live_pipeline_uses_supplicant_age_not_old_nm_timestamp(self):
        response, lan = self.successful_inputs()
        result, _ = self.diagnostic(self.fake_link(seen=60), response=response, lan=lan)
        self.assertEqual(result["results"][0]["lan_receiver"], "Test LAN TV")
        self.assertEqual(result["fresh_peer_count"], 1)

    def test_missing_or_stale_supplicant_age_does_not_match(self):
        for timestamp in (None, 60):
            response, lan = self.successful_inputs(timestamp)
            result, _ = self.diagnostic(self.fake_link(), response=response, lan=lan)
            self.assertIsNone(result["results"][0]["lan_receiver"])

    def test_network_change_suppresses_otherwise_valid_live_match(self):
        response, lan = self.successful_inputs()
        result, _ = self.diagnostic(self.fake_link(), network_changed=True, response=response, lan=lan)
        self.assertIsNone(result["results"][0]["lan_receiver"])
        self.assertEqual(result["results"][0]["reason"], "network-changed")


def time_future():
    import time
    return time.monotonic() + 10000


if __name__ == "__main__":
    unittest.main()
