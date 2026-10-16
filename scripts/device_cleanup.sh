#!/bin/bash
set -euo pipefail
root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
if pgrep -f '^/usr/bin/python -I /usr/local/libexec/hovencast-p2p-helper.py' >/dev/null; then
  echo "Stop the active HovenCast Wi-Fi Direct session before removal." >&2
  exit 1
fi
cast_env="${XDG_DATA_HOME:-$HOME/.local/share}/hovencast/chromecast-venv"
if [[ -x "$cast_env/bin/python" ]]; then
  "$cast_env/bin/python" "$root/scripts/chromecast_setup.py" --remove-firewall
fi
if [[ -f /usr/local/libexec/hovencast-p2p-helper.py || -f /usr/local/libexec/hovencast-p2p-helper || -f /usr/share/polkit-1/actions/org.hovencast.p2p.policy || -f /etc/polkit-1/rules.d/49-hovencast-p2p.rules || -e /etc/NetworkManager/conf.d/90-hovencast-wifi-compat.conf ]]; then
  bash "$root/scripts/remove-p2p-helper"
fi
