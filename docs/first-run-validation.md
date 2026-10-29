# HovenCast 2.5.0 release validation

Validation completed October 29, 2026. The release combines Roku, Fire TV,
Google Cast and experimental Apple TV paths with guided first-use setup.

## Automated and packaging checks

- 265 Python regression tests passed, including eight isolated first-run scenarios.
- JavaScript model tests, Omarchy manifest validation, shell syntax and whitespace
  checks passed. QML lint exited successfully with framework warnings.
- The Miracast helper builds from the included C source.
- Bundled Google Cast and AirPlay binaries match the SHA-256 values recorded in
  their provenance files. Source/rebuild inputs and license notices are included.
- Google Cast installation rejects a checksum mismatch or missing shared-library
  ABI before replacing the previous helper.

The first-run harness uses bubblewrap with a private home and isolated network,
process, runtime and D-Bus namespaces. It exercises the real Omarchy add, enable,
remove and terminal command routes, local Git clone, setup entry points, native
compilation and portal configuration. Package installation, privileged helper
operations, shell IPC and desktop services are simulated. Cases include trust
confirmation, placement, declining installation, setup cancellation/retry,
package failure, compilation failure, service failure/portal rollback,
idempotency, cleanup and recovery after the plugin directory is deleted.

The test package stub handles the option separator used by current Omarchy's
`pacman -Q -- PACKAGE` verification command.

## Real same-laptop acceptance

The working plugin, user data, state, configuration and system integration were
backed up privately before a fresh plugin installation on the development laptop.
Actual panel-launched core setup passed with exit status 0 and setupReady=true.
The detached setup terminal survived a deliberate plugin reload.

Fresh Google Cast optional setup installed its pinned dependencies, verified
the native helper, discovered CarbonCast and created receiver-scoped UFW rules.
The owner manually reconfirmed that Google Cast and Fire Stick still work on
October 29. That confirmation did not separately enumerate every picture,
audio, cursor and Stop/reconnect case; those cases are not all claimed as newly
verified. Prior detailed transport measurements remain in the receiver notes.

Actual cleanup and restoration passed on October 29. Uninstall restored the
prior portal configuration, removed tracked receiver firewall rules while
preserving unrelated rules, and completed privileged helper removal. Setup
then restored the working integration. Archived system-file contents, the
Wi-Fi compatibility opt-in, portal configuration, receiver firewall ownership
and shell settings matched the saved working setup. Portal services were active
and HovenCast reported v2.5.0, setupReady=true, state=idle.

The initial external acceptance harness omitted two protected polkit grant
files from its archive because it checked their existence without privilege.
A read-only privileged follow-up confirmed both restored grants match the
installed candidate source. Their exact pre-test bytes were unavailable; this
limitation does not establish a setup failure. The external harness was fixed.
A widget placement preference reset by ordinary disable/enable was restored.

All required Arch packages were already installed on this laptop. Real
installation of missing packages on a package-clean host remains unverified.

## Experimental Apple TV / AirPlay

Apple TV dependencies and the native helper were installed, but no Apple TV was
available for final acceptance. Fresh setup, PIN pairing, playback, audio,
cursor smoothness and Stop/reconnect require further hardware testing. Earlier
development checks do not establish fresh-install acceptance of this release.
Apple TV remains explicitly experimental; the owner deferred its hardware tests.

A live video demonstration is also deferred and is not a release requirement.
Existing screenshots are labeled as showing the earlier panel.

## Marketplace checks and publication boundary

The marketplace's unchanged compatibility and static baseline functions passed
local inspection of the candidate: zero findings and a review-required outcome
for bundled executable binaries, privilege, service management, installation
and package management. A local inspection is not official bot evidence or an
approval. Marketplace checks are not a security audit or guarantee.

Publishing a newer approved commit preserves the existing manual-installation
override. The separate standard-installation action's reviewed exception is
installer-only and is ineligible for this multi-capability plugin. The
marketplace maintainer must explicitly resolve the curated override through an
authorized change. The live catalog and deployed Copy action determine whether
standard installation has actually been enabled.
