# Open Magi desktop shell

A thin Tauri v2 desktop wrapper for self-hosting Open Magi. On launch it starts
a local `magi serve` process, waits for it to report ready, then loads the live
dashboard at `http://127.0.0.1:<port>/dashboard` in a hardened webview. A
self-host user double-clicks an app instead of running `magi serve` in a
terminal. First-run setup happens in-app because the committed dashboard already
serves the onboarding wizard.

## Crate layout

This is a Cargo workspace with two crates:

| Path | Crate | Purpose |
| --- | --- | --- |
| `core/` | `magi_desktop_core` | Pure, GUI-free decision logic. No `tauri` dependency. Holds `url_policy`, `server` (binary resolution, bootstrap readiness, log path, free-port), and the `lifecycle` state machine. Fully unit-tested. |
| `.` (root) | `magi-desktop` | The Tauri v2 GUI binary. Spawns `magi serve`, polls readiness, opens the webview, routes navigations. Depends on `magi_desktop_core` plus `tauri`. |

The pure logic lives in a separate crate with zero external dependencies so
`cargo test -p magi_desktop_core` runs the full unit suite on any host without
the GUI toolchain, system webview, or any network fetch.

## Develop

The Tauri CLI is not required for the pure tests, but it is the convenient way
to run the GUI in dev. Install it once:

```
cargo install tauri-cli --version '^2'
```

Run the unit tests (no GUI toolchain needed):

```
cd apps/desktop
cargo test -p magi_desktop_core
```

Run the app in dev (needs a `magi` on PATH or `MAGI_BIN` set, plus the icons and
sidecar described below):

```
cd apps/desktop
cargo tauri dev
```

Lint and format:

```
cargo fmt --check
cargo clippy
```

## Build a release bundle

```
cd apps/desktop
cargo tauri build
```

This produces the bundle targets configured in `tauri.conf.json`
(`app`, `dmg`, `deb`, `appimage`, `msi`). Two artifacts must be present first:

### Icons

`tauri.conf.json` references the standard icon set under `icons/`
(`32x32.png`, `128x128.png`, `128x128@2x.png`, `icon.icns`, `icon.ico`).
Generate them from the Open Magi app icon:

```
cargo tauri icon ../web/public/openmagi-app-icon.png
```

The icons are build artifacts and are not committed (see `.gitignore`).

### Python runtime (`magi` onedir)

The shell runs the Python `magi serve` runtime. For a self-contained app we
bundle a standalone `magi` built with PyInstaller **`--onedir`**. Onedir
produces a DIRECTORY (`magi/` plus `_internal/`), so it ships as a Tauri
**`bundle.resources`** entry, NOT `externalBin` (externalBin is single-file per
target triple and cannot carry a dependency tree).

Validated recipe (a spike built this and ran `magi-agent --help`; the onedir was
~363 MB):

```
pyinstaller \
  --onedir \
  --name magi \
  --collect-data magi_agent \
  --collect-submodules magi_agent \
  --collect-all litellm \
  --collect-all rdflib \
  --collect-all pyshacl \
  --hidden-import uvicorn \
  <entrypoint that calls magi_agent.main:main>
```

1. Build the onedir per target platform with the recipe above.
2. Place the produced `dist/magi/` directory at `binaries/magi/` so
   `binaries/magi/magi` is the executable (see `binaries/README.md`).
3. `tauri.conf.json` ships it via `"resources": { "binaries/magi": "magi" }`,
   so `cargo tauri build` copies the tree into the app's resource directory.

At runtime the shell resolves the binary in this order (see
`core/src/server.rs::resolve_magi_binary`):

1. the bundled onedir executable, `<resource_dir>/magi/magi`,
2. the `MAGI_BIN` environment override,
3. `~/.magi/bin/magi`,
4. `magi` on `PATH`.

So the bundled tree is optional for Homebrew users: if a `magi` is already on
`PATH` (from `brew install openmagi/tap/magi-agent`), the shell uses it and no
resource tree needs to ship. The bundled tree exists for users who want a single
double-clickable app with no separate install.

## Signing and notarization

Code signing (macOS notarization, Windows Authenticode) is a release-pipeline
step, not a local-build concern. Configure the signing identity and notarization
credentials in CI and pass them to `cargo tauri build`; nothing in this repo
embeds any signing secret.

## Security posture

- The runtime binds loopback only. The shell launches `magi serve --host
  127.0.0.1`, so the agent (which uses a well-known local dev token) is NOT
  reachable from the LAN.
- Only the exact loopback origin we launched (`http://127.0.0.1:<port>`) loads
  in-window. `localhost` is treated as External: we bind and launch at
  `127.0.0.1`, and `localhost` can resolve elsewhere (IPv6, hosts overrides).
  Every other navigation is opened in the system browser and blocked in the
  webview (`core/src/url_policy.rs`).
- `window.open` / `target=_blank` / programmatic new webviews are guarded too:
  the shell denies every new window and routes the requested URL through the
  same policy (External opens in the system browser; an in-app loopback link
  navigates the existing window). This closes the popup phishing surface that a
  top-level navigation guard alone would miss.
- The webview capability set (`capabilities/default.json`) grants only
  `opener:allow-open-url`. No filesystem, shell-exec, or arbitrary command
  permissions are exposed to the page.
- A Content Security Policy restricts the webview to the loopback origin.
- The serve log (`~/.magi/logs/desktop-serve.log`) can echo the bearer token in
  uvicorn access lines, so on unix the `logs` directory is created `0700` and
  the file `0600` (owner-only).
- OAuth (for example Composio) completes in the system browser and the dashboard
  polls for completion, so no in-app auth popup window is needed.
