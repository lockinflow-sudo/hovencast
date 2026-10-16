#!/bin/bash
# Keep setup failures visible and retain output without recording keyboard input.
set -uo pipefail
umask 077
state_root="${XDG_STATE_HOME:-$HOME/.local/state}/hovencast"
mkdir -p -- "$state_root" || exit 1
log="$state_root/last-setup-terminal.log"
exec > >(tee -- "$log") 2>&1
printf 'HovenCast setup terminal\n'
if [[ $# -ne 1 || ! -x $1 ]]; then
  printf 'Setup command is unavailable. Reinstall the HovenCast plugin.\n'
  result=1
else
  "$1"
  result=$?
fi
printf '\nSetup exited with status %s.\nLog: %s\n' "$result" "$log"
printf 'Press Enter to close…'
read -r </dev/tty || true
exit "$result"
