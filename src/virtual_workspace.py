from __future__ import annotations

import fcntl
import json
import os
import pathlib
import subprocess
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Callable, Iterator


VIRTUAL_OUTPUT = "HovenCast-TV"
STATE_VERSION = 1


def _runtime_root(required: bool = True) -> pathlib.Path | None:
    value = os.environ.get("XDG_RUNTIME_DIR", "").strip()
    if not value:
        if required:
            raise RuntimeError("HovenCast requires XDG_RUNTIME_DIR")
        return None
    root = pathlib.Path(value) / "omacast"
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    root.chmod(0o700)
    return root


def _process_is_running(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def _safe_workspace(value: Any) -> str:
    workspace = str(value or "").strip()
    if not workspace or len(workspace) > 128:
        raise ValueError("workspace name must contain 1 to 128 characters")
    if any(ord(character) < 32 for character in workspace):
        raise ValueError("workspace name contains a control character")
    if workspace.startswith("special:"):
        raise ValueError("special workspaces cannot be cast")
    return workspace


def _lua_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


@dataclass(frozen=True)
class Focus:
    monitor: str
    workspace: str


class VirtualWorkspace:
    """Own a fixed headless output while moving workspaces under its capture."""

    def __init__(
        self,
        run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
        output: str = VIRTUAL_OUTPUT,
    ) -> None:
        self.run = run
        self.output = output
        root = _runtime_root()
        assert root is not None
        self.state_path = root / "virtual-workspace.json"
        self.lock_path = root / "virtual-workspace.lock"

    @contextmanager
    def locked(self) -> Iterator[None]:
        with self.lock_path.open("a+", encoding="utf-8") as lock:
            self.lock_path.chmod(0o600)
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            yield

    def _command(self, arguments: list[str], check: bool = True) -> str:
        result = self.run(
            arguments,
            check=check,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        return result.stdout.strip()

    def _json(self, subject: str) -> list[dict[str, Any]]:
        raw = self._command(["hyprctl", "-j", *subject.split()])
        value = json.loads(raw)
        if not isinstance(value, list):
            raise RuntimeError(f"hyprctl {subject} returned an unexpected value")
        return value

    def monitors(self) -> list[dict[str, Any]]:
        return self._json("monitors all")

    def workspaces(self) -> list[dict[str, Any]]:
        return self._json("workspaces")

    def focus(self) -> Focus:
        monitors = self.monitors()
        monitor = next((item for item in monitors if item.get("focused")), None)
        if monitor is None:
            monitor = next(
                (item for item in monitors if item.get("name") != self.output), None
            )
        if monitor is None:
            raise RuntimeError("Hyprland has no usable display")
        active = monitor.get("activeWorkspace") or {}
        return Focus(str(monitor.get("name", "")), str(active.get("name", "")))

    def _dispatch(self, expression: str) -> None:
        self._command(["hyprctl", "dispatch", expression])

    def _focus_monitor(self, monitor: str) -> None:
        self._dispatch(f"hl.dsp.focus({{ monitor = {_lua_string(monitor)} }})")

    def _focus_workspace(self, workspace: str) -> None:
        self._dispatch(f"hl.dsp.focus({{ workspace = {_lua_string(workspace)} }})")

    def _move_workspace(self, workspace: str, monitor: str) -> None:
        self._dispatch(
            "hl.dsp.workspace.move({ workspace = "
            + _lua_string(workspace)
            + ", monitor = "
            + _lua_string(monitor)
            + " })"
        )

    def _restore_focus(self, focus: Focus) -> None:
        monitors = {str(item.get("name", "")) for item in self.monitors()}
        if focus.monitor not in monitors or focus.monitor == self.output:
            focus = self.local_focus()
        self._focus_monitor(focus.monitor)
        if focus.workspace:
            self._focus_workspace(focus.workspace)

    def _read_state(self) -> dict[str, Any]:
        try:
            value = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise RuntimeError("HovenCast virtual workspace state is unavailable") from error
        if not isinstance(value, dict) or value.get("version") != STATE_VERSION:
            raise RuntimeError("HovenCast virtual workspace state is invalid")
        return value

    def _write_state(self, state: dict[str, Any]) -> None:
        temporary = self.state_path.with_name(f".{self.state_path.name}.{os.getpid()}")
        temporary.write_text(json.dumps(state, sort_keys=True) + "\n", encoding="utf-8")
        temporary.chmod(0o600)
        os.replace(temporary, self.state_path)

    def local_focus(self) -> Focus:
        state = self._read_state()
        saved = state.get("local_focus") or {}
        monitor = str(saved.get("monitor", ""))
        workspace = str(saved.get("workspace", ""))
        if monitor and monitor != self.output:
            return Focus(monitor, workspace)
        physical = next(
            (item for item in self.monitors() if item.get("name") != self.output),
            None,
        )
        if physical is None:
            raise RuntimeError("Hyprland has no local display")
        active = physical.get("activeWorkspace") or {}
        return Focus(str(physical.get("name", "")), str(active.get("name", "")))

    def _next_fallback_workspace(self, monitor: str, excluded: set[str]) -> str:
        candidates = [
            str(item.get("name", ""))
            for item in self.workspaces()
            if item.get("monitor") == monitor
            and str(item.get("name", "")) not in excluded
            and not str(item.get("name", "")).startswith("special:")
        ]
        if candidates:
            return sorted(
                candidates,
                key=lambda item: (
                    not item.isdigit(),
                    int(item) if item.isdigit() else item,
                ),
            )[0]
        numeric = [
            int(str(item.get("name")))
            for item in self.workspaces()
            if str(item.get("name", "")).isdigit()
        ]
        candidate = max(numeric, default=0) + 1
        while str(candidate) in excluded:
            candidate += 1
        return str(candidate)

    def start(self, width: int, height: int, workspace: str) -> dict[str, Any]:
        workspace = _safe_workspace(workspace)
        with self.locked():
            self.recover_stale(lock_held=True)
            monitors = self.monitors()
            if any(item.get("name") == self.output for item in monitors):
                raise RuntimeError(
                    f"Hyprland output {self.output} already exists and is not owned by this session"
                )
            local = self.focus()
            physical = [item for item in monitors if item.get("name") != self.output]
            if not physical:
                raise RuntimeError("HovenCast needs a physical display for focus recovery")
            right_edge = max(
                int(item.get("x", 0))
                + round(int(item.get("width", 0)) / (float(item.get("scale", 1)) or 1))
                for item in physical
            )
            self._command(["hyprctl", "output", "create", "headless", self.output])
            try:
                monitor_lua = (
                    "hl.monitor({ output = "
                    + _lua_string(self.output)
                    + f", mode = {_lua_string(f'{width}x{height}@60')}, "
                    + f"position = {_lua_string(f'{right_edge}x0')}, scale = 1 }})"
                )
                self._command(["hyprctl", "eval", monitor_lua])
                state: dict[str, Any] = {
                    "version": STATE_VERSION,
                    "pid": os.getpid(),
                    "output": self.output,
                    "width": width,
                    "height": height,
                    "local_focus": {"monitor": local.monitor, "workspace": local.workspace},
                    "restore_focus": {"monitor": local.monitor, "workspace": local.workspace},
                    "selected_workspace": "",
                    "workspace_origins": {},
                    "created_at": time.time(),
                }
                self._write_state(state)
                self._switch_locked(workspace, local)
                return self._read_state()
            except Exception:
                self._command(["hyprctl", "output", "remove", self.output], check=False)
                self.state_path.unlink(missing_ok=True)
                raise

    def _workspace(self, name: str) -> dict[str, Any] | None:
        return next((item for item in self.workspaces() if str(item.get("name")) == name), None)

    def _switch_locked(
        self, workspace: str, entry_focus: Focus | None = None
    ) -> dict[str, Any]:
        workspace = _safe_workspace(workspace)
        state = self._read_state()
        entry_focus = entry_focus or self.focus()
        previous = str(state.get("selected_workspace", ""))
        origins = state.setdefault("workspace_origins", {})

        current = self._workspace(workspace)
        if workspace not in origins:
            origin_monitor = str(current.get("monitor", "")) if current else entry_focus.monitor
            if origin_monitor == self.output:
                origin_monitor = self.local_focus().monitor
            origins[workspace] = origin_monitor
            self._write_state(state)

        if previous and previous != workspace:
            previous_info = self._workspace(previous)
            if previous_info and previous_info.get("monitor") == self.output:
                destination = str(origins.get(previous) or self.local_focus().monitor)
                self._move_workspace(previous, destination)

        current = self._workspace(workspace)
        source_monitor = str(
            (current or {}).get("monitor", "") or origins.get(workspace, "")
        )
        if source_monitor and source_monitor != self.output:
            monitor = next(
                (item for item in self.monitors() if item.get("name") == source_monitor),
                None,
            )
            active_name = str((monitor or {}).get("activeWorkspace", {}).get("name", ""))
            saved_local = state.get("local_focus") or {}
            local_needs_fallback = (
                str(saved_local.get("monitor", "")) == source_monitor
                and str(saved_local.get("workspace", "")) == workspace
            )
            if active_name == workspace or local_needs_fallback:
                fallback = self._next_fallback_workspace(source_monitor, {workspace})
                self._focus_monitor(source_monitor)
                self._focus_workspace(fallback)
                if local_needs_fallback:
                    state["local_focus"] = {
                        "monitor": source_monitor,
                        "workspace": fallback,
                    }
                    if entry_focus == Focus(source_monitor, workspace):
                        entry_focus = Focus(source_monitor, fallback)
                    self._write_state(state)
            current = self._workspace(workspace)
            if current and current.get("monitor") != self.output:
                self._move_workspace(workspace, self.output)
            elif not current:
                self._focus_monitor(self.output)
                self._focus_workspace(workspace)
        elif not current:
            self._focus_monitor(self.output)
            self._focus_workspace(workspace)

        self._focus_monitor(self.output)
        self._focus_workspace(workspace)
        state["selected_workspace"] = workspace
        self._write_state(state)
        if entry_focus.monitor != self.output:
            self._restore_focus(entry_focus)
        return state

    def switch(self, workspace: str) -> dict[str, Any]:
        with self.locked():
            state = self._read_state()
            if not _process_is_running(int(state.get("pid", 0))):
                self.recover_stale(lock_held=True)
                raise RuntimeError("The HovenCast session is no longer running")
            return self._switch_locked(workspace)

    def focus_tv(self) -> dict[str, Any]:
        with self.locked():
            state = self._read_state()
            workspace = _safe_workspace(state.get("selected_workspace"))
            self._focus_monitor(self.output)
            self._focus_workspace(workspace)
            return state

    def focus_local_display(self) -> dict[str, Any]:
        with self.locked():
            state = self._read_state()
            self._restore_focus(self.local_focus())
            return state

    def stop(self) -> None:
        with self.locked():
            self._stop_locked()

    def _stop_locked(self) -> None:
        if not self.state_path.is_file():
            return
        state = self._read_state()
        local = self.local_focus()
        restore_value = state.get("restore_focus") or {}
        restore = Focus(
            str(restore_value.get("monitor", "")) or local.monitor,
            str(restore_value.get("workspace", "")) or local.workspace,
        )
        selected = str(state.get("selected_workspace", ""))
        origins = state.get("workspace_origins") or {}
        monitors = {str(item.get("name", "")) for item in self.monitors()}
        errors: list[str] = []
        if selected:
            try:
                info = self._workspace(selected)
                destination = str(origins.get(selected) or local.monitor)
                if destination not in monitors or destination == self.output:
                    destination = local.monitor
                if info and info.get("monitor") == self.output:
                    self._move_workspace(selected, destination)
            except (OSError, ValueError, json.JSONDecodeError, subprocess.SubprocessError) as error:
                errors.append(f"could not restore workspace {selected}: {error}")
        try:
            self._restore_focus(restore)
        except (OSError, ValueError, json.JSONDecodeError, subprocess.SubprocessError) as error:
            errors.append(f"could not restore laptop focus: {error}")
        if self.output in monitors:
            try:
                self._command(["hyprctl", "output", "remove", self.output])
            except (OSError, subprocess.SubprocessError) as error:
                errors.append(f"could not remove {self.output}: {error}")
        remaining = {str(item.get("name", "")) for item in self.monitors()}
        if self.output not in remaining and not errors:
            self.state_path.unlink(missing_ok=True)
            self.disarm_auto_picker()
            return
        if self.output not in remaining:
            errors.append("the virtual output was removed; recovery state was retained")
        raise RuntimeError("; ".join(errors) or "virtual workspace cleanup did not finish")

    def recover_stale(self, lock_held: bool = False) -> bool:
        if not lock_held:
            with self.locked():
                return self.recover_stale(lock_held=True)
        if not self.state_path.is_file():
            return False
        try:
            state = self._read_state()
            if _process_is_running(int(state.get("pid", 0))):
                return False
        except RuntimeError:
            pass
        self._stop_locked()
        return True

    def arm_auto_picker(self, workspace: str) -> pathlib.Path:
        workspace = _safe_workspace(workspace)
        runtime = os.environ.get("XDG_RUNTIME_DIR", "").strip()
        if not runtime:
            raise RuntimeError("HovenCast requires XDG_RUNTIME_DIR")
        picker_root = pathlib.Path(runtime) / "omacast-picker"
        picker_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        picker_root.chmod(0o700)
        target = picker_root / "auto-selection.json"
        temporary = target.with_name(f".{target.name}.{os.getpid()}")
        payload = {
            "pid": os.getpid(),
            "output": self.output,
            "metadata": {
                "kind": "workspace",
                "workspace": workspace,
                "output": self.output,
            },
        }
        temporary.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")
        temporary.chmod(0o600)
        os.replace(temporary, target)
        return target

    def disarm_auto_picker(self) -> None:
        runtime = os.environ.get("XDG_RUNTIME_DIR", "").strip()
        if runtime:
            (pathlib.Path(runtime) / "omacast-picker" / "auto-selection.json").unlink(
                missing_ok=True
            )

    def status(self) -> dict[str, Any]:
        with self.locked():
            state = self._read_state()
            state["running"] = _process_is_running(int(state.get("pid", 0)))
            return state


def guard(parent_pid: int, poll_seconds: float = 0.5) -> int:
    manager = VirtualWorkspace()
    while True:
        if not manager.state_path.is_file():
            return 0
        if not _process_is_running(parent_pid):
            manager.recover_stale()
            return 0
        time.sleep(poll_seconds)
