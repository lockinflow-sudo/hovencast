import importlib.util
import pathlib
import sys
import unittest
import subprocess
import json
import ipaddress
import xml.etree.ElementTree as ET
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from p2p_privilege import P2PPrivilege
spec = importlib.util.spec_from_file_location("p2p_helper", ROOT / "scripts/p2p_helper.py")
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)


class P2PPrivilegeTest(unittest.TestCase):
    def test_cleanup_attempts_both_rules_even_if_one_deletion_fails(self):
        with mock.patch.object(helper.subprocess, "run", side_effect=[RuntimeError("first deletion failed"), None]) as command, mock.patch.object(helper, "emit") as event:
            with self.assertRaisesRegex(RuntimeError, "cleanup failed"):
                helper.remove_rules(["INPUT", "rtsp"], ["INPUT", "dhcp"])
            self.assertEqual(command.call_count, 2)
            self.assertEqual(command.call_args.args[0], ["/usr/bin/iptables", "-w", "5", "-D", "INPUT", "dhcp"])
            event.assert_called_once_with("p2p-dhcp-removed")

    def test_dhcp_bootstrap_is_selected_mac_and_p2p_interface_only(self):
        rule = helper.dhcp_peer_rule("p2p-wlp2s0-2", "16:fe:2b:f7:ae:11", "87930c51-a891-4156-a140-4643dfe76456")
        self.assertIn("16:fe:2b:f7:ae:11", rule)
        self.assertIn("p2p-wlp2s0-2", rule)
        self.assertEqual(rule[rule.index("--sport") + 1], "68")
        self.assertEqual(rule[rule.index("--dport") + 1], "67")
        for interface, mac in [("wlp2s0", "16:fe:2b:f7:ae:11"), ("p2p-test", "bad"), ("p2p-test+", "16:fe:2b:f7:ae:11")]:
            with self.assertRaises(ValueError):
                helper.dhcp_peer_rule(interface, mac, "87930c51-a891-4156-a140-4643dfe76456")

    def test_group_owner_lease_matches_interface_mac_not_discovery_mac(self):
        network = ipaddress.ip_network("10.42.0.0/24")
        contents = "200 1e:fe:2b:f7:ae:11 10.42.0.20 wrong *\n200 16:fe:2b:f7:ae:11 10.42.0.31 selected *\n"
        self.assertEqual(helper.leased_peer_address(contents, "16:fe:2b:f7:ae:11", "10.42.0.1", network, 100), "10.42.0.31")

    def test_group_owner_lease_rejects_expired_outside_local_and_ambiguous(self):
        network = ipaddress.ip_network("10.42.0.0/24")
        mac = "16:fe:2b:f7:ae:11"
        for lease in [f"99 {mac} 10.42.0.31 * *", f"200 {mac} 192.168.1.31 * *", f"200 {mac} 10.42.0.1 * *", f"200 {mac} 10.42.0.255 * *", f"bad {mac} bad * *"]:
            self.assertEqual(helper.leased_peer_address(lease, mac, "10.42.0.1", network, 100), "")
        with self.assertRaises(ValueError):
            helper.leased_peer_address(f"200 {mac} 10.42.0.31 * *\n200 {mac} 10.42.0.32 * *", mac, "10.42.0.1", network, 100)

    def test_group_owner_waits_for_selected_association_without_guessing(self):
        with mock.patch.object(helper.subprocess, "run", return_value=mock.Mock(stdout="FAIL\n")) as command, mock.patch.object(helper.os, "open") as opened:
            self.assertEqual(helper.group_member_address("p2p-wlp2s0-1", "1e:fe:2b:f7:ae:11", "10.42.0.1", ipaddress.ip_network("10.42.0.0/24")), "")
            opened.assert_not_called()
            self.assertEqual(command.call_args.args[0], ["/usr/bin/wpa_cli", "-i", "p2p-wlp2s0-1", "p2p_group_member", "1e:fe:2b:f7:ae:11"])

    def test_launch_uses_only_fixed_root_owned_entrypoint(self):
        with mock.patch("p2p_privilege.HELPER") as implementation, mock.patch("p2p_privilege.ENTRYPOINT") as entry, mock.patch("p2p_privilege.subprocess.Popen") as launch:
            for path in (implementation, entry):
                path.is_symlink.return_value = False
                path.is_file.return_value = True
                path.stat.return_value.st_uid = 0
                path.stat.return_value.st_mode = 0o100755
            entry.__str__.return_value = "/usr/local/libexec/hovencast-p2p-helper"
            P2PPrivilege("token", "p2p-dev-test", "peer")
            argv = launch.call_args.args[0]
            self.assertEqual(argv[:2], ["/usr/bin/pkexec", "/usr/local/libexec/hovencast-p2p-helper"])
            self.assertNotIn("/usr/bin/python", argv)

    def test_untrusted_helper_is_never_launched(self):
        for owner, mode, symlink in [(1000, 0o755, False), (0, 0o777, False), (0, 0o755, True)]:
            with self.subTest(owner=owner, mode=mode, symlink=symlink), mock.patch("p2p_privilege.HELPER") as path, mock.patch("p2p_privilege.subprocess.Popen") as launch:
                path.is_symlink.return_value = symlink
                path.is_file.return_value = True
                path.stat.return_value.st_uid = owner
                path.stat.return_value.st_mode = mode
                with self.assertRaises(RuntimeError):
                    P2PPrivilege("token", "p2p-dev-test", "peer")
                launch.assert_not_called()

    def test_policy_grants_only_fixed_helper_to_active_local_session(self):
        action = ET.parse(ROOT / "scripts/org.hovencast.p2p.policy").getroot().find("action")
        self.assertEqual(action.attrib["id"], "org.hovencast.p2p.connect")
        self.assertEqual([node.text for node in action.find("defaults")], ["no", "no", "no"])
        self.assertEqual(action.find("annotate").text, "/usr/local/libexec/hovencast-p2p-helper")
        script = (ROOT / "scripts/49-hovencast-p2p.rules").read_text()
        harness = '''let rule; const polkit={Result:{YES:"yes",NO:"no"},addRule:r=>rule=r};
        SCRIPT
        let results=[];
        for (const local of [true,false]) for (const active of [true,false])
          results.push(rule({id:"org.hovencast.p2p.connect"},{local,active}));
        results.push(rule({id:"org.freedesktop.policykit.exec"},{local:true,active:true}) ?? null);
        console.log(JSON.stringify(results));'''.replace("SCRIPT", script)
        result = subprocess.run(["node", "-e", harness], capture_output=True, text=True, check=True)
        self.assertEqual(json.loads(result.stdout), ["yes", "no", "no", "no", None])
        wrapper = (ROOT / "scripts/hovencast-p2p-helper").read_text()
        self.assertIn('exec /usr/bin/python -I /usr/local/libexec/hovencast-p2p-helper.py "$@"', wrapper)

    def test_helper_rejects_extra_arguments_and_interpreter_options(self):
        with mock.patch.object(helper.os, "geteuid", return_value=0), mock.patch.dict(helper.os.environ, {"PKEXEC_UID": "1000"}):
            with self.assertRaises(ValueError):
                helper.run(["-c", "print('not executable')", "p2p-dev-test", "aa:bb:cc:dd:ee:ff"])
            with self.assertRaises(ValueError):
                helper.run(["1", "token", "iface", "peer", "extra"])

    def test_rule_is_exact_peer_interface_destination_and_rtsp_only(self):
        rule = helper.firewall_rule("p2p-wlp2s0-20", "192.168.49.142", "192.168.49.1", "87930c51-a891-4156-a140-4643dfe76456")
        self.assertIn("192.168.49.1/32", rule)
        self.assertIn("192.168.49.142/32", rule)
        self.assertIn("p2p-wlp2s0-20", rule)
        self.assertIn("7236", rule)
        self.assertNotIn("+", " ".join(rule))

    def test_rule_rejects_lan_interfaces_injection_and_wrong_addresses(self):
        for interface, receiver in [("wlp2s0", "192.168.49.1"), ("p2p-wlp2s0+", "192.168.49.1"),
                                     ("p2p-test", "127.0.0.1"), ("p2p-test", "192.168.49.142")]:
            with self.assertRaises(ValueError):
                helper.firewall_rule(interface, "192.168.49.142", receiver, "87930c51-a891-4156-a140-4643dfe76456")

    def test_close_releases_stdin_lease_once(self):
        lease = P2PPrivilege.__new__(P2PPrivilege)
        lease.process = mock.Mock()
        lease.process.returncode = 0
        lease.process.stdout.read.return_value = b'{"event":"p2p-firewall-removed"}\n'
        lease.closed = False
        lease.close()
        lease.close()
        lease.process.stdin.close.assert_called_once()
        lease.process.wait.assert_called_once_with(timeout=12)

    def test_denied_authorization_is_reported(self):
        lease = P2PPrivilege.__new__(P2PPrivilege)
        lease.process = mock.Mock()
        lease.process.poll.return_value = 126
        lease.buffer = b""
        lease.events = []
        with mock.patch("p2p_privilege.select.select", return_value=([], [], [])):
            with self.assertRaisesRegex(RuntimeError, "authorization"):
                lease.wait("helper-authorized")

    def test_cancel_interrupts_authorization(self):
        lease = P2PPrivilege.__new__(P2PPrivilege)
        with self.assertRaises(InterruptedError):
            lease.wait("helper-authorized", cancelled=lambda: True)
