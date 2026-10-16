from __future__ import annotations

import concurrent.futures
import http.client
import os
import re
import select
import socket
import struct
import subprocess
import time
import xml.etree.ElementTree as ET
from typing import Any, BinaryIO
from dataclasses import asdict, dataclass, replace

from cast_identity import (
    DiscoveryService, IdentityEvidence, identity_claims,
    normalize_guid, normalize_roku_id,
)


MICE_PROTOCOL_VERSION = 1
MICE_SOURCE_READY = 1
MICE_FRIENDLY_NAME = 0
MICE_RTSP_PORT = 2
MICE_SOURCE_ID = 3
DEFAULT_SOURCE_ID = b"HovenCastSender1"
MAX_AVAHI_OUTPUT_BYTES = 65_536
MAX_AVAHI_LINE_BYTES = 4_096
MAX_AVAHI_RECORDS = 256
MAX_MICE_RECEIVERS = 64


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
    vendor: str = ""
    # Internal observations only. Keep the legacy discovery wire format stable.
    discovery_service: DiscoveryService | None = None
    identity_evidence: tuple[IdentityEvidence, ...] = ()

    def json(self) -> dict[str, Any]:
        legacy = asdict(self)
        legacy.pop("discovery_service")
        legacy.pop("identity_evidence")
        return legacy


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


def p2p_video_mode(mode: VideoMode) -> VideoMode:
    """Keep receiver resolution/profile, but bound interactive encoding latency."""
    return replace(mode, bitrate_kbps=5000 if mode.height <= 720 else 6500,
                   encoder_preset="superfast", queue_time_ns=66_666_667,
                   vbv_buffer_ms=200)


def live_remux_command(source: str, destination: str, *, p2p: bool = False) -> list[str]:
    command = ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "fatal", "-y"]
    if p2p:
        command += ["-probesize", "131072", "-analyzeduration", "200000"]
    command += ["-i", source, "-map", "0:v:0", "-map", "0:a:0", "-c", "copy",
                "-mpegts_start_pid", "256", "-mpegts_pmt_start_pid", "4096",
                # A hardware decoder needs PCR-to-presentation headroom;
                # the zero-delay trial passed packet tests but coincided with
                # a receiver-side freeze during workspace/video changes.
                "-muxdelay", "0.075" if p2p else "0.7",
                "-muxpreload", "0.075" if p2p else "0.7"]
    if p2p:
        command += ["-flush_packets", "1"]
    return command + ["-f", "mpegts", destination]


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
    details: dict[str, Any] = {
        "native_width": dimensions[0],
        "native_height": dimensions[1],
        "model": str(root.findtext("model-name") or "").strip(),
        "vendor": str(root.findtext("vendor-name") or "").strip(),
    }
    evidence = ()
    for tag, namespace, normalize in (
        ("udn", "roku-udn", normalize_guid),
        ("serial-number", "roku-serial-number", normalize_roku_id),
        ("device-id", "roku-device-id", normalize_roku_id),
    ):
        evidence += identity_claims(
            namespace, (node.text or "" for node in root.findall(tag) if not len(node)),
            "roku-ecp", normalize,
        )
    if evidence:
        details["identity_evidence"] = evidence
    return details


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
    supported = []
    for receiver, detail in zip(receivers, details):
        # A resolved MICE advertisement is only shown after the device also
        # answers Roku's ECP device-info endpoint with a usable resolution.
        # Model names are compatibility metadata, not a security boundary.
        if not detail:
            continue
        # ECP supplements the mDNS claim; it must not overwrite it. Do not
        # reconcile differing namespaces or suppress conflicting values here.
        metadata = dict(detail)
        evidence = metadata.pop("identity_evidence", ())
        combined = tuple(dict.fromkeys((*receiver.identity_evidence, *evidence)))
        supported.append(replace(receiver, **metadata, identity_evidence=combined))
    return supported


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


def parse_mice_identity(txt: str) -> tuple[IdentityEvidence, ...]:
    """Extract only container_id from Avahi's quoted TXT string list.

    Preserve Avahi decimal escaping until after tokenization (shlex would
    consume the backslashes). Reject malformed framing, not the receiver.
    Other TXT entries are deliberately not retained as personal metadata.
    """
    if len(txt) > MAX_AVAHI_LINE_BYTES:
        return ()
    remaining = txt.strip()
    values = []
    while remaining:
        match = re.match(r'"((?:[^"\\]|\\.)*)"(?:\s+|$)', remaining)
        if not match:
            return ()
        # TXT quoting also escapes literal backslashes and quotes. Decode in
        # one pass so an escaped literal backslash cannot become a decimal
        # escape during a second pass.
        def unescape(escape: re.Match[str]) -> str:
            value = escape[1]
            if value.isdigit():
                number = int(value)
                return chr(number) if number <= 255 else "\x00"
            return value

        item = re.sub(r'\\([0-9]{3}|["\\])', unescape, match[1])
        key, separator, value = item.partition("=")
        if separator and key.casefold() == "container_id":
            values.append(value)
        remaining = remaining[match.end():]
    return identity_claims("mice-container-id", values, "mice-mdns", normalize_guid)


def parse_mice_service(fields: list[str]) -> DiscoveryService | None:
    values = tuple(decode_avahi_name(value) for value in (fields[3], fields[4], fields[5], fields[6]))
    if any(not value or len(value) > 255 or any(ord(char) < 32 or ord(char) == 127 for char in value)
           for value in values):
        return None
    return DiscoveryService(*values)


def _read_avahi_output(timeout: float, command: list[str] | None = None) -> bytes:
    # communicate() and run(..., stdout=PIPE) buffer without a size ceiling.
    # Read only a bounded amount, even if a network responder floods Avahi.
    process = subprocess.Popen(
        command or ["avahi-browse", "-rtp", "_display._tcp"],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    assert process.stdout is not None
    output = bytearray()
    deadline = time.monotonic() + max(0.0, timeout)
    try:
        while len(output) < MAX_AVAHI_OUTPUT_BYTES:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            readable, _, _ = select.select([process.stdout], [], [], remaining)
            if not readable:
                break
            chunk = os.read(process.stdout.fileno(), min(4096, MAX_AVAHI_OUTPUT_BYTES - len(output)))
            if not chunk:
                break
            output.extend(chunk)
    finally:
        process.stdout.close()
        if process.poll() is None:
            process.terminate()
        try:
            process.wait(timeout=0.5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=0.5)
    return bytes(output)


def discover_mice_receivers(timeout: int = 4) -> list[Receiver]:
    # A slow receiver may leave Avahi running after valid entries arrive.
    # Keep complete entries already read, but never retain unbounded output.
    output = _read_avahi_output(timeout)
    complete_output = output.rsplit(b"\n", 1)[0] if b"\n" in output else b""
    receivers: dict[tuple[str, int], Receiver] = {}
    for line in complete_output.split(b"\n", MAX_AVAHI_RECORDS)[:MAX_AVAHI_RECORDS]:
        if len(line) > MAX_AVAHI_LINE_BYTES:
            continue
        line = line.decode("utf-8", "replace").rstrip("\r")
        fields = line.split(";")
        if len(fields) < 9 or fields[0] != "=":
            continue
        interface, family, name, address, port_text = fields[1], fields[2], fields[3], fields[7], fields[8]
        if family != "IPv4":
            continue
        try:
            port = int(port_text)
        except ValueError:
            continue
        if not 0 < port < 65536:
            continue
        receiver = Receiver(
            decode_avahi_name(name), address, port, interface,
            discovery_service=parse_mice_service(fields),
            identity_evidence=parse_mice_identity(";".join(fields[9:])),
        )
        receivers[(receiver.address, receiver.port)] = receiver
        if len(receivers) >= MAX_MICE_RECEIVERS:
            break
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
