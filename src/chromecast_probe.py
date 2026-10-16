#!/usr/bin/env python3
"""Standalone Google Cast prototype; never touches HovenCast's Miracast state."""
from __future__ import annotations

import argparse
import json
import secrets
import socket
import subprocess
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from uuid import UUID

import pychromecast
import zeroconf
from pychromecast.dial import get_device_info


def discover(seconds: float, hosts: list[str]) -> list[dict]:
    zconf = zeroconf.Zeroconf()
    browser = pychromecast.CastBrowser(
        pychromecast.SimpleCastListener(lambda *_: None), zconf, known_hosts=hosts
    )
    try:
        browser.start_discovery()
        time.sleep(seconds)
        return sorted([
            dict(uuid=str(i.uuid), name=i.friendly_name, model=i.model_name,
                 host=i.host, port=i.port, cast_type=i.cast_type)
            for i in list(browser.devices.values())
        ], key=lambda i: (i["name"] or "", i["uuid"]))
    finally:
        browser.stop_discovery()
        zconf.close()


def connect(host: str):
    info = get_device_info(host, timeout=4)
    if info is None:
        raise RuntimeError(f"Cannot read Cast identity from {host}")
    cast = pychromecast.get_chromecast_from_host(
        (host, 8009, UUID(str(info.uuid)), info.model_name, info.friendly_name),
        tries=1, timeout=4,
    )
    try:
        cast.wait(timeout=10)
        return cast, info
    except BaseException:
        cast.disconnect()
        raise


def status(cast, info) -> dict:
    return dict(name=info.friendly_name, model=info.model_name,
                uuid=str(info.uuid), app=cast.status.display_name,
                app_id=cast.status.app_id,
                playback=cast.media_controller.status.player_state)


def generate_clip(folder: Path) -> None:
    subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=30",
        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000",
        "-t", "20", "-c:v", "libx264", "-preset", "veryfast",
        "-profile:v", "baseline", "-level", "3.1", "-pix_fmt", "yuv420p",
        "-g", "60", "-keyint_min", "60", "-sc_threshold", "0",
        "-c:a", "aac", "-b:a", "128k", "-af", "volume=0.05",
        "-f", "hls", "-hls_time", "2", "-hls_playlist_type", "vod",
        "-hls_segment_filename", str(folder / "segment%03d.ts"),
        str(folder / "test.m3u8"),
    ], check=True, timeout=60)


class ClipServer:
    """Serve only generated test media through an unguessable URL prefix."""
    def __init__(self, host: str, folder: Path, port: int = 0):
        self.prefix = "/" + secrets.token_urlsafe(24) + "/"
        self.assets = {
            self.prefix + p.name: (p.read_bytes(),
                "application/vnd.apple.mpegurl" if p.suffix == ".m3u8" else "video/mp2t")
            for p in folder.iterdir() if p.suffix in {".m3u8", ".ts"}
        }
        self.requests: list[str] = []
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_OPTIONS(self):
                self.send_response(204)
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Access-Control-Allow-Methods", "GET, HEAD, OPTIONS")
                self.end_headers()

            def do_HEAD(self):
                self.serve(False)

            def do_GET(self):
                self.serve(True)

            def serve(self, body: bool):
                asset = owner.assets.get(self.path)
                if asset is None:
                    self.send_error(404)
                    return
                data, mime = asset
                owner.requests.append(self.path.rsplit("/", 1)[-1])
                self.send_response(200)
                self.send_header("Content-Type", mime)
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                if body:
                    self.wfile.write(data)

        self.server = ThreadingHTTPServer((host, port), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.url = f"http://{host}:{self.server.server_port}{self.prefix}test.m3u8"

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)


def local_address(target: str) -> str:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.connect((target, 8009))
        return sock.getsockname()[0]


def test_playback(host: str, port: int = 8765) -> dict:
    cast, info = connect(host)
    url = None
    try:
        # Refuse to replace another app or an existing playback session.
        idle_media = (cast.status.app_id == "CC1AD845"
                      and not cast.media_controller.status.content_id
                      and cast.media_controller.status.player_state in {"UNKNOWN", "IDLE"})
        if cast.status.app_id not in {None, "E8C28D3C"} and not idle_media:
            raise RuntimeError("Receiver has an active app. Return it to its idle screen before testing.")
        with tempfile.TemporaryDirectory(prefix="hovencast-chromecast-") as tmp:
            folder = Path(tmp)
            generate_clip(folder)
            with ClipServer(local_address(host), folder, port) as server:
                url = server.url
                mc = cast.media_controller
                mc.play_media(url, "application/vnd.apple.mpegurl",
                              title="HovenCast Chromecast test", stream_type="BUFFERED")
                deadline = time.monotonic() + 45
                played = False
                while time.monotonic() < deadline:
                    mc.update_status()
                    time.sleep(1)
                    if mc.status.content_id != url:
                        continue
                    played |= mc.status.player_state == "PLAYING"
                    if played and mc.status.player_state == "IDLE":
                        break
                result = status(cast, info)
                result.update(playing_observed=played,
                              fetched_files=sorted(set(server.requests)))
                if not played:
                    raise RuntimeError("No PLAYING status received; fetched files: " +
                                       repr(result["fetched_files"]))
                return result
    finally:
        try:
            # Only stop our own media, never another client's replacement cast.
            if url and cast.media_controller.status.content_id == url:
                cast.media_controller.stop()
        finally:
            cast.disconnect()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    scan = sub.add_parser("discover", help="Read device identities without launching apps")
    scan.add_argument("--seconds", type=float, default=10)
    scan.add_argument("--host", action="append", default=[], help="Known IP fallback")
    for command in ("status", "test"):
        child = sub.add_parser(command)
        child.add_argument("host")
        if command == "test":
            child.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    try:
        if args.command == "discover":
            result = discover(args.seconds, args.host)
        elif args.command == "status":
            cast, info = connect(args.host)
            try:
                result = status(cast, info)
            finally:
                cast.disconnect()
        else:
            result = test_playback(args.host, args.port)
        print(json.dumps(result, indent=2))
    except (Exception, KeyboardInterrupt) as error:
        parser.exit(1, f"{type(error).__name__}: {error}\n")


if __name__ == "__main__":
    main()
