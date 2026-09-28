# Security

## Trust model

HovenCast is intended for a trusted local network. Starting a cast sends the
selected screen content and desktop audio directly to the TV selected in the
widget. No cloud service is used.

During a session, the RTSP server listens only on the local address used to
reach that TV and accepts only the selected TV's IP address. This reduces
exposure to other devices on the LAN, but IP matching is not cryptographic
authentication. A hostile device with control of the local network could still
attempt impersonation or traffic interception.

The receiver list requires both a MICE advertisement and valid Roku ECP device
information. TCL Roku models 32S331, 55S405, and 65S451 are physically tested;
other models are compatibility candidates. A receiver's advertised identity
is useful compatibility filtering, not a security credential.

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
`$XDG_RUNTIME_DIR/omacast/` with user-only permissions. The picker image is
deleted after selection, and the runtime directory normally disappears at
logout. HovenCast does not intentionally create screen-recording files.

## Reporting a vulnerability

Please do not publish sensitive exploit details in a public issue. Use GitHub's
private vulnerability reporting for this repository when available, or contact
the maintainer privately through their GitHub profile.
