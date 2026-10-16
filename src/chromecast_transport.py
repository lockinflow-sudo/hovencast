"""Google Cast discovery and a bounded, session-owned live DASH sender."""
from __future__ import annotations

import argparse
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import signal
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

CAST_PORT = 8765
EVENT_FILE = None


def known_hosts_path() -> Path:
    return Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state") / "hovencast/cast-hosts.json"


def known_hosts() -> list[str]:
    try:
        entries = json.loads(known_hosts_path().read_text())
        return [str(ipaddress.IPv4Address(x)) for x in entries][:32]
    except (OSError, ValueError, TypeError):
        return []


def remember_hosts(hosts: list[str]) -> None:
    path = known_hosts_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(sorted(set(hosts))[:32]) + "\n")
    path.chmod(0o600)


def discover_receivers(timeout: float = 4, hosts: list[str] | None = None) -> list[dict]:
    from concurrent.futures import ThreadPoolExecutor
    from pychromecast.dial import get_device_info
    import zeroconf
    observed = set(hosts if hosts is not None else known_hosts())
    lock = threading.Lock()
    zconf = zeroconf.Zeroconf()

    class Listener:
        def add_service(self, zc, service_type, name):
            info = zc.get_service_info(service_type, name, timeout=1000)
            if info:
                with lock:
                    observed.update(info.parsed_addresses(zeroconf.IPVersion.V4Only))

        def update_service(self, zc, service_type, name):
            self.add_service(zc, service_type, name)

        def remove_service(self, *_):
            pass

    browser = zeroconf.ServiceBrowser(zconf, "_googlecast._tcp.local.", Listener())
    try:
        time.sleep(timeout)
    finally:
        browser.cancel()
        zconf.close()
    with lock:
        selected = sorted(observed)[:32]

    def probe(host):
        try:
            host = str(ipaddress.IPv4Address(host))
            info = get_device_info(host, timeout=2)
            if info is None or info.cast_type != "cast":
                return None
            return dict(name=info.friendly_name or host,
                        address=f"cast:{info.uuid}@{host}", port=8009,
                        interface="", protocol="google-cast",
                        model=info.model_name or "", vendor=info.manufacturer or "",
                        native_width=0, native_height=0)
        except (ValueError, OSError):
            return None

    with ThreadPoolExecutor(max_workers=8) as pool:
        rows = [row for row in pool.map(probe, selected) if row]
    return sorted(rows, key=lambda x: (x["name"], x["address"]))


def parse_address(address: str) -> tuple[str, str]:
    from uuid import UUID
    if not address.startswith("cast:"):
        raise ValueError("Invalid Google Cast address")
    uuid, host = address[5:].split("@", 1)
    return str(UUID(uuid)), str(ipaddress.IPv4Address(host))


def connect_receiver(address: str):
    import pychromecast
    from pychromecast.dial import get_device_info
    uuid, host = parse_address(address)
    info = get_device_info(host, timeout=4)
    if info is None or str(info.uuid) != uuid or info.cast_type != "cast":
        raise RuntimeError("The selected Chromecast changed or is unavailable. Search again.")
    cast = pychromecast.get_chromecast_from_host(
        (host, 8009, info.uuid, info.model_name, info.friendly_name), tries=1, timeout=4)
    try:
        cast.wait(timeout=10)
        return cast
    except BaseException:
        cast.disconnect()
        raise


def local_address(host: str) -> str:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.connect((host, 8009))
        return sock.getsockname()[0]


def dash_command(source: Path, folder: Path) -> list[str]:
    return ["ffmpeg", "-hide_banner", "-loglevel", "warning", "-nostdin", "-y",
            "-probesize", "1000000", "-analyzeduration", "1000000",
            "-i", str(source), "-map", "0:v:0", "-map", "0:a:0",
            "-c:v", "libx264", "-preset", "ultrafast", "-tune", "zerolatency",
            "-profile:v", "baseline", "-pix_fmt", "yuv420p", "-vf", "setpts=PTS-STARTPTS",
            "-r", "30", "-g", "30", "-sc_threshold", "0", "-b:v", "3000k",
            "-maxrate", "3500k", "-bufsize", "3000k", "-c:a", "aac", "-b:a", "192k",
            "-ar", "48000", "-ac", "2", "-af", "asetpts=N/SR/TB",
            "-f", "dash", "-seg_duration", "2", "-window_size", "10",
            "-extra_window_size", "6", "-use_template", "1", "-use_timeline", "1",
            "-adaptation_sets", "id=0,streams=v id=1,streams=a",
            "-init_seg_name", "init$RepresentationID$.mp4",
            "-media_seg_name", "segment$RepresentationID$_$Number%06d$.m4s",
            str(folder / "live.mpd")]


class LiveServer:
    def __init__(self, folder: Path, bind: str, peer: str, port: int = CAST_PORT, *,
                 playlist: str = "live.mpd", allowed: str = r"live\.mpd|init[01]\.mp4|segment[01]_\d{6}\.m4s", playlist_transform=None):
        self.token = secrets.token_urlsafe(24)
        self.requests = 0
        self.segments = 0
        self.fetched = set()
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def end_headers(self):
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Cache-Control", "no-store")
                super().end_headers()

            def do_OPTIONS(self):
                if self.client_address[0] != peer:
                    self.send_error(403)
                    return
                self.send_response(204)
                self.send_header("Access-Control-Allow-Methods", "GET, HEAD, OPTIONS")
                self.end_headers()

            def do_HEAD(self):
                self.serve(False)

            def do_GET(self):
                self.serve(True)

            def serve(self, body: bool):
                if self.client_address[0] != peer:
                    self.send_error(403)
                    return
                prefix = "/" + owner.token + "/"
                name = self.path.removeprefix(prefix)
                if (not self.path.startswith(prefix)
                        or not re.fullmatch(allowed, name)):
                    self.send_error(404)
                    return
                path = folder / name
                try:
                    if path.is_symlink():
                        raise FileNotFoundError
                    data = path.read_bytes()
                    if name == playlist and playlist_transform is not None:
                        data = playlist_transform(data)
                except OSError:
                    self.send_error(404)
                    return
                owner.requests += 1
                owner.fetched.add(name)
                owner.segments += int(name.endswith((".m4s", ".ts")))
                self.send_response(200)
                self.send_header("Content-Type", "application/vnd.apple.mpegurl" if name.endswith(".m3u8") else "video/mp2t" if name.endswith(".ts") else "application/dash+xml"
                                 if name.endswith(".mpd") else "audio/mp4"
                                 if name.startswith(("init1", "segment1")) else "video/mp4")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                if body:
                    self.wfile.write(data)

        self.server = ThreadingHTTPServer((bind, port), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.url = f"http://{bind}:{self.server.server_port}/{self.token}/{playlist}"

    def start(self):
        self.thread.start()

    def close(self):
        if self.thread.is_alive():
            self.server.shutdown()
            self.thread.join(timeout=2)
        self.server.server_close()


def emit(event: str, **fields):
    line = json.dumps(dict(time=time.time(), event=event, **fields))
    print(line, flush=True)
    if EVENT_FILE:
        with Path(EVENT_FILE).open("a") as stream:
            stream.write(line + "\n")


def run_sender(args) -> int:
    native = os.environ.get("HOVENCAST_CAST_NATIVE_SENDER")
    if native is None:
        candidate = Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local/share"))) / "hovencast/cast_sender"
        native = str(candidate) if candidate.is_file() and os.access(candidate, os.X_OK) else ""
    if native:
        from chromecast_native import run_native_sender
        return run_native_sender(args, native)
    stopped = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stopped.set())
    cast = server = remux = None
    url = None
    played = False
    try:
        _, host = parse_address(args.address)
        folder = Path(args.folder)
        cast = connect_receiver(args.address)
        emit("cast-control-connected", receiver=args.address)
        server = LiveServer(folder, local_address(host), host)
        server.start()
        url = server.url
        remux = subprocess.Popen(dash_command(Path(args.input), folder))
        playlist = folder / "live.mpd"
        deadline = time.monotonic() + 25
        while not stopped.wait(.1):
            if remux.poll() is not None:
                raise RuntimeError("Chromecast stream packaging stopped unexpectedly")
            if (playlist.exists() and len(list(folder.glob("segment0_*.m4s"))) >= 3
                    and len(list(folder.glob("segment1_*.m4s"))) >= 3):
                break
            if time.monotonic() > deadline:
                raise RuntimeError("Chromecast video stream did not become ready")
        if stopped.is_set():
            return 0
        cast.media_controller.play_media(url, "application/dash+xml",
                                        title="HovenCast desktop", stream_type="LIVE")
        emit("cast-load-requested")
        deadline = time.monotonic() + 30
        last_fetch = time.monotonic()
        last_requests = 0
        last_report = 0.0
        while not stopped.wait(1):
            if remux.poll() is not None:
                raise RuntimeError("Chromecast stream packaging stopped unexpectedly")
            media = cast.media_controller
            media.update_status()
            if time.monotonic() - last_report >= 5:
                emit("cast-playback-status", player_state=media.status.player_state,
                     position=media.status.current_time, requests=server.requests,
                     fetched_segments=server.segments)
                last_report = time.monotonic()
            own_content = media.status.content_id == url
            playing = own_content and media.status.player_state == "PLAYING"
            if playing and not played:
                emit("cast-playing", fetched_segments=server.segments, fetched_files=sorted(server.fetched))
                played = True
            if played and (not own_content or media.status.player_state == "IDLE"):
                emit("cast-receiver-stopped")
                return 0
            if not played and time.monotonic() > deadline:
                raise RuntimeError("Chromecast could not play the desktop stream. "
                                   "Check its network access and Chromecast setup." if server.requests
                                   else "Chromecast cannot reach this computer. Run Chromecast setup to allow local streaming.")
            if server.requests != last_requests:
                last_requests = server.requests
                last_fetch = time.monotonic()
            if played and time.monotonic() - last_fetch > 25:
                raise RuntimeError("Chromecast stopped requesting the desktop stream")
        return 0
    except Exception as error:
        emit("cast-transport-failed", error=str(error), fetched_files=sorted(server.fetched) if server else [])
        return 1
    finally:
        if remux is not None and remux.poll() is None:
            remux.terminate()
            try:
                remux.wait(timeout=1)
            except subprocess.TimeoutExpired:
                remux.kill()
                remux.wait()
        if cast is not None:
            try:
                if url and cast.media_controller.status.content_id == url:
                    cast.media_controller.stop()
            except Exception as error:
                emit("cast-cleanup-warning", error=str(error))
            finally:
                try:
                    cast.disconnect()
                except Exception as error:
                    emit("cast-cleanup-warning", error=str(error))
        if server is not None:
            server.close()
        emit("cast-transport-stopped", played=played)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--address", required=True)
    parser.add_argument("--input", required=True)
    parser.add_argument("--folder", required=True)
    parser.add_argument("--events", default="")
    arguments = parser.parse_args()
    EVENT_FILE = arguments.events
    raise SystemExit(run_sender(arguments))
