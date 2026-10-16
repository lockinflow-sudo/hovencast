#!/usr/bin/python
"""Read-only Wi-Fi Direct readiness check; never changes network settings."""
import pathlib
import shutil
import sys

import gi
gi.require_version("NM", "1.0")
from gi.repository import NM


def main():
    errors = []
    warnings = []
    for command in ("dnsmasq", "wpa_cli", "iptables", "pkexec"):
        if not shutil.which(command):
            errors.append(f"Missing {command}; run hovencast-setup.")
    client = NM.Client.new(None)
    if not client.get_nm_running():
        errors.append("NetworkManager is not running.")
    if not client.wireless_get_enabled() or not client.wireless_hardware_get_enabled():
        errors.append("Wi-Fi is disabled or hardware-blocked.")
    devices = [device for device in client.get_devices()
               if isinstance(device, NM.DeviceWifiP2P)]
    if not devices:
        errors.append("No Wi-Fi Direct adapter is exposed by NetworkManager; check adapter/driver P2P support.")
    else:
        print(f"NetworkManager exposes {len(devices)} Wi-Fi Direct adapter(s).")
    workaround = pathlib.Path("/run/NetworkManager/conf.d/99-hovencast-p2p-test.conf")
    if workaround.exists():
        warnings.append("A per-boot HovenCast scan-MAC diagnostic workaround is installed. "
                        "This is not a clean/default adapter configuration.")
    persistent = pathlib.Path("/etc/NetworkManager/conf.d/90-hovencast-wifi-compat.conf")
    if persistent.exists():
        warnings.append("The opt-in persistent HovenCast adapter compatibility configuration is installed. "
                        "Scan MAC privacy is reduced for the selected adapter.")
    for message in warnings:
        print("WARNING: " + message)
    for message in errors:
        print("ERROR: " + message, file=sys.stderr)
    if not errors:
        print("P2P prerequisites found. Pairing and video/audio still require a hardware test; "
              "this check does not prove driver compatibility.")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
