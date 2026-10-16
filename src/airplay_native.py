"""Session-owned native AirPlay mirroring helper integration."""
import asyncio
import contextlib
import json
import os
from pathlib import Path
import signal
import re
import time

import chromecast_transport as media


def helper_path():
    data = Path(os.environ.get('XDG_DATA_HOME') or Path.home() / '.local/share')
    return Path(os.environ.get('HOVENCAST_AIRPLAY_NATIVE', str(data / 'hovencast/native/bin/doubletake')))


def credentials_path():
    state = Path(os.environ.get('XDG_STATE_HOME') or Path.home() / '.local/state')
    return Path(os.environ.get('HOVENCAST_AIRPLAY_NATIVE_CREDENTIALS', str(state / 'hovencast/airplay/native-credentials.json')))


def available():
    binary = helper_path()
    return binary.is_file() and os.access(binary, os.X_OK)


def configuration(identifier):
    if not available():
        return None
    credentials = credentials_path()
    try:
        records = json.loads(credentials.read_text())
        if not isinstance(records.get(identifier), dict):
            return None
        if not all(records[identifier].get(key) for key in ('pairing_id', 'ed25519_seed', 'ed25519_public')):
            return None
    except (OSError, ValueError, AttributeError):
        return None
    return helper_path(), credentials


async def pair_receiver(address, pin_path):
    from airplay_transport import parse_address
    identifier, host = parse_address(address)
    credentials = credentials_path()
    credentials.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    credentials.parent.chmod(0o700)
    process = await asyncio.create_subprocess_exec(
        str(helper_path()), '-target', host, '-expected-device-id', identifier,
        '-creds', str(credentials), '-pair', '-pair-only',
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    read = None
    transcript = ''
    prompted = submitted = False
    deadline = time.monotonic() + 90
    try:
        while time.monotonic() < deadline:
            if read is None:
                read = asyncio.create_task(process.stdout.read(4096))
            done, _ = await asyncio.wait({read}, timeout=.1)
            if read in done:
                chunk = read.result()
                read = None
                if not chunk:
                    code = await process.wait()
                    if code != 0 or not configuration(identifier):
                        raise RuntimeError('Native Apple TV pairing failed. Retry with the current TV PIN.')
                    credentials.chmod(0o600)
                    return 0
                transcript = (transcript + chunk.decode(errors='replace'))[-8192:]
                if 'Enter the PIN shown on the receiver:' in transcript and not prompted:
                    print(json.dumps(dict(event='airplay-pin-required')), flush=True)
                    prompted = True
                if 'configured password' in transcript and not prompted:
                    raise RuntimeError('Set Apple TV AirPlay security to an on-screen code to pair in HovenCast.')
            if prompted and not submitted and pin_path.exists():
                value = json.loads(pin_path.read_text())
                pin_path.unlink(missing_ok=True)
                if value.get('address') != address:
                    continue
                pin = str(value.get('pin', ''))
                if not re.fullmatch(r'\d{4}', pin):
                    raise ValueError('Invalid Apple TV PIN')
                process.stdin.write((pin+'\n').encode())
                await process.stdin.drain()
                submitted = True
        raise RuntimeError('Apple TV pairing timed out. Select the TV to retry.')
    finally:
        if process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), 5)
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
        if read:
            read.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await read
        pin_path.unlink(missing_ok=True)


async def run_sender(args, host, config):
    binary, credentials = config
    stopped = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stopped.set)
    loop.add_signal_handler(signal.SIGUSR1, lambda: media.emit('video-trace-unavailable', protocol='airplay-native'))
    process = reader = None
    played = False
    last_line = ''
    try:
        process = await asyncio.create_subprocess_exec(
            str(binary), '-target', host, '-expected-device-id', args.address.split(':', 1)[1].split('@')[0], '-creds', str(credentials),
            '-port-range', '8766-8768', '-input-ts', args.input,
            '-video-codec', 'h264', '-hwaccel', 'none',
            stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT)
        deadline = time.monotonic() + 40
        while not stopped.is_set():
            if reader is None:
                reader = asyncio.create_task(process.stdout.readline())
            done, _ = await asyncio.wait({reader}, timeout=.2)
            if reader in done:
                line = reader.result()
                reader = None
                if not line:
                    code = await process.wait()
                    if code == 0 and played:
                        media.emit('cast-receiver-stopped', protocol='airplay-native')
                        return 0
                    raise RuntimeError(f'Native AirPlay ended (exit {code}): {last_line}')
                last_line = line.decode(errors='replace').strip()
                print(last_line, flush=True)
                if 'mirror session ready' in last_line:
                    media.emit('cast-control-ready', protocol='airplay-native', receiver=args.address)
                if 'HOVENCAST_NATIVE_VIDEO_READY' in last_line and not played:
                    played = True
                    media.emit('cast-playing', protocol='airplay-native', evidence='native-mirroring-video-sent')
                if 'audio capture failed' in last_line or 'audio streaming error' in last_line:
                    raise RuntimeError(last_line)
            if not played and time.monotonic() > deadline:
                raise RuntimeError('Native AirPlay did not start. Pair the Apple TV again or retry.')
        return 0
    except Exception as error:
        media.emit('cast-transport-failed', protocol='airplay-native', error=str(error))
        return 1
    finally:
        if process and process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), 5)
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
        if reader:
            reader.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await reader
        media.emit('cast-transport-stopped', protocol='airplay-native', played=played)
