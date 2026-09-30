#!/usr/bin/python

import pathlib
import socket
import struct
import subprocess
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
SENDER = ROOT / "build/wfd-sender"


def read_mice_message(connection: socket.socket) -> bytes:
    size_bytes = connection.recv(2)
    if len(size_bytes) != 2:
        raise EOFError("MICE message length was not received")
    size = struct.unpack(">H", size_bytes)[0]
    chunks = [size_bytes]
    remaining = size - 2
    while remaining:
        chunk = connection.recv(remaining)
        if not chunk:
            raise EOFError("MICE message ended early")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


class WfdSenderLifecycleTest(unittest.TestCase):
    def exercise_sender(self, timeout: int, terminate: bool = False) -> None:
        if not SENDER.exists():
            self.skipTest("wfd-sender has not been built")
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                listener.bind(("127.0.0.1", 7250))
            except OSError as error:
                self.skipTest(f"local MICE test port is unavailable: {error}")
            listener.listen(1)
            listener.settimeout(5)
            with tempfile.NamedTemporaryFile() as artifact:
                process = subprocess.Popen(
                    [str(SENDER), "127.0.0.1", artifact.name, str(timeout)],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
                try:
                    connection, _peer = listener.accept()
                    with connection:
                        connection.settimeout(5)
                        source_ready = read_mice_message(connection)
                        if terminate:
                            process.terminate()
                        stop_projection = read_mice_message(connection)
                    stdout, stderr = process.communicate(timeout=5)
                finally:
                    if process.poll() is None:
                        process.kill()
                        process.wait()

        self.assertEqual(process.returncode, 0, stderr)
        self.assertEqual(source_ready[2:4], bytes([1, 1]))
        self.assertIn(bytes([2, 0, 2, 0x1C, 0x44]), source_ready)
        self.assertEqual(stop_projection[2:4], bytes([1, 2]))
        self.assertNotIn(bytes([2, 0, 2, 0x1C, 0x44]), stop_projection)
        self.assertTrue(stop_projection.endswith(bytes([3, 0, 16]) + b"HovenCastSender1"))
        self.assertIn('"event":"mice-stop-projection","detail":"sent"', stdout)

    def test_timeout_sends_stop_projection(self) -> None:
        self.exercise_sender(timeout=1)

    def test_sigterm_sends_stop_projection(self) -> None:
        self.exercise_sender(timeout=0, terminate=True)


if __name__ == "__main__":
    unittest.main()
