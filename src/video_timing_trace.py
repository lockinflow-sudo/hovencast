"""Bounded, opt-in video timestamps only; no pixels, audio or input capture."""
import time
import hashlib


class VideoTimingTrace:
    def __init__(self, emit, *, seconds=90, limit=20000):
        self.emit = emit
        self.seconds = seconds
        self.limit = limit
        self.started = None
        self.count = 0

    def record(self, stage, pts, duration):
        now = time.monotonic_ns()
        if self.started is None:
            self.started = now
        if now - self.started >= self.seconds * 1_000_000_000 or self.count >= self.limit:
            return
        self.count += 1
        self.emit(stage=stage, monotonic_ns=now, pts_ns=pts, duration_ns=duration)

    def record_transport(self, payload):
        """Record video PES timestamps, not every mux packet or its pixels."""
        for offset in range(0, len(payload) - 187, 188):
            ts = payload[offset:offset + 188]
            if ts[0] != 0x47 or not ts[1] & 0x40 or ((ts[1] & 31) << 8 | ts[2]) != 256 or not ts[3] & 0x10:
                continue
            header = 4 + (1 + ts[4] if ts[3] & 0x20 else 0)
            if header + 14 > 188:
                continue
            pes = ts[header:]
            if pes[:3] != b'\x00\x00\x01' or not pes[7] & 0x80 or pes[8] < 5:
                continue
            p = pes[9:14]
            pts = ((p[0] & 14) << 29 | p[1] << 22 | (p[2] & 254) << 14 | p[3] << 7 | p[4] >> 1)
            now = time.monotonic_ns()
            if self.started is None:
                self.started = now
            if now - self.started >= self.seconds * 1_000_000_000 or self.count >= self.limit:
                return
            self.count += 1
            # Stream-copy remuxing rebases PTS. Match unique payload prefixes
            # across stages instead of assuming a fixed timestamp offset.
            # Log only a digest, never encoded bytes. Repeated prefixes must
            # be rejected as ambiguous by the analysis.
            payload_start = header + 9 + pes[8]
            fields = {}
            if payload_start + 64 <= 188:
                fields["payload_sha256"] = hashlib.sha256(ts[payload_start:payload_start + 64]).hexdigest()
            self.emit(stage="mux-video-output", monotonic_ns=now, pts90k=pts, **fields)
