"""Cast Streaming adapter for the locally built Open Screen sender."""
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
import re
from pathlib import Path
import secrets
import selectors
import signal
import subprocess
import threading
import time


class RawRelay:
    """Give the native audio/video decoders independent bounded TS readers."""
    def __init__(self, source):
        self.source = source
        self.condition = threading.Condition()
        self.chunks = deque(maxlen=200)
        self.sequence = 0
        self.closed = False
        self.bytes = 0
        self.clock_origin = None
        self.clock_ready = threading.Event()
        self.pending_ts = bytearray()
        token = '/' + secrets.token_urlsafe(24) + '/stream.ts'
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_GET(self):
                if self.path != token:
                    self.send_error(404)
                    return
                self.send_response(200)
                self.send_header('Content-Type', 'video/mp2t')
                self.send_header('Cache-Control', 'no-store')
                self.end_headers()
                with owner.condition:
                    cursor = owner.chunks[0][0] if owner.chunks else owner.sequence
                try:
                    while True:
                        with owner.condition:
                            owner.condition.wait_for(lambda: owner.closed or owner.sequence > cursor, .5)
                            if owner.closed:
                                return
                            packets = [(n, data) for n, data in owner.chunks if n >= cursor]
                        for n, data in packets:
                            self.wfile.write(data)
                            cursor = n + 1
                except (BrokenPipeError, ConnectionResetError):
                    pass

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.server.daemon_threads = True
        self.url = f'http://127.0.0.1:{self.server.server_port}{token}'
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.reader = threading.Thread(target=self.read, daemon=True)

    def start(self):
        self.thread.start()
        self.reader.start()

    def read(self):
        with Path(self.source).open('rb', buffering=0) as stream:
            while not self.closed:
                data = stream.read(1316)
                if not data:
                    break
                self.record_clock(data, time.monotonic_ns())
                with self.condition:
                    self.chunks.append((self.sequence, data))
                    self.sequence += 1
                    self.bytes += len(data)
                    self.condition.notify_all()
        with self.condition:
            self.closed = True
            self.condition.notify_all()

    def record_clock(self, data, received_ns):
        """Map original video PTS to the source arrival clock, before buffering."""
        if self.clock_origin is not None:
            return
        self.pending_ts.extend(data)
        while len(self.pending_ts) >= 188:
            if self.pending_ts[0] != 0x47:
                del self.pending_ts[0]
                continue
            ts = self.pending_ts[:188]
            del self.pending_ts[:188]
            if not ts[1] & 0x40 or ((ts[1] & 31) << 8 | ts[2]) != 256 or not ts[3] & 0x10:
                continue
            offset = 4 + (1 + ts[4] if ts[3] & 0x20 else 0)
            if offset + 14 > 188:
                continue
            pes = ts[offset:]
            if pes[:3] != b'\x00\x00\x01' or not pes[7] & 0x80 or pes[8] < 5:
                continue
            pts = pes[9:14]
            ticks = ((pts[0] & 14) << 29 | pts[1] << 22 | (pts[2] & 254) << 14 | pts[3] << 7 | pts[4] >> 1)
            self.clock_origin = (ticks * 1_000_000 // 90000, received_ns // 1000)
            self.pending_ts.clear()
            self.clock_ready.set()
            return

    def close(self):
        with self.condition:
            self.closed = True
            self.condition.notify_all()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=1)
        self.reader.join(timeout=1)


def run_native_sender(args, binary):
    import chromecast_transport as transport
    transport.EVENT_FILE = args.events
    from pychromecast.dial import get_device_info
    stopped = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stopped.set())
    relay = process = selector = None
    played = False
    try:
        uuid, host = transport.parse_address(args.address)
        info = get_device_info(host, timeout=4)
        if info is None or str(info.uuid) != uuid or info.cast_type != 'cast':
            raise RuntimeError('The selected Chromecast changed or is unavailable. Search again.')
        target_delay_ms = int(os.environ.get('HOVENCAST_CAST_PLAYOUT_DELAY_MS', '400'))
        if not 100 <= target_delay_ms <= 1000:
            raise RuntimeError('Google Cast playback delay must be between 100 and 1000 ms.')
        bitrate_bps = int(os.environ.get('HOVENCAST_CAST_BITRATE_BPS', '6000000'))
        if not 500000 <= bitrate_bps <= 8000000:
            raise RuntimeError('Google Cast bitrate must be between 500000 and 8000000 bps.')
        in_flight_ms = int(os.environ.get('HOVENCAST_CAST_IN_FLIGHT_MS', str(max(66, target_delay_ms - 100))))
        if not 66 <= in_flight_ms <= target_delay_ms:
            raise RuntimeError('Google Cast acknowledgment window exceeds the playback delay.')
        relay = RawRelay(args.input)
        relay.start()
        source_deadline = time.monotonic() + 10
        while not relay.clock_ready.wait(.1):
            if stopped.is_set():
                return 0
            if relay.closed or time.monotonic() > source_deadline:
                raise RuntimeError('The screen capture did not provide a timed video stream.')
        pts_us, clock_us = relay.clock_origin
        environment = dict(os.environ, HOVENCAST_MEDIA_ORIGIN_PTS_US=str(pts_us),
                           HOVENCAST_MEDIA_ORIGIN_MONOTONIC_US=str(clock_us),
                           HOVENCAST_CAST_PLAYOUT_DELAY_MS=str(target_delay_ms),
                           HOVENCAST_CAST_IN_FLIGHT_MS=str(in_flight_ms))
        process = subprocess.Popen([binary, '-n', '-c', 'vp8', '-m', str(bitrate_bps), host + ':8009', relay.url],
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=environment)
        os.set_blocking(process.stdout.fileno(), False)
        selector = selectors.DefaultSelector()
        selector.register(process.stdout, selectors.EVENT_READ)
        deadline = time.monotonic() + 30
        pending = b''
        negotiated_delay_ms = None
        with (Path(args.folder) / 'native-sender.log').open('wb') as log:
            while not stopped.is_set():
                for key, _ in selector.select(.2):
                    chunk = os.read(key.fd, 65536)
                    if chunk:
                        if log.tell() > 2 * 1024 * 1024:
                            log.seek(0)
                            log.truncate()
                        log.write(chunk)
                        log.flush()
                        pending = (pending + chunk)[-131072:]
                        delay_match = re.search(rb'HOVENCAST_TARGET_DELAY_MS=(\d+)', pending)
                        if delay_match:
                            negotiated_delay_ms = int(delay_match[1])
                        if b'video has failed' in pending or b'audio has failed' in pending:
                            raise RuntimeError('The native screen stream lost an audio or video decoder.')
                        if not played and b'HOVENCAST_RTCP_RECEIVED' in pending:
                            played = True
                            transport.emit('cast-playing', transport='cast-streaming', target_delay_ms=negotiated_delay_ms)
                if process.poll() is not None:
                    if stopped.is_set():
                        return 0
                    if played and process.returncode == 0:
                        transport.emit("cast-receiver-stopped")
                        return 0
                    raise RuntimeError(f'Native Cast Streaming stopped (exit {process.returncode}).')
                if not played and time.monotonic() > deadline:
                    raise RuntimeError('The Chromecast did not acknowledge the native screen stream.')
        return 0
    except Exception as error:
        transport.emit('cast-transport-failed', error=str(error))
        return 1
    finally:
        if selector is not None:
            selector.close()
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        if relay is not None:
            relay.close()
        transport.emit('cast-transport-stopped', played=played)
