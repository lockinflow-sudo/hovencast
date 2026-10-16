#!/usr/bin/python

import pathlib
import os
import sys
import time
import socket
import struct
import subprocess
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
SENDER = ROOT / "build/wfd-sender"
sys.path.insert(0, str(ROOT / "src"))
from omarchy_cast_protocol import read_rtsp_message, live_remux_command


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
    def test_p2p_fifo_delivers_rtp_after_play(self):
        if not SENDER.exists():
            self.skipTest("wfd-sender has not been built")
        environment = dict(os.environ, HOVENCAST_TRANSPORT="miracast-p2p",
                           HOVENCAST_P2P_LOCAL_ADDRESS="127.0.0.1",
                           HOVENCAST_P2P_NETWORK="127.0.0.0/24",
                           GST_DEBUG="rtspmedia:4,filesrc:4")
        with tempfile.TemporaryDirectory() as directory, socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp:
            fifo = pathlib.Path(directory) / "live.ts"
            raw_fifo = pathlib.Path(directory) / "raw.ts"
            os.mkfifo(fifo)
            os.mkfifo(raw_fifo)
            writer = subprocess.Popen([
                "gst-launch-1.0", "-q", "videotestsrc", "is-live=true", "num-buffers=600",
                "!", "video/x-raw,format=I420,width=1280,height=720,framerate=30/1",
                "!", "x264enc", "tune=zerolatency", "speed-preset=veryfast", "bitrate=5000",
                "key-int-max=30", "bframes=0", "byte-stream=true", "aud=true",
                "!", "video/x-h264,profile=baseline,stream-format=byte-stream,alignment=au",
                "!", "h264parse", "config-interval=-1", "!", "queue", "!", "mux.sink_256",
                "audiotestsrc", "is-live=true", "num-buffers=940", "!",
                "audio/x-raw,format=S16LE,rate=48000,channels=2", "!", "fdkaacenc", "bitrate=192000",
                "!", "aacparse", "!", "queue", "!", "mux.sink_257",
                "mpegtsmux", "name=mux", "alignment=7", "pat-interval=9000", "pmt-interval=9000",
                "!", "filesink", f"location={raw_fifo}", "sync=false"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            remux = subprocess.Popen(live_remux_command(str(raw_fifo), str(fifo), p2p=True),
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            udp.bind(("127.0.0.2", 0))
            udp.settimeout(5)
            port = udp.getsockname()[1]
            process = subprocess.Popen([str(SENDER), "127.0.0.2", str(fifo), "15"],
                                       env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            try:
                deadline = time.monotonic() + 3
                while True:
                    try:
                        sink = socket.create_connection(("127.0.0.1", 7236), timeout=10,
                                                        source_address=("127.0.0.2", 0))
                        break
                    except ConnectionRefusedError:
                        if time.monotonic() > deadline:
                            raise
                        time.sleep(0.02)
                with sink, sink.makefile("rb") as stream:
                    def reply(request, body=b""):
                        sink.sendall(f"RTSP/1.0 200 OK\r\nCSeq: {request.headers['cseq']}\r\nContent-Length: {len(body)}\r\n\r\n".encode() + body)
                    reply(read_rtsp_message(stream))
                    sink.sendall(b"OPTIONS * RTSP/1.0\r\nCSeq: 1\r\nRequire: org.wfa.wfd1.0\r\n\r\n")
                    read_rtsp_message(stream)
                    reply(read_rtsp_message(stream),
                          f"wfd_video_formats: 00 00 01 10 0001FFFF 00000000 00000000 00 0000 0000 01 none none\r\nwfd_audio_codecs: AAC 00000001 00\r\nwfd_client_rtp_ports: RTP/AVP/UDP;unicast {port} 0 mode=play\r\nwfd_display_edid: none\r\n".encode())
                    selected = read_rtsp_message(stream)
                    self.assertIn(b"wfd_video_formats: 00 00 01 10", selected.body)
                    self.assertIn(f"unicast {port} {port + 1} mode=play".encode(), selected.body)
                    reply(selected)
                    reply(read_rtsp_message(stream))
                    uri = "rtsp://127.0.0.1:7236/wfd1.0"
                    sink.sendall(f"DESCRIBE {uri} RTSP/1.0\r\nCSeq: 2\r\nAccept: application/sdp\r\n\r\n".encode())
                    describe = read_rtsp_message(stream)
                    self.assertIn("200 OK", describe.start_line)
                    sink.sendall(f"SETUP {uri}/streamid=0 RTSP/1.0\r\nCSeq: 3\r\nTransport: RTP/AVP/UDP;unicast;client_port={port}\r\n\r\n".encode())
                    setup = read_rtsp_message(stream)
                    self.assertIn("200 OK", setup.start_line)
                    session = setup.headers["session"].split(";")[0]
                    sink.sendall(f"PLAY {uri} RTSP/1.0\r\nCSeq: 4\r\nSession: {session}\r\nRange: npt=0.000-\r\n\r\n".encode())
                    play = read_rtsp_message(stream)
                    self.assertIn("200 OK", play.start_line)
                    packet, _ = udp.recvfrom(2048)
                    self.assertEqual(packet[1] & 0x7f, 33)
                    deadline = time.monotonic() + 3
                    packets = 0
                    while time.monotonic() < deadline:
                        udp.recvfrom(2048)
                        packets += 1
                    self.assertGreater(packets, 100)
            finally:
                process.terminate()
                stdout, stderr = process.communicate(timeout=5)
                if sys.exc_info()[0] is not None:
                    sys.stderr.write(stderr[-16000:])
                remux.terminate()
                remux.wait(timeout=5)
                writer.terminate()
                writer.wait(timeout=5)

    def test_p2p_rejects_other_peer_and_negotiates_without_mice(self):
        if not SENDER.exists():
            self.skipTest("wfd-sender has not been built")
        environment = dict(os.environ, HOVENCAST_TRANSPORT="miracast-p2p",
                           HOVENCAST_P2P_LOCAL_ADDRESS="127.0.0.1",
                           HOVENCAST_P2P_NETWORK="127.0.0.0/24")
        with tempfile.NamedTemporaryFile() as artifact:
            process = subprocess.Popen(
                [str(SENDER), "127.0.0.2", artifact.name, "10"],
                env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True)
            try:
                deadline = time.monotonic() + 3
                while True:
                    try:
                        rejected = socket.create_connection(("127.0.0.1", 7236),
                            timeout=1, source_address=("127.0.0.3", 0))
                        break
                    except ConnectionRefusedError:
                        if time.monotonic() > deadline:
                            raise
                        time.sleep(0.02)
                with rejected:
                    self.assertEqual(rejected.recv(1024), b"")
                with socket.create_connection(("127.0.0.1", 7236), timeout=3,
                        source_address=("127.0.0.2", 0)) as sink:
                    stream = sink.makefile("rb")
                    request = read_rtsp_message(stream)
                    self.assertEqual(request.start_line, "OPTIONS * RTSP/1.0")
                    cseq = request.headers["cseq"]
                    sink.sendall(f"RTSP/1.0 200 OK\r\nCSeq: {cseq}\r\nContent-Length: 0\r\n\r\n".encode())
                    sink.sendall(b"OPTIONS * RTSP/1.0\r\nCSeq: 1\r\nRequire: org.wfa.wfd1.0\r\n\r\n")
                    reply = read_rtsp_message(stream)
                    self.assertIn("200 OK", reply.start_line)
                    query = read_rtsp_message(stream)
                    self.assertTrue(query.start_line.startswith("GET_PARAMETER "))
                    self.assertIn(b"wfd_video_formats", query.body)
                    stream.close()
                stdout, stderr = process.communicate(timeout=3)
                self.assertIn('"event":"p2p-source-ready"', stdout)
                self.assertIn('"event":"rtsp-rejected-peer","detail":"127.0.0.3"', stdout)
                self.assertNotIn('"event":"mice-source-ready"', stdout)
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait()

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
