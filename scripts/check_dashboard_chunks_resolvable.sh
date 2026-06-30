#!/usr/bin/env bash
# Guard: every "/_next/static/chunks/<hash>.(js|css)" reference inside the
# committed magi_agent/web_dashboard/ bundle MUST point at a chunk that
# actually exists on disk.
#
# Failure mode this catches: PR #1147 (v0.1.92) shipped a wheel where the
# rebuilt bundle's HTML/manifests referenced chunk hashes that were not in
# the committed tree (Next.js standalone build + the cp -R in
# scripts/build-web-dashboard.sh produced an inconsistent bundle). Operators
# upgrading from 0.1.91 to 0.1.92 hit a blank dashboard because the browser
# 404'd every chunk; the regression survived through 0.1.93 because the
# existing freshness gate only checks "src changed implies bundle changed",
# not "bundle is internally consistent".
#
# Logic:
#   1. Scan every text-ish file under magi_agent/web_dashboard/ (.html, .txt,
#      .css, .js, .json, .webmanifest, .xml) for the string
#      "_next/static/chunks/<basename>.(js|css)".
#   2. Deduplicate the basenames.
#   3. For each basename, assert
#      magi_agent/web_dashboard/_next/static/chunks/<basename> exists.
#   4. If any are missing, print the missing chunks plus the files that
#      reference each, and exit non-zero.
#
# Allowed exceptions: none. ANY referenced chunk must exist on disk. If a
# rebuild legitimately removes a chunk, the HTML/manifest that references it
# must be regenerated or removed in the same commit.

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BUNDLE="$ROOT/magi_agent/web_dashboard"
CHUNKS_DIR="$BUNDLE/_next/static/chunks"

if [ ! -d "$BUNDLE" ]; then
  echo "::warning::$BUNDLE does not exist; gate is a no-op." >&2
  exit 0
fi

if [ ! -d "$CHUNKS_DIR" ]; then
  cat >&2 <<EOF
::error::$CHUNKS_DIR does not exist.

The web dashboard bundle is committed but the _next/static/chunks/ directory
is missing entirely. Rebuild the bundle:

  bash scripts/build-web-dashboard.sh
  git add magi_agent/web_dashboard
  git commit
EOF
  exit 1
fi

# Collect every referenced chunk basename (deduplicated, sorted).
# Pattern matches "_next/static/chunks/<basename>.(js|css)" with optional
# leading slash. Basename charset matches Next.js / Turbopack chunk hashes:
# lowercase letters, digits, underscore, hyphen.
REFERENCED="$(
  grep -rohE '_next/static/chunks/[A-Za-z0-9_\-]+\.(js|css)' \
    --include='*.html' \
    --include='*.txt' \
    --include='*.css' \
    --include='*.js' \
    --include='*.json' \
    --include='*.webmanifest' \
    --include='*.xml' \
    "$BUNDLE" 2>/dev/null \
    | sed 's|^.*_next/static/chunks/||' \
    | sort -u || true
)"

if [ -z "$REFERENCED" ]; then
  echo "No /_next/static/chunks/ references found in $BUNDLE - gate skipped."
  exit 0
fi

MISSING=""
while IFS= read -r basename; do
  [ -z "$basename" ] && continue
  if [ ! -f "$CHUNKS_DIR/$basename" ]; then
    MISSING="${MISSING}${basename}"$'\n'
  fi
done <<< "$REFERENCED"

if [ -z "$MISSING" ]; then
  count="$(printf '%s\n' "$REFERENCED" | grep -c . || true)"
  echo "All ${count} referenced dashboard chunks exist on disk - gate passes."
  exit 0
fi

{
  echo "::error::web_dashboard bundle references chunks that do not exist on disk."
  echo
  echo "The committed magi_agent/web_dashboard/ bundle is shipped verbatim"
  echo "inside the published wheel and served at /dashboard. Browsers will"
  echo "404 every missing chunk and the dashboard will render blank."
  echo
  echo "Missing chunks (referenced from HTML/manifests but absent under"
  echo "magi_agent/web_dashboard/_next/static/chunks/):"
  echo
  while IFS= read -r basename; do
    [ -z "$basename" ] && continue
    echo "  - $basename"
    # Show the first few files that reference each missing chunk so the
    # contributor can see what HTML/route is broken.
    refs="$(
      grep -rlE "_next/static/chunks/${basename}([^A-Za-z0-9_\-]|\$)" \
        --include='*.html' \
        --include='*.txt' \
        --include='*.css' \
        --include='*.js' \
        --include='*.json' \
        --include='*.webmanifest' \
        --include='*.xml' \
        "$BUNDLE" 2>/dev/null | head -5 || true
    )"
    if [ -n "$refs" ]; then
      while IFS= read -r f; do
        rel="${f#$ROOT/}"
        echo "      referenced by: $rel"
      done <<< "$refs"
    fi
  done <<< "$MISSING"
  echo
  echo "Fix:"
  echo "  bash scripts/build-web-dashboard.sh"
  echo "  git add magi_agent/web_dashboard"
  echo "  git commit"
  echo
  echo "If a rebuild legitimately dropped a chunk, the HTML/manifest that"
  echo "references it must be regenerated or removed in the same commit."
} >&2

exit 1
