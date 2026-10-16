"""Install receiver-scoped local streaming firewall rules for Apple TVs."""
import json
import os
import subprocess
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from airplay_transport import discover_receivers, parse_address
from chromecast_setup import load_rules, rule_command, save_rules


def main():
    rows = discover_receivers()
    if not rows:
        raise SystemExit('No Apple TVs found. Enable AirPlay and search again.')
    rules = load_rules()
    ufw = subprocess.run(['systemctl', 'is-active', '--quiet', 'ufw']).returncode == 0
    for row in rows:
        _, host = parse_address(row['address'])
        route = json.loads(subprocess.check_output(['ip', '-j', 'route', 'get', host], text=True))[0]
        for protocol, port in (("tcp", 8765), ("udp", 8766), ("udp", 8767)):
            rule = dict(host=host, local=route['prefsrc'], interface=route['dev'])
            if protocol == "udp":
                rule.update(protocol=protocol, port=port)
            if ufw and rule not in rules:
                print(f"Allowing {row['name']} to receive HovenCast {protocol.upper()}/{port}.", flush=True)
                result = subprocess.run(rule_command(rule), check=True, capture_output=True,
                                        text=True, env=dict(os.environ, LC_ALL='C'))
                if 'Skipping' not in result.stdout:
                    rules.append(rule)
                    save_rules(rules)
    print(json.dumps(dict(ready=True, receivers=rows), indent=2))


if __name__ == '__main__':
    main()
