#!/usr/bin/python
"""Root-owned, on-demand P2P join/firewall lease. No saved firewall rules."""
import ipaddress
import json
import os
import pathlib
import pwd
import re
import select
import signal
import subprocess
import sys
import time
import uuid

MAC_PATTERN = r"(?:[0-9a-f]{2}:){5}[0-9a-f]{2}"


def leased_peer_address(contents, interface_mac, local, network, now):
    """Use only an unexpired lease for the selected associated P2P member."""
    if not re.fullmatch(MAC_PATTERN, interface_mac):
        raise ValueError("Invalid P2P member address")
    matches = set()
    for line in contents.splitlines():
        fields = line.split()
        if len(fields) < 3 or fields[1].lower() != interface_mac:
            continue
        try:
            expiry = int(fields[0])
            address = ipaddress.IPv4Address(fields[2])
        except ValueError:
            continue
        if expiry != 0 and expiry <= now:
            continue
        if (address in network and str(address) != local
                and address not in {network.network_address, network.broadcast_address}
                and not address.is_multicast and not address.is_loopback):
            matches.add(str(address))
    if len(matches) > 1:
        raise ValueError("Ambiguous selected receiver DHCP leases")
    return next(iter(matches), "")


def group_member_mac(interface, mac):
    if not re.fullmatch(r"p2p-[A-Za-z0-9_.-]{1,40}", interface) or not re.fullmatch(MAC_PATTERN, mac):
        raise ValueError("Invalid P2P group or selected peer")
    # Supplicant maps the selected device MAC to its *current group interface*
    # MAC. Never guess from a device name, first neighbour, or subnet-wide rule.
    result = subprocess.run(["/usr/bin/wpa_cli", "-i", interface, "p2p_group_member", mac],
                            capture_output=True, text=True, check=True, timeout=5)
    member = result.stdout.strip().lower()
    return member if re.fullmatch(MAC_PATTERN, member) else ""


def group_member_lease(interface, member, local, network):
    if not re.fullmatch(r"p2p-[A-Za-z0-9_.-]{1,40}", interface) or not re.fullmatch(MAC_PATTERN, member):
        raise ValueError("Invalid selected P2P lease identity")
    path = f"/var/lib/NetworkManager/dnsmasq-{interface}.leases"
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        return ""
    with os.fdopen(fd) as lease_file:
        contents = lease_file.read(65537)
    if len(contents) > 65536:
        raise ValueError("P2P lease file is too large")
    return leased_peer_address(contents, member, local, network, time.time())


def group_member_address(interface, mac, local, network):
    member = group_member_mac(interface, mac)
    return group_member_lease(interface, member, local, network) if member else ""


def dhcp_peer_rule(interface, member, token):
    if not re.fullmatch(r"p2p-[A-Za-z0-9_.-]{1,40}", interface) or not re.fullmatch(MAC_PATTERN, member):
        raise ValueError("Invalid selected P2P DHCP client")
    # DHCP starts with source 0.0.0.0 and a broadcast destination. Bind the
    # exception to the selected associated member's MAC, not an entire subnet.
    return ["INPUT", "-i", interface, "-p", "udp", "--sport", "68", "--dport", "67",
            "-m", "mac", "--mac-source", member, "-m", "comment", "--comment",
            "hovencast-dhcp-" + str(uuid.UUID(token)), "-j", "ACCEPT"]


def remove_rules(rule, dhcp_rule):
    cleanup_errors = []
    for candidate, event_name in ((rule, "p2p-firewall-removed"), (dhcp_rule, "p2p-dhcp-removed")):
        if candidate:
            try:
                subprocess.run(["/usr/bin/iptables", "-w", "5", "-D", *candidate], check=True, timeout=10)
                emit(event_name)
            except Exception as error:
                cleanup_errors.append(str(error))
    if cleanup_errors:
        raise RuntimeError("P2P firewall cleanup failed: " + "; ".join(cleanup_errors))


def emit(event, **fields):
    print(json.dumps(dict(event=event, **fields)), flush=True)


def process_identity(pid):
    path = pathlib.Path(f"/proc/{pid}")
    stat = (path / "stat").read_text().rsplit(")", 1)[1].split()
    return path.stat().st_uid, stat[19]


def firewall_rule(interface, local, remote, token):
    if not re.fullmatch(r"p2p-[A-Za-z0-9_.-]{1,40}", interface):
        raise ValueError("Not a Wi-Fi Direct interface")
    local, remote = ipaddress.IPv4Address(local), ipaddress.IPv4Address(remote)
    if local == remote or remote.is_multicast or remote.is_unspecified or remote.is_loopback:
        raise ValueError("Invalid receiver address")
    return ["INPUT", "-i", interface, "-s", str(remote) + "/32", "-d", str(local) + "/32",
            "-p", "tcp", "--dport", "7236", "-m", "comment", "--comment", "hovencast-" + str(uuid.UUID(token)), "-j", "ACCEPT"]


def run(argv):
    if os.geteuid() != 0 or len(argv) != 4:
        raise ValueError("Run the installed helper through pkexec")
    uid = int(os.environ["PKEXEC_UID"])
    pid = int(argv[0])
    token = str(uuid.UUID(argv[1]))
    iface, mac = argv[2:]
    if not re.fullmatch(r"p2p-dev-[A-Za-z0-9_.-]{1,30}", iface) or not re.fullmatch(r"(?:[0-9a-f]{2}:){5}[0-9a-f]{2}", mac):
        raise ValueError("Invalid P2P device or peer")
    identity = process_identity(pid)
    if uid == 0 or identity[0] != uid:
        raise ValueError("The casting process does not belong to the caller")
    username = pwd.getpwuid(uid).pw_name
    import gi
    gi.require_version("NM", "1.0")
    from gi.repository import GLib, NM
    client = NM.Client.new(None)
    device = next((d for d in client.get_devices() if isinstance(d, NM.DeviceWifiP2P) and d.get_iface() == iface), None)
    wifi = iface.removeprefix("p2p-dev-")
    if device is None or not any(isinstance(d, NM.DeviceWifi) and d.get_iface() == wifi for d in client.get_devices()):
        raise ValueError("P2P adapter was not found")
    peer = next((p for p in device.get_peers() if p.get_hw_address().lower() == mac), None)
    if peer is None:
        raise ValueError("Selected receiver is no longer visible")
    rule = None
    dhcp_rule = None
    joined = False
    connected = False
    deadline = time.monotonic() + 75
    context = GLib.MainContext.default()
    stopped = False
    def stop(*_args):
        nonlocal stopped
        stopped = True
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    emit("helper-authorized")
    try:
        while not stopped:
            while context.pending():
                context.iteration(False)
            if process_identity(pid) != identity:
                break
            if select.select([sys.stdin], [], [], 0)[0] and not os.read(sys.stdin.fileno(), 1):
                break
            active = device.get_active_connection()
            if active is None or active.get_uuid() != token:
                if connected:
                    break
                if time.monotonic() > deadline:
                    raise TimeoutError("HovenCast pairing did not activate")
                time.sleep(0.05)
                continue
            connection = active.get_connection()
            general = connection.get_setting_connection()
            setting = connection.get_setting(NM.SettingWifiP2P)
            if general.get_id() != "HovenCast Wi-Fi Direct" or setting is None or setting.props.peer.lower() != mac or list(general.props.permissions) != [f"user:{username}:"]:
                raise ValueError("Refusing a connection not owned by this HovenCast session")
            if device.get_state() == NM.DeviceState.CONFIG and not joined:
                info = subprocess.run(["/usr/bin/wpa_cli", "-i", wifi, "p2p_peer", mac], capture_output=True, text=True, check=True, timeout=5).stdout
                group = re.search(r"^group_capab=(0x[0-9a-fA-F]+)$", info, re.M)
                if group and int(group[1], 16) & 1:
                    for command in (["p2p_cancel"], ["p2p_connect", mac, "pbc", "join"]):
                        result = subprocess.run(["/usr/bin/wpa_cli", "-i", wifi, *command], capture_output=True, text=True, check=True, timeout=5)
                        if "OK" not in result.stdout.splitlines():
                            raise RuntimeError("The Wi-Fi Direct group join was rejected")
                    emit("p2p-group-join")
                joined = True
            if device.get_state() == NM.DeviceState.ACTIVATED and not connected:
                config = active.get_ip4_config()
                addresses = config.get_addresses() if config else []
                dhcp = active.get_dhcp4_config()
                options = dhcp.get_options() if dhcp else {}
                remote = config.get_gateway() or options.get("routers", "").split(" ")[0]
                if not addresses:
                    raise RuntimeError("The Wi-Fi Direct link has no IPv4 address")
                local = addresses[0].get_address()
                network = ipaddress.ip_network(f"{local}/{addresses[0].get_prefix()}", strict=False)
                if not remote:
                    member = group_member_mac(device.get_ip_iface(), mac)
                    if member and dhcp_rule is None:
                        candidate = dhcp_peer_rule(device.get_ip_iface(), member, token)
                        subprocess.run(["/usr/bin/iptables", "-w", "5", "-I", candidate[0], "1", *candidate[1:]], check=True, timeout=10)
                        dhcp_rule = candidate
                        emit("p2p-dhcp-ready", member=member)
                    remote = group_member_lease(device.get_ip_iface(), member, local, network) if member else ""
                    if not remote:
                        if time.monotonic() > deadline:
                            raise TimeoutError("The selected display did not obtain a Wi-Fi Direct address")
                        time.sleep(0.1)
                        continue
                    emit("p2p-receiver-address", role="group-owner", receiver=remote)
                if ipaddress.IPv4Address(remote) not in network:
                    raise ValueError("Receiver address is outside the P2P link")
                candidate = firewall_rule(device.get_ip_iface(), local, remote, token)
                subprocess.run(["/usr/bin/iptables", "-w", "5", "-I", candidate[0], "1", *candidate[1:]], check=True, timeout=10)
                rule = candidate
                connected = True
                emit("p2p-firewall-ready", receiver=remote)
            if not connected and time.monotonic() > deadline:
                raise TimeoutError("Wi-Fi Direct pairing timed out")
            time.sleep(0.05)
    finally:
        remove_rules(rule, dhcp_rule)


if __name__ == "__main__":
    try:
        run(sys.argv[1:])
    except Exception as error:
        emit("helper-error", error=str(error))
        sys.exit(1)
