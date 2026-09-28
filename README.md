# HovenCast

HovenCast is an Omarchy bar plugin for sharing a screen, window, or selected area,
with desktop audio, to a compatible Roku TV on the same local network.

Previously published as OmaCast. The installed plugin ID (`hoven.cast`),
command names, and runtime paths remain unchanged so existing installations
can update without reinstalling.

Version 0.1.1 uses Miracast over Infrastructure (MICE). It has been physically
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

```sh
omarchy plugin add https://github.com/lockinflow-sudo/hovencast.git --enable
~/.config/omarchy/plugins/hoven.cast/omacast-setup
```

Setup changes `~/.config/hypr/xdph.conf` and restarts the user portal services.
The prior values and a full pre-install copy are stored under
`${XDG_STATE_HOME:-~/.local/state}/omacast/` for safe removal.
Setup changes only `custom_picker_binary`; it preserves your existing
`allow_token_by_default` preference.

## Use

1. Click the cast icon in the Omarchy bar.
2. Choose a discovered Roku TV.
3. Approve the sender on the TV if prompted.
4. Choose a screen, window, or area in the HovenCast picker.
5. Right-click the bar icon to stop.

Audio moves to the TV while casting by default and is restored afterward.
Enable **Keep audio on this computer** in the widget settings to play it
locally too. If HovenCast or the computer is interrupted before cleanup, the next
HovenCast command recovers audio routing left by the interrupted session.

Window sharing captures the selected monitor and crops it to the chosen window,
following that window as its geometry changes. Keep private windows off that
monitor while sharing. The portal picker is system-wide while HovenCast is
installed, so it also appears for screen-sharing requests from other apps.

## Remove

Restore the external portal setting before deleting the plugin:

```sh
omarchy plugin disable hoven.cast
~/.config/omarchy/plugins/hoven.cast/omacast-uninstall
omarchy plugin remove hoven.cast
```

The uninstaller restores only values still owned by HovenCast. If you changed a
managed value after setup, it leaves that value alone instead of overwriting
your newer configuration.

`omarchy plugin remove` does not run plugin uninstall hooks. If the plugin was
removed first, clone the same release again and run `./omacast-uninstall`; its
restore state is kept outside the plugin directory.

## Privacy and files

HovenCast streams directly to the selected TV on the local network and does not
upload the capture. Its RTSP listener binds only to the receiver-facing local
address and rejects connections not originating from the selected TV. It does
not save screen recordings. The picker preview is deleted as soon as selection
ends. A small newline-delimited diagnostic event log is replaced for each
session under `$XDG_RUNTIME_DIR/omacast/` and normally disappears at logout.

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
  receivers in 0.1.1. Other discovered Roku models may or may not work.
- Discovery depends on Roku MICE advertisement and local multicast traffic.
- 720p and 1080p Roku modes are selected automatically; other resolutions are
  not supported.
- Receiver reconnect, network-roaming, suspend/resume, and long-duration
  reliability need broader field testing.
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
