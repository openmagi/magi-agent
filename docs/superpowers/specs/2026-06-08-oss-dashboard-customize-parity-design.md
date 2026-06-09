# OSS Dashboard — Customize Tab Parity (with Hosted Web)

- Date: 2026-06-08
- Repo: `openmagi/magi-agent` (OSS runtime + `apps/web` static dashboard)
- Status: Design approved (brainstorming). Implementation pending (Phase 1 first).

## Goal

Make the OSS local dashboard's **Customize** tab match the hosted web app's
Customize tab in look-and-feel and behavior, and keep **Skills** as a separate
tab (it already is, in both products). Parity must be *honest*: every control
the user sees must map to something the OSS local runtime actually persists and
enforces — no fake toggles.

## Background

Hosted web (`clawy` monorepo, `src/components/dashboard/customize/`) Customize
tab is a friendly two-card UX:

1. **Verification Rules** card → modal with ~24 builtin "presets" in 7
   categories (each toggleable with a mode hybrid/deterministic/llm), a custom
   rule wizard (hook point + condition + check + fail behavior), and always-on
   security hooks. Persists to Supabase (`bots.agent_rules`,
   `bots.agent_config.builtin_presets`) via `PATCH /api/bots/[botId]`.
2. **Custom Tools** card → modal listing tools with enable/disable, plus
   add/delete custom tools. Persists via `/api/bots/[botId]/tools`.

OSS (`apps/web/src/components/dashboard/customize/customize-tab.tsx`) currently
renders a read-mostly "Python ADK runtime console" (recipes, harness presets,
phase routing, tools/skills introspection) — different shape, mostly static
catalog data with graceful fallback.

The two backends differ fundamentally:
- Hosted = multi-tenant SaaS, Supabase persistence, per-bot rows.
- OSS = single local runtime, `RuntimeConfig` is a **frozen Pydantic model**
  parsed from env at startup (`magi_agent/config/models.py`,
  `magi_agent/config/env.py`); there is **no runtime config-write path**.

So the frontend is portable, but "actually applies locally" requires a new
persistence + apply layer in the Python runtime.

## Decisions (locked during brainstorming)

1. **Parity level**: full UX parity **and** it actually applies on the OSS local
   runtime (not visual-only, not local-storage-only).
2. **Verification Rules content**: keep the hosted two-card UX shell, but fill
   the Verification Rules card with **OSS's real primitives** — recipes
   (`recipes/first_party/`), harness presets, hooks (`hooks/registry.py`),
   gates — that the runtime genuinely enforces. No 1:1 copy of hosted's 24
   presets, no placeholder toggles.
3. **Custom Tools scope**: enable/disable existing tools **+ persist** that
   state. **No** custom-tool *creation* (name/description/body) — adding new
   capability is already covered by the separate Skills tab (custom skills),
   and inline tool bodies are an RCE surface we will not open.
4. **Skills**: stays a separate tab. No change required to the Skills tab for
   this work beyond not regressing it.
5. **Persistence approach (Approach A)**: a dedicated overrides file
   `~/.magi/customize.json`, **not** writes into `config.toml` (avoids
   clobbering hand-edited toml/comments and keeps `RuntimeConfig` frozen).
   Changes apply at runtime startup / explicit reload — **not** live hot-apply
   of a running runtime (rejected as YAGNI-violating and concurrency-risky).
6. **Gating**: new behavior lands behind a default-OFF gate per OSS convention;
   each phase is an independent PR.

## Architecture

```
apps/web (Next.js static export)
  customize-tab.tsx ──HTTP──> magi_agent/transport/customize.py ──> customize/store.py
    Verification Rules card        GET/PATCH /v1/app/customize        (~/.magi/customize.json)
    Custom Tools card              + per-item enable/disable                 │
                                            │                         customize/catalog.py
                                            │                         (OSS primitives → UI)
                                            ▼
                                   customize/apply.py (startup)
                                   → RuntimeProfile / HookRegistry / ToolHost
```

### Components & boundaries

- **`magi_agent/customize/store.py`** — overrides I/O only. Loads/merges/writes
  `~/.magi/customize.json`. Knows nothing about the runtime. Missing file ⇒
  empty overrides. Atomic writes (write-temp-then-rename).
- **`magi_agent/customize/catalog.py`** — read-only reflection of OSS real
  primitives into a UI-shaped catalog: recipes, harness presets, hooks (with
  enforce metadata + which are always-on/security and therefore non-toggleable),
  tools. No mutation.
- **`magi_agent/customize/apply.py`** (Phase 2) — pure function from
  `overrides → runtime registries`. Called on startup: enables/disables hooks
  and tools, marks active recipes/presets. Ignores always-on/security hooks.
- **`magi_agent/transport/customize.py`** — HTTP surface. Reuses the existing
  auth guard pattern from `transport/tools.py` (`_unauthorized_response`).
  Registered with one line in the app wiring (mirrors
  `register_tool_admin_routes`).
- **`apps/web/.../customize/*`** — port hosted's `customize-tab.tsx`,
  `verification-rule-modal.tsx`, `custom-tool-modal.tsx`, fed by
  `/v1/app/customize` instead of Supabase. Preset/rule items are OSS catalog
  items.

### `~/.magi/customize.json` schema (draft)

```json
{
  "verification": {
    "recipes": ["research", "coding_evidence_gate"],
    "harness_presets": ["answer_quality", "fact_grounding"],
    "hooks": { "some_hook_name": false },
    "custom_rules": []
  },
  "tools": { "web_fetch": true, "shell": false }
}
```

- `hooks` / `tools` maps store only *overrides* (deltas) from defaults.
- Security / always-on hooks are never written here and are ignored if present.
- `custom_rules` stays empty until Phase 3.

### Data flow

- **Read**: `GET /v1/app/customize` → `{ catalog, overrides }`. `catalog` is the
  live OSS primitive set with enforce metadata; `overrides` is the stored file.
  Frontend renders the merged view (catalog item + its current on/off/mode).
- **Write**: `PATCH /v1/app/customize` (or per-item enable/disable POST) merges
  into the overrides file. Response includes a `reloadRequired: true` hint.
- **Apply**: on runtime boot, `apply.py` reads overrides and configures
  `RuntimeProfile` / `HookRegistry` / `ToolHost`. Existing
  `/api/tools/{name}/enable|disable` continue to work for the current session;
  the overrides file makes the choice durable across restarts.

## Phasing

### Phase 1 — Read/visual parity (frontend + read endpoint)
- `customize/store.py` (load/merge only; no write yet).
- `customize/catalog.py` (OSS primitives → UI catalog).
- `transport/customize.py` with `GET /v1/app/customize`; register route.
- `apps/web`: replace the runtime-console Customize layout with the hosted
  two-card layout (Verification Rules + Custom Tools), data from
  `/v1/app/customize`. Toggles render; client-side state only (no persistence
  yet) or shown as read-only with a "coming in next phase" affordance.
- **Done when**: screen matches hosted; both cards present; catalog reflects
  real OSS recipes/presets/hooks/tools.

### Phase 2 — Persistence + apply (write endpoint + startup apply)
- `store.py`: atomic write.
- `transport/customize.py`: `PATCH /v1/app/customize` + per-item enable/disable.
- `customize/apply.py`: startup apply into registries; security/always-on
  excluded.
- Frontend: wire Save / toggles to the write endpoint; surface "reload required"
  UX (no hot-apply).
- **Done when**: toggles persist to disk and actually enforce after restart.

### Phase 3 — Custom rule wizard → hook compilation (follow-up, optional)
- Port hosted's custom-rule wizard (hook point + condition + check + fail
  behavior) and compile each rule into an OSS hook spec stored in
  `custom_rules[]`, registered at startup.
- Highest uncertainty (rule→hook compilation semantics); deferred. Phases 1–2
  already deliver "same screen as hosted + real, durable toggles."

## What exists vs. what's new (grounding)

Exists in OSS today:
- Tool enable/disable endpoints: `GET /api/tools`,
  `POST /api/tools/{name}/enable|disable` (`magi_agent/transport/tools.py`),
  with an auth guard — but state is **in-memory / per-session**, not persisted.
- Hook registry with `register/replace/unregister/enable/disable`
  (`magi_agent/hooks/registry.py`) — enforcement infra is real, but **no HTTP
  mutation endpoints** and **no persisted state**.
- First-party recipes (`recipes/first_party/`) and harness presets — selected at
  startup via `MAGI_RUNTIME_PROFILE`; **not runtime-selectable** today.

New in this work:
- `~/.magi/customize.json` persistence + `customize/` package.
- `GET/PATCH /v1/app/customize` (+ per-item) endpoints.
- Startup apply of overrides into profile/registries.
- Frontend two-card Customize parity.

## Non-goals

- No Supabase / multi-tenant concepts in OSS.
- No custom-tool *creation* / inline code execution.
- No live hot-apply to a running runtime.
- No change to the Skills tab beyond non-regression.
- No 1:1 reproduction of hosted's 24-preset catalog.

## Risks / open items (resolve during Phase 1 planning)

- Exact current OSS endpoint surface for `/v1/app/config` and `/v1/app/skills`
  is uncertain (the existing tab uses local fallback catalog data). Confirm
  precise handlers before wiring, to avoid duplicate/competing routes.
- Confirm OSS config home dir (`~/.magi/` assumed) and reuse the same resolver
  the runtime already uses for config.toml.
- Hook catalog must clearly flag which hooks are security/always-on
  (non-toggleable) so the UI renders them like hosted's "Security (Always On)"
  section.
- Auth: reuse `transport/tools.py`'s guard so the new endpoints match existing
  local-dev token behavior.
