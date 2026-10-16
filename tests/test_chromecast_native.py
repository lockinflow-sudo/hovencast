import os
from pathlib import Path
import tempfile
import threading
import unittest
from urllib.request import urlopen

from chromecast_native import RawRelay


class NativeRelayTests(unittest.TestCase):
    def test_audio_and_video_readers_receive_the_same_stream(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / 'source.ts'
            os.mkfifo(source)
            finished = threading.Event()
            packet = b'G' + bytes(1315)

            def write():
                with source.open('wb', buffering=0) as stream:
                    stream.write(packet)
                    finished.wait(3)

            relay = RawRelay(source)
            relay.start()
            writer = threading.Thread(target=write)
            writer.start()
            try:
                with urlopen(relay.url, timeout=2) as audio, urlopen(relay.url, timeout=2) as video:
                    self.assertEqual(audio.read(1316), packet)
                    self.assertEqual(video.read(1316), packet)
                self.assertEqual(relay.chunks.maxlen, 200)
            finally:
                finished.set()
                writer.join(timeout=2)
                relay.close()
            self.assertFalse(relay.reader.is_alive())
            self.assertFalse(relay.thread.is_alive())
