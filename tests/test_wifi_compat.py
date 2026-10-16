import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))
import wifi_compat


class WifiCompatibilityTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.target = pathlib.Path(self.temp.name) / "compat.conf"

    def test_adapter_scoped_idempotent_install_and_remove(self):
        for _ in range(2):
            wifi_compat.install("wlp2s0", self.target)
        text = self.target.read_text()
        self.assertIn("match-device=interface-name:wlp2s0", text)
        self.assertIn("wifi.scan-rand-mac-address=no", text)
        self.assertEqual(self.target.stat().st_mode & 0o777, 0o644)
        wifi_compat.remove(self.target)
        wifi_compat.remove(self.target)
        self.assertFalse(self.target.exists())

    def test_rejects_configuration_injection_and_globs(self):
        for interface in ("", "*", "wlan0;*", "../wlan0", "wlan0\n[device]", "a" * 16):
            with self.assertRaises(ValueError):
                wifi_compat.render(interface)

    def test_preserves_modified_or_unowned_configuration(self):
        for text in ("user configuration\n", wifi_compat.render("wlp2s0") + "# user change\n"):
            self.target.write_text(text)
            for operation in (lambda: wifi_compat.install("wlp2s0", self.target),
                              lambda: wifi_compat.remove(self.target)):
                with self.assertRaises(RuntimeError):
                    operation()
            self.assertEqual(self.target.read_text(), text)

    def test_refuses_symlink(self):
        other = self.target.parent / "other"
        other.write_text("preserve")
        self.target.symlink_to(other)
        with self.assertRaises(RuntimeError):
            wifi_compat.install("wlp2s0", self.target)
        with self.assertRaises(RuntimeError):
            wifi_compat.remove(self.target)
        self.assertEqual(other.read_text(), "preserve")

    def test_does_not_silently_change_selected_adapter(self):
        wifi_compat.install("wlp2s0", self.target)
        with self.assertRaises(RuntimeError):
            wifi_compat.install("wlan0", self.target)
        self.assertEqual(self.target.read_text(), wifi_compat.render("wlp2s0"))
