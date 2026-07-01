# Bundled `magi` runtime (PyInstaller onedir)

This directory holds the standalone serve runtime that the desktop app ships.
It is built with PyInstaller in **`--onedir`** mode, which produces a DIRECTORY
(not a single file): a `magi/` folder containing the `magi` executable plus an
`_internal/` tree of its dependencies. The executable is the SERVE entry
(`magi_agent.main:main`), invoked as `<bin> --host 127.0.0.1 --port <port>`
(no `serve` subcommand). It is named `magi` only because PyInstaller's
`--name magi` sets the binary name; it is NOT the Typer CLI.

Because onedir is a directory, it is shipped as a Tauri **`bundle.resources`**
entry (NOT `externalBin`, which is single-file per target triple and cannot
carry a dependency tree). The mapping in `../tauri.conf.json` is:

```json
"resources": { "binaries/magi": "magi" }
```

so the tree lands at `<resource_dir>/magi/` in the packaged app, with the
executable at `<resource_dir>/magi/magi`.

## Validated build recipe

This is the proven recipe (a spike built it and ran `magi-agent --help`
successfully; the resulting onedir was ~363 MB):

```
pyinstaller \
  --onedir \
  --name magi \
  --collect-data magi_agent \
  --collect-submodules magi_agent \
  --collect-all litellm \
  --collect-all rdflib \
  --collect-all pyshacl \
  --collect-all tiktoken \
  --collect-submodules tiktoken_ext \
  --hidden-import tiktoken_ext.openai_public \
  --copy-metadata tiktoken \
  --hidden-import uvicorn \
  <entrypoint that calls magi_agent.main:main>
```

Then place the produced `dist/magi/` directory here as `binaries/magi/` (so
`binaries/magi/magi` is the executable) before `cargo tauri build`. Build it
once per target platform.

## Runtime resolution and the PATH fallback

At runtime the shell resolves the binary in this order (see
`core/src/server.rs::resolve_magi_binary`):

1. the bundled onedir executable, `<resource_dir>/magi/magi`,
2. the `MAGI_BIN` environment override,
3. `~/.magi/bin/magi-agent` (the serve console script),
4. `~/.magi/bin/magi` (secondary),
5. `magi-agent` on `PATH` (brew's serve console script),
6. `magi` on `PATH` (secondary; the Typer CLI).

The system fallback prefers `magi-agent` (the SERVE console script). The brew
`magi` command is the Typer CLI and has NO serve command, so it is only a
secondary fallback.

Homebrew installs land on `PATH` (`brew install openmagi/tap/magi-agent`), so
the bundled tree is OPTIONAL for those users: if `magi-agent` is already on
`PATH`, the shell uses it and no resource tree needs to ship.

The actual onedir trees are NOT committed (they are large, per-platform build
artifacts produced by the release pipeline).
