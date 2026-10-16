"""Exercise failed setup through a real controlling terminal."""
import os
from pathlib import Path
import pty
import select
import signal
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]


class SetupTerminalTest(unittest.TestCase):
    def test_failure_stays_visible_and_logs_without_keyboard_input(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            command = base / 'setup with spaces; literal'
            command.write_text('#!/bin/bash\nprintf "Simulated setup failure\\n"\nexit 7\n')
            command.chmod(0o700)
            pid, terminal = pty.fork()
            if pid == 0:
                os.environ['XDG_STATE_HOME'] = str(base / 'state')
                os.execv('/bin/bash', ['bash', str(ROOT / 'scripts/setup_terminal.sh'), str(command)])
            output = bytearray()
            try:
                deadline = time.monotonic() + 10
                while b'Press Enter to close' not in output and time.monotonic() < deadline:
                    if select.select([terminal], [], [], 0.1)[0]:
                        output.extend(os.read(terminal, 4096))
                self.assertIn(b'Simulated setup failure', output)
                self.assertIn(b'Setup exited with status 7', output)
                self.assertIn(b'Press Enter to close', output)
                self.assertEqual(os.waitpid(pid, os.WNOHANG), (0, 0))
                os.write(terminal, b'keyboard-input-must-not-be-logged\n')
                _, status = os.waitpid(pid, 0)
                pid = None
                self.assertEqual(os.waitstatus_to_exitcode(status), 7)
                log = base / 'state/hovencast/last-setup-terminal.log'
                self.assertIn('Simulated setup failure', log.read_text())
                self.assertNotIn('keyboard-input', log.read_text())
                self.assertEqual(log.stat().st_mode & 0o777, 0o600)
            finally:
                os.close(terminal)
                if pid is not None:
                    try:
                        os.kill(pid, signal.SIGKILL)
                        os.waitpid(pid, 0)
                    except ProcessLookupError:
                        pass
