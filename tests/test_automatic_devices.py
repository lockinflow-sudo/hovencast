import pathlib
import sys
import unittest
from unittest import mock
import os
import json

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
import automatic_devices as automatic
from omarchy_cast_protocol import Receiver
from cast_identity import DiscoveryService, IdentityEvidence
from mice_vendor import MiceAdvertisement


def lan(serial="TEST123", address="192.0.2.1", device_id="DEVICE123"):
    return Receiver("Same name", address, 7250, "wlan0",
                    discovery_service=DiscoveryService("service", "_display._tcp", "local", serial + ".local"),
                    identity_evidence=(IdentityEvidence("roku-serial-number", serial, "roku-ecp"),
                                       IdentityEvidence("roku-device-id", device_id, "roku-ecp"),
                                       IdentityEvidence("mice-container-id", "11111111-2222-3333-4444-555555555555", "mice-mdns")))


class GroupingTests(unittest.TestCase):
    def setUp(self):
        self.lan = lan()
        self.peer = Receiver("Same name", "p2p:02:00:00:00:00:01", 7236, "p2p-dev-wlan0", protocol="miracast-p2p")
        self.key = (self.peer.interface, self.peer.address[4:])
        self.advertisements = {self.key: MiceAdvertisement("test123", ("192.0.2.1",), 5)}
        self.observed = {self.key: 95}

    def group(self, lan_rows=None, peers=None, advertisements=None, observed=None):
        return automatic.grouped_rows(lan_rows or [self.lan], peers or [self.peer],
                                      self.advertisements if advertisements is None else advertisements,
                                      self.observed if observed is None else observed,
                                      now=100, lan_seen=90, scope="epoch-1")

    def test_one_row_retains_both_exact_endpoints_and_lan_default(self):
        rows = self.group()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["address"], self.lan.address)
        self.assertEqual(rows[0]["endpoints"], [self.lan.json(), self.peer.json()])
        self.assertNotIn("identity_evidence", json.dumps(rows))
        self.assertNotIn("TEST123", json.dumps(rows))

    def test_identical_names_and_container_ids_do_not_merge_other_tv(self):
        rows = self.group(lan_rows=[self.lan, lan("OTHER456", "192.0.2.2", "DEVICE456")])
        self.assertEqual(len(rows), 2)
        self.assertIn("endpoints", rows[0])
        self.assertNotIn("endpoints", rows[1])

    def test_absent_stale_invalid_metadata_preserves_legacy_rows(self):
        for observed, advertisements in (({}, {}), ({self.key: 60}, self.advertisements),
                                         ({self.key: 95}, {self.key: None})):
            self.assertEqual(self.group(observed=observed, advertisements=advertisements),
                             [self.lan.json(), self.peer.json()])

    def test_competing_p2p_claims_keep_all_rows(self):
        from dataclasses import replace
        second = replace(self.peer, address="p2p:02:00:00:00:00:02")
        key = (second.interface, second.address[4:])
        rows = self.group(peers=[self.peer, second],
                          advertisements={**self.advertisements, key: self.advertisements[self.key]},
                          observed={**self.observed, key: 95})
        self.assertEqual(rows, [self.lan.json(), self.peer.json(), second.json()])

    def test_network_change_discards_grouping(self):
        with mock.patch.object(automatic, "read_metadata", return_value=(self.advertisements, self.observed)), \
             mock.patch.object(automatic, "network_snapshot", return_value="new"):
            rows = automatic.discover_rows([self.lan], [self.peer], lan_seen=90,
                                            client=mock.Mock(), initial="old")
            self.assertEqual(rows, [self.lan.json(), self.peer.json()])

    def test_missing_network_context_never_reads_or_groups(self):
        with mock.patch.object(automatic, "read_metadata") as read:
            self.assertEqual(automatic.discover_rows([self.lan], [self.peer], lan_seen=90),
                             [self.lan.json(), self.peer.json()])
            read.assert_not_called()


class ReaderClientTests(unittest.TestCase):
    def test_missing_or_untrusted_helper_never_invoked(self):
        with mock.patch.object(automatic, "trusted_reader", return_value=False), \
             mock.patch.object(automatic.subprocess, "run") as run:
            peer = Receiver("TV", "p2p:02:00:00:00:00:01", 7236, "p2p-dev-wlan0", protocol="miracast-p2p")
            self.assertEqual(automatic.read_metadata([peer]), ({}, {}))
            run.assert_not_called()

    def test_installed_entry_point_and_caller_are_fixed(self):
        peer = Receiver("TV", "p2p:02:00:00:00:00:01", 7236, "p2p-dev-wlan0", protocol="miracast-p2p")
        with mock.patch.object(automatic, "trusted_reader", return_value=True), \
             mock.patch.object(automatic.subprocess, "run", return_value=mock.Mock(returncode=1)) as run:
            self.assertEqual(automatic.read_metadata([peer]), ({}, {}))
            command = run.call_args.args[0]
            self.assertEqual(command[:4], ["/usr/bin/pkexec", "--disable-internal-agent",
                                         str(automatic.READER), str(os.getpid())])
            self.assertNotIn("/usr/bin/python", command)

    def test_policy_grants_only_fixed_metadata_action_active_local(self):
        root = pathlib.Path(__file__).resolve().parents[1]
        policy = (root / "scripts/org.hovencast.metadata.policy").read_text()
        rule = (root / "scripts/49-hovencast-metadata.rules").read_text()
        self.assertIn('action id="org.hovencast.metadata.read"', policy)
        self.assertIn(str(automatic.READER), policy)
        self.assertIn('subject.local === true && subject.active === true', rule)
        self.assertNotIn("auth_admin", policy)


if __name__ == "__main__":
    unittest.main()
