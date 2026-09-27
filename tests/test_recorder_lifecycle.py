#!/usr/bin/python

import io
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

import gi  # noqa: E402

gi.require_version("Gst", "1.0")
from gi.repository import GLib, Gst  # noqa: E402
from omarchy_cast import (  # noqa: E402
    Counters,
    Recorder,
    SilentAudioRoute,
    recover_orphan_audio_route,
    window_crop_geometry,
)


class FakeProcess:
    def __init__(self, name: str, calls: list[str]) -> None:
        self.name = name
        self.calls = calls
        self.running = True

    def poll(self) -> int | None:
        return None if self.running else -15

    def terminate(self) -> None:
        self.calls.append(f"{self.name}:terminate")

    def wait(self, timeout: float | None = None) -> int:
        self.calls.append(f"{self.name}:wait")
        self.running = False
        return -15

    def kill(self) -> None:
        self.calls.append(f"{self.name}:kill")


class FakePipeline:
    def __init__(self, calls: list[str]) -> None:
        self.calls = calls

    def set_state(self, state: Gst.State) -> None:
        self.calls.append(f"pipeline:{state.value_nick}")


class FakeBranchElement:
    def __init__(self, name: str, calls: list[str]) -> None:
        self.name = name
        self.calls = calls

        self.properties: dict[str, object] = {}

    def set_state(self, state: Gst.State) -> Gst.StateChangeReturn:
        self.calls.append(f"{self.name}:{state.value_nick}")
        return Gst.StateChangeReturn.SUCCESS

    def set_property(self, name: str, value: object) -> None:
        self.properties[name] = value
        self.calls.append(f"{self.name}:property:{name}")

    def sync_state_with_parent(self) -> bool:
        self.calls.append(f"{self.name}:sync")
        return True


class FakeBranchPipeline:
    def __init__(self, names: tuple[str, ...], calls: list[str]) -> None:
        self.elements = {name: FakeBranchElement(name, calls) for name in names}

    def get_by_name(self, name: str) -> FakeBranchElement | None:
        return self.elements.get(name)


class FakeLoop:
    def __init__(self, calls: list[str]) -> None:
        self.calls = calls

    def quit(self) -> None:
        self.calls.append("loop:quit")


class RecorderLifecycleTest(unittest.TestCase):
    @mock.patch("omarchy_cast.process_is_running", return_value=False)
    @mock.patch.object(SilentAudioRoute, "stop")
    def test_orphan_audio_route_is_recovered(
        self, stop: mock.Mock, _running: mock.Mock
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            runtime = pathlib.Path(temporary)
            state_root = runtime / "omacast"
            state_root.mkdir()
            (state_root / "audio-route.json").write_text(
                json.dumps(
                    {
                        "pid": 999999,
                        "previous_sink": "speakers",
                        "sink_name": "omarchy_cast_999999",
                        "module_id": 42,
                    }
                ),
                encoding="utf-8",
            )
            with mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": str(runtime)}):
                recover_orphan_audio_route()

        stop.assert_called_once_with(announce=False)

    @mock.patch("omarchy_cast.process_is_running", return_value=True)
    @mock.patch.object(SilentAudioRoute, "stop")
    def test_live_audio_route_is_not_recovered(
        self, stop: mock.Mock, _running: mock.Mock
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            runtime = pathlib.Path(temporary)
            state_root = runtime / "omacast"
            state_root.mkdir()
            (state_root / "audio-route.json").write_text(
                json.dumps(
                    {
                        "pid": os.getpid(),
                        "previous_sink": "speakers",
                        "sink_name": "omarchy_cast_live",
                        "module_id": 43,
                    }
                ),
                encoding="utf-8",
            )
            with mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": str(runtime)}):
                recover_orphan_audio_route()

        stop.assert_not_called()

    def test_stop_closes_fifo_consumers_before_pipeline(self) -> None:
        calls: list[str] = []
        recorder = Recorder.__new__(Recorder)
        recorder.stopping = False
        recorder.sender_process = FakeProcess("receiver", calls)
        recorder.auxiliary_processes = [FakeProcess("transport", calls)]
        recorder.pipeline = FakePipeline(calls)
        recorder.tone_process = None
        recorder.audio_route = None
        recorder.session = None
        recorder.event_file = io.StringIO()
        recorder.counters = Counters()
        recorder.loop = FakeLoop(calls)

        recorder._stop("test")

        self.assertLess(calls.index("receiver:terminate"), calls.index("pipeline:null"))
        self.assertLess(calls.index("transport:terminate"), calls.index("pipeline:null"))
        self.assertEqual(calls[-1], "loop:quit")

    def test_live_capture_with_frozen_output_triggers_watchdog(self) -> None:
        reasons: list[str] = []
        recorder = Recorder.__new__(Recorder)
        recorder.stopping = False
        recorder.sender_process = object()
        recorder.event_file = io.StringIO()
        recorder.counters = Counters(
            capture_frames=300,
            last_capture_monotonic=time.monotonic(),
            video_frames=150,
            audio_buffers=250,
            video_bytes=1_000,
            audio_bytes=1_000,
        )
        recorder.previous_bytes = 2_000
        recorder.output_stall_ticks = 7
        recorder.previous_capture_frames = 300
        recorder.previous_video_frames = 150
        recorder.window_capture_stall_ticks = 0
        recorder.window_video_stall_ticks = 0
        recorder.window_video_recovery_attempts = 0
        recorder.source_kind = "screen"
        recorder.exit_code = 0

        def stop(reason: str) -> None:
            reasons.append(reason)
            recorder.stopping = True

        recorder._stop = stop

        result = recorder._metrics()

        self.assertEqual(result, GLib.SOURCE_REMOVE)
        self.assertEqual(reasons, ["output-stalled"])
        self.assertEqual(recorder.exit_code, 1)
        self.assertIn('"event": "output-stalled"', recorder.event_file.getvalue())

    def test_window_stall_restarts_only_video_branch(self) -> None:
        names = (
            "video_capture_queue",
            "video_convert",
            "video_scale",
            "video_rate",
            "video_output_caps",
            "video_encode_queue",
            "video_encoder",
            "encoded_video",
            "video_mux_queue",
        )
        calls: list[str] = []
        recorder = Recorder.__new__(Recorder)
        recorder.pipeline = FakeBranchPipeline(names, calls)
        recorder.event_file = io.StringIO()
        recorder.window_video_recovery_attempts = 0
        recorder.capture_caps = "video/x-raw,width=1920,height=1080"

        recovered = recorder._restart_window_video_branch()

        self.assertTrue(recovered)
        self.assertEqual(recorder.window_video_recovery_attempts, 1)
        self.assertNotIn("portal_video:ready", calls)
        self.assertEqual(calls[0], "video_mux_queue:ready")
        self.assertEqual(calls[-1], "video_mux_queue:sync")

    @mock.patch("omarchy_cast.subprocess.run")
    def test_window_crop_tracks_hyprland_geometry(self, run: mock.Mock) -> None:
        run.side_effect = [
            subprocess.CompletedProcess(
                [],
                0,
                stdout='[{"address":"0xabc","at":[640,40],"size":[640,720]}]',
            ),
            subprocess.CompletedProcess(
                [],
                0,
                stdout='[{"name":"eDP-1","x":0,"y":0,"width":1920,"height":1200,"scale":1}]',
            ),
        ]

        # The picker supplies the pointer address in decimal while hyprctl
        # reports the same client address in hexadecimal.
        geometry = window_crop_geometry(str(int("abc", 16)), "eDP-1")

        self.assertEqual(geometry.left, 640)
        self.assertEqual(geometry.right, 640)
        self.assertEqual(geometry.top, 40)
        self.assertEqual(geometry.bottom, 440)
        self.assertEqual(geometry.width, 640)
        self.assertEqual(geometry.height, 720)


if __name__ == "__main__":
    unittest.main()
