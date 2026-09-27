#!/usr/bin/python

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from typing import Any, Callable

import gi

gi.require_version("Gst", "1.0")
gi.require_version("Xdp", "1.0")
gi.require_version("GLibUnix", "2.0")
from gi.repository import Gio, GLib, GLibUnix, Gst, Xdp  # noqa: E402

from omarchy_cast_protocol import (
    VideoMode,
    choose_video_mode,
    discover_mice_receivers,
    enrich_receiver_details,
    probe_wfd_capabilities,
)

APP_ROOT = pathlib.Path(__file__).resolve().parents[1]
APP_VERSION = json.loads(
    (APP_ROOT / "manifest.json").read_text(encoding="utf-8")
)["version"]


def runtime_root(required: bool = False) -> pathlib.Path | None:
    value = os.environ.get("XDG_RUNTIME_DIR", "").strip()
    if not value:
        if required:
            raise RuntimeError("OmaCast requires XDG_RUNTIME_DIR")
        return None
    root = pathlib.Path(value) / "omacast"
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    root.chmod(0o700)
    return root


def audio_route_state_path() -> pathlib.Path | None:
    root = runtime_root()
    return root / "audio-route.json" if root else None


def process_is_running(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def emit(event_file: Any, event: str, **fields: Any) -> None:
    record = {"time": time.time(), "event": event, **fields}
    line = json.dumps(record, sort_keys=True)
    print(line, flush=True)
    if event_file:
        event_file.write(line + "\n")
        event_file.flush()


def pulse_default_monitor() -> str:
    result = subprocess.run(
        ["pactl", "get-default-sink"],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    )
    return result.stdout.strip() + ".monitor"


def picker_source_info() -> dict[str, Any]:
    runtime_dir = os.environ.get("XDG_RUNTIME_DIR", "").strip()
    if not runtime_dir:
        return {"kind": "unknown"}
    selection_file = pathlib.Path(runtime_dir) / "omacast-picker" / "source-selection"
    try:
        raw = selection_file.read_text(encoding="utf-8").strip()
    except OSError:
        return {"kind": "unknown"}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        parsed = {"kind": raw}
    if not isinstance(parsed, dict):
        return {"kind": "unknown"}
    kind = str(parsed.get("kind", "unknown"))
    parsed["kind"] = kind if kind in {"screen", "window", "region"} else "unknown"
    return parsed


@dataclass(frozen=True)
class WindowCrop:
    left: int
    right: int
    top: int
    bottom: int
    width: int
    height: int


def normalize_window_address(value: Any) -> int | None:
    text = str(value or "").strip().lower()
    if not text:
        return None
    try:
        return int(text, 16 if text.startswith("0x") else 10)
    except ValueError:
        return None


def window_crop_geometry(window_address: str, output: str) -> WindowCrop:
    clients_result = subprocess.run(
        ["hyprctl", "-j", "clients"],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    )
    monitors_result = subprocess.run(
        ["hyprctl", "-j", "monitors"],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    )
    clients = json.loads(clients_result.stdout)
    monitors = json.loads(monitors_result.stdout)
    selected_address = normalize_window_address(window_address)
    if selected_address is None:
        raise RuntimeError("The selected window address is invalid.")
    client = next(
        (
            item
            for item in clients
            if normalize_window_address(item.get("address")) == selected_address
        ),
        None,
    )
    monitor = next((item for item in monitors if item.get("name") == output), None)
    if client is None:
        raise RuntimeError("The selected window is no longer open.")
    if monitor is None:
        raise RuntimeError("The selected display is no longer available.")
    scale = float(monitor.get("scale", 1.0)) or 1.0
    monitor_width = int(monitor.get("width", 0))
    monitor_height = int(monitor.get("height", 0))
    monitor_x = int(monitor.get("x", 0))
    monitor_y = int(monitor.get("y", 0))
    client_at = client.get("at", [0, 0])
    client_size = client.get("size", [0, 0])
    left = round((int(client_at[0]) - monitor_x) * scale)
    top = round((int(client_at[1]) - monitor_y) * scale)
    width = round(int(client_size[0]) * scale)
    height = round(int(client_size[1]) * scale)
    left = max(0, min(left, monitor_width - 1))
    top = max(0, min(top, monitor_height - 1))
    width = max(1, min(width, monitor_width - left))
    height = max(1, min(height, monitor_height - top))
    return WindowCrop(
        left=left,
        right=max(0, monitor_width - left - width),
        top=top,
        bottom=max(0, monitor_height - top - height),
        width=width,
        height=height,
    )


class SilentAudioRoute:
    def __init__(self, event_file: Any) -> None:
        self.event_file = event_file
        self.sink_name = f"omarchy_cast_{os.getpid()}"
        self.previous_sink: str | None = None
        self.module_id: int | None = None

    def _save_recovery_state(self) -> None:
        path = audio_route_state_path()
        if path is None or not self.previous_sink or self.module_id is None:
            return
        payload = {
            "pid": os.getpid(),
            "previous_sink": self.previous_sink,
            "sink_name": self.sink_name,
            "module_id": self.module_id,
        }
        temporary = path.with_name(f".{path.name}.{os.getpid()}")
        temporary.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")
        temporary.chmod(0o600)
        os.replace(temporary, path)

    def start(self) -> str:
        self.previous_sink = subprocess.run(
            ["pactl", "get-default-sink"],
            check=True,
            text=True,
            stdout=subprocess.PIPE,
        ).stdout.strip()
        result = subprocess.run(
            [
                "pactl",
                "load-module",
                "module-null-sink",
                f"sink_name={self.sink_name}",
                "sink_properties=device.description=Omarchy Cast",
                "rate=48000",
                "channels=2",
            ],
            check=True,
            text=True,
            stdout=subprocess.PIPE,
        )
        self.module_id = int(result.stdout.strip())
        self._save_recovery_state()
        try:
            subprocess.run(["pactl", "set-default-sink", self.sink_name], check=True)
            self._move_inputs(self.sink_name)
        except (OSError, ValueError, subprocess.SubprocessError):
            self.stop()
            raise
        emit(
            self.event_file,
            "audio-route",
            mode="cast-only",
            cast_sink=self.sink_name,
            previous_sink=self.previous_sink,
        )
        return self.sink_name + ".monitor"

    def _sink_inputs(self) -> list[tuple[str, str]]:
        result = subprocess.run(
            ["pactl", "list", "short", "sink-inputs"],
            check=True,
            text=True,
            stdout=subprocess.PIPE,
        )
        inputs = []
        for line in result.stdout.splitlines():
            fields = line.split()
            if len(fields) >= 2:
                inputs.append((fields[0], fields[1]))
        return inputs

    def _move_inputs(self, destination: str, only_from: str | None = None) -> None:
        for input_id, sink_id in self._sink_inputs():
            if only_from is not None and sink_id != only_from:
                continue
            subprocess.run(
                ["pactl", "move-sink-input", input_id, destination],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

    def stop(self, announce: bool = True) -> None:
        if self.module_id is None:
            return
        cast_sink_id = None
        result = subprocess.run(
            ["pactl", "list", "short", "sinks"],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
        )
        for line in result.stdout.splitlines():
            fields = line.split()
            if len(fields) >= 2 and fields[1] == self.sink_name:
                cast_sink_id = fields[0]
                break
        if self.previous_sink:
            subprocess.run(
                ["pactl", "set-default-sink", self.previous_sink],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            if cast_sink_id:
                self._move_inputs(self.previous_sink, only_from=cast_sink_id)
        subprocess.run(
            ["pactl", "unload-module", str(self.module_id)],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if announce:
            emit(
                self.event_file,
                "audio-route",
                mode="restored",
                restored_sink=self.previous_sink,
            )
        self.module_id = None
        state_path = audio_route_state_path()
        if state_path:
            state_path.unlink(missing_ok=True)


def recover_orphan_audio_route() -> None:
    state_path = audio_route_state_path()
    if state_path is None or not state_path.is_file():
        return
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
        pid = int(state["pid"])
        if process_is_running(pid):
            return
        route = SilentAudioRoute(None)
        route.previous_sink = str(state["previous_sink"])
        route.sink_name = str(state["sink_name"])
        route.module_id = int(state["module_id"])
        route.stop(announce=False)
        print("Recovered audio routing left by an interrupted OmaCast session", file=sys.stderr)
    except (KeyError, TypeError, ValueError, json.JSONDecodeError, OSError, subprocess.SubprocessError) as error:
        print(f"Could not recover interrupted OmaCast audio routing: {error}", file=sys.stderr)


@dataclass
class Counters:
    capture_frames: int = 0
    capture_pts_ns: int | None = None
    first_capture_monotonic: float | None = None
    last_capture_monotonic: float | None = None
    video_frames: int = 0
    video_keyframes: int = 0
    video_bytes: int = 0
    audio_buffers: int = 0
    audio_bytes: int = 0
    video_pts_ns: int | None = None
    audio_pts_ns: int | None = None
    first_video_pts_ns: int | None = None
    first_audio_pts_ns: int | None = None
    first_video_monotonic: float | None = None
    first_audio_monotonic: float | None = None
    dropped_buffers: int = 0


class Recorder:
    def __init__(
        self,
        output: pathlib.Path,
        duration: int,
        event_file: Any,
        test_tone: bool,
        video_profile: str = "baseline",
        sender_start: Callable[
            [], tuple[subprocess.Popen[bytes], list[subprocess.Popen[bytes]]]
        ]
        | None = None,
        mute_local_audio: bool = False,
        mirror_output: pathlib.Path | None = None,
        video_mode: VideoMode | None = None,
    ) -> None:
        self.output = output
        self.duration = duration
        self.event_file = event_file
        self.portal = Xdp.Portal.new()
        self.session: Xdp.Session | None = None
        self.pipeline: Gst.Pipeline | None = None
        self.loop = GLib.MainLoop()
        self.cancellable = Gio.Cancellable()
        self.started_monotonic = time.monotonic()
        self.counters = Counters()
        self.previous_bytes = 0
        self.output_stall_ticks = 0
        self.previous_capture_frames = 0
        self.previous_video_frames = 0
        self.window_capture_stall_ticks = 0
        self.window_video_stall_ticks = 0
        self.window_video_recovery_attempts = 0
        self.source_kind = "unknown"
        self.window_address = ""
        self.window_output = ""
        self.window_crop: WindowCrop | None = None
        self.capture_caps: str | None = None
        self.last_capture_caps_change: float | None = None
        self.test_tone = test_tone
        self.video_profile = video_profile
        self.video_mode = video_mode
        self.sender_start = sender_start
        self.sender_process: subprocess.Popen[bytes] | None = None
        self.auxiliary_processes: list[subprocess.Popen[bytes]] = []
        self.audio_route = SilentAudioRoute(event_file) if mute_local_audio else None
        self.mirror_output = mirror_output
        self.tone_process: subprocess.Popen[bytes] | None = None
        self.exit_code = 1
        self.stopping = False

    def start(self) -> int:
        emit(self.event_file, "portal-requested", source="monitor", cursor="embedded")
        self.portal.create_screencast_session(
            Xdp.OutputType.MONITOR,
            Xdp.ScreencastFlags.NONE,
            Xdp.CursorMode.EMBEDDED,
            Xdp.PersistMode.NONE,
            None,
            self.cancellable,
            self._portal_created,
            None,
        )
        GLibUnix.signal_add(GLib.PRIORITY_DEFAULT, signal.SIGINT, self._interrupted)
        GLibUnix.signal_add(GLib.PRIORITY_DEFAULT, signal.SIGTERM, self._interrupted)
        self.loop.run()
        return self.exit_code

    def _portal_created(self, portal: Xdp.Portal, result: Gio.AsyncResult, _data: Any) -> None:
        try:
            self.session = portal.create_screencast_session_finish(result)
        except GLib.Error as error:
            self._fail("portal-create-failed", error)
            return
        self.session.connect("closed", self._portal_closed)
        self.session.start(None, self.cancellable, self._portal_started, None)

    def _portal_started(self, session: Xdp.Session, result: Gio.AsyncResult, _data: Any) -> None:
        try:
            if not session.start_finish(result):
                raise RuntimeError("portal did not start the session")
            streams = session.get_streams()
            stream_list = list(streams)
            if len(stream_list) != 1:
                raise RuntimeError(f"expected one monitor stream, got {len(stream_list)}")
            node_id = int(stream_list[0][0])
            pipewire_fd = session.open_pipewire_remote()
            source_info = picker_source_info()
            self.source_kind = str(source_info.get("kind", "unknown"))
            self.window_address = str(source_info.get("windowAddress", ""))
            self.window_output = str(source_info.get("output", ""))
            emit(
                self.event_file,
                "portal-started",
                node_id=node_id,
                source_kind=self.source_kind,
                window_address=self.window_address or None,
                window_output=self.window_output or None,
            )
            self._start_pipeline(pipewire_fd, node_id)
        except (GLib.Error, RuntimeError) as error:
            self._fail("portal-start-failed", error)

    def _start_pipeline(self, pipewire_fd: int, node_id: int) -> None:
        try:
            audio_source = self.audio_route.start() if self.audio_route else pulse_default_monitor()
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            self._fail("audio-route-failed", error)
            return
        audio_device = audio_source.replace("\\", "\\\\").replace('"', '\\"')
        output = str(self.output).replace("\\", "\\\\").replace('"', '\\"')
        if self.mirror_output:
            mirror = str(self.mirror_output).replace("\\", "\\\\").replace('"', '\\"')
            sink_description = f"""
                mpegtsmux name=mux alignment=7 pat-interval=9000 pmt-interval=9000
                    ! tee name=transport
                transport. ! queue ! filesink location="{output}" sync=false
                transport. ! queue ! filesink location="{mirror}" sync=false
            """
        else:
            sink_description = f"""
                mpegtsmux name=mux alignment=7 pat-interval=9000 pmt-interval=9000
                    ! filesink location="{output}" sync=false
            """
        if self.video_profile not in {"baseline", "high"}:
            self._fail("pipeline-create-failed", RuntimeError(f"unsupported H.264 profile: {self.video_profile}"))
            return
        mode = self.video_mode or VideoMode(
            "1080p", 1920, 1080, 6000, "ultrafast", "00000080", 500_000_000, 600
        )
        if mode.encoder_preset not in {"ultrafast", "superfast", "veryfast"}:
            self._fail(
                "pipeline-create-failed",
                RuntimeError(f"unsupported encoder preset: {mode.encoder_preset}"),
            )
            return
        if self.sender_start:
            try:
                self.sender_process, self.auxiliary_processes = self.sender_start()
                emit(self.event_file, "sender-started", pid=self.sender_process.pid)
                for process in self.auxiliary_processes:
                    emit(
                        self.event_file,
                        "transport-process-started",
                        pid=process.pid,
                    )
            except (OSError, subprocess.SubprocessError) as error:
                self._fail("sender-start-failed", error)
                return
        window_crop_description = ""
        if self.source_kind == "window":
            if not self.window_address or not self.window_output:
                self._fail(
                    "pipeline-create-failed",
                    RuntimeError("the selected window metadata is unavailable"),
                )
                return
            try:
                self.window_crop = window_crop_geometry(
                    self.window_address, self.window_output
                )
            except (OSError, ValueError, json.JSONDecodeError, subprocess.SubprocessError, RuntimeError) as error:
                self._fail("pipeline-create-failed", error)
                return
            window_crop_description = f"""
                ! videocrop name=window_crop left={self.window_crop.left} right={self.window_crop.right} top={self.window_crop.top} bottom={self.window_crop.bottom}
            """
        description = f"""
            pipewiresrc fd={pipewire_fd} path={node_id} do-timestamp=true keepalive-time=33 name=portal_video
                ! queue name=video_capture_queue max-size-buffers=3 leaky=downstream
                {window_crop_description}
                ! videoconvert name=video_convert
                ! videoscale name=video_scale add-borders=true
                ! videorate name=video_rate
                ! capsfilter name=video_output_caps caps-change-mode=delayed caps="video/x-raw,format=I420,width={mode.width},height={mode.height},framerate=30/1,pixel-aspect-ratio=1/1"
                ! queue name=video_encode_queue max-size-time={mode.queue_time_ns} leaky=downstream
                ! x264enc name=video_encoder tune=zerolatency speed-preset={mode.encoder_preset} option-string=level=4.0 cabac=true dct8x8=true bitrate={mode.bitrate_kbps} vbv-buf-capacity={mode.vbv_buffer_ms} key-int-max=30 bframes=0 byte-stream=true aud=true
                ! video/x-h264,profile={self.video_profile},stream-format=byte-stream,alignment=au
                ! h264parse name=encoded_video config-interval=-1
                ! queue name=video_mux_queue max-size-time={mode.queue_time_ns}
                ! mux.sink_256
            pulsesrc device="{audio_device}" do-timestamp=true name=desktop_audio
                ! queue max-size-time={mode.queue_time_ns} leaky=downstream
                ! audioconvert
                ! audioresample
                ! audio/x-raw,format=S16LE,rate=48000,channels=2
                ! fdkaacenc bitrate=192000 afterburner=true
                ! audio/mpeg,mpegversion=4,stream-format=raw
                ! aacparse name=encoded_audio
                ! queue max-size-time={mode.queue_time_ns}
                ! mux.sink_257
            {sink_description}
        """
        try:
            parsed = Gst.parse_launch(description)
            if not isinstance(parsed, Gst.Pipeline):
                raise RuntimeError("GStreamer did not create a pipeline")
            self.pipeline = parsed
            self._add_probe("portal_video", self._capture_probe)
            self._add_probe(
                "portal_video",
                self._capture_event_probe,
                Gst.PadProbeType.EVENT_DOWNSTREAM,
            )
            self._add_probe("encoded_video", self._video_probe)
            self._add_probe("encoded_audio", self._audio_probe)
            bus = self.pipeline.get_bus()
            bus.add_signal_watch()
            bus.connect("message", self._bus_message)
            result = self.pipeline.set_state(Gst.State.PLAYING)
            if result == Gst.StateChangeReturn.FAILURE:
                raise RuntimeError("GStreamer refused PLAYING state")
            emit(
                self.event_file,
                "pipeline-started",
                output=str(self.output),
                video=(
                    f"H.264 {self.video_profile} {mode.width}x{mode.height}p30 "
                    f"{mode.bitrate_kbps}kbps {mode.encoder_preset}"
                ),
                video_mode=mode.name,
                wfd_cea_bitmap=mode.cea_bitmap,
                audio="AAC-LC stereo 48kHz 192kbps",
                audio_source=audio_device,
                mirror_output=str(self.mirror_output) if self.mirror_output else None,
                source_kind=self.source_kind,
                resize_recovery=self.source_kind == "window",
                window_crop=(
                    {
                        "left": self.window_crop.left,
                        "right": self.window_crop.right,
                        "top": self.window_crop.top,
                        "bottom": self.window_crop.bottom,
                        "width": self.window_crop.width,
                        "height": self.window_crop.height,
                    }
                    if self.window_crop
                    else None
                ),
            )
            if self.test_tone:
                self.tone_process = subprocess.Popen(
                    [
                        "gst-launch-1.0",
                        "-q",
                        "audiotestsrc",
                        "wave=sine",
                        "freq=440",
                        "volume=0.05",
                        "!",
                        "audioconvert",
                        "!",
                        "audioresample",
                        "!",
                        "pulsesink",
                    ],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                emit(self.event_file, "test-tone-started", frequency_hz=440)
            GLib.timeout_add_seconds(1, self._metrics)
            GLib.timeout_add(250, self._check_sender)
            if self.source_kind == "window":
                GLib.timeout_add(200, self._update_window_crop)
            if self.duration > 0:
                GLib.timeout_add_seconds(self.duration, self._finish_duration)
        except (GLib.Error, RuntimeError, subprocess.SubprocessError) as error:
            self._fail("pipeline-create-failed", error)

    def _add_probe(
        self,
        element_name: str,
        callback: Any,
        probe_type: Gst.PadProbeType = Gst.PadProbeType.BUFFER,
    ) -> None:
        assert self.pipeline is not None
        element = self.pipeline.get_by_name(element_name)
        if element is None:
            raise RuntimeError(f"missing pipeline element {element_name}")
        pad = element.get_static_pad("src")
        if pad is None:
            raise RuntimeError(f"missing source pad on {element_name}")
        pad.add_probe(probe_type, callback)

    def _capture_event_probe(
        self, _pad: Gst.Pad, info: Gst.PadProbeInfo
    ) -> Gst.PadProbeReturn:
        event = info.get_event()
        if event and event.type == Gst.EventType.CAPS:
            caps = event.parse_caps().to_string()
            previous = self.capture_caps
            if previous != caps:
                self.capture_caps = caps
                if previous is not None:
                    self.last_capture_caps_change = time.monotonic()
                emit(
                    self.event_file,
                    "capture-caps",
                    source_kind=self.source_kind,
                    changed=previous is not None,
                    caps=caps,
                )
        return Gst.PadProbeReturn.OK

    def _video_probe(self, _pad: Gst.Pad, info: Gst.PadProbeInfo) -> Gst.PadProbeReturn:
        buffer = info.get_buffer()
        if buffer:
            self.counters.video_frames += 1
            self.counters.video_bytes += buffer.get_size()
            if not buffer.has_flags(Gst.BufferFlags.DELTA_UNIT):
                self.counters.video_keyframes += 1
            if buffer.pts != Gst.CLOCK_TIME_NONE:
                self.counters.video_pts_ns = int(buffer.pts)
                if self.counters.first_video_pts_ns is None:
                    self.counters.first_video_pts_ns = int(buffer.pts)
            if self.counters.first_video_monotonic is None:
                self.counters.first_video_monotonic = time.monotonic()
                emit(self.event_file, "first-video", latency_ms=self._latency_ms())
        return Gst.PadProbeReturn.OK

    def _capture_probe(self, _pad: Gst.Pad, info: Gst.PadProbeInfo) -> Gst.PadProbeReturn:
        buffer = info.get_buffer()
        if buffer:
            now = time.monotonic()
            self.counters.capture_frames += 1
            self.counters.last_capture_monotonic = now
            if buffer.pts != Gst.CLOCK_TIME_NONE:
                self.counters.capture_pts_ns = int(buffer.pts)
            if self.counters.first_capture_monotonic is None:
                self.counters.first_capture_monotonic = now
                emit(self.event_file, "first-capture", latency_ms=self._latency_ms())
        return Gst.PadProbeReturn.OK

    def _audio_probe(self, _pad: Gst.Pad, info: Gst.PadProbeInfo) -> Gst.PadProbeReturn:
        buffer = info.get_buffer()
        if buffer:
            self.counters.audio_buffers += 1
            self.counters.audio_bytes += buffer.get_size()
            if buffer.pts != Gst.CLOCK_TIME_NONE:
                self.counters.audio_pts_ns = int(buffer.pts)
                if self.counters.first_audio_pts_ns is None:
                    self.counters.first_audio_pts_ns = int(buffer.pts)
            if self.counters.first_audio_monotonic is None:
                self.counters.first_audio_monotonic = time.monotonic()
                emit(self.event_file, "first-audio", latency_ms=self._latency_ms())
        return Gst.PadProbeReturn.OK

    def _latency_ms(self) -> int:
        return round((time.monotonic() - self.started_monotonic) * 1000)

    def _update_window_crop(self) -> bool:
        if self.stopping or self.source_kind != "window" or not self.pipeline:
            return GLib.SOURCE_REMOVE
        try:
            geometry = window_crop_geometry(self.window_address, self.window_output)
        except (OSError, ValueError, json.JSONDecodeError, subprocess.SubprocessError, RuntimeError) as error:
            emit(self.event_file, "window-geometry-unavailable", error=str(error))
            return GLib.SOURCE_CONTINUE
        if geometry == self.window_crop:
            return GLib.SOURCE_CONTINUE
        crop = self.pipeline.get_by_name("window_crop")
        if crop is None:
            self._fail("window-crop-missing", RuntimeError("missing window crop element"))
            return GLib.SOURCE_REMOVE
        # Expand first, then trim, so a transition never briefly creates an
        # invalid crop rectangle while individual properties are changing.
        crop.set_property("left", 0)
        crop.set_property("right", 0)
        crop.set_property("top", 0)
        crop.set_property("bottom", 0)
        crop.set_property("left", geometry.left)
        crop.set_property("right", geometry.right)
        crop.set_property("top", geometry.top)
        crop.set_property("bottom", geometry.bottom)
        previous = self.window_crop
        self.window_crop = geometry
        emit(
            self.event_file,
            "window-crop-updated",
            previous=(
                {
                    "left": previous.left,
                    "right": previous.right,
                    "top": previous.top,
                    "bottom": previous.bottom,
                    "width": previous.width,
                    "height": previous.height,
                }
                if previous
                else None
            ),
            current={
                "left": geometry.left,
                "right": geometry.right,
                "top": geometry.top,
                "bottom": geometry.bottom,
                "width": geometry.width,
                "height": geometry.height,
            },
        )
        return GLib.SOURCE_CONTINUE

    def _restart_window_video_branch(self) -> bool:
        if not self.pipeline:
            return False
        names = (
            "video_capture_queue",
            "video_convert",
            "video_scale",
            "video_rate",
            "video_output_caps",
            "video_encode_queue",
            "video_encoder",
            "encoded_video",
            "video_mux_queue",
        )
        elements = [self.pipeline.get_by_name(name) for name in names]
        if any(element is None for element in elements):
            return False
        emit(
            self.event_file,
            "window-video-recovery-started",
            attempt=self.window_video_recovery_attempts + 1,
            caps=self.capture_caps,
        )
        for element in reversed(elements):
            assert element is not None
            element.set_state(Gst.State.READY)
        recovered = True
        for element in elements:
            assert element is not None
            recovered = element.sync_state_with_parent() and recovered
        self.window_video_recovery_attempts += 1
        emit(
            self.event_file,
            "window-video-recovery-completed",
            attempt=self.window_video_recovery_attempts,
            recovered=recovered,
        )
        return recovered

    def _metrics(self) -> bool:
        if self.stopping:
            return GLib.SOURCE_REMOVE
        total_bytes = self.counters.video_bytes + self.counters.audio_bytes
        bitrate = (total_bytes - self.previous_bytes) * 8
        self.previous_bytes = total_bytes
        drift_ms = None
        capture_idle_ms = None
        if self.counters.last_capture_monotonic is not None:
            capture_idle_ms = round(
                (time.monotonic() - self.counters.last_capture_monotonic) * 1000
            )
        if (
            self.counters.video_pts_ns is not None
            and self.counters.audio_pts_ns is not None
            and self.counters.first_video_pts_ns is not None
            and self.counters.first_audio_pts_ns is not None
        ):
            video_elapsed = self.counters.video_pts_ns - self.counters.first_video_pts_ns
            audio_elapsed = self.counters.audio_pts_ns - self.counters.first_audio_pts_ns
            drift_ms = round((video_elapsed - audio_elapsed) / 1_000_000, 3)
        emit(
            self.event_file,
            "metrics",
            capture_frames=self.counters.capture_frames,
            capture_idle_ms=capture_idle_ms,
            video_frames=self.counters.video_frames,
            video_keyframes=self.counters.video_keyframes,
            audio_buffers=self.counters.audio_buffers,
            bitrate_bps=bitrate,
            av_drift_ms=drift_ms,
            dropped_buffers=self.counters.dropped_buffers,
        )
        capture_is_live = capture_idle_ms is not None and capture_idle_ms < 1_000
        output_was_started = (
            self.counters.video_frames > 0 and self.counters.audio_buffers > 0
        )
        if self.source_kind == "window" and output_was_started:
            if capture_is_live:
                self.window_capture_stall_ticks = 0
            else:
                self.window_capture_stall_ticks += 1
                if self.window_capture_stall_ticks >= 8:
                    self.exit_code = 1
                    emit(
                        self.event_file,
                        "output-stalled",
                        error="The display capture behind the selected window stopped.",
                        cause="window-monitor-source",
                        stall_seconds=self.window_capture_stall_ticks,
                        capture_frames=self.counters.capture_frames,
                        video_frames=self.counters.video_frames,
                        audio_buffers=self.counters.audio_buffers,
                    )
                    self._stop("window-monitor-source-stalled")
                    return GLib.SOURCE_REMOVE
        capture_advanced = self.counters.capture_frames > self.previous_capture_frames
        video_advanced = self.counters.video_frames > self.previous_video_frames
        self.previous_capture_frames = self.counters.capture_frames
        self.previous_video_frames = self.counters.video_frames
        if (
            self.source_kind == "window"
            and capture_is_live
            and output_was_started
            and capture_advanced
            and not video_advanced
        ):
            self.window_video_stall_ticks += 1
            if (
                self.window_video_stall_ticks >= 2
                and self.window_video_recovery_attempts < 1
            ):
                if self._restart_window_video_branch():
                    self.window_video_stall_ticks = 0
                    self.output_stall_ticks = 0
                    return GLib.SOURCE_CONTINUE
        else:
            self.window_video_stall_ticks = 0
        if self.sender_process and capture_is_live and output_was_started:
            self.output_stall_ticks = self.output_stall_ticks + 1 if bitrate == 0 else 0
            if self.output_stall_ticks >= 8:
                self.exit_code = 1
                window_capture_failed = (
                    self.source_kind == "window" and self.window_video_stall_ticks > 0
                )
                error = (
                    "Window capture stopped after the window changed size."
                    if window_capture_failed
                    else "The TV stopped accepting the screen stream."
                )
                emit(
                    self.event_file,
                    "output-stalled",
                    error=error,
                    cause="window-capture" if window_capture_failed else "receiver-output",
                    stall_seconds=self.output_stall_ticks,
                    capture_frames=self.counters.capture_frames,
                    video_frames=self.counters.video_frames,
                    audio_buffers=self.counters.audio_buffers,
                )
                self._stop("output-stalled")
                return GLib.SOURCE_REMOVE
        else:
            self.output_stall_ticks = 0
        return GLib.SOURCE_CONTINUE

    def _finish_duration(self) -> bool:
        if self.pipeline and not self.stopping:
            emit(self.event_file, "duration-reached", duration_seconds=self.duration)
            self.pipeline.send_event(Gst.Event.new_eos())
        return GLib.SOURCE_REMOVE

    def _check_sender(self) -> bool:
        if self.stopping or not self.sender_process:
            return GLib.SOURCE_REMOVE
        for process in self.auxiliary_processes:
            return_code = process.poll()
            if return_code not in {None, 0}:
                self._fail(
                    "transport-process-exited",
                    RuntimeError(f"transport process exited with status {return_code}"),
                )
                return GLib.SOURCE_REMOVE
        return_code = self.sender_process.poll()
        if return_code is None:
            return GLib.SOURCE_CONTINUE
        if return_code == 0:
            self.exit_code = 0
            self._stop("sender-exited")
        else:
            self._fail("sender-exited", RuntimeError(f"sender exited with status {return_code}"))
        return GLib.SOURCE_REMOVE

    def _bus_message(self, _bus: Gst.Bus, message: Gst.Message) -> None:
        if message.type == Gst.MessageType.ERROR:
            error, debug = message.parse_error()
            sender_return_code = self.sender_process.poll() if self.sender_process else None
            self._fail(
                "pipeline-error",
                error,
                debug=debug,
                sender_return_code=sender_return_code,
            )
        elif message.type == Gst.MessageType.EOS:
            self.exit_code = 0
            self._stop("eos")
        elif message.type == Gst.MessageType.QOS:
            _format, _processed, dropped = message.parse_qos_stats()
            self.counters.dropped_buffers = max(self.counters.dropped_buffers, int(dropped))
        elif message.type == Gst.MessageType.STATE_CHANGED and message.src == self.pipeline:
            old, new, pending = message.parse_state_changed()
            emit(
                self.event_file,
                "pipeline-state",
                old=old.value_nick,
                new=new.value_nick,
                pending=pending.value_nick,
            )

    def _portal_closed(self, _session: Xdp.Session) -> None:
        if not self.stopping:
            self._fail("portal-closed", RuntimeError("the portal session was closed"))

    def _interrupted(self) -> bool:
        self.exit_code = 130
        self._stop("signal")
        return GLib.SOURCE_REMOVE

    def _fail(self, event: str, error: BaseException, **fields: Any) -> None:
        emit(self.event_file, event, error=str(error), **fields)
        self.exit_code = 1
        self._stop(event)

    def _stop_process(
        self,
        process: subprocess.Popen[bytes] | None,
        role: str,
        grace_seconds: float = 2,
    ) -> None:
        if process is None or process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=grace_seconds)
            emit(self.event_file, "process-stopped", role=role, method="terminate")
        except subprocess.TimeoutExpired:
            process.kill()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                emit(self.event_file, "process-stop-timeout", role=role)
                return
            emit(self.event_file, "process-stopped", role=role, method="kill")

    def _stop(self, reason: str) -> None:
        if self.stopping:
            return
        self.stopping = True
        # Close the receiver and every FIFO consumer before stopping GStreamer.
        # Otherwise a filesink blocked on a full FIFO can make set_state(NULL)
        # wait forever and leave both the TV session and audio routing stranded.
        self._stop_process(self.sender_process, "receiver")
        for index, process in enumerate(self.auxiliary_processes):
            self._stop_process(process, f"transport-{index}")
        if self.pipeline:
            self.pipeline.set_state(Gst.State.NULL)
        if self.tone_process and self.tone_process.poll() is None:
            self.tone_process.terminate()
            try:
                self.tone_process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.tone_process.kill()
                self.tone_process.wait()
        if self.audio_route:
            self.audio_route.stop()
        if self.session:
            self.session.close()
        emit(
            self.event_file,
            "stopped",
            reason=reason,
            capture_frames=self.counters.capture_frames,
            video_frames=self.counters.video_frames,
            video_keyframes=self.counters.video_keyframes,
            video_bytes=self.counters.video_bytes,
            audio_buffers=self.counters.audio_buffers,
            audio_bytes=self.counters.audio_bytes,
            dropped_buffers=self.counters.dropped_buffers,
        )
        self.loop.quit()


def run_record(args: argparse.Namespace) -> int:
    output = pathlib.Path(args.output).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    event_path = output.with_suffix(output.suffix + ".events.jsonl")
    Gst.init(None)
    with event_path.open("w", encoding="utf-8") as event_file:
        recorder = Recorder(output, args.duration, event_file, args.test_tone)
        return recorder.start()


def ffprobe_json(path: pathlib.Path) -> dict[str, Any]:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_streams",
            "-show_format",
            "-of",
            "json",
            str(path),
        ],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    )
    return json.loads(result.stdout)


def frame_hashes(path: pathlib.Path) -> list[str]:
    process = subprocess.Popen(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            str(path),
            "-vf",
            "fps=1,scale=160:90",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "gray",
            "-",
        ],
        stdout=subprocess.PIPE,
    )
    assert process.stdout is not None
    frame_size = 160 * 90
    hashes = []
    while True:
        frame = process.stdout.read(frame_size)
        if len(frame) != frame_size:
            break
        hashes.append(hashlib.sha256(frame).hexdigest())
    if process.wait() != 0:
        raise RuntimeError("ffmpeg could not decode video frames")
    return hashes


def audio_level(path: pathlib.Path) -> float | None:
    result = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "info",
            "-i",
            str(path),
            "-map",
            "0:a:0",
            "-af",
            "volumedetect",
            "-f",
            "null",
            "-",
        ],
        text=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    for line in result.stderr.splitlines():
        if "mean_volume:" in line:
            return float(line.split("mean_volume:", 1)[1].split(" dB", 1)[0].strip())
    return None


def run_verify(args: argparse.Namespace) -> int:
    path = pathlib.Path(args.input).expanduser().resolve()
    if not path.is_file():
        print(json.dumps({"ok": False, "error": f"not a file: {path}"}))
        return 1
    probe = ffprobe_json(path)
    streams = probe.get("streams", [])
    video = next((stream for stream in streams if stream.get("codec_type") == "video"), None)
    audio = next((stream for stream in streams if stream.get("codec_type") == "audio"), None)
    hashes = frame_hashes(path)
    unique_hashes = len(set(hashes))
    mean_volume_db = audio_level(path)
    checks = {
        "video_h264": bool(video and video.get("codec_name") == "h264"),
        "video_baseline": bool(video and video.get("profile") in {"Baseline", "Constrained Baseline"}),
        "audio_aac": bool(audio and audio.get("codec_name") == "aac"),
        "audio_48khz_stereo": bool(audio and audio.get("sample_rate") == "48000" and audio.get("channels") == 2),
        "decoded_video_frames": len(hashes) >= 2,
        "changing_pixels": unique_hashes >= 2,
        "audible_audio": mean_volume_db is not None and mean_volume_db > -60.0,
    }
    result = {
        "ok": all(checks.values()),
        "file": str(path),
        "checks": checks,
        "sampled_frames": len(hashes),
        "unique_frame_hashes": unique_hashes,
        "mean_volume_db": mean_volume_db,
        "format": probe.get("format", {}),
        "video": video,
        "audio": audio,
    }
    rendered = json.dumps(result, indent=2, sort_keys=True)
    print(rendered)
    if args.output:
        output = pathlib.Path(args.output).expanduser().resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered + "\n", encoding="utf-8")
    return 0 if result["ok"] else 1


def run_discover(_args: argparse.Namespace) -> int:
    receivers = [receiver.json() for receiver in enrich_receiver_details(discover_mice_receivers())]
    print(json.dumps({"receivers": receivers}, indent=2, sort_keys=True))
    return 0


def run_mice_probe(args: argparse.Namespace) -> int:
    messages = probe_wfd_capabilities(args.address, args.name, timeout=args.timeout)
    transcript = []
    for message in messages:
        transcript.append(
            {
                "start_line": message.start_line,
                "headers": message.headers,
                "body": message.body.decode("utf-8", "replace"),
            }
        )
    result = {"receiver": args.address, "messages": transcript}
    rendered = json.dumps(result, indent=2, sort_keys=True)
    print(rendered)
    if args.output:
        output = pathlib.Path(args.output).expanduser().resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered + "\n", encoding="utf-8")
    return 0


def run_cast_artifact(args: argparse.Namespace) -> int:
    artifact = pathlib.Path(args.input).expanduser().resolve()
    if not artifact.is_file():
        raise FileNotFoundError(artifact)
    root = pathlib.Path(__file__).resolve().parents[1]
    helper = root / "build" / "wfd-sender"
    if not helper.is_file():
        raise RuntimeError("run ./omacast-setup before casting")
    environment = dict(os.environ)
    environment["OMARCHY_CAST_FRIENDLY_NAME"] = args.name
    environment["OMARCHY_CAST_SOURCE_ID"] = args.source_id
    result = subprocess.run(
        [str(helper), args.address, str(artifact), str(args.timeout)],
        env=environment,
        check=False,
    )
    return result.returncode


def run_cast_live(args: argparse.Namespace) -> int:
    root = pathlib.Path(__file__).resolve().parents[1]
    helper = root / "build" / "wfd-sender"
    if not helper.is_file():
        raise RuntimeError("run ./omacast-setup before casting")
    session_root = runtime_root(required=True)
    assert session_root is not None
    event_path = session_root / "session.events.jsonl"
    video_mode = choose_video_mode(args.address, args.quality)
    Gst.init(None)
    with tempfile.TemporaryDirectory(prefix="live-", dir=session_root) as runtime_dir:
        raw_transport_fifo = pathlib.Path(runtime_dir) / "raw-transport.ts"
        transport_fifo = pathlib.Path(runtime_dir) / "transport.ts"
        os.mkfifo(raw_transport_fifo, 0o600)
        os.mkfifo(transport_fifo, 0o600)

        def start_sender() -> tuple[subprocess.Popen[bytes], list[subprocess.Popen[bytes]]]:
            environment = dict(os.environ)
            environment["OMARCHY_CAST_FRIENDLY_NAME"] = args.name
            environment["OMARCHY_CAST_SOURCE_ID"] = args.source_id
            environment["OMARCHY_CAST_CEA_MODE"] = video_mode.cea_bitmap
            sender_timeout = args.duration + 30 if args.duration > 0 else 0
            sender = subprocess.Popen(
                [str(helper), args.address, str(transport_fifo), str(sender_timeout)],
                env=environment,
            )
            remux = subprocess.Popen(
                [
                    "ffmpeg",
                    "-nostdin",
                    "-hide_banner",
                    "-loglevel",
                    "fatal",
                    "-y",
                    "-i",
                    str(raw_transport_fifo),
                    "-map",
                    "0:v:0",
                    "-map",
                    "0:a:0",
                    "-c",
                    "copy",
                    "-mpegts_start_pid",
                    "256",
                    "-mpegts_pmt_start_pid",
                    "4096",
                    "-muxdelay",
                    "0.7",
                    "-muxpreload",
                    "0.7",
                    "-f",
                    "mpegts",
                    str(transport_fifo),
                ],
            )
            return sender, [remux]

        with event_path.open("w", encoding="utf-8") as event_file:
            emit(
                event_file,
                "live-cast-requested",
                receiver=args.address,
                duration_seconds=args.duration,
                video_profile="high",
                video_mode=video_mode.name,
                video_width=video_mode.width,
                video_height=video_mode.height,
                video_bitrate_kbps=video_mode.bitrate_kbps,
                encoder_preset=video_mode.encoder_preset,
                wfd_cea_bitmap=video_mode.cea_bitmap,
            )
            recorder = Recorder(
                raw_transport_fifo,
                args.duration,
                event_file,
                False,
                video_profile="high",
                video_mode=video_mode,
                sender_start=start_sender,
                mute_local_audio=not args.keep_local_audio,
            )
            return recorder.start()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Cast an Omarchy desktop to a supported Roku TV")
    parser.add_argument("--version", action="version", version=f"OmaCast {APP_VERSION}")
    subparsers = parser.add_subparsers(dest="command", required=True)
    discover = subparsers.add_parser("discover", help="discover Miracast-over-LAN receivers")
    discover.set_defaults(func=run_discover)
    cast_live = subparsers.add_parser("cast-live", help="share a portal-selected monitor live over WFD/MICE")
    cast_live.add_argument("--address", required=True)
    cast_live.add_argument("--name", default="OmaCast")
    cast_live.add_argument("--source-id", default="OmaCastSender001")
    cast_live.add_argument("--duration", type=int, default=120, help="seconds to stream; 0 runs until interrupted")
    cast_live.add_argument(
        "--quality",
        choices=("auto", "720p", "1080p"),
        default="auto",
        help="video mode; auto uses Roku native panel information when available",
    )
    cast_live.add_argument(
        "--keep-local-audio",
        action="store_true",
        help="also play desktop audio on the computer while casting",
    )
    cast_live.set_defaults(func=run_cast_live)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    required_commands = ("avahi-browse", "ffmpeg", "ffprobe", "pactl")
    missing = [command for command in required_commands if not shutil.which(command)]
    if missing:
        print(f"missing required commands: {', '.join(missing)}", file=sys.stderr)
        return 2
    recover_orphan_audio_route()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
