"""Pure domain-model tests: no network, desktop or running cast changes."""
import pathlib
import sys
import unittest
from dataclasses import FrozenInstanceError, replace

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from cast_devices import (
    CastTransport,
    IdentityEvidence,
    device_from_receiver,
    devices_from_receivers,
    receiver_from_device,
    receiver_from_endpoint,
)
from omarchy_cast_protocol import Receiver


class CastDeviceModelTest(unittest.TestCase):
    def setUp(self):
        self.roku = Receiver(
            name='32" Roku TV', address="192.0.2.10", port=7250,
            interface="wlan0", native_width=1280, native_height=720,
            model="32S331", vendor="TCL",
        )
        self.fire_tv = Receiver(
            name="Fire TV", address="p2p:aa:bb:cc:dd:ee:ff", port=7236,
            interface="p2p-dev-wlan0", protocol="miracast-p2p",
        )

    def test_known_protocol_family_has_separate_transports(self):
        lan = device_from_receiver(self.roku).endpoints[0]
        p2p = device_from_receiver(self.fire_tv).endpoints[0]
        self.assertEqual(lan.protocol, "miracast")
        self.assertEqual(p2p.protocol, "miracast")
        self.assertEqual(lan.transport, CastTransport.INFRASTRUCTURE)
        self.assertEqual(p2p.transport, CastTransport.WIFI_DIRECT)
        self.assertEqual(lan.legacy_protocol, "miracast-mice")
        self.assertEqual(p2p.legacy_protocol, "miracast-p2p")

    def test_all_legacy_fields_round_trip_without_json_changes(self):
        variants = (
            self.roku, self.fire_tv,
            replace(self.roku, native_width=3840, native_height=2160),
            replace(self.fire_tv, name="", model="", vendor=""),
            replace(self.roku, protocol="future-protocol", name="TV ☃"),
        )
        for original in variants:
            with self.subTest(receiver=original):
                before = original.json()
                device = device_from_receiver(original)
                restored = receiver_from_device(device)
                self.assertEqual(restored, original)
                self.assertEqual(restored.json(), before)
                self.assertEqual(original.json(), before)
                self.assertEqual(receiver_from_endpoint(device.endpoints[0]), original)

    def test_device_presentation_preserves_vendor_and_model(self):
        device = device_from_receiver(self.roku)
        self.assertEqual(device.name, self.roku.name)
        self.assertEqual(device.manufacturer, "TCL")
        self.assertEqual(device.model, "32S331")
        self.assertTrue(device.provisional)
        self.assertEqual(device.endpoints[0].identity_evidence, ())

    def test_same_name_lan_and_p2p_stay_separate(self):
        peers = [self.roku, replace(self.fire_tv, name=self.roku.name)]
        devices = devices_from_receivers(peers)
        self.assertEqual(len(devices), 2)
        self.assertNotEqual(devices[0].id, devices[1].id)
        self.assertEqual([receiver_from_device(d) for d in devices], peers)

    def test_endpoint_keys_include_protocol_port_and_interface(self):
        peers = [
            self.roku,
            replace(self.roku, protocol="other-protocol"),
            replace(self.roku, port=7251),
            replace(self.roku, interface="eth0"),
            replace(self.roku, address="192.0.2.11"),
        ]
        devices = devices_from_receivers(peers)
        self.assertEqual(len({d.id for d in devices}), len(peers))
        self.assertEqual(len({d.endpoints[0].id for d in devices}), len(peers))

    def test_order_duplicates_and_empty_input_are_preserved(self):
        peers = [self.fire_tv, self.roku, self.fire_tv]
        devices = devices_from_receivers(iter(peers))
        self.assertEqual([receiver_from_device(d) for d in devices], peers)
        self.assertEqual(devices[0], devices[2])
        self.assertEqual(devices_from_receivers([]), [])

    def test_keys_are_deterministic_but_not_physical_identity(self):
        first = device_from_receiver(self.roku)
        self.assertEqual(first, device_from_receiver(self.roku))
        renamed = device_from_receiver(replace(self.roku, name="Bedroom"))
        self.assertEqual(first.id, renamed.id)
        self.assertEqual(first.endpoints[0].id, renamed.endpoints[0].id)
        changed_ip = device_from_receiver(replace(self.roku, address="192.0.2.20"))
        self.assertNotEqual(first.id, changed_ip.id)
        self.assertTrue(changed_ip.provisional)

    def test_unknown_protocol_does_not_infer_transport_from_address(self):
        original = replace(self.fire_tv, protocol="google-cast")
        device = device_from_receiver(original)
        self.assertEqual(device.endpoints[0].protocol, "google-cast")
        self.assertEqual(device.endpoints[0].transport, CastTransport.UNKNOWN)
        self.assertEqual(receiver_from_device(device), original)

    def test_no_implicit_selection_with_zero_or_multiple_endpoints(self):
        device = device_from_receiver(self.roku)
        extra = device_from_receiver(self.fire_tv).endpoints[0]
        for endpoints in ((), (*device.endpoints, extra)):
            with self.subTest(endpoints=endpoints):
                with self.assertRaisesRegex(ValueError, "exactly one endpoint"):
                    receiver_from_device(replace(device, endpoints=endpoints))

    def test_display_edits_do_not_change_legacy_observation(self):
        device = replace(device_from_receiver(self.roku), name="My TV",
                         manufacturer="Display vendor", model="Display model")
        self.assertEqual(receiver_from_device(device), self.roku)

    def test_models_and_identity_evidence_are_immutable(self):
        device = device_from_receiver(self.roku)
        evidence = IdentityEvidence("mice-container-id", "example", "mice")
        for obj, field in ((device, "name"), (device.endpoints[0], "address"),
                           (evidence, "value")):
            with self.subTest(type=type(obj)):
                with self.assertRaises(FrozenInstanceError):
                    setattr(obj, field, "changed")
        self.assertIsInstance(device.endpoints, tuple)
        self.assertIsInstance(device.endpoints[0].identity_evidence, tuple)


if __name__ == "__main__":
    unittest.main()
