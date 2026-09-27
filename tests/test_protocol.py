#!/usr/bin/python

import pathlib
import io
import subprocess
import struct
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from omarchy_cast_protocol import (  # noqa: E402
    Receiver,
    RtspMessage,
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
        self.assertTrue(message.endswith(bytes([3, 0, 16]) + b"OmaCastSender001"))

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

    @mock.patch("omarchy_cast_protocol.subprocess.run")
    def test_discovery_keeps_resolved_entries_after_timeout(self, run: mock.Mock) -> None:
        partial = (
            "=;wlan0;IPv4;55\\034\\032TCL\\032Roku\\032TV;_display._tcp;local;"
            "roku.local;192.168.1.225;7250;\n"
        )
        run.side_effect = subprocess.TimeoutExpired(
            ["avahi-browse"],
            4,
            output=partial.encode(),
        )

        receivers = discover_mice_receivers()

        self.assertEqual(len(receivers), 1)
        self.assertEqual(receivers[0].name, '55" TCL Roku TV')
        self.assertEqual(receivers[0].address, "192.168.1.225")


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
    def test_discovery_filters_unverified_models(self, probe: mock.Mock) -> None:
        receivers = [
            Receiver("Supported", "192.168.1.10", 7250, "wlan0"),
            Receiver("Unknown", "192.168.1.11", 7250, "wlan0"),
        ]
        probe.side_effect = [
            {"vendor": "TCL", "model": "32S331", "native_width": 1280, "native_height": 720},
            {"vendor": "Other", "model": "Fake", "native_width": 1920, "native_height": 1080},
        ]

        supported = enrich_receiver_details(receivers)

        self.assertEqual([receiver.address for receiver in supported], ["192.168.1.10"])

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
