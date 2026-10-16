import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import urlopen

from chromecast_probe import ClipServer, test_playback


class ChromecastProbeTests(unittest.TestCase):
    def test_server_only_exposes_named_media_under_token(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / "test.m3u8").write_text("#EXTM3U\nsegment000.ts\n")
            (folder / "segment000.ts").write_bytes(b"media")
            (folder / "private.txt").write_text("private")
            with ClipServer("127.0.0.1", folder) as server:
                with urlopen(server.url) as response:
                    self.assertEqual(response.headers["Access-Control-Allow-Origin"], "*")
                    self.assertIn(b"segment000.ts", response.read())
                root = server.url.split(server.prefix)[0]
                for path in ("/test.m3u8", server.prefix + "private.txt",
                             server.prefix + "../private.txt"):
                    with self.assertRaises(HTTPError) as error:
                        urlopen(root + path)
                    self.assertEqual(error.exception.code, 404)
                    error.exception.close()
            self.assertFalse(server.thread.is_alive())

    def test_active_receiver_is_not_replaced_and_connection_is_closed(self):
        calls = []
        cast = SimpleNamespace(status=SimpleNamespace(app_id="OTHER_APP"),
                               disconnect=lambda: calls.append("disconnect"))
        with patch("chromecast_probe.connect", return_value=(cast, None)):
            with self.assertRaisesRegex(RuntimeError, "active app"):
                test_playback("192.0.2.1")
        self.assertEqual(calls, ["disconnect"])


if __name__ == "__main__":
    unittest.main()
