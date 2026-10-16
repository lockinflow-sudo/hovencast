import json
import os
from pathlib import Path
import tempfile
import subprocess
import unittest
from unittest import mock
from urllib.error import HTTPError
from urllib.request import urlopen

import chromecast_transport as cast
import omarchy_cast


class CastTransportTests(unittest.TestCase):
    def test_real_aac_transport_can_be_packaged_as_mp4_fragments(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            source = folder / "input.ts"
            subprocess.run([
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
                "-i", "testsrc2=size=160x90:rate=30", "-f", "lavfi", "-i",
                "sine=frequency=440:sample_rate=48000", "-t", "2", "-c:v", "libx264",
                "-preset", "ultrafast", "-profile:v", "baseline", "-g", "15",
                "-c:a", "aac", "-ac", "2", "-f", "mpegts", str(source)
            ], check=True, capture_output=True, timeout=20)
            subprocess.run(cast.dash_command(source, folder), check=True,
                           capture_output=True, timeout=20)
            fragments = sorted(folder.glob("segment*.m4s"))
            self.assertGreaterEqual(len(fragments), 2)
            streams = []
            starts = []
            for track in (0, 1):
                sample = folder / f"sample{track}.mp4"
                fragment = sorted(folder.glob(f"segment{track}_*.m4s"))[0]
                sample.write_bytes((folder / f"init{track}.mp4").read_bytes() + fragment.read_bytes())
                result = subprocess.run([
                    "ffprobe", "-v", "error", "-show_streams", "-of", "json", str(sample)
                ], check=True, capture_output=True, text=True, timeout=10)
                track_streams = json.loads(result.stdout)["streams"]
                streams.extend(track_streams)
                starts.extend(float(s["start_time"]) for s in track_streams)
            self.assertEqual({s["codec_name"] for s in streams}, {"h264", "aac"})
            self.assertLess(max(starts) - min(starts), .1)
            audio = next(s for s in streams if s["codec_name"] == "aac")
            self.assertEqual(audio["sample_rate"], "48000")
            self.assertEqual(audio["channels"], 2)

    def test_address_contains_identity_and_rejects_shell_or_bad_ip(self):
        self.assertEqual(cast.parse_address("cast:babbb6c3-5acd-25a7-d361-5d9d5080749a@192.168.1.156"),
                         ("babbb6c3-5acd-25a7-d361-5d9d5080749a", "192.168.1.156"))
        for address in ("192.168.1.156", "cast:bad@192.168.1.156",
                        "cast:babbb6c3-5acd-25a7-d361-5d9d5080749a@bad"):
            with self.assertRaises(ValueError):
                cast.parse_address(address)

    def test_dhcp_identity_change_does_not_cast_to_wrong_display(self):
        info = mock.Mock(uuid="00000000-0000-0000-0000-000000000000", cast_type="cast")
        with mock.patch("pychromecast.dial.get_device_info", return_value=info), \
                mock.patch("pychromecast.get_chromecast_from_host") as connect:
            with self.assertRaisesRegex(RuntimeError, "changed"):
                cast.connect_receiver("cast:babbb6c3-5acd-25a7-d361-5d9d5080749a@192.168.1.156")
            connect.assert_not_called()

    def test_server_refreshes_playlist_and_never_serves_unrelated_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / "live.mpd").write_text("#EXTM3U\nold\n")
            (folder / "segment0_000000.m4s").write_bytes(b"video")
            (folder / "secret").write_text("private")
            server = cast.LiveServer(folder, "127.0.0.1", "127.0.0.1", 0)
            server.start()
            try:
                with urlopen(server.url) as r:
                    self.assertIn(b"old", r.read())
                    self.assertEqual(r.headers["Cache-Control"], "no-store")
                (folder / "live.mpd").write_text("#EXTM3U\nnew\n")
                with urlopen(server.url) as r:
                    self.assertIn(b"new", r.read())
                base = server.url.rsplit("/", 1)[0]
                for name in ("../secret", "secret", "segment0_000000.m4s.tmp"):
                    with self.assertRaises(HTTPError) as err:
                        urlopen(base + "/" + name)
                    self.assertEqual(err.exception.code, 404)
                    err.exception.close()
            finally:
                server.close()
            self.assertFalse(server.thread.is_alive())

    def test_server_rejects_other_network_clients(self):
        with tempfile.TemporaryDirectory() as tmp:
            server = cast.LiveServer(Path(tmp), "127.0.0.1", "192.0.2.10", 0)
            server.start()
            try:
                with self.assertRaises(HTTPError) as err:
                    urlopen(server.url)
                self.assertEqual(err.exception.code, 403)
                err.exception.close()
            finally:
                server.close()

    def test_encoded_frames_do_not_prematurely_mark_cast_connected(self):
        address = "cast:babbb6c3-5acd-25a7-d361-5d9d5080749a@192.168.1.156"
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": tmp}):
            state = dict(address=address, started_at=1)
            with mock.patch("omarchy_cast.read_cast_session_state", return_value=state), \
                    mock.patch("omarchy_cast.session_process_is_running", return_value=True), \
                    mock.patch("omarchy_cast.read_cast_session_events", return_value=[
                        dict(time=2, event="metrics", video_frames=100)]):
                self.assertEqual(omarchy_cast.cast_session_snapshot()["state"], "connecting")
                events = omarchy_cast.cast_session_path("cast-transport.events.jsonl")
                events.write_text(json.dumps(dict(time=3, event="cast-playing")) + "\n")
                self.assertEqual(omarchy_cast.cast_session_snapshot()["state"], "streaming")


if __name__ == "__main__":
    unittest.main()
