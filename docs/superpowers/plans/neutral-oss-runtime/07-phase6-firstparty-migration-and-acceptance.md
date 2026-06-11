# Phase 6 — First-Party Migration, Microkernel Shrink, Acceptance

> **For agentic workers:** REQUIRED SUB-SKILL: `superpowers:subagent-driven-development` or
> `superpowers:executing-plans`. Read `00-BLUEPRINT.md` first. This phase **depends on every prior
> phase** (P0 golden oracle, P1 loader, P2 typed-context ABI + registries, P3 validator slice, P4
> easy provides types, P5 control-plane migration). It is the **de-privileging keystone** plus final
> acceptance — do not start it until P3+P4+P5 deliverables exist and their tests are green.

**Goal:** Flip the live runtime so first-party primitives are **loaded the same way a user pack is**
(D7 keystone), the live catalog is **manifest-built** (D4), the core shrinks to the microkernel
(D6 = `{ loader, registries, typed-context dispatcher, ADK loop }`), local-trust enforces authored
rules **by default** (drop hosted staged-authority for local), and the §1 "no privilege" spec is
encoded as passing acceptance tests.

**Architecture:** First-party control/tool/validator/evidence/recipe/connector/harness/callback impls
move into bundled packs at `magi_agent/firstparty/packs/*/pack.toml` (created across P3–P5; this
phase completes the migration and **deletes the hardcode assembly**). `build_default_plugin()` /
`App(plugins=[...])` in `cli/real_runner.py` are flipped to load first-party LoopControls **through
the Phase-1 loader** — so a user pack loads in parallel with the identical mechanism. The live
catalog flips from `CompileRecipePackCatalog.default()` to `catalog_build.build_catalog_from_packs()`.

**Tech stack:** Python 3.12+, `uv`, pydantic v2 (frozen, `extra="forbid"`, `populate_by_name=True`),
Google ADK, `tomllib`, pytest. Verify headless with fake-model — **no API keys**. Every test/runtime
command is prefixed `MAGI_CONFIG="$(mktemp -d)/config.toml"` (isolates `~/.magi/config.toml`); runtime
runs use `LOCAL_DEV_MODEL_SENTINEL="local-dev"` (defined `config/env.py:58`).

---

## Pre-flight: confirm prior-phase deliverables exist

This phase consumes the following symbols from earlier phases. **First task step is always a grep.**
If any is missing, STOP — the prior phase has not landed.

- [ ] **Step 0: Verify the kernel + bundled-pack surface exists**

```bash
cd /Users/kevin/Desktop/claude_code/magi-agent/.worktrees/neutral-runtime
ls magi_agent/packs/manifest.py magi_agent/packs/discovery.py magi_agent/packs/loader.py \
   magi_agent/packs/registries.py magi_agent/packs/context.py magi_agent/packs/catalog_build.py
ls -d magi_agent/firstparty/packs/
grep -n "def load_packs\|class LoadedPacks\|class PackLoader" magi_agent/packs/loader.py
grep -n "def build_catalog_from_packs" magi_agent/packs/catalog_build.py
grep -n "class ControlPlaneContext\|def control_plane_controls\|def build_control_plane" magi_agent/packs/registries.py
```

Expected: all paths exist; `load_packs(...)` (P1), `build_catalog_from_packs(...)` (P1),
`build_control_plane(loaded_packs, ...) -> ControlPlane` (P5 registry assembly). The exact names are
fixed by P1/P5 docs; **re-grep and use the real names** if they differ — wire to what the prior phase
actually shipped, do not invent.

- [ ] **Step 1: Baseline green** (the suites this phase will modify or depend on)

```bash
MAGI_CONFIG="$(mktemp -d)/config.toml" uv run pytest \
  tests/packs/ tests/firstparty/ \
  tests/fixtures/neutral_runtime_golden/test_golden_regression.py \
  tests/adk_bridge/test_control_plane.py -q
```
Expected: all pass. If red on the pristine P5 tip, STOP and report — never build the flip on a red base.

---

## Task 6.1: Bundle remaining first-party primitives as packs (dual-load coexistence)

P3–P5 already migrated validators and the 6 controls into `magi_agent/firstparty/packs/`. This task
ensures **every** still-hardcoded first-party primitive in the `.default()` catalog has a bundled-pack
home, so 6.3's catalog flip loses nothing. Source of truth for what must exist = the refs in
`CompileRecipePackCatalog.default()` (`authoring/compiler.py:70`).

**Files:**
- Create: `magi_agent/firstparty/packs/core_authoring/pack.toml`
- Create: `magi_agent/firstparty/packs/core_authoring/impls.py`
- Test: `tests/firstparty/test_core_authoring_pack_covers_default_catalog.py`

- [ ] **Step 1: Grep the live default catalog refs (snapshot may have drifted)**

```bash
grep -n "def default" magi_agent/authoring/compiler.py
sed -n '69,91p' magi_agent/authoring/compiler.py
```
Current (`authoring/compiler.py:70-90`) the default declares these refs:
`connectorRefs=("connector.source.readonly",)`,
`toolRefs=("BrowserLive","CitationVerify","FileWrite","SourceOpen")`,
`pluginRefs=("plugin.source-review.readonly",)`,
`validatorRefs=("validator:sourceOpened@1","validator:quoteExactMatch@1")`,
`harnessRefs=("harness:authoring-static@1",)`,
`requiredEvidenceRefs=("openedSourceSnapshot","quoteDigest")`,
`evidenceProducerRefs=("evidence:source-opened@1","evidence:quote-digest@1")`,
`approvalAuthorityRefs=("authority:owner-human@1",)`.
**Re-read** — wire to whatever refs are actually present.

- [ ] **Step 2: Write the failing coverage test** — every default-catalog ref must be `provides`-d by
  some bundled first-party pack (so the flat catalog covers the legacy default).

```python
# tests/firstparty/test_core_authoring_pack_covers_default_catalog.py
from pathlib import Path

from magi_agent.authoring.compiler import CompileRecipePackCatalog
from magi_agent.packs.discovery import discover_packs
from magi_agent.packs.loader import load_packs

_FIRSTPARTY_DIR = Path(__file__).resolve().parents[2] / "magi_agent" / "firstparty" / "packs"


def _all_provided_refs() -> set[str]:
    loaded = load_packs(discover_packs(search_paths=[_FIRSTPARTY_DIR]))
    refs: set[str] = set()
    for pack in loaded.packs:
        for entry in pack.manifest.provides:
            refs.add(entry.ref)
    return refs


def test_bundled_first_party_packs_cover_the_legacy_default_catalog():
    default = CompileRecipePackCatalog.default()
    expected = {
        *default.connector_refs,
        *default.tool_refs,
        *default.plugin_refs,
        *default.validator_refs,
        *default.harness_refs,
        *default.evidence_producer_refs,
        *default.approval_authority_refs,
    }
    provided = _all_provided_refs()
    missing = expected - provided
    assert missing == set(), f"default-catalog refs with no bundled-pack home: {sorted(missing)}"
```

> Note: `discover_packs`/`load_packs`/`pack.manifest.provides`/`entry.ref` are P1 names. Re-grep
> `magi_agent/packs/discovery.py` + `loader.py` + `manifest.py` (`ProvidesEntry.ref`) and adjust the
> call shape to the real API before running.

- [ ] **Step 3: Run, see it fail**

```bash
MAGI_CONFIG="$(mktemp -d)/config.toml" uv run pytest \
  tests/firstparty/test_core_authoring_pack_covers_default_catalog.py -q
```
Expected: FAIL — `core_authoring` pack does not exist; some refs (`connector.source.readonly`,
`plugin.source-review.readonly`, `harness:authoring-static@1`, `authority:owner-human@1`, the
authoring-only tool/evidence refs not covered by P3/P4 packs) have no bundled home.

- [ ] **Step 4: Create the bundled pack manifest** covering the authoring-only refs not already shipped
  by a P3/P4/P5 pack. (Tool/validator/evidence refs already owned by a P4 pack — e.g. `FileWrite` — stay
  there; declare here only the ones with no home: list them by diffing the FAIL output.)

```toml
# magi_agent/firstparty/packs/core_authoring/pack.toml
[pack]
name = "core_authoring"
version = "1.0.0"
description = "First-party authoring-plane primitives (source-review connector, static harness, owner authority)."

[[provides]]
type = "connector"
ref = "connector.source.readonly"
impl = "magi_agent.firstparty.packs.core_authoring.impls:source_readonly_connector"

[[provides]]
type = "harness"
ref = "harness:authoring-static@1"
impl = "magi_agent.firstparty.packs.core_authoring.impls:authoring_static_harness"

[[provides]]
type = "callback"
ref = "plugin.source-review.readonly"
priority = 100
phase = "review"
impl = "magi_agent.firstparty.packs.core_authoring.impls:source_review_readonly_callback"

[[provides]]
type = "evidence_producer"
ref = "evidence:source-opened@1"
impl = "magi_agent.firstparty.packs.core_authoring.impls:source_opened_evidence"

[[provides]]
type = "evidence_producer"
ref = "evidence:quote-digest@1"
impl = "magi_agent.firstparty.packs.core_authoring.impls:quote_digest_evidence"

[[provides]]
type = "tool"
ref = "SourceOpen"
impl = "magi_agent.firstparty.packs.core_authoring.impls:source_open_tool"

[[provides]]
type = "tool"
ref = "CitationVerify"
impl = "magi_agent.firstparty.packs.core_authoring.impls:citation_verify_tool"

[[provides]]
type = "tool"
ref = "BrowserLive"
impl = "magi_agent.firstparty.packs.core_authoring.impls:browser_live_tool"
```

> **Important:** `[[provides]]` field names (`type`, `ref`, `impl`, `priority`, `phase`,
> `gate_position`) and the 8 allowed `type` values (`tool · callback · validator · harness ·
> control_plane · evidence_producer · recipe · connector`) are fixed by P1 `manifest.py`
> (`PackManifest`/`ProvidesEntry`). Match them exactly. Delete any `[[provides]]` block whose ref a
> P3/P4 pack already owns (the FAIL list tells you which are actually missing).

- [ ] **Step 5: Locate the real first-party impl symbols, THEN write the adapters** — thin re-exports
  of the **existing** first-party functions (no behavior change; migration is a move, not a rewrite).
  The module paths in the template below (`magi_agent.authoring.connectors`, `.harness`, `.review`,
  `.evidence`, `.tools`) are **NOT verified to exist** — they are illustrative shapes. Before writing
  the adapter file you MUST locate each real producer:

```bash
grep -rn "SourceOpen\|CitationVerify\|BrowserLive" magi_agent/ --include=*.py | grep -iv test
grep -rn "source.review\|source_review\|authoring-static\|authoring_static" magi_agent/ --include=*.py | grep -iv test
grep -rn "source.opened\|source_opened\|quote.digest\|quote_digest" magi_agent/ --include=*.py | grep -iv test
grep -rn "connector.source.readonly\|source_readonly" magi_agent/ --include=*.py | grep -iv test
```
Point each adapter at the **real** function the grep surfaces. If a ref is purely declarative (the
hardcode produced a `spec=`/dataclass, not a callable), use `spec = "relpath"` in the toml and drop
its adapter entirely. Each impl takes only its **typed context** (D5) — re-grep
`magi_agent/packs/context.py` for the exact context dataclass per type and match the P4 impl
signatures already shipped. The template below shows the **adapter shape only**; replace every
`from magi_agent.authoring.* import *` line with the real module:symbol the grep found.

```python
# magi_agent/firstparty/packs/core_authoring/impls.py
# TEMPLATE SHAPE — replace each `from magi_agent.authoring.* import *` import below
# with the real producer located by Step 5's grep. Do NOT assume these modules exist.
"""Bundled first-party authoring primitives.

Each symbol referenced by ``pack.toml`` is lazily imported by the loader at
registration time and receives ONLY its narrow typed context (D5). These are
thin adapters over the existing first-party authoring behavior; bundling them as
a pack removes the last hardcoded ``CompileRecipePackCatalog.default()`` privilege.
"""
from __future__ import annotations

from magi_agent.packs.context import (
    CallbackContext,
    ConnectorContext,
    EvidenceProducerContext,
    HarnessContext,
    ToolContext,
)


def source_readonly_connector(ctx: ConnectorContext) -> object:
    from magi_agent.authoring.connectors import build_source_readonly_connector

    return build_source_readonly_connector(ctx)


def authoring_static_harness(ctx: HarnessContext) -> object:
    from magi_agent.authoring.harness import build_authoring_static_harness

    return build_authoring_static_harness(ctx)


def source_review_readonly_callback(ctx: CallbackContext) -> object:
    from magi_agent.authoring.review import source_review_readonly

    return source_review_readonly(ctx)


def source_opened_evidence(ctx: EvidenceProducerContext) -> object:
    from magi_agent.authoring.evidence import source_opened_snapshot

    return source_opened_snapshot(ctx)


def quote_digest_evidence(ctx: EvidenceProducerContext) -> object:
    from magi_agent.authoring.evidence import quote_digest

    return quote_digest(ctx)


def source_open_tool(ctx: ToolContext) -> object:
    from magi_agent.authoring.tools import build_source_open_tool

    return build_source_open_tool(ctx)


def citation_verify_tool(ctx: ToolContext) -> object:
    from magi_agent.authoring.tools import build_citation_verify_tool

    return build_citation_verify_tool(ctx)


def browser_live_tool(ctx: ToolContext) -> object:
    from magi_agent.authoring.tools import build_browser_live_tool

    return build_browser_live_tool(ctx)
```

> The `magi_agent.authoring.{connectors,harness,review,evidence,tools}` module/symbol names are
> placeholders for **wherever the current first-party impl lives** — grep the codebase
> (`grep -rn "SourceOpen\|source.review\|authoring-static" magi_agent/`) and point each adapter at the
> real existing function. The contract: impl takes only the typed context, returns the same primitive
> object the hardcode produced. If a ref's behavior is purely declarative (a `spec=`), use
> `spec = "relpath"` in the toml instead of `impl =` and drop its adapter.

- [ ] **Step 6: Run, see it pass**

```bash
MAGI_CONFIG="$(mktemp -d)/config.toml" uv run pytest \
  tests/firstparty/test_core_authoring_pack_covers_default_catalog.py -q
```
Expected: PASS — every legacy default ref now has a bundled-pack home.

- [ ] **Step 7: Commit**

```bash
git add magi_agent/firstparty/packs/core_authoring/ \
        tests/firstparty/test_core_authoring_pack_covers_default_catalog.py
git commit -m "feat(firstparty): bundle remaining authoring primitives as a pack (dual-load)"
```

---

## Task 6.2: Keystone — load first-party LoopControls from bundled packs

Flip `build_default_plugin()` so the 6 controls come from the **loaded packs** (via P5's
`build_control_plane`) instead of `build_default_plane()`'s hand-assembled `plane.register(...)`
sequence. A user-supplied `control_plane` pack now loads through the **identical** path.

**Files:**
- Modify: `magi_agent/adk_bridge/control_plane.py` (`build_default_plugin`)
- Test: `tests/adk_bridge/test_build_default_plugin_pack_loaded.py`

- [ ] **Step 1: Grep the current construction (line numbers are HEAD-802e707b snapshots)**

```bash
grep -n "def build_default_plugin\|def build_default_plane\|_ExtendedControlPlanePlugin" \
  magi_agent/adk_bridge/control_plane.py
sed -n '1175,1210p' magi_agent/adk_bridge/control_plane.py
```
Current (`control_plane.py:1175-1209`) `build_default_plugin` calls `build_default_plane(...)` (which
at `:1091-1172` hand-`plane.register(...)`s each of the 6 controls) then wraps it in
`_ExtendedControlPlanePlugin(plane)`.

- [ ] **Step 2: Write the failing test** — first-party controls must be reachable through the pack
  loader, AND a user `control_plane` pack registered in the same call must coexist.

```python
# tests/adk_bridge/test_build_default_plugin_pack_loaded.py
from magi_agent.adk_bridge.control_plane import build_default_plugin


def test_build_default_plugin_loads_controls_from_packs_and_accepts_user_packs():
    env = {"MAGI_LOOP_GUARD_ENABLED": "1"}  # turn on a first-party control

    user_pack_loaded = {"seen": False}

    class _UserControl:
        name = "user.control"

        async def on_before_model(self, *, callback_context, llm_request):
            user_pack_loaded["seen"] = True

    plugin = build_default_plugin(
        env,
        extra_controls=[_UserControl()],  # the parallel user-pack injection seam
    )
    plane = plugin._plane  # _ExtendedControlPlanePlugin wraps a ControlPlane
    names = {getattr(c, "name", type(c).__name__) for c in plane._controls}
    # first-party resilience (loop-guard) control loaded from the bundled pack:
    assert any("resilience" in n.lower() or "loop" in n.lower() for n in names)
    # user pack control loaded through the identical mechanism, in parallel:
    assert "user.control" in names
```

> `plugin._plane` / `plane._controls` are the existing private accessors (`control_plane.py:236-237`,
> `_ExtendedControlPlanePlugin.__init__` stores the plane). If P5 renamed them, re-grep and adjust.

- [ ] **Step 3: Run, see it fail**

```bash
MAGI_CONFIG="$(mktemp -d)/config.toml" uv run pytest \
  tests/adk_bridge/test_build_default_plugin_pack_loaded.py -q
```
Expected: FAIL — `build_default_plugin` has no `extra_controls` param and assembles controls by
hardcode, not by pack.

- [ ] **Step 4: Implement the flip** — replace `build_default_plane(...)`'s hand-assembly call with a
  pack-loaded plane, plus an `extra_controls` injection seam for user packs.

Current code to replace (`control_plane.py:1197-1209`):
```python
    env = os_environ if os_environ is not None else dict(os.environ)
    plane = build_default_plane(
        os_environ=env,
        general_automation_receipts=general_automation_receipts,
        contract_required=contract_required,
        agent_role=agent_role,
        self_review_fork_runner=self_review_fork_runner,
        self_review_candidate_sink=self_review_candidate_sink,
        self_review_config=self_review_config,
        self_review_now=self_review_now,
        self_review_scheduler=self_review_scheduler,
    )
    return _ExtendedControlPlanePlugin(plane)
```

Replacement:
```python
    env = os_environ if os_environ is not None else dict(os.environ)
    from magi_agent.packs.discovery import discover_packs  # noqa: PLC0415
    from magi_agent.packs.loader import load_packs  # noqa: PLC0415
    from magi_agent.packs.registries import build_control_plane  # noqa: PLC0415

    loaded = load_packs(discover_packs(env=env))
    plane = build_control_plane(
        loaded,
        os_environ=env,
        general_automation_receipts=general_automation_receipts,
        contract_required=contract_required,
        agent_role=agent_role,
        self_review_fork_runner=self_review_fork_runner,
        self_review_candidate_sink=self_review_candidate_sink,
        self_review_config=self_review_config,
        self_review_now=self_review_now,
        self_review_scheduler=self_review_scheduler,
    )
    for control in extra_controls or ():
        plane.register(control)
    return _ExtendedControlPlanePlugin(plane)
```

And add `extra_controls` to the signature (after `self_review_scheduler`):
```python
    self_review_scheduler: Callable[[Coroutine[Any, Any, None]], None] | None = None,
    extra_controls: "list[LoopControl] | None" = None,
```

> `build_control_plane(loaded, ...)` is P5's registry-assembly function: it reads the bundled
> `control_plane` packs' manifests, lazily imports each control impl with its typed context + the same
> env-gated collaborators `build_default_plane` passed, registers them in `priority`/`gate_position`
> order, and returns a `ControlPlane`. **Re-grep `magi_agent/packs/registries.py` for its real
> signature** and pass through whatever collaborators it actually accepts. `discover_packs(env=env)`
> resolves the bundled-first-party dir + `~/.magi/packs/` + `<cwd>/.magi/packs/` honoring
> `config.toml [packs]` (P1/D1).

- [ ] **Step 5: Run the new test, see it pass**

```bash
MAGI_CONFIG="$(mktemp -d)/config.toml" uv run pytest \
  tests/adk_bridge/test_build_default_plugin_pack_loaded.py -q
```
Expected: PASS.

- [ ] **Step 6: Run the Phase-0 golden regression** (this edit touches the 6 control-plane controls)

```bash
MAGI_CONFIG="$(mktemp -d)/config.toml" uv run pytest \
  tests/fixtures/neutral_runtime_golden/test_golden_regression.py -q
```
Expected: PASS — the pack-loaded plane must be **behavior-identical** to the hand-assembled one. A
**diff = a behavior change to review**; the flip should be behavior-preserving, so investigate any
diff (likely a control-ordering or env-gating mismatch in `build_control_plane`). Regenerate via
`python -m tests.fixtures.neutral_runtime_golden.capture --write` **only if** the change is intended
and correct.

- [ ] **Step 7: Run the existing control-plane suite** (no regression in the old behavior tests)

```bash
MAGI_CONFIG="$(mktemp -d)/config.toml" uv run pytest \
  tests/adk_bridge/test_control_plane.py tests/test_runner_plugin_composition.py \
  tests/test_resilience_plugin_wiring.py tests/adk_bridge/test_context_compaction_plugin.py -q
```
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add magi_agent/adk_bridge/control_plane.py \
        tests/adk_bridge/test_build_default_plugin_pack_loaded.py
git commit -m "refactor(control-plane): load first-party controls via pack loader; accept user control packs (keystone)"
```

---

## Task 6.3: Flip the live catalog to manifest-built (D4)

Make the live path build its catalog from loaded pack manifests
(`catalog_build.build_catalog_from_packs`) instead of `CompileRecipePackCatalog.default()`.

**Files:**
- Modify: `magi_agent/authoring/compiler.py` (`compile_recipe_pack` default-catalog selection)
- Test: `tests/authoring/test_live_catalog_is_manifest_built.py`

- [ ] **Step 1: Grep the current default-catalog injection (snapshot may have drifted)**

```bash
grep -n "CompileRecipePackCatalog.default()\|def compile_recipe_pack\|def build_catalog_from_packs" \
  magi_agent/authoring/compiler.py magi_agent/packs/catalog_build.py
sed -n '149,155p' magi_agent/authoring/compiler.py
```
Current (`authoring/compiler.py:154`):
`reference_catalog = catalog or CompileRecipePackCatalog.default()`.

- [ ] **Step 2: Write the failing test** — when no explicit catalog is passed, the live catalog must be
  manifest-built (provably: it must contain a ref provided by a bundled pack but **not** present in the
  static `.default()` hardcode, AND must not be the `.default()` object identity).

```python
# tests/authoring/test_live_catalog_is_manifest_built.py
from magi_agent.authoring.compiler import (
    CompileRecipePackCatalog,
    resolve_live_catalog,
)


def test_resolve_live_catalog_is_manifest_built_not_hardcoded_default():
    static = CompileRecipePackCatalog.default()
    live = resolve_live_catalog()  # no explicit catalog → manifest-built
    # It is a real catalog, not the frozen hardcode object:
    assert isinstance(live, CompileRecipePackCatalog)
    assert live is not static
    # Provably manifest-sourced: union of bundled-pack refs ⊇ the legacy default's
    # tool refs (the migration preserves them) AND includes pack-only refs.
    assert set(static.tool_refs).issubset(set(live.tool_refs))
```

- [ ] **Step 3: Run, see it fail**

```bash
MAGI_CONFIG="$(mktemp -d)/config.toml" uv run pytest \
  tests/authoring/test_live_catalog_is_manifest_built.py -q
```
Expected: FAIL — `resolve_live_catalog` does not exist.

- [ ] **Step 4: Implement `resolve_live_catalog` + route the live default through it.**

Add to `authoring/compiler.py` (after `compile_recipe_pack`):
```python
def resolve_live_catalog(
    *,
    env: Mapping[str, str] | None = None,
) -> CompileRecipePackCatalog:
    """Build the live catalog from loaded pack manifests (D4).

    Replaces the hardcoded ``CompileRecipePackCatalog.default()`` on the live
    path: discovers packs (bundled first-party + user dirs), reads their static
    ``provides`` refs, and folds them into a flat catalog. No first-party-only
    tier — a user pack's refs land in exactly the same fields as first-party's.
    """
    from magi_agent.packs.catalog_build import build_catalog_from_packs  # noqa: PLC0415
    from magi_agent.packs.discovery import discover_packs  # noqa: PLC0415
    from magi_agent.packs.loader import load_packs  # noqa: PLC0415

    loaded = load_packs(discover_packs(env=env))
    return build_catalog_from_packs(loaded)
```

Then change the live default selection. Current (`authoring/compiler.py:154`):
```python
    reference_catalog = catalog or CompileRecipePackCatalog.default()
```
Replacement:
```python
    reference_catalog = catalog if catalog is not None else resolve_live_catalog()
```

> `build_catalog_from_packs(loaded) -> CompileRecipePackCatalog` is the P1 `catalog_build.py`
> deliverable (D4): it maps each loaded `ProvidesEntry` into the matching catalog field
> (`tool`→`tool_refs`, `validator`→`validator_refs`, `connector`→`connector_refs`,
> `evidence_producer`→`evidence_producer_refs`, `harness`→`harness_refs`, `callback`→`plugin_refs`,
> etc.) and preserves the `hard_invariant`/`required_*` floors `CompileRecipePackCatalog`'s validators
> require (it is a hosted floor — keep `required_hard_invariant_refs` satisfied so the model validator
> at `:110-115` passes). **Re-grep its real signature.**

- [ ] **Step 5: Run, see it pass**

```bash
MAGI_CONFIG="$(mktemp -d)/config.toml" uv run pytest \
  tests/authoring/test_live_catalog_is_manifest_built.py -q
```
Expected: PASS.

- [ ] **Step 6: No-regression on existing compiler tests**

```bash
MAGI_CONFIG="$(mktemp -d)/config.toml" uv run pytest tests/authoring/ -q
```
Expected: PASS (tests passing an explicit `catalog=` are unchanged; only the `None` default path moved).

- [ ] **Step 7: Commit**

```bash
git add magi_agent/authoring/compiler.py tests/authoring/test_live_catalog_is_manifest_built.py
git commit -m "feat(authoring): build live catalog from pack manifests instead of hardcoded default (D4)"
```

---

## Task 6.4: Microkernel shrink assertion (D6)

Encode D6: the core = `{ loader, registries, typed-context dispatcher, ADK loop }`. Everything else is
a removable pack. This is an **architecture guard test** — it fails if a future change re-privileges a
primitive by hardcoding it into a core module.

**Files:**
- Test: `tests/packs/test_microkernel_core_is_minimal.py`

- [ ] **Step 1: Grep to confirm the hardcode is gone** (the assertion targets)

```bash
grep -n "plane.register(" magi_agent/adk_bridge/control_plane.py
grep -n "CompileRecipePackCatalog.default()" magi_agent/authoring/compiler.py
```
Expected after 6.2+6.3: `build_default_plane` (legacy, now unused by the live path) may still contain
`plane.register(...)` lines, and `compile_recipe_pack` no longer calls `.default()` on the live path.
6.4 asserts the **live entrypoints** don't hardcode-register.

- [ ] **Step 2: Write the failing/guarding test** — the core modules must not statically reference any
  concrete first-party primitive class; primitive registration must flow through the loader.

```python
# tests/packs/test_microkernel_core_is_minimal.py
import ast
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2] / "magi_agent"

# The microkernel (D6): loader + registries + typed-context dispatcher + ADK loop seam.
_CORE_MODULES = [
    _ROOT / "packs" / "loader.py",
    _ROOT / "packs" / "registries.py",
    _ROOT / "packs" / "context.py",
    _ROOT / "packs" / "catalog_build.py",
]

# Concrete first-party primitive symbols that MUST NOT be hardcoded into the core.
_FORBIDDEN_FIRSTPARTY_SYMBOLS = {
    "GaConstraintReinjectionControl",
    "SelfReviewAfterTurnControl",
    "MaxStepsBrakeControl",
    "_EditRetryLoopControl",
    "_ResilienceLoopControl",
    "_CompactionLoopControl",
}


def _names_referenced(path: Path) -> set[str]:
    tree = ast.parse(path.read_text())
    return {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)} | {
        n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)
    }


def test_core_modules_do_not_hardcode_first_party_primitives():
    for module in _CORE_MODULES:
        referenced = _names_referenced(module)
        leaked = referenced & _FORBIDDEN_FIRSTPARTY_SYMBOLS
        assert leaked == set(), f"{module.name} hardcodes first-party primitives: {sorted(leaked)}"


def test_live_compiler_path_does_not_call_hardcoded_default_catalog():
    src = (_ROOT / "authoring" / "compiler.py").read_text()
    # the live default path must go through resolve_live_catalog, not .default()
    assert "resolve_live_catalog()" in src
    assert "catalog or CompileRecipePackCatalog.default()" not in src
```

- [ ] **Step 3: Run** — `test_core_modules_do_not_hardcode_first_party_primitives` should PASS if P5
  built `registries.py` to register from manifests (controls named via `module:symbol` strings, not
  imported class refs). `test_live_compiler_path_...` PASSES because of 6.3.

```bash
MAGI_CONFIG="$(mktemp -d)/config.toml" uv run pytest \
  tests/packs/test_microkernel_core_is_minimal.py -q
```
Expected: PASS. If `registries.py` imports a control class directly, that is a leak — fix `registries.py`
to resolve impls via the loader's lazy `module:symbol` import (P1 ABI), not a hardcoded import.

- [ ] **Step 4: Commit**

```bash
git add tests/packs/test_microkernel_core_is_minimal.py
git commit -m "test(packs): guard microkernel core against hardcoded first-party primitives (D6)"
```

---

## Task 6.5: Local-trust default — authored rules enforce by default

Drop the hosted **staged-authority** posture for local: an authored validator/evidence requirement
that is missing should default to **enforce** (`repair_required`/`block`) for local full-trust, not the
conservative hosted `"audit"` default. Gate on the runtime profile so hosted/safe profiles keep the
old behavior.

**Files:**
- Modify: `magi_agent/cli/real_runner.py` (`_build_default_runner_policy_assembly` → missing-evidence action)
- Test: `tests/cli/test_local_trust_enforces_authored_rules.py`

- [ ] **Step 1: Grep the staged-authority / missing-evidence default (snapshots may have drifted)**

```bash
grep -n "missing_evidence_action\|missingEvidenceAction\|repair_required\|\"audit\"" \
  magi_agent/cli/engine.py magi_agent/cli/real_runner.py
grep -n "_runtime_profile_default_enabled\|RUNTIME_PROFILE_ENV\|_SAFE_RUNTIME_PROFILES" \
  magi_agent/config/env.py
```
Current: `cli/engine.py:196` defaults `missing_evidence_action` to `"audit"` when unset;
`real_runner.py:464` reads `missing_action = plan.final_gate_policy.missing_evidence_action` from the
materializer (which under hosted staging tends to `"audit"`). The local-trust flip upgrades a local
full-profile `"audit"` to `"repair_required"`. `config/env.py:1972-1974` already has the profile gate:
`_runtime_profile_default_enabled(env)` is `True` unless profile ∈ `{safe,off,minimal,conservative,eval}`.

- [ ] **Step 2: Write the failing test** — under the default (full) local profile, a policy assembly's
  `missing_evidence_action` is `repair_required`; under a `safe`/`eval` profile it stays `audit`.

```python
# tests/cli/test_local_trust_enforces_authored_rules.py
from magi_agent.cli.real_runner import _local_trust_missing_evidence_action


def test_local_full_trust_upgrades_audit_to_repair_required():
    assert (
        _local_trust_missing_evidence_action("audit", env={})
        == "repair_required"
    )


def test_safe_profile_keeps_hosted_audit_posture():
    assert (
        _local_trust_missing_evidence_action("audit", env={"MAGI_RUNTIME_PROFILE": "safe"})
        == "audit"
    )


def test_explicit_repair_required_is_preserved():
    assert (
        _local_trust_missing_evidence_action("repair_required", env={})
        == "repair_required"
    )
```

- [ ] **Step 3: Run, see it fail**

```bash
MAGI_CONFIG="$(mktemp -d)/config.toml" uv run pytest \
  tests/cli/test_local_trust_enforces_authored_rules.py -q
```
Expected: FAIL — `_local_trust_missing_evidence_action` does not exist.

- [ ] **Step 4: Implement the helper + apply it in the assembly builder.**

Add to `cli/real_runner.py` (near `_required_deliverable_evidence_from_assembly`):
```python
def _local_trust_missing_evidence_action(
    materialized_action: str,
    *,
    env: Mapping[str, str] | None = None,
) -> str:
    """Local full-trust enforces authored rules by default (drop hosted staging).

    Hosted runs stage authority — a missing authored evidence requirement only
    *audits*. For OSS local full-trust the author IS the operator, so a missing
    requirement should enforce (``repair_required``) by default. Safe/eval/minimal
    profiles (the same set that gates every other full-runtime feature) keep the
    conservative hosted ``audit`` posture. An explicit ``repair_required`` is never
    downgraded.
    """
    from magi_agent.config.env import _runtime_profile_default_enabled  # noqa: PLC0415

    source = os.environ if env is None else env
    if materialized_action == "repair_required":
        return "repair_required"
    if materialized_action == "audit" and _runtime_profile_default_enabled(source):
        return "repair_required"
    return materialized_action
```

Then apply it in `_build_default_runner_policy_assembly`. Current (`real_runner.py:464`):
```python
    missing_action = plan.final_gate_policy.missing_evidence_action
```
Replacement:
```python
    missing_action = _local_trust_missing_evidence_action(
        plan.final_gate_policy.missing_evidence_action
    )
```

> `_runtime_profile_default_enabled` is the existing single source of truth (`config/env.py:1972`); it
> returns `False` for `{safe,off,minimal,conservative,eval}` so the same profiles that disable every
> full-runtime feature also keep the hosted audit posture here — one consistent local-trust seam.

- [ ] **Step 5: Run, see it pass**

```bash
MAGI_CONFIG="$(mktemp -d)/config.toml" uv run pytest \
  tests/cli/test_local_trust_enforces_authored_rules.py -q
```
Expected: PASS.

- [ ] **Step 6: Golden regression** (the gate's action feeds the live enforce path at
  `cli/engine.py:2174-2233` — confirm the control-plane decision trace is unchanged; the action change
  is an engine-gate remediation field, not a control-plane callback, so the golden should be green)

```bash
MAGI_CONFIG="$(mktemp -d)/config.toml" uv run pytest \
  tests/fixtures/neutral_runtime_golden/test_golden_regression.py -q
```
Expected: PASS. Diff = a behavior change to review; regenerate via `capture --write` only if intended.

- [ ] **Step 7: Commit**

```bash
git add magi_agent/cli/real_runner.py tests/cli/test_local_trust_enforces_authored_rules.py
git commit -m "feat(cli): local full-trust enforces authored rules by default (drop hosted staged-authority)"
```

---

## Task 6.6: Acceptance — §1 "no privilege" as tests

The four §1 final-state assertions, each a test. The pack passes only if all hold.

**Files:**
- Test: `tests/packs/test_acceptance_no_privilege.py`

- [ ] **Step 1: Write the four acceptance tests** (they should PASS given 6.2/6.3 + P1–P5).

```python
# tests/packs/test_acceptance_no_privilege.py
"""§1 'no privilege' acceptance spec (00-BLUEPRINT.md §1).

The whole pack passes only if every first-party behavior is expressible and
loadable through the SAME (a) loader, (b) flat catalog, (c) typed context as a
third-party user pack — no in-code shortcut, no first-party-only tier.
"""
import inspect
import textwrap
from pathlib import Path

from magi_agent.adk_bridge.control_plane import build_default_plugin
from magi_agent.authoring.compiler import CompileRecipePackCatalog, resolve_live_catalog
from magi_agent.packs.context import (
    CallbackContext,
    ConnectorContext,
    ControlPlaneContext,
    EvidenceProducerContext,
    HarnessContext,
    RecipeContext,
    ToolContext,
    ValidatorContext,
)
from magi_agent.packs.discovery import discover_packs
from magi_agent.packs.loader import load_packs

_ROOT = Path(__file__).resolve().parents[2] / "magi_agent"
_FIRSTPARTY_DIR = _ROOT / "firstparty" / "packs"

_PROVIDES_TYPES = (
    "tool", "callback", "validator", "harness",
    "control_plane", "evidence_producer", "recipe", "connector",
)
_TYPED_CONTEXTS = (
    ToolContext, CallbackContext, ValidatorContext, HarnessContext,
    ControlPlaneContext, EvidenceProducerContext, RecipeContext, ConnectorContext,
)


# --- Assertion 1: no first-party primitive registered via a hardcoded path -------------
def test_no_hardcoded_first_party_registration_on_live_path():
    cp = (_ROOT / "adk_bridge" / "control_plane.py").read_text()
    # build_default_plugin must not hand-assemble controls; it loads them.
    plugin_src = cp.split("def build_default_plugin", 1)[1].split("\ndef ", 1)[0]
    assert "plane.register(" not in plugin_src
    assert "load_packs(" in plugin_src
    comp = (_ROOT / "authoring" / "compiler.py").read_text()
    assert "catalog or CompileRecipePackCatalog.default()" not in comp


# --- Assertion 2: a user pack can add/override/remove every one of the 8 provides ------
def _write_user_pack(tmp_path: Path, body: str) -> Path:
    pack_dir = tmp_path / "packs" / "user_demo"
    pack_dir.mkdir(parents=True)
    (pack_dir / "pack.toml").write_text(textwrap.dedent(body))
    (pack_dir / "impls.py").write_text(
        "def make(ctx):\n    return ctx\n"
    )
    return tmp_path / "packs"


def test_user_pack_can_add_every_provides_type(tmp_path):
    entries = "\n".join(
        f'[[provides]]\ntype = "{t}"\nref = "user.add.{t}"\n'
        f'impl = "user_demo.impls:make"\n'
        for t in _PROVIDES_TYPES
    )
    toml = f'[pack]\nname = "user_demo"\nversion = "1.0.0"\n\n{entries}'
    pack_dir = tmp_path / "packs" / "user_demo"
    pack_dir.mkdir(parents=True)
    (pack_dir / "pack.toml").write_text(toml)
    (pack_dir / "impls.py").write_text("def make(ctx):\n    return ctx\n")
    loaded = load_packs(discover_packs(search_paths=[tmp_path / "packs"]))
    provided = {e.ref for p in loaded.packs for e in p.manifest.provides}
    for t in _PROVIDES_TYPES:
        assert f"user.add.{t}" in provided


def test_user_pack_can_override_a_first_party_ref(tmp_path):
    # Override the first-party FileWrite tool ref via [packs] override precedence.
    pack_dir = tmp_path / "packs" / "user_override"
    pack_dir.mkdir(parents=True)
    (pack_dir / "pack.toml").write_text(
        '[pack]\nname = "user_override"\nversion = "1.0.0"\n\n'
        '[[provides]]\ntype = "tool"\nref = "FileWrite"\n'
        'impl = "user_override.impls:make"\n'
    )
    (pack_dir / "impls.py").write_text("def make(ctx):\n    return 'OVERRIDDEN'\n")
    env = {"MAGI_PACKS_OVERRIDE": "user_override:FileWrite"}
    loaded = load_packs(
        discover_packs(env=env, search_paths=[_FIRSTPARTY_DIR, tmp_path / "packs"])
    )
    winner = loaded.resolve_ref("tool", "FileWrite")
    assert winner.pack_name == "user_override"


def test_user_pack_can_remove_forbid_a_first_party_ref(tmp_path):
    env = {"MAGI_PACKS_FORBID": "FileWrite"}
    loaded = load_packs(discover_packs(env=env, search_paths=[_FIRSTPARTY_DIR]))
    provided = {e.ref for p in loaded.packs for e in p.manifest.provides}
    assert "FileWrite" not in provided


# --- Assertion 3: the live catalog is manifest-built ----------------------------------
def test_live_catalog_is_manifest_built():
    static = CompileRecipePackCatalog.default()
    live = resolve_live_catalog()
    assert live is not static
    assert set(static.tool_refs).issubset(set(live.tool_refs))


# --- Assertion 4: every primitive impl takes only its typed context -------------------
def test_every_first_party_impl_takes_only_its_typed_context():
    loaded = load_packs(discover_packs(search_paths=[_FIRSTPARTY_DIR]))
    type_to_ctx = dict(zip(_PROVIDES_TYPES, _TYPED_CONTEXTS, strict=True))
    for pack in loaded.packs:
        for entry in pack.manifest.provides:
            if entry.impl is None:  # declarative spec= entries have no impl
                continue
            fn = loaded.import_impl(entry)
            sig = inspect.signature(fn)
            params = [p for p in sig.parameters.values()
                      if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)]
            assert len(params) == 1, f"{entry.ref}: impl must take exactly its typed context"
            ann = params[0].annotation
            expected = type_to_ctx[entry.type]
            assert ann in (expected, expected.__name__, inspect.Parameter.empty), (
                f"{entry.ref}: impl param annotated {ann!r}, expected {expected.__name__}"
            )
```

> `loaded.packs`, `pack.manifest.provides`, `entry.ref`, `entry.type`, `entry.impl`,
> `loaded.resolve_ref(type, ref)` (returning an object with `.pack_name`), `loaded.import_impl(entry)`,
> and the `MAGI_PACKS_OVERRIDE`/`MAGI_PACKS_FORBID`/`config.toml [packs]` precedence are P1/P2
> deliverables. **Re-grep `loader.py`/`discovery.py`/`registries.py` and adapt each call to the real
> API** (the override/forbid may be expressed via `config.toml [packs]` rather than env — use whatever
> P1 shipped; the assertion content is what matters, not the exact knob). The 8 typed context classes
> are P2 `context.py`.

- [ ] **Step 2: Run the acceptance suite**

```bash
MAGI_CONFIG="$(mktemp -d)/config.toml" uv run pytest \
  tests/packs/test_acceptance_no_privilege.py -q
```
Expected: PASS — all four §1 assertions hold. A failure here is a **real privilege leak**, not a test
bug: fix the runtime (e.g. a control still hardcoded, a ref a user pack can't override) until green.

- [ ] **Step 3: Commit**

```bash
git add tests/packs/test_acceptance_no_privilege.py
git commit -m "test(packs): encode §1 no-privilege acceptance spec as tests"
```

---

## Task 6.7: Full-suite + golden green (final gate)

- [ ] **Step 1: Phase-0 golden regression (control-plane behavior unchanged end-to-end)**

```bash
MAGI_CONFIG="$(mktemp -d)/config.toml" uv run pytest \
  tests/fixtures/neutral_runtime_golden/ -q
```
Expected: PASS. Any diff = a behavior change to review; this phase's flips are behavior-preserving, so
investigate. Regenerate via `python -m tests.fixtures.neutral_runtime_golden.capture --write` only if
the change is intended and correct (and call it out in the commit).

- [ ] **Step 2: Full packs + firstparty + control-plane + authoring + cli suites**

```bash
MAGI_CONFIG="$(mktemp -d)/config.toml" uv run pytest \
  tests/packs/ tests/firstparty/ tests/authoring/ tests/cli/ \
  tests/adk_bridge/ tests/fixtures/neutral_runtime_golden/ -q
```
Expected: all PASS.

- [ ] **Step 3: Full repo suite**

```bash
MAGI_CONFIG="$(mktemp -d)/config.toml" uv run pytest -q
```
Expected: green (aside from any pre-existing import-boundary/socket failures documented in
`reference_magi_agent_test_env` — confirm they are the *same* known failures as on the P5 tip, not new
breakage caused by this phase).

- [ ] **Step 4: Smoke a real fake-model turn through the flipped path (no API keys)**

```bash
MAGI_CONFIG="$(mktemp -d)/config.toml" LOCAL_DEV_MODEL_SENTINEL="local-dev" \
MAGI_LOG_LEVEL=info uv run magi run --output text "list the files in this directory" 2>&1 | tail -20
```
Expected: a completed turn with the fake-model — first-party controls active (loaded from packs), no
`build_default_plane`/hardcode path taken, no crash. (If `magi run` flags differ, re-grep
`magi_agent/cli/` for the headless entrypoint and use it.)

- [ ] **Step 5: Commit any final adjustments**

```bash
git add -A
git commit -m "test(packs): full-suite + golden green after first-party migration (Phase 6 acceptance)"
```

---

## Acceptance criteria (Phase 6 done)

- [ ] `build_default_plugin()` no longer hand-assembles the 6 controls; it loads them via the Phase-1
  loader and accepts user `control_plane` packs in parallel (`extra_controls` / bundled discovery).
- [ ] `grep` proves no first-party LoopControl/tool/validator is registered via a hardcoded path on the
  live entrypoints (`test_acceptance_no_privilege::test_no_hardcoded_first_party_registration_on_live_path`).
- [ ] The live catalog is manifest-built: `resolve_live_catalog()` replaces
  `CompileRecipePackCatalog.default()` on the `None`-catalog path.
- [ ] A user pack can **add** a primitive of every one of the 8 `provides` types, **override** a
  first-party ref, and **remove/forbid** a first-party ref — all green.
- [ ] Every bundled first-party impl takes only its typed context (no `RuntimeContext`/AppState
  god-object, no privileged kwargs).
- [ ] Microkernel guard test green: core modules don't hardcode concrete first-party primitives (D6).
- [ ] Local full-trust enforces authored rules by default (`audit`→`repair_required` under the full
  profile); `safe`/`eval` profiles keep hosted audit posture.
- [ ] Phase-0 golden regression green (control-plane behavior unchanged) and full repo suite green
  (modulo documented pre-existing failures).

## Rollback

Each task is one revertible commit. To revert this phase without touching earlier phases:
- Revert 6.2 (`refactor(control-plane): load first-party controls via pack loader …`) → `build_default_plugin`
  falls back to `build_default_plane`'s hand-assembly (still present; never deleted).
- Revert 6.3 (`feat(authoring): build live catalog from pack manifests …`) → live path returns to
  `CompileRecipePackCatalog.default()`.
- Revert 6.5 (`feat(cli): local full-trust enforces authored rules …`) → restores the hosted `audit`
  default.
- 6.1 / 6.4 / 6.6 are additive (bundled pack + guard/acceptance tests); reverting their commits removes
  the files. `git revert <sha>` per commit, in reverse order, restores the pre-Phase-6 runtime exactly.
  No data migration, no schema/deploy change — all edits are in-process Python.

## Hand-off to later phases

Phase 6 is the de-privileging keystone **and the final phase of this pack** — per `00-BLUEPRINT.md`
§7 the index ends at `07-phase6-firstparty-migration-and-acceptance.md`, which folds the blueprint's
Phase-6 migration AND Phase-7 acceptance/local-trust flip into this one doc (Tasks 6.5 + 6.6). There
is no separate later phase doc in this pack. After Phase 6:
- `build_default_plane` is **legacy/unused on the live path** — a later cleanup may delete it once no
  test imports it (grep `build_default_plane` first; the golden oracle's scenario drivers may still use
  it to assemble controls in isolation — keep it until those are re-pointed at the pack loader).
- The §1 acceptance suite (`tests/packs/test_acceptance_no_privilege.py`) is the permanent contract:
  any future re-privileging (a hardcoded `plane.register`, a first-party-only catalog tier, a god-object
  context) fails it. Treat a red acceptance test as a privilege regression, not a flaky test.
- Local-trust default (`_local_trust_missing_evidence_action`) is the single seam where OSS local
  diverges from hosted staging; hosted keeps its own posture via the `safe`/profile gate — do not add a
  second divergence point.
