# HovenCast 2.5.0 release validation

The release candidate combines the hardware-tuned backends with guided core
setup and optional Google Cast / Apple TV setup from the panel.

## Passed on October 15, 2026

- All 263 Python regression tests passed, including eight isolated first-run flow scenarios.
- JavaScript model tests passed; the plugin manifest validates; QML lint passes.
- The Miracast helper builds from the included source.
- Bundled Google Cast and AirPlay binaries match their recorded SHA-256 values.
- The Google Cast installer rejects a checksum mismatch or missing ABI before
  replacing the prior working helper; corresponding rebuild inputs and notices are included.

The first-run harness mounts the host read-only in bubblewrap, uses a temporary
home, and isolates networking, processes, runtime and D-Bus. It runs the real
Omarchy add/enable/remove and terminal command routes, local Git clone, setup
entry points, media checks, native compilation and portal configuration code.
Package installation, privileged helper installation/removal, terminal creation,
shell IPC and portal services are simulated. These scenarios cover installation
trust/placement, declining installation, fresh setup, cancellation/retry,
package failure, compilation failure, portal rollback, idempotency, removal,
and recovery after the plugin directory is deleted.

The source backend hardware validation predates this unified release candidate:
Fire Stick cursor/audio, native Apple TV cursor/audio, and Google Cast normal
video, picture, cursor and audio were confirmed on the configured development
laptop. This does not establish a clean machine's installation behavior.

## Final acceptance still required

1. Install the candidate in the selected real Omarchy environment.
2. Check the visible first-use panel and real terminal confirmation.
3. Verify actual dependency installation, scoped helper approval, native helper
   installation and portal restart. Record whether packages were already present.
4. Verify Roku, Fire TV, Google Cast and Apple TV paths on available hardware,
   including normal video/audio, cursor movement and Stop/reconnect.
5. Verify uninstall restores screen sharing and removes owned helper/firewall
   integration; preserve and restore the previous working installation.
6. Publish the tested commit and v2.5.0 release, then request exact-commit marketplace
   review and removal of the existing manual-installation override.

No clean-machine acceptance, GitHub publication, or marketplace deployment has
been claimed. The working installed plugin was not replaced during preparation.

## Same-laptop acceptance — October 16, 2026 (in progress)

The user selected a backed-up fresh plugin installation on the existing laptop.
The plugin, user data, state, shell/portal configuration, helper/polkit files,
adapter compatibility setting and UFW rules were backed up before changes.
Old cleanup removed eight owned firewall rules, the scoped helpers, and the
owned picker setting. The original plugin/data/state directories were parked
outside the active plugin directory.

The candidate was cloned through the real `omarchy plugin add ... --enable`
path from its local release checkout. The initial running shell retained the old
panel until a shell restart; after restart, IPC and the visible first-use panel
both reported HovenCast 2.5.0. The visible setup entry and missing-support checks
were verified. Real panel-to-terminal setup and hardware testing are pending.

All required Arch packages already exist on this laptop. This test therefore
cannot establish installation of missing Arch packages on a package-clean host.
Optional receiver Python environments and bundled helpers start from empty
user-data storage for this acceptance run.
