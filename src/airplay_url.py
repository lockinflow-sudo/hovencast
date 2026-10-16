"""Session-owned modern AirPlay video queue, using pyatv 0.18 authentication."""
import asyncio
import os
import json
from pyatv.auth.hap_pairing import parse_credentials
from pyatv.protocols.raop.protocols import StreamContext, TimingServer
from pyatv.support.http import http_connect
from pyatv.support.rtsp import RtspSession
from airplay_compat.stream import AirPlayV2


class AirPlayUrlSession:
    def __init__(self, host, port, credentials):
        self.host = host
        self.port = port
        self.credentials = credentials
        self.connection = None
        self.protocol = None
        self.rtsp = None
        self.timing_transport = None
        self.started = False
        self.played = False

    @property
    def playback_state(self):
        return self.protocol.playback_state if self.protocol else None

    async def connect(self):
        self.connection = await http_connect(self.host, self.port)
        if os.environ.get('HOVENCAST_AIRPLAY_DIAGNOSTICS'):
            original = self.connection.send_and_receive
            async def traced(method, uri, *args, **kwargs):
                print(json.dumps(dict(event='airplay-control-request', method=method, uri=uri, protocol=kwargs.get('protocol'))), flush=True)
                response = await original(method, uri, *args, **kwargs)
                print(json.dumps(dict(event='airplay-control-response', method=method, uri=uri, code=response.code)), flush=True)
                return response
            self.connection.send_and_receive = traced
        self.rtsp = RtspSession(self.connection)
        context = StreamContext()
        context.credentials = parse_credentials(self.credentials)
        self.protocol = AirPlayV2(context, self.rtsp)

    async def play(self, url):
        if not self.connection:
            await self.connect()
        self.timing_transport, timing = await asyncio.get_running_loop().create_datagram_endpoint(
            TimingServer, local_addr=(self.connection.local_ip, 8766))
        response = await self.protocol.play_url(timing.port, url)
        if not 200 <= response.code < 300:
            raise RuntimeError(f'Apple TV rejected the video queue (HTTP {response.code}).')
        self.started = True
        state = self.protocol.playback_state
        deadline = asyncio.get_running_loop().time() + 30
        while True:
            if state == 'playing':
                self.played = True
            elif self.played and state in ('idle', 'stopped', 'ended'):
                return
            elif state in ('failed', 'error'):
                raise RuntimeError('Apple TV reported a video playback error.')
            if not self.played and asyncio.get_running_loop().time() > deadline:
                raise RuntimeError('Apple TV did not start the desktop video stream.')
            state = await self.protocol.wait_for_playback_state_change(state, 1)

    async def close(self):
        try:
            if self.protocol:
                if self.started:
                    try:
                        await asyncio.wait_for(self.protocol._send_url_command({'type': 'setRate', 'rate': 0.0}), 2)
                    except Exception:
                        pass
                if self.rtsp and self.protocol.event_transport:
                    try:
                        await asyncio.wait_for(self.rtsp.exchange("TEARDOWN", allow_error=True), 2)
                    except Exception:
                        pass
        finally:
            if self.protocol:
                self.protocol.teardown()
            if self.timing_transport:
                self.timing_transport.close()
                self.timing_transport = None
            if self.connection:
                self.connection.close()
                self.connection = None
