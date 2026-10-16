"""Optional Cast setup: verified display hosts and receiver-specific UFW rules."""
import argparse
import ipaddress
import json
import os
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from chromecast_transport import CAST_PORT, discover_receivers, known_hosts, parse_address, remember_hosts


def state_path():
    base = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state")
    return base / "hovencast/cast-firewall.json"


def load_rules():
    try:
        rules = json.loads(state_path().read_text())
        if not isinstance(rules, list):
            raise ValueError("Invalid saved Chromecast firewall state")
        return rules
    except FileNotFoundError:
        return []


def rule_command(rule, remove=False):
    host = str(ipaddress.IPv4Address(rule["host"]))
    local = str(ipaddress.IPv4Address(rule["local"]))
    interface = rule["interface"]
    import re
    if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,15}", interface):
        raise ValueError("Invalid network interface")
    protocol = rule.get("protocol", "tcp")
    port = rule.get("port", CAST_PORT)
    if (protocol, port) not in (("tcp", CAST_PORT), ("udp", 8766), ("udp", 8767)):
        raise ValueError("Invalid streaming firewall rule")
    return ["/usr/bin/pkexec", "/usr/bin/ufw"] + (["--force", "delete"] if remove else []) + [
        "allow", "in", "on", interface, "proto", protocol, "from", host,
        "to", local, "port", str(port)]


def save_rules(rules):
    path = state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rules, indent=2) + "\n")
    path.chmod(0o600)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", action="append", default=[])
    parser.add_argument("--remove-firewall", action="store_true")
    args = parser.parse_args()
    rules = load_rules()
    if args.remove_firewall:
        for rule in list(rules):
            subprocess.run(rule_command(rule, True), check=True)
            rules.remove(rule)
            save_rules(rules)
        return
    hosts = [str(ipaddress.IPv4Address(x)) for x in args.host] or known_hosts()
    receivers = discover_receivers(6, hosts)
    verified = [parse_address(row["address"])[1] for row in receivers]
    remember_hosts(sorted(set(hosts + verified)))
    ufw = subprocess.run(["systemctl", "is-active", "--quiet", "ufw"], check=False).returncode == 0
    for row in receivers:
        _, host = parse_address(row["address"])
        route = json.loads(subprocess.check_output(["ip", "-j", "route", "get", host], text=True))[0]
        for protocol, port in (("tcp", CAST_PORT), ("udp", 8766), ("udp", 8767)):
            rule = dict(host=host, local=route["prefsrc"], interface=route["dev"])
            if protocol == "udp":
                rule.update(protocol=protocol, port=port)
            if ufw and rule not in rules:
                print(f"Allowing {row['name']} on {protocol.upper()}/{port}.", flush=True)
                result = subprocess.run(rule_command(rule), check=True, text=True, stdout=subprocess.PIPE, env=dict(os.environ, LC_ALL="C"))
                print(result.stdout, end="", flush=True)
                if "Skipping" not in result.stdout:
                    rules.append(rule)
                    save_rules(rules)
    print(json.dumps(dict(ready=bool(receivers), receivers=receivers), indent=2))
    if not receivers:
        raise SystemExit("No Cast displays resolved. Retry with --host DEVICE_IP.")


if __name__ == "__main__":
    main()
