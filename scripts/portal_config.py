#!/usr/bin/python

from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import stat
import tempfile
from typing import Any


SECTION_RE = re.compile(r"^\s*([A-Za-z0-9_.-]+)\s*\{\s*$")
KEY_RE = re.compile(
    r"^(?P<indent>\s*)(?P<key>allow_token_by_default|custom_picker_binary)"
    r"\s*=\s*(?P<value>.*?)\s*$"
)


def config_path() -> pathlib.Path:
    config_home = pathlib.Path(os.environ.get("XDG_CONFIG_HOME", pathlib.Path.home() / ".config"))
    return config_home / "hypr" / "xdph.conf"


def state_dir() -> pathlib.Path:
    state_home = pathlib.Path(os.environ.get("XDG_STATE_HOME", pathlib.Path.home() / ".local/state"))
    return state_home / "omacast"


def find_section(lines: list[str]) -> tuple[int, int] | None:
    for start, line in enumerate(lines):
        match = SECTION_RE.match(line)
        if not match or match.group(1) != "screencopy":
            continue
        depth = 1
        for end in range(start + 1, len(lines)):
            depth += lines[end].count("{") - lines[end].count("}")
            if depth == 0:
                return start, end
        raise RuntimeError("unterminated screencopy section in xdph.conf")
    return None


def read_values(lines: list[str]) -> tuple[bool, dict[str, dict[str, Any]]]:
    section = find_section(lines)
    values = {
        key: {"present": False, "value": ""}
        for key in ("allow_token_by_default", "custom_picker_binary")
    }
    if section is None:
        return False, values
    start, end = section
    for line in lines[start + 1 : end]:
        match = KEY_RE.match(line)
        if match and not values[match.group("key")]["present"]:
            values[match.group("key")] = {
                "present": True,
                "value": match.group("value"),
            }
    return True, values


def set_value(lines: list[str], key: str, value: str | None) -> list[str]:
    section = find_section(lines)
    if section is None:
        if value is None:
            return lines
        if lines and lines[-1].strip():
            lines.append("")
        lines.extend(["screencopy {", f"    {key} = {value}", "}"])
        return lines

    start, end = section
    matches = [
        index
        for index in range(start + 1, end)
        if (match := KEY_RE.match(lines[index])) and match.group("key") == key
    ]
    if matches:
        first = matches[0]
        indent = KEY_RE.match(lines[first]).group("indent")  # type: ignore[union-attr]
        if value is None:
            for index in reversed(matches):
                del lines[index]
        else:
            lines[first] = f"{indent}{key} = {value}"
            for index in reversed(matches[1:]):
                del lines[index]
    elif value is not None:
        lines.insert(end, f"    {key} = {value}")
    return lines


def atomic_write(path: pathlib.Path, text: str, mode: int = 0o644) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    handle, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = pathlib.Path(temporary_name)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(text)
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def install(picker: pathlib.Path) -> None:
    target = config_path()
    state_root = state_dir()
    state_file = state_root / "portal.json"
    backup_file = state_root / "xdph.conf.before-omacast"
    picker = picker.resolve(strict=True)

    existed = target.exists()
    original_text = target.read_text(encoding="utf-8") if existed else ""
    mode = stat.S_IMODE(target.stat().st_mode) if existed else 0o644
    lines = original_text.splitlines()

    if state_file.exists():
        state = json.loads(state_file.read_text(encoding="utf-8"))
        if pathlib.Path(state["config"]) != target:
            raise RuntimeError(f"saved HovenCast state belongs to {state['config']}, not {target}")
        _, current = read_values(lines)
        old_picker = str(state["installed_picker"])
        current_picker = current["custom_picker_binary"]
        if current_picker["present"] and current_picker["value"] not in {old_picker, str(picker)}:
            raise RuntimeError(
                "custom_picker_binary changed after HovenCast setup; remove or reconcile it before reinstalling"
            )
        state["installed_picker"] = str(picker)
    else:
        section_present, previous = read_values(lines)
        state = {
            "version": 1,
            "config": str(target),
            "config_existed": existed,
            "section_present": section_present,
            "previous": {"custom_picker_binary": previous["custom_picker_binary"]},
            "installed_picker": str(picker),
        }
        state_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        atomic_write(backup_file, original_text, mode)

    lines = set_value(lines, "custom_picker_binary", str(picker))
    atomic_write(target, "\n".join(lines).rstrip() + "\n", mode)
    atomic_write(state_file, json.dumps(state, indent=2, sort_keys=True) + "\n", 0o600)
    print(f"Configured {target} to use {picker}")


def remove() -> None:
    state_root = state_dir()
    state_file = state_root / "portal.json"
    backup_file = state_root / "xdph.conf.before-omacast"
    if not state_file.exists():
        print("No saved HovenCast portal state; nothing to restore")
        return

    state = json.loads(state_file.read_text(encoding="utf-8"))
    target = pathlib.Path(state["config"])
    text = target.read_text(encoding="utf-8") if target.exists() else ""
    mode = stat.S_IMODE(target.stat().st_mode) if target.exists() else 0o644
    lines = text.splitlines()
    _, current = read_values(lines)
    expected = {"custom_picker_binary": str(state["installed_picker"])}
    # Compatibility with the initial 0.1.0 setup script, which also managed
    # allow_token_by_default. New installs leave that user preference alone.
    if "allow_token_by_default" in state.get("previous", {}):
        expected["allow_token_by_default"] = "true"

    for key, installed_value in expected.items():
        value = current[key]
        if not value["present"] or value["value"] != installed_value:
            print(f"Leaving user-modified {key} unchanged")
            continue
        previous = state["previous"][key]
        lines = set_value(lines, key, previous["value"] if previous["present"] else None)

    section = find_section(lines)
    if not state["section_present"] and section is not None:
        start, end = section
        meaningful = [line for line in lines[start + 1 : end] if line.strip() and not line.lstrip().startswith("#")]
        if not meaningful:
            del lines[start : end + 1]
            while lines and not lines[-1].strip():
                lines.pop()

    if not state["config_existed"] and not any(line.strip() for line in lines):
        target.unlink(missing_ok=True)
    else:
        atomic_write(target, "\n".join(lines).rstrip() + "\n", mode)

    state_file.unlink(missing_ok=True)
    backup_file.unlink(missing_ok=True)
    try:
        state_root.rmdir()
    except OSError:
        pass
    print(f"Restored HovenCast-managed settings in {target}")


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    install_parser = subparsers.add_parser("install")
    install_parser.add_argument("--picker", required=True, type=pathlib.Path)
    subparsers.add_parser("remove")
    args = parser.parse_args()
    if args.command == "install":
        install(args.picker)
    else:
        remove()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
