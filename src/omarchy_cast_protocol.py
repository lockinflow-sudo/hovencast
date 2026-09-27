from __future__ import annotations

import concurrent.futures
import http.client
import re
import socket
import struct
import subprocess
import xml.etree.ElementTree as ET
from typing import Any, BinaryIO
from dataclasses import asdict, dataclass, replace


MICE_PROTOCOL_VERSION = 1
MICE_SOURCE_READY = 1
MICE_FRIENDLY_NAME = 0
MICE_RTSP_PORT = 2
MICE_SOURCE_ID = 3
DEFAULT_SOURCE_ID = b"OmaCastSender001"


@dataclass(frozen=True)
class Receiver:
    name: str
    address: str
    port: int
    interface: str
    protocol: str = "miracast-mice"
    native_width: int | None = None
    native_height: int | None = None
    model: str = ""

    def json(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class VideoMode:
    name: str
    width: int
    height: int
    bitrate_kbps: int
    encoder_preset: str
    cea_bitmap: str
    queue_time_ns: int
    vbv_buffer_ms: int


VIDEO_MODE_720P = VideoMode("720p", 1280, 720, 5000, "veryfast", "00000020", 120_000_000, 120)
VIDEO_MODE_1080P = VideoMode("1080p", 1920, 1080, 6000, "ultrafast", "00000080", 500_000_000, 600)


def parse_roku_device_info(payload: bytes | str) -> dict[str, Any]:
    try:
        root = ET.fromstring(payload)
    except (ET.ParseError, TypeError, ValueError):
        return {}
    resolution = str(root.findtext("ui-resolution") or "").strip().lower()
    dimensions = {
        "720p": (1280, 720),
        "1080p": (1920, 1080),
        "4k": (3840, 2160),
        "2160p": (3840, 2160),
    }.get(resolution)
    if dimensions is None:
        return {}
    return {
        "native_width": dimensions[0],
        "native_height": dimensions[1],
        "model": str(root.findtext("model-name") or "").strip(),
    }


def probe_roku_device_info(address: str, timeout: float = 0.75) -> dict[str, Any]:
    connection = http.client.HTTPConnection(address, 8060, timeout=timeout)
    try:
        connection.request("GET", "/query/device-info", headers={"Connection": "close"})
        response = connection.getresponse()
        if response.status != 200:
            return {}
        return parse_roku_device_info(response.read(131_072))
    except (OSError, http.client.HTTPException):
        return {}
    finally:
        connection.close()


def enrich_receiver_details(receivers: list[Receiver]) -> list[Receiver]:
    if not receivers:
        return receivers
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(8, len(receivers))) as executor:
        details = list(executor.map(lambda item: probe_roku_device_info(item.address), receivers))
    return [replace(receiver, **detail) if detail else receiver for receiver, detail in zip(receivers, details)]


def choose_video_mode(address: str, quality: str = "auto") -> VideoMode:
    if quality == "720p":
        return VIDEO_MODE_720P
    if quality == "1080p":
        return VIDEO_MODE_1080P
    if quality != "auto":
        raise ValueError(f"unsupported video quality: {quality}")
    details = probe_roku_device_info(address)
    return VIDEO_MODE_720P if details.get("native_height") == 720 else VIDEO_MODE_1080P


def _tlv(kind: int, value: bytes) -> bytes:
    return bytes([kind]) + struct.pack(">H", len(value)) + value


def build_mice_source_ready(name: str, rtsp_port: int = 7236, source_id: bytes = DEFAULT_SOURCE_ID) -> bytes:
    if not 0 < rtsp_port < 65536:
        raise ValueError("RTSP port must be between 1 and 65535")
    if len(source_id) != 16:
        raise ValueError("MICE source ID must be exactly 16 bytes")
    encoded_name = ("\ufeff" + name).encode("utf-16le")
    if len(encoded_name) > 520:
        raise ValueError("MICE friendly name exceeds 520 encoded bytes")
    payload = bytes([MICE_PROTOCOL_VERSION, MICE_SOURCE_READY])
    payload += _tlv(MICE_FRIENDLY_NAME, encoded_name)
    payload += _tlv(MICE_RTSP_PORT, struct.pack(">H", rtsp_port))
    payload += _tlv(MICE_SOURCE_ID, source_id)
    return struct.pack(">H", len(payload) + 2) + payload


def send_mice_source_ready(address: str, name: str, port: int = 7250, timeout: float = 3.0) -> int:
    message = build_mice_source_ready(name)
    with socket.create_connection((address, port), timeout=timeout) as connection:
        connection.sendall(message)
    return len(message)


def decode_avahi_name(value: str) -> str:
    return re.sub(r"\\(\d{3})", lambda match: chr(int(match.group(1), 10)), value)


def discover_mice_receivers(timeout: int = 4) -> list[Receiver]:
    try:
        result = subprocess.run(
            ["avahi-browse", "-rtp", "_display._tcp"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=timeout,
            check=False,
        )
        output = result.stdout
    except subprocess.TimeoutExpired as error:
        # Some receivers advertise immediately but never finish address
        # resolution. Avahi then outlives the discovery window even though its
        # stdout already contains valid resolved entries. Keep those entries
        # instead of turning one slow receiver into a total discovery failure.
        partial = error.stdout or ""
        output = partial.decode("utf-8", "replace") if isinstance(partial, bytes) else partial
    receivers: dict[tuple[str, int], Receiver] = {}
    for line in output.splitlines():
        fields = line.split(";")
        if len(fields) < 9 or fields[0] != "=":
            continue
        interface, family, name, address, port_text = fields[1], fields[2], fields[3], fields[7], fields[8]
        if family != "IPv4":
            continue
        receiver = Receiver(decode_avahi_name(name), address, int(port_text), interface)
        receivers[(receiver.address, receiver.port)] = receiver
    return sorted(receivers.values(), key=lambda item: (item.name.casefold(), item.address))


@dataclass(frozen=True)
class RtspMessage:
    start_line: str
    headers: dict[str, str]
    body: bytes


def read_rtsp_message(stream: BinaryIO) -> RtspMessage:
    start_line_bytes = stream.readline(8192)
    if not start_line_bytes:
        raise EOFError("RTSP peer closed the connection")
    if len(start_line_bytes) >= 8192 or not start_line_bytes.endswith(b"\n"):
        raise ValueError("invalid RTSP start line")
    headers: dict[str, str] = {}
    while True:
        line = stream.readline(8192)
        if line in {b"\r\n", b"\n"}:
            break
        if not line:
            raise EOFError("RTSP peer closed during headers")
        if len(line) >= 8192 or b":" not in line:
            raise ValueError("invalid RTSP header")
        name, value = line.decode("utf-8", "replace").split(":", 1)
        headers[name.strip().lower()] = value.strip()
    content_length = int(headers.get("content-length", "0"))
    if content_length < 0 or content_length > 1_048_576:
        raise ValueError("invalid RTSP content length")
    body = stream.read(content_length)
    if len(body) != content_length:
        raise EOFError("RTSP peer closed during body")
    return RtspMessage(start_line_bytes.decode("utf-8", "replace").strip(), headers, body)


def format_rtsp_request(method: str, uri: str, cseq: int, body: bytes = b"", **headers: str) -> bytes:
    lines = [f"{method} {uri} RTSP/1.0", f"CSeq: {cseq}"]
    lines.extend(f"{name.replace('_', '-')}: {value}" for name, value in headers.items())
    if body:
        lines.append(f"Content-Length: {len(body)}")
    return ("\r\n".join(lines) + "\r\n\r\n").encode() + body


def format_rtsp_ok(request: RtspMessage) -> bytes:
    cseq = request.headers.get("cseq", "0")
    public = "org.wfa.wfd1.0, OPTIONS, DESCRIBE, GET_PARAMETER, PAUSE, PLAY, SETUP, SET_PARAMETER, TEARDOWN"
    return f"RTSP/1.0 200 OK\r\nCSeq: {cseq}\r\nPublic: {public}\r\nContent-Length: 0\r\n\r\n".encode()


def probe_wfd_capabilities(address: str, friendly_name: str, listen_port: int = 7236, timeout: float = 30.0) -> list[RtspMessage]:
    query = (
        b"wfd_client_rtp_ports\r\n"
        b"wfd_audio_codecs\r\n"
        b"wfd_video_formats\r\n"
        b"wfd_display_edid\r\n"
        b"wfd_idr_request_capability\r\n"
        b"microsoft_cursor\r\n"
    )
    transcript: list[RtspMessage] = []
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("0.0.0.0", listen_port))
        listener.listen(1)
        listener.settimeout(timeout)
        send_mice_source_ready(address, friendly_name)
        connection, peer = listener.accept()
        if peer[0] != address:
            connection.close()
            raise RuntimeError(f"unexpected RTSP peer {peer[0]}")
        with connection:
            connection.settimeout(timeout)
            stream = connection.makefile("rwb", buffering=0)
            stream.write(format_rtsp_request("OPTIONS", "*", 1, Require="org.wfa.wfd1.0"))
            transcript.append(read_rtsp_message(stream))
            sink_options = read_rtsp_message(stream)
            transcript.append(sink_options)
            if not sink_options.start_line.startswith("OPTIONS "):
                raise RuntimeError(f"expected receiver OPTIONS, got {sink_options.start_line}")
            stream.write(format_rtsp_ok(sink_options))
            stream.write(
                format_rtsp_request(
                    "GET_PARAMETER",
                    "rtsp://localhost/wfd1.0",
                    2,
                    query,
                    Content_Type="text/parameters",
                )
            )
            transcript.append(read_rtsp_message(stream))
    return transcript
