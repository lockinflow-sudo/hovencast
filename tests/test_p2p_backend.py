import pathlib
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
from p2p_backend import P2PLink, sink_device_info, async_call, GLib, NM


class P2PDiscoveryTest(unittest.TestCase):
    def test_accepts_available_fire_tv_sink(self):
        self.assertEqual(sink_device_info(bytes.fromhex("00000600111c440032")),
                         (True, 7236))

    def test_rejects_non_display_and_busy_sink(self):
        for data in (b"", bytes.fromhex("00000600901c440032"),
                     bytes.fromhex("00000600011c440032")):
            self.assertFalse(sink_device_info(data)[0])

    def test_rejects_truncated_subelement(self):
        self.assertEqual(sink_device_info(bytes.fromhex("00000600111c44")),
                         (False, 0))

    def test_skips_unknown_subelements(self):
        self.assertEqual(sink_device_info(bytes.fromhex("090001ff00000600111c440032")),
                         (True, 7236))

    def test_refuses_arbitrary_p2p_address_before_discovery(self):
        with mock.patch("p2p_backend.NM.Client.new"):
            link = P2PLink()
            with self.assertRaises(ValueError):
                link.connect("p2p:invalid")

    def test_cleanup_is_idempotent_after_failed_activation(self):
        with mock.patch("p2p_backend.NM.Client.new"):
            link = P2PLink()
            active = mock.Mock()
            active.get_state.return_value = NM.ActiveConnectionState.DEACTIVATED
            link.active = active
            link.close()
            link.close()
            link.client.deactivate_connection_async.assert_not_called()

    def test_missing_dhcp_dependency_fails_before_discovery(self):
        with mock.patch("p2p_backend.NM.Client.new"), mock.patch("p2p_backend.shutil.which", return_value=None):
            link = P2PLink()
            with mock.patch.object(link, "discover") as discover:
                with self.assertRaisesRegex(RuntimeError, "requires dnsmasq"):
                    link.connect("p2p:aa:bb:cc:dd:ee:ff")
                discover.assert_not_called()

    def test_missing_p2p_adapter_has_actionable_error(self):
        with mock.patch("p2p_backend.NM.Client.new"), mock.patch("p2p_backend.shutil.which", return_value="/usr/bin/dnsmasq"):
            link = P2PLink()
            with mock.patch.object(link, "devices", return_value=[]):
                with self.assertRaisesRegex(RuntimeError, "no Wi-Fi Direct adapter"):
                    link.connect("p2p:aa:bb:cc:dd:ee:ff")

    def test_failed_firewall_cleanup_still_deactivates_link(self):
        with mock.patch("p2p_backend.NM.Client.new"), mock.patch("p2p_backend.async_call") as call:
            link = P2PLink()
            link.active = mock.Mock()
            link.active.get_state.return_value = NM.ActiveConnectionState.ACTIVATED
            link.privilege = mock.Mock()
            link.privilege.close.side_effect = RuntimeError("cleanup failed")
            with self.assertRaisesRegex(RuntimeError, "cleanup failed"):
                link.close()
            call.assert_called_once()
            self.assertIsNone(link.privilege)
            self.assertIsNone(link.active)

    def test_async_error_is_propagated(self):
        def start(cancellable, callback, data):
            callback(None, "result", data)
        def finish(result):
            raise RuntimeError("pairing failed")
        with self.assertRaisesRegex(RuntimeError, "pairing failed"):
            async_call(start, finish)

    def test_cancelled_operation_cancels_dbus_request(self):
        cancellables = []
        def start(cancellable, callback, data):
            cancellables.append(cancellable)
        with self.assertRaises(InterruptedError):
            async_call(start, lambda r: None, cancelled=lambda: True)
        self.assertTrue(cancellables[0].is_cancelled())
