# Native AirPlay helper

This separately invoked helper comes from https://github.com/omarroth/doubletake
and is licensed LGPL-3.0-or-later. See LICENSE and COPYING.GPL.
`provenance.json` records the exact upstream revision and local modifications.
`doubletake-source.tar.gz` is the complete corresponding source, including
HovenCast changes and tests. Go modules are pinned by go.mod and go.sum.

Rebuild using Go 1.25 or newer:

```sh
mkdir -p build
tar -xzf doubletake-source.tar.gz -C build
cd build/doubletake
go test ./...
go build -o doubletake ./cmd/doubletake
```

You may replace the installed helper at
`$XDG_DATA_HOME/hovencast/native/bin/doubletake` (normally under ~/.local/share)
with your rebuilt version. The HovenCast adapter invokes it as a process.
The bundled binary targets Linux amd64, requires FFmpeg, GStreamer and pactl,
and was built from this source. Credentials are never included in this archive.
