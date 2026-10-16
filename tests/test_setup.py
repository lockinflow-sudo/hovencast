"""Exercise setup against isolated configuration and fake system commands."""
import contextlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location("hovencast_setup", SCRIPTS / "setup.py")
setup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup)
portal = setup.portal_config


class SetupTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.root = self.base / "plugin with spaces"
        (self.root / "src").mkdir(parents=True)
        (self.root / "scripts").mkdir()
        (self.root / "src/wfd_sender.c").write_text("test source")
        (self.root / "hovencast-picker").write_text("#!/bin/bash\n")
        shutil.copy(SCRIPTS / "portal_config.py", self.root / "scripts/portal_config.py")
        self.env = mock.patch.dict(os.environ, {
            "XDG_CONFIG_HOME": str(self.base / "config"),
            "XDG_STATE_HOME": str(self.base / "state"),
        })
        self.env.start()
        self.root_patch = mock.patch.object(setup, "ROOT", self.root)
        self.root_patch.start()
        self.packages = mock.patch.object(setup, "missing_packages", return_value=[])
        self.packages.start()
        self.output = contextlib.redirect_stdout(io.StringIO())
        self.output.__enter__()

    def tearDown(self):
        self.output.__exit__(None, None, None)
        self.packages.stop()
        self.root_patch.stop()
        self.env.stop()
        self.temp.cleanup()

    def build(self):
        build = self.root / "build"
        build.mkdir(exist_ok=True)
        helper = build / "wfd-sender"
        helper.write_text("#!/bin/bash\n")
        helper.chmod(0o755)
        (build / "wfd-sender.source.sha256").write_text(
            hashlib.sha256((self.root / "src/wfd_sender.c").read_bytes()).hexdigest())

    def original(self):
        target = portal.config_path()
        target.parent.mkdir(parents=True)
        text = "# keep this\nscreencopy {\n    custom_picker_binary = /opt/my-picker\n    allow_token_by_default = false\n}\n"
        target.write_text(text)
        target.chmod(0o640)
        return text

    def test_check_is_read_only_and_detects_missing_setup(self):
        self.assertFalse(setup.readiness()["ready"])
        self.assertFalse((self.base / "config").exists())
        self.assertFalse((self.base / "state").exists())

    def test_completed_setup_and_changed_source_readiness(self):
        self.build()
        with mock.patch.object(setup, "restart_portals"):
            setup.configure()
        self.assertTrue(setup.readiness()["ready"])
        (self.root / "src/wfd_sender.c").write_text("new source")
        self.assertFalse(setup.readiness()["ready"])

    def test_cancel_leaves_configuration_and_packages_untouched(self):
        old = self.original()
        with mock.patch.object(sys.stdin, "isatty", return_value=True), \
                mock.patch("builtins.input", return_value="n"), \
                mock.patch.object(setup, "run") as run:
            self.assertEqual(setup.install(), 0)
        run.assert_not_called()
        self.assertEqual(portal.config_path().read_text(), old)
        self.assertFalse(portal.state_dir().exists())

    def test_noninteractive_setup_does_not_change_configuration(self):
        with mock.patch.object(sys.stdin, "isatty", return_value=False), \
                mock.patch.object(setup, "run") as run, \
                contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(setup.install(), 2)
        run.assert_not_called()
        self.assertFalse(portal.config_path().exists())

    def test_build_failure_does_not_change_portal(self):
        old = self.original()
        def command(argv, **kwargs):
            if argv[0] == str(self.root / "build-backend"):
                raise subprocess.CalledProcessError(1, argv)
            return subprocess.CompletedProcess(argv, 0)
        with mock.patch.object(sys.stdin, "isatty", return_value=True), \
                mock.patch("builtins.input", return_value="y"), \
                mock.patch.object(setup, "run", side_effect=command):
            with self.assertRaises(subprocess.CalledProcessError):
                setup.install()
        self.assertEqual(portal.config_path().read_text(), old)
        self.assertFalse(portal.state_dir().exists())

    def test_successful_guided_install_and_repeat_are_idempotent(self):
        self.original()
        def command(argv, **kwargs):
            if argv[0] == str(self.root / "build-backend"):
                self.build()
            return subprocess.CompletedProcess(argv, 0)
        with mock.patch.object(sys.stdin, "isatty", return_value=True), \
                mock.patch("builtins.input", return_value="y"), \
                mock.patch.object(setup, "run", side_effect=command):
            self.assertEqual(setup.install(), 0)
        with mock.patch.object(setup, "run") as run:
            self.assertEqual(setup.install(), 0)
        run.assert_not_called()
        self.assertTrue(setup.readiness()["ready"])

    def test_readiness_stays_false_until_services_finish(self):
        self.build()
        def restart():
            self.assertFalse(setup.readiness()["ready"])
        with mock.patch.object(setup, "restart_portals", side_effect=restart):
            setup.configure()
        self.assertTrue(setup.readiness()["ready"])

    def test_service_failure_restores_existing_config_and_mode(self):
        old = self.original()
        self.build()
        with mock.patch.object(setup, "restart_portals", side_effect=[RuntimeError("service failed"), None]):
            with self.assertRaises(RuntimeError):
                setup.configure()
        self.assertEqual(portal.config_path().read_text(), old)
        self.assertEqual(portal.config_path().stat().st_mode & 0o777, 0o640)
        self.assertFalse((portal.state_dir() / "portal.json").exists())

    def test_failure_removes_config_created_by_setup(self):
        self.build()
        with mock.patch.object(setup, "restart_portals", side_effect=[RuntimeError("service failed"), None]):
            with self.assertRaises(RuntimeError):
                setup.configure()
        self.assertFalse(portal.config_path().exists())

    def test_final_readiness_failure_rolls_back(self):
        old = self.original()
        with mock.patch.object(setup, "restart_portals"):
            with self.assertRaisesRegex(RuntimeError, "Build the wireless"):
                setup.configure()
        self.assertEqual(portal.config_path().read_text(), old)

    def test_failed_reinstall_keeps_previous_install_recoverable(self):
        old = self.original()
        self.build()
        with mock.patch.object(setup, "restart_portals"):
            setup.configure()
        before = (portal.state_dir() / "portal.json").read_bytes()
        with mock.patch.object(setup, "restart_portals", side_effect=[RuntimeError("failed"), None]):
            with self.assertRaises(RuntimeError):
                setup.configure()
        self.assertEqual((portal.state_dir() / "portal.json").read_bytes(), before)
        portal.remove()
        self.assertEqual(portal.config_path().read_text(), old)

    def test_concurrent_unrelated_config_edit_is_preserved_on_rollback(self):
        self.original()
        self.build()
        calls = 0
        def restart():
            nonlocal calls
            calls += 1
            if calls == 1:
                target = portal.config_path()
                target.write_text(target.read_text() + "# added during setup\n")
                raise RuntimeError("service failed")
        with mock.patch.object(setup, "restart_portals", side_effect=restart):
            with self.assertRaises(RuntimeError):
                setup.configure()
        text = portal.config_path().read_text()
        self.assertIn("/opt/my-picker", text)
        self.assertIn("# added during setup", text)
        self.assertNotIn(str(self.root / "hovencast-picker"), text)

    def test_concurrent_user_picker_change_is_preserved(self):
        self.original()
        self.build()
        def restart():
            target = portal.config_path()
            target.write_text(target.read_text().replace(str(self.root / "hovencast-picker"), "/opt/new-picker"))
            raise RuntimeError("service failed")
        with mock.patch.object(setup, "restart_portals", side_effect=restart), \
                contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(RuntimeError):
                setup.configure()
        self.assertIn("/opt/new-picker", portal.config_path().read_text())
        self.assertTrue((portal.state_dir() / "portal.json").exists())

    def test_recovery_survives_plugin_removal(self):
        old = self.original()
        self.build()
        with mock.patch.object(setup, "restart_portals"):
            setup.configure()
        recovery = portal.state_dir() / "restore-portal.py"
        shutil.rmtree(self.root)
        subprocess.run([sys.executable, str(recovery), "remove"], check=True, capture_output=True)
        self.assertEqual(portal.config_path().read_text(), old)

    def test_uninstall_preserves_new_user_picker_and_can_repeat(self):
        self.original()
        self.build()
        with mock.patch.object(setup, "restart_portals"):
            setup.configure()
        target = portal.config_path()
        target.write_text(target.read_text().replace(str(self.root / "hovencast-picker"), "/opt/new-picker"))
        with mock.patch.object(setup, "restart_portals"):
            self.assertEqual(setup.uninstall(), 0)
            self.assertEqual(setup.uninstall(), 0)
        self.assertIn("/opt/new-picker", target.read_text())

    def test_setup_lock_prevents_parallel_changes(self):
        import fcntl
        state = portal.state_dir()
        state.mkdir(parents=True)
        with (state / ".setup.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with mock.patch.object(sys, "argv", ["setup"]), \
                    mock.patch.object(setup, "install") as install, \
                    contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(setup.main(), 1)
            install.assert_not_called()


if __name__ == "__main__":
    unittest.main()


class SetupOutcomeTest(unittest.TestCase):
    setUp = SetupTest.setUp
    tearDown = SetupTest.tearDown
    def test_failed_setup_is_preserved_for_the_panel(self):
        with mock.patch.object(sys, 'argv', ['hovencast-setup']), \
             mock.patch.object(setup, 'install', side_effect=RuntimeError('helper approval cancelled')), \
             contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(setup.main(), 1)
        outcome = portal.state_dir() / 'last-setup.json'
        self.assertEqual(json.loads(outcome.read_text())['status'], 'failed')
        self.assertEqual(outcome.stat().st_mode & 0o777, 0o600)
        self.assertIn('Last setup failed: helper approval cancelled', setup.readiness()['issues'])
