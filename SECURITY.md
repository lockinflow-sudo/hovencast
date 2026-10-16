# Security

## Experimental Wi-Fi Direct transport

HovenCast uses libnm to negotiate a P2P link to the selected device identifier.
The link is volatile and bound to the process's D-Bus lifetime. IPv4 and IPv6
never become default routes. The RTSP listener binds only to the P2P group's
local IPv4 address. When the receiver owns the group, only its gateway address
is accepted; when HovenCast owns the group, the first connection from that
dedicated group subnet is accepted and pinned. Additional RTSP clients are
rejected. Network identity checks are not cryptographic identity verification.
No GNOME casting service is required. Fire Stick workspace video has been
physically verified; other P2P receivers remain experimental.

The on-demand P2P helper is installed root-owned under
`/usr/local/libexec/hovencast-p2p-helper.py`. A root-owned executable entry point
invokes that fixed file with Python isolated mode; user arguments cannot become
interpreter options. The dedicated `org.hovencast.p2p.connect` polkit action
permits only active local sessions without repeated authentication. Inactive
and remote callers are denied. This grants local desktop applications access
to the helper's limited P2P operations, not arbitrary root execution or generic
Python, shell, NetworkManager or firewall privileges. Installation requires
administrative approval once; uninstallation removes the action and rule.
It validates the caller's UID and process start time, the selected NetworkManager
adapter, peer and session UUID, and the profile's per-user permissions. When the
peer already owns a group it issues only that adapter's PBC group join.
After DHCP it adds one runtime TCP 7236 allowance restricted to the actual P2P
interface, laptop address and selected receiver address. If the receiver owns
the group, this address comes from DHCP's router. If the laptop owns the group,
the helper maps the selected device MAC through supplicant's current group
membership to its interface MAC, then requires a matching unexpired, in-subnet
NetworkManager DHCP lease. It never selects the first neighbour or opens the
whole subnet. It removes that
rule on lease EOF, caller exit or link loss; saved UFW rules remain unchanged.
Laptop-owned groups also lease a UDP client-port 68 to server-port 67 allowance
on the actual P2P interface, matched to the selected associated receiver's MAC,
so DHCP can work with an INPUT-drop firewall. This rule allows initial broadcast
DHCP and renewals, and is removed on the same lease cleanup. Cleanup attempts
both rules even when one deletion fails, and reports failures rather than
claiming successful removal.
The helper does not load code from the user-owned plugin or execute shell input.
Both link roles use the same exact-peer firewall lease. Laptop-owned groups
currently require supplicant group-member mapping and NetworkManager's dnsmasq
lease file; other DHCP providers are not supported by this address-discovery path.

## Trust model

### Receiver grouping

The separate `org.hovencast.metadata.read` polkit action permits active local
sessions to invoke only `/usr/local/libexec/hovencast-metadata-reader`, a fixed
root-owned wrapper using isolated Python and a fixed root-owned script. It
validates PKEXEC_UID against the caller PID owner and process birth time, and
rechecks caller identity during reads and before returning. Arguments cannot
be interpreter options. Inactive/remote sessions are denied, not prompted.

Reads are restricted to one validated adapter's peer list and at most sixteen
selected peers' DeviceAddress/VendorExtension properties, plus a fixed
`wpa_cli p2p_peer` age query. Paths, address continuity, payload sizes, peer
counts and timeouts are bounded. There is no arbitrary D-Bus request, GetAll,
credential query, pairing, firewall change, file-write API or user-code import.
Root helper installation is separate from ordinary discovery. Uninstallation
removes the metadata grant before its fixed files. No general Python/root or
NetworkManager authorization is granted.

Grouping requires fresh, same-snapshot advertised hostname/IP, matching LAN
service metadata and independent non-conflicting Roku ECP anchors. Reused
container IDs never justify a merge. Changed NetworkManager activation paths,
ambiguous candidates or multiple P2P peers claiming one LAN endpoint prevent
grouping. Advertisements remain unauthenticated; this is display organization,
not a security credential. Endpoint choices preserve their exact original
transport/address/port/interface, with LAN as default and no automatic fallback.

HovenCast is intended for a trusted local network. Starting a cast sends the
selected screen content and desktop audio directly to the TV selected in the
widget. No cloud service is used.

During a session, the RTSP server listens only on the local address used to
reach that TV and accepts only the selected TV's IP address. This reduces
exposure to other devices on the LAN, but IP matching is not cryptographic
authentication. A hostile device with control of the local network could still
attempt impersonation or traffic interception.

The MICE receiver list requires both a MICE advertisement and valid Roku ECP device
information. TCL Roku models 32S331, 55S405, and 65S451 are physically tested;
other models are compatibility candidates. A receiver's advertised identity
is useful compatibility filtering, not a security credential.
Discovery limits the amount of Avahi output it reads, along with individual
record lengths and the number of candidate receivers, before probing devices.

## Local integration

The setup script installs dependencies using Omarchy's package helper, builds
the included C source locally, and configures the Hyprland portal to use the
HovenCast picker. The picker is system-wide while the plugin is installed and can
therefore appear when other applications request screen sharing. Setup does not
enable portal restore tokens automatically.

Window sharing captures the selected monitor and crops the outgoing video to
the window. Other content on that monitor is processed locally even though only
the crop is sent. Use screen or region sharing, or keep sensitive windows off
that monitor, when this distinction matters.

Temporary picker images, session logs, and crash-recovery state live under
`$XDG_RUNTIME_DIR/hovencast/` with user-only permissions. The picker image is
deleted after selection, and the runtime directory normally disappears at
logout. HovenCast does not intentionally create screen-recording files.

## Reporting a vulnerability

Please do not publish sensitive exploit details in a public issue. Use GitHub's
private vulnerability reporting for this repository when available, or contact
the maintainer privately through their GitHub profile.

## Native Cast and AirPlay helpers

The optional Google Cast sender is Chromium Open Screen with the accompanying
source/build patch. It retains receiver certificate verification. Its live
MPEG-TS input relay listens on loopback with a random session URL; the negotiated
media connection uses Cast UDP. The worker validates the receiver UUID before
connecting. The packaged binary's SHA-256 and shared-library availability are
checked before installation. The worker terminates its child and relay on Stop.

Apple TV discovery and PIN pairing use the optional pyatv dependency. Native
mirroring invokes the separately bundled doubletake helper using paired,
encrypted AirPlay. Pairing data is user-only and is not included in the source
or binary archives. URL playback remains a diagnostic fallback. Google Cast and
AirPlay setup add only verified receiver-specific UFW rules; newly created rules
are recorded for removal, while equivalent pre-existing rules are preserved.

First-use checks do not install dependencies or change configuration. The user
reviews setup in a terminal before packages, privileged helper installation or
portal changes. The persistent recovery script can restore the portal picker
after the plugin directory is deleted; privileged helper/firewall cleanup still
requires the same release's uninstaller. Native binaries and pairing data are
retained on removal and remain under the user's data/state directories.
