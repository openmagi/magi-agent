#!/usr/bin/env bash
# Sync the canonical Open Magi design system into each consumer repo.
#
# One-way copy: design-system/ (in this magi-agent repo) -> each repo's
# vendored components/ui/_ds/. Mirrors the house build-web-dashboard.sh
# pattern (one-way, committed snapshot). Consumer repos must be checked out
# side-by-side under the same parent directory as magi-agent.
#
# Run from anywhere:  bash scripts/sync-design-system.sh
#
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"          # magi-agent repo root
DS="$ROOT/design-system"
PARENT="$(cd "$ROOT/.." && pwd)"                  # holds magi-agent, clawy, magi-control-plane
BUNDLE="$DS/ui"

VERSION="$(node -e "process.stdout.write(require('$DS/MANIFEST.json').version)")"
echo "design-system version: $VERSION"

# sha256 helper (macOS shasum / linux sha256sum)
sha256() {
  if command -v shasum >/dev/null 2>&1; then shasum -a 256 "$1" | awk '{print $1}';
  else sha256sum "$1" | awk '{print $1}'; fi
}

HEADER_LINE1="GENERATED FILE — DO NOT EDIT."
HEADER_LINE2="Source: magi-agent/design-system. Regenerate via scripts/sync-design-system.sh."

# slice a sentinel-delimited region out of tokens.css (inclusive of contents,
# exclusive of the sentinel lines themselves).
slice_block() {  # $1 = block name (core|brand)
  awk -v b="$1" '
    $0 ~ ("@ds:" b " BEGIN") { inb=1; next }
    $0 ~ ("@ds:" b " END")   { inb=0 }
    inb { print }
  ' "$DS/tokens.css"
}

sync_one() {
  local repo="$1" dsrel="$2" brand="$3"
  local repo_root="$PARENT/$repo"
  local dest="$repo_root/$dsrel"

  if [ ! -d "$repo_root" ]; then
    echo "  SKIP $repo (not found at $repo_root)"
    return
  fi

  echo "→ $repo  ($dsrel, brand=$brand)"
  rm -rf "$dest"
  mkdir -p "$dest"

  # 1. copy primitive bundle with GENERATED header
  for f in "$BUNDLE"/*.ts "$BUNDLE"/*.tsx; do
    local base; base="$(basename "$f")"
    {
      echo "/* $HEADER_LINE1"
      echo "   $HEADER_LINE2 */"
      cat "$f"
    } > "$dest/$base"
  done

  # 2. tokens.css (core block is already wrapped in @theme{}; brand for clawy)
  {
    echo "/* $HEADER_LINE1"
    echo "   $HEADER_LINE2"
    echo "   DS_VERSION: $VERSION */"
    slice_block core
    if [ "$brand" = "1" ]; then
      echo ""
      echo "/* landing brand extension */"
      slice_block brand
    fi
  } > "$dest/tokens.css"

  # 3. version stamp
  echo "$VERSION" > "$dest/.ds-version"

  # 4. manifest of sha256 over every vendored file (sorted, stable)
  ( cd "$dest" && \
    for vf in $(ls -1 | grep -vE '^(MANIFEST\.sha256)$' | sort); do
      printf "%s  %s\n" "$(sha256 "$vf")" "$vf"
    done ) > "$dest/MANIFEST.sha256"

  local n; n="$(ls -1 "$dest" | grep -vE '^(MANIFEST\.sha256|\.ds-version)$' | wc -l | tr -d ' ')"
  echo "  wrote $n files + manifest"
}

sync_one "magi-agent"             "apps/web/src/components/ui/_ds" "0"
sync_one "clawy"                  "src/components/ui/_ds"          "1"
sync_one "magi-control-plane/web" "components/ui/_ds"             "0"

echo ""
echo "Done. Review and commit each repo separately."
echo "Each repo's CI runs check-ds-drift.mjs to guard the vendored snapshot."
