# Virtual workspace capture feasibility

Tested on September 28, 2026 with Hyprland 0.56.2,
xdg-desktop-portal-hyprland 1.4.1, PipeWire 1.6.8, and GStreamer 1.28.6.

## Result

The local portal/PipeWire path continuously captures a Hyprland headless output.
The proof created `HovenCast-TV`, configured it as 1280 × 720 at scale 1, and
selected that output through the existing portal path.

The integrated 20-second run switched the virtual output from workspace 3 to
workspace 1 and back to workspace 3 while keeping the same PipeWire node and
GStreamer pipeline alive:

- Portal node: 74
- Capture caps: BGRA, 1280 × 720
- Capture frames: 624
- Encoded video frames: 610
- Dropped buffers: 0
- Output: H.264 constrained baseline, 1280 × 720 at 30 fps
- Audio: AAC-LC stereo, 48 kHz
- Verification samples: 20 decoded frames, 20 distinct hashes
- Pipeline errors, source stalls, and caps changes: none

A separate forced-crash test killed the virtual-output owner with `SIGKILL`.
The detached guard restored workspace 1 to `eDP-1`, returned focus to the
laptop, removed `HovenCast-TV`, and deleted the recovery state within two
seconds.

## Design consequence

The stable capture source is the output, not a particular workspace. Workspace
switching is therefore a Hyprland operation only; it does not renegotiate the
Roku connection, portal session, PipeWire node, encoder, or MPEG transport.

## Hardware validation

The complete v0.2.0 path was validated end to end with a TCL Roku 32S331. The
test covered sustained video and audio playback, workspace switching, keyboard
and pointer focus, moving application windows between the laptop and TV, audio
following those windows, clean stop, and receiver reconnection.
