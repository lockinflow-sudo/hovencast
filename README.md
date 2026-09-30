# HovenCast

HovenCast is an Omarchy bar plugin that turns a compatible Roku TV into a
wireless Hyprland workspace. It can also share one application window, with
desktop audio, over the same local network.

Version 0.2.0 uses Miracast over Infrastructure (MICE). It has been physically
tested with TCL Roku models 32S331, 55S405, and 65S451. Other Roku TVs that
advertise MICE and provide valid Roku device information are shown as
compatible candidates, but still need community testing. Fire TV, Wi-Fi
Direct, Google Cast, AirPlay, and non-Roku receivers are not supported.

## Requirements

- A current Omarchy installation using the Quattro shell and Hyprland portal.
- A Roku TV with **Settings → System → Screen mirroring → Screen mirroring
  mode** set to Prompt or Always allow. Models other than the three listed
  above are community-tested rather than confirmed.
- The computer and TV on the same LAN, with multicast discovery available.
- An Arch package mirror reachable during setup if any dependency is missing.

The setup script installs missing packages through `omarchy pkg add`, builds the
small WFD/RTSP helper from included C source, and configures
`xdg-desktop-portal-hyprland` to use HovenCast's source picker. No prebuilt binary
or recording is included.

## Install

Version 0.2.0 uses HovenCast-only setup commands and runtime paths. Before
installing it over an earlier build, run that build's bundled uninstaller and
remove the installed plugin.

```sh
omarchy plugin add https://github.com/lockinflow-sudo/hovencast.git --enable
~/.config/omarchy/plugins/hoven.cast/hovencast-setup
```

Setup changes `~/.config/hypr/xdph.conf` and restarts the user portal services.
The prior values and a full pre-install copy are stored under
`${XDG_STATE_HOME:-~/.local/state}/hovencast/` for safe removal.
Setup changes only `custom_picker_binary`; it preserves your existing
`allow_token_by_default` preference.

## Use

1. Click the cast icon in the Omarchy bar.
2. Choose **Virtual workspace** and the Hyprland workspace to show, or choose
   **Application window**.
3. Choose a discovered Roku TV and approve the sender on the TV if prompted.
4. While workspace casting, choose another workspace at any time. The Roku
   connection and encoder keep running against the same virtual display.
5. Right-click the bar icon to stop.

The virtual TV output uses the receiver's supported video mode (1280 × 720 for
the TCL Roku 32S331) at scale 1, so applications receive a genuine TV-sized
fullscreen layout without changing the laptop display. A workspace is visible
on only one output. If the laptop's visible workspace is sent to the TV,
HovenCast first switches the laptop to another workspace.

Use the widget's **Control TV** and **Return to laptop** actions for mouse and
keyboard focus. Omarchy's existing **Ctrl+Alt+Tab** and
**Ctrl+Alt+Shift+Tab** monitor-focus shortcuts also move between the laptop and
TV, including when the widget is no longer focused.

During virtual-workspace casting, audio follows its application window between
the laptop and TV workspaces. During application-window casting, audio moves to
the TV for the session. Enable **Keep audio on this computer** in the widget
settings to keep audio local while also sending it to the TV. HovenCast restores
the previous audio sink and workspace layout on normal stop. A detached guard
restores the workspaces and removes the headless output if the casting backend
crashes; the next HovenCast command also recovers stale state.

Window sharing captures the selected monitor and crops it to the chosen window,
following that window as its geometry changes. Keep private windows off that
monitor while sharing. The portal picker is system-wide while HovenCast is
installed, so it also appears for screen-sharing requests from other apps.

The workspace controller is also available from a terminal:

```sh
omarchy-cast workspace-list
omarchy-cast workspace set 4
omarchy-cast workspace focus-tv
omarchy-cast workspace focus-local
```

## Remove

Restore the external portal setting before deleting the plugin:

```sh
omarchy plugin disable hoven.cast
~/.config/omarchy/plugins/hoven.cast/hovencast-uninstall
omarchy plugin remove hoven.cast
```

The uninstaller restores only values still owned by HovenCast. If you changed a
managed value after setup, it leaves that value alone instead of overwriting
your newer configuration.

`omarchy plugin remove` does not run plugin uninstall hooks. If the plugin was
removed first, clone the same release again and run `./hovencast-uninstall`; its
restore state is kept outside the plugin directory.

## Privacy and files

HovenCast streams directly to the selected TV on the local network and does not
upload the capture. Its RTSP listener binds only to the receiver-facing local
address and rejects connections not originating from the selected TV. It does
not save screen recordings. The picker preview is deleted as soon as selection
ends. A small newline-delimited diagnostic event log is replaced for each
session under `$XDG_RUNTIME_DIR/hovencast/` and normally disappears at logout.

See [SECURITY.md](SECURITY.md) for the trust model, residual risks, and private
reporting guidance.

## Compatibility reports

If your Roku model works—or does not—please open a
[compatibility report](https://github.com/lockinflow-sudo/hovencast/issues/new?template=compatibility-report.yml).
Include the TV vendor and model, Omarchy version, and which source types you
tested. Please redact local IP addresses, usernames, and anything visible in a
screen capture before attaching diagnostics.

## Known limitations

- TCL Roku models 32S331, 55S405, and 65S451 are the only physically tested
  receivers. Other discovered Roku models may or may not work.
- Discovery depends on Roku MICE advertisement and local multicast traffic.
- 720p and 1080p Roku modes are selected automatically; other resolutions are
  not supported.
- Receiver reconnect, network-roaming, suspend/resume, and long-duration
  reliability need broader field testing.
- Browsers may share one audio process across multiple windows. If windows from
  the same browser process occupy both displays, HovenCast keeps that shared
  stream on its established display until an associated window changes
  workspace; it cannot identify the individual tab producing the audio.
- A compositor crash removes the headless output itself; HovenCast restores any
  remaining saved state on the next command, but applications may decide to
  resize or reposition their own windows after an output disappears.
- The custom picker changes the portal picker for all applications until
  HovenCast is uninstalled.

## Development and validation

```sh
omarchy plugin validate .
node tests/model.test.js
python -m unittest discover -s tests -p 'test_*.py'
./build-backend
qmllint -I "$OMARCHY_PATH/shell" Panel.qml
```

The repository intentionally ignores local builds, Python caches, event logs,
and transport streams.
The marketplace preview is based on the live panel, with its local network
address redacted.

## License

[MIT](LICENSE)
