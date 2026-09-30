#!/usr/bin/python

import pathlib
import io
import struct
import sys
import time
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from omarchy_cast_protocol import (  # noqa: E402
    Receiver,
    RtspMessage,
    MAX_AVAHI_OUTPUT_BYTES,
    MAX_AVAHI_LINE_BYTES,
    MAX_MICE_RECEIVERS,
    _read_avahi_output,
    build_mice_source_ready,
    choose_video_mode,
    decode_avahi_name,
    discover_mice_receivers,
    enrich_receiver_details,
    format_rtsp_ok,
    format_rtsp_request,
    parse_roku_device_info,
    read_rtsp_message,
)


class MiceSourceReadyTest(unittest.TestCase):
    def test_message_has_length_version_command_and_tlvs(self) -> None:
        message = build_mice_source_ready("Omarchy")
        self.assertEqual(struct.unpack(">H", message[:2])[0], len(message))
        self.assertEqual(message[2:4], bytes([1, 1]))
        self.assertIn(bytes([2, 0, 2, 0x1C, 0x44]), message)
        self.assertTrue(message.endswith(bytes([3, 0, 16]) + b"HovenCastSender1"))

    def test_name_is_bom_prefixed_utf16le(self) -> None:
        message = build_mice_source_ready("Desk")
        self.assertEqual(message[4], 0)
        name_length = struct.unpack(">H", message[5:7])[0]
        encoded_name = message[7 : 7 + name_length]
        self.assertEqual(encoded_name, "\ufeffDesk".encode("utf-16le"))

    def test_source_id_must_be_sixteen_bytes(self) -> None:
        with self.assertRaisesRegex(ValueError, "exactly 16"):
            build_mice_source_ready("Desk", source_id=b"short")


class AvahiDecodeTest(unittest.TestCase):
    def test_decimal_escapes(self) -> None:
        self.assertEqual(decode_avahi_name(r"55\034\032TCL\032Roku\032TV"), '55" TCL Roku TV')

    @mock.patch("omarchy_cast_protocol._read_avahi_output")
    def test_discovery_keeps_resolved_entries_after_timeout(self, read_output: mock.Mock) -> None:
        partial = (
            "=;wlan0;IPv4;55\\034\\032TCL\\032Roku\\032TV;_display._tcp;local;"
            "roku.local;192.168.1.225;7250;\n"
        )
        read_output.return_value = partial.encode()

        receivers = discover_mice_receivers()

        self.assertEqual(len(receivers), 1)
        self.assertEqual(receivers[0].name, '55" TCL Roku TV')
        self.assertEqual(receivers[0].address, "192.168.1.225")

    def test_reader_caps_flooded_output(self) -> None:
        script = "import sys,time; sys.stdout.buffer.write(b'x' * 131072); sys.stdout.flush(); time.sleep(2)"
        started = time.monotonic()
        output = _read_avahi_output(1, [sys.executable, "-c", script])

        self.assertEqual(len(output), MAX_AVAHI_OUTPUT_BYTES)
        self.assertLess(time.monotonic() - started, 1)

    def test_reader_keeps_complete_lines_on_timeout(self) -> None:
        script = (
            "import sys,time; sys.stdout.buffer.write(b'=;wlan0;IPv4;TV;_display._tcp;"
            "local;tv.local;192.168.1.10;7250;\\n'); sys.stdout.flush(); time.sleep(2)"
        )
        started = time.monotonic()
        output = _read_avahi_output(0.1, [sys.executable, "-c", script])

        self.assertIn(b"192.168.1.10;7250;\n", output)
        self.assertLess(time.monotonic() - started, 1)

    @mock.patch("omarchy_cast_protocol._read_avahi_output")
    def test_discovery_skips_oversized_line_and_invalid_port(self, read_output: mock.Mock) -> None:
        oversized = b"=" + b"x" * MAX_AVAHI_LINE_BYTES + b"\n"
        invalid_port = b"=;wlan0;IPv4;TV;_display._tcp;local;tv.local;192.168.1.10;bad;\n"
        read_output.return_value = oversized + invalid_port

        self.assertEqual(discover_mice_receivers(), [])

    @mock.patch("omarchy_cast_protocol._read_avahi_output")
    def test_discovery_caps_receiver_count(self, read_output: mock.Mock) -> None:
        read_output.return_value = b"".join(
            f"=;wlan0;IPv4;TV{i};_display._tcp;local;tv.local;192.168.1.{i};7250;\n".encode()
            for i in range(1, MAX_MICE_RECEIVERS + 10)
        )

        self.assertEqual(len(discover_mice_receivers()), MAX_MICE_RECEIVERS)


class ReceiverProfileTest(unittest.TestCase):
    def test_roku_device_info_resolution(self) -> None:
        info = parse_roku_device_info(
            b"<device-info><vendor-name>TCL</vendor-name><model-name>32S331</model-name>"
            b"<ui-resolution>720p</ui-resolution></device-info>"
        )
        self.assertEqual(info["native_width"], 1280)
        self.assertEqual(info["native_height"], 720)
        self.assertEqual(info["model"], "32S331")
        self.assertEqual(info["vendor"], "TCL")

    @mock.patch("omarchy_cast_protocol.probe_roku_device_info")
    def test_discovery_accepts_unlisted_roku_models_with_device_info(
        self, probe: mock.Mock
    ) -> None:
        receivers = [
            Receiver("Tested", "192.168.1.10", 7250, "wlan0"),
            Receiver("Untested", "192.168.1.11", 7250, "wlan0"),
            Receiver("Unverified", "192.168.1.12", 7250, "wlan0"),
        ]
        probe.side_effect = [
            {"vendor": "TCL", "model": "32S331", "native_width": 1280, "native_height": 720},
            {"vendor": "Hisense", "model": "R6", "native_width": 1920, "native_height": 1080},
            {},
        ]

        supported = enrich_receiver_details(receivers)

        self.assertEqual(
            [receiver.address for receiver in supported],
            ["192.168.1.10", "192.168.1.11"],
        )
        self.assertEqual(supported[1].vendor, "Hisense")
        self.assertEqual(supported[1].model, "R6")

    @mock.patch("omarchy_cast_protocol.probe_roku_device_info")
    def test_auto_mode_matches_720p_roku_panel(self, probe: mock.Mock) -> None:
        probe.return_value = {"native_width": 1280, "native_height": 720}
        mode = choose_video_mode("192.168.1.240")
        self.assertEqual((mode.width, mode.height), (1280, 720))
        self.assertEqual(mode.cea_bitmap, "00000020")

    @mock.patch("omarchy_cast_protocol.probe_roku_device_info")
    def test_auto_mode_preserves_1080p_default(self, probe: mock.Mock) -> None:
        probe.return_value = {}
        mode = choose_video_mode("192.168.1.225")
        self.assertEqual((mode.width, mode.height), (1920, 1080))
        self.assertEqual(mode.cea_bitmap, "00000080")


class RtspTest(unittest.TestCase):
    def test_read_message_with_body(self) -> None:
        raw = b"RTSP/1.0 200 OK\r\nCSeq: 2\r\nContent-Length: 5\r\n\r\nhello"
        message = read_rtsp_message(io.BytesIO(raw))
        self.assertEqual(message.start_line, "RTSP/1.0 200 OK")
        self.assertEqual(message.headers["cseq"], "2")
        self.assertEqual(message.body, b"hello")

    def test_rejects_unbounded_body(self) -> None:
        raw = b"RTSP/1.0 200 OK\r\nContent-Length: 1048577\r\n\r\n"
        with self.assertRaisesRegex(ValueError, "content length"):
            read_rtsp_message(io.BytesIO(raw))

    def test_format_request_and_response(self) -> None:
        request = format_rtsp_request("OPTIONS", "*", 7, Require="org.wfa.wfd1.0")
        self.assertIn(b"OPTIONS * RTSP/1.0\r\nCSeq: 7\r\n", request)
        response = format_rtsp_ok(RtspMessage("OPTIONS * RTSP/1.0", {"cseq": "9"}, b""))
        self.assertIn(b"RTSP/1.0 200 OK\r\nCSeq: 9\r\n", response)


if __name__ == "__main__":
    unittest.main()
