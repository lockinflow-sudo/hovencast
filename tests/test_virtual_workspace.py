from __future__ import annotations

import json
import os
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from virtual_workspace import (  # noqa: E402
    Focus,
    VirtualWorkspace,
    _process_start_ticks,
    _safe_placement,
    _safe_workspace,
    _state_process_is_running,
)


PHYSICAL_MONITOR = {
    "name": "eDP-1",
    "focused": True,
    "x": 0,
    "y": 0,
    "width": 1920,
    "height": 1200,
    "scale": 1.5,
    "activeWorkspace": {"name": "1"},
}


class VirtualWorkspaceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.environment = mock.patch.dict(
            os.environ, {"XDG_RUNTIME_DIR": self.temporary.name}
        )
        self.environment.start()
        self.manager = VirtualWorkspace(run=mock.Mock())

    def tearDown(self) -> None:
        self.environment.stop()
        self.temporary.cleanup()

    def write_state(self, **updates: object) -> dict[str, object]:
        state: dict[str, object] = {
            "version": 1,
            "pid": os.getpid(),
            "start_ticks": _process_start_ticks(os.getpid()),
            "output": self.manager.output,
            "width": 1280,
            "height": 720,
            "local_focus": {"monitor": "eDP-1", "workspace": "1"},
            "restore_focus": {"monitor": "eDP-1", "workspace": "1"},
            "selected_workspace": "",
            "workspace_origins": {},
        }
        state.update(updates)
        self.manager._write_state(state)
        return state

    def test_workspace_name_validation(self) -> None:
        self.assertEqual(_safe_workspace(" web "), "web")
        for invalid in ("", "special:scratchpad", "bad\nname", "x" * 129):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                _safe_workspace(invalid)

    def test_display_placement_validation(self) -> None:
        for placement in ("left", "above", "below", "right"):
            self.assertEqual(_safe_placement(placement), placement)
        with self.assertRaises(ValueError):
            _safe_placement("diagonal")

    def test_process_identity_rejects_a_reused_pid(self) -> None:
        state = {
            "pid": os.getpid(),
            "start_ticks": (_process_start_ticks(os.getpid()) or 0) + 1,
        }
        self.assertFalse(_state_process_is_running(state))

    def test_process_identity_accepts_the_current_process(self) -> None:
        state = {
            "pid": os.getpid(),
            "start_ticks": _process_start_ticks(os.getpid()),
        }
        self.assertTrue(_state_process_is_running(state))

    def test_start_creates_a_tv_sized_headless_output(self) -> None:
        commands: list[list[str]] = []

        def command(arguments: list[str], check: bool = True) -> str:
            commands.append(arguments)
            return "ok"

        self.manager._command = command  # type: ignore[method-assign]
        self.manager.monitors = mock.Mock(return_value=[PHYSICAL_MONITOR])
        self.manager.focus = mock.Mock(return_value=Focus("eDP-1", "1"))

        def select(workspace: str, _focus: Focus) -> None:
            state = self.manager._read_state()
            state["selected_workspace"] = workspace
            self.manager._write_state(state)

        self.manager._switch_locked = mock.Mock(side_effect=select)
        self.manager._wait_output_ready = mock.Mock()
        state = self.manager.start(1280, 720, "4")

        self.assertEqual(
            commands[0],
            ["hyprctl", "output", "create", "headless", "HovenCast-TV"],
        )
        self.assertEqual(commands[1][:2], ["hyprctl", "eval"])
        self.assertIn('mode = "1280x720@60"', commands[1][2])
        self.assertIn('position = "1280x40"', commands[1][2])
        self.assertEqual(state["selected_workspace"], "4")
        self.assertEqual(state["placement"], "right")
        self.manager._wait_output_ready.assert_called_once_with("4", 1280, 720)

    def test_start_positions_output_at_the_selected_laptop_edge(self) -> None:
        commands: list[list[str]] = []

        def command(arguments: list[str], check: bool = True) -> str:
            commands.append(arguments)
            return "ok"

        self.manager._command = command  # type: ignore[method-assign]
        self.manager.monitors = mock.Mock(return_value=[PHYSICAL_MONITOR])
        self.manager.focus = mock.Mock(return_value=Focus("eDP-1", "1"))
        self.manager.recover_stale = mock.Mock()

        def select(workspace: str, _focus: Focus) -> None:
            state = self.manager._read_state()
            state["selected_workspace"] = workspace
            self.manager._write_state(state)

        self.manager._switch_locked = mock.Mock(side_effect=select)
        self.manager._wait_output_ready = mock.Mock()
        positions = {
            "left": "-1280x40",
            "above": "0x-720",
            "below": "0x800",
            "right": "1280x40",
        }

        for placement, expected in positions.items():
            with self.subTest(placement=placement):
                commands.clear()
                self.manager.state_path.unlink(missing_ok=True)
                state = self.manager.start(1280, 720, "4", placement)
                self.assertIn(f'position = "{expected}"', commands[1][2])
                self.assertEqual(state["placement"], placement)

    def test_start_configures_anchor_and_output_atomically(self) -> None:
        commands: list[list[str]] = []

        def command(arguments: list[str], check: bool = True) -> str:
            commands.append(arguments)
            return "ok"

        self.manager._command = command  # type: ignore[method-assign]
        self.manager.monitors = mock.Mock(return_value=[PHYSICAL_MONITOR])
        self.manager.focus = mock.Mock(return_value=Focus("eDP-1", "1"))

        def select(workspace: str, _focus: Focus) -> None:
            state = self.manager._read_state()
            state["selected_workspace"] = workspace
            self.manager._write_state(state)

        self.manager._switch_locked = mock.Mock(side_effect=select)
        self.manager._wait_output_ready = mock.Mock()

        self.manager.start(1280, 720, "4", "right")

        monitor_lua = commands[1][2]
        self.assertIn('output = "eDP-1"', monitor_lua)
        self.assertIn('position = "0x0"', monitor_lua)
        self.assertIn('output = "HovenCast-TV"', monitor_lua)
        self.assertIn('position = "1280x40"', monitor_lua)
        self.assertLess(
            monitor_lua.index('output = "eDP-1"'),
            monitor_lua.index('output = "HovenCast-TV"'),
        )

    def test_output_ready_waits_for_selected_workspace_and_mode(self) -> None:
        self.manager.monitors = mock.Mock(
            side_effect=[
                [
                    {
                        "name": "HovenCast-TV",
                        "width": 1280,
                        "height": 720,
                        "activeWorkspace": {"name": "1"},
                    }
                ],
                [
                    {
                        "name": "HovenCast-TV",
                        "width": 1280,
                        "height": 720,
                        "activeWorkspace": {"name": "2"},
                    }
                ],
            ]
        )

        self.manager._wait_output_ready("2", 1280, 720, timeout_seconds=0.2)

        self.assertEqual(self.manager.monitors.call_count, 2)

    @mock.patch("virtual_workspace.time.sleep")
    def test_cleanup_waits_for_output_removal_and_restored_focus(
        self, _sleep: mock.Mock
    ) -> None:
        self.manager.monitors = mock.Mock(
            side_effect=[
                [
                    PHYSICAL_MONITOR,
                    {
                        "name": "HovenCast-TV",
                        "focused": False,
                        "activeWorkspace": {"name": "1"},
                    },
                ],
                [{**PHYSICAL_MONITOR, "activeWorkspace": {"name": "2"}}],
                *[
                    [{**PHYSICAL_MONITOR, "activeWorkspace": {"name": "1"}}]
                    for _ in range(10)
                ],
            ]
        )
        self.manager._restore_focus = mock.Mock()

        self.manager._wait_cleanup_ready(
            Focus("eDP-1", "1"), timeout_seconds=1.0
        )

        self.manager._restore_focus.assert_called_once_with(Focus("eDP-1", "1"))
        self.assertEqual(self.manager.monitors.call_count, 12)

    def test_switch_moves_an_active_laptop_workspace_after_showing_fallback(self) -> None:
        self.write_state()
        self.manager.focus = mock.Mock(return_value=Focus("eDP-1", "1"))
        self.manager._workspace = mock.Mock(
            return_value={"name": "1", "monitor": "eDP-1"}
        )
        self.manager.monitors = mock.Mock(return_value=[PHYSICAL_MONITOR])
        self.manager._next_fallback_workspace = mock.Mock(return_value="2")
        calls: list[str] = []
        self.manager._focus_monitor = lambda value: calls.append(f"monitor:{value}")  # type: ignore[method-assign]
        self.manager._focus_workspace = lambda value: calls.append(f"workspace:{value}")  # type: ignore[method-assign]
        self.manager._move_workspace = lambda workspace, monitor: calls.append(f"move:{workspace}:{monitor}")  # type: ignore[method-assign]
        self.manager._restore_focus = lambda value: calls.append(f"restore:{value.monitor}:{value.workspace}")  # type: ignore[method-assign]

        state = self.manager._switch_locked("1")

        self.assertEqual(
            calls,
            [
                "monitor:eDP-1",
                "workspace:2",
                "move:1:HovenCast-TV",
                "monitor:HovenCast-TV",
                "workspace:1",
                "restore:eDP-1:2",
            ],
        )
        self.assertEqual(state["workspace_origins"], {"1": "eDP-1"})
        self.assertEqual(state["selected_workspace"], "1")
        self.assertEqual(state["local_focus"], {"monitor": "eDP-1", "workspace": "2"})

    def test_switch_restores_previous_workspace_before_moving_next(self) -> None:
        self.write_state(
            selected_workspace="1", workspace_origins={"1": "eDP-1"}
        )
        self.manager.focus = mock.Mock(return_value=Focus("eDP-1", "3"))
        self.manager._workspace = mock.Mock(
            side_effect=[
                {"name": "3", "monitor": "eDP-1"},
                {"name": "1", "monitor": "HovenCast-TV"},
                {"name": "3", "monitor": "eDP-1"},
                {"name": "3", "monitor": "eDP-1"},
            ]
        )
        self.manager.monitors = mock.Mock(
            return_value=[
                {**PHYSICAL_MONITOR, "activeWorkspace": {"name": "2"}},
                {
                    "name": "HovenCast-TV",
                    "focused": False,
                    "activeWorkspace": {"name": "1"},
                },
            ]
        )
        calls: list[str] = []
        self.manager._focus_monitor = lambda value: calls.append(f"monitor:{value}")  # type: ignore[method-assign]
        self.manager._focus_workspace = lambda value: calls.append(f"workspace:{value}")  # type: ignore[method-assign]
        self.manager._move_workspace = lambda workspace, monitor: calls.append(f"move:{workspace}:{monitor}")  # type: ignore[method-assign]
        self.manager._restore_focus = mock.Mock()

        state = self.manager._switch_locked("3")

        self.assertEqual(calls[0], "move:1:eDP-1")
        self.assertIn("move:3:HovenCast-TV", calls)
        self.assertEqual(state["workspace_origins"]["3"], "eDP-1")

    def test_auto_picker_request_is_private_and_identifies_workspace(self) -> None:
        target = self.manager.arm_auto_picker("7")
        payload = json.loads(target.read_text(encoding="utf-8"))
        self.assertEqual(payload["output"], "HovenCast-TV")
        self.assertEqual(payload["metadata"]["kind"], "workspace")
        self.assertEqual(payload["metadata"]["workspace"], "7")
        self.assertEqual(target.stat().st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
