# Native Google Cast sender

The Linux x86-64 helper is built from Chromium Open Screen. `provenance.json`
records the upstream revision, binary SHA-256, and tested playback settings.
`source.patch` contains all local source/build changes against that revision;
`args.gn` records the GN build configuration. License notices for Open Screen
and its statically linked dependencies are in `licenses/`.

The binary dynamically uses system FFmpeg 63, libopus and libvpx. It targets
the tested Arch Linux ABI, not arbitrary Linux distributions. Setup checks
the checksum and missing shared libraries before replacing an installed helper.
It never disables Cast receiver certificate verification.

To rebuild after an ABI change:

1. Fetch Chromium's depot_tools and Open Screen from their official repositories.
2. Check out the revision in `provenance.json` and run `gclient sync` for its pinned dependencies.
3. Apply `source.patch` with `git apply` in the Open Screen checkout.
4. Copy `args.gn` to `out/hovencast/args.gn`, run `gn gen out/hovencast`, then
   `ninja -C out/hovencast -j2 cast_sender`.
5. Replace the bundled binary and update its SHA-256 in `provenance.json`.
   Run `python scripts/install_chromecast_native.py` from the plugin checkout.

Upstream: https://github.com/chromium/openscreen

Build/runtime media libraries are separately supplied by Arch packages; they
are not bundled here. The helper is invoked as a separate process.
