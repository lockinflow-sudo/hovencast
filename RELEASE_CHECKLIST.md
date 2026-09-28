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
- [ ] Add an optional marketplace preview image.
- [x] Submit the repository URL through the Omarchy marketplace issue form ([#9033](https://github.com/omacom/omarchy-plugin-marketplace/issues/9033)).

## v0.1.1 corrective release

- [x] Preserve picker metadata until the backend consumes it.
- [x] Pass the picker/backend handoff regression test.
- [x] Validate a live window cast to the 65-inch TCL Roku TV and clean audio restoration.
- [x] Refresh the marketplace preview with the v0.1.1 UI.
- [x] Create and push the public GitHub `v0.1.1` release.
- [x] Update marketplace submission [#9033](https://github.com/omacom/omarchy-plugin-marketplace/issues/9033).

## HovenCast rebrand

- [x] Rename the user-facing application while preserving the `hoven.cast` plugin ID and existing runtime paths.
- [x] Rename the GitHub repository to `lockinflow-sudo/hovencast`.
- [x] Update marketplace issue #9033 with the HovenCast name and new repository URL.
- [x] Install and verify the renamed widget locally.
- [x] Replace the preview image so it shows the renamed UI with the address redacted.
- [ ] Publish a new release after outstanding marketplace review findings are resolved.
