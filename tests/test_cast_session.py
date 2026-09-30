#!/usr/bin/python

import argparse
import json
import os
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from omarchy_cast import (  # noqa: E402
    cast_live_command,
    cast_session_snapshot,
    process_start_ticks,
    run_session_start,
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
        "keep_local_audio": False,
        "ipc_target": "hoven.cast",
    }
    values.update(overrides)
    return argparse.Namespace(**values)


class CastSessionTest(unittest.TestCase):
    def test_command_preserves_workspace_and_picker_target(self) -> None:
        command = cast_live_command(arguments())

        self.assertIn("cast-live", command)
        self.assertEqual(command[command.index("--workspace") + 1], "1")
        self.assertEqual(command[command.index("--ipc-target") + 1], "hoven.cast")

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
        self.assertTrue(popen.call_args.kwargs["start_new_session"])
        self.assertTrue(popen.call_args.kwargs["close_fds"])


if __name__ == "__main__":
    unittest.main()
