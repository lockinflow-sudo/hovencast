#!/usr/bin/python

import argparse
import contextlib
import io
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from omarchy_cast import (  # noqa: E402
    _last_session_failure,
    cast_live_command,
    cast_session_snapshot,
    process_start_ticks,
    run_source_list,
    run_session_runner,
    run_session_start,
    session_runner_command,
)


def arguments(**overrides: object) -> argparse.Namespace:
    values = {
        "address": "192.168.1.240",
        "receiver_name": "Bedroom TV",
        "name": "HovenCast",
        "source_id": "HovenCastSender1",
        "duration": 0,
        "quality": "720p",
        "workspace": "1",
        "source_kind": "",
        "source_output": "",
        "source_region": "",
        "source_window_address": "",
        "source_window_class": "",
        "source_window_title": "",
        "placement": "above",
        "keep_local_audio": False,
        "ipc_target": "hoven.cast",
    }
    values.update(overrides)
    return argparse.Namespace(**values)


class CastSessionTest(unittest.TestCase):
    @mock.patch("omarchy_cast.subprocess.run")
    def test_source_list_removes_the_legacy_desktop_preview(
        self, run: mock.Mock
    ) -> None:
        run.side_effect = [
            subprocess.CompletedProcess(
                [],
                0,
                stdout=json.dumps(
                    [
                        {
                            "id": 0,
                            "name": "eDP-1",
                            "description": "Built-in display",
                            "width": 1920,
                            "height": 1200,
                            "focused": True,
                        }
                    ]
                ),
            ),
            subprocess.CompletedProcess([], 0, stdout="[]"),
        ]
        with tempfile.TemporaryDirectory() as temporary:
            runtime = pathlib.Path(temporary)
            legacy = runtime / "hovencast/share-source-preview.png"
            legacy.parent.mkdir()
            legacy.write_bytes(b"old preview")
            output = io.StringIO()
            with mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": str(runtime)}):
                with contextlib.redirect_stdout(output):
                    self.assertEqual(run_source_list(argparse.Namespace()), 0)

            self.assertFalse(legacy.exists())
            self.assertEqual(json.loads(output.getvalue())["preview"], "")

    def test_command_preserves_workspace_and_picker_target(self) -> None:
        command = cast_live_command(arguments())

        self.assertIn("cast-live", command)
        self.assertEqual(command[command.index("--workspace") + 1], "1")
        self.assertEqual(command[command.index("--placement") + 1], "above")
        self.assertEqual(command[command.index("--ipc-target") + 1], "hoven.cast")

        runner = session_runner_command(arguments())
        self.assertEqual(runner[2], "session-run")

    def test_command_preserves_preselected_window(self) -> None:
        command = cast_live_command(
            arguments(
                workspace="",
                source_kind="window",
                source_output="eDP-1",
                source_window_address="0x1234",
                source_window_class="chatgpt",
                source_window_title="HovenCast frontend chat",
            )
        )

        self.assertEqual(command[command.index("--source-kind") + 1], "window")
        self.assertEqual(command[command.index("--source-output") + 1], "eDP-1")
        self.assertEqual(
            command[command.index("--source-window-address") + 1], "0x1234"
        )
        self.assertNotIn("--workspace", command)

    def test_snapshot_reconnects_to_running_stream(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            runtime = pathlib.Path(temporary)
            state_root = runtime / "hovencast"
            state_root.mkdir()
            (state_root / "cast-session.json").write_text(
                json.dumps(
                    {
                        "pid": os.getpid(),
                        "start_ticks": process_start_ticks(os.getpid()),
                        "started_at": 1,
                        "address": "192.168.1.240",
                        "receiver": "Bedroom TV",
                        "workspace": "1",
                        "stop_requested": False,
                    }
                ),
                encoding="utf-8",
            )
            (state_root / "session.events.jsonl").write_text(
                json.dumps(
                    {
                        "time": 2,
                        "event": "metrics",
                        "video_frames": 90,
                        "audio_buffers": 140,
                        "bitrate_bps": 4_000_000,
                        "av_drift_ms": 2.5,
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            with mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": str(runtime)}):
                snapshot = cast_session_snapshot()

        self.assertTrue(snapshot["running"])
        self.assertEqual(snapshot["state"], "streaming")
        self.assertEqual(snapshot["workspace"], "1")
        self.assertEqual(snapshot["video_frames"], 90)

    def test_last_failure_ignores_cleanup_events(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            runtime = pathlib.Path(temporary)
            state_root = runtime / "hovencast"
            state_root.mkdir()
            (state_root / "session.events.jsonl").write_text(
                "\n".join(
                    json.dumps({"time": index, "event": event})
                    for index, event in enumerate(
                        ("startup-stalled", "process-stopped", "stopped"), start=1
                    )
                )
                + "\n",
                encoding="utf-8",
            )
            with mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": str(runtime)}):
                failure = _last_session_failure()

        self.assertEqual(failure, "startup-stalled")

    def test_snapshot_reports_connecting_during_recovery(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            runtime = pathlib.Path(temporary)
            state_root = runtime / "hovencast"
            state_root.mkdir()
            (state_root / "cast-session.json").write_text(
                json.dumps(
                    {
                        "pid": os.getpid(),
                        "start_ticks": process_start_ticks(os.getpid()),
                        "started_at": 1,
                    }
                ),
                encoding="utf-8",
            )
            (state_root / "session.events.jsonl").write_text(
                "\n".join(
                    json.dumps(record)
                    for record in (
                        {"time": 2, "event": "live-cast-requested"},
                        {"time": 3, "event": "metrics", "video_frames": 60},
                        {"time": 4, "event": "startup-stalled", "error": "stale"},
                        {"time": 5, "event": "connection-recovery-started"},
                    )
                )
                + "\n",
                encoding="utf-8",
            )
            with mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": str(runtime)}):
                snapshot = cast_session_snapshot()

        self.assertEqual(snapshot["state"], "connecting")
        self.assertEqual(snapshot["video_frames"], 0)
        self.assertEqual(snapshot["error"], "")

    @mock.patch("omarchy_cast.cast_session_snapshot", return_value={"running": True})
    @mock.patch("omarchy_cast.process_start_ticks", return_value=12345)
    @mock.patch("omarchy_cast.subprocess.Popen")
    def test_session_start_detaches_from_panel_process(
        self,
        popen: mock.Mock,
        _start_ticks: mock.Mock,
        _snapshot: mock.Mock,
    ) -> None:
        popen.return_value.pid = 4242
        with tempfile.TemporaryDirectory() as temporary:
            runtime = pathlib.Path(temporary)
            with mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": str(runtime)}):
                run_session_start(arguments())
                state = json.loads(
                    (runtime / "hovencast/cast-session.json").read_text(encoding="utf-8")
                )

        self.assertEqual(state["pid"], 4242)
        self.assertEqual(state["workspace"], "1")
        self.assertEqual(state["placement"], "above")
        self.assertTrue(popen.call_args.kwargs["start_new_session"])
        self.assertTrue(popen.call_args.kwargs["close_fds"])
        self.assertEqual(popen.call_args.args[0][2], "session-run")

    @mock.patch("omarchy_cast.time.sleep")
    @mock.patch("omarchy_cast.recover_capture_portal", return_value=True)
    @mock.patch("omarchy_cast._last_session_failure", return_value="startup-stalled")
    @mock.patch("omarchy_cast.subprocess.run")
    def test_session_runner_retries_failed_startup(
        self,
        run: mock.Mock,
        _last_event: mock.Mock,
        recover: mock.Mock,
        _sleep: mock.Mock,
    ) -> None:
        run.side_effect = [
            subprocess.CompletedProcess([], 1),
            subprocess.CompletedProcess([], 0),
        ]

        result = run_session_runner(arguments())

        self.assertEqual(result, 0)
        self.assertEqual(run.call_count, 2)
        self.assertEqual(run.call_args_list[0].kwargs["env"]["HOVENCAST_ATTEMPT"], "1")
        self.assertEqual(run.call_args_list[1].kwargs["env"]["HOVENCAST_ATTEMPT"], "2")
        recover.assert_called_once_with(2)


if __name__ == "__main__":
    unittest.main()
