# Pack Manifest Reference (`pack.toml`)

A pack is a directory containing a `pack.toml` manifest. The manifest declares
its `provides` entries **statically**, so the catalog is built without importing
any pack code (impls are lazy-imported at registration time). Schema source:
`magi_agent/packs/manifest.py` (`PackManifest`, `ProvidesEntry` — pydantic v2,
frozen, `extra="forbid"`, camelCase aliases).

## Discovery

Packs are discovered by globbing `pack.toml` under three bases, in priority
order (`magi_agent/packs/discovery.py`):

1. bundled first-party: `magi_agent/firstparty/packs/`
2. user home: `~/.magi/packs/`
3. project: `<cwd>/.magi/packs/`

`config.toml` `[packs]` controls the set: `disable = ["<packId>"]` removes a
pack (this is also how you *remove/forbid* a first-party pack), `order = [...]`
pins load order (and opts a `defaultEnabled = false` pack back in). On a
colliding `(type, ref)` the **last** pack in resolved order wins, and packs
resolve in base-precedence order — bundled first-party, then `~/.magi/packs/`,
then `<cwd>/.magi/packs/` — so a pack in your user or project dir overrides
first-party by default. First-party holds no privilege: it is discovered,
overridable, and removable exactly like your packs.

Impl module paths resolve with zero env setup: the loader auto-appends the
discovered pack's parent directory to `sys.path` when the impl's top-level
module lives there. Keep pack directory names unique across your pack roots.

## Top-level fields

| Field | Type | Required | Notes |
|---|---|---|---|
| `packId` | string | yes | Non-empty, globally unique (e.g. `user.my-check`). |
| `displayName` | string | yes | Human-readable name. |
| `version` | string | no (default `"1"`) | Free-form version string. |
| `description` | string | no (default `""`) | One-line summary. |
| `defaultEnabled` | bool | no (default `true`) | `false` packs load only when pinned in `[packs] order`. |
| `provides` | array of tables | no (default empty) | The `[[provides]]` entries below. Duplicate `ref` within one pack is rejected. |

## `[[provides]]` entries

| Field | Type | Notes |
|---|---|---|
| `type` | string | One of the 8 provides types: `tool`, `callback`, `validator`, `harness`, `control_plane`, `evidence_producer`, `recipe`, `connector`. |
| `ref` | string | Non-empty public ref this entry contributes (e.g. `verifier:myCheck@1`). |
| `impl` | string | `"module.path:symbol"` — required for every type except `recipe`; mutually exclusive with `spec`. |
| `spec` | string | Relpath to a declarative spec file — required for `recipe`, forbidden elsewhere. |
| `priority` | int | Ordered types only (`callback`, `control_plane`): ascending registration order. |
| `phase` | string | Ordered types only: free-form phase label (e.g. `"loop"`, `"beforeTurnStart"`). |
| `gatePosition` | `"before"` \| `"after"` | `control_plane` only; defaults to `"after"` the permission gate. A before_tool-deciding control MUST opt into `"before"` explicitly (the dispatcher raises `GatePositionViolation` otherwise). |

Validation rules (enforced by the model validator):

- `recipe` entries must declare `spec` and not `impl`; every other type must
  declare `impl` and not `spec`.
- `impl` must be of the form `module.path:symbol`.
- `priority`/`phase` are rejected on non-ordered types; `gatePosition` is
  rejected on non-`control_plane` types.

## Catalog mapping

Loaded refs land in the flat live catalog (`magi_agent/packs/catalog_build.py`),
with no first-party-only tier: `tool` → `toolRefs`, `connector` →
`connectorRefs`, `validator` → `validatorRefs`, `harness` → `harnessRefs`,
`evidence_producer` → `evidenceProducerRefs`, `control_plane` + `callback` →
`pluginRefs`. `recipe` entries register their spec instead of a catalog ref.
The static default reference floor is preserved (unioned in) so existing recipe
refs keep validating even with no packs on disk.

## Example (a bundled first-party manifest — same format you use)

    packId = "openmagi.tools-clock"
    displayName = "Clock tool"
    version = "1.0.0"
    description = "First-party Clock tool bundled as a removable pack (no privilege)."

    [[provides]]
    type = "tool"
    ref = "Clock"
    impl = "magi_agent.firstparty.packs.tools_clock.impl:provide_clock"

Scaffold any of the 8 types with `magi pack new <type> <name>`.
