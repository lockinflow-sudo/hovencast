import asyncio
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock
from urllib.error import HTTPError
from urllib.request import urlopen

import airplay_transport as airplay
import chromecast_transport as media
from pyatv.const import Protocol


@mock.patch.dict(os.environ, {'HOVENCAST_AIRPLAY_NATIVE': ''})
class AirPlayTests(unittest.TestCase):
    def test_receiver_identity_is_validated_before_connecting(self):
        async def run():
            config = mock.Mock()
            service = mock.Mock(identifier='AA:BB:CC:DD:EE:FF', properties={'model': 'AppleTV14,1'})
            config.get_service.return_value = service
            with mock.patch('pyatv.scan', new=mock.AsyncMock(return_value=[config])):
                with self.assertRaisesRegex(RuntimeError, 'changed'):
                    await airplay.select_receiver('airplay:00:11:22:33:44:55@192.0.2.1', mock.Mock())
        asyncio.run(run())
        for value in ['192.0.2.1', 'airplay:bad@192.0.2.1', 'airplay:00:11:22:33:44:55@127.0.0.1;echo bad']:
            with self.assertRaises(ValueError):
                airplay.parse_address(value)

    def test_actual_hls_contains_h264_and_aac_and_bounded_playlist(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            source = folder / 'input.ts'
            subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i',
                            'testsrc2=size=160x90:rate=30', '-f', 'lavfi', '-i',
                            'sine=frequency=440:sample_rate=48000', '-t', '20',
                            '-c:v', 'libx264', '-preset', 'ultrafast', '-profile:v', 'baseline',
                            '-g', '15', '-c:a', 'aac', '-ac', '2', '-f', 'mpegts', str(source)],
                           capture_output=True, check=True, timeout=30)
            subprocess.run(airplay.hls_command(source, folder), capture_output=True, check=True, timeout=20)
            playlist = airplay.live_playlist((folder / 'live.m3u8').read_bytes()).decode()
            self.assertIn('#EXT-X-TARGETDURATION:1\n', playlist)
            segments = [x for x in playlist.splitlines() if not x.startswith('#')]
            self.assertEqual(len(segments), 6)
            durations = [float(x.split(':', 1)[1].rstrip(',')) for x in playlist.splitlines()
                         if x.startswith('#EXTINF:')]
            self.assertLessEqual(max(durations), .6)
            self.assertLessEqual(sum(durations), 3.1)
            probe = json.loads(subprocess.check_output(['ffprobe', '-v', 'error', '-show_streams',
                                                       '-of', 'json', str(folder / segments[0])]))
            self.assertEqual({x['codec_name'] for x in probe['streams']}, {'h264', 'aac'})
            starts = [float(x['start_time']) for x in probe['streams']]
            self.assertLess(max(starts) - min(starts), .1)
            self.assertLessEqual(len(list(folder.glob('airplay*.ts'))), 10)
            self.assertFalse(list(folder.glob('*.tmp')))

    def test_hls_server_refreshes_playlist_and_restricts_files_and_peer(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / 'live.m3u8').write_text('first')
            (folder / 'airplay000000.ts').write_bytes(b'video')
            (folder / 'credentials.json').write_text('secret')
            (folder / 'airplay000001.ts').symlink_to(folder / 'credentials.json')
            server = media.LiveServer(folder, '127.0.0.1', '127.0.0.1', 0,
                                      playlist='live.m3u8', allowed=r'live\.m3u8|airplay\d{6,}\.ts')
            server.start()
            try:
                with urlopen(server.url) as response:
                    self.assertEqual(response.read(), b'first')
                    self.assertEqual(response.headers['Content-Type'], 'application/vnd.apple.mpegurl')
                (folder / 'live.m3u8').write_text('updated')
                with urlopen(server.url) as response:
                    self.assertEqual(response.read(), b'updated')
                base = server.url.rsplit('/', 1)[0]
                with urlopen(base + '/airplay000000.ts') as response:
                    self.assertEqual(response.headers['Content-Type'], 'video/mp2t')
                    self.assertEqual(response.read(), b'video')
                for name in ['credentials.json', '../credentials.json', 'airplay000001.ts', 'airplay000000.ts.tmp']:
                    with self.assertRaises(HTTPError) as error:
                        urlopen(base + '/' + name)
                    self.assertEqual(error.exception.code, 404)
                    error.exception.close()
                with self.assertRaises(HTTPError) as error:
                    urlopen(server.url.replace(server.token, 'wrong-token'))
                error.exception.close()
            finally:
                server.close()
            self.assertFalse(server.thread.is_alive())
            server = media.LiveServer(folder, '127.0.0.1', '192.0.2.1', 0, playlist='live.m3u8')
            server.start()
            try:
                with self.assertRaises(HTTPError) as error:
                    urlopen(server.url)
                self.assertEqual(error.exception.code, 403)
                error.exception.close()
            finally:
                server.close()

    def test_pairing_stores_credentials_privately_and_never_prints_them(self):
        async def run():
            store = mock.Mock(save=mock.AsyncMock())
            pairing = mock.Mock(begin=mock.AsyncMock(), finish=mock.AsyncMock(), close=mock.AsyncMock(), has_paired=True)
            async def begin():
                airplay.submit_pin('airplay:00:11:22:33:44:55@192.0.2.1', '0123')
            pairing.begin.side_effect = begin
            with mock.patch.object(airplay, 'storage', new=mock.AsyncMock(return_value=store)), \
                    mock.patch.object(airplay, 'select_receiver', new=mock.AsyncMock()), \
                    mock.patch('pyatv.pair', new=mock.AsyncMock(return_value=pairing)), \
                    mock.patch('builtins.print') as output:
                await airplay.pair_receiver('airplay:00:11:22:33:44:55@192.0.2.1')
                pairing.pin.assert_called_once_with(123)
                pairing.close.assert_awaited_once()
                store.save.assert_awaited_once()
                self.assertEqual(airplay.state_path().stat().st_mode & 0o777, 0o600)
                self.assertFalse(airplay.pin_path().exists())
                self.assertNotIn('0123', str(output.call_args_list))
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, XDG_RUNTIME_DIR=tmp, XDG_STATE_HOME=tmp):
            # The real storage initializer establishes the private parent.
            asyncio.run(airplay.storage())
            asyncio.run(run())

    def test_bad_pin_closes_pairing_without_saving_credentials(self):
        async def run():
            store = mock.Mock(save=mock.AsyncMock())
            pairing = mock.Mock(begin=mock.AsyncMock(), finish=mock.AsyncMock(side_effect=RuntimeError('incorrect PIN')),
                                close=mock.AsyncMock())
            async def begin():
                airplay.submit_pin('airplay:00:11:22:33:44:55@192.0.2.1', '9999')
            pairing.begin.side_effect = begin
            with mock.patch.object(airplay, 'storage', new=mock.AsyncMock(return_value=store)), \
                    mock.patch.object(airplay, 'select_receiver', new=mock.AsyncMock()), \
                    mock.patch('pyatv.pair', new=mock.AsyncMock(return_value=pairing)):
                with self.assertRaisesRegex(RuntimeError, 'incorrect PIN'):
                    await airplay.pair_receiver('airplay:00:11:22:33:44:55@192.0.2.1')
                pairing.close.assert_awaited_once()
                store.save.assert_not_awaited()
                self.assertFalse(airplay.pin_path().exists())
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, XDG_RUNTIME_DIR=tmp):
            asyncio.run(run())

    def test_unpaired_receiver_fails_before_starting_stream_resources(self):
        async def run():
            config = mock.Mock()
            config.get_service.return_value.credentials = None
            args = mock.Mock(address='airplay:00:11:22:33:44:55@192.0.2.1')
            with mock.patch.object(airplay, 'storage', new=mock.AsyncMock()), \
                    mock.patch.object(airplay, 'select_receiver', new=mock.AsyncMock(return_value=config)), \
                    mock.patch('pyatv.connect') as connect, mock.patch.object(media, 'emit') as emit:
                self.assertEqual(await airplay.run_sender(args), 1)
                connect.assert_not_called()
                failures = [call for call in emit.call_args_list if call.args[0] == 'cast-transport-failed']
                self.assertIn('Pair', failures[0].kwargs['error'])
        asyncio.run(run())


if __name__ == '__main__':
    unittest.main()
