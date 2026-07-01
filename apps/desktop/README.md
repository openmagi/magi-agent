# Open Magi desktop shell

A thin Tauri v2 desktop wrapper for self-hosting Open Magi. On launch it starts
the local serve runtime, waits for it to report ready, then loads the live
dashboard at `http://127.0.0.1:<port>/dashboard` in a hardened webview. A
self-host user double-clicks an app instead of running the serve command in a
terminal. First-run setup happens in-app because the committed dashboard already
serves the onboarding wizard.

The serve entrypoint is the SERVE console script `magi-agent`
(`magi_agent.main:main`), a plain argparse invoked as
`<bin> --host 127.0.0.1 --port <port>`. There is NO `serve` subcommand. (The
brew-installed `magi` command is the Typer CLI, `magi_agent.cli.__main__`, which
has no serve command; the bundled PyInstaller binary is named `magi` but is the
same `main:main` serve entry, so it accepts these flags too.)

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

The shell runs the Python serve runtime (`magi_agent.main:main`). For a
self-contained app we bundle a standalone `magi` built with PyInstaller
**`--onedir`**. Onedir
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
3. `~/.magi/bin/magi-agent` (the serve console script),
4. `~/.magi/bin/magi` (secondary),
5. `magi-agent` on `PATH` (brew's serve console script),
6. `magi` on `PATH` (secondary; the Typer CLI).

The system fallback prefers `magi-agent` because that is the SERVE console
script. The brew `magi` command is the Typer CLI and has no serve command, so it
is only a secondary candidate.

So the bundled tree is optional for Homebrew users: if `magi-agent` is already on
`PATH` (from `brew install openmagi/tap/magi-agent`), the shell uses it and no
resource tree needs to ship. The bundled tree exists for users who want a single
double-clickable app with no separate install.

## Release (macOS)

The macOS release is automated by
`.github/workflows/desktop-release-macos.yml` (runs on `macos-14`, arm64). It
builds the PyInstaller sidecar, deep-signs it, then runs `cargo tauri build`
which signs + notarizes the app, and publishes a signed+stapled `.dmg` plus an
`.app.tar.gz`.

### Cut a release

```
git tag desktop-v0.1.0
git push origin desktop-v0.1.0
```

The tag (`desktop-v*`) triggers the workflow; you can also run it manually via
`workflow_dispatch` (a manual run builds + uploads artifacts but does not
publish a GitHub Release). Keep the tag version in sync with `version` in
`tauri.conf.json`.

### Required GitHub secrets

| Secret | What it is |
| --- | --- |
| `APPLE_CERTIFICATE` | base64 of the "Developer ID Application" `.p12` (`base64 -i cert.p12`) |
| `APPLE_CERTIFICATE_PASSWORD` | password used when exporting that `.p12` |
| `APPLE_SIGNING_IDENTITY` | e.g. `Developer ID Application: Your Name (TEAMID)` |
| `APPLE_ID` | Apple ID email used for notarization |
| `APPLE_PASSWORD` | app-specific password for that Apple ID |
| `APPLE_TEAM_ID` | 10-char Apple Developer Team ID |
| `KEYCHAIN_PASSWORD` | any random string; unlocks the temporary CI keychain |

Notarization needs a paid Apple Developer account. No secret value is ever
printed and the temporary keychain is created in the runner temp dir.

### Why the sidecar is deep-signed BEFORE `cargo tauri build`

Apple notarization requires EVERY nested Mach-O to be individually signed with
the Developer ID, timestamped, and under the Hardened Runtime. That includes the
`magi` executable and every `.dylib`/`.so` under
`Resources/magi/_internal/`. Tauri copies `bundle.resources` (our
`binaries/magi` onedir) into the app but does NOT recurse into a resource tree
to sign it (it only signs the main binary, configured frameworks, and
`externalBin`). So the pipeline runs `packaging/deep-sign-sidecar.sh` first,
which signs the tree **inside-out** (every nested dylib/so, then the `magi`
executable last, per Apple's inside-out rule) with
`codesign --force --options runtime --timestamp --entitlements entitlements.sidecar.plist --sign "$APPLE_SIGNING_IDENTITY"`.
Tauri then signs the outer `.app`, sealing the already-signed tree, and
notarizes + staples it.

Signing is driven by env: `tauri.conf.json` leaves `signingIdentity` unset, so
the CLI reads `APPLE_SIGNING_IDENTITY` from the environment; nothing in this
repo embeds a signing secret or identity string.

### Entitlements rationale

Two files, both valid plists:

- `entitlements.plist` (the app, referenced by `tauri.conf.json`) is an EMPTY
  dictionary: the Rust/WKWebView shell requests no Hardened Runtime exceptions.
- `entitlements.sidecar.plist` (the frozen-Python child) grants exactly three
  exceptions, applied only to the sidecar process during deep-signing:
  - `com.apple.security.cs.allow-jit` - CPython/native deps allocate executable
    memory.
  - `com.apple.security.cs.allow-unsigned-executable-memory` - PyInstaller's
    bootloader executes the interpreter from unsigned memory pages.
  - `com.apple.security.cs.disable-library-validation` - the `_internal` tree
    loads many dylibs and `dlopen()`s plugins at runtime.

  These are the standard, unavoidable set for shipping a notarized frozen-Python
  binary under the Hardened Runtime. They relax memory-integrity and
  library-provenance guarantees for the SIDECAR process only; the shell keeps
  the full Hardened Runtime and the runtime binds loopback only.

### How the `.dmg` is produced

The workflow prefers the tauri-built `.dmg`. Because tauri's dmg step
(`bundle_dmg`, AppleScript) can be flaky in headless CI, if no tauri dmg is
present it falls back to `hdiutil create` from the signed, notarized, stapled
`.app` plus an `/Applications` symlink. Either way the final dmg is normalized
to `Open-Magi_<version>_aarch64.dmg`, then notarized (`xcrun notarytool submit
--wait`) and stapled (`xcrun stapler staple`) so the download itself passes
offline Gatekeeper.

### Homebrew cask

`dist/homebrew/open-magi.rb` is the cask (source of truth). After the first
release, pin its `sha256` and copy it into the tap repo
`openmagi/homebrew-tap` under `Casks/`. See `dist/homebrew/README.md` for the
exact digest-and-copy steps. Then:

```
brew install --cask openmagi/tap/open-magi
```

## Signing and notarization (local)

Code signing (macOS notarization, Windows Authenticode) is a release-pipeline
step, not a local-build concern. `cargo tauri build` locally produces an
ad-hoc-signed app (no identity), which launches on the build machine but is not
distributable. Configure the secrets above in CI for a distributable build;
nothing in this repo embeds any signing secret.

## Security posture

- The runtime binds loopback only. The shell launches the serve runtime as
  `<bin> --host 127.0.0.1 --port <port>`, so the agent (which uses a well-known
  local dev token) is NOT reachable from the LAN.
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
