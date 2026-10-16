"""Bundled helper verification must preserve existing installs on failure."""
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

spec = importlib.util.spec_from_file_location('cast_install', Path(__file__).resolve().parents[1] / 'scripts/install_chromecast_native.py')
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


class NativeInstallTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'bundle'
        self.data = Path(self.temp.name) / 'data'
        (self.root / 'linux-amd64').mkdir(parents=True)
        self.binary = self.root / 'linux-amd64/cast_sender'
        self.binary.write_bytes(b'test native binary')
        for name in ('source.patch', 'args.gn'):
            (self.root / name).write_text('build input')
        (self.root / 'licenses').mkdir()
        (self.root / 'licenses/LICENSE').write_text('test notice')
        (self.root / 'provenance.json').write_text(json.dumps({'binary_sha256': hashlib.sha256(self.binary.read_bytes()).hexdigest()}))
        target = self.data / 'hovencast'
        target.mkdir(parents=True)
        (target / 'cast_sender').write_bytes(b'working previous version')
        self.platform = mock.patch.object(installer.platform, 'machine', return_value='x86_64')
        self.platform.start()
        self.addCleanup(self.platform.stop)

    def test_checksum_mismatch_preserves_working_helper(self):
        self.binary.write_bytes(b'changed')
        with mock.patch.object(installer.subprocess, 'run') as run:
            with self.assertRaisesRegex(RuntimeError, 'checksum'):
                installer.install(self.root, self.data)
        run.assert_not_called()
        self.assertEqual((self.data / 'hovencast/cast_sender').read_bytes(), b'working previous version')

    def test_missing_abi_preserves_working_helper(self):
        result = subprocess.CompletedProcess([], 0, 'libavcodec.so.63 => not found', '')
        with mock.patch.object(installer.subprocess, 'run', return_value=result):
            with self.assertRaisesRegex(RuntimeError, 'matching system libraries'):
                installer.install(self.root, self.data)
        self.assertEqual((self.data / 'hovencast/cast_sender').read_bytes(), b'working previous version')

    def test_install_carries_matching_binary_and_rebuild_inputs(self):
        result = subprocess.CompletedProcess([], 0, 'all libraries resolved', '')
        with mock.patch.object(installer.subprocess, 'run', return_value=result):
            installer.install(self.root, self.data)
        target = self.data / 'hovencast'
        self.assertEqual((target / 'cast_sender').read_bytes(), self.binary.read_bytes())
        self.assertTrue((target / 'cast_sender').stat().st_mode & 0o111)
        self.assertTrue((target / 'cast-sender-source.patch').is_file())
        self.assertTrue((target / 'cast-sender-licenses/LICENSE').is_file())
