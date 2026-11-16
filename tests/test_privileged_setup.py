"""Missing/stale protected installation files must reopen reviewed setup."""
from contextlib import ExitStack, redirect_stdout
import copy
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
spec = importlib.util.spec_from_file_location("privileged_setup", SCRIPTS / "setup.py")
setup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup)
import read_p2p_metadata as reader


class PrivilegedSetupTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        shutil.copytree(SCRIPTS, self.root / "scripts")
        (self.root / "src").mkdir()
        (self.root / "src/wfd_sender.c").write_bytes(b"source")
        (self.root / "build").mkdir()
        binary = self.root / "build/wfd-sender"
        binary.write_text("helper")
        binary.chmod(0o755)
        (self.root / "build/wfd-sender.source.sha256").write_text(hashlib.sha256(b"source").hexdigest())
        (self.root / "hovencast-picker").touch()
        self.config = self.root / "portal.conf"
        self.state = self.root / "state"
        self.state.mkdir()
        picker = str(self.root / "hovencast-picker")
        self.config.write_text("screencopy {\n custom_picker_binary = " + picker + "\n}\n")
        (self.state / "portal.json").write_text(json.dumps({
            "installed_picker": picker, "config": str(self.config), "setup_complete": True}))
        self.expected = {
            name: {"uid": 0, "gid": 0, "mode": mode,
                   "sha256": hashlib.sha256((self.root / "scripts" / name).read_bytes()).hexdigest()}
            for name, _destination, mode in setup.INSTALLATION_FILES}
        self.installed = copy.deepcopy(self.expected)
        self.destinations = {destination: name for name, destination, _mode in setup.INSTALLATION_FILES}
        self.stack.enter_context(mock.patch.object(setup, "ROOT", self.root))
        self.stack.enter_context(mock.patch.object(setup, "missing_packages", return_value=[]))
        self.stack.enter_context(mock.patch.object(setup.portal_config, "config_path", return_value=self.config))
        self.stack.enter_context(mock.patch.object(setup.portal_config, "state_dir", return_value=self.state))
        self.stack.enter_context(mock.patch.object(setup, "installed_file_record", side_effect=self.file_record))
        self.probe = self.stack.enter_context(mock.patch.object(setup.subprocess, "run", side_effect=self.report))
        self.stack.enter_context(redirect_stdout(io.StringIO()))

    def file_record(self, destination):
        record = self.installed.get(self.destinations[destination])
        if record is None:
            raise FileNotFoundError(destination)
        return record

    def report(self, argv, **kwargs):
        self.assertEqual(argv[0:3], ["/usr/bin/pkexec", "--disable-internal-agent",
                                     "/usr/local/libexec/hovencast-metadata-reader"])
        self.assertEqual(argv[3:], [str(os.getpid()), "--check-installation"])
        self.assertEqual(kwargs["timeout"], 5)
        return subprocess.CompletedProcess(argv, 0, json.dumps({"version": 1, "installation": self.installed}), "")

    def test_each_missing_or_stale_file_requires_setup(self):
        for name in self.expected:
            for failure in (None, {**self.expected[name], "sha256": "stale"}):
                with self.subTest(file=name, failure=failure):
                    self.installed = copy.deepcopy(self.expected)
                    self.installed[name] = failure
                    self.assertFalse(setup.readiness()["ready"])

    def test_unsafe_ownership_and_permissions_require_setup(self):
        for name in self.expected:
            for change in ({"uid": 1000}, {"gid": 1000}, {"mode": 0o666}, {"mode": 0o4755}):
                with self.subTest(file=name, change=change):
                    self.installed = copy.deepcopy(self.expected)
                    self.installed[name].update(change)
                    self.assertFalse(setup.readiness()["ready"])

    def test_untrusted_reader_or_missing_deny_by_default_policy_is_not_invoked(self):
        for name in ("read_p2p_metadata.py", "hovencast-metadata-reader", "org.hovencast.metadata.policy"):
            with self.subTest(file=name):
                self.installed = copy.deepcopy(self.expected)
                self.installed[name] = None
                self.probe.reset_mock()
                self.assertFalse(setup.readiness()["ready"])
                self.probe.assert_not_called()

    def test_denied_timed_out_and_malformed_checks_require_setup(self):
        for response in (subprocess.CompletedProcess([], 127, "", "denied"),
                         subprocess.CompletedProcess([], 0, "not json", ""),
                         subprocess.CompletedProcess([], 0, "null", "")):
            with self.subTest(response=response):
                self.probe.side_effect = None
                self.probe.return_value = response
                self.assertFalse(setup.readiness()["ready"])
        self.probe.side_effect = subprocess.TimeoutExpired("pkexec", 5)
        self.assertFalse(setup.readiness()["ready"])

    def test_complete_install_is_ready_and_remains_a_no_op(self):
        self.assertTrue(setup.readiness()["ready"])
        with mock.patch.object(setup, "run") as mutation:
            self.assertEqual(setup.install(), 0)
        mutation.assert_not_called()

    def test_confirmed_setup_repairs_missing_and_stale_components(self):
        for failure in (None, {**self.expected["p2p_helper.py"], "sha256": "old"}):
            with self.subTest(failure=failure):
                self.installed = copy.deepcopy(self.expected)
                self.installed["p2p_helper.py"] = failure
                self.assertFalse(setup.readiness()["ready"])
                def install(argv, **kwargs):
                    if "scripts/install-p2p-helper" in " ".join(argv):
                        self.installed = copy.deepcopy(self.expected)
                    return subprocess.CompletedProcess(argv, 0)
                with mock.patch.object(sys.stdin, "isatty", return_value=True), \
                     mock.patch("builtins.input", return_value="y"), \
                     mock.patch.object(setup, "run", side_effect=install) as mutation:
                    self.assertEqual(setup.install(), 0)
                self.assertTrue(any("scripts/install-p2p-helper" in " ".join(c.args[0])
                                    for c in mutation.call_args_list))
                self.assertTrue(setup.readiness()["ready"])

    def test_declining_repair_leaves_installation_and_portal_unchanged(self):
        self.installed["49-hovencast-p2p.rules"] = None
        before = self.config.read_bytes()
        with mock.patch.object(sys.stdin, "isatty", return_value=True), \
             mock.patch("builtins.input", return_value="n"), mock.patch.object(setup, "run") as mutation:
            self.assertEqual(setup.install(), 0)
        mutation.assert_not_called()
        self.assertIsNone(self.installed["49-hovencast-p2p.rules"])
        self.assertEqual(self.config.read_bytes(), before)


class InstalledReaderChecks(unittest.TestCase):
    def test_file_record_hashes_contents_and_reports_actual_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "helper"
            path.write_bytes(b"installed source")
            path.chmod(0o644)
            self.assertEqual(reader.installed_file_record(path), {
                "uid": os.getuid(), "gid": os.getgid(), "mode": 0o644,
                "sha256": hashlib.sha256(b"installed source").hexdigest()})

    def test_symlink_directory_fifo_and_oversized_files_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            ordinary = base / "ordinary"
            ordinary.write_bytes(b"data")
            link = base / "link"
            link.symlink_to(ordinary)
            fifo = base / "fifo"
            os.mkfifo(fifo)
            large = base / "large"
            large.write_bytes(b"x" * (1024 * 1024 + 1))
            for path in (link, base, fifo, large):
                with self.subTest(path=path), self.assertRaises((OSError, ValueError)):
                    reader.installed_file_record(path)

    def test_report_reads_only_fixed_paths_and_marks_missing_files(self):
        with mock.patch.object(reader, "installed_file_record", side_effect=FileNotFoundError) as read:
            result = reader.installation_status()
        self.assertEqual([c.args[0] for c in read.call_args_list],
                         [destination for _name, destination, _mode in reader.INSTALLATION_FILES])
        self.assertEqual(result, {"version": 1, "installation": {
            name: None for name, _destination, _mode in reader.INSTALLATION_FILES}})

    def test_installation_mode_requires_an_authorized_live_caller(self):
        for argv in (["--check-installation"],
                     ["--caller", "123", "--check-installation", "/etc/shadow"]):
            with self.subTest(argv=argv), mock.patch.object(reader.os, "geteuid", return_value=0), \
                 mock.patch.dict(os.environ, {"PKEXEC_UID": "1000"}), \
                 mock.patch.object(reader, "caller_identity", return_value=(1000, "birth")), \
                 mock.patch.object(reader, "installation_status") as check:
                with self.assertRaises(ValueError):
                    reader.main(argv)
                check.assert_not_called()
        with mock.patch.object(reader.os, "geteuid", return_value=0), \
             mock.patch.dict(os.environ, {"PKEXEC_UID": "1000"}), \
             mock.patch.object(reader, "caller_identity", side_effect=[(1000, "birth"), (1000, "new-birth")]), \
             mock.patch.object(reader, "installation_status", return_value={}), \
             redirect_stdout(io.StringIO()) as output:
            with self.assertRaises(ValueError):
                reader.main(["--caller", "123", "--check-installation"])
            self.assertEqual(output.getvalue(), "")

    def test_installation_mode_needs_no_receiver_or_dbus_access(self):
        reply = {"version": 1, "installation": {}}
        with mock.patch.object(reader.os, "geteuid", return_value=0), \
             mock.patch.dict(os.environ, {"PKEXEC_UID": "1000"}), \
             mock.patch.object(reader, "caller_identity", return_value=(1000, "birth")), \
             mock.patch.object(reader, "installation_status", return_value=reply), \
             mock.patch.object(reader, "validate_request") as receiver, \
             redirect_stdout(io.StringIO()) as output:
            reader.main(["--caller", "123", "--check-installation"])
        receiver.assert_not_called()
        self.assertEqual(json.loads(output.getvalue()), reply)
