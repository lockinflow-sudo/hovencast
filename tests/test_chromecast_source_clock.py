import tempfile
from pathlib import Path
import unittest
from chromecast_native import RawRelay


def video_pes(ticks, pid=256, adaptation=False):
    pts = bytes([0x21 | ((ticks >> 29) & 14), (ticks >> 22) & 255,
                 1 | ((ticks >> 14) & 254), (ticks >> 7) & 255,
                 1 | ((ticks << 1) & 254)])
    header = bytes([0x47, 0x40 | (pid >> 8), pid & 255, 0x30 if adaptation else 0x10])
    if adaptation:
        header += b'\x01\x00'
    payload = b'\x00\x00\x01\xe0\x00\x00\x80\x80\x05' + pts
    return (header + payload).ljust(188, b'\xff')


class SourceClockTests(unittest.TestCase):
    def make_relay(self):
        relay = RawRelay(Path(tempfile.gettempdir())/'unused-clock-source.ts')
        self.addCleanup(relay.server.server_close)
        return relay

    def test_clock_maps_split_video_pes_before_reader_start(self):
        relay = self.make_relay()
        packet = video_pes(324000000)
        relay.record_clock(packet[:17], 9000000000)
        self.assertFalse(relay.clock_ready.is_set())
        relay.record_clock(packet[17:], 9005000000)
        self.assertEqual(relay.clock_origin, (3600000000, 9005000))
        self.assertTrue(relay.clock_ready.is_set())

    def test_audio_does_not_set_video_clock_and_adaptation_is_supported(self):
        relay = self.make_relay()
        relay.record_clock(video_pes(1, pid=257), 1000000000)
        self.assertIsNone(relay.clock_origin)
        relay.record_clock(video_pes(324090000, adaptation=True), 2000000000)
        self.assertEqual(relay.clock_origin, (3601000000, 2000000))

    def test_later_frames_cannot_move_the_shared_clock(self):
        relay = self.make_relay()
        relay.record_clock(video_pes(90000), 1000000000)
        relay.record_clock(video_pes(180000), 3000000000)
        self.assertEqual(relay.clock_origin, (1000000, 1000000))

    def test_non_video_packets_keep_pending_storage_bounded(self):
        relay = self.make_relay()
        relay.record_clock(bytes(4096), 1000000000)
        self.assertIsNone(relay.clock_origin)
        self.assertLess(len(relay.pending_ts), 188)


if __name__ == '__main__':
    unittest.main()
