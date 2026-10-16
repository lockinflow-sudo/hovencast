#!/usr/bin/python
"""Selected-peer metadata reader; standalone diagnostic or fixed installed entry.

Run with isolated Python. Reads selected peer DeviceAddress/VendorExtension
properties from a pinned supplicant bus owner; never GetAll or credentials.
Also reads age with the fixed wpa_cli p2p_peer command. No local imports,
credential queries, connection or configuration writes.
The installed wrapper requires --caller PID and verifies PKEXEC_UID/PID birth.
"""
import json
import re
import sys
import time
import os
import pathlib

ROOT = "/fi/w1/wpa_supplicant1"
SERVICE = "fi.w1.wpa_supplicant1"
PEER = SERVICE + ".Peer"
PROPERTIES = "org.freedesktop.DBus.Properties"
MAC = r"(?:[0-9a-f]{2}:){5}[0-9a-f]{2}"


class ReadUnavailable(Exception):
    def __init__(self, stage):
        self.stage = stage


def validate_request(interface, selected):
    if not re.fullmatch(r"p2p-dev-[A-Za-z0-9_.-]{1,30}", interface):
        raise ValueError("Invalid P2P interface")
    if not 1 <= len(selected) <= 16 or len(set(selected)) != len(selected):
        raise ValueError("Select one to sixteen distinct peers")
    if any(not re.fullmatch(MAC, mac) for mac in selected):
        raise ValueError("Invalid selected peer")


def bounded_blocks(raw):
    if not isinstance(raw, (list, tuple)) or len(raw) > 16:
        raise ValueError("Excessive vendor blocks")
    blocks = []
    total = 0
    for block in raw:
        if not isinstance(block, (list, tuple, bytes)) or len(block) > 4096:
            raise ValueError("Invalid vendor block")
        if any(type(value) is not int or not 0 <= value <= 255 for value in block):
            raise ValueError("Invalid vendor byte")
        total += len(block)
        if total > 16384:
            raise ValueError("Excessive vendor bytes")
        blocks.append(bytes(block).hex())
    return blocks


def read_selected(call, interface, selected, read_age=lambda interface, mac: None):
    """call(path, interface, method, signature, args) -> unpacked tuple."""
    validate_request(interface, selected)
    # NM's p2p-dev-* management device belongs to this physical Wi-Fi adapter.
    # Supplicant's registered interface/control socket uses the base name.
    path = call(ROOT, SERVICE, "GetInterface", "(s)",
                (interface.removeprefix("p2p-dev-"),))[0]
    if not re.fullmatch(ROOT + r"/Interfaces/[A-Za-z0-9_]{1,64}", path):
        raise ValueError("Invalid supplicant interface path")
    paths = call(path, PROPERTIES, "Get", "(ss)",
                 (SERVICE + ".Interface.P2PDevice", "Peers"))[0]
    if len(paths) > 128:
        raise ValueError("Excessive supplicant peers")
    output = {}
    observed = {}
    for peer_path in paths:
        if not re.fullmatch(re.escape(path) + r"/Peers/[A-Za-z0-9_]{1,64}", peer_path):
            raise ValueError("Peer outside selected interface")
        address = call(peer_path, PROPERTIES, "Get", "(ss)", (PEER, "DeviceAddress"))[0]
        if len(address) != 6:
            raise ValueError("Invalid peer device address")
        mac = bytes(address).hex(":")
        if mac not in selected:
            continue
        if mac in output:
            raise ValueError("Ambiguous selected peer")
        raw = call(peer_path, PROPERTIES, "Get", "(ss)", (PEER, "VendorExtension"))[0]
        again = call(peer_path, PROPERTIES, "Get", "(ss)", (PEER, "DeviceAddress"))[0]
        if bytes(again) != bytes(address):
            raise ValueError("Peer changed while reading")
        output[mac] = bounded_blocks(raw)
        observed[mac] = read_age(interface, mac)
    return {"version": 1, "interface": interface, "peers": output, "observed_at": observed}


def peer_observed_at(interface, mac):
    validate_request(interface, [mac])
    import subprocess
    started = time.monotonic()
    result = subprocess.run(["/usr/bin/wpa_cli", "-i", interface.removeprefix("p2p-dev-"), "p2p_peer", mac],
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=2)
    if result.returncode or len(result.stdout) > 8192:
        return None
    text = result.stdout.decode("ascii", "replace")
    if text.splitlines()[0:1] != [mac]:
        return None
    ages = re.findall(r"^age=([0-9]{1,6})$", text, re.M)
    if len(ages) != 1:
        return None
    # age is truncated to integer seconds. Round conservatively, not younger.
    return started - int(ages[0]) - 1


def main(argv):
    caller = None
    if argv[:1] == ["--caller"]:
        if len(argv) < 4 or not argv[1].isdigit() or os.geteuid() != 0:
            raise ValueError("Invalid installed reader invocation")
        pid = int(argv[1])
        uid = int(os.environ.get("PKEXEC_UID", "0"))
        caller = (pid, caller_identity(pid))
        if uid <= 0 or caller[1][0] != uid:
            raise ValueError("Caller does not belong to the authorizing user")
        argv = argv[2:]
    if len(argv) < 2:
        raise ValueError("Usage: read_p2p_metadata.py P2P_INTERFACE PEER_MAC ...")
    validate_request(argv[0], argv[1:])
    from gi.repository import Gio, GLib
    bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
    owner = bus.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus",
                          "org.freedesktop.DBus", "GetNameOwner",
                          GLib.Variant("(s)", (SERVICE,)), GLib.VariantType.new("(s)"),
                          Gio.DBusCallFlags.NONE, 1000, None).unpack()[0]
    deadline = time.monotonic() + 10

    def call(path, interface, method, signature, args):
        if caller is not None and caller_identity(caller[0]) != caller[1]:
            raise ValueError("Caller exited or changed")
        remaining = int((deadline - time.monotonic()) * 1000)
        if remaining <= 0:
            raise TimeoutError("Metadata read timed out")
        try:
            result = bus.call_sync(owner, path, interface, method, GLib.Variant(signature, args),
                                   None, Gio.DBusCallFlags.NONE, min(remaining, 1000), None)
        except Exception:
            stage = "interface-lookup" if method == "GetInterface" else {
                "Peers": "peer-list", "DeviceAddress": "peer-address",
                "VendorExtension": "vendor-extension"}.get(args[-1], "property-read")
            raise ReadUnavailable(stage) from None
        return result.unpack()

    def age(interface, mac):
        if time.monotonic() >= deadline:
            raise TimeoutError("Metadata read timed out")
        return peer_observed_at(interface, mac)

    reply = read_selected(call, argv[0], argv[1:], age)
    if caller is not None and caller_identity(caller[0]) != caller[1]:
        raise ValueError("Caller exited or changed")
    print(json.dumps(reply))


def caller_identity(pid):
    if not 1 <= pid <= 4194304:
        raise ValueError("Invalid caller PID")
    path = pathlib.Path(f"/proc/{pid}")
    stat = (path / "stat").read_text().rsplit(")", 1)[1].split()
    return path.stat().st_uid, stat[19]


if __name__ == "__main__":
    try:
        main(sys.argv[1:])
    except Exception as error:
        # Do not expose bus properties or raw metadata in error text.
        print(json.dumps({"error": "metadata-unavailable",
                          "stage": error.stage if isinstance(error, ReadUnavailable) else "reader",
                          "system_changes": False}), file=sys.stderr)
        sys.exit(1)
