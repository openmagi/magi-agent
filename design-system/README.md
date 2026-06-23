# Open Magi Design System (canonical source)

This directory is the **single source of truth** for the design tokens and UI
primitives shared across every Open Magi web surface:

| Surface | Repo | Deploy |
|---|---|---|
| OSS dashboard (`/dashboard`) | `magi-agent/apps/web` | shipped in the Python wheel |
| openmagi.ai (landing + dashboard + chat) | `clawy` | Vercel |
| cp.openmagi.ai (control-plane console) | `magi-control-plane/web` | Vercel |

## Layout

```
design-system/
  tokens.css        # the token contract — @ds:core (all) + @ds:brand (landing only)
  ui/               # the vendorable bundle (copied wholesale into each repo's _ds/)
    cn.ts           # zero-dependency clsx-compatible className composer
    Button.tsx Card.tsx Badge.tsx Input.tsx Select.tsx Switch.tsx
    Skeleton.tsx EmptyState.tsx ErrorState.tsx PageHeader.tsx KPI.tsx
    Code.tsx CopyButton.tsx Modal.tsx GlassCard.tsx
    index.ts
  MANIFEST.json     # DS_VERSION + consumer list
  README.md
```

`Logo` and `LanguageSwitcher` stay **repo-local** (they couple to each repo's
i18n provider and logo assets) and are intentionally NOT vendored.

## Golden rule

**Never edit the vendored copies** (`<repo>/.../components/ui/_ds/`). They carry
a `GENERATED — do not edit` header and a sha256 manifest; each repo's CI runs
`check-ds-drift.mjs` and fails the build if a vendored file was hand-edited.

To change anything:

1. Edit `tokens.css` and/or files under `ui/` here.
2. Bump `version` in `MANIFEST.json`.
3. Run `scripts/sync-design-system.sh` (needs the consumer repos checked out
   side-by-side under the same parent dir).
4. Review + commit each repo separately.

## How it reaches each repo

`sync-design-system.sh` copies `ui/` → `<repo>/.../components/ui/_ds/`, slices
`tokens.css` into `_ds/tokens.css` (core, plus brand for clawy), prepends the
GENERATED header, and writes `_ds/MANIFEST.sha256` + `_ds/.ds-version`.

Each repo's `globals.css` imports the tokens with one line near the top:

```css
@import "tailwindcss";
@import "<relative>/components/ui/_ds/tokens.css";
```

App code imports primitives via the repo barrel, e.g.
`import { Button, Card } from "@/components/ui"`.
