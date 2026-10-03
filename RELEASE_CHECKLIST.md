# HovenCast release checklist

## Complete locally

- [x] Versioned marketplace manifest at the repository root.
- [x] Backend source and build script included; no prebuilt helper required.
- [x] No hard-coded development checkout paths.
- [x] Portal picker setup preserves prior configuration.
- [x] Uninstall restores only HovenCast-owned portal values.
- [x] README documents dependencies, setup, removal, privacy, and Roku-only scope.
- [x] MIT license included.
- [x] Recordings, build products, caches, and prototype work excluded.

## Requires owner approval

- [x] Choose the final public GitHub repository URL and add it to `README.md`.
- [x] Create and push the public repository.
- [x] Re-run validation from the public clone.
- [x] Validate v0.1.0 with a live 32-inch TCL Roku cast, synchronized audio, and clean stop/restoration.
- [x] Create and push the public GitHub `v0.1.0` release.
- [x] Add an optional marketplace preview image.
- [x] Submit the repository URL through the Omarchy marketplace issue form ([#9033](https://github.com/omacom/omarchy-plugin-marketplace/issues/9033)).

## v0.1.1 corrective release

- [x] Preserve picker metadata until the backend consumes it.
- [x] Pass the picker/backend handoff regression test.
- [x] Validate a live window cast to the 65-inch TCL Roku TV and clean audio restoration.
- [x] Refresh the marketplace preview with the v0.1.1 UI.
- [x] Create and push the public GitHub `v0.1.1` release.
- [x] Update marketplace submission [#9033](https://github.com/omacom/omarchy-plugin-marketplace/issues/9033).

## HovenCast rebrand

- [x] Use HovenCast naming throughout the current source tree, scripts, runtime paths, and documentation.
- [x] Rename the GitHub repository to `lockinflow-sudo/hovencast`.
- [x] Update marketplace issue #9033 with the HovenCast name and new repository URL.
- [x] Install and verify the renamed widget locally.
- [x] Replace the preview image so it shows the renamed UI with the address redacted.
- [x] Resolve the outstanding marketplace review findings before the next release.

## Marketplace discovery-output finding

- [x] Bound Avahi discovery output, record lengths, and receiver count.
- [x] Test flooded output, timeout with valid partial output, malformed records, and receiver cap.
- [x] Confirm live Roku discovery still finds the available TVs.
- [x] Get marketplace reviewer confirmation before tagging the next release.

## v0.2.0 virtual workspace casting

- [x] Prove PipeWire can continuously capture a 1280 × 720 Hyprland headless output.
- [x] Keep the portal node and encoder alive while changing the workspace on the output.
- [x] Move the laptop to a fallback before casting its visible workspace.
- [x] Restore workspace placement and focus after normal stop.
- [x] Recover the output and workspace placement after a forced backend crash.
- [x] Add window-versus-workspace selection and live workspace controls.
- [x] Validate the complete transport with the TCL Roku 32S331.
- [x] Route audio with its application window between laptop and TV workspaces.
- [x] Gracefully end the MICE projection so the receiver accepts the next session.
- [x] Rebuild the native sender automatically when its source changes.
- [x] Pass a clean-install test from a fresh clone.
- [x] Pass five consecutive stop/start cycles against the TCL Roku 32S331.
- [ ] Install or publish the updated plugin only after explicit owner approval.
