# Homebrew cask: `open-magi`

`open-magi.rb` is the desktop-app cask. It is kept here in the app repo as the
source of truth and copied into the tap repo `openmagi/homebrew-tap` under
`Casks/open-magi.rb` to publish.

Install (once published):

```
brew install --cask openmagi/tap/open-magi
```

## Finalize after the first release

The cask ships with `sha256 :no_check` so it is installable immediately after a
release, but pinning the digest is strongly recommended. After the
`desktop-release-macos` workflow publishes a `desktop-v<version>` release:

1. Note the published dmg asset name: `Open-Magi_<version>_aarch64.dmg`.
2. Compute its digest:

   ```
   VERSION=0.1.0
   curl -L -o open-magi.dmg \
     "https://github.com/openmagi/magi-agent/releases/download/desktop-v${VERSION}/Open-Magi_${VERSION}_aarch64.dmg"
   shasum -a 256 open-magi.dmg
   ```

3. In `open-magi.rb`, bump `version` to match and replace `sha256 :no_check`
   with `sha256 "<digest>"`. The `url` is templated on `#{version}`, so no
   other edit is needed.
4. Copy the updated `open-magi.rb` into `openmagi/homebrew-tap` `Casks/` and
   commit.

## Notes

- The url and asset name are produced by `.github/workflows/desktop-release-macos.yml`
  (the dmg is normalized to `Open-Magi_<version>_aarch64.dmg` regardless of
  whether tauri or the hdiutil fallback built it), so the template stays stable.
- arm64-only for now. When an x86_64 / universal build is added, switch the
  cask to an `on_arm` / `on_intel` url block.
