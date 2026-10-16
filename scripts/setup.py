#!/usr/bin/python
"""First-use setup. Checks are read-only; changes require an interactive review."""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys

import portal_config

ROOT = Path(__file__).resolve().parents[1]
PACKAGES = (
    "avahi", "ffmpeg", "gcc", "grim", "gst-plugin-pipewire", "gst-plugins-bad",
    "gst-plugins-base-libs", "gst-plugins-good", "gst-plugins-ugly", "gst-rtsp-server",
    "gstreamer", "jq", "libportal", "libpulse", "pkgconf", "python", "python-gobject",
    "slurp", "xdg-desktop-portal-hyprland", "xdg-desktop-portal",
    "dnsmasq", "libnm", "networkmanager", "wpa_supplicant", "polkit",
    "iptables", "pipewire", "pipewire-pulse", "opus", "libvpx",
)
ELEMENTS = (
    "pipewiresrc", "videoconvert", "videoscale", "videorate", "x264enc", "h264parse",
    "pulsesrc", "audioconvert", "audioresample", "fdkaacenc", "aacparse", "mpegtsmux",
)
SERVICES = ("xdg-desktop-portal-hyprland.service", "xdg-desktop-portal.service")


def run(command: list[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(command, check=True, **kwargs)


def missing_packages() -> list[str]:
    result = run(["pacman", "-Qq"], capture_output=True, text=True)
    installed = set(result.stdout.splitlines())
    return [name for name in PACKAGES if name not in installed]


def readiness(*, require_complete: bool = True) -> dict:
    issues = []
    missing = missing_packages()
    if missing:
        issues.append("Install missing packages: " + ", ".join(missing))
    helper = ROOT / "build/wfd-sender"
    source_hash = hashlib.sha256((ROOT / "src/wfd_sender.c").read_bytes()).hexdigest()
    hash_file = ROOT / "build/wfd-sender.source.sha256"
    built_hash = hash_file.read_text().strip() if hash_file.exists() else ""
    if not os.access(helper, os.X_OK) or built_hash != source_hash:
        issues.append("Build the wireless display helper")
    target = portal_config.config_path()
    text = target.read_text() if target.exists() else ""
    _, values = portal_config.read_values(text.splitlines())
    picker = str((ROOT / "hovencast-picker").resolve())
    state_file = portal_config.state_dir() / "portal.json"
    saved = json.loads(state_file.read_text()) if state_file.exists() else {}
    if (values["custom_picker_binary"]["value"] != picker
            or saved.get("installed_picker") != picker
            or saved.get("config") != str(target)):
        issues.append("Set up screen sharing and save recovery information")
    if require_complete and not saved.get("setup_complete", True):
        issues.append("Finish guided setup before connecting a display")
    data = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share")
    for manifest, helper_path in (("native/chromecast/provenance.json", "hovencast/cast_sender"),
                                  ("native/provenance.json", "hovencast/native/bin/doubletake")):
        provenance = ROOT / manifest
        if provenance.is_file():
            expected = json.loads(provenance.read_text())["binary_sha256"]
            installed = data / helper_path
            if not installed.is_file() or hashlib.sha256(installed.read_bytes()).hexdigest() != expected:
                issues.append("Install or update bundled receiver helper: " + installed.name)
    return {"ready": not issues, "issues": issues, "missing_packages": missing}


def restart_portals() -> None:
    run(["systemctl", "--user", "restart", *SERVICES])
    run(["systemctl", "--user", "is-active", "--quiet", *SERVICES])


def snapshot(paths: list[Path]) -> dict[Path, tuple[bytes, int] | None]:
    return {p: (p.read_bytes(), stat.S_IMODE(p.stat().st_mode)) if p.exists() else None
            for p in paths}


def restore(before: dict[Path, tuple[bytes, int] | None]) -> None:
    for path, saved in before.items():
        if saved is None:
            path.unlink(missing_ok=True)
        else:
            portal_config.atomic_write(path, saved[0].decode("utf-8"), saved[1])


def configure() -> None:
    """Apply portal changes last, restoring the immediate previous setup on failure."""
    state_root = portal_config.state_dir()
    target = portal_config.config_path()
    before = snapshot([target, state_root / "portal.json",
                       state_root / "xdph.conf.before-hovencast"])
    # A self-contained recovery tool survives deletion of the plugin directory.
    recovery = state_root / "restore-portal.py"
    portal_config.atomic_write(recovery, (ROOT / "scripts/portal_config.py").read_text(), 0o700)
    installed_text = None
    try:
        portal_config.install(ROOT / "hovencast-picker")
        installed_text = target.read_text()
        restart_portals()
        print("\n[4/4] Checking readiness…", flush=True)
        status = readiness(require_complete=False)
        if not status["ready"]:
            raise RuntimeError("Setup needs attention: " + "; ".join(status["issues"]))
        state_file = state_root / "portal.json"
        state = json.loads(state_file.read_text())
        state["setup_complete"] = True
        portal_config.atomic_write(state_file, json.dumps(state, indent=2, sort_keys=True) + "\n", 0o600)
    except BaseException:
        # Respect a concurrent user edit instead of replacing their new picker.
        text = target.read_text() if target.exists() else ""
        _, values = portal_config.read_values(text.splitlines())
        old = before[target][0].decode("utf-8") if before[target] else ""
        owned_picker = values["custom_picker_binary"]["value"] == str((ROOT / "hovencast-picker").resolve())
        if text == old or text == installed_text or owned_picker:
            if text == old or text == installed_text:
                restore(before)
            else:
                # The user edited other settings during setup. Restore only our key.
                section_present, previous = portal_config.read_values(old.splitlines())
                value = previous["custom_picker_binary"]
                lines = portal_config.set_value(text.splitlines(), "custom_picker_binary",
                                                value["value"] if value["present"] else None)
                section = portal_config.find_section(lines)
                if not section_present and section is not None:
                    start, end = section
                    if not any(line.strip() for line in lines[start + 1:end]):
                        del lines[start:end + 1]
                mode = stat.S_IMODE(target.stat().st_mode)
                portal_config.atomic_write(target, "\n".join(lines).rstrip() + "\n", mode)
                restore({path: saved for path, saved in before.items() if path != target})
            try:
                restart_portals()
                print("Restored the previous screen-sharing setup.", flush=True)
            except Exception:
                print("Configuration restored; portal restart failed. Restart your session.",
                      file=sys.stderr, flush=True)
        else:
            print("The picker changed during setup; preserved your edit and recovery files.",
                  file=sys.stderr, flush=True)
        raise


def install(compat_adapter: str | None = None) -> int:
    status = readiness()
    if status["ready"]:
        print("HovenCast is ready. Select a workspace and a supported TV in the bar.")
        return 0
    print("HovenCast — your TV as a second Omarchy monitor\n")
    print("Supports compatible Roku, Fire TV, Google Cast and paired Apple TV receivers.")
    print("Setup will:\n  1. Install missing Arch packages\n  2. Build the display helper locally")
    print("  Install scoped local-session Wi-Fi Direct and identity helpers (admin approval)")
    print("  Install the bundled Google Cast and AirPlay native helpers")
    if compat_adapter:
        print(f"Opt-in: disable scan MAC randomization for {compat_adapter}, including ordinary Wi-Fi scans.")
    print("  3. Back up and change the screen-sharing picker for all applications")
    print("  4. Restart screen-sharing services (finish any active screen shares first)")
    print("\nMissing packages: " + (", ".join(status["missing_packages"]) or "none"))
    print("If portal setup fails, its previous configuration will be restored.")
    print("Installed packages and the local build are retained on failure or removal.\n")
    if not sys.stdin.isatty():
        print("Open a terminal and run hovencast-setup to review and continue.", file=sys.stderr)
        return 2
    if input("Continue with setup? [y/N] ").strip().lower() not in {"y", "yes"}:
        print("Setup cancelled. No packages or portal settings were changed.")
        return 0
    if status["missing_packages"]:
        print("\n[1/4] Installing missing packages…", flush=True)
        run(["omarchy", "pkg", "add", *status["missing_packages"]])
    print("\n[2/4] Checking media support and building the helper…", flush=True)
    for element in ELEMENTS:
        run(["gst-inspect-1.0", element], stdout=subprocess.DEVNULL)
    run(["python", "-c", 'import gi; gi.require_version("Gst", "1.0"); '
         'gi.require_version("Xdp", "1.0"); from gi.repository import Gst, Xdp'])
    run([str(ROOT / "build-backend")])
    helper_args = ["--wifi-compat-adapter", compat_adapter] if compat_adapter else []
    run(["bash", str(ROOT / "scripts/install-p2p-helper"), *helper_args])
    for installer in ("install_chromecast_native.py", "install_airplay_native.py"):
        path = ROOT / "scripts" / installer
        if path.is_file():
            run([sys.executable, str(path)])
    print("\n[3/4] Saving recovery information and setting up screen sharing…", flush=True)
    configure()
    print("\nReady. Open HovenCast, choose a workspace, then select your TV.")
    print("Your laptop stays on its own workspace. Approve the connection on the TV.")
    return 0


def uninstall() -> int:
    # Match the actual backend argv, not pgrep's regex or this script's own command.
    for cmdline in Path("/proc").glob("[0-9]*/cmdline"):
        try:
            args = cmdline.read_bytes().split(b"\0")
        except OSError:
            continue
        backend = os.fsencode(ROOT / "src/omarchy_cast.py")
        if backend in args and any(command in args for command in
                                   (b"session-run", b"session-start", b"cast-live", b"workspace-guard")):
            raise RuntimeError("Stop casting and disable hoven.cast before removing setup.")
    cleanup = ROOT / "scripts/device_cleanup.sh"
    if cleanup.is_file():
        run(["bash", str(cleanup)])
    portal_config.remove()
    restart_portals()
    (portal_config.state_dir() / "restore-portal.py").unlink(missing_ok=True)
    print("Screen-sharing settings restored. You can now run: omarchy plugin remove hoven.cast")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument("--check", action="store_true", help="read-only readiness check as JSON")
    actions.add_argument("--uninstall", action="store_true", help="restore screen-sharing settings")
    parser.add_argument("--wifi-compat-adapter", metavar="INTERFACE", help="opt-in adapter-specific scan MAC compatibility")
    args = parser.parse_args()
    if args.wifi_compat_adapter:
        import re
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,14}", args.wifi_compat_adapter):
            parser.error("Invalid Wi-Fi adapter name")
    if args.check:
        try:
            status = readiness()
        except Exception as error:
            status = {"ready": False, "issues": [str(error)], "missing_packages": []}
        print(json.dumps(status))
        return 0
    state_root = portal_config.state_dir()
    state_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (state_root / ".setup.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("HovenCast setup or removal is already running.", file=sys.stderr)
            return 1
        try:
            return uninstall() if args.uninstall else install(args.wifi_compat_adapter)
        except (Exception, KeyboardInterrupt) as error:
            print(f"HovenCast setup did not finish: {error}", file=sys.stderr)
            return 1


if __name__ == "__main__":
    raise SystemExit(main())
