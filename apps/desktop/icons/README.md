# Icons

`tauri.conf.json` references the standard Tauri icon set. These five files are
**committed** so the release pipeline does not need to regenerate them:

- `32x32.png`
- `128x128.png`
- `128x128@2x.png`
- `icon.icns` (macOS)
- `icon.ico` (Windows)

The rest of a `cargo tauri icon` run (the 512px `icon.png`, the `64x64.png`,
the Windows Store/Square tiles, and the `android/` + `ios/` trees) is large and
platform-spread, so it stays gitignored (see `../.gitignore`).

## Regenerate

The icons derive from the Open Magi app icon. To regenerate the full set:

```
cd apps/desktop
cargo tauri icon ../web/public/openmagi-app-icon.png
```

That rewrites every size into this directory; only the five committed files
above are tracked, the rest are ignored again.
