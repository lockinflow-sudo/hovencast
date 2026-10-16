import asyncio
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock
import airplay_native as native


class NativePairingTests(unittest.TestCase):
    def test_native_pairing_consumes_pin_privately_and_saves_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            helper=root/'helper'
            helper.write_text('''#!/usr/bin/env python3
import sys,json,os
from pathlib import Path
args=sys.argv
assert args[args.index('-expected-device-id')+1]=='00:11:22:33:44:55'
assert '-pair-only' in args
print('Enter the PIN shown on the receiver: ',end='',flush=True)
assert input()=='0123'
p=Path(args[args.index('-creds')+1])
fd=os.open(p,os.O_CREAT|os.O_WRONLY,0o600)
with os.fdopen(fd,'w') as f: json.dump({'00:11:22:33:44:55': {'pairing_id':'test','ed25519_seed':'test','ed25519_public':'test'}},f)
''')
            helper.chmod(0o700)
            pin=root/'pin.json'; credentials=root/'state'/'credentials.json'
            address='airplay:00:11:22:33:44:55@192.0.2.1'
            def output(line, **kwargs):
                if 'airplay-pin-required' in line:
                    pin.write_text(json.dumps(dict(address=address,pin='0123')))
            async def run():
                with mock.patch('builtins.print',side_effect=output) as printed:
                    self.assertEqual(await native.pair_receiver(address,pin),0)
                    self.assertNotIn('0123',str(printed.call_args_list))
            with mock.patch.dict(os.environ,HOVENCAST_AIRPLAY_NATIVE=str(helper),HOVENCAST_AIRPLAY_NATIVE_CREDENTIALS=str(credentials)):
                asyncio.run(run())
                self.assertIsNotNone(native.configuration('00:11:22:33:44:55'))
                self.assertIsNone(native.configuration('AA:BB:CC:DD:EE:FF'))
            self.assertFalse(pin.exists())
            self.assertEqual(credentials.stat().st_mode&0o777,0o600)
            self.assertEqual(credentials.parent.stat().st_mode&0o777,0o700)

    def test_malformed_credentials_do_not_select_native_transport(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);helper=root/'helper';helper.write_text('');helper.chmod(0o700)
            creds=root/'credentials'
            with mock.patch.dict(os.environ,HOVENCAST_AIRPLAY_NATIVE=str(helper),HOVENCAST_AIRPLAY_NATIVE_CREDENTIALS=str(creds)):
                for data in ('[]','{','{"device":{}}'):
                    creds.write_text(data)
                    self.assertIsNone(native.configuration('device'))
