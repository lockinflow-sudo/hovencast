"""Subprocess first-run flow in a private home, with simulated packages/services.

Real Omarchy add/enable/remove/terminal commands, git clone, setup entry points,
media checks, and native compilation run in a bubblewrap namespace. Package
installation, shell IPC, and desktop services are simulated. No host desktop
or package state is changed. This is not a hardware/clean-VM acceptance test.
"""
import json
import os
from pathlib import Path
import pty
import select
import time
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
OMARCHY = Path('/usr/share/omarchy')
HOME_PATH = Path.home()
MISSING = ['ffmpeg', 'gcc', 'gst-plugins-ugly']

STUB = r'''#!/usr/bin/python
import json, os, pathlib, sys
root = pathlib.Path('/tmp/test')
name = pathlib.Path(sys.argv[0]).name
args = sys.argv[1:]
with (root/'commands.jsonl').open('a') as f:
    f.write(json.dumps([name, *args])+'\n')
if name == 'pacman':
    packages = set(json.loads((root/'packages.json').read_text()))
    if args == ['-Qq']:
        print('\n'.join(sorted(packages)))
    elif args and args[0] == '-Q':
        sys.exit(0 if all(p in packages for p in args[1:]) else 1)
    elif args and args[0] == '-S':
        if (root/'fail-package').exists(): sys.exit(1)
        packages.update(a for a in args[1:] if not a.startswith('-'))
        (root/'packages.json').write_text(json.dumps(sorted(packages)))
    else:
        print('Unexpected pacman operation', args, file=sys.stderr); sys.exit(1)
elif name == 'sudo':
    if not args or args[0] != 'pacman': sys.exit(1)
    os.execv('/tmp/test/bin/pacman', ['pacman', *args[1:]])
elif name == 'gum':
    if args and args[0] == 'confirm':
        if (root/'decline-install').exists(): sys.exit(1)
    elif args and args[0] == 'choose': print('right')
    else: sys.exit(1)
elif name == 'systemctl':
    if (root/'fail-service').exists() and any('xdg-desktop-portal' in arg for arg in args): sys.exit(1)
elif name == 'cc':
    if (root/'fail-build').exists(): sys.exit(1)
    os.execv('/usr/bin/cc', ['/usr/bin/cc', *args])
elif name == 'omarchy-shell':
    command = args[1]
    if command == 'listPlugins':
        plugins=[]
        for p in pathlib.Path.home().glob('.config/omarchy/plugins/*/manifest.json'):
            m=json.loads(p.read_text()); m['enabled']=(root/'enabled').exists(); plugins.append(m)
        print(json.dumps(plugins))
    elif command == 'enablePlugin':
        (root/'enabled').touch(); print('ok')
    elif command == 'setPluginEnabled':
        (root/'enabled').unlink(missing_ok=True); print('ok')
    elif command == 'rescanPlugins': print('ok')
    else: sys.exit(1)
elif name == 'omarchy-cmd-terminal-cwd':
    print(pathlib.Path.home())
elif name == 'uwsm-app':
    if args[0] == '--': args=args[1:]
    os.execvp(args[0], args)
elif name == 'xdg-terminal-exec':
    while args and (args[0].startswith('--dir=') or args[0]=='-e'): args=args[1:]
    os.execvp(args[0], args)
else: sys.exit(1)
'''


@unittest.skipUnless(shutil.which('bwrap') and OMARCHY.exists() and shutil.which('node'),
                     'requires the Omarchy host and bubblewrap')
class FirstRunFlowTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture_temp = tempfile.TemporaryDirectory(prefix='hovencast-source-', dir='/var/tmp')
        cls.fixture = Path(cls.fixture_temp.name)
        files = subprocess.check_output(['git', 'ls-files'], cwd=ROOT, text=True).splitlines()
        files += ['scripts/setup.py']
        for name in files:
            source = ROOT / name
            if not source.is_file(): continue
            target = cls.fixture / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        # Privileged helper installation/removal is simulated. The actual
        # plugin clone, terminal flow, compiler and portal transaction run below.
        for name in ('install-p2p-helper', 'device_cleanup.sh'):
            (cls.fixture / 'scripts' / name).write_text('#!/bin/bash\nexit 0\n')
        subprocess.run(['git', 'init', '-q', str(cls.fixture)], check=True)
        subprocess.run(['git', '-C', str(cls.fixture), 'add', '.'], check=True)
        subprocess.run(['git', '-C', str(cls.fixture), '-c', 'user.name=Test Fixture',
                        '-c', 'user.email=fixture@example.invalid', 'commit', '-qm',
                        'Unpublished public-release onboarding fixture'], check=True)
        cls.host_packages = subprocess.check_output(['pacman', '-Qq'], text=True).splitlines()

    @classmethod
    def tearDownClass(cls):
        cls.fixture_temp.cleanup()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='hovencast-first-run-', dir='/var/tmp')
        self.base = Path(self.temp.name)
        for name in ['home', 'bin', 'runtime']:
            (self.base/name).mkdir()
        (self.base/'runtime').chmod(0o700)
        for name in ['pacman', 'sudo', 'systemctl', 'cc', 'omarchy-shell',
                     'omarchy-cmd-terminal-cwd', 'uwsm-app', 'xdg-terminal-exec', 'gum']:
            p = self.base/'bin'/name
            p.write_text(STUB)
            p.chmod(0o755)
        (self.base/'packages.json').write_text(json.dumps(
            [p for p in self.host_packages if p not in MISSING]))
        self.plugin = HOME_PATH/'.config/omarchy/plugins/hoven.cast'
        self.local_plugin = self.base/'home/.config/omarchy/plugins/hoven.cast'
        self.config = self.base/'home/.config/hypr/xdph.conf'
        self.config.parent.mkdir(parents=True)
        self.original = '# user configuration\nscreencopy {\n    custom_picker_binary = /opt/old-picker\n    allow_token_by_default = false\n}\n'
        self.config.write_text(self.original)
        self.config.chmod(0o640)

    def tearDown(self):
        self.temp.cleanup()

    def command(self, argv, answer=None, interactive=False):
        sandbox = ['bwrap', '--die-with-parent', '--unshare-all', '--ro-bind', '/', '/',
                   '--bind', str(self.base/'home'), str(HOME_PATH),
                   '--tmpfs', '/tmp', '--bind', str(self.base), '/tmp/test',
                   '--ro-bind', str(self.fixture), '/tmp/fixture', '--tmpfs', '/run', '--proc', '/proc', '--dev', '/dev',
                   '--chdir', str(HOME_PATH),
                   '--setenv', 'PATH', '/tmp/test/bin:/usr/share/omarchy/bin:/usr/bin',
                   '--setenv', 'OMARCHY_PATH', str(OMARCHY),
                   '--setenv', 'XDG_CONFIG_HOME', str(HOME_PATH/'.config'),
                   '--setenv', 'XDG_STATE_HOME', str(HOME_PATH/'.local/state'),
                   '--setenv', 'XDG_RUNTIME_DIR', '/tmp/test/runtime',
                   '--setenv', 'TMPDIR', '/tmp',
                   '--unsetenv', 'DBUS_SESSION_BUS_ADDRESS',
                   '--unsetenv', 'WAYLAND_DISPLAY', '--unsetenv', 'DISPLAY',
                   *argv]
        if interactive:
            master, slave = pty.openpty()
            try:
                process = subprocess.Popen(sandbox, stdin=slave, stdout=slave, stderr=slave)
                os.close(slave)
                slave = None
                output = bytearray()
                deadline = time.monotonic() + 30
                while time.monotonic() < deadline:
                    if select.select([master], [], [], 0.1)[0]:
                        try:
                            chunk = os.read(master, 65536)
                        except OSError:
                            break
                        if not chunk: break
                        output.extend(chunk)
                    elif process.poll() is not None:
                        break
                if process.poll() is None:
                    process.kill()
                code = process.wait(timeout=2)
                return subprocess.CompletedProcess(argv, code, output.decode(errors="replace"), "")
            finally:
                os.close(master)
                if slave is not None: os.close(slave)
        if answer is None:
            return subprocess.run(sandbox, capture_output=True, text=True, timeout=30)
        master, slave = pty.openpty()
        try:
            process = subprocess.Popen(sandbox, stdin=slave, stdout=subprocess.PIPE,
                                       stderr=subprocess.PIPE, text=True)
            os.close(slave)
            slave = None
            os.write(master, answer.encode())
            stdout, stderr = process.communicate(timeout=30)
            return subprocess.CompletedProcess(argv, process.returncode, stdout, stderr)
        finally:
            os.close(master)
            if slave is not None: os.close(slave)

    def ok(self, argv, answer=None):
        result = self.command(argv, answer)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def add(self):
        # Local git fixture contains the unpublished changes. --yes skips gum
        # only; the production clone, validation, enable, and placement run.
        self.ok(['omarchy', 'plugin', 'add', 'file:///tmp/fixture', '--enable', '--yes'])
        self.assertTrue((self.local_plugin/'.git').exists())
        self.assertTrue((self.base/'enabled').exists())
        self.assertFalse((self.local_plugin/'build/wfd-sender').exists())
        self.assertEqual(self.config.read_text(), self.original)
        self.assertFalse((self.base/'home/.local/state/hovencast/portal.json').exists())

    def status(self):
        return json.loads(self.ok([str(self.plugin/'hovencast-setup'), '--check']).stdout)

    def setup(self, answer='y\n'):
        return self.command([str(self.plugin/'hovencast-setup')], answer)

    def test_interactive_add_shows_trust_prompt_and_placement(self):
        result = self.command(['omarchy', 'plugin', 'add', 'file:///tmp/fixture', '--enable'],
                              interactive=True)
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn('unsandboxed code', result.stdout)
        self.assertTrue((self.base/'enabled').exists())
        log = [json.loads(line) for line in (self.base/'commands.jsonl').read_text().splitlines()]
        self.assertTrue(any(row[:2] == ['gum', 'confirm'] for row in log))
        self.assertTrue(any(row[:2] == ['gum', 'choose'] for row in log))
        self.assertEqual(self.config.read_text(), self.original)
        self.assertFalse(self.status()['ready'])

    def test_declining_install_does_not_clone_or_enable(self):
        (self.base/'decline-install').touch()
        result = self.command(['omarchy', 'plugin', 'add', 'file:///tmp/fixture', '--enable'],
                              interactive=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.local_plugin.exists())
        self.assertFalse((self.base/'enabled').exists())
        self.assertEqual(self.config.read_text(), self.original)

    def test_fresh_clone_terminal_setup_repeat_and_remove(self):
        self.add()
        self.assertFalse(self.status()['ready'])
        self.assertEqual(set(self.status()['missing_packages']), set(MISSING))
        launcher = subprocess.check_output(['node', '-e',
            'console.log(JSON.stringify(require("./Model.js").setupLaunchCommand(process.argv[1])))',
            str(self.plugin/'hovencast-setup')], cwd=ROOT, text=True)
        result = self.ok(json.loads(launcher), 'y\n\n')
        self.assertIn('Ready. Open HovenCast', result.stdout)
        self.assertTrue(self.status()['ready'])
        log = [json.loads(line) for line in (self.base/'commands.jsonl').read_text().splitlines()]
        installs = [row for row in log if row[:2] == ['pacman', '-S']]
        self.assertEqual(len(installs), 1)
        self.assertEqual(set(arg for arg in installs[0][2:] if not arg.startswith('-')), set(MISSING))
        self.assertIn('allow_token_by_default = false', self.config.read_text())
        self.assertEqual(self.config.stat().st_mode & 0o777, 0o640)
        self.assertEqual(self.setup().returncode, 0)
        self.ok(['omarchy', 'plugin', 'disable', 'hoven.cast'])
        self.ok([str(self.plugin/'hovencast-uninstall')])
        self.ok(['omarchy', 'plugin', 'remove', 'hoven.cast', '--yes'])
        self.assertEqual(self.config.read_text(), self.original)
        self.assertFalse(self.local_plugin.exists())

    def test_cancel_setup_then_retry(self):
        self.add()
        result = self.setup('n\n')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('Setup cancelled', result.stdout)
        self.assertEqual(self.config.read_text(), self.original)
        self.assertFalse((self.local_plugin/'build').exists())
        self.assertEqual(set(self.status()['missing_packages']), set(MISSING))
        result = self.setup()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(self.status()['ready'])

    def test_package_failure_keeps_portal_unchanged(self):
        self.add()
        (self.base/'fail-package').touch()
        result = self.setup()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.config.read_text(), self.original)
        self.assertFalse((self.local_plugin/'build').exists())
        self.assertFalse(self.status()['ready'])

    def test_compilation_failure_keeps_portal_unchanged(self):
        self.add()
        (self.base/'fail-build').touch()
        result = self.setup()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.config.read_text(), self.original)
        self.assertFalse((self.base/'home/.local/state/hovencast/portal.json').exists())
        self.assertFalse(self.status()['ready'])

    def test_service_failure_rolls_back_and_can_retry(self):
        self.add()
        (self.base/'fail-service').touch()
        result = self.setup()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Configuration restored', result.stderr)
        self.assertEqual(self.config.read_text(), self.original)
        self.assertFalse(self.status()['ready'])
        (self.base/'fail-service').unlink()
        result = self.setup()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(self.status()['ready'])

    def test_recovery_after_plugin_removed_first(self):
        self.add()
        result = self.setup()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.ok(['omarchy', 'plugin', 'remove', 'hoven.cast', '--yes'])
        self.assertFalse(self.local_plugin.exists())
        recovery = HOME_PATH/'.local/state/hovencast/restore-portal.py'
        self.ok(['python', str(recovery), 'remove'])
        self.assertEqual(self.config.read_text(), self.original)


if __name__ == '__main__':
    unittest.main()
