"""Unified setup retains network prerequisites and fails before portal mutation."""
import importlib.util
from pathlib import Path
import subprocess
import sys
import unittest
from unittest import mock

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location('release_setup', SCRIPTS / 'setup.py')
setup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup)


class SetupReadinessTest(unittest.TestCase):
    def test_network_and_native_media_dependencies_are_explicit(self):
        required = {'dnsmasq', 'networkmanager', 'libnm', 'wpa_supplicant', 'polkit',
                    'iptables', 'pipewire', 'pipewire-pulse', 'xdg-desktop-portal', 'opus', 'libvpx'}
        self.assertTrue(required <= set(setup.PACKAGES))

    def test_privileged_stage_failure_does_not_configure_portal(self):
        status = {'ready': False, 'issues': ['setup needed'], 'missing_packages': []}
        calls = []
        def run(argv, **kwargs):
            calls.append(argv)
            if 'scripts/install-p2p-helper' in ' '.join(argv):
                raise subprocess.CalledProcessError(126, argv)
            return subprocess.CompletedProcess(argv, 0)
        with mock.patch.object(setup, 'readiness', return_value=status), \
             mock.patch.object(sys.stdin, 'isatty', return_value=True), \
             mock.patch('builtins.input', return_value='y'), \
             mock.patch.object(setup, 'run', side_effect=run), \
             mock.patch.object(setup, 'configure') as configure:
            with self.assertRaises(subprocess.CalledProcessError):
                setup.install()
        configure.assert_not_called()
        self.assertLess(next(i for i, c in enumerate(calls) if c[0].endswith('build-backend')),
                        next(i for i, c in enumerate(calls) if 'install-p2p-helper' in ' '.join(c)))

    def test_wifi_compatibility_requires_explicit_adapter(self):
        status = {'ready': False, 'issues': [], 'missing_packages': []}
        with mock.patch.object(setup, 'readiness', return_value=status), \
             mock.patch.object(sys.stdin, 'isatty', return_value=True), \
             mock.patch('builtins.input', return_value='y'), \
             mock.patch.object(setup, 'run') as run, mock.patch.object(setup, 'configure'):
            setup.install('wlan0')
        helper = next(c.args[0] for c in run.call_args_list if 'install-p2p-helper' in ' '.join(c.args[0]))
        self.assertEqual(helper[-2:], ['--wifi-compat-adapter', 'wlan0'])
