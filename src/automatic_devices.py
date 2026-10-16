"""Fresh snapshot grouping for the beta. No name/MAC/IP-only merging.

Rows retain exact legacy endpoints; default is LAN, alternatives are explicit.
Missing/denied metadata leaves discovery usable with separate rows.
"""
import os
import pathlib
import subprocess
import time
import uuid

from cast_devices import device_from_receiver
from device_reconciliation import EndpointObservation, propose_mice_association
from identity_diagnostic import decode_reply, network_snapshot

READER = pathlib.Path("/usr/local/libexec/hovencast-metadata-reader")
READER_SOURCE = pathlib.Path("/usr/local/libexec/hovencast-metadata-reader.py")


def trusted_reader():
    for path in (READER, READER_SOURCE):
        if path.is_symlink() or not path.is_file():
            return False
        stat = path.stat()
        if stat.st_uid != 0 or stat.st_mode & 0o022:
            return False
    return True


def read_metadata(peers):
    if not peers or not trusted_reader():
        return {}, {}
    groups = {}
    for peer in peers:
        groups.setdefault(peer.interface, set()).add(peer.address.removeprefix("p2p:"))
    advertisements, observed = {}, {}
    for interface, macs in groups.items():
        if not 1 <= len(macs) <= 16:
            continue
        selected = sorted(macs)
        try:
            result = subprocess.run(
                ["/usr/bin/pkexec", "--disable-internal-agent", str(READER),
                 str(os.getpid()), interface, *selected],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=15)
            if result.returncode:
                continue
            decoded, times = decode_reply(result.stdout, interface, selected)
        except (OSError, ValueError, subprocess.TimeoutExpired):
            continue
        advertisements.update({(interface, mac): value for mac, value in decoded.items()})
        observed.update({(interface, mac): value for mac, value in times.items()})
    return advertisements, observed


def grouped_rows(lan, peers, advertisements, observed, *, now, lan_seen, scope):
    observations = [EndpointObservation(device_from_receiver(r).endpoints[0], scope, lan_seen)
                    for r in lan]
    rows = [r.json() for r in lan]
    indices = {o.endpoint.id: i for i, o in enumerate(observations)}
    proposals = []
    for peer in peers:
        key = (peer.interface, peer.address.removeprefix("p2p:"))
        seen = observed.get(key)
        proposal = None
        if seen is not None:
            observation = EndpointObservation(device_from_receiver(peer).endpoints[0], scope, seen)
            proposal = propose_mice_association(observation, advertisements.get(key), observations, now=now)
        proposals.append(proposal.lan_endpoint_id if proposal else None)
    # Multiple P2P peers claiming one LAN endpoint are ambiguous: keep all rows.
    for peer, target in zip(peers, proposals):
        if target is not None and proposals.count(target) == 1:
            row = rows[indices[target]]
            row["endpoints"] = [lan[indices[target]].json(), peer.json()]
        else:
            rows.append(peer.json())
    return rows


def discover_rows(lan, peers, *, lan_seen, client=None, initial=None):
    if not peers or client is None or initial is None:
        return [r.json() for r in (*lan, *peers)]
    advertisements, observed = read_metadata(peers)
    if client is not None:
        from gi.repository import GLib
        context = GLib.MainContext.default()
        while context.pending():
            context.iteration(False)
        if network_snapshot(client) != initial:
            advertisements, observed = {}, {}
    return grouped_rows(lan, peers, advertisements, observed, now=time.monotonic(),
                        lan_seen=lan_seen, scope="scan:" + str(uuid.uuid4()))
