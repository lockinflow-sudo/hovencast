"""Explicit live association check; never called by taskbar discovery.

Scan uses the existing unprivileged discovery. --authorize-read permits a
one-shot pkexec diagnostic, not an installed helper/policy or future grant.
Only friendly names and decisions are printed; IDs/IPs/vendor bytes stay in RAM.
"""
import argparse
import json
import math
from dataclasses import replace
import pathlib
import subprocess
import time
import uuid

from cast_devices import device_from_receiver
from device_reconciliation import EndpointObservation, propose_mice_association
from mice_vendor import parse_mice_vendor_extensions
from omarchy_cast_protocol import discover_mice_receivers, enrich_receiver_details
from p2p_backend import P2PLink


def decode_reply(raw, interface, selected):
    if len(raw) > 540000:
        raise ValueError("Excessive metadata response")
    data = json.loads(raw)
    if (not isinstance(data, dict) or data.get("version") != 1
            or data.get("interface") != interface or not isinstance(data.get("peers"), dict)
            or not set(data["peers"]).issubset(selected)):
        raise ValueError("Unexpected metadata response")
    output = {}
    for mac, blocks in data["peers"].items():
        if not isinstance(blocks, list) or len(blocks) > 16:
            raise ValueError("Invalid vendor blocks")
        total = 0
        for block in blocks:
            if not isinstance(block, str) or len(block) > 8192:
                raise ValueError("Invalid vendor encoding")
            total += len(block)
        if total > 32768:
            raise ValueError("Excessive vendor encoding")
        output[mac] = parse_mice_vendor_extensions(bytes.fromhex(block) for block in blocks)
    observed = data.get("observed_at", {})
    if not isinstance(observed, dict) or not set(observed).issubset(data["peers"]):
        raise ValueError("Unexpected age response")
    now = time.monotonic()
    for value in observed.values():
        if value is not None and (type(value) not in (int, float) or not math.isfinite(value)
                                  or not 0 <= value <= now):
            raise ValueError("Invalid observation time")
    return output, observed


def network_snapshot(client):
    """Reject proposals if NM topology/activation changes during this run."""
    rows = []
    for device in client.get_devices():
        active = device.get_active_connection()
        rows.append((device.get_iface(), int(device.get_state()),
                     active.get_uuid() if active else "", active.get_path() if active else ""))
    return tuple(sorted(rows))


def run(authorize=False):
    link = P2PLink()
    initial = network_snapshot(link.client)
    receivers = link.discover(timeout=8)
    now = time.monotonic()
    boot_now = time.clock_gettime(time.CLOCK_BOOTTIME)
    scope = "diagnostic:" + str(uuid.uuid4())
    observations = []
    by_interface = {}
    for receiver in receivers:
        mac = receiver.address.removeprefix("p2p:")
        matches = [p for d in link.devices() if d.get_iface() == receiver.interface
                   for p in d.get_peers() if p.get_hw_address().lower() == mac]
        # NM LastSeen is CLOCK_BOOTTIME seconds, not the Python monotonic epoch.
        if len(matches) != 1:
            continue
        seen = matches[0].get_last_seen()
        if seen <= 0 or seen > boot_now:
            continue
        observation = EndpointObservation(device_from_receiver(receiver).endpoints[0], scope,
                                          now - max(0, boot_now - seen))
        observations.append((receiver, observation))
        by_interface.setdefault(receiver.interface, []).append(mac)
    advertisements = {}
    observed_times = {}
    reader = pathlib.Path(__file__).resolve().parents[1] / "scripts/read_p2p_metadata.py"
    read_status = "ok" if by_interface else "not-attempted"
    read_failures = []
    for interface, selected in by_interface.items():
        if len(selected) > 16:
            read_status = "too-many-peers"
            continue
        command = ["/usr/bin/python", "-I", str(reader), interface, *selected]
        if authorize:
            command.insert(0, "/usr/bin/pkexec")
        result = subprocess.run(command, capture_output=True, timeout=60)
        if result.returncode:
            read_status = "read-unavailable"
            failure = "authorization-unavailable" if result.returncode in (126, 127) else "reader-unavailable"
            if len(result.stderr) <= 1024:
                try:
                    stage = json.loads(result.stderr).get("stage")
                    if stage in {"interface-lookup", "peer-list", "peer-address", "vendor-extension", "reader"}:
                        failure = stage
                except (ValueError, AttributeError):
                    pass
            read_failures.append(failure)
            continue
        decoded, times = decode_reply(result.stdout, interface, selected)
        advertisements.update({(interface, mac): value for mac, value in decoded.items()})
        observed_times.update({(interface, mac): value for mac, value in times.items()})
    lan_started = time.monotonic()
    lan_receivers = enrich_receiver_details(discover_mice_receivers())
    lan = [EndpointObservation(device_from_receiver(r).endpoints[0], scope, lan_started)
           for r in lan_receivers]
    # Pump pending NM changes before checking the network epoch.
    from gi.repository import GLib
    context = GLib.MainContext.default()
    while context.pending():
        context.iteration(False)
    unchanged = network_snapshot(link.client) == initial
    by_id = {o.endpoint.id: r.name for r, o in zip(lan_receivers, lan)}
    results = []
    fresh_count = 0
    for receiver, peer in observations:
        key = (receiver.interface, receiver.address[4:])
        advertisement = advertisements.get(key)
        seen = observed_times.get(key)
        if seen is None:
            advertisement = None
        else:
            peer = replace(peer, observed_at=seen)
            fresh_count += int(0 <= time.monotonic() - seen <= 30)
        proposal = propose_mice_association(peer, advertisement, lan, now=time.monotonic())
        results.append({"peer": receiver.name,
                        "lan_receiver": by_id.get(proposal.lan_endpoint_id) if unchanged else None,
                        "reason": proposal.reason if unchanged else "network-changed",
                        "container_collision_count": len(proposal.colliding_container_ids)})
    return {"diagnostic_only": True, "metadata_read": read_status,
            "read_failures": read_failures,
            "network_unchanged": unchanged, "selected_peer_count": len(observations),
            "fresh_peer_count": fresh_count, "lan_receiver_count": len(lan), "results": results}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--authorize-read", action="store_true")
    args = parser.parse_args()
    try:
        print(json.dumps(run(args.authorize_read), indent=2))
    except Exception:
        print(json.dumps({"diagnostic_only": True, "error": "diagnostic-unavailable",
                          "system_changes": False}))
        raise SystemExit(1)
