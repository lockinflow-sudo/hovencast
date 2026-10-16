import asyncio
import plistlib
from unittest import IsolatedAsyncioTestCase, mock
import airplay_url
from airplay_compat.stream import AirPlayV2
from pyatv.protocols.raop.protocols import StreamContext


class AirPlayUrlTests(IsolatedAsyncioTestCase):
    async def test_url_queue_contains_session_specific_stream_and_media_url(self):
        rtsp = mock.Mock(exchange=mock.AsyncMock(return_value=mock.Mock(code=200)))
        stream = AirPlayV2(StreamContext(), rtsp)
        stream._stream_id = 37
        response = await stream._send_url_command({'type': 'insertPlayQueueItem', 'item': {'Content-Location': 'http://192.0.2.2/live.m3u8'}})
        call = rtsp.exchange.call_args
        self.assertEqual(call.args, ('POST',))
        self.assertEqual(call.kwargs['uri'], '/command')
        self.assertEqual(call.kwargs['headers']['X-Apple-StreamID'], 37)
        payload = plistlib.loads(call.kwargs['body']['params']['data'])
        self.assertEqual(payload['item']['Content-Location'], 'http://192.0.2.2/live.m3u8')
        self.assertEqual(response.code, 200)

    async def test_waits_for_receiver_playing_before_accepting_stopped(self):
        states = iter(['playing', 'stopped'])
        protocol = mock.Mock(play_url=mock.AsyncMock(return_value=mock.Mock(code=200)),
                             wait_for_playback_state_change=mock.AsyncMock(side_effect=lambda *args: next(states)),
                             playback_state='idle')
        transport = mock.Mock()
        session = airplay_url.AirPlayUrlSession('192.0.2.1', 7000, 'private')
        session.connection = mock.Mock(local_ip='192.0.2.2')
        session.protocol = protocol
        loop = asyncio.get_running_loop()
        with mock.patch.object(loop, 'create_datagram_endpoint', new=mock.AsyncMock(return_value=(transport, mock.Mock(port=8766)))):
            await session.play('http://192.0.2.2/live.m3u8')
        self.assertTrue(session.played)
        self.assertEqual(protocol.wait_for_playback_state_change.await_count, 2)

    async def test_rejected_queue_does_not_claim_playback_and_closes_resources(self):
        protocol = mock.Mock(play_url=mock.AsyncMock(return_value=mock.Mock(code=500)),
                             event_transport=mock.Mock(), _send_url_command=mock.AsyncMock())
        connection = mock.Mock(local_ip='192.0.2.2')
        transport = mock.Mock()
        session = airplay_url.AirPlayUrlSession('192.0.2.1', 7000, 'private')
        session.connection = connection
        session.protocol = protocol
        session.rtsp = mock.Mock(exchange=mock.AsyncMock())
        loop = asyncio.get_running_loop()
        with mock.patch.object(loop, 'create_datagram_endpoint', new=mock.AsyncMock(return_value=(transport, mock.Mock(port=8766)))):
            with self.assertRaisesRegex(RuntimeError, 'rejected'):
                await session.play('http://192.0.2.2/live.m3u8')
        await session.close()
        self.assertFalse(session.played)
        connection.close.assert_called_once()
        transport.close.assert_called_once()
        protocol.teardown.assert_called_once()
        protocol._send_url_command.assert_not_awaited()

    async def test_stop_is_scoped_to_queue_and_tears_down_timing_and_event_channels(self):
        session = airplay_url.AirPlayUrlSession('192.0.2.1', 7000, 'private')
        session.started = True
        session.connection = mock.Mock()
        connection = session.connection
        session.timing_transport = mock.Mock()
        timing = session.timing_transport
        session.rtsp = mock.Mock(exchange=mock.AsyncMock())
        session.protocol = mock.Mock(_send_url_command=mock.AsyncMock(side_effect=RuntimeError('disconnected')))
        await session.close()
        session.protocol._send_url_command.assert_awaited_once_with({'type': 'setRate', 'rate': 0.0})
        session.rtsp.exchange.assert_awaited_once_with('TEARDOWN', allow_error=True)
        session.protocol.teardown.assert_called_once()
        connection.close.assert_called_once()
        timing.close.assert_called_once()
        self.assertIsNone(session.connection)

    async def test_playback_events_do_not_accept_unrelated_types(self):
        stream = AirPlayV2(StreamContext(), mock.Mock())
        stream._handle_event({'type': 'irrelevant', 'name': 'playing'})
        self.assertIsNone(stream.playback_state)
        stream._handle_event({'type': 'playbackState', 'params': {'playbackState': 'Playing'}})
        self.assertEqual(stream.playback_state, 'playing')
        stream._handle_event({'type': 'playbackState', 'name': 'Stopped'})
        self.assertEqual(stream.playback_state, 'stopped')
