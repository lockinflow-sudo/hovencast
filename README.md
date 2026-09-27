# OmaCast

OmaCast is an Omarchy bar plugin for sharing a screen, window, or selected area,
with desktop audio, to a compatible Roku TV on the same local network.

Version 0.1.0 is deliberately narrow. It has been physically tested with TCL
Roku TVs using Miracast over Infrastructure (MICE). Fire TV, Wi-Fi Direct,
Google Cast, AirPlay, and non-Roku receivers are not supported in this release.

## Requirements

- A current Omarchy installation using the Quattro shell and Hyprland portal.
- A TCL Roku TV with **Settings → System → Screen mirroring → Screen mirroring
  mode** set to Prompt or Always allow.
- The computer and TV on the same LAN, with multicast discovery available.
- An Arch package mirror reachable during setup if any dependency is missing.

The setup script installs missing packages through `omarchy pkg add`, builds the
small WFD/RTSP helper from included C source, and configures
`xdg-desktop-portal-hyprland` to use OmaCast's source picker. No prebuilt binary
or recording is included.

## Install

Once this repository has a public URL:

```sh
omarchy plugin add <repository-url> --enable
~/.config/omarchy/plugins/hoven.cast/omacast-setup
```

Setup changes `~/.config/hypr/xdph.conf` and restarts the user portal services.
The prior values and a full pre-install copy are stored under
`${XDG_STATE_HOME:-~/.local/state}/omacast/` for safe removal.

## Use

1. Click the cast icon in the Omarchy bar.
2. Choose a discovered Roku TV.
3. Approve the sender on the TV if prompted.
4. Choose a screen, window, or area in the OmaCast picker.
5. Right-click the bar icon to stop.

Audio moves to the TV while casting by default and is restored afterward.
Enable **Keep audio on this computer** in the widget settings to play it
locally too.

Window sharing captures the selected monitor and crops it to the chosen window,
following that window as its geometry changes. Keep private windows off that
monitor while sharing. The portal picker is system-wide while OmaCast is
installed, so it also appears for screen-sharing requests from other apps.

## Remove

Restore the external portal setting before deleting the plugin:

```sh
omarchy plugin disable hoven.cast
~/.config/omarchy/plugins/hoven.cast/omacast-uninstall
omarchy plugin remove hoven.cast
```

The uninstaller restores only values still owned by OmaCast. If you changed a
managed value after setup, it leaves that value alone instead of overwriting
your newer configuration.

`omarchy plugin remove` does not run plugin uninstall hooks. If the plugin was
removed first, clone the same release again and run `./omacast-uninstall`; its
restore state is kept outside the plugin directory.

## Privacy and files

OmaCast streams directly on the local network and does not upload the capture.
It does not save screen recordings. A small newline-delimited diagnostic event
log is replaced for each session under `$XDG_RUNTIME_DIR/omacast/` and normally
disappears at logout.

## Known limitations

- Only the tested TCL Roku path is supported in 0.1.0.
- Discovery depends on Roku MICE advertisement and local multicast traffic.
- 720p and 1080p Roku modes are selected automatically; other resolutions are
  not supported.
- Receiver reconnect, network-roaming, suspend/resume, and long-duration
  reliability need broader field testing.
- The custom picker changes the portal picker for all applications until
  OmaCast is uninstalled.

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

## License

[MIT](LICENSE)
