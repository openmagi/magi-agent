#!/usr/bin/env bash
# Naming-leak ratchet gate (16-module-diet PR10).
#
# Blocks NEW occurrences of legacy brand names in magi_agent/**/*.py while
# tolerating the already-baselined ones (additive-only ratchet):
#   - a file NOT in the baseline that contains a pattern  -> FAIL
#   - a baselined file whose count rises above its max    -> FAIL
#   - counts at or below the baseline                     -> PASS
#     (drops below baseline emit a non-blocking warning: ratchet down)
#
# Usage:
#     bash scripts/check_naming.sh            # gate (same command in CI)
#     bash scripts/check_naming.sh --update   # regenerate the baseline
#
# Cleanup PRs (e.g. the PR8 openmagi/openclaw purge) should re-run --update in
# the same PR so the baseline ratchets down and stays equal to reality.
set -euo pipefail

cd "$(git rev-parse --show-toplevel)"

BASELINE="scripts/naming_baseline.tsv"
PATHSPEC=':(glob)magi_agent/**/*.py'
TAB=$'\t'

# "<pattern>|<extra git-grep flags>" — fixed-string match, scoped to PATHSPEC.
PATTERNS=(
    'openmagi|-i'
    'openclaw|-i'
    'clawy|-i'
    'CORE_AGENT_|'
)

scan_pattern() { # $1=pattern $2=flags -> "pattern<TAB>file<TAB>count" lines
    local pattern="$1" flag="$2" out
    if [ -n "$flag" ]; then
        out="$(git grep --untracked -c -F "$flag" -e "$pattern" -- "$PATHSPEC" || true)"
    else
        out="$(git grep --untracked -c -F -e "$pattern" -- "$PATHSPEC" || true)"
    fi
    [ -z "$out" ] && return 0
    printf '%s\n' "$out" | awk -F: -v p="$pattern" '{printf "%s\t%s\t%s\n", p, $1, $2}'
}

current="$(
    for spec in "${PATTERNS[@]}"; do
        scan_pattern "${spec%%|*}" "${spec##*|}"
    done | LC_ALL=C sort -t "$TAB" -k1,1 -k2,2
)"

if [ "${1:-}" = "--update" ]; then
    {
        echo "# Naming-leak ratchet baseline (auto-generated — do not edit by hand)."
        echo "# Format: pattern<TAB>file<TAB>max_occurrences (git grep -c -F, magi_agent/**/*.py)."
        echo "# Regenerate: bash scripts/check_naming.sh --update"
        echo "# scripts/check_naming.sh fails on files/counts above this baseline;"
        echo "# cleanup PRs should re-run --update so the baseline ratchets down."
        printf '%s\n' "$current"
    } > "$BASELINE"
    echo "baseline updated: $BASELINE"
    exit 0
fi

if [ ! -f "$BASELINE" ]; then
    echo "Naming-leak gate FAILED: missing $BASELINE" >&2
    echo "Generate it with: bash scripts/check_naming.sh --update" >&2
    exit 1
fi

report="$(printf '%s\n' "$current" | awk -F'\t' -v base="$BASELINE" '
    BEGIN {
        while ((getline line < base) > 0) {
            if (line ~ /^#/ || line == "") continue
            n = split(line, f, "\t")
            if (n == 3) max[f[1] "\t" f[2]] = f[3] + 0
        }
        close(base)
    }
    NF == 3 {
        key = $1 "\t" $2
        seen[key] = $3 + 0
        if (!(key in max)) {
            printf "VIOL\t%s: %d new \"%s\" occurrence(s) (file not in baseline)\n", $2, $3, $1
        } else if ($3 + 0 > max[key]) {
            printf "VIOL\t%s: \"%s\" count %d exceeds baseline %d\n", $2, $1, $3, max[key]
        }
    }
    END {
        for (key in max) {
            split(key, k, "\t")
            if (!(key in seen)) {
                printf "IMPR\t%s: \"%s\" gone (baseline %d)\n", k[2], k[1], max[key]
            } else if (seen[key] < max[key]) {
                printf "IMPR\t%s: \"%s\" %d -> %d\n", k[2], k[1], max[key], seen[key]
            }
        }
    }
')"

violations="$(printf '%s\n' "$report" | grep '^VIOL' | cut -f2- || true)"
improvements="$(printf '%s\n' "$report" | grep '^IMPR' | cut -f2- || true)"

if [ -n "$violations" ]; then
    {
        echo "Naming-leak gate FAILED — new legacy-name occurrences detected:"
        echo
        printf '%s\n' "$violations"
        echo
        echo "New code must not introduce openmagi/openclaw/clawy/CORE_AGENT_ names"
        echo "(use Magi/magi_agent/MAGI_* instead). If an occurrence is genuinely"
        echo "unavoidable, ratchet the baseline in the same PR and justify it:"
        echo
        echo "    bash scripts/check_naming.sh --update"
    } >&2
    exit 1
fi

if [ -n "$improvements" ]; then
    echo "::warning::naming baseline is stale-high; lock in the cleanup by re-running 'bash scripts/check_naming.sh --update' and committing scripts/naming_baseline.tsv"
    printf '%s\n' "$improvements"
fi

for spec in "${PATTERNS[@]}"; do
    p="${spec%%|*}"
    total="$(printf '%s\n' "$current" | awk -F'\t' -v p="$p" '$1 == p {s += $3} END {print s + 0}')"
    echo "naming gate OK: ${p} = ${total} occurrence(s) (within baseline)"
done
