"""Client for the root-owned, session-lifetime P2P helper."""
import json
import os
import pathlib
import select
import subprocess
import time

HELPER = pathlib.Path("/usr/local/libexec/hovencast-p2p-helper.py")
ENTRYPOINT = pathlib.Path("/usr/local/libexec/hovencast-p2p-helper")


class P2PPrivilege:
    def __init__(self, token, interface, peer):
        for path in (HELPER, ENTRYPOINT):
            if path.is_symlink() or not path.is_file() or path.stat().st_uid != 0 or path.stat().st_mode & 0o022:
                raise RuntimeError("Install HovenCast's root-owned P2P helper with scripts/install-p2p-helper before casting")
        self.process = subprocess.Popen(
            ["/usr/bin/pkexec", str(ENTRYPOINT), str(os.getpid()), token, interface, peer],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=None, bufsize=0)
        self.buffer = b""
        self.events = []
        self.closed = False

    def wait(self, expected, cancelled=lambda: False, timeout=90, tick=lambda: None):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if cancelled():
                raise InterruptedError("Wi-Fi Direct connection cancelled")
            tick()
            self.check()
            for event in self.events:
                if event.get("event") == expected:
                    self.events.remove(event)
                    return event
            time.sleep(0.02)
        if expected == "p2p-firewall-ready":
            raise TimeoutError("The Wi-Fi Direct link remained active but the selected receiver's address was not ready")
        raise TimeoutError("Wi-Fi Direct authorization/helper timed out")

    def check(self):
        if select.select([self.process.stdout], [], [], 0.05)[0]:
            data = os.read(self.process.stdout.fileno(), 8192)
            self.buffer += data
            while b"\n" in self.buffer:
                line, self.buffer = self.buffer.split(b"\n", 1)
                event = json.loads(line)
                self.events.append(event)
                # Timestamp milestones in the parent session log; do not log
                # raw supplicant data, credentials, or unrestricted fields.
                if event.get("event") in {"helper-authorized", "p2p-group-join",
                                         "p2p-dhcp-ready", "p2p-receiver-address",
                                         "p2p-firewall-ready", "helper-error"}:
                    print(json.dumps({"event": "p2p-helper-event",
                                      "helper_event": event["event"],
                                      "time": time.time(),
                                      **{key: event[key] for key in ("role", "receiver", "member", "error")
                                         if key in event}}, sort_keys=True), flush=True)
                if event.get("event") == "helper-error":
                    raise RuntimeError(event.get("error", "P2P helper failed"))
        if self.process.poll() is not None:
            raise RuntimeError("Wi-Fi Direct authorization/helper failed; try connecting again")

    def close(self):
        if self.closed:
            return
        self.closed = True
        self.process.stdin.close()  # EOF releases the root-owned firewall lease.
        try:
            self.process.wait(timeout=12)
            remaining = self.process.stdout.read().decode("utf-8")
            if remaining:
                print(remaining, end="", flush=True)
            if self.process.returncode not in (0, 126, 127):
                raise RuntimeError("Wi-Fi Direct helper exited with an error; check the session log")
        except subprocess.TimeoutExpired:
            # The helper also watches the caller's PID/start time. Never claim
            # cleanup succeeded if authorization or cleanup is still pending.
            raise RuntimeError("P2P helper cleanup is still pending")
        finally:
            self.process.stdout.close()
