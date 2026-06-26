# Icons (placeholder)

`tauri.conf.json` references the standard Tauri icon set:

- `32x32.png`
- `128x128.png`
- `128x128@2x.png`
- `icon.icns` (macOS)
- `icon.ico` (Windows)

These are NOT committed yet. Generate them from the Open Magi app icon
(`apps/web/public/openmagi-app-icon.png`) with:

```
cargo tauri icon apps/web/public/openmagi-app-icon.png
```

which writes all required sizes into this `icons/` directory. The release
pipeline runs this step; the GUI binary builds locally once the icons exist.
