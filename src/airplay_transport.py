"""Apple TV discovery, PIN pairing and session-owned AirPlay HLS streaming."""
from __future__ import annotations
import argparse
import asyncio
import contextlib
import fcntl
import ipaddress
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time

import chromecast_transport as media
import airplay_native


def parse_address(address):
    match = re.fullmatch(r'airplay:([0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5})@(.+)', address)
    if not match:
        raise ValueError('Invalid Apple TV address. Search again.')
    return match[1].upper(), str(ipaddress.IPv4Address(match[2]))


def state_path():
    return Path(os.environ.get('XDG_STATE_HOME') or Path.home() / '.local/state') / 'hovencast/airplay/credentials.json'


async def storage():
    from pyatv.storage.file_storage import FileStorage
    path = state_path()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.parent.chmod(0o700)
    if path.exists():
        path.chmod(0o600)
    result = FileStorage(str(path), asyncio.get_running_loop())
    await result.load()
    return result


async def select_receiver(address, store):
    import pyatv
    from pyatv.const import Protocol
    identifier, host = parse_address(address)
    devices = await pyatv.scan(asyncio.get_running_loop(), hosts=[host],
                               timeout=5, protocol=Protocol.AirPlay)
    for config in devices:
        service = config.get_service(Protocol.AirPlay)
        if (service and (service.identifier or '').upper() == identifier
                and service.properties.get('model', '').startswith('AppleTV')):
            settings = await store.get_settings(config)
            service.credentials = settings.protocols.airplay.credentials
            return config
    raise RuntimeError('The selected Apple TV changed or is unavailable. Search again.')


async def discover(timeout=4):
    import pyatv
    from pyatv.const import Protocol
    # Avahi sees sleep-proxy advertisements that pyatv multicast can miss.
    result = await asyncio.to_thread(subprocess.run,
        ['avahi-browse', '-r', '-t', '-p', '_airplay._tcp'],
        capture_output=True, text=True, timeout=10)
    hosts = set()
    for line in result.stdout.splitlines():
        fields = line.split(';', 9)
        if len(fields) == 10 and fields[0] == '=' and fields[2] == 'IPv4' and 'model=AppleTV' in fields[9]:
            with contextlib.suppress(ValueError):
                hosts.add(str(ipaddress.IPv4Address(fields[7])))
    if not hosts:
        return []
    store = await storage()
    devices = await pyatv.scan(asyncio.get_running_loop(), hosts=sorted(hosts),
                               timeout=timeout, protocol=Protocol.AirPlay)
    rows = []
    for config in devices:
        service = config.get_service(Protocol.AirPlay)
        model = service.properties.get('model', '') if service else ''
        if not service or not service.identifier or not model.startswith('AppleTV'):
            continue
        address = f'airplay:{service.identifier.upper()}@{config.address}'
        parse_address(address)
        settings = await store.get_settings(config)
        rows.append(dict(name=config.name, address=address, port=service.port,
                         protocol='airplay', interface='', model=model, vendor='Apple',
                         paired=bool(settings.protocols.airplay.credentials or airplay_native.configuration(service.identifier.upper())),
                         native_width=0, native_height=0))
    return sorted(rows, key=lambda row: (row['name'], row['address']))


def discover_receivers(timeout=4):
    return asyncio.run(discover(timeout))


def pin_path():
    root = os.environ.get('XDG_RUNTIME_DIR')
    if not root:
        raise RuntimeError('Apple TV pairing requires XDG_RUNTIME_DIR')
    folder = Path(root) / 'hovencast'
    folder.mkdir(mode=0o700, exist_ok=True)
    folder.chmod(0o700)
    return folder / 'airplay-pin.json'


def submit_pin(address, pin):
    parse_address(address)
    if not re.fullmatch(r'\d{4}', pin):
        raise ValueError('Enter the four-digit PIN shown on the Apple TV.')
    path = pin_path()
    temporary = path.with_suffix('.tmp')
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as file:
        json.dump(dict(address=address, pin=pin), file)
    os.replace(temporary, path)


async def pair_receiver(address):
    import pyatv
    from pyatv.const import Protocol
    path = pin_path()
    with path.with_suffix('.lock').open('w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        path.unlink(missing_ok=True)
        import airplay_native
        if airplay_native.available():
            return await airplay_native.pair_receiver(address, path)
        store = await storage()
        config = await select_receiver(address, store)
        pairing = await pyatv.pair(config, Protocol.AirPlay, asyncio.get_running_loop(), storage=store)
        try:
            await asyncio.wait_for(pairing.begin(), 15)
            print(json.dumps(dict(event='airplay-pin-required')), flush=True)
            deadline = time.monotonic() + 90
            while time.monotonic() < deadline:
                if path.exists():
                    value = json.loads(path.read_text())
                    path.unlink(missing_ok=True)
                    if value.get('address') != address:
                        continue
                    if not re.fullmatch(r'\d{4}', str(value.get('pin', ''))):
                        raise ValueError('Invalid Apple TV PIN')
                    pairing.pin(int(value['pin']))
                    await asyncio.wait_for(pairing.finish(), 15)
                    if not pairing.has_paired:
                        raise RuntimeError('Apple TV pairing failed. Retry with the PIN on the TV.')
                    # Create with private permissions before FileStorage writes secrets.
                    fd = os.open(state_path(), os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
                    os.close(fd)
                    await store.save()
                    state_path().chmod(0o600)
                    return 0
                await asyncio.sleep(.2)
            raise RuntimeError('Apple TV pairing timed out. Select the TV to retry.')
        finally:
            await pairing.close()
            path.unlink(missing_ok=True)


async def require_paired(address):
    import airplay_native
    if airplay_native.configuration(parse_address(address)[0]):
        return
    from pyatv.const import Protocol
    config = await select_receiver(address, await storage())
    if not config.get_service(Protocol.AirPlay).credentials:
        raise RuntimeError('Pair this Apple TV in HovenCast before connecting.')


def live_playlist(data):
    # FFmpeg rounds half-second segments down to TARGETDURATION:0.
    # HLS clients need a positive integer even with sub-second segments.
    return data.replace(b'#EXT-X-TARGETDURATION:0\n', b'#EXT-X-TARGETDURATION:1\n')


def hls_command(source, folder):
    return ['ffmpeg', '-hide_banner', '-loglevel', 'warning', '-nostdin', '-y',
            '-probesize', '1000000', '-analyzeduration', '1000000', '-i', str(source),
            '-map', '0:v:0', '-map', '0:a:0', '-c', 'copy', '-f', 'hls',
            # Keep the live window short: the Apple TV buffers this playlist.
            # Retain older files separately so in-flight requests still succeed.
            '-hls_time', '0.5', '-hls_list_size', '6', '-hls_delete_threshold', '4',
            '-hls_flags', 'delete_segments+independent_segments+temp_file',
            '-hls_segment_filename', str(folder / 'airplay%06d.ts'), str(folder / 'live.m3u8')]


async def run_sender(args):
    import airplay_native
    identifier, host = parse_address(args.address)
    native = airplay_native.configuration(identifier)
    if native:
        return await airplay_native.run_sender(args, host, native)
    import pyatv
    from pyatv.const import Protocol
    stopped = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stopped.set)
    loop.add_signal_handler(signal.SIGUSR1, lambda: media.emit('video-trace-unavailable', protocol='airplay'))
    atv = server = remux = playback = None
    played = False
    try:
        _, host = parse_address(args.address)
        store = await storage()
        config = await select_receiver(args.address, store)
        if not config.get_service(Protocol.AirPlay).credentials:
            raise RuntimeError('Pair this Apple TV in HovenCast before connecting.')
        from airplay_url import AirPlayUrlSession
        service = config.get_service(Protocol.AirPlay)
        atv = AirPlayUrlSession(host, service.port, service.credentials)
        await asyncio.wait_for(atv.connect(), 15)
        media.emit('cast-control-ready', protocol='airplay', receiver=args.address)
        folder = Path(args.folder)
        server = media.LiveServer(folder, media.local_address(host), host,
                                 playlist='live.m3u8', allowed=r'live\.m3u8|airplay\d{6,}\.ts',
                                 playlist_transform=live_playlist)
        server.start()
        remux = subprocess.Popen(hls_command(Path(args.input), folder))
        deadline = time.monotonic() + 25
        while not stopped.is_set():
            if remux.poll() is not None:
                raise RuntimeError('Apple TV stream packaging stopped unexpectedly')
            if (folder / 'live.m3u8').exists() and len(list(folder.glob('airplay*.ts'))) >= 3:
                break
            if time.monotonic() > deadline:
                raise RuntimeError('Apple TV video stream did not become ready')
            await asyncio.sleep(.1)
        if stopped.is_set():
            return 0
        playback = asyncio.create_task(atv.play(server.url))
        media.emit('cast-load-requested', protocol='airplay')
        deadline = time.monotonic() + 35
        last_fetch = time.monotonic()
        last_segments = 0
        while not stopped.is_set():
            if remux.poll() is not None:
                raise RuntimeError('Apple TV stream packaging stopped unexpectedly')
            if playback.done():
                await playback  # Propagate authentication/receiver errors.
                if not played:
                    raise RuntimeError('Apple TV ended playback before receiving the desktop stream.')
                media.emit('cast-receiver-stopped')
                return 0
            if server.segments != last_segments:
                last_segments = server.segments
                last_fetch = time.monotonic()
            if atv.played and server.segments >= 2 and not played:
                # Require the receiver's playing event as well as delivered video segments.
                media.emit('cast-playing', protocol='airplay', evidence='receiver-playing-and-segment-delivery', fetched_segments=server.segments)
                played = True
            if not played and time.monotonic() > deadline:
                raise RuntimeError('Apple TV cannot fetch the desktop stream. Run hovencast-airplay-setup to allow local streaming.')
            if played and time.monotonic() - last_fetch > 25:
                raise RuntimeError('Apple TV stopped requesting the desktop stream')
            await asyncio.sleep(.5)
        return 0
    except Exception as error:
        media.emit('cast-transport-failed', protocol='airplay', error=str(error), requests=server.requests if server else 0, fetched_segments=server.segments if server else 0)
        return 1
    finally:
        if playback:
            playback.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await playback
        if atv:
            with contextlib.suppress(Exception):
                await atv.close()
        if remux and remux.poll() is None:
            remux.terminate()
            try:
                await asyncio.to_thread(remux.wait, timeout=2)
            except subprocess.TimeoutExpired:
                remux.kill()
                await asyncio.to_thread(remux.wait)
        if server:
            await asyncio.to_thread(server.close)
        media.emit('cast-transport-stopped', protocol='airplay', played=played)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    for name in ('address', 'input', 'folder', 'events'):
        parser.add_argument('--' + name, required=True)
    args = parser.parse_args()
    media.EVENT_FILE = args.events
    sys.exit(asyncio.run(run_sender(args)))
