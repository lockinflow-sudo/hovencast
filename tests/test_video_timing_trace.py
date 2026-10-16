import json
import hashlib
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from video_timing_trace import VideoTimingTrace


class VideoTimingTraceTest(unittest.TestCase):
    def test_records_stage_clock_and_pts_without_pixels(self):
        output = mock.Mock()
        trace = VideoTimingTrace(output)
        with mock.patch("video_timing_trace.time.monotonic_ns", return_value=123):
            trace.record("capture", 456, None)
        output.assert_called_once_with(stage="capture", monotonic_ns=123, pts_ns=456, duration_ns=None)

    def test_time_and_count_bounds(self):
        output = mock.Mock()
        trace = VideoTimingTrace(output, seconds=1, limit=2)
        with mock.patch("video_timing_trace.time.monotonic_ns", side_effect=[0, 1, 2, 1_000_000_000]):
            for _ in range(4):
                trace.record("encode-output", 0, 0)
        self.assertEqual(output.call_count, 2)

    def test_mux_trace_records_only_video_pes_and_obeys_limit(self):
        packet = bytearray(188)
        packet[:4] = bytes([0x47, 0x41, 0, 0x10])
        packet[4:18] = bytes([0, 0, 1, 0xe0, 0, 0, 0x80, 0x80, 5,
                              0x21, 0, 5, 0xbf, 0x21])  # 90,000 ticks
        output = mock.Mock()
        trace = VideoTimingTrace(output, limit=1)
        trace.record_transport(bytes(packet) * 2)
        self.assertEqual(output.call_count, 1)
        self.assertEqual(output.call_args.kwargs['pts90k'], 90000)
        self.assertEqual(output.call_args.kwargs['stage'], 'mux-video-output')
        self.assertNotIn('payload', output.call_args.kwargs)
        packet[2] = 1  # audio PID 257
        VideoTimingTrace(output).record_transport(packet)
        VideoTimingTrace(output).record_transport(bytes(3))
        self.assertEqual(output.call_count, 1)

    def test_payload_digest_survives_timestamp_rebasing(self):
        packet = bytearray(188)
        packet[:4] = bytes([0x47, 0x41, 0, 0x10])
        packet[4:18] = bytes([0, 0, 1, 0xe0, 0, 0, 0x80, 0x80, 5,
                              0x21, 0, 5, 0xbf, 0x21])
        packet[18:82] = bytes(range(64))
        output = mock.Mock()
        trace = VideoTimingTrace(output)
        trace.record_transport(bytes(packet))
        packet[13:18] = bytes([0x21, 0, 1, 0, 1])
        trace.record_transport(bytes(packet))
        first, second = [call.kwargs for call in output.call_args_list]
        self.assertNotEqual(first['pts90k'], second['pts90k'])
        self.assertEqual(first['payload_sha256'], second['payload_sha256'])
        self.assertEqual(first['payload_sha256'], hashlib.sha256(bytes(range(64))).hexdigest())

    @unittest.skipUnless(shutil.which("cc") and shutil.which("pkg-config"), "native build tools unavailable")
    def test_native_rtp_trace_extracts_video_pts_without_network(self):
        packages = ["gstreamer-1.0", "gstreamer-rtsp-1.0", "gstreamer-rtsp-server-1.0", "gio-2.0"]
        flags = subprocess.run(["pkg-config", "--cflags", "--libs", *packages], capture_output=True, text=True)
        if flags.returncode:
            self.skipTest("GStreamer development packages unavailable")
        with tempfile.TemporaryDirectory() as directory:
            source = pathlib.Path(directory) / "trace.c"
            binary = pathlib.Path(directory) / "trace"
            source.write_text(f'''#define main sender_main
#include "{ROOT / 'src/wfd_sender.c'}"
#include <unistd.h>
#undef main
int main(void) {{
  gst_init(NULL, NULL);
  guint8 bytes[200] = {{0}};
  bytes[0] = 0x80; bytes[1] = 33;
  guint8 *ts = bytes + 12;
  ts[0] = 0x47; ts[1] = 0x41; ts[2] = 0; ts[3] = 0x10;
  guint8 *pes = ts + 4;
  pes[2] = 1; pes[3] = 0xe0; pes[6] = 0x80; pes[7] = 0x80; pes[8] = 5;
  guint64 pts = 90000;
  pes[9] = 0x21 | ((pts >> 29) & 14);
  pes[10] = pts >> 22; pes[11] = 1 | ((pts >> 14) & 254);
  pes[12] = pts >> 7; pes[13] = 1 | ((pts << 1) & 254);
  GstBuffer *buffer = gst_buffer_new_allocate(NULL, sizeof(bytes), NULL);
  gst_buffer_fill(buffer, 0, bytes, sizeof(bytes));
  trace_rtp_video(buffer);
  guint handler = g_unix_signal_add(SIGUSR1, start_live_video_trace, NULL);
  kill(getpid(), SIGUSR1);
  while (!video_trace_enabled)
    g_main_context_iteration(NULL, TRUE);
  g_source_remove(handler);
  trace_rtp_video(buffer);
  gst_buffer_unref(buffer);
  TransportTrace trace = {{.stage = "remux-fifo-input", .pending = g_byte_array_new()}};
  buffer = gst_buffer_new_allocate(NULL, 50, NULL);
  gst_buffer_fill(buffer, 0, ts, 50);
  trace_transport_chunk(&trace, buffer);
  gst_buffer_unref(buffer);
  buffer = gst_buffer_new_allocate(NULL, 138, NULL);
  gst_buffer_fill(buffer, 0, ts + 50, 138);
  trace_transport_chunk(&trace, buffer);
  gst_buffer_unref(buffer);
  g_byte_array_unref(trace.pending);
  buffer = gst_buffer_new_allocate(NULL, 188, NULL);
  gst_buffer_fill(buffer, 0, ts, 188);
  trace_transport_video(buffer, "tsparse-output", FALSE);
  gst_buffer_unref(buffer);
  buffer = gst_buffer_new_allocate(NULL, 3, NULL);
  trace_rtp_video(buffer);
  gst_buffer_unref(buffer);
  return 0;
}}
''')
            subprocess.run(["cc", "-std=gnu17", "-Wall", "-Wextra", "-Werror", str(source), "-o", str(binary), *flags.stdout.split()], check=True, capture_output=True)
            output = subprocess.check_output([str(binary)], text=True)
            events = [json.loads(line) for line in output.splitlines()]
            self.assertEqual(events[0]['event'], 'video-trace-started')
            events = [e for e in events if e['event'] == 'video-stage']
            self.assertEqual([e['stage'] for e in events], ['rtp-packetized', 'remux-fifo-input', 'tsparse-output'])
            self.assertEqual([e['pts90k'] for e in events], [90000] * 3)
            self.assertEqual([e['payload_sha256'] for e in events],
                             [hashlib.sha256(bytes(64)).hexdigest()] * 3)
            event = events[0]
            self.assertEqual(event["stage"], "rtp-packetized")
            self.assertEqual(event["pts90k"], 90000)
            self.assertGreater(event["monotonic_ns"], 0)
