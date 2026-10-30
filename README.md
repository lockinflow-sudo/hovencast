# HovenCast

**Turn your TV into a wireless second monitor for Omarchy.**

Give your TV its own workspace while you keep working on your laptop.
Or share a screen, a window, or a selected area with audio.

**Tested with compatible Roku TVs, Fire TV Stick, and Google Cast.**
Apple TV / AirPlay is **experimental and needs further hardware testing**.

[Try HovenCast](#try-hovencast) · [Check your receiver](#receiver-support) · [Report a problem](https://github.com/lockinflow-sudo/hovencast/issues/new?template=bug-report.yml) · [Share a working setup](https://github.com/lockinflow-sudo/hovencast/issues/new?template=compatibility-report.yml)

## What can I do with it?

- **Add room to work.** Keep a reference page or another application on the TV while your laptop stays on a separate workspace.
- **Share what you choose.** Send a screen, an application window, or a selected area to the TV with audio.
- **Control it from the Omarchy bar.** Pick a receiver, change the TV workspace, or stop casting from the panel.

Streams travel directly over your local network or a Wi-Fi Direct link. No cloud relay is required.

## Try HovenCast

For a current **Omarchy Quattro desktop with Hyprland on x86-64**:

```sh
omarchy plugin add https://github.com/lockinflow-sudo/hovencast.git --enable
```

1. Open HovenCast in the bar and select **Review and set up**. Review the changes, enter **y** in the setup terminal, and approve administrator prompts.
2. Prepare your receiver using the table below. For Google Cast, also select **Set up Google Cast** in the panel.
3. Choose **Extend Display** for a separate workspace, or **Share Screen** for existing content. Select your TV and approve any prompt it shows.

The command installs the widget. First-use setup is required before casting: it installs missing packages and streaming helpers, builds the Miracast helper, changes the system-wide screen-sharing picker, and restarts the user portal services. Finish other screen shares first. Setup saves recovery information, and you can decline it and return later. See [setup details](#requirements-and-setup-changes) and [removal](#remove-and-recover).

After updating an existing installation, stop casting and run `omarchy restart shell` once to load the new panel.

## Receiver support

| Receiver | Before connecting | Status |
| --- | --- | --- |
| TCL Roku 32S331, 55S405, 65S451 | Same LAN; enable screen mirroring in the TV settings | Hardware tested |
| Fire TV Stick | Open **Display Mirroring**; requires compatible Wi-Fi Direct adapter/driver | Hardware tested; owner reconfirmed working for 2.5 |
| Google Cast / Chromecast | Same LAN; run **Set up Google Cast** from the panel | Hardware tested on CarbonCast; owner reconfirmed working for 2.5 |
| Apple TV / AirPlay | Same LAN; **Set up Apple TV (experimental)**, then PIN pairing | **Experimental — further hardware testing required** |
| Other receiver models | Depends on protocol, receiver and Wi-Fi adapter | Community testing welcome; compatibility is not guaranteed |

Apple TV fresh setup, PIN pairing, playback, audio, cursor smoothness, and stop/reconnect need further hardware validation. Earlier development tests do not establish 2.5 fresh-install acceptance.

Wireless display delay varies by receiver and network. Review the [Google Cast measurements](docs/google-cast-cursor-results.md) and [Fire Stick results](docs/firestick-cursor-results.md) if latency matters for your use. The tuned Google Cast default is 720p30 with a 6 Mbps maximum media bitrate.

## Help improve HovenCast

**Tried it? Tell us what happened—even if everything worked.** Reports from more receiver models and Wi-Fi adapters help identify what to improve next.

- [**Share a working or partially working setup**](https://github.com/lockinflow-sudo/hovencast/issues/new?template=compatibility-report.yml): receiver model, connection type, and what you tested.
- [**Report a problem or get setup help**](https://github.com/lockinflow-sudo/hovencast/issues/new?template=bug-report.yml): where you got stuck and what you expected.
- [**Suggest an improvement**](https://github.com/lockinflow-sudo/hovencast/issues/new?template=feature-request.yml): what you want to do and what would make it easier.

Reports are public and require a GitHub account. Share only relevant log lines. Remove PINs, pairing credentials, personal screen content, and private network details.

## Panel screenshots

HovenCast 2.5 panels showing Roku, Fire TV Stick, and Google Cast receiver choices. Receiver lists are staged from discovered devices for readability, and live previews are hidden for privacy. These images show the controls before connecting.

| Extend Display | Share Screen |
| --- | --- |
| <img src="docs/screenshots/extend-display.png" alt="HovenCast 2.5 with Extend Display selected, workspace controls, and Roku, Fire TV Stick, and Google Cast receivers" width="420"> | <img src="docs/screenshots/share-screen.png" alt="HovenCast 2.5 with Share Screen selected, Desktop, Window, and Selection controls, and Google Cast highlighted" width="420"> |

## Use and stop

**Extend Display** places a workspace on a dedicated wireless display. Choose
left, right, above, or below placement relative to your laptop. The panel can switch the TV's
workspace during a session. Moving an application between laptop and TV
workspaces also follows its audio where the application exposes a distinct
playback stream; one browser process spanning both displays can share audio.

**Share Screen** offers screen, window, and area selection. Window mode captures
the selected monitor locally and crops the outgoing stream to the chosen window.
Other monitor content is processed locally even though only the crop is sent.

Right-click the bar icon or use the panel Stop control. HovenCast restores the
workspace placement and prior audio route. Fire TV receivers may need Display
Mirroring reopened before a new connection. Names alone never merge receivers;
verified grouping keeps explicit Local network and Wi-Fi Direct choices.

## Setup help

If setup stops, keep the terminal open and read the error. Its output is saved privately in `${XDG_STATE_HOME:-~/.local/state}/hovencast/last-setup-terminal.log`; keyboard input is not recorded.

You can reopen setup from a terminal:

```sh
~/.config/omarchy/plugins/hoven.cast/hovencast-setup
```

For a known Chromecast that mDNS does not find:

```sh
~/.config/omarchy/plugins/hoven.cast/hovencast-chromecast-setup --host DEVICE_IP
```

Core setup requires active NetworkManager and Avahi services. If your Omarchy installation still uses another network manager, [report your setup](https://github.com/lockinflow-sudo/hovencast/issues/new?template=bug-report.yml) before changing networking.

The marketplace may continue to show **Manual setup** until its maintainer approves the release and explicitly resolves the installation override. The GitHub command above follows the repository's current default branch; a marketplace-reviewed snapshot does not pin that command to a particular commit.

## Requirements and setup changes

HovenCast targets Omarchy Quattro, Hyprland, PipeWire/Pulse, and the Hyprland
screen-sharing portal. Its core package checks cover Avahi, NetworkManager,
libnm, wpa_supplicant, dnsmasq, polkit, iptables, Python/GObject, GStreamer and
its media plugins, FFmpeg, libportal, libpulse, opus, libvpx, GCC/pkgconf,
grim/slurp, jq, and both desktop portal packages. The complete package list is
in [`scripts/setup.py`](scripts/setup.py). Existing packages are preserved.

Google Cast setup installs `PyChromecast==14.0.10`; Apple TV setup installs
`pyatv==0.18.0` in a user-owned virtual environment. Native helper source,
licenses, checksums and rebuild instructions are under [`native/`](native/).
The bundled helpers target Linux x86-64. The Google Cast binary depends on the
tested Arch FFmpeg/libvpx ABI; setup reports missing libraries before replacing
an existing helper. Other architectures or later ABI changes require a rebuild.

Wi-Fi Direct requires a compatible adapter and driver. Setup installs scoped
root-owned helpers, not unrestricted sudo access. Normal discovery and casting
use the active local session's limited polkit authorization. Wi-Fi profiles and
session firewall leases are removed on disconnect. Google Cast and AirPlay
setup record only newly created, receiver-specific UFW rules for later cleanup.

Some adapters require an explicit scan-MAC compatibility setting. It is **off by
default**. Use this only after diagnosing that adapter's negotiation failure:

```sh
~/.config/omarchy/plugins/hoven.cast/hovencast-setup --wifi-compat-adapter INTERFACE
```

This disables scan MAC randomization for that adapter, including ordinary Wi-Fi
scans. The installer explains the privacy tradeoff. Uninstall removes only an
unchanged HovenCast-owned compatibility file.

## Remove and recover

Stop casting, disable the widget, then run cleanup **before** removing the plugin:

```sh
omarchy plugin disable hoven.cast
~/.config/omarchy/plugins/hoven.cast/hovencast-uninstall
omarchy plugin remove hoven.cast
```

Cleanup removes HovenCast-owned receiver firewall rules and scoped privileged
helpers, restores only portal values still owned by HovenCast, and restarts the
user portals. It preserves unrelated configuration and pre-existing firewall
rules. Installed packages, native helper files, optional Python environments,
and saved pairing data are retained rather than removing shared dependencies
or credentials automatically.

If the plugin was deleted before cleanup, the self-contained portal recovery
script remains at `${XDG_STATE_HOME:-$HOME/.local/state}/hovencast/restore-portal.py`:

```sh
python "${XDG_STATE_HOME:-$HOME/.local/state}/hovencast/restore-portal.py" remove
systemctl --user restart xdg-desktop-portal-hyprland.service xdg-desktop-portal.service
```

That recovery restores the picker only. Reinstall the same plugin version and
run its uninstaller to remove receiver firewall rules and privileged helpers.

## Privacy, validation and limitations

No cloud relay or saved screen recording is required. Session logs and picker
previews use user-only runtime storage. AirPlay pairing credentials are stored
with user-only permissions. Receiver names and LAN addresses are compatibility
signals; Miracast's IP checks are not cryptographic authentication. Native Cast
retains certificate verification, and native AirPlay uses paired encryption.
See [SECURITY.md](SECURITY.md) for the helper authorization and trust model.

The backend hardware tests were performed on an already configured laptop.
The isolated first-run tests exercise clone, terminal setup, cancellation,
failures, rollback and removal while simulating package installation,
privileged installation and desktop services. They do not establish a clean
machine's hardware compatibility. The [2.5 release checklist](RELEASE_CHECKLIST.md)
tracks the remaining acceptance gates.

## License

HovenCast's plugin code is MIT licensed. The separately invoked AirPlay helper
is LGPL-3.0-or-later with corresponding source included. Open Screen and its
bundled dependency notices accompany the native Google Cast helper. System
media libraries retain their own licenses.
