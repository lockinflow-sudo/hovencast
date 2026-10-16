#!/usr/bin/python
"""Explicit, persistent adapter-scoped scan compatibility setting."""
import argparse
import os
import pathlib
import re
import tempfile

TARGET = pathlib.Path("/etc/NetworkManager/conf.d/90-hovencast-wifi-compat.conf")
HEADER = "# HovenCast opt-in Wi-Fi compatibility v1\n"


def render(interface):
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,14}", interface):
        raise ValueError("Invalid Wi-Fi interface name")
    return (HEADER + "# Disables scan MAC randomization for this adapter, including non-cast scans.\n"
            "[device-hovencast-wifi-compat]\n"
            f"match-device=interface-name:{interface}\n"
            "wifi.scan-rand-mac-address=no\n")


def owned(contents):
    match = re.search(r"^match-device=interface-name:([A-Za-z0-9_.-]+)$", contents, re.M)
    return bool(match and contents == render(match[1]))


def existing(target):
    if target.is_symlink():
        raise RuntimeError("Refusing a symlinked compatibility configuration")
    if not target.exists():
        return None
    contents = target.read_text()
    if not owned(contents):
        raise RuntimeError("Compatibility configuration was modified or is not HovenCast-owned; leaving it unchanged")
    return contents


def install(interface, target=TARGET):
    text = render(interface)
    previous = existing(target)
    if previous is not None and previous != text:
        raise RuntimeError("A different adapter is configured; remove the old compatibility setting first")
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".hovencast-wifi-", dir=target.parent)
    temporary = pathlib.Path(name)
    try:
        with os.fdopen(fd, "w") as stream:
            stream.write(text)
        temporary.chmod(0o644)
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def remove(target=TARGET):
    if existing(target) is not None:
        target.unlink()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    add = commands.add_parser("install")
    add.add_argument("--interface", required=True)
    commands.add_parser("remove")
    args = parser.parse_args()
    if os.geteuid() != 0:
        parser.error("Administrative authentication is required")
    if args.command == "install":
        render(args.interface)
        if not (pathlib.Path("/sys/class/net") / args.interface / "phy80211").exists():
            parser.error("The selected interface is not an available Wi-Fi adapter")
        install(args.interface)
        print(f"Persistent Wi-Fi compatibility installed for {args.interface} only.")
    else:
        remove()
        print("HovenCast Wi-Fi compatibility setting removed (if installed).")
    print("Restart NetworkManager when no cast is running to apply this change.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
