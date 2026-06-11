# Phase 4 — Easy `provides` Types (tool · evidence_producer · recipe · connector/MCP · harness · callback)

> **For agentic workers:** REQUIRED SUB-SKILL: `superpowers:subagent-driven-development` or
> `superpowers:executing-plans`. Read `00-BLUEPRINT.md` first (it defines the §1 "no privilege"
> spec, the D1–D7 contract, the `magi_agent/packs/` file-structure map, and the conventions this
> doc honors). This phase depends on Phase 1 (manifest/discovery/loader), Phase 2 (typed-context
> ABI + registries), and Phase 3 (the validator vertical slice it copies). It is **HIGH-parallel**:
> the six task groups below are fully independent — `/workflows` should fan out **one agent per
> unit** (Group A … Group F) and merge/dedup at the end.

**Goal:** Prove "no privilege" (00-BLUEPRINT §1) for six more `provides` types by replicating the
Phase-3 validator pattern once per type. For **each** type we ship: (1) a **bundled first-party
pack** under `magi_agent/firstparty/packs/<name>/` providing the type via `pack.toml` (the SAME
loader/format third-party uses); (2) **registration via its typed context** (the D5 narrow handle —
no god-object, no first-party-only kwarg); (3) a **user-pack add/override/remove** test (a pack in
`~/.magi/packs/` adds a new ref, overrides a first-party ref, and removes/forbids one); (4) a
**live-path enforce/usage** confirmation that the registered primitive actually runs (or is actually
offered) on the CLI path, not just catalogued.

**Architecture:** Each unit rides one already-live seam. No control-plane LoopControl is touched, so
**no Phase-0 golden regression is required for Groups A–F** (the golden oracle only gates the 6
LoopControls; see "Golden-oracle note" below). Difficulty + the seam each unit rides:

| Group | Type | Difficulty | Rides existing seam |
|---|---|---|---|
| A | `tool` | easy | `tools/registry.py` `ToolRegistry` + `ToolManifest` (skill/MCP discovery precedent) |
| B | `evidence_producer` | easy | evidence requirements already live-checked at `cli/engine.py:~2138` verifier bus |
| C | `recipe` | easy-medium | `authoring/compiler.py` `CompileRecipePackCatalog` + `recipes/materializer.py` |
| D | `connector` (MCP) | medium | `plugins/mcp_adapter.py` `McpAdapter` + `plugins/extension_boundary.py` |
| E | `harness` | easy | `harness/resolved.py` resolved presets (`ResolvedHarnessPack`) |
| F | `callback` | medium | `adk_bridge/callback_adapter.py` → `hooks/bus.py` `HookBus`; expose the unexposed `hooks/registry.py` `HookRegistry` |

**Tech stack:** Python 3.11+ (`requires-python = ">=3.11"`), `uv`, pydantic v2 (frozen,
`extra="forbid"`, `populate_by_name=True`, camelCase aliases — matching `authoring/compiler.py`
`_MODEL_CONFIG` and `harness/resolved.py` `_RESOLVED_MODEL_CONFIG`), `tomllib` (stdlib), pytest with
fake-model (`LOCAL_DEV_MODEL_SENTINEL="local-dev"`, **no API keys**).

---

## 0. Shared contract this phase rides (from Phases 1–3)

These symbols are **created in Phases 1–3**; this phase imports them. If a name drifted, **grep for
the real symbol before editing** (`grep -rn "class PackManifest" magi_agent/packs/`). The shapes
below are the contract every unit codes against (they match the 00-BLUEPRINT file map exactly):

- `magi_agent/packs/manifest.py`
  - `PackManifest` — frozen pydantic; fields `pack_id: str` (alias `packId`), `version: str = "1"`,
    `provides: tuple[ProvidesEntry, ...]`. `.from_toml(path: Path) -> PackManifest` classmethod.
  - `ProvidesEntry` — frozen pydantic; fields `type: ProvidesType`, `ref: str`,
    `impl: str | None = None` (`"module:symbol"`), `spec: str | None = None` (declarative relpath),
    `priority: int = 100`, `phase: str | None = None`, `gate_position: Literal["before","after"] = "after"`.
    `ProvidesType = Literal["tool","callback","validator","harness","control_plane",
    "evidence_producer","recipe","connector"]`. `.load_impl() -> object` lazy-imports `module:symbol`.
- `magi_agent/packs/discovery.py`
  - `discover_packs(*, search_paths: tuple[Path, ...] | None = None, config: Mapping | None = None)
    -> tuple[PackManifest, ...]` — finds `pack.toml` under `firstparty/packs/` + user dirs, applies
    `config.toml [packs]` enable/disable/order/override.
  - `default_search_paths() -> tuple[Path, ...]` — `(firstparty_packs_dir(), user_packs_dir(),
    cwd_packs_dir())`. `firstparty_packs_dir() -> Path` returns
    `Path(magi_agent.__file__).parent / "firstparty" / "packs"`.
- `magi_agent/packs/loader.py`
  - `load_packs(manifests: tuple[PackManifest, ...], registries: PackRegistries) -> LoadReport` —
    for each `ProvidesEntry`, lazy-imports its impl and registers it into the matching registry via
    the typed context dispatcher. `LoadReport` carries `.registered: tuple[str, ...]` and
    `.removed: tuple[str, ...]`.
- `magi_agent/packs/registries.py`
  - `PackRegistries` — a frozen container holding one registry per type. Relevant accessors for this
    phase: `.tools: ToolRegistry`, `.hooks: HookRegistry`, plus generic keyed registries
    `.evidence_producers`, `.recipes`, `.connectors`, `.harnesses` exposing
    `.register(ref, impl)`, `.replace(ref, impl)`, `.remove(ref)`, `.resolve(ref)`, `.list_refs()`.
    NOTE: `.tools` (`ToolRegistry`) and `.hooks` (`HookRegistry`) are the **existing** runtime
    classes — their removal method is `unregister(name)` (confirmed `tools/registry.py:101`,
    `hooks/registry.py:60`), NOT `remove`; only the four greenfield keyed registries use `.remove`.
  - A pack entry whose `ref` is prefixed `"-"` (e.g. `"-validator:foo@1"`) means **remove/forbid**
    that ref. The loader calls the registry's removal method — `.remove(...)` on the keyed registries,
    `.unregister(...)` on `.tools`/`.hooks`. This is the Phase-3 removal convention this phase reuses
    verbatim.
- `magi_agent/packs/context.py` — typed-context dataclasses (D5). Each is `@dataclass(frozen=True)`,
  read-mostly, exposes ONLY that type's capabilities. The dispatcher
  `build_context(entry: ProvidesEntry, registries: PackRegistries) -> object` returns the right one.
  - `ToolProvideContext(register: Callable[[ToolManifest], None])`
  - `EvidenceProducerProvideContext(register: Callable[[str, ProducerSpec], None])`
  - `RecipeProvideContext(register: Callable[[str, RecipePackManifest], None])`
  - `ConnectorProvideContext(register: Callable[[str, ConnectorSpec], None])`
  - `HarnessProvideContext(register: Callable[[str, ResolvedHarnessPack], None])`
  - `CallbackProvideContext(register: Callable[[HookManifest, HookHandler], None])`
- `magi_agent/packs/catalog_build.py`
  - `build_catalog(registries: PackRegistries) -> CompileRecipePackCatalog` (D4) — unions loaded
    refs into the live catalog (replaces `CompileRecipePackCatalog.default()` on the live path).

**Phase-3 reference unit (read it before coding any group):**
`magi_agent/firstparty/packs/validators_core/pack.toml` + its impl module, and
`tests/firstparty/test_validators_core_pack.py`. Every group below is the validator pattern with one
type swapped. **First action in every group: `ls magi_agent/firstparty/packs/` and read the
`validators_core` pack + its test to copy the exact pack/test skeleton.**

**Golden-oracle note (applies to all groups):** none of Groups A–F edit the 6 control-plane
LoopControls, so the Phase-0 golden regression
(`tests/fixtures/neutral_runtime_golden/test_golden_regression.py`) is **not** in their verify path.
If, while wiring Group F's callback live path, you find yourself editing
`adk_bridge/control_plane.py`'s `build_default_plugin` LoopControl assembly — **stop**: that is Phase
5 scope, not Phase 4. (Group F registers a `HookManifest` into the `HookBus`, which is a separate
seam from the LoopControls.)

---

## GROUP A — `tool` provides type  *(difficulty: easy)*

Rides `magi_agent/tools/registry.py` `ToolRegistry` (the skill/MCP discovery precedent already
registers `ToolManifest`s into it). The first-party tool catalog
(`magi_agent/tools/catalog.py` `register_core_tool_manifests`) is the privilege to dissolve: we ship
ONE core tool as a bundled pack instead of a hardcode, prove a user pack can add/override/remove a
tool ref, and confirm a registered tool reaches the live mode-scoped tool list.

### Task A.1: First-party `tools_clock` pack provides the `Clock` tool via typed context

**Files:**
- Create: `magi_agent/firstparty/packs/tools_clock/pack.toml`
- Create: `magi_agent/firstparty/packs/tools_clock/impl.py`
- Test: `tests/firstparty/test_tools_clock_pack.py`

- [ ] **Step 1: Read the precedent.** `ls magi_agent/firstparty/packs/` and read
  `validators_core/pack.toml` + `tests/firstparty/test_validators_core_pack.py`. Then re-grep the
  real `Clock` manifest you will reproduce:
  `grep -n '"Clock"' magi_agent/tools/catalog.py` (HEAD snapshot: `catalog.py:264`). Confirm the
  `ToolManifest` import path: `grep -n "class ToolManifest" magi_agent/tools/manifest.py`.

- [ ] **Step 2: Write the failing test.**

```python
# tests/firstparty/test_tools_clock_pack.py
from pathlib import Path

from magi_agent.packs.discovery import discover_packs, firstparty_packs_dir
from magi_agent.packs.loader import load_packs
from magi_agent.packs.registries import PackRegistries


def _firstparty_only() -> tuple:
    pack_dir = firstparty_packs_dir() / "tools_clock"
    manifests = discover_packs(search_paths=(pack_dir,))
    return manifests


def test_tools_clock_pack_registers_clock_via_typed_context():
    registries = PackRegistries.empty()
    report = load_packs(_firstparty_only(), registries)
    assert "Clock" in report.registered
    manifest = registries.tools.resolve("Clock")
    assert manifest is not None
    assert manifest.name == "Clock"
    assert manifest.permission == "meta"
    assert "plan" in manifest.available_in_modes and "act" in manifest.available_in_modes
```

- [ ] **Step 3: Run it, see it fail.**

```bash
MAGI_CONFIG="$(mktemp -d)/config.toml" uv run pytest \
  tests/firstparty/test_tools_clock_pack.py -q
```
Expected: FAIL (`tools_clock` pack does not exist).

- [ ] **Step 4: Create the pack manifest.**

```toml
# magi_agent/firstparty/packs/tools_clock/pack.toml
packId = "tools_clock"
version = "1"

[[provides]]
type = "tool"
ref = "Clock"
impl = "magi_agent.firstparty.packs.tools_clock.impl:provide_clock"
```

- [ ] **Step 5: Create the impl (registers via the D5 `ToolProvideContext`, no privileged kwargs).**

```python
# magi_agent/firstparty/packs/tools_clock/impl.py
from __future__ import annotations

from magi_agent.packs.context import ToolProvideContext
from magi_agent.tools.catalog import CORE_TOOL_SOURCE, CORE_TOOL_INPUT_SCHEMA
from magi_agent.tools.manifest import Budget, ToolManifest


def provide_clock(context: ToolProvideContext) -> None:
    context.register(
        ToolManifest(
            name="Clock",
            description="Read current time metadata.",
            kind="core",
            source=CORE_TOOL_SOURCE,
            permission="meta",
            input_schema=CORE_TOOL_INPUT_SCHEMA,
            timeout_ms=30_000,
            budget=Budget(max_calls_per_turn=10, max_parallel=1),
            dangerous=False,
            is_concurrency_safe=True,
            mutates_workspace=False,
            parallel_safety="readonly",
            available_in_modes=("plan", "act"),
            tags=("utility", "time", "meta"),
            enabled_by_default=True,
            opt_out=True,
        )
    )
```

- [ ] **Step 6: Run it, see it pass.**

```bash
MAGI_CONFIG="$(mktemp -d)/config.toml" uv run pytest \
  tests/firstparty/test_tools_clock_pack.py -q
```
Expected: PASS.

- [ ] **Step 7: Commit.**

```bash
git add magi_agent/firstparty/packs/tools_clock/ tests/firstparty/test_tools_clock_pack.py
git commit -m "feat(packs): bundle Clock as a first-party tool pack via typed context"
```

### Task A.2: User pack add / override / remove for the `tool` type

**Files:**
- Test: `tests/packs/test_user_tool_pack.py`

- [ ] **Step 1: Read** `tests/firstparty/test_validators_core_pack.py`'s user-pack section (the
  override/remove convention). Confirm the `"-"`-prefix remove convention by
  `grep -rn "startswith(\"-\")\|remove(" magi_agent/packs/loader.py`.

- [ ] **Step 2: Write the failing test** (uses `tmp_path` user-pack dir; no `~/.magi` writes).

```python
# tests/packs/test_user_tool_pack.py
from pathlib import Path

from magi_agent.packs.discovery import discover_packs, firstparty_packs_dir
from magi_agent.packs.loader import load_packs
from magi_agent.packs.registries import PackRegistries


def _write(p: Path, body: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body)


def test_user_tool_pack_add_override_remove(tmp_path: Path):
    user = tmp_path / "user_tools"
    # ADD a brand-new tool ref
    _write(user / "pack.toml",
           'packId = "user_tools"\nversion = "1"\n\n'
           '[[provides]]\ntype = "tool"\nref = "WeatherPeek"\n'
           'impl = "user_tools.impl:provide_weather"\n\n'
           '[[provides]]\ntype = "tool"\nref = "Clock"\n'
           'impl = "user_tools.impl:provide_clock_override"\n\n'
           '[[provides]]\ntype = "tool"\nref = "-Calculation"\n')
    _write(user / "impl.py",
           "from magi_agent.packs.context import ToolProvideContext\n"
           "from magi_agent.tools.catalog import CORE_TOOL_SOURCE, CORE_TOOL_INPUT_SCHEMA\n"
           "from magi_agent.tools.manifest import Budget, ToolManifest\n"
           "def _mk(name, desc):\n"
           "    return ToolManifest(name=name, description=desc, kind='external',\n"
           "        source=CORE_TOOL_SOURCE, permission='meta', input_schema=CORE_TOOL_INPUT_SCHEMA,\n"
           "        timeout_ms=10_000, budget=Budget(max_calls_per_turn=3, max_parallel=1),\n"
           "        dangerous=False, is_concurrency_safe=True, mutates_workspace=False,\n"
           "        parallel_safety='readonly', available_in_modes=('plan','act'),\n"
           "        tags=('utility','meta'), enabled_by_default=True, opt_out=True)\n"
           "def provide_weather(ctx):\n    ctx.register(_mk('WeatherPeek', 'Peek the weather.'))\n"
           "def provide_clock_override(ctx):\n    ctx.register(_mk('Clock', 'OVERRIDDEN clock.'))\n")
    # first-party Clock + Calculation packs must be present so override/remove have a target
    fp_clock = firstparty_packs_dir() / "tools_clock"
    manifests = discover_packs(search_paths=(fp_clock, user))

    registries = PackRegistries.empty()
    # seed the first-party Calculation so remove has a target (a 2nd firstparty pack
    # may not exist yet — register a stand-in via the tool registry directly)
    from magi_agent.tools.catalog import CORE_TOOL_SOURCE, CORE_TOOL_INPUT_SCHEMA
    from magi_agent.tools.manifest import Budget, ToolManifest
    registries.tools.register(ToolManifest(
        name="Calculation", description="calc", kind="external", source=CORE_TOOL_SOURCE,
        permission="meta", input_schema=CORE_TOOL_INPUT_SCHEMA, timeout_ms=10_000,
        budget=Budget(max_calls_per_turn=3, max_parallel=1), dangerous=False,
        is_concurrency_safe=True, mutates_workspace=False, parallel_safety="readonly",
        available_in_modes=("plan", "act"), tags=("utility", "meta"),
        enabled_by_default=True, opt_out=True))

    load_packs(manifests, registries)

    # ADD
    assert registries.tools.resolve("WeatherPeek") is not None
    # OVERRIDE
    assert registries.tools.resolve("Clock").description == "OVERRIDDEN clock."
    # REMOVE
    assert registries.tools.resolve("Calculation") is None
```

> Note: the override targets a **non-protected** tool (the loader registers first-party `tools_clock`
> with `kind="core"`, but `ToolRegistry.replace` allows metadata-preserving replacement; the user
> impl uses `kind="external"` and only changes the description — a non-downgrade field — so
> `_protected_metadata_downgrade_reasons` returns empty). If your Phase-1 loader instead loads
> first-party `Clock` as non-protected, the override is unconditionally allowed. Either way, the
> assertion holds. Confirm by `grep -n "def replace" magi_agent/tools/registry.py:57`.

- [ ] **Step 3: Run, see it fail.** `MAGI_CONFIG="$(mktemp -d)/config.toml" uv run pytest tests/packs/test_user_tool_pack.py -q`
  Expected: FAIL until the loader's add/override/remove wiring for `tool` is exercised (it was built
  in P1; if it FAILs because the loader does not yet handle the `tool` type, that is the real gap —
  fix in `magi_agent/packs/loader.py` per Step 4, otherwise it should pass directly).

- [ ] **Step 4: If the loader does not dispatch `tool`, wire it.** Grep the dispatch:
  `grep -n "tool\b\|ToolProvideContext\|ProvidesType" magi_agent/packs/context.py magi_agent/packs/loader.py`.
  The `build_context` dispatcher must, for `entry.type == "tool"`, return
  `ToolProvideContext(register=registries.tools.replace if registries.tools.resolve(entry.ref) else registries.tools.register)`
  and for a `"-"`-prefixed ref call `registries.tools.unregister(entry.ref[1:])` (catch `KeyError`).
  Show the exact replacement only if the grep proves the branch is missing; otherwise this step is a
  no-op.

- [ ] **Step 5: Run, see it pass.** Same command. Expected: PASS.

- [ ] **Step 6: Commit.**

```bash
git add tests/packs/test_user_tool_pack.py magi_agent/packs/loader.py
git commit -m "test(packs): user tool pack can add, override, and remove tool refs"
```

### Task A.3: Live-path usage confirmation (registered tool reaches the mode-scoped list)

**Files:**
- Test: `tests/packs/test_tool_pack_live_path.py`

- [ ] **Step 1: Read the live tool surface.** `grep -n "def list_available" magi_agent/tools/registry.py`
  (HEAD: `registry.py:147`) — a registered+enabled tool is offered to the model when
  `mode in available_in_modes`. That is the live path for tools.

- [ ] **Step 2: Write the test.**

```python
# tests/packs/test_tool_pack_live_path.py
from magi_agent.packs.discovery import discover_packs, firstparty_packs_dir
from magi_agent.packs.loader import load_packs
from magi_agent.packs.registries import PackRegistries


def test_pack_registered_tool_is_offered_in_act_mode():
    registries = PackRegistries.empty()
    load_packs(discover_packs(search_paths=(firstparty_packs_dir() / "tools_clock",)), registries)
    names = {m.name for m in registries.tools.list_available(mode="act")}
    assert "Clock" in names
    plan_names = {m.name for m in registries.tools.list_available(mode="plan")}
    assert "Clock" in plan_names
```

- [ ] **Step 3: Run, see it pass.** `MAGI_CONFIG="$(mktemp -d)/config.toml" uv run pytest tests/packs/test_tool_pack_live_path.py -q`
  Expected: PASS (the pack-registered tool is live, not metadata-only).

- [ ] **Step 4: Commit.**

```bash
git add tests/packs/test_tool_pack_live_path.py
git commit -m "test(packs): pack-registered tool reaches the live mode-scoped tool list"
```

---

## GROUP B — `evidence_producer` provides type  *(difficulty: easy)*

Rides the **already-live** evidence enforce point at `cli/engine.py:~2138` (`execute_pre_final_verifier_bus`
checks `assembly.evidence_requirements` against `observed_public_refs`). An evidence producer is the
thing that emits the observed ref. We bundle a first-party producer, prove a user pack can
add/override/remove a producer ref, and confirm a pack-registered producer's ref satisfies a
required-evidence gate.

### Task B.1: First-party `evidence_gitdiff` producer pack

**Files:**
- Create: `magi_agent/firstparty/packs/evidence_gitdiff/pack.toml`
- Create: `magi_agent/firstparty/packs/evidence_gitdiff/impl.py`
- Test: `tests/firstparty/test_evidence_gitdiff_pack.py`

- [ ] **Step 1: Read the precedent + the producer surface.** Re-grep the real `GitDiff` builtin:
  `grep -n 'type="GitDiff"' magi_agent/evidence/builtin.py` (HEAD: `builtin.py:81`); read its
  `producer_surfaces`. Confirm `ProducerSpec` shape: `grep -n "class ProducerSpec\|ProducerSpec"
  magi_agent/packs/context.py`. Contract for this phase: `ProducerSpec` is a frozen pydantic with
  `evidence_type: str`, `public_ref: str`, `producer_surfaces: tuple[str, ...]`, and
  `emit(observed: ObservedEvidence) -> str | None` returning the public ref it contributes.

- [ ] **Step 2: Write the failing test.**

```python
# tests/firstparty/test_evidence_gitdiff_pack.py
from magi_agent.packs.discovery import discover_packs, firstparty_packs_dir
from magi_agent.packs.loader import load_packs
from magi_agent.packs.registries import PackRegistries


def test_evidence_gitdiff_pack_registers_producer():
    registries = PackRegistries.empty()
    report = load_packs(
        discover_packs(search_paths=(firstparty_packs_dir() / "evidence_gitdiff",)),
        registries,
    )
    assert "evidence:gitdiff@1" in report.registered
    spec = registries.evidence_producers.resolve("evidence:gitdiff@1")
    assert spec is not None
    assert spec.evidence_type == "GitDiff"
    assert "tool_host" in spec.producer_surfaces
    assert spec.public_ref == "GitDiff"
```

- [ ] **Step 3: Run, see it fail.** `MAGI_CONFIG="$(mktemp -d)/config.toml" uv run pytest tests/firstparty/test_evidence_gitdiff_pack.py -q` → FAIL.

- [ ] **Step 4: Create the pack.**

```toml
# magi_agent/firstparty/packs/evidence_gitdiff/pack.toml
packId = "evidence_gitdiff"
version = "1"

[[provides]]
type = "evidence_producer"
ref = "evidence:gitdiff@1"
impl = "magi_agent.firstparty.packs.evidence_gitdiff.impl:provide_gitdiff_producer"
```

- [ ] **Step 5: Create the impl (registers via `EvidenceProducerProvideContext`).**

```python
# magi_agent/firstparty/packs/evidence_gitdiff/impl.py
from __future__ import annotations

from magi_agent.packs.context import EvidenceProducerProvideContext, ProducerSpec


def provide_gitdiff_producer(context: EvidenceProducerProvideContext) -> None:
    context.register(
        "evidence:gitdiff@1",
        ProducerSpec(
            evidence_type="GitDiff",
            public_ref="GitDiff",
            producer_surfaces=("tool_host", "transcript"),
        ),
    )
```

- [ ] **Step 6: Run, see it pass.** Same command → PASS.

- [ ] **Step 7: Commit.**

```bash
git add magi_agent/firstparty/packs/evidence_gitdiff/ tests/firstparty/test_evidence_gitdiff_pack.py
git commit -m "feat(packs): bundle GitDiff as a first-party evidence_producer pack"
```

### Task B.2: User pack add / override / remove for `evidence_producer`

**Files:**
- Test: `tests/packs/test_user_evidence_producer_pack.py`

- [ ] **Step 1: Read** the Phase-3 user-pack test for the override/remove convention.

- [ ] **Step 2: Write the failing test.**

```python
# tests/packs/test_user_evidence_producer_pack.py
from pathlib import Path

from magi_agent.packs.discovery import discover_packs, firstparty_packs_dir
from magi_agent.packs.loader import load_packs
from magi_agent.packs.registries import PackRegistries


def test_user_evidence_producer_add_override_remove(tmp_path: Path):
    user = tmp_path / "user_ev"
    user.mkdir()
    (user / "pack.toml").write_text(
        'packId = "user_ev"\nversion = "1"\n\n'
        '[[provides]]\ntype = "evidence_producer"\nref = "evidence:custom-snap@1"\n'
        'impl = "user_ev.impl:provide_custom"\n\n'
        '[[provides]]\ntype = "evidence_producer"\nref = "evidence:gitdiff@1"\n'
        'impl = "user_ev.impl:provide_gitdiff_override"\n\n'
        '[[provides]]\ntype = "evidence_producer"\nref = "-evidence:gitdiff-removable@1"\n'
    )
    (user / "impl.py").write_text(
        "from magi_agent.packs.context import ProducerSpec\n"
        "def provide_custom(ctx):\n"
        "    ctx.register('evidence:custom-snap@1', ProducerSpec(\n"
        "        evidence_type='custom:snap', public_ref='custom:snap',\n"
        "        producer_surfaces=('tool_host',)))\n"
        "def provide_gitdiff_override(ctx):\n"
        "    ctx.register('evidence:gitdiff@1', ProducerSpec(\n"
        "        evidence_type='GitDiff', public_ref='GitDiffV2',\n"
        "        producer_surfaces=('tool_host','verifier')))\n"
    )
    manifests = discover_packs(
        search_paths=(firstparty_packs_dir() / "evidence_gitdiff", user)
    )
    registries = PackRegistries.empty()
    from magi_agent.packs.context import ProducerSpec
    registries.evidence_producers.register(
        "evidence:gitdiff-removable@1",
        ProducerSpec(evidence_type="custom:rm", public_ref="rm", producer_surfaces=("tool_host",)),
    )
    load_packs(manifests, registries)

    assert registries.evidence_producers.resolve("evidence:custom-snap@1") is not None  # ADD
    assert registries.evidence_producers.resolve("evidence:gitdiff@1").public_ref == "GitDiffV2"  # OVERRIDE
    assert registries.evidence_producers.resolve("evidence:gitdiff-removable@1") is None  # REMOVE
```

- [ ] **Step 3: Run, see it fail/pass.** `MAGI_CONFIG="$(mktemp -d)/config.toml" uv run pytest tests/packs/test_user_evidence_producer_pack.py -q`.
  If the loader does not dispatch `evidence_producer`, wire it in `magi_agent/packs/loader.py`
  identically to the `tool` branch (register / replace / remove on `registries.evidence_producers`).

- [ ] **Step 4: Commit.**

```bash
git add tests/packs/test_user_evidence_producer_pack.py magi_agent/packs/loader.py
git commit -m "test(packs): user evidence_producer pack can add, override, and remove refs"
```

### Task B.3: Live-path enforce confirmation (producer ref satisfies a required-evidence gate)

**Files:**
- Test: `tests/packs/test_evidence_producer_live_path.py`

- [ ] **Step 1: Read the live enforce point.** Re-grep `grep -n "execute_pre_final_verifier_bus"
  magi_agent/cli/engine.py magi_agent/harness/verifier_bus.py` (HEAD enforce at `engine.py:2137`).
  The gate blocks when a required evidence ref is **not** in `observed_public_refs`. A pack producer
  contributes its `public_ref` to that observed set.

- [ ] **Step 2: Write the test (drives the verifier bus directly, fake-model-free, no engine boot).**

```python
# tests/packs/test_evidence_producer_live_path.py
from magi_agent.harness.verifier_bus import execute_pre_final_verifier_bus
from magi_agent.packs.discovery import discover_packs, firstparty_packs_dir
from magi_agent.packs.loader import load_packs
from magi_agent.packs.registries import PackRegistries


def test_pack_producer_ref_satisfies_required_evidence_gate():
    registries = PackRegistries.empty()
    load_packs(
        discover_packs(search_paths=(firstparty_packs_dir() / "evidence_gitdiff",)),
        registries,
    )
    spec = registries.evidence_producers.resolve("evidence:gitdiff@1")
    observed = (spec.public_ref,)  # the producer contributes its public ref

    # Gate with the producer's ref present -> pass.
    bus_pass = execute_pre_final_verifier_bus(
        required_evidence=("GitDiff",),
        required_validators=(),
        observed_public_refs=observed,
        evidence_records=(),
        document_coverage_gate_enabled=False,
    )
    assert bus_pass["decision"] == "pass"

    # Gate with the ref absent -> block (proves the gate is real, not cosmetic).
    bus_block = execute_pre_final_verifier_bus(
        required_evidence=("GitDiff",),
        required_validators=(),
        observed_public_refs=(),
        evidence_records=(),
        document_coverage_gate_enabled=False,
    )
    assert bus_block["decision"] == "block"
    assert "GitDiff" in bus_block["missingEvidence"]
```

> If `execute_pre_final_verifier_bus`'s return dict uses different keys, re-grep
> `grep -n "decision\|missingEvidence\|matchedRefs" magi_agent/harness/verifier_bus.py` and adjust
> the assertion keys to the real ones (`engine.py:2144` reads `matchedRefs`; `:2191` writes
> `decision`).

- [ ] **Step 3: Run, see it pass.** `MAGI_CONFIG="$(mktemp -d)/config.toml" uv run pytest tests/packs/test_evidence_producer_live_path.py -q` → PASS.

- [ ] **Step 4: Commit.**

```bash
git add tests/packs/test_evidence_producer_live_path.py
git commit -m "test(packs): pack evidence_producer ref satisfies the live required-evidence gate"
```

---

## GROUP C — `recipe` provides type  *(difficulty: easy-medium)*

Rides `authoring/compiler.py` (`CompileRecipePackCatalog`, `compile_recipe_pack`) and
`recipes/materializer.py` (`RecipeMaterializer`). A user recipe is a `spec` (declarative) entry that
injects a `RecipePackManifest` into the catalog. We bundle a first-party recipe pack, prove a user
recipe adds/overrides/removes a recipe ref, and confirm a pack-injected recipe materializes into the
live reliability plan.

### Task C.1: First-party `recipe_authoring_static` recipe pack (declarative `spec`)

**Files:**
- Create: `magi_agent/firstparty/packs/recipe_authoring_static/pack.toml`
- Create: `magi_agent/firstparty/packs/recipe_authoring_static/authoring_static.recipe.toml`
- Test: `tests/firstparty/test_recipe_authoring_static_pack.py`

- [ ] **Step 1: Read the precedent + recipe shapes.** Re-grep
  `grep -n "class RecipePackManifest" magi_agent/recipes/compiler.py` (HEAD: `compiler.py:890`) for
  the field set (`packId`, `displayName`, `description`, `toolRefs`, `validatorRefs`,
  `evidenceRefs`, …). Read `with_first_party_packs` / `_first_party_packs`
  (`compiler.py:1341`/`:1932`) to see how first-party recipes are assembled today (the hardcode this
  pack replaces). Confirm `RecipeProvideContext` + how a `spec` relpath is read:
  `grep -n "spec\|RecipeProvideContext\|RecipePackManifest" magi_agent/packs/context.py magi_agent/packs/loader.py`.

- [ ] **Step 2: Write the failing test.**

```python
# tests/firstparty/test_recipe_authoring_static_pack.py
from magi_agent.packs.discovery import discover_packs, firstparty_packs_dir
from magi_agent.packs.loader import load_packs
from magi_agent.packs.registries import PackRegistries


def test_recipe_authoring_static_pack_registers_recipe():
    registries = PackRegistries.empty()
    report = load_packs(
        discover_packs(search_paths=(firstparty_packs_dir() / "recipe_authoring_static",)),
        registries,
    )
    assert "recipe:authoring-static@1" in report.registered
    manifest = registries.recipes.resolve("recipe:authoring-static@1")
    assert manifest is not None
    assert manifest.pack_id == "authoring-static"
    assert "validator:sourceOpened@1" in manifest.validator_refs
```

- [ ] **Step 3: Run, see it fail.** `MAGI_CONFIG="$(mktemp -d)/config.toml" uv run pytest tests/firstparty/test_recipe_authoring_static_pack.py -q` → FAIL.

- [ ] **Step 4: Create the pack manifest + the declarative recipe spec.**

```toml
# magi_agent/firstparty/packs/recipe_authoring_static/pack.toml
packId = "recipe_authoring_static"
version = "1"

[[provides]]
type = "recipe"
ref = "recipe:authoring-static@1"
spec = "authoring_static.recipe.toml"
```

```toml
# magi_agent/firstparty/packs/recipe_authoring_static/authoring_static.recipe.toml
packId = "authoring-static"
version = "1"
displayName = "Authoring (static)"
description = "Read-only source authoring recipe: open source + exact-quote validators."
defaultEnabled = true
toolRefs = ["FileRead", "SourceOpen"]
validatorRefs = ["validator:sourceOpened@1", "validator:quoteExactMatch@1"]
evidenceRefs = ["openedSourceSnapshot", "quoteDigest"]
```

> The `spec` keys are the `RecipePackManifest` camelCase aliases (`packId`, `displayName`,
> `validatorRefs`, …) — confirmed against `compiler.py:890`. The loader reads the relpath
> (resolved against the pack dir), `tomllib.load`s it, and validates via
> `RecipePackManifest.model_validate(data)` inside `RecipeProvideContext.register`.

- [ ] **Step 5: If the loader does not yet read `spec` relpaths for `recipe`, wire it.** Grep
  `grep -n "spec\b" magi_agent/packs/loader.py`. The recipe branch must resolve
  `pack_dir / entry.spec`, `tomllib.load`, and call
  `RecipeProvideContext(register=registries.recipes.register).register(entry.ref,
  RecipePackManifest.model_validate(data))`. Show the exact replacement only if the grep proves it
  missing.

- [ ] **Step 6: Run, see it pass.** Same command → PASS.

- [ ] **Step 7: Commit.**

```bash
git add magi_agent/firstparty/packs/recipe_authoring_static/ tests/firstparty/test_recipe_authoring_static_pack.py
git commit -m "feat(packs): bundle authoring-static as a first-party declarative recipe pack"
```

### Task C.2: User recipe pack add / override / remove

**Files:**
- Test: `tests/packs/test_user_recipe_pack.py`

- [ ] **Step 1: Write the failing test.**

```python
# tests/packs/test_user_recipe_pack.py
from pathlib import Path

from magi_agent.packs.discovery import discover_packs, firstparty_packs_dir
from magi_agent.packs.loader import load_packs
from magi_agent.packs.registries import PackRegistries


def test_user_recipe_add_override_remove(tmp_path: Path):
    user = tmp_path / "user_recipe"
    user.mkdir()
    (user / "pack.toml").write_text(
        'packId = "user_recipe"\nversion = "1"\n\n'
        '[[provides]]\ntype = "recipe"\nref = "recipe:my-flow@1"\nspec = "my_flow.recipe.toml"\n\n'
        '[[provides]]\ntype = "recipe"\nref = "recipe:authoring-static@1"\nspec = "override.recipe.toml"\n\n'
        '[[provides]]\ntype = "recipe"\nref = "-recipe:removable@1"\n'
    )
    (user / "my_flow.recipe.toml").write_text(
        'packId = "my-flow"\nversion = "1"\ndisplayName = "My Flow"\n'
        'description = "A user flow."\ntoolRefs = ["FileRead"]\n'
    )
    (user / "override.recipe.toml").write_text(
        'packId = "authoring-static"\nversion = "2"\ndisplayName = "Authoring OVERRIDE"\n'
        'description = "Overridden authoring recipe."\nvalidatorRefs = ["validator:sourceOpened@1"]\n'
    )
    manifests = discover_packs(
        search_paths=(firstparty_packs_dir() / "recipe_authoring_static", user)
    )
    registries = PackRegistries.empty()
    from magi_agent.recipes.compiler import RecipePackManifest
    registries.recipes.register(
        "recipe:removable@1",
        RecipePackManifest(packId="removable", displayName="rm", description="removable"),
    )
    load_packs(manifests, registries)

    assert registries.recipes.resolve("recipe:my-flow@1") is not None  # ADD
    assert registries.recipes.resolve("recipe:authoring-static@1").version == "2"  # OVERRIDE
    assert registries.recipes.resolve("recipe:removable@1") is None  # REMOVE
```

- [ ] **Step 2: Run, see it pass.** `MAGI_CONFIG="$(mktemp -d)/config.toml" uv run pytest tests/packs/test_user_recipe_pack.py -q` (wire the loader `recipe` branch if absent, as in C.1 Step 5).

- [ ] **Step 3: Commit.**

```bash
git add tests/packs/test_user_recipe_pack.py magi_agent/packs/loader.py
git commit -m "test(packs): user recipe pack can add, override, and remove recipe refs"
```

### Task C.3: Live-path materialization confirmation

**Files:**
- Test: `tests/packs/test_recipe_pack_live_path.py`

- [ ] **Step 1: Read the materializer + its real call signature.** Re-grep
  `grep -n "class RecipeMaterializer\|def with_reliability_defaults\|def materialize\|class RecipeSnapshot\|class ReliabilityMaterializationPlan\|required_validators"
  magi_agent/recipes/materializer.py magi_agent/recipes/compiler.py` (HEAD: materializer
  `with_reliability_defaults` at `:80`, `materialize` at `:89`; `RecipeSnapshot` at
  `compiler.py:1091`; plan model `ReliabilityMaterializationPlan` at `materializer.py:46`). Confirm
  the **real** call shape against an existing test:
  `grep -n "materialize(\|RecipeSnapshot(" tests/test_live_ts_surface_recipe_integration.py`. Two
  facts that the test below depends on (do not paraphrase — they are exact):
  - The materializer is constructed via the `with_reliability_defaults()` classmethod and
    `materialize(snapshot, *, modelProvider=..., modelLabel=...)` **requires** both keyword args (no
    default). A bare `RecipeMaterializer().materialize(snapshot)` raises.
  - `ReliabilityMaterializationPlan` has **no** `materialized_validator_refs` field. Materialized
    validator refs surface on `plan.final_gate_policy.required_validators`
    (`grep -n "required_validators" magi_agent/recipes/materializer.py`); the order ref
    `"order:06-validators"` lands in `plan.materialization_order_refs`.

- [ ] **Step 2: Write the test (build a `RecipeSnapshot` for the pack recipe, materialize with the
  real signature, assert the validator ref flows into the live final-gate policy).**

```python
# tests/packs/test_recipe_pack_live_path.py
from magi_agent.packs.discovery import discover_packs, firstparty_packs_dir
from magi_agent.packs.loader import load_packs
from magi_agent.packs.registries import PackRegistries
from magi_agent.recipes.compiler import RecipeSnapshot, build_recipe_snapshot_id
from magi_agent.recipes.materializer import RecipeMaterializer


def test_pack_recipe_materializes_validator_refs():
    registries = PackRegistries.empty()
    load_packs(
        discover_packs(search_paths=(firstparty_packs_dir() / "recipe_authoring_static",)),
        registries,
    )
    manifest = registries.recipes.resolve("recipe:authoring-static@1")
    assert manifest is not None
    # Compose a single-pack snapshot naming the pack-provided recipe.
    snapshot = RecipeSnapshot(
        snapshotId=build_recipe_snapshot_id((manifest.pack_id,)),
        resolvedProfile={"taskType": "authoring"},
        selectedPackIds=(manifest.pack_id,),
    )
    plan = RecipeMaterializer.with_reliability_defaults().materialize(
        snapshot,
        modelProvider="google",
        modelLabel="gemini-3.5-flash",
    )
    # The pack recipe's selected pack id reaches the live plan.
    assert manifest.pack_id in plan.selected_pack_ids
    # The validators order-ref is materialized into the live plan ordering.
    assert "order:06-validators" in plan.materialization_order_refs
```

> If `RecipeSnapshot` / `build_recipe_snapshot_id` field names or import paths differ, re-grep
> `grep -n "class RecipeSnapshot\|def build_recipe_snapshot_id\|snapshotId\|resolvedProfile\|selectedPackIds"
> magi_agent/recipes/compiler.py` and adjust. The assertion intent stays fixed: **the pack recipe's
> pack id reaches `plan.selected_pack_ids` and the validators stage reaches
> `plan.materialization_order_refs`** — i.e. a pack-provided recipe is consumed by the live
> materializer, not catalogued only. (Use `plan.final_gate_policy.required_validators` instead if you
> need to assert a specific validator ref and the snapshot resolves one for this pack.)

- [ ] **Step 3: Run, see it pass.** `MAGI_CONFIG="$(mktemp -d)/config.toml" uv run pytest tests/packs/test_recipe_pack_live_path.py -q` → PASS.

- [ ] **Step 4: Commit.**

```bash
git add tests/packs/test_recipe_pack_live_path.py
git commit -m "test(packs): pack-provided recipe materializes its validator refs into the live plan"
```

---

## GROUP D — `connector` (MCP) provides type  *(difficulty: medium)*

Rides `plugins/mcp_adapter.py` (`McpAdapter`, `McpAdapterConfig`, `_tool_manifest_from_mcp`) and
`plugins/extension_boundary.py` (the `mcp_server.load` / `mcp_tool.project` operations). A connector
is an MCP-server descriptor + its projected tool manifests. We bundle a first-party connector pack,
prove a user pack adds/overrides/removes a connector ref, and confirm a pack-registered connector's
projected tools reach the tool registry (the live offer path, shared with Group A).

### Task D.1: First-party `connector_local_readonly` pack

**Files:**
- Create: `magi_agent/firstparty/packs/connector_local_readonly/pack.toml`
- Create: `magi_agent/firstparty/packs/connector_local_readonly/impl.py`
- Test: `tests/firstparty/test_connector_local_readonly_pack.py`

- [ ] **Step 1: Read the precedent + MCP surface.** Re-grep
  `grep -n "class McpAdapter\|class McpAdapterConfig\|def _tool_manifest_from_mcp\|class McpServerSecurityManifest"
  magi_agent/plugins/mcp_adapter.py` (HEAD: `:278`/`:66`/`:665`/`:113`). Read
  `extension_boundary.py:281` `_kind_for_operation` for the `mcp_server`/`mcp_tool` kinds. Confirm
  `ConnectorSpec`: `grep -n "class ConnectorSpec\|ConnectorProvideContext" magi_agent/packs/context.py`.
  Contract: `ConnectorSpec` is a frozen pydantic with `server_ref: str`,
  `tool_manifests: tuple[ToolManifest, ...]`, `readonly: bool = True`.

- [ ] **Step 2: Write the failing test.**

```python
# tests/firstparty/test_connector_local_readonly_pack.py
from magi_agent.packs.discovery import discover_packs, firstparty_packs_dir
from magi_agent.packs.loader import load_packs
from magi_agent.packs.registries import PackRegistries


def test_connector_local_readonly_pack_registers_connector():
    registries = PackRegistries.empty()
    report = load_packs(
        discover_packs(search_paths=(firstparty_packs_dir() / "connector_local_readonly",)),
        registries,
    )
    assert "connector:local-readonly@1" in report.registered
    spec = registries.connectors.resolve("connector:local-readonly@1")
    assert spec is not None
    assert spec.server_ref == "local-readonly"
    assert spec.readonly is True
    assert any(m.name == "LocalSourceOpen" for m in spec.tool_manifests)
```

- [ ] **Step 3: Run, see it fail.** `MAGI_CONFIG="$(mktemp -d)/config.toml" uv run pytest tests/firstparty/test_connector_local_readonly_pack.py -q` → FAIL.

- [ ] **Step 4: Create the pack.**

```toml
# magi_agent/firstparty/packs/connector_local_readonly/pack.toml
packId = "connector_local_readonly"
version = "1"

[[provides]]
type = "connector"
ref = "connector:local-readonly@1"
impl = "magi_agent.firstparty.packs.connector_local_readonly.impl:provide_connector"
```

```python
# magi_agent/firstparty/packs/connector_local_readonly/impl.py
from __future__ import annotations

from magi_agent.packs.context import ConnectorProvideContext, ConnectorSpec
from magi_agent.tools.catalog import CORE_TOOL_SOURCE, CORE_TOOL_INPUT_SCHEMA
from magi_agent.tools.manifest import Budget, ToolManifest


def provide_connector(context: ConnectorProvideContext) -> None:
    context.register(
        "connector:local-readonly@1",
        ConnectorSpec(
            server_ref="local-readonly",
            readonly=True,
            tool_manifests=(
                ToolManifest(
                    name="LocalSourceOpen",
                    description="Open a local source file via the read-only connector.",
                    kind="external",
                    source=CORE_TOOL_SOURCE,
                    permission="read",
                    input_schema=CORE_TOOL_INPUT_SCHEMA,
                    timeout_ms=30_000,
                    budget=Budget(max_calls_per_turn=10, max_parallel=1),
                    dangerous=False,
                    is_concurrency_safe=True,
                    mutates_workspace=False,
                    parallel_safety="readonly",
                    available_in_modes=("plan", "act"),
                    tags=("connector", "source", "read"),
                    enabled_by_default=True,
                    opt_out=True,
                ),
            ),
        ),
    )
```

- [ ] **Step 5: Run, see it pass.** Same command → PASS.

- [ ] **Step 6: Commit.**

```bash
git add magi_agent/firstparty/packs/connector_local_readonly/ tests/firstparty/test_connector_local_readonly_pack.py
git commit -m "feat(packs): bundle a read-only local connector as a first-party pack"
```

### Task D.2: User connector pack add / override / remove

**Files:**
- Test: `tests/packs/test_user_connector_pack.py`

- [ ] **Step 1: Write the failing test.**

```python
# tests/packs/test_user_connector_pack.py
from pathlib import Path

from magi_agent.packs.discovery import discover_packs, firstparty_packs_dir
from magi_agent.packs.loader import load_packs
from magi_agent.packs.registries import PackRegistries


def test_user_connector_add_override_remove(tmp_path: Path):
    user = tmp_path / "user_conn"
    user.mkdir()
    (user / "pack.toml").write_text(
        'packId = "user_conn"\nversion = "1"\n\n'
        '[[provides]]\ntype = "connector"\nref = "connector:my-mcp@1"\nimpl = "user_conn.impl:provide_my"\n\n'
        '[[provides]]\ntype = "connector"\nref = "connector:local-readonly@1"\nimpl = "user_conn.impl:provide_override"\n\n'
        '[[provides]]\ntype = "connector"\nref = "-connector:removable@1"\n'
    )
    (user / "impl.py").write_text(
        "from magi_agent.packs.context import ConnectorSpec\n"
        "def provide_my(ctx):\n"
        "    ctx.register('connector:my-mcp@1', ConnectorSpec(server_ref='my-mcp', readonly=True, tool_manifests=()))\n"
        "def provide_override(ctx):\n"
        "    ctx.register('connector:local-readonly@1', ConnectorSpec(server_ref='local-readonly-v2', readonly=True, tool_manifests=()))\n"
    )
    manifests = discover_packs(
        search_paths=(firstparty_packs_dir() / "connector_local_readonly", user)
    )
    registries = PackRegistries.empty()
    from magi_agent.packs.context import ConnectorSpec
    registries.connectors.register(
        "connector:removable@1",
        ConnectorSpec(server_ref="rm", readonly=True, tool_manifests=()),
    )
    load_packs(manifests, registries)

    assert registries.connectors.resolve("connector:my-mcp@1") is not None  # ADD
    assert registries.connectors.resolve("connector:local-readonly@1").server_ref == "local-readonly-v2"  # OVERRIDE
    assert registries.connectors.resolve("connector:removable@1") is None  # REMOVE
```

- [ ] **Step 2: Run, see it pass.** `MAGI_CONFIG="$(mktemp -d)/config.toml" uv run pytest tests/packs/test_user_connector_pack.py -q` (wire loader `connector` branch if absent).

- [ ] **Step 3: Commit.**

```bash
git add tests/packs/test_user_connector_pack.py magi_agent/packs/loader.py
git commit -m "test(packs): user connector pack can add, override, and remove connector refs"
```

### Task D.3: Live-path usage confirmation (connector tools reach the tool registry)

**Files:**
- Create: `magi_agent/packs/connector_projection.py`
- Test: `tests/packs/test_connector_pack_live_path.py`

- [ ] **Step 1: Read the projection precedent.** `_tool_manifest_from_mcp` (`mcp_adapter.py:665`)
  is how MCP tools become `ToolManifest`s. Our connector already carries `ToolManifest`s, so the
  live step is just registering them into `registries.tools` — the shared Group-A offer path.

- [ ] **Step 2: Write the failing test.**

```python
# tests/packs/test_connector_pack_live_path.py
from magi_agent.packs.connector_projection import project_connector_tools
from magi_agent.packs.discovery import discover_packs, firstparty_packs_dir
from magi_agent.packs.loader import load_packs
from magi_agent.packs.registries import PackRegistries


def test_connector_tools_reach_the_live_tool_registry():
    registries = PackRegistries.empty()
    load_packs(
        discover_packs(search_paths=(firstparty_packs_dir() / "connector_local_readonly",)),
        registries,
    )
    project_connector_tools(registries)
    names = {m.name for m in registries.tools.list_available(mode="act")}
    assert "LocalSourceOpen" in names
```

- [ ] **Step 3: Run, see it fail.** `MAGI_CONFIG="$(mktemp -d)/config.toml" uv run pytest tests/packs/test_connector_pack_live_path.py -q` → FAIL (`connector_projection` missing).

- [ ] **Step 4: Implement the projector.**

```python
# magi_agent/packs/connector_projection.py
from __future__ import annotations

from magi_agent.packs.registries import PackRegistries


def project_connector_tools(registries: PackRegistries) -> tuple[str, ...]:
    """Register each loaded connector's projected ToolManifests into the live
    tool registry, so connector tools share the same mode-scoped offer path as
    native tools (the Group-A live seam). Returns the registered tool names."""
    registered: list[str] = []
    for ref in registries.connectors.list_refs():
        spec = registries.connectors.resolve(ref)
        if spec is None:
            continue
        for manifest in spec.tool_manifests:
            if registries.tools.resolve(manifest.name) is None:
                registries.tools.register(manifest)
            else:
                registries.tools.replace(manifest)
            registered.append(manifest.name)
    return tuple(registered)
```

- [ ] **Step 5: Run, see it pass.** Same command → PASS.

- [ ] **Step 6: Commit.**

```bash
git add magi_agent/packs/connector_projection.py tests/packs/test_connector_pack_live_path.py
git commit -m "feat(packs): project connector tools into the live tool registry"
```

---

## GROUP E — `harness` provides type  *(difficulty: easy)*

Rides `harness/resolved.py` resolved presets (`ResolvedHarnessPack`, `ResolvedHarnessPresetState`).
A harness provides a named `ResolvedHarnessPack` (tools/hooks/permission defaults) that the resolved
preset state consumes. We bundle a first-party harness pack, prove a user pack adds/overrides/removes
a harness ref, and confirm a pack-provided harness's components are read by the resolved-state seam.

### Task E.1: First-party `harness_coding_lean` pack

**Files:**
- Create: `magi_agent/firstparty/packs/harness_coding_lean/pack.toml`
- Create: `magi_agent/firstparty/packs/harness_coding_lean/impl.py`
- Test: `tests/firstparty/test_harness_coding_lean_pack.py`

- [ ] **Step 1: Read the precedent + harness shape.** Re-grep
  `grep -n "class ResolvedHarnessPack\|coding=ResolvedHarnessPack" magi_agent/harness/resolved.py`
  (HEAD: `:122`, the `coding` pack built at `:373`). Read its `components` keys
  (`tools`, `hooks`, `childAgent`, `permissionDefaults`) and `opt_out_allowed`. Confirm
  `HarnessProvideContext`: `grep -n "HarnessProvideContext" magi_agent/packs/context.py`.

- [ ] **Step 2: Write the failing test.**

```python
# tests/firstparty/test_harness_coding_lean_pack.py
from magi_agent.packs.discovery import discover_packs, firstparty_packs_dir
from magi_agent.packs.loader import load_packs
from magi_agent.packs.registries import PackRegistries


def test_harness_coding_lean_pack_registers_pack():
    registries = PackRegistries.empty()
    report = load_packs(
        discover_packs(search_paths=(firstparty_packs_dir() / "harness_coding_lean",)),
        registries,
    )
    assert "harness:coding-lean@1" in report.registered
    pack = registries.harnesses.resolve("harness:coding-lean@1")
    assert pack is not None
    assert pack.enabled is True
    assert "FileEdit" in pack.components["tools"]
```

- [ ] **Step 3: Run, see it fail.** `MAGI_CONFIG="$(mktemp -d)/config.toml" uv run pytest tests/firstparty/test_harness_coding_lean_pack.py -q` → FAIL.

- [ ] **Step 4: Create the pack.**

```toml
# magi_agent/firstparty/packs/harness_coding_lean/pack.toml
packId = "harness_coding_lean"
version = "1"

[[provides]]
type = "harness"
ref = "harness:coding-lean@1"
impl = "magi_agent.firstparty.packs.harness_coding_lean.impl:provide_harness"
```

```python
# magi_agent/firstparty/packs/harness_coding_lean/impl.py
from __future__ import annotations

from magi_agent.harness.resolved import ResolvedHarnessPack
from magi_agent.packs.context import HarnessProvideContext


def provide_harness(context: HarnessProvideContext) -> None:
    context.register(
        "harness:coding-lean@1",
        ResolvedHarnessPack(
            enabled=True,
            source="custom-plugin",
            components={
                "tools": ("FileRead", "FileEdit", "PatchApply"),
                "hooks": ("coding-verification",),
                "childAgent": (),
                "permissionDefaults": ("write_requires_act",),
            },
            opt_out_allowed=("childReview",),
        ),
    )
```

- [ ] **Step 5: Run, see it pass.** Same command → PASS.

- [ ] **Step 6: Commit.**

```bash
git add magi_agent/firstparty/packs/harness_coding_lean/ tests/firstparty/test_harness_coding_lean_pack.py
git commit -m "feat(packs): bundle a lean coding harness as a first-party pack"
```

### Task E.2: User harness pack add / override / remove

**Files:**
- Test: `tests/packs/test_user_harness_pack.py`

- [ ] **Step 1: Write the failing test.**

```python
# tests/packs/test_user_harness_pack.py
from pathlib import Path

from magi_agent.packs.discovery import discover_packs, firstparty_packs_dir
from magi_agent.packs.loader import load_packs
from magi_agent.packs.registries import PackRegistries


def test_user_harness_add_override_remove(tmp_path: Path):
    user = tmp_path / "user_harness"
    user.mkdir()
    (user / "pack.toml").write_text(
        'packId = "user_harness"\nversion = "1"\n\n'
        '[[provides]]\ntype = "harness"\nref = "harness:my-pack@1"\nimpl = "user_harness.impl:provide_my"\n\n'
        '[[provides]]\ntype = "harness"\nref = "harness:coding-lean@1"\nimpl = "user_harness.impl:provide_override"\n\n'
        '[[provides]]\ntype = "harness"\nref = "-harness:removable@1"\n'
    )
    (user / "impl.py").write_text(
        "from magi_agent.harness.resolved import ResolvedHarnessPack\n"
        "def provide_my(ctx):\n"
        "    ctx.register('harness:my-pack@1', ResolvedHarnessPack(enabled=True, source='custom-plugin',\n"
        "        components={'tools': ('FileRead',)}))\n"
        "def provide_override(ctx):\n"
        "    ctx.register('harness:coding-lean@1', ResolvedHarnessPack(enabled=False, source='custom-plugin',\n"
        "        components={'tools': ('FileRead',)}))\n"
    )
    manifests = discover_packs(
        search_paths=(firstparty_packs_dir() / "harness_coding_lean", user)
    )
    registries = PackRegistries.empty()
    from magi_agent.harness.resolved import ResolvedHarnessPack
    registries.harnesses.register(
        "harness:removable@1",
        ResolvedHarnessPack(enabled=True, source="custom-plugin", components={"tools": ()}),
    )
    load_packs(manifests, registries)

    assert registries.harnesses.resolve("harness:my-pack@1") is not None  # ADD
    assert registries.harnesses.resolve("harness:coding-lean@1").enabled is False  # OVERRIDE
    assert registries.harnesses.resolve("harness:removable@1") is None  # REMOVE
```

- [ ] **Step 2: Run, see it pass.** `MAGI_CONFIG="$(mktemp -d)/config.toml" uv run pytest tests/packs/test_user_harness_pack.py -q` (wire loader `harness` branch if absent).

- [ ] **Step 3: Commit.**

```bash
git add tests/packs/test_user_harness_pack.py magi_agent/packs/loader.py
git commit -m "test(packs): user harness pack can add, override, and remove harness refs"
```

### Task E.3: Live-path usage confirmation (harness components read by resolved-state seam)

**Files:**
- Create: `magi_agent/packs/harness_projection.py`
- Test: `tests/packs/test_harness_pack_live_path.py`

- [ ] **Step 1: Read the resolved-state consumption.** `build_default_resolved_harness_state`
  (`resolved.py:311`) assembles the live `ResolvedHarnessPresetState` from `ResolvedHarnessPack`s;
  `_default_effective_harness_packs` (`:424`) decides which packs are effective. The live seam reads
  a pack's `components["tools"]` / `effective_hooks`. We inject the pack-provided harness into a
  resolved state and assert its tools survive.

- [ ] **Step 2: Write the failing test.**

```python
# tests/packs/test_harness_pack_live_path.py
from magi_agent.harness.resolved import build_default_resolved_harness_state
from magi_agent.packs.discovery import discover_packs, firstparty_packs_dir
from magi_agent.packs.harness_projection import apply_harness_pack
from magi_agent.packs.loader import load_packs
from magi_agent.packs.registries import PackRegistries


def test_pack_harness_components_reach_resolved_state():
    registries = PackRegistries.empty()
    load_packs(
        discover_packs(search_paths=(firstparty_packs_dir() / "harness_coding_lean",)),
        registries,
    )
    pack = registries.harnesses.resolve("harness:coding-lean@1")
    state = build_default_resolved_harness_state(agent_role="coding")
    updated = apply_harness_pack(state, slot="coding", pack=pack)
    assert "FileEdit" in updated.coding.components["tools"]
    assert updated.coding.enabled is True
```

- [ ] **Step 3: Run, see it fail.** `MAGI_CONFIG="$(mktemp -d)/config.toml" uv run pytest tests/packs/test_harness_pack_live_path.py -q` → FAIL (`harness_projection` missing).

- [ ] **Step 4: Implement the projector (uses `ResolvedHarnessPresetState.model_copy`, which is
  alias-aware — see `resolved.py:74`).**

```python
# magi_agent/packs/harness_projection.py
from __future__ import annotations

from magi_agent.harness.resolved import ResolvedHarnessPack, ResolvedHarnessPresetState


def apply_harness_pack(
    state: ResolvedHarnessPresetState,
    *,
    slot: str,
    pack: ResolvedHarnessPack,
) -> ResolvedHarnessPresetState:
    """Inject a pack-provided ResolvedHarnessPack into one of the resolved
    state's role slots (general/coding/research/verification), so a pack harness
    participates in the live resolved preset on equal footing with first-party."""
    if slot not in {"general", "coding", "research", "verification"}:
        raise ValueError(f"unknown harness slot: {slot}")
    return state.model_copy(update={slot: pack})
```

- [ ] **Step 5: Run, see it pass.** Same command → PASS.

- [ ] **Step 6: Commit.**

```bash
git add magi_agent/packs/harness_projection.py tests/packs/test_harness_pack_live_path.py
git commit -m "feat(packs): inject pack-provided harness into the live resolved preset state"
```

---

## GROUP F — `callback` provides type  *(difficulty: medium)*

Rides `adk_bridge/callback_adapter.py` → `hooks/bus.py` `HookBus`. The keystone gap: `hooks/registry.py`
`HookRegistry` **exists but is unexposed** — there is no discovery seam that loads user hook manifests
into the live `HookBus`. This group exposes that discovery: a `callback` pack provides a
`HookManifest` + handler, the loader registers it via `CallbackProvideContext`, and a projector turns
the registry into the `RegisteredHook` tuple the `HookBus` consumes. We bundle a first-party callback
pack, prove a user pack adds/overrides/removes a callback ref, and confirm a pack callback fires live
through the `HookBus`.

### Task F.1: First-party `callback_turn_audit` pack (a `beforeTurnStart` handler)

**Files:**
- Create: `magi_agent/firstparty/packs/callback_turn_audit/pack.toml`
- Create: `magi_agent/firstparty/packs/callback_turn_audit/impl.py`
- Test: `tests/firstparty/test_callback_turn_audit_pack.py`

- [ ] **Step 1: Read the precedent + hook surface.** Re-grep
  `grep -n "class HookManifest\|class HookPoint\|BEFORE_TURN_START" magi_agent/hooks/manifest.py`
  (HEAD: `:38`/`:18`/`:19`), `grep -n "class HookResult" magi_agent/hooks/result.py`,
  `grep -n "class HookContext" magi_agent/hooks/context.py`,
  `grep -n "class RegisteredHook\|class HookBus" magi_agent/hooks/bus.py` (HEAD: `:32`/`:77`), and
  `grep -n "class HookRegistry\|def register\|def list_enabled" magi_agent/hooks/registry.py`
  (HEAD: `:19`/`:23`/`:99`). Confirm `CallbackProvideContext`:
  `grep -n "CallbackProvideContext" magi_agent/packs/context.py`. Contract: its `.register` takes
  `(manifest: HookManifest, handler: HookHandler)` and stores both into `registries.hooks`
  (`HookRegistry`) plus a parallel handler map the projector reads.

- [ ] **Step 2: Write the failing test.**

```python
# tests/firstparty/test_callback_turn_audit_pack.py
from magi_agent.hooks.manifest import HookPoint
from magi_agent.packs.discovery import discover_packs, firstparty_packs_dir
from magi_agent.packs.loader import load_packs
from magi_agent.packs.registries import PackRegistries


def test_callback_turn_audit_pack_registers_hook():
    registries = PackRegistries.empty()
    report = load_packs(
        discover_packs(search_paths=(firstparty_packs_dir() / "callback_turn_audit",)),
        registries,
    )
    assert "turn-audit" in report.registered
    manifest = registries.hooks.resolve("turn-audit")
    assert manifest is not None
    assert manifest.point is HookPoint.BEFORE_TURN_START
    assert registries.hooks_handler("turn-audit") is not None
```

- [ ] **Step 3: Run, see it fail.** `MAGI_CONFIG="$(mktemp -d)/config.toml" uv run pytest tests/firstparty/test_callback_turn_audit_pack.py -q` → FAIL.

- [ ] **Step 4: Create the pack.**

```toml
# magi_agent/firstparty/packs/callback_turn_audit/pack.toml
packId = "callback_turn_audit"
version = "1"

[[provides]]
type = "callback"
ref = "turn-audit"
impl = "magi_agent.firstparty.packs.callback_turn_audit.impl:provide_callback"
priority = 100
phase = "beforeTurnStart"
```

```python
# magi_agent/firstparty/packs/callback_turn_audit/impl.py
from __future__ import annotations

from magi_agent.hooks.context import HookContext
from magi_agent.hooks.manifest import HookManifest, HookPoint
from magi_agent.hooks.result import HookResult
from magi_agent.packs.context import CallbackProvideContext
from magi_agent.tools.manifest import ToolSource


def _turn_audit_handler(context: HookContext) -> HookResult:
    # Pure-observe, non-blocking audit; never blocks the turn.
    return HookResult(action="continue", reason="turn-audit observed")


def provide_callback(context: CallbackProvideContext) -> None:
    context.register(
        HookManifest(
            name="turn-audit",
            point=HookPoint.BEFORE_TURN_START,
            description="Record a per-turn audit marker at turn start.",
            source=ToolSource(kind="native-plugin", package="magi_agent.firstparty"),
            priority=100,
            blocking=False,
            opt_out=True,
        ),
        _turn_audit_handler,
    )
```

> `HookResult` action values are confirmed by `hooks/result.py`; `HookManifest.source` is a
> `ToolSource` (`manifest.py:9`). `blocking=False` makes this a fire-and-forget audit so it can never
> alter the turn outcome — the safe live-default for an authored callback.

- [ ] **Step 5: Run, see it pass.** Same command → PASS (requires the Phase-2 `registries.hooks` +
  `registries.hooks_handler` accessor; if missing, that is a P2 gap — add a `_hook_handlers: dict`
  beside the `HookRegistry` in `magi_agent/packs/registries.py` and a `hooks_handler(name)` getter).

- [ ] **Step 6: Commit.**

```bash
git add magi_agent/firstparty/packs/callback_turn_audit/ tests/firstparty/test_callback_turn_audit_pack.py
git commit -m "feat(packs): bundle a turn-start audit callback as a first-party pack"
```

### Task F.2: User callback pack add / override / remove

**Files:**
- Test: `tests/packs/test_user_callback_pack.py`

- [ ] **Step 1: Read the override/remove rules for hooks.** `HookRegistry.replace`
  (`registry.py:35`) preserves protected metadata; `unregister` raises on `unregister_protected`
  (`registry.py:60`). Our `turn-audit` is `native-plugin`-sourced and `opt_out=True`, so
  `is_protected_manifest` is False (it is not `security_critical`, not `hard_safety`, and
  `opt_out=True`) — overridable. But `_is_non_user_unregisterable` returns True for `native-plugin`
  sources, so `unregister` of `turn-audit` would raise. Therefore the **remove** sub-test must target
  a **user-sourced** removable hook (kind `custom-plugin`), not the first-party one. Confirm:
  `grep -n "_NON_USER_UNREGISTERABLE_SOURCES\|def is_unregister_protected_manifest" magi_agent/hooks/registry.py`.

- [ ] **Step 2: Write the failing test.**

```python
# tests/packs/test_user_callback_pack.py
from pathlib import Path

from magi_agent.packs.discovery import discover_packs, firstparty_packs_dir
from magi_agent.packs.loader import load_packs
from magi_agent.packs.registries import PackRegistries


def test_user_callback_add_override_remove(tmp_path: Path):
    user = tmp_path / "user_cb"
    user.mkdir()
    (user / "pack.toml").write_text(
        'packId = "user_cb"\nversion = "1"\n\n'
        '[[provides]]\ntype = "callback"\nref = "my-marker"\nimpl = "user_cb.impl:provide_my"\nphase = "afterTurnEnd"\n\n'
        '[[provides]]\ntype = "callback"\nref = "turn-audit"\nimpl = "user_cb.impl:provide_override"\nphase = "beforeTurnStart"\n\n'
        '[[provides]]\ntype = "callback"\nref = "-removable-cb"\n'
    )
    (user / "impl.py").write_text(
        "from magi_agent.hooks.manifest import HookManifest, HookPoint\n"
        "from magi_agent.hooks.result import HookResult\n"
        "from magi_agent.tools.manifest import ToolSource\n"
        "_SRC = ToolSource(kind='custom-plugin', package='user_cb')\n"
        "def _h(ctx):\n    return HookResult(action='continue')\n"
        "def provide_my(ctx):\n"
        "    ctx.register(HookManifest(name='my-marker', point=HookPoint.AFTER_TURN_END,\n"
        "        description='user marker', source=_SRC, blocking=False), _h)\n"
        "def provide_override(ctx):\n"
        "    ctx.register(HookManifest(name='turn-audit', point=HookPoint.BEFORE_TURN_START,\n"
        "        description='OVERRIDDEN audit', source=_SRC, blocking=False), _h)\n"
    )
    manifests = discover_packs(
        search_paths=(firstparty_packs_dir() / "callback_turn_audit", user)
    )
    registries = PackRegistries.empty()
    # seed a removable user-sourced hook so '-removable-cb' has a target
    from magi_agent.hooks.manifest import HookManifest, HookPoint
    from magi_agent.tools.manifest import ToolSource
    registries.hooks.register(HookManifest(
        name="removable-cb", point=HookPoint.AFTER_TURN_END, description="rm",
        source=ToolSource(kind="custom-plugin", package="user_cb"), blocking=False))
    load_packs(manifests, registries)

    assert registries.hooks.resolve("my-marker") is not None  # ADD
    assert registries.hooks.resolve("turn-audit").description == "OVERRIDDEN audit"  # OVERRIDE
    assert registries.hooks.resolve("removable-cb") is None  # REMOVE
```

> The override assertion holds because `HookRegistry.replace` keeps the new manifest when the existing
> one is **not** protected (`turn-audit` is `opt_out=True` native-plugin → `protected=False`), and the
> description is free to change. The remove assertion targets a `custom-plugin`-sourced hook so
> `unregister` does not raise (`_NON_USER_UNREGISTERABLE_SOURCES = {"builtin","native-plugin"}`).

- [ ] **Step 3: Run, see it pass.** `MAGI_CONFIG="$(mktemp -d)/config.toml" uv run pytest tests/packs/test_user_callback_pack.py -q` (wire loader `callback` branch if absent: register/replace on `registries.hooks` + handler map, `unregister` for `"-"` refs catching `KeyError`/`ValueError`).

- [ ] **Step 4: Commit.**

```bash
git add tests/packs/test_user_callback_pack.py magi_agent/packs/loader.py
git commit -m "test(packs): user callback pack can add, override, and remove callback refs"
```

### Task F.3: Live-path firing confirmation (pack callback runs through HookBus)

**Files:**
- Create: `magi_agent/packs/hook_projection.py`
- Test: `tests/packs/test_callback_pack_live_path.py`

- [ ] **Step 1: Read the HookBus consume path.** `HookBus.__init__` takes
  `hooks: tuple[RegisteredHook, ...]` (`bus.py:81`); `.run(point=…, context=…, harness_state=…)`
  fires the matching-point hooks (`bus.py:120`). `RegisteredHook(manifest, handler)` is the unit
  (`bus.py:32`). The projector turns the pack registry into that tuple.

- [ ] **Step 2: Write the failing test (drives `HookBus.run` synchronously — the handler is sync).**

```python
# tests/packs/test_callback_pack_live_path.py
from magi_agent.harness.resolved import build_default_resolved_harness_state
from magi_agent.hooks.bus import HookBus
from magi_agent.hooks.context import HookContext
from magi_agent.hooks.manifest import HookPoint
from magi_agent.packs.discovery import discover_packs, firstparty_packs_dir
from magi_agent.packs.hook_projection import project_registered_hooks
from magi_agent.packs.loader import load_packs
from magi_agent.packs.registries import PackRegistries


def test_pack_callback_fires_through_hook_bus():
    registries = PackRegistries.empty()
    load_packs(
        discover_packs(search_paths=(firstparty_packs_dir() / "callback_turn_audit",)),
        registries,
    )
    registered = project_registered_hooks(registries)
    assert any(h.manifest.name == "turn-audit" for h in registered)

    bus = HookBus(hooks=registered)
    result = bus.run(
        point=HookPoint.BEFORE_TURN_START,
        context=HookContext(),
        harness_state=build_default_resolved_harness_state(),
    )
    # The audit hook is non-blocking and returns continue; the bus must not block.
    assert result.final_action == "continue"
    assert any("turn-audit observed" == r.reason for r in result.results)
```

> `HookContext()` is the live hook input. If it requires fields, re-grep
> `grep -n "class HookContext" magi_agent/hooks/context.py` and pass the minimal required kwargs.
> A non-blocking native hook's result IS appended in synchronous `.run()` (see `bus.py:~172`
> `results.append(result)` — the async-only skip is for `execution_type in {command,http,llm}`, and
> ours is native), so the `turn-audit observed` reason is present.
> One scoping caveat: `.run()` first calls `resolve_scoped_harness_hooks(...)` and only fires hooks
> whose manifest is in the resolved scope (`grep -n "def resolve_scoped_harness_hooks" magi_agent/hooks/`).
> If the assert on `results` is empty, the hook was scoped out — pass a `harness_state` that includes
> `turn-audit` in its effective hooks (or assert on `registered`/`final_action` only) rather than
> changing the bus.

- [ ] **Step 3: Run, see it fail.** `MAGI_CONFIG="$(mktemp -d)/config.toml" uv run pytest tests/packs/test_callback_pack_live_path.py -q` → FAIL (`hook_projection` missing).

- [ ] **Step 4: Implement the projector.**

```python
# magi_agent/packs/hook_projection.py
from __future__ import annotations

from magi_agent.hooks.bus import RegisteredHook
from magi_agent.packs.registries import PackRegistries


def project_registered_hooks(registries: PackRegistries) -> tuple[RegisteredHook, ...]:
    """Turn the pack hook registry + handler map into the RegisteredHook tuple
    the HookBus consumes — the live callback seam (previously unexposed because
    HookRegistry had no discovery path into the bus)."""
    registered: list[RegisteredHook] = []
    for manifest in registries.hooks.list_all():
        handler = registries.hooks_handler(manifest.name)
        if handler is None:
            continue
        registered.append(RegisteredHook(manifest=manifest, handler=handler))
    return tuple(registered)
```

- [ ] **Step 5: Run, see it pass.** Same command → PASS. This is the keystone: a user/first-party
  callback authored as a pack now **fires live** through the same `HookBus` the ADK callback adapter
  drives.

- [ ] **Step 6: Commit.**

```bash
git add magi_agent/packs/hook_projection.py tests/packs/test_callback_pack_live_path.py
git commit -m "feat(packs): expose HookRegistry discovery so pack callbacks fire through HookBus"
```

---

## Whole-phase verification (run after all 6 groups merge)

- [ ] **Run the full phase test selection** (headless, no keys):

```bash
MAGI_CONFIG="$(mktemp -d)/config.toml" uv run pytest \
  tests/firstparty/test_tools_clock_pack.py \
  tests/firstparty/test_evidence_gitdiff_pack.py \
  tests/firstparty/test_recipe_authoring_static_pack.py \
  tests/firstparty/test_connector_local_readonly_pack.py \
  tests/firstparty/test_harness_coding_lean_pack.py \
  tests/firstparty/test_callback_turn_audit_pack.py \
  tests/packs/test_user_tool_pack.py \
  tests/packs/test_user_evidence_producer_pack.py \
  tests/packs/test_user_recipe_pack.py \
  tests/packs/test_user_connector_pack.py \
  tests/packs/test_user_harness_pack.py \
  tests/packs/test_user_callback_pack.py \
  tests/packs/test_tool_pack_live_path.py \
  tests/packs/test_evidence_producer_live_path.py \
  tests/packs/test_recipe_pack_live_path.py \
  tests/packs/test_connector_pack_live_path.py \
  tests/packs/test_harness_pack_live_path.py \
  tests/packs/test_callback_pack_live_path.py -q
```
Expected: all PASS.

- [ ] **Confirm no control-plane drift was introduced** (defensive — Groups A–F must not touch the 6
  LoopControls). If `git diff --name-only main...HEAD` shows any change under
  `adk_bridge/control_plane.py`'s LoopControl assembly, run the Phase-0 golden regression:

```bash
MAGI_CONFIG="$(mktemp -d)/config.toml" uv run pytest \
  tests/fixtures/neutral_runtime_golden/test_golden_regression.py -q
```
A diff here = a behavior change to review; regenerate via `capture --write` only if intended. (For a
correct Phase 4 there should be **no** such change, so this run is expected to be clean/PASS.)

---

## Acceptance criteria (Phase 4 done)

- [ ] Six bundled first-party packs exist under `magi_agent/firstparty/packs/` (`tools_clock`,
  `evidence_gitdiff`, `recipe_authoring_static`, `connector_local_readonly`, `harness_coding_lean`,
  `callback_turn_audit`), each with a `pack.toml` in the **same** format/loader as third-party.
- [ ] Each of the six types registers via its **typed context** (`ToolProvideContext`,
  `EvidenceProducerProvideContext`, `RecipeProvideContext`, `ConnectorProvideContext`,
  `HarnessProvideContext`, `CallbackProvideContext`) — no god-object, no first-party-only kwarg.
- [ ] For each type, a user pack in a temp dir can **add** a new ref, **override** a first-party ref,
  and **remove** (`"-"`-prefix) a ref — verified by a green test.
- [ ] For each type, the registered primitive reaches a **live** path: tool → mode-scoped offer list;
  evidence_producer → required-evidence gate; recipe → materialized reliability plan; connector →
  projected into the tool registry; harness → injected into the resolved preset state; callback →
  fires through `HookBus`.
- [ ] The previously-unexposed `HookRegistry` now has a discovery path into the live `HookBus`
  (`magi_agent/packs/hook_projection.py`).
- [ ] The whole-phase pytest selection is green, headless, **no API keys**.
- [ ] No change to the 6 control-plane LoopControls (Phase-5 scope); golden regression clean if run.

## Rollback

Every group is additive: new packs under `magi_agent/firstparty/packs/<name>/`, new tests under
`tests/firstparty/` and `tests/packs/`, and three new projector modules
(`magi_agent/packs/{connector_projection,harness_projection,hook_projection}.py`). Any loader-branch
edits in `magi_agent/packs/loader.py` are additive type-dispatch cases. Revert =
`git revert` the per-group commits or delete the named files; the kernel (Phases 1–3) and the
existing runtime are untouched. Each group is independently revertible since the six are disjoint.

## Hand-off to later phases

- **Phase 5 (control-plane migration)** reuses the exact pattern proven here (bundled first-party
  pack + typed context + add/override/remove + live confirmation) for the harder `control_plane`
  type and its 4 seams — and it DOES require the Phase-0 golden regression per migrated control.
- **Phase 6 (first-party migration + microkernel shrink + flat catalog flip)** relies on these six
  packs being loadable by `load_packs` so it can flip the live catalog from
  `CompileRecipePackCatalog.default()` to `catalog_build.build_catalog(registries)` and shrink the
  hardcodes in `tools/catalog.py`, `evidence/builtin.py`, `harness/resolved.py`, and the recipe
  `_first_party_packs()` assembly. The projectors created here
  (`connector_projection`, `harness_projection`, `hook_projection`) are the wiring Phase 6 calls on
  the live `real_runner.py` path.
- **Phase 7 (acceptance)** asserts the §1 "no privilege" properties; the six add/override/remove
  tests here are the per-type evidence those assertions aggregate.
