from pathlib import Path
import sys
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import chromecast_setup


class AirPlayFirewallTests(unittest.TestCase):
    def test_legacy_video_rules_and_new_timing_rules_are_removable(self):
        rule = dict(host='192.0.2.1', local='192.0.2.2', interface='wlan0')
        legacy = chromecast_setup.rule_command(rule, True)
        self.assertIn('tcp', legacy)
        self.assertEqual(legacy[-1], '8765')
        rule.update(protocol='udp', port=8766)
        timing = chromecast_setup.rule_command(rule, True)
        self.assertIn('delete', timing)
        self.assertIn('udp', timing)
        self.assertEqual(timing[-1], '8766')
        self.assertIn('192.0.2.1', timing)
        self.assertIn('192.0.2.2', timing)

    def test_saved_rules_cannot_open_arbitrary_ports_or_protocols(self):
        for protocol, port in [('udp', 0), ('udp', '1:65535'), ('tcp', 22), ('any', 8766)]:
            with self.assertRaises(ValueError):
                chromecast_setup.rule_command(dict(host='192.0.2.1', local='192.0.2.2', interface='wlan0',protocol=protocol,port=port))
