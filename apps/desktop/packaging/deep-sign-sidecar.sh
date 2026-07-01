#!/usr/bin/env bash
#
# Deep-sign the bundled PyInstaller `magi` onedir tree, inside-out, under the
# Hardened Runtime, BEFORE `cargo tauri build` bundles it.
#
# Why this exists (and why tauri alone is not enough):
#   Apple notarization requires EVERY nested Mach-O to be individually signed
#   with the Developer ID, timestamped, and under the Hardened Runtime. Tauri
#   copies `bundle.resources` (our `binaries/magi` onedir) into
#   Contents/Resources/magi/ but does NOT recurse into a resource tree to sign
#   it (it only signs the main binary, configured frameworks, and externalBin).
#   So an unsigned .dylib/.so under _internal/ would fail notarization. We sign
#   the whole tree here first; tauri then seals it inside the already-signed
#   outer .app.
#
# Order matters: Apple requires inside-out signing (nested code first, the
# containing executable last). We sign every dylib/so first, then the `magi`
# executable, then any other stray Mach-O.
#
# Usage:
#   APPLE_SIGNING_IDENTITY="Developer ID Application: ... (TEAMID)" \
#     packaging/deep-sign-sidecar.sh binaries/magi packaging/entitlements.sidecar.plist
#
# Args:
#   $1  path to the onedir tree (default: binaries/magi)
#   $2  path to the sidecar entitlements plist (default: entitlements.sidecar.plist)
#
# Env:
#   APPLE_SIGNING_IDENTITY  required. The Developer ID Application identity (or
#                           its SHA-1) present in the keychain.
set -euo pipefail

TREE="${1:-binaries/magi}"
ENTITLEMENTS="${2:-entitlements.sidecar.plist}"

if [ -z "${APPLE_SIGNING_IDENTITY:-}" ]; then
  echo "error: APPLE_SIGNING_IDENTITY is not set" >&2
  exit 1
fi
if [ ! -d "$TREE" ]; then
  echo "error: sidecar tree not found: $TREE" >&2
  exit 1
fi
if [ ! -f "$ENTITLEMENTS" ]; then
  echo "error: sidecar entitlements not found: $ENTITLEMENTS" >&2
  exit 1
fi

EXE="$TREE/magi"
if [ ! -f "$EXE" ]; then
  echo "error: onedir executable not found: $EXE" >&2
  exit 1
fi

sign_one() {
  # --force: re-sign even if a page already carries an ad-hoc signature.
  # --options runtime: Hardened Runtime. --timestamp: secure timestamp (needed
  # for notarization). --entitlements: the three frozen-Python exceptions.
  codesign --force --options runtime --timestamp \
    --entitlements "$ENTITLEMENTS" \
    --sign "$APPLE_SIGNING_IDENTITY" \
    "$1"
}

echo "==> deep-signing sidecar tree: $TREE"
echo "    identity: $APPLE_SIGNING_IDENTITY"
echo "    entitlements: $ENTITLEMENTS"

# 1) Inside-out: every nested Mach-O (.dylib / .so / .dylib.N) first. We detect
#    Mach-O by magic via `file`, not by extension, so we catch extension-less
#    shared objects too. Skip the main `magi` exe here; it is signed last.
count=0
while IFS= read -r -d '' f; do
  [ "$f" = "$EXE" ] && continue
  if file -b "$f" | grep -q 'Mach-O'; then
    sign_one "$f"
    count=$((count + 1))
  fi
done < <(find "$TREE" -type f \
  \( -name '*.dylib' -o -name '*.so' -o -name '*.so.*' -o -name '*.dylib.*' -o -perm -u+x \) \
  -print0)
echo "    signed $count nested Mach-O files"

# 2) Finally the containing executable.
sign_one "$EXE"
echo "    signed executable: $EXE"

# 3) Verify the exe seals under the strict, notarization-equivalent policy.
codesign --verify --strict --verbose=2 "$EXE"
echo "==> sidecar deep-sign complete"
