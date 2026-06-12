# Pack C — Full Microkernel (gates / goal-loop / scheduler / memory → policy packs)

> **For agentic workers:** REQUIRED SUB-SKILL: `superpowers:subagent-driven-development` or
> `superpowers:executing-plans`. Read `00-BLUEPRINT.md` (§1 "no privilege" spec, D1–D7,
> conventions) and `08-ROADMAP-post-p6.md` (§Pack C — the scope contract this doc implements)
> first. This pack chains AFTER Pack B; C1/C2/C3/C4 are parallelizable per subsystem once the
> shared Task C0 lands.

**Goal:** Apply the proven Phase-5 policy/mechanism split to the 4 subsystems P1–P6 deferred:
**C1 gates** (`gates/gate5b_full_toolhost.py`, 2812 lines: tool impls → `tool` packs; authz/dispatch
policy → `control_plane` packs with `phase="tool_host"`; pure dispatch envelope stays kernel),
**C2 goal_loop_control** (continue/stop policy → `loop_policy` pack; hook plumbing +
continuation-prompt re-injection mechanism stays kernel), **C3 scheduler** ("which job / when"
policy → `schedule_policy` pack; file-lock + lease + at-most-once advance mechanism stays kernel),
**C4 memory** (recall/compaction/review *strategy* → `memory_strategy` packs; stores + receipt
envelopes stay kernel). Each subsystem follows the per-subsystem method from 08-ROADMAP §Pack C:
**(1)** golden-style behavior oracle FIRST (the Phase-0 recorder/tap/scenario/capture pattern,
`tests/fixtures/neutral_runtime_golden/` is the template; each subsystem gets a parallel fixture
dir), **(2)** decompose policy→pack primitive(s) with typed contexts / mechanism→kernel, **(3)**
migrate first-party policy into bundled packs under `magi_agent/firstparty/packs/`, **(4)** gate =
subsystem oracle green + full suite.

**Architecture:** One serial shared task (C0) extends the kernel surface: 3 new `provides` types
(`loop_policy`, `schedule_policy`, `memory_strategy`) added to the D2 schema + `PrimitiveType` +
`PackRegistries` + typed provide-contexts, plus a `workspace handler` extension to the existing
`tool` type (`ToolRegistry.register(manifest, *, handler=…)` already exists in shipped code — C1
rides it via a parallel keyed registry because gate5b workspace handlers have a different
signature than `ToolHandler`). After C0, each subsystem decomposes independently: the kernel keeps
the bare mechanism and resolves the policy from the registries (dual-load: `None` → legacy
default, byte-identical), first-party policy ships as bundled packs loaded by the SAME
loader/format a `~/.magi/packs` pack uses (§1 no privilege). Every behavior change is gated by the
subsystem oracle captured BEFORE any decomposition.

**Tech stack:** Python 3.11+, `uv`, pydantic v2 (frozen, `extra="forbid"`), Google ADK, `tomllib`,
pytest. Touch points: `magi_agent/packs/{manifest,context,registries}.py`,
`magi_agent/gates/gate5b_full_toolhost.py`, `magi_agent/harness/goal_loop_control.py`,
`magi_agent/harness/scheduler_executor.py` (+ `scheduler_job_execution.py`),
`magi_agent/harness/{memory_recall,memory_compaction,memory_review}.py`,
`magi_agent/firstparty/packs/`, `tests/fixtures/{gate5b,goal_loop,scheduler,memory}_golden/`.

---

## Ground truth (measured on branch `feat/neutral-runtime`, HEAD 75a45520 — re-verify at point of use)

The shipped tree has moved ~60 commits past the roadmap's assumptions. Facts this plan is built
on (each task's Step 1 re-greps before editing):

- **gate5b is 2812 lines** (not "~89KB monolith of unknown shape"). Its structure is already
  half-decomposed: `Gate5BFullToolHost.dispatch()` (`:988`) is a pure envelope (counter
  preflight/dup/budget → allowlist → memory-mode enforce → permission preflight → `_handle` →
  output filter → diagnostics → receipts → public events → error taxonomy), and `_handle()`
  (`:1152`) is an 11-branch if-chain over `_GATE5B_LEGACY_FULL_TOOLHOST_TOOL_NAMES`
  (`Clock, Calculation, FileRead, Glob, Grep, FileWrite, FileEdit, PatchApply, Bash` `:276`) plus
  `TestRun`/`GitDiff`, falling through to `_dispatch_registry_tool()` (`:1422`) which ALREADY
  routes non-legacy names through the neutral `ToolRegistry`/`ToolDispatcher`. The decomposition
  is therefore a MOVE of the 11 legacy branches + 2 policy enforcers, not a rewrite.
- **`ToolRegistry.register/replace` already accept `handler=`** (`magi_agent/tools/registry.py:46`)
  and `ToolDispatcher` runs registered handlers — but `ToolHandler = Callable[[ToolArguments,
  ToolContext], ToolResult | Awaitable[ToolResult]]` (`tools/base.py:12`) is a DIFFERENT signature than a gate5b workspace
  handler needs (raw output object + host services). C1 keeps the two kinds distinct (a parallel
  `workspace_tool_handlers` keyed registry) instead of overloading `ToolRegistry.handler`.
- **Two control_plane impl conventions coexist in shipped code:** `build_control_plane_from_packs`
  (`packs/registries.py:324`) treats `control_plane` impls as PROVIDERS (called with
  `ControlPlaneProvideContext`), while `ContextDispatcher` (`packs/context.py:548`) treats registry
  entries as ctx-callables (`entry.impl(ctx)` with `BeforeToolCtx`/`AfterToolCtx`). C1's dispatch
  policies use the **ContextDispatcher convention** with manifest `phase = "tool_host"` +
  `gatePosition = "before"`; `build_control_plane_from_packs` must learn to SKIP
  `phase == "tool_host"` entries (Task C1.6 Step 6).
- **goal_loop policy is already a pure function over injected seams:**
  `decide_loop_continuation(LoopControlInput) -> LoopControlResult`
  (`harness/goal_loop_control.py:451`) — store/judge/spend-probe/evidence-gate are all
  Protocol-injected. The ONLY hardcode is `build_after_turn_goal_loop_hook` (`:653`) calling
  `decide_loop_continuation` by name. The "typed context" for C2 is `LoopControlInput` itself.
- **scheduler policy hardcode is exactly 1 call site:** `_tick_inside_lock` (`scheduler_executor.py
  :494`) calls `job.compute_next_run(now=now)` (cron grammar) with the `_ONCE_EXHAUSTED_NEXT_RUN`
  sentinel fallback; due-selection is the `ScheduledJobSource.due_jobs` contract. Lock, lease,
  at-most-once `record_advance`-before-receipt ordering, receipts = mechanism.
- **memory harnesses already take policy objects as parameters** — the hardcodes are the DEFAULT
  strategies: `_compaction_denial_reasons` (`memory_compaction.py:428`, called at `compact()`),
  the projection/namespace policies callers construct inline (`memory_recall.py:81` passes them
  through), and `should_run_review` (`memory_review.py:165`, the N-turn trigger). C4 registers
  these three as named strategies; stores/write-host/receipts stay kernel.
- **`PerInvocationState`, `ControlPlaneContext`, `KeyedRefRegistry`, `load_into_registries`,
  `project_into_registries` all exist and are live** (Phase 2/3/5/6 landed). New provide types
  follow the exact `harness` pattern: Literal entry + `PrimitiveType` member + `KeyedRefRegistry`
  slot + frozen provide-context dataclass + a `project_into_registries` branch.
- **Removal/forbid is NOT a `"-"`-prefix entry** — it is `config.toml [packs] disable = [...]`
  upstream (`resolve_enabled_packs`), per the `project_into_registries` docstring. §1 tests use
  that mechanism.
- **The Phase-0 golden oracle** (`tests/fixtures/neutral_runtime_golden/`) is the template:
  `recorder.py` (content-addressed events, `_digest` = sha256[:16] of sorted JSON), `tap.py`
  (pure-observe wrappers), `scenarios.py` (drivers copying trigger setups from existing tests),
  `capture.py` (`--write`-guarded regen, `SCENARIOS` dict + `golden_path` + `render`),
  `test_golden_regression.py` (unified-diff fail + non-trivial expectations). Each Ci oracle
  clones this 5-file shape into its own fixture dir with its own `GOLDEN_DIR`.

**Where shipped code differs from 08-ROADMAP's sketch:** the roadmap's table says goal-loop keeps
a "`run_async` re-entry mechanism" in kernel — in the shipped tree no production driver calls the
next turn yet (module docstring `:75-78`: decisions are emitted through `decision_sink`; the
driver re-injects). So C2's kernel mechanism = the after-turn hook + sink plumbing, and the policy
seam is the decision function. The roadmap also said "new provides sub-type **or** control_plane"
for loop policy — this plan picks a first-class `loop_policy` type because the decision signature
(`LoopControlInput -> LoopControlResult`) is not a LoopControl hook shape.

---

## Conventions recap (do not skip — identical to Phase 5)

- **TDD, bite-sized:** failing test → run (FAIL) → minimal impl → run (PASS) → commit. One logical
  change per commit. Conventional commits (`feat(packs): …`, `refactor(gates): …`,
  `test(goal-loop): …`).
- **No API keys, isolated config:** prefix EVERY pytest run with
  `MAGI_CONFIG="$(mktemp -d)/config.toml"`. No test in this pack needs a provider key.
- **Re-grep first:** every modify-task's Step 1 locates the current code; `:NNN` refs are
  HEAD-75a45520 snapshots and may drift.
- **Oracle-before-touch:** each subsystem's C*.0 oracle task MUST be committed (goldens captured on
  the pristine pre-decomposition tree) before any other task in that subsystem starts.
- **Phase-0 golden gate:** any task that touches `magi_agent/packs/context.py`,
  `magi_agent/packs/registries.py`, or the control-plane assembly
  (`build_control_plane_from_packs`) runs
  `tests/fixtures/neutral_runtime_golden/test_golden_regression.py` in its verify step. A diff =
  unintended behavior change → revert; never `capture --write` inside Pack C (Pack C is
  behavior-preserving everywhere).
- **Dual-load reversibility:** every kernel seam takes `policy=None` → legacy default
  (byte-identical). The flip to pack-loaded defaults happens in each subsystem's LAST task only.
- **Pydantic:** frozen, `extra="forbid"`, `populate_by_name=True`, camelCase aliases.

## Dispatch shape (drive with `/workflows`)

| Stage | Parallel? | Units |
|---|---|---|
| C0 shared surface | serial (1 agent) | manifest types + registries + provide contexts + tool-handler extension |
| C1.0 / C2.0 / C3.0 / C4.0 oracles | **4-way parallel** after C0 | one oracle fixture dir each, captured pre-decomposition |
| C1.2–C1.7 | serial within C1 (C1.3/C1.4/C1.5 tool-handler tasks parallelizable after C1.2) | gates decomposition |
| C2.1–C2.2 / C3.1–C3.2 / C4.1–C4.2 | **3-way parallel**, serial within each | per-subsystem decompose + bundled pack |
| C5 acceptance | serial, last | §1 assertions across all 4 + full suite |

Barrier after every task = that subsystem's oracle + the listed legacy suites.

---

## Task C0 (SERIAL, shared barrier): 3 new provides types + workspace-handler seam

**Files:**
- Modify: `magi_agent/packs/manifest.py`
- Modify: `magi_agent/packs/context.py`
- Modify: `magi_agent/packs/registries.py`
- Test: `tests/packs/test_pack_c_provides_types.py` (new)
- Test fixture impls: `tests/packs/pack_c_fixture_impls.py` (new)

This is the only task C1–C4 all depend on. It widens the schema + registries; NO subsystem is
migrated here.

- [ ] **Step 1: Re-grep the current schema + registry shape.**

```bash
cd /Users/kevin/Desktop/claude_code/magi-agent/.worktrees/neutral-runtime
grep -n "ProvidesType = Literal\|_SPEC_TYPES\|_ORDERED_TYPES" magi_agent/packs/manifest.py
grep -n "class PrimitiveType\|CONNECTOR = " magi_agent/packs/context.py
grep -n "class PackRegistries\|self.harnesses\|def _provide_harness\|elif ptype == \"harness\"\|elif ptype == \"callback\"" magi_agent/packs/registries.py
grep -n "class ToolProvideContext" -A4 magi_agent/packs/context.py
```

Expected (shipped): `ProvidesType` is the 8-entry Literal; `PrimitiveType` ends at
`CONNECTOR = "connector"`; `PackRegistries.__init__` builds `tools/hooks` + 4 `KeyedRefRegistry`
slots; `ToolProvideContext` is a frozen dataclass with exactly one field
`register: Callable[[Any], None]`; `project_into_registries` has per-type `elif` branches ending
with `callback`.

- [ ] **Step 2: Write the failing test.**

```python
# tests/packs/test_pack_c_provides_types.py
from __future__ import annotations

from pathlib import Path

from magi_agent.packs.manifest import PackManifest


def _manifest(ptype: str, ref: str, impl: str) -> PackManifest:
    return PackManifest.model_validate(
        {
            "packId": "test.pack-c",
            "displayName": "pack c fixture",
            "provides": [{"type": ptype, "ref": ref, "impl": impl}],
        }
    )


def test_manifest_accepts_the_three_pack_c_types():
    for ptype, ref in (
        ("loop_policy", "loop_policy:fake@1"),
        ("schedule_policy", "schedule_policy:fake@1"),
        ("memory_strategy", "memory_strategy:fake@1"),
    ):
        m = _manifest(ptype, ref, "tests.packs.pack_c_fixture_impls:provide_loop_policy")
        assert m.provides[0].type == ptype


def test_primitive_type_enum_has_pack_c_members():
    from magi_agent.packs.context import PrimitiveType

    assert PrimitiveType.LOOP_POLICY.value == "loop_policy"
    assert PrimitiveType.SCHEDULE_POLICY.value == "schedule_policy"
    assert PrimitiveType.MEMORY_STRATEGY.value == "memory_strategy"


def test_load_into_registries_projects_pack_c_types_and_workspace_handler(tmp_path: Path):
    """End-to-end through the REAL discover -> resolve -> load -> project pipeline
    (same path a ~/.magi/packs user pack takes — §1 no privilege)."""
    from magi_agent.packs.registries import load_into_registries

    pack_dir = tmp_path / "pack-c-fixture"
    pack_dir.mkdir()
    (pack_dir / "pack.toml").write_text(
        'packId = "test.pack-c-fixture"\n'
        'displayName = "pack c fixture"\n'
        "\n"
        "[[provides]]\n"
        'type = "loop_policy"\n'
        'ref = "loop_policy:fake@1"\n'
        'impl = "tests.packs.pack_c_fixture_impls:provide_loop_policy"\n'
        "\n"
        "[[provides]]\n"
        'type = "schedule_policy"\n'
        'ref = "schedule_policy:fake@1"\n'
        'impl = "tests.packs.pack_c_fixture_impls:provide_schedule_policy"\n'
        "\n"
        "[[provides]]\n"
        'type = "memory_strategy"\n'
        'ref = "memory_strategy:fake@1"\n'
        'impl = "tests.packs.pack_c_fixture_impls:provide_memory_strategy"\n'
        "\n"
        "[[provides]]\n"
        'type = "tool"\n'
        'ref = "workspace:FakeTool@1"\n'
        'impl = "tests.packs.pack_c_fixture_impls:provide_workspace_handler"\n'
    )
    registries, report = load_into_registries([tmp_path])
    assert "loop_policy:fake@1" in report.registered
    assert registries.loop_policies.resolve("loop_policy:fake@1") is not None
    assert registries.schedule_policies.resolve("schedule_policy:fake@1") is not None
    assert registries.memory_strategies.resolve("memory_strategy:fake@1") is not None
    # The tool provider registered a WORKSPACE handler (keyed by tool name).
    handler = registries.workspace_tool_handlers.resolve("FakeTool")
    assert callable(handler)
    assert handler({"x": 1}, None) == {"echo": {"x": 1}}
```

```python
# tests/packs/pack_c_fixture_impls.py
"""Importable provider impls for the C0 end-to-end loader test.

These are deliberately external-shaped: each receives ONLY its typed
provide-context — identical capability to a ~/.magi/packs pack (§1).
"""
from __future__ import annotations

from typing import Any


def provide_loop_policy(context: Any) -> None:
    context.register("loop_policy:fake@1", lambda loop_input: loop_input)


def provide_schedule_policy(context: Any) -> None:
    context.register("schedule_policy:fake@1", object())


def provide_memory_strategy(context: Any) -> None:
    context.register("memory_strategy:fake@1", object())


def provide_workspace_handler(context: Any) -> None:
    if context.register_workspace_handler is not None:
        context.register_workspace_handler(
            "FakeTool", lambda args, view: {"echo": dict(args)}
        )
```

- [ ] **Step 3: Run, see it FAIL.**

```bash
MAGI_CONFIG="$(mktemp -d)/config.toml" uv run pytest tests/packs/test_pack_c_provides_types.py -q
```

Expected: FAIL — pydantic `ValidationError` (`loop_policy` not in the `ProvidesType` Literal),
then `AttributeError: LOOP_POLICY` once the manifest is fixed.

- [ ] **Step 4: Implement the schema + registry additions.**

In `magi_agent/packs/manifest.py`, extend the Literal (the shipped 8 entries are quoted in Step 1;
keep their order, append the new three):

```python
ProvidesType = Literal[
    "tool",
    "callback",
    "validator",
    "harness",
    "control_plane",
    "evidence_producer",
    "recipe",
    "connector",
    # Pack C policy types (decomposed-subsystem policies; same loader, no privilege)
    "loop_policy",
    "schedule_policy",
    "memory_strategy",
]
```

(No change to `_SPEC_TYPES` / `_ORDERED_TYPES` / `_GATE_POSITION_TYPES` — the new types are
code-impl, unordered, no gate position.)

In `magi_agent/packs/context.py`, extend `PrimitiveType` (after `CONNECTOR = "connector"`):

```python
    LOOP_POLICY = "loop_policy"
    SCHEDULE_POLICY = "schedule_policy"
    MEMORY_STRATEGY = "memory_strategy"
```

Replace the shipped `ToolProvideContext` (quoted below as currently shipped) with the
handler-extended version, and add the three new provide contexts next to
`HarnessProvideContext`:

```python
# shipped today:
# @dataclass(frozen=True)
# class ToolProvideContext:
#     register: Callable[[Any], None]

@dataclass(frozen=True)
class ToolProvideContext:
    """D5 typed context a ``tool`` impl receives.

    ``register`` accepts a ``ToolManifest`` (unchanged from Phase 4).
    ``register_workspace_handler`` (Pack C1) additionally lets a tool pack bind a
    WORKSPACE handler ``(args, WorkspaceHostView) -> output`` keyed by tool name —
    the gate5b toolhost executes it inside its unchanged dispatch envelope.
    ``None`` when the projector predates C1 (backward compatible)."""

    register: Callable[[Any], None]
    register_workspace_handler: Callable[[str, Any], None] | None = None


@dataclass(frozen=True)
class LoopPolicyProvideContext:
    """D5 typed context a ``loop_policy`` impl receives: ``register(ref, policy)``
    where ``policy`` is ``Callable[[LoopControlInput], LoopControlResult]``."""

    register: Callable[[str, Any], None]


@dataclass(frozen=True)
class SchedulePolicyProvideContext:
    """D5 typed context a ``schedule_policy`` impl receives: ``register(ref, policy)``
    where ``policy`` satisfies ``harness.scheduler_executor.SchedulePolicy``."""

    register: Callable[[str, Any], None]


@dataclass(frozen=True)
class MemoryStrategyProvideContext:
    """D5 typed context a ``memory_strategy`` impl receives: ``register(ref, strategy)``."""

    register: Callable[[str, Any], None]
```

Add the four names to `__all__` in `context.py`
(`"LoopPolicyProvideContext", "SchedulePolicyProvideContext", "MemoryStrategyProvideContext"` —
`ToolProvideContext` is already exported).

In `magi_agent/packs/registries.py`:

1. `PackRegistries.__init__` — after the shipped `self.harnesses = KeyedRefRegistry()` line add:

```python
        self.loop_policies = KeyedRefRegistry()
        self.schedule_policies = KeyedRefRegistry()
        self.memory_strategies = KeyedRefRegistry()
        # C1: gate5b workspace tool handlers, keyed by TOOL NAME (not provides ref).
        self.workspace_tool_handlers = KeyedRefRegistry()
```

2. Add one shared keyed-provider helper next to `_provide_harness`:

```python
def _provide_keyed(registry: KeyedRefRegistry) -> Callable[..., None]:
    def register(ref: str, value: Any) -> None:
        registry.replace(ref, value)
    return register


def _provide_workspace_handler(registries: PackRegistries) -> Callable[[str, Any], None]:
    def register(tool_name: str, handler: Any) -> None:
        registries.workspace_tool_handlers.replace(tool_name, handler)
    return register
```

3. In `project_into_registries`, change the shipped `tool` branch

```python
        if ptype == "tool":
            impl(_ctx.ToolProvideContext(register=_provide_tool(registries, ref)))
            registered.append(ref)
```

to

```python
        if ptype == "tool":
            impl(_ctx.ToolProvideContext(
                register=_provide_tool(registries, ref),
                register_workspace_handler=_provide_workspace_handler(registries),
            ))
            registered.append(ref)
```

and append three branches after the shipped `callback` branch:

```python
        elif ptype == "loop_policy":
            impl(_ctx.LoopPolicyProvideContext(
                register=_provide_keyed(registries.loop_policies)))
            registered.append(ref)
        elif ptype == "schedule_policy":
            impl(_ctx.SchedulePolicyProvideContext(
                register=_provide_keyed(registries.schedule_policies)))
            registered.append(ref)
        elif ptype == "memory_strategy":
            impl(_ctx.MemoryStrategyProvideContext(
                register=_provide_keyed(registries.memory_strategies)))
            registered.append(ref)
```

- [ ] **Step 5: Run, see it PASS.**

```bash
MAGI_CONFIG="$(mktemp -d)/config.toml" uv run pytest tests/packs/test_pack_c_provides_types.py -q
```

Expected: PASS (3 tests).

- [ ] **Step 6: Regression — packs suite + Phase-0 golden (context/registries are shared files).**

```bash
MAGI_CONFIG="$(mktemp -d)/config.toml" uv run pytest tests/packs/ \
  tests/fixtures/neutral_runtime_golden/test_golden_regression.py -q
```

Expected: ALL PASS, no golden diff (additive schema only).

- [ ] **Step 7: Commit.**

```bash
git add magi_agent/packs/manifest.py magi_agent/packs/context.py magi_agent/packs/registries.py \
        tests/packs/test_pack_c_provides_types.py tests/packs/pack_c_fixture_impls.py
git commit -m "feat(packs): add loop_policy/schedule_policy/memory_strategy types + workspace-handler seam

Pack C shared surface: 3 new provides types (manifest Literal + PrimitiveType +
KeyedRefRegistry slots + typed provide contexts + projection branches) and the
ToolProvideContext.register_workspace_handler extension C1 binds gate5b tool
impls through. No subsystem migrated; golden unchanged."
```

**Barrier:** C1.0/C2.0/C3.0/C4.0 may now run in parallel.

---

# C1 — gates (`gate5b_full_toolhost.py`): tool packs + dispatch-policy packs

## C1.1 The decomposition MAP (the contract every C1 task implements)

Read order for the implementing agent: `Gate5BFullToolHost.__init__` (`:827`), `dispatch`
(`:988`), `_handle` (`:1152`), `_dispatch_registry_tool` (`:1422`), `_preflight_legacy_tool`
(`:1464`), `_enforce_read_before_mutation` (`:1554`), `build_gate5b_full_toolhost_bundle`
(`:1953`). Every line of the file falls into exactly one row:

| gate5b element (HEAD-75a45520) | Destination | Why |
|---|---|---|
| `dispatch()` envelope: `Gate5BFullToolCounter` preflight (`duplicate_tool_call`/`tool_call_digest_conflict`/`max_tool_calls_exhausted`), allowlist check (`tool_not_allowlisted`), exception→outcome taxonomy (`Gate5BFullToolRegistryBlocked`/`Gate5BFullToolReadLedgerError`/`Gate5BFullToolPathPolicyError`/`TimeoutExpired`/`OSError`), receipts (`receipt_boundary`, `edit_match_receipt_boundary`, `diagnostics_boundary`), `_bounded_output`, public tool events | **kernel** (unchanged) | pure dispatch wiring per 08-ROADMAP table |
| 11 `_handle` branches: `Clock, Calculation, FileRead, Glob, Grep, FileWrite, FileEdit, PatchApply, Bash, TestRun, GitDiff` | **`tool` packs** → workspace handlers `(args, WorkspaceHostView) -> output` in `firstparty/packs/workspace_tools_default/` | tool impls → tool packs |
| `_dispatch_registry_tool` fallback (registry tools via `ToolDispatcher`) | **kernel** (unchanged) | already the neutral path |
| `_enforce_memory_mode` (`:895`) + `_filter_memory_mode_output` (`:936`) | **`control_plane` pack** `gates_policy_default`, entry `gate5b:memory-mode@1`, `phase="tool_host"`, `gatePosition="before"` (ctx-callable: denies via `BeforeToolCtx.decide`, filters via `AfterToolCtx.override`) | authz policy → control_plane pack |
| `_preflight_legacy_tool` (`ToolPermissionPolicy().decide` over `_legacy_tool_manifest`) | **`control_plane` pack** entry `gate5b:permission-preflight@1` (same pack, template repeat) | D6: even the permission gate is a removable pack |
| `_enforce_read_before_mutation` + `_record_full_read` + `ReadLedger` store | **kernel store + `WorkspaceHostView` capability** (`view.enforce_read_before_mutation` / `view.record_full_read`); handlers CALL it | mirrors the S-A evidence split: store=kernel, policy consumes a view |
| `_safe_child_path`, `_is_gate5b_workspace_escape`, `_is_sensitive_workspace_path`, `_redact`, `_BoundedPipeCapture`, `_run_shell_command` process mechanics, `_format_after_write`, `_content_digest`, ripgrep helpers, `_evaluate_expression` | **kernel** (exposed to handlers ONLY through `WorkspaceHostView`) | path-safety/bounding/redaction mechanism |
| `_selected_scope_error` + `Gate5BFullToolHostConfig` validation + bundle assembly | **kernel** (unchanged) | environment/scope gating of the WHOLE host, not per-call policy |

Per-tool extraction is a repeating template (Task C1.5); the doc fully works `Clock` (C1.3,
trivial) and `FileEdit` (C1.4, the hardest: read-ledger + fuzzy cascade + format-on-write +
edit-match receipt). Do NOT copy this table blindly — Step 1 of every task re-greps.

## Task C1.0 (oracle FIRST — commit before any C1 decomposition): gate5b golden oracle

**Files (new, mirroring `tests/fixtures/neutral_runtime_golden/`):**
- `tests/fixtures/gate5b_golden/__init__.py` (empty)
- `tests/fixtures/gate5b_golden/recorder.py`
- `tests/fixtures/gate5b_golden/scenarios.py`
- `tests/fixtures/gate5b_golden/capture.py`
- `tests/fixtures/gate5b_golden/test_golden_regression.py`
- `tests/fixtures/gate5b_golden/golden/` (captured JSON, committed)

- [ ] **Step 1: Re-read the template + the trigger sources.**

```bash
ls tests/fixtures/neutral_runtime_golden/
sed -n '1,60p' tests/fixtures/neutral_runtime_golden/capture.py
grep -n "def dispatch\|read_ledger_enabled\|memory_mode" magi_agent/gates/gate5b_full_toolhost.py | head
ls tests/gates/
```

Trigger setups are copied from the existing suites (the proven Phase-0 convention; there is NO
`tests/gates/test_gate5b_full_toolhost.py` — the gates suite is per-feature):
`tests/gates/test_file_tool_path_alias.py` (host construction + ok FileWrite/FileRead dispatches),
`tests/gates/test_gate5b_full_toolhost_memory_mode.py` (protected-memory block args),
`tests/gates/test_gate5b_read_ledger.py` (edit-without-read block).

- [ ] **Step 2: Write the recorder.**

```python
# tests/fixtures/gate5b_golden/recorder.py
"""Gate5B dispatch-trace recorder — content-addressed, order-preserving.

One event per ``Gate5BFullToolHost.dispatch`` call: tool, args digest, outcome
status+reason, and (for tools whose output is deterministic across runs) the
receipt's bounded-output digest. Mirrors neutral_runtime_golden/recorder.py.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any


def _digest(obj: Any) -> str:
    blob = json.dumps(obj, sort_keys=True, default=str).encode()
    return "sha256:" + hashlib.sha256(blob).hexdigest()[:16]


#: Tools whose dispatch output is byte-stable across machines/runs (no clock
#: drift beyond the injected now_ms, no env-dependent notes). Bash/TestRun are
#: excluded: ``_deadline_note_safe()`` may inject an env-dependent note.
STABLE_OUTPUT_TOOLS = frozenset(
    {"Clock", "Calculation", "FileRead", "FileWrite", "FileEdit", "Glob", "Grep", "GitDiff"}
)


@dataclass
class Gate5BDispatchRecorder:
    events: list[dict[str, Any]] = field(default_factory=list)

    def record_dispatch(self, *, tool_name: str, args: dict[str, Any], outcome: Any) -> None:
        receipt = outcome.receipt
        self.events.append(
            {
                "kind": "dispatch",
                "tool": tool_name,
                "args_digest": _digest(args),
                "status": outcome.status,
                "reason": outcome.reason,
                "output_digest": (
                    receipt.bounded_output_digest
                    if tool_name in STABLE_OUTPUT_TOOLS and outcome.status == "ok"
                    else None
                ),
            }
        )


def normalize_trace(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [dict(e) for e in events]
```

- [ ] **Step 3: Write the scenario drivers.**

```python
# tests/fixtures/gate5b_golden/scenarios.py
"""Two scenario drivers over the REAL Gate5BFullToolHost.

``dispatch_ok``      — the 8 deterministic legacy tools succeed end-to-end.
``dispatch_blocked`` — one block per policy family: allowlist, path policy,
memory-mode (incognito), read-ledger (edit without fresh read), call budget.
NOTE the budget family needs one interleaved ok call: ``Gate5BFullToolCounter``
increments ``_tool_calls`` only in ``finish_call`` (blocked outcomes are NEVER
budget-counted), so ``max_tool_calls_exhausted`` only fires after a completion.

Construction copied from tests/gates/test_file_tool_path_alias.py; memory-mode
args copied from tests/gates/test_gate5b_full_toolhost_memory_mode.py;
read-ledger trigger copied from tests/gates/test_gate5b_read_ledger.py.
Workspace = a fresh tmp dir per run; every recorded field is content-addressed
relative paths/outputs, so the trace is machine-independent.
"""
from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path
from typing import Any

from tests.fixtures.gate5b_golden.recorder import Gate5BDispatchRecorder, normalize_trace

_FIXED_NOW_MS = 1_700_000_000_000


def _pin_env() -> dict[str, str | None]:
    """Pin call-time env flags the handlers read so the trace is stable.
    Returns the previous values for restoration."""
    pinned = {"MAGI_EDIT_FUZZY_MATCH_ENABLED": "0"}
    previous: dict[str, str | None] = {}
    for key, value in pinned.items():
        previous[key] = os.environ.get(key)
        os.environ[key] = value
    return previous


def _restore_env(previous: dict[str, str | None]) -> None:
    for key, value in previous.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


def _host(workspace: Path, **overrides: Any):
    from magi_agent.gates.gate5b_full_toolhost import (
        Gate5BFullToolHost,
        Gate5BFullToolHostConfig,
    )

    kwargs: dict[str, Any] = dict(
        config=Gate5BFullToolHostConfig.model_validate(
            {
                "enabled": True,
                "killSwitchEnabled": False,
                "routeAttachmentEnabled": True,
                "environment": "local",
                "environmentAllowlist": ["local"],
                "maxToolCallsPerTurn": overrides.pop("max_calls", 32),
                "maxPerToolOutputBytes": 8192,
                "commandTimeoutMs": 5000,
            }
        ),
        workspace_root=workspace,
        exposed_tool_names=overrides.pop(
            "exposed",
            ("Clock", "Calculation", "FileRead", "FileWrite", "FileEdit",
             "Glob", "Grep", "GitDiff"),
        ),
        now_ms=lambda: _FIXED_NOW_MS,
    )
    kwargs.update(overrides)
    return Gate5BFullToolHost(**kwargs)


async def _drive(host: Any, rec: Gate5BDispatchRecorder,
                 calls: list[tuple[str, dict[str, Any]]]) -> None:
    for index, (tool, args) in enumerate(calls):
        outcome = await host.dispatch(
            tool,
            args,
            request_digest=f"req-{index}",
            tool_call_id=f"call-{index}",
        )
        rec.record_dispatch(tool_name=tool, args=dict(args), outcome=outcome)


def run_dispatch_ok_scenario() -> list[dict[str, Any]]:
    previous = _pin_env()
    try:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            host = _host(workspace)
            rec = Gate5BDispatchRecorder()
            asyncio.run(
                _drive(
                    host,
                    rec,
                    [
                        ("Clock", {}),
                        ("Calculation", {"expression": "6*7"}),
                        ("FileWrite", {"path": "notes/a.txt", "content": "hello golden\n"}),
                        ("FileRead", {"path": "notes/a.txt"}),
                        ("FileEdit", {"path": "notes/a.txt",
                                      "oldText": "hello", "newText": "hi"}),
                        ("Glob", {"pattern": "**/*.txt"}),
                        ("Grep", {"pattern": "hi", "glob": "**/*.txt"}),
                        ("GitDiff", {}),
                    ],
                )
            )
            return normalize_trace(rec.events)
    finally:
        _restore_env(previous)


def run_dispatch_blocked_scenario() -> list[dict[str, Any]]:
    previous = _pin_env()
    try:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            # Pre-existing file so the read-ledger edit check has a target.
            (workspace / "existing.txt").write_text("already here\n", encoding="utf-8")
            # Protected-memory file (path shape copied from
            # tests/gates/test_gate5b_full_toolhost_memory_mode.py — re-check there
            # if is_protected_memory_path's patterns moved).
            (workspace / "memory").mkdir()
            (workspace / "memory" / "MEMORY.md").write_text("secret\n", encoding="utf-8")
            host = _host(
                workspace,
                exposed=("Clock", "FileRead", "FileEdit"),
                read_ledger_enabled=True,
                memory_mode="incognito",
                max_calls=1,
            )
            rec = Gate5BDispatchRecorder()
            asyncio.run(
                _drive(
                    host,
                    rec,
                    [
                        # 1. not allowlisted (blocked outcomes never consume budget)
                        ("Bash", {"command": "printf hi"}),
                        # 2. workspace escape -> path_policy_denied
                        ("FileRead", {"path": "../escape.txt"}),
                        # 3. incognito read of protected memory -> memory_mode_blocked
                        ("FileRead", {"path": "memory/MEMORY.md"}),
                        # 4. edit of existing file without a fresh full read
                        #    -> read_ledger_* block (record=False, not budget-counted)
                        ("FileEdit", {"path": "existing.txt",
                                      "oldText": "already", "newText": "still"}),
                        # 5. the ONLY ok completion -> finish_call consumes the
                        #    whole maxToolCallsPerTurn=1 budget
                        ("Clock", {}),
                        # 6. budget: second completion attempt (fresh call id)
                        #    -> max_tool_calls_exhausted at before_call
                        ("Clock", {}),
                    ],
                )
            )
            return normalize_trace(rec.events)
    finally:
        _restore_env(previous)
```

- [ ] **Step 4: Write capture + regression (clone the template, swapped names).**

```python
# tests/fixtures/gate5b_golden/capture.py
"""Golden capture/regen for the gate5b dispatch oracle. --write guarded.

Usage:
    python -m tests.fixtures.gate5b_golden.capture --write
    python -m tests.fixtures.gate5b_golden.capture            # dry-run/diff
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Callable

from tests.fixtures.gate5b_golden import scenarios

GOLDEN_DIR = Path(__file__).parent / "golden"

SCENARIOS: dict[str, Callable[[], list[dict[str, Any]]]] = {
    "dispatch_ok": scenarios.run_dispatch_ok_scenario,
    "dispatch_blocked": scenarios.run_dispatch_blocked_scenario,
}


def golden_path(name: str) -> Path:
    return GOLDEN_DIR / f"{name}.json"


def render(trace: list[dict[str, Any]]) -> str:
    return json.dumps(trace, indent=2, sort_keys=True) + "\n"


def capture_all(*, write: bool) -> int:
    GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
    changed = 0
    for name, driver in SCENARIOS.items():
        rendered = render(driver())
        path = golden_path(name)
        existing = path.read_text() if path.exists() else None
        if existing == rendered:
            print(f"  unchanged  {name}")
            continue
        changed += 1
        if write:
            path.write_text(rendered)
            print(f"  WROTE      {name}")
        else:
            print(f"  {'NEW' if existing is None else 'DRIFT':<10} {name}")
    return changed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    changed = capture_all(write=args.write)
    if not args.write and changed:
        print(f"\n{changed} golden(s) would change. If intended, re-run with --write.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

```python
# tests/fixtures/gate5b_golden/test_golden_regression.py
"""Gate5B dispatch golden regression. A diff = a gate5b behavior change to review.

Before/after ANY C1 edit, run this; never `capture --write` inside Pack C
(C1 is behavior-preserving)."""
from __future__ import annotations

import difflib
import json

import pytest

from tests.fixtures.gate5b_golden.capture import SCENARIOS, golden_path, render


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_gate5b_golden_trace_unchanged(name: str) -> None:
    path = golden_path(name)
    assert path.exists(), (
        f"missing golden '{name}'. Capture on the pristine pre-C1 base:\n"
        f"  python -m tests.fixtures.gate5b_golden.capture --write"
    )
    live = render(SCENARIOS[name]())
    golden = path.read_text()
    if live != golden:
        diff = "".join(
            difflib.unified_diff(
                golden.splitlines(keepends=True), live.splitlines(keepends=True),
                fromfile=f"golden/{name}.json", tofile=f"live/{name}",
            )
        )
        pytest.fail(f"gate5b dispatch behavior changed for '{name}':\n{diff}")


def test_gate5b_goldens_are_non_trivial() -> None:
    ok = json.loads(golden_path("dispatch_ok").read_text())
    blocked = json.loads(golden_path("dispatch_blocked").read_text())
    assert len(ok) == 8 and all(e["status"] == "ok" for e in ok)
    assert all(e["output_digest"] for e in ok), "ok trace must carry output digests"
    blocked_events = [e for e in blocked if e["status"] == "blocked"]
    # 5 distinct policy families must each have produced a block with its own reason
    # (event 5 is the single ok completion that arms the budget family).
    assert [e["status"] for e in blocked] == ["blocked"] * 4 + ["ok", "blocked"]
    assert len(blocked_events) == 5
    assert len({e["reason"] for e in blocked_events}) == 5
```

- [ ] **Step 5: Capture on the PRISTINE tree, then verify the regression passes twice (stability).**

```bash
MAGI_CONFIG="$(mktemp -d)/config.toml" uv run python -m tests.fixtures.gate5b_golden.capture --write
MAGI_CONFIG="$(mktemp -d)/config.toml" uv run pytest tests/fixtures/gate5b_golden/ -q
MAGI_CONFIG="$(mktemp -d)/config.toml" uv run pytest tests/fixtures/gate5b_golden/ -q
```

Expected: 2 goldens written; regression PASS twice (re-run proves determinism). If
`dispatch_blocked` event 3 or 4 records `status="ok"` (protected path / ledger trigger drifted),
fix the scenario args from the named test files BEFORE committing — the non-trivial expectations
test enforces 5 distinct block reasons.

- [ ] **Step 6: Commit.**

```bash
git add tests/fixtures/gate5b_golden/
git commit -m "test(gates): gate5b dispatch golden oracle (2 scenarios, 14 events)

Pack C1 Phase-0-style oracle captured on the pristine pre-decomposition tree:
8-tool ok trace with content-addressed outputs + 5-family blocked trace
(allowlist, path policy, memory mode, read ledger, call budget)."
```

## Task C1.2: `WorkspaceHostView` typed context + host handler/policy seams (kernel side)

**Files:**
- Modify: `magi_agent/packs/context.py` (add `WorkspaceHostView`)
- Modify: `magi_agent/gates/gate5b_full_toolhost.py` (host params + `_handle` head + policy fan-out)
- Test: `tests/gates/test_gate5b_workspace_handler_seam.py` (new)

- [ ] **Step 1: Re-grep the host internals the view will wrap.**

```bash
grep -n "def _safe_child_path\|def _enforce_read_before_mutation\|def _record_full_read\|def _format_after_write\|def _content_digest\|def _run_shell_command\|def _ripgrep_active\|_last_edit_match_result" \
  magi_agent/gates/gate5b_full_toolhost.py | head -12
grep -n "async def _handle\|self._enforce_memory_mode(tool_name, args)\|self._preflight_legacy_tool\|def _filter_memory_mode_output" \
  magi_agent/gates/gate5b_full_toolhost.py
```

Confirm: `dispatch` calls (in order, inside its try block) `self._enforce_memory_mode(tool_name,
args)` → `self._preflight_legacy_tool(...)` → `await self._handle(...)` →
`self._filter_memory_mode_output(tool_name, output)`.

- [ ] **Step 2: Write the failing test.**

```python
# tests/gates/test_gate5b_workspace_handler_seam.py
from __future__ import annotations

import asyncio
from pathlib import Path

from magi_agent.gates.gate5b_full_toolhost import (
    Gate5BFullToolHost,
    Gate5BFullToolHostConfig,
)
from magi_agent.packs.context import WorkspaceHostView


def _host(tmp_path: Path, **kw):
    return Gate5BFullToolHost(
        config=Gate5BFullToolHostConfig.model_validate(
            {"enabled": True, "killSwitchEnabled": False, "routeAttachmentEnabled": True,
             "environment": "local", "environmentAllowlist": ["local"],
             "maxToolCallsPerTurn": 8}
        ),
        workspace_root=tmp_path,
        exposed_tool_names=("Clock", "FileRead"),
        now_ms=lambda: 1_700_000_000_000,
        **kw,
    )


def test_injected_workspace_handler_takes_precedence(tmp_path: Path):
    def fake_clock(args, view):
        assert isinstance(view, WorkspaceHostView)
        return {"nowMs": view.now_ms(), "viaPack": True}

    host = _host(tmp_path, workspace_handlers={"Clock": fake_clock})
    outcome = asyncio.run(
        host.dispatch("Clock", {}, request_digest="r", tool_call_id="c")
    )
    assert outcome.status == "ok"
    # The envelope (counter/receipts) is unchanged; the handler produced output.
    assert outcome.receipt.tool_name == "Clock"


def test_view_resolve_path_enforces_workspace_confinement(tmp_path: Path):
    host = _host(tmp_path)
    view = WorkspaceHostView(host=host)
    import pytest
    from magi_agent.gates.gate5b_full_toolhost import Gate5BFullToolPathPolicyError

    with pytest.raises(Gate5BFullToolPathPolicyError):
        view.resolve_path("../outside.txt")


def test_dispatch_policy_deny_maps_to_blocked(tmp_path: Path):
    def deny_clock(ctx):
        # ContextDispatcher convention: duck-typed on the hook ctx.
        if hasattr(ctx, "decide") and ctx.tool_name == "Clock":
            ctx.decide("deny", reason="test_policy_block")

    host = _host(tmp_path, dispatch_policies=(deny_clock,))
    outcome = asyncio.run(
        host.dispatch("Clock", {}, request_digest="r", tool_call_id="c")
    )
    assert outcome.status == "blocked"
    assert outcome.reason == "test_policy_block"
```

- [ ] **Step 3: Run, see it FAIL.**

```bash
MAGI_CONFIG="$(mktemp -d)/config.toml" uv run pytest tests/gates/test_gate5b_workspace_handler_seam.py -q
```

Expected: FAIL (`ImportError: WorkspaceHostView`; `Gate5BFullToolHost.__init__` has no
`workspace_handlers`/`dispatch_policies` kwargs).

- [ ] **Step 4: Implement.**

(a) `magi_agent/packs/context.py` — add after `MemoryStrategyProvideContext`:

```python
class WorkspaceHostView:
    """C1 typed context a gate5b workspace tool handler receives.

    The ONLY handle a tool impl gets (first-party and user packs receive the
    identical object — §1). Wraps the kernel mechanisms (path safety, read
    ledger, formatter, bounded shell) without exposing the host. All gate5b
    imports are lazy so packs.context keeps a gates-free import graph.
    """

    def __init__(self, *, host: Any) -> None:
        self._host = host

    # -- read-only host facts -------------------------------------------------
    @property
    def workspace_root(self) -> Any:  # pathlib.Path
        return self._host.workspace_root

    @property
    def config(self) -> Any:  # frozen Gate5BFullToolHostConfig
        return self._host.config

    def now_ms(self) -> int:
        return int(self._host.now_ms())

    def ripgrep_active(self) -> bool:
        return bool(self._host._ripgrep_active())

    # -- kernel path safety ----------------------------------------------------
    def resolve_path(self, path_text: str, *, allow_missing: bool = False) -> Any:
        from magi_agent.gates.gate5b_full_toolhost import _safe_child_path

        return _safe_child_path(
            self._host.workspace_root, path_text, allow_missing=allow_missing
        )

    def path_digest(self, target: Any) -> str:
        from magi_agent.gates.gate5b_full_toolhost import _digest

        return _digest(target.relative_to(self._host.workspace_root).as_posix())

    # -- kernel read-ledger store (policy stays kernel; handlers consume) ------
    def enforce_read_before_mutation(self, target: Any) -> None:
        self._host._enforce_read_before_mutation(target)

    def record_full_read(self, target: Any, content: str) -> None:
        self._host._record_full_read(target, content)

    # -- kernel write-side services ---------------------------------------------
    def format_after_write(self, target: Any) -> None:
        self._host._format_after_write(target)

    def content_digest(self, target: Any) -> str | None:
        return self._host._content_digest(target)

    def store_edit_match_result(self, match: Any) -> None:
        """Hand the EditMatchResult back so dispatch() builds the EditMatch
        evidence receipt exactly as the legacy branch did."""
        self._host._last_edit_match_result = match

    # -- kernel bounded/redacted shell ------------------------------------------
    def run_command(self, command: str, *, timeout_s: float) -> dict[str, Any]:
        return self._host._run_shell_command(command, timeout_s=timeout_s)
```

Add `"WorkspaceHostView"` to `__all__`.

(b) `magi_agent/gates/gate5b_full_toolhost.py` — extend `__init__` (quote of the shipped tail of
the signature: `..., memory_mode: "MemoryMode | str" = "normal", public_event_sink: ... = None`)
with two trailing kwargs and their state:

```python
        # C1 seams (dual-load: empty/None preserves legacy behavior exactly).
        workspace_handlers: Mapping[str, object] | None = None,
        dispatch_policies: Sequence[object] | None = None,
    ) -> None:
        ...existing body unchanged, then at the end of __init__ add:
        self._workspace_handlers: dict[str, object] = dict(workspace_handlers or {})
        self._dispatch_policies: tuple[object, ...] = tuple(dispatch_policies or ())
```

At the TOP of `_handle` (before the `if tool_name == "Clock":` line) add the handler-first lookup:

```python
        handler = self._workspace_handlers.get(tool_name)
        if handler is not None:
            from magi_agent.packs.context import WorkspaceHostView

            output = handler(args, WorkspaceHostView(host=self))
            if inspect.isawaitable(output):
                output = await output
            return output
```

(`import inspect` at module top if absent — grep first.)

Replace the dispatch-time policy call site. Shipped code inside `dispatch`:

```python
                self._enforce_memory_mode(tool_name, args)
                self._preflight_legacy_tool(tool_name, args, tool_call_id=tool_call_id)
```

becomes

```python
                self._run_dispatch_policies(tool_name, args, tool_call_id=tool_call_id)
```

and the shipped output filter call `output = self._filter_memory_mode_output(tool_name, output)`
becomes `output = self._apply_after_dispatch_policies(tool_name, args, output)`. Add the two
methods (dual-load: empty policies → legacy enforcement, byte-identical):

```python
    def _run_dispatch_policies(
        self,
        tool_name: str,
        args: Mapping[str, object],
        *,
        tool_call_id: str,
    ) -> None:
        if not self._dispatch_policies:
            # Legacy path until C1.6 migrates these into the gates_policy_default pack.
            self._enforce_memory_mode(tool_name, args)
            self._preflight_legacy_tool(tool_name, args, tool_call_id=tool_call_id)
            return
        from magi_agent.packs.context import (
            BeforeToolCtx,
            EvidenceReadView,
            SessionReadView,
        )

        session = SessionReadView(
            invocation_id=tool_call_id,
            agent_name="gate5b_full_toolhost",
            turn_index=0,
            state={
                "memoryMode": str(self.memory_mode),
                "workspaceRoot": str(self.workspace_root),
            },
        )
        for impl in self._dispatch_policies:
            ctx = BeforeToolCtx(
                tool_name=tool_name,
                tool_args=dict(args),
                session=session,
                evidence=EvidenceReadView(),
            )
            impl(ctx)
            decision = ctx.decision()
            if decision.action == "deny":
                reason = str(
                    (decision.deny_result or {}).get("error", "dispatch_policy_denied")
                )
                raise Gate5BFullToolRegistryBlocked(reason)

    def _apply_after_dispatch_policies(
        self,
        tool_name: str,
        args: Mapping[str, object],
        output: object,
    ) -> object:
        if not self._dispatch_policies:
            return self._filter_memory_mode_output(tool_name, output)
        from magi_agent.packs.context import AfterToolCtx, SessionReadView

        session = SessionReadView(
            invocation_id="gate5b", agent_name="gate5b_full_toolhost", turn_index=0,
            state={"memoryMode": str(self.memory_mode)},
        )
        current = output
        for impl in self._dispatch_policies:
            ctx = AfterToolCtx(
                tool_name=tool_name, tool_args=dict(args),
                result=current, session=session,
            )
            impl(ctx)
            override = ctx.override_result()
            if override is not None:
                current = override
        return current
```

Thread the same two kwargs through `build_gate5b_full_toolhost_bundle` (its shipped signature is
quoted at C1.1; add `workspace_handlers=None, dispatch_policies=None` params and pass them to the
`Gate5BFullToolHost(...)` construction — defaults `None` keep every existing call site, including
`magi_agent/transport/health.py` and `magi_agent/transport/chat_routes.py`, byte-identical).

- [ ] **Step 5: Run, see it PASS — new seam test + the C1.0 oracle + the gates suite + the
Phase-0 golden gate (this task edits `magi_agent/packs/context.py`, a shared kernel file).**

```bash
MAGI_CONFIG="$(mktemp -d)/config.toml" uv run pytest \
  tests/gates/test_gate5b_workspace_handler_seam.py \
  tests/fixtures/gate5b_golden/ tests/gates/ tests/packs/ \
  tests/fixtures/neutral_runtime_golden/test_golden_regression.py -q
```

Expected: ALL PASS, both oracles no-diff (no handlers/policies are injected on any legacy path
yet; the context additions are purely additive).

- [ ] **Step 6: Commit.**

```bash
git add magi_agent/packs/context.py magi_agent/gates/gate5b_full_toolhost.py \
        tests/gates/test_gate5b_workspace_handler_seam.py
git commit -m "feat(gates): WorkspaceHostView + workspace-handler/dispatch-policy seams on gate5b

C1 kernel side: _handle consults pack-registered workspace handlers first
(typed WorkspaceHostView, capability parity), and dispatch routes the
memory-mode/permission enforcement through injectable ctx-callable policies
(dual-load: empty -> legacy enforcement byte-identical). Oracle unchanged."
```

## Task C1.3 (worked example A — the template): `Clock` handler → bundled tool pack

**Files:**
- New: `magi_agent/firstparty/packs/workspace_tools_default/__init__.py` (empty)
- New: `magi_agent/firstparty/packs/workspace_tools_default/pack.toml`
- New: `magi_agent/firstparty/packs/workspace_tools_default/impl.py`
- Test: `tests/firstparty/test_workspace_tools_default_pack.py` (new)

- [ ] **Step 1: Re-read the legacy branch being moved.** Shipped `_handle` head:

```python
        if tool_name == "Clock":
            return {"nowMs": self.now_ms()}
        if tool_name == "Calculation":
            return {"value": _evaluate_expression(str(args.get("expression", "0")))}
```

- [ ] **Step 2: Write the failing test.**

```python
# tests/firstparty/test_workspace_tools_default_pack.py
from __future__ import annotations

import asyncio
from pathlib import Path

from magi_agent.packs.discovery import default_search_bases
from magi_agent.packs.registries import load_into_registries


def _bundled_handlers():
    registries, _ = load_into_registries(list(default_search_bases()))
    return registries.workspace_tool_handlers


def test_bundled_pack_registers_clock_workspace_handler():
    handlers = _bundled_handlers()
    assert callable(handlers.resolve("Clock"))


def test_pack_clock_handler_is_byte_identical_to_legacy(tmp_path: Path):
    from magi_agent.gates.gate5b_full_toolhost import (
        Gate5BFullToolHost,
        Gate5BFullToolHostConfig,
    )

    config = Gate5BFullToolHostConfig.model_validate(
        {"enabled": True, "killSwitchEnabled": False, "routeAttachmentEnabled": True,
         "environment": "local", "environmentAllowlist": ["local"],
         "maxToolCallsPerTurn": 8}
    )

    def _outcome(host):
        return asyncio.run(
            host.dispatch("Clock", {}, request_digest="r", tool_call_id="c")
        )

    legacy = Gate5BFullToolHost(
        config=config, workspace_root=tmp_path, exposed_tool_names=("Clock",),
        now_ms=lambda: 1_700_000_000_000,
    )
    handlers = _bundled_handlers()
    packed = Gate5BFullToolHost(
        config=config, workspace_root=tmp_path, exposed_tool_names=("Clock",),
        now_ms=lambda: 1_700_000_000_000,
        workspace_handlers={"Clock": handlers.resolve("Clock")},
    )
    a, b = _outcome(legacy), _outcome(packed)
    assert a.status == b.status == "ok"
    assert a.receipt.bounded_output_digest == b.receipt.bounded_output_digest
```

- [ ] **Step 3: Run, see it FAIL.**

```bash
MAGI_CONFIG="$(mktemp -d)/config.toml" uv run pytest tests/firstparty/test_workspace_tools_default_pack.py -q
```

Expected: FAIL (`resolve("Clock")` returns `None` — no bundled pack registers a workspace handler).

- [ ] **Step 4: Implement the bundled pack.**

```toml
# magi_agent/firstparty/packs/workspace_tools_default/pack.toml
# Bundled first-party gate5b workspace tool handlers. Loaded by the SAME
# loader/format a user pack uses (D1/D3): each handler is a `tool` provides
# entry whose impl binds a (args, WorkspaceHostView) -> output callable keyed by
# tool name. The gate5b dispatch ENVELOPE (counter/receipts/path-safety/
# memory-mode policies) stays kernel — these entries carry ONLY the tool logic
# moved verbatim out of Gate5BFullToolHost._handle. A user pack registering the
# same tool name later in pack order overrides each handler individually (§1).

packId = "openmagi.workspace-tools-default"
displayName = "Workspace tool handlers (gate5b)"
version = "1.0.0"
description = "First-party gate5b legacy tool implementations as removable workspace handlers."

[[provides]]
type = "tool"
ref = "workspace:Clock@1"
impl = "magi_agent.firstparty.packs.workspace_tools_default.impl:provide_clock"

[[provides]]
type = "tool"
ref = "workspace:Calculation@1"
impl = "magi_agent.firstparty.packs.workspace_tools_default.impl:provide_calculation"
```

```python
# magi_agent/firstparty/packs/workspace_tools_default/impl.py
"""First-party gate5b workspace tool handlers (no privilege, typed-view only).

Each provider receives ONLY the ToolProvideContext and binds a handler
``(args, WorkspaceHostView) -> output``. Bodies are MOVED verbatim from
``Gate5BFullToolHost._handle`` branches — behavior byte-identical (the C1.0
oracle proves it). A handler raising ValueError/OSError flows through the
unchanged dispatch error taxonomy.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from magi_agent.packs.context import ToolProvideContext, WorkspaceHostView


def _clock(args: Mapping[str, object], view: WorkspaceHostView) -> dict[str, object]:
    return {"nowMs": view.now_ms()}


def provide_clock(context: ToolProvideContext) -> None:
    if context.register_workspace_handler is not None:
        context.register_workspace_handler("Clock", _clock)


def _calculation(args: Mapping[str, object], view: WorkspaceHostView) -> dict[str, object]:
    from magi_agent.gates.gate5b_full_toolhost import _evaluate_expression

    return {"value": _evaluate_expression(str(args.get("expression", "0")))}


def provide_calculation(context: ToolProvideContext) -> None:
    if context.register_workspace_handler is not None:
        context.register_workspace_handler("Calculation", _calculation)
```

> NOTE `_evaluate_expression` is a pure module-level helper (`gate5b_full_toolhost.py:2619`,
> stdlib-AST arithmetic) — importing it from the pack is library reuse, not privileged host
> access. If C1.7's cleanup later moves such helpers to a neutral module, only this import line
> changes.

- [ ] **Step 5: Run, see it PASS (+ oracle still green — legacy hosts pass no handlers).**

```bash
MAGI_CONFIG="$(mktemp -d)/config.toml" uv run pytest \
  tests/firstparty/test_workspace_tools_default_pack.py tests/fixtures/gate5b_golden/ -q
```

- [ ] **Step 6: Commit.**

```bash
git add magi_agent/firstparty/packs/workspace_tools_default/ \
        tests/firstparty/test_workspace_tools_default_pack.py
git commit -m "feat(gates): bundle Clock+Calculation as workspace-handler tool pack

C1 worked example A: first two _handle branches moved verbatim into
openmagi.workspace-tools-default, registered through the same loader a user
pack uses. Byte-identical outcome digest proven against the legacy branch."
```

## Task C1.4 (worked example B — the hard one): `FileEdit` handler

**Files:**
- Modify: `magi_agent/firstparty/packs/workspace_tools_default/pack.toml` (+1 entry)
- Modify: `magi_agent/firstparty/packs/workspace_tools_default/impl.py`
- Test: extend `tests/firstparty/test_workspace_tools_default_pack.py`

- [ ] **Step 1: Re-read the shipped FileEdit branch** (`_handle`, snapshot `:1230-1280`): resolve
path → `_enforce_read_before_mutation` → oldText/newText (both alias spellings) →
`empty_old_text` guard → call-time `_edit_fuzzy_match_enabled()` → fuzzy cascade
(`NoMatchError`→`old_text_not_found`, `MultipleMatchesError`→`old_text_not_unique`, store
`_last_edit_match_result`) ELSE substring replace → `_format_after_write` → result dict with
`pathDigest`/`replacements` (+`matchTier`/`matchConfidence` when fuzzy, +`contentDigest` when
`format_on_write_enabled`).

- [ ] **Step 2: Failing test (append).**

```python
# tests/firstparty/test_workspace_tools_default_pack.py  (append)
def test_pack_file_edit_handler_matches_legacy_including_read_ledger(tmp_path: Path, monkeypatch):
    import asyncio

    from magi_agent.gates.gate5b_full_toolhost import (
        Gate5BFullToolHost,
        Gate5BFullToolHostConfig,
    )

    monkeypatch.setenv("MAGI_EDIT_FUZZY_MATCH_ENABLED", "0")
    config = Gate5BFullToolHostConfig.model_validate(
        {"enabled": True, "killSwitchEnabled": False, "routeAttachmentEnabled": True,
         "environment": "local", "environmentAllowlist": ["local"],
         "maxToolCallsPerTurn": 8}
    )
    handlers = _bundled_handlers()
    assert callable(handlers.resolve("FileEdit"))

    async def run(host, workspace: Path):
        (workspace / "f.txt").write_text("alpha beta\n", encoding="utf-8")
        # Fresh full read first so the read ledger allows the edit.
        await host.dispatch("FileRead", {"path": "f.txt"},
                            request_digest="r0", tool_call_id="c0")
        return await host.dispatch(
            "FileEdit", {"path": "f.txt", "oldText": "alpha", "newText": "gamma"},
            request_digest="r1", tool_call_id="c1",
        )

    ws_a, ws_b = tmp_path / "a", tmp_path / "b"
    ws_a.mkdir(); ws_b.mkdir()
    legacy = Gate5BFullToolHost(
        config=config, workspace_root=ws_a,
        exposed_tool_names=("FileRead", "FileEdit"),
        now_ms=lambda: 1_700_000_000_000, read_ledger_enabled=True,
    )
    packed = Gate5BFullToolHost(
        config=config, workspace_root=ws_b,
        exposed_tool_names=("FileRead", "FileEdit"),
        now_ms=lambda: 1_700_000_000_000, read_ledger_enabled=True,
        workspace_handlers={"FileEdit": handlers.resolve("FileEdit")},
    )
    a = asyncio.run(run(legacy, ws_a))
    b = asyncio.run(run(packed, ws_b))
    assert a.status == b.status == "ok"
    assert a.receipt.bounded_output_digest == b.receipt.bounded_output_digest
    assert (ws_a / "f.txt").read_text() == (ws_b / "f.txt").read_text() == "gamma beta\n"
```

- [ ] **Step 3: Run, see it FAIL** (`resolve("FileEdit")` is `None`).

```bash
MAGI_CONFIG="$(mktemp -d)/config.toml" uv run pytest \
  tests/firstparty/test_workspace_tools_default_pack.py::test_pack_file_edit_handler_matches_legacy_including_read_ledger -q
```

- [ ] **Step 4: Implement — MOVE the branch body onto the view.**

pack.toml (append):

```toml
[[provides]]
type = "tool"
ref = "workspace:FileEdit@1"
impl = "magi_agent.firstparty.packs.workspace_tools_default.impl:provide_file_edit"
```

impl.py (append; this is the shipped branch body re-expressed over `WorkspaceHostView` — compare
side-by-side with the Step-1 quote when reviewing):

```python
def _file_edit(args: Mapping[str, object], view: WorkspaceHostView) -> dict[str, object]:
    from magi_agent.config.env import edit_fuzzy_match_enabled

    target = view.resolve_path(str(args.get("path") or args.get("filePath") or ""))
    view.enforce_read_before_mutation(target)
    old_text = str(args.get("oldText", args.get("old_text", "")))
    new_text = str(args.get("newText", args.get("new_text", "")))
    if not old_text:
        raise ValueError("empty_old_text")
    current = target.read_text(encoding="utf-8", errors="replace")
    # Call-time read (NOT import-time): the import-time constant froze before
    # profile env defaults were applied — same bug class the shipped branch fixed.
    fuzzy_enabled = edit_fuzzy_match_enabled()
    match_result: Any = None
    if fuzzy_enabled:
        from magi_agent.coding.edit_matching import (
            MultipleMatchesError,
            NoMatchError,
            replace as fuzzy_replace,
        )

        try:
            match_result = fuzzy_replace(current, old_text, new_text)
        except NoMatchError:
            raise ValueError("old_text_not_found")
        except MultipleMatchesError:
            raise ValueError("old_text_not_unique")
        # Hand the structured match back so dispatch() builds the EditMatch
        # evidence receipt after the handler returns (kernel mechanism).
        view.store_edit_match_result(match_result)
        target.write_text(match_result.result, encoding="utf-8")
    else:
        if old_text not in current:
            raise ValueError("old_text_not_found")
        target.write_text(current.replace(old_text, new_text, 1), encoding="utf-8")
    view.format_after_write(target)
    edit_result: dict[str, object] = {
        "pathDigest": view.path_digest(target),
        "replacements": 1,
    }
    if fuzzy_enabled and match_result is not None:
        from magi_agent.coding.edit_matching import EditMatchResult

        if isinstance(match_result, EditMatchResult):
            edit_result["matchTier"] = match_result.tier
            edit_result["matchConfidence"] = match_result.confidence
    if view.config.format_on_write_enabled:
        content_digest = view.content_digest(target)
        if content_digest is not None:
            edit_result["contentDigest"] = content_digest
    return edit_result


def provide_file_edit(context: ToolProvideContext) -> None:
    if context.register_workspace_handler is not None:
        context.register_workspace_handler("FileEdit", _file_edit)
```

- [ ] **Step 5: Run, see it PASS — pack test, fuzzy-edit legacy suite, oracle.**

```bash
MAGI_CONFIG="$(mktemp -d)/config.toml" uv run pytest \
  tests/firstparty/test_workspace_tools_default_pack.py \
  tests/gates/test_gate5b_fuzzy_edit.py tests/gates/test_gate5b_read_ledger.py \
  tests/fixtures/gate5b_golden/ -q
```

- [ ] **Step 6: Commit.**

```bash
git add magi_agent/firstparty/packs/workspace_tools_default/ \
        tests/firstparty/test_workspace_tools_default_pack.py
git commit -m "feat(gates): FileEdit workspace handler (read-ledger + fuzzy cascade via view)

C1 worked example B: the hardest _handle branch moved onto WorkspaceHostView
(enforce_read_before_mutation, store_edit_match_result, format_after_write,
path_digest). Byte-identical receipt digest vs legacy with ledger ON."
```

## Task C1.5 (REPEATING TEMPLATE): the remaining 9 workspace handlers

Apply the C1.3/C1.4 pattern to each of: `FileRead`, `Glob`, `Grep`, `FileWrite`, `PatchApply`,
`Bash`, `TestRun`, `GitDiff` (and nothing else — `Clock`/`Calculation`/`FileEdit` are done; all
other names already flow through `_dispatch_registry_tool`). One commit per tool. For EACH tool
`<T>`:

- [ ] 1. **Re-read the shipped `_handle` branch for `<T>`** (`grep -n 'if tool_name == "<T>"' -A40
  magi_agent/gates/gate5b_full_toolhost.py`). Identify every host attribute it touches and map
  each to a `WorkspaceHostView` member (extend the view ONLY if a kernel mechanism is missing —
  e.g. `Bash`/`TestRun` need only `view.run_command(command, timeout_s=...)` with
  `view.config.command_timeout_ms / 1000` resp. the module's `_TEST_RUN_TIMEOUT_S = 300.0`;
  `FileRead` needs `view.record_full_read` + the `read_quality` library imports from
  `magi_agent.coding.read_format`, and ports `_handle_file_read` (`:1784`) including the
  did-you-mean/missing-file shape — move `_did_you_mean_candidates` logic with it; `GitDiff`
  ports `_handle_git_diff` (`:1398`) — promote the module-level `_is_git_repository`,
  `_git_status_porcelain`, `_git_diff_numstat` imports the same lazy way C1.3 imported
  `_evaluate_expression`).
- [ ] 2. **Failing test** (append to `tests/firstparty/test_workspace_tools_default_pack.py`):
  same-input dual-host comparison asserting equal `status` + `receipt.bounded_output_digest`
  (for `Bash`/`TestRun` assert equal `exitCode`/`stdout` fields instead of the digest — the
  deadline note is env-dependent), exactly like C1.4 Step 2.
- [ ] 3. Run → FAIL (`resolve("<T>") is None`).
- [ ] 4. **Move the branch body** into `_<t>(args, view)` + `provide_<t>` in `impl.py`; add the
  `[[provides]]` entry `ref = "workspace:<T>@1"`. The body is a MOVE: keep alias handling
  (`path`/`filePath`), error strings (`empty_command`, `unsupported_patch_shape`, …), and result
  keys byte-identical.
- [ ] 5. Run → PASS: the new test + the tool's legacy suite
  (`test_gate5b_ripgrep.py` for Glob/Grep; `test_gate5b_format_on_write.py` +
  `test_gate5b_read_ledger.py` for PatchApply — there is NO dedicated apply_patch suite, its
  gates coverage lives in those two; `test_gate5b_shell_env_hygiene.py` for Bash,
  `test_gate5b_test_run.py` for TestRun,
  `test_gate5b_git_diff.py` for GitDiff, `test_gate5b_read_quality.py` +
  `test_file_tool_path_alias.py` for FileRead/FileWrite) + `tests/fixtures/gate5b_golden/`.
- [ ] 6. Commit: `feat(gates): <T> workspace handler moved to workspace-tools-default pack`.

**Exit check for C1.5 (run once after the last tool):**

```bash
MAGI_CONFIG="$(mktemp -d)/config.toml" uv run pytest tests/gates/ tests/firstparty/ \
  tests/fixtures/gate5b_golden/ -q
python - <<'EOF'
from magi_agent.packs.discovery import default_search_bases
from magi_agent.packs.registries import load_into_registries
regs, _ = load_into_registries(list(default_search_bases()))
names = set(regs.workspace_tool_handlers.list_refs())
expected = {"Clock", "Calculation", "FileRead", "Glob", "Grep", "FileWrite",
            "FileEdit", "PatchApply", "Bash", "TestRun", "GitDiff"}
missing = expected - names
assert not missing, f"missing workspace handlers: {missing}"
print("all 11 workspace handlers pack-loaded")
EOF
```

## Task C1.6: dispatch-policy pack (`memory-mode` worked; `permission-preflight` template)

**Files:**
- New: `magi_agent/firstparty/packs/gates_policy_default/__init__.py` (empty)
- New: `magi_agent/firstparty/packs/gates_policy_default/pack.toml`
- New: `magi_agent/firstparty/packs/gates_policy_default/impl.py`
- Modify: `magi_agent/packs/registries.py` (`build_control_plane_from_packs` skips
  `phase="tool_host"`; add `build_tool_host_runtime_from_packs`)
- Test: `tests/firstparty/test_gates_policy_default_pack.py` (new)

- [ ] **Step 1: Re-grep both shipped policy bodies + the loop-plane assembly filter.**

```bash
grep -n "def _enforce_memory_mode" -A45 magi_agent/gates/gate5b_full_toolhost.py | head -55
grep -n "def _preflight_legacy_tool" -A35 magi_agent/gates/gate5b_full_toolhost.py | head -40
grep -n "if primitive.type == \"control_plane\"" magi_agent/packs/registries.py
grep -n "_MEMORY_WRITE_TOOL_NAMES\|_MEMORY_READ_TOOL_NAMES\|_memory_read_target_paths\|_grep_glob_may_include_protected_memory\|_filter_protected_memory_matches" \
  magi_agent/gates/gate5b_full_toolhost.py | head
```

The memory-mode body delegates to library predicates imported at gate5b top
(`normalize_memory_mode`, `is_long_term_memory_write_disabled`, `is_incognito_memory_mode`,
`is_long_term_memory_read_disabled`, `is_protected_memory_path`, `memory_write_target_paths`,
`command_mentions_protected_memory`, `command_may_write_protected_memory`) — grep their import
line to get the canonical module, and import the SAME names in the pack impl (library reuse).

- [ ] **Step 2: Failing test.**

```python
# tests/firstparty/test_gates_policy_default_pack.py
from __future__ import annotations

import asyncio
from pathlib import Path


def _policies():
    from magi_agent.packs.registries import build_tool_host_runtime_from_packs

    _handlers, policies = build_tool_host_runtime_from_packs()
    return policies


def test_bundled_tool_host_policies_load():
    assert len(_policies()) >= 1


def test_memory_mode_policy_blocks_protected_write_via_policy_path(tmp_path: Path):
    from magi_agent.gates.gate5b_full_toolhost import (
        Gate5BFullToolHost,
        Gate5BFullToolHostConfig,
    )

    host = Gate5BFullToolHost(
        config=Gate5BFullToolHostConfig.model_validate(
            {"enabled": True, "killSwitchEnabled": False, "routeAttachmentEnabled": True,
             "environment": "local", "environmentAllowlist": ["local"],
             "maxToolCallsPerTurn": 8}
        ),
        workspace_root=tmp_path,
        exposed_tool_names=("FileWrite",),
        now_ms=lambda: 1_700_000_000_000,
        memory_mode="read_only",
        dispatch_policies=_policies(),
    )
    # Target path copied from tests/gates/test_gate5b_full_toolhost_memory_mode.py.
    outcome = asyncio.run(
        host.dispatch(
            "FileWrite",
            {"path": "memory/MEMORY.md", "content": "x"},
            request_digest="r", tool_call_id="c",
        )
    )
    assert outcome.status == "blocked"
    assert outcome.reason == "memory_mode_blocked"


def test_loop_plane_assembly_skips_tool_host_entries():
    """build_control_plane_from_packs must NOT register phase='tool_host' impls
    as LoopControls (they are ctx-callables, not providers)."""
    from magi_agent.packs.registries import build_control_plane_from_packs

    plane = build_control_plane_from_packs()
    for control in plane._controls:
        assert "gate5b" not in type(control).__name__.lower()
```

- [ ] **Step 3: Run, see it FAIL** (`ImportError: build_tool_host_runtime_from_packs`).

```bash
MAGI_CONFIG="$(mktemp -d)/config.toml" uv run pytest tests/firstparty/test_gates_policy_default_pack.py -q
```

- [ ] **Step 4: Implement.**

(a) pack.toml:

```toml
# magi_agent/firstparty/packs/gates_policy_default/pack.toml
# Gate5b dispatch policies as control_plane entries with phase="tool_host".
# These are ctx-callables in the ContextDispatcher convention (impl(ctx)), NOT
# ControlPlaneProvideContext providers — build_control_plane_from_packs skips
# phase="tool_host"; the gate5b host loads them via
# build_tool_host_runtime_from_packs. gatePosition="before" is the explicit
# opt-in required for a deciding before_tool impl (the shipped
# GatePositionViolation guard semantics).

packId = "openmagi.gates-policy-default"
displayName = "Gate5b dispatch policies"
version = "1.0.0"
description = "First-party gate5b authz policies (memory mode, permission preflight) as removable packs."

[[provides]]
type = "control_plane"
ref = "gate5b:memory-mode@1"
impl = "magi_agent.firstparty.packs.gates_policy_default.impl:memory_mode_policy"
priority = 0
phase = "tool_host"
gatePosition = "before"

[[provides]]
type = "control_plane"
ref = "gate5b:permission-preflight@1"
impl = "magi_agent.firstparty.packs.gates_policy_default.impl:permission_preflight_policy"
priority = 10
phase = "tool_host"
gatePosition = "before"
```

(b) impl.py — the memory-mode body MOVED from `_enforce_memory_mode` /
`_filter_memory_mode_output`, duck-typed on the hook ctx (the ContextDispatcher contract: every
impl is called at every hook and must no-op on contexts it does not handle):

```python
# magi_agent/firstparty/packs/gates_policy_default/impl.py
"""Gate5b dispatch policies (no privilege; BeforeToolCtx/AfterToolCtx only).

``memory_mode_policy`` is the MOVED body of Gate5BFullToolHost._enforce_memory_mode
(deny path, hasattr(ctx, "decide")) + _filter_memory_mode_output (filter path,
hasattr(ctx, "override")). It reads the channel memory mode off the typed
session view — never the host. ``permission_preflight_policy`` is the moved
_preflight_legacy_tool. Library predicates are imported from the same module
gate5b imports them from (grep the gate5b import block for the canonical path
before editing — Step 1)."""
from __future__ import annotations

from typing import Any

# Same import source as gate5b_full_toolhost.py's top-of-file block (:59-68,
# verified): magi_agent.tools.memory_mode_guard. (NOT memory.write_boundary —
# that module holds the compaction-side authority flags, not these predicates.)
from magi_agent.tools.memory_mode_guard import (
    command_may_write_protected_memory,
    command_mentions_protected_memory,
    is_incognito_memory_mode,
    is_long_term_memory_read_disabled,
    is_long_term_memory_write_disabled,
    is_protected_memory_path,
    memory_write_target_paths,
    normalize_memory_mode,
)

_MEMORY_WRITE_TOOL_NAMES = frozenset({"FileWrite", "FileEdit", "PatchApply"})
_MEMORY_READ_TOOL_NAMES = frozenset({"FileRead", "Glob", "Grep"})


def _deny(ctx: Any) -> None:
    ctx.decide("deny", reason="memory_mode_blocked")


def memory_mode_policy(ctx: Any) -> None:
    from magi_agent.gates.gate5b_full_toolhost import (
        _filter_protected_memory_matches,
        _grep_glob_may_include_protected_memory,
        _memory_read_target_paths,
    )

    mode = normalize_memory_mode(str(ctx.session.get_state("memoryMode", "normal")))
    if hasattr(ctx, "decide"):  # before_tool: block path (moved _enforce_memory_mode)
        tool_name = ctx.tool_name
        args = dict(ctx.tool_args)
        if tool_name in _MEMORY_WRITE_TOOL_NAMES:
            if not is_long_term_memory_write_disabled(mode):
                return
            for path in memory_write_target_paths(tool_name, args):
                if is_protected_memory_path(path):
                    _deny(ctx)
                    return
            return
        if tool_name == "Bash":
            command = args.get("command")
            command_text = command if isinstance(command, str) else ""
            if (
                is_incognito_memory_mode(mode)
                and command_mentions_protected_memory(command_text)
            ) or (
                is_long_term_memory_write_disabled(mode)
                and command_may_write_protected_memory(command_text)
            ):
                _deny(ctx)
            return
        if tool_name in _MEMORY_READ_TOOL_NAMES:
            if not is_long_term_memory_read_disabled(mode):
                return
            for path in _memory_read_target_paths(tool_name, args):
                if is_protected_memory_path(path):
                    _deny(ctx)
                    return
            if tool_name == "Grep" and _grep_glob_may_include_protected_memory(args):
                _deny(ctx)
        return
    if hasattr(ctx, "override"):  # after_tool: filter path (moved _filter_memory_mode_output)
        if ctx.tool_name not in {"Glob", "Grep"}:
            return
        if not is_long_term_memory_read_disabled(mode):
            return
        filtered = _filter_protected_memory_matches(ctx.result)
        if filtered is not ctx.result:
            ctx.override(filtered)


def permission_preflight_policy(ctx: Any) -> None:
    """Moved _preflight_legacy_tool: ToolPermissionPolicy over the legacy
    manifest (D6 — the permission gate itself is a removable pack)."""
    if not hasattr(ctx, "decide"):
        return
    from magi_agent.gates.gate5b_full_toolhost import (
        _GATE5B_LEGACY_FULL_TOOLHOST_TOOL_NAMES,
        _legacy_tool_manifest,
        _permission_reason_code,
    )
    from magi_agent.tools.context import ToolContext
    from magi_agent.tools.permission import ToolPermissionPolicy

    tool_name = ctx.tool_name
    if tool_name not in _GATE5B_LEGACY_FULL_TOOLHOST_TOOL_NAMES:
        return
    args = dict(ctx.tool_args)
    preflight_tool_name = tool_name
    if (
        tool_name == "PatchApply"
        and "content" in args and "patch" not in args and "diff" not in args
    ):
        preflight_tool_name = "FileWrite"
    manifest = _legacy_tool_manifest(preflight_tool_name)
    mode = "act" if "act" in manifest.available_in_modes else "plan"
    decision = ToolPermissionPolicy().decide(
        manifest,
        args,
        ToolContext(
            botId="gate5b-selected-full-toolhost",
            turnId=f"gate5b-full-toolhost:{ctx.session.invocation_id}",
            workspaceRoot=str(ctx.session.get_state("workspaceRoot", "")),
            memoryMode=str(ctx.session.get_state("memoryMode", "normal")),
            permissionScope={
                "mode": "selected_full_toolhost",
                "source": "selected_full_toolhost",
            },
        ),
        mode=mode,
    )
    if decision.action == "allow":
        return
    ctx.decide("deny", reason=_permission_reason_code(decision.metadata))
```

(c) `magi_agent/packs/registries.py` — two edits. First, the loop-plane filter. Shipped:

```python
    for primitive in result.primitives:
        if primitive.type == "control_plane":
            sink_adapter.register(primitive)
```

becomes

```python
    for primitive in result.primitives:
        # phase="tool_host" entries are gate5b dispatch ctx-callables, not
        # LoopControl providers — they load via build_tool_host_runtime_from_packs.
        if primitive.type == "control_plane" and primitive.phase != "tool_host":
            sink_adapter.register(primitive)
```

Second, the gate5b-side loader (append at module end):

```python
def build_tool_host_runtime_from_packs(
    bases: "list[Any] | None" = None,
) -> "tuple[dict[str, Any], tuple[Any, ...]]":
    """Load the gate5b workspace runtime from packs (C1 keystone).

    Returns ``(workspace_handlers_by_tool_name, dispatch_policy_impls)``:
    - handlers from every loaded ``tool`` entry that bound a workspace handler;
    - ``control_plane`` entries with ``phase == "tool_host"`` as ctx-callables,
      ordered by ``(priority, registration)`` via the keyed PrimitiveRegistry
      (last-wins override — a user pack replaces a first-party ref, §1).
    """
    from magi_agent.packs.discovery import (
        default_search_bases,
        discover_pack_files,
        load_packs_config,
        resolve_enabled_packs,
    )
    from magi_agent.packs.loader import RecordingSink, load_packs

    search_bases = list(bases) if bases is not None else default_search_bases()
    discovered = discover_pack_files(search_bases)
    enabled = resolve_enabled_packs(discovered, load_packs_config())
    sink = RecordingSink()
    result = load_packs(enabled, sink)

    registries = PackRegistries()
    tool_primitives = [p for p in result.primitives if p.type == "tool"]
    project_into_registries(tool_primitives, registries)
    handlers = {
        name: registries.workspace_tool_handlers.resolve(name)
        for name in registries.workspace_tool_handlers.list_refs()
    }

    registry = PrimitiveRegistry()
    adapter = RegistryRegistrationSink(registry)
    for primitive in result.primitives:
        if primitive.type == "control_plane" and primitive.phase == "tool_host":
            adapter.register(primitive)
    policies = tuple(
        entry.impl for entry in registry.list(ptype=PrimitiveType.CONTROL_PLANE)
    )
    return handlers, policies
```

- [ ] **Step 5: Run, see it PASS — new pack tests + memory-mode legacy suite + oracle.**

```bash
MAGI_CONFIG="$(mktemp -d)/config.toml" uv run pytest \
  tests/firstparty/test_gates_policy_default_pack.py \
  tests/gates/test_gate5b_full_toolhost_memory_mode.py \
  tests/fixtures/gate5b_golden/ -q
```

- [ ] **Step 6: Phase-0 golden gate (this task edited the control-plane assembly file).**

```bash
MAGI_CONFIG="$(mktemp -d)/config.toml" uv run pytest \
  tests/fixtures/neutral_runtime_golden/test_golden_regression.py \
  tests/packs/ -q
```

Expected: PASS, no diff (the loop-plane filter only EXCLUDES a phase that did not exist before).

- [ ] **Step 7: Commit.**

```bash
git add magi_agent/firstparty/packs/gates_policy_default/ magi_agent/packs/registries.py \
        tests/firstparty/test_gates_policy_default_pack.py
git commit -m "feat(gates): memory-mode + permission-preflight as tool_host control_plane pack

C1 policy migration: _enforce_memory_mode/_filter_memory_mode_output and
_preflight_legacy_tool moved into openmagi.gates-policy-default (ctx-callable
convention, phase=tool_host, gatePosition=before). Loop-plane assembly now
skips tool_host entries; build_tool_host_runtime_from_packs loads handlers +
policies for the host. Phase-0 + gate5b goldens unchanged."
```

## Task C1.7 (flip + delete): gate5b live path loads packs by default

**Files:**
- Modify: `magi_agent/gates/gate5b_full_toolhost.py`
- Test: extend `tests/gates/test_gate5b_workspace_handler_seam.py`

- [ ] **Step 1: Re-grep call sites + the legacy bodies to delete.**

```bash
grep -rn "build_gate5b_full_toolhost_bundle(" magi_agent/ --include="*.py" | grep -v gate5b_full_toolhost
grep -n "if tool_name == \"Clock\"\|def _enforce_memory_mode\|def _filter_memory_mode_output\|def _preflight_legacy_tool" \
  magi_agent/gates/gate5b_full_toolhost.py
```

Expected call sites: `magi_agent/transport/health.py`, `magi_agent/transport/chat_routes.py`
(+ tests). Neither passes the new kwargs — the flip happens inside the bundle builder so call
sites stay untouched.

- [ ] **Step 2: Failing test (append).**

```python
# tests/gates/test_gate5b_workspace_handler_seam.py  (append)
def test_bundle_builder_defaults_to_pack_loaded_runtime(tmp_path: Path):
    from magi_agent.gates.gate5b_full_toolhost import build_gate5b_full_toolhost_bundle

    bundle = build_gate5b_full_toolhost_bundle(
        config={"enabled": True, "killSwitchEnabled": False,
                "routeAttachmentEnabled": True, "environment": "local",
                "environmentAllowlist": ["local"], "maxToolCallsPerTurn": 8,
                "allowedToolNames": ["Clock"]},
        scope={"selectedBotDigest": "", "selectedOwnerDigest": "", "environment": "local"},
        workspace_root=tmp_path,
    )
    assert bundle.status == "ready"
    assert bundle.host._workspace_handlers.get("Clock") is not None
    assert len(bundle.host._dispatch_policies) >= 1
```

- [ ] **Step 3: Run, see it FAIL** (`_workspace_handlers` is empty — builder passes nothing).

```bash
MAGI_CONFIG="$(mktemp -d)/config.toml" uv run pytest \
  tests/gates/test_gate5b_workspace_handler_seam.py::test_bundle_builder_defaults_to_pack_loaded_runtime -q
```

Expected: FAIL — `assert bundle.host._workspace_handlers.get("Clock") is not None` (it is `None`).

- [ ] **Step 4: Implement the flip.**

In `build_gate5b_full_toolhost_bundle`, before the `host = Gate5BFullToolHost(...)` construction:

```python
    if workspace_handlers is None and dispatch_policies is None:
        workspace_handlers, dispatch_policies = _pack_loaded_workspace_runtime()
```

with a module-level memoized loader (pack discovery walks disk; once per process):

```python
@functools.lru_cache(maxsize=1)
def _pack_loaded_workspace_runtime() -> tuple[Mapping[str, object], tuple[object, ...]]:
    from magi_agent.packs.registries import build_tool_host_runtime_from_packs

    return build_tool_host_runtime_from_packs()
```

(`import functools` at top — grep first.) Then DELETE the now-dead legacy paths:

1. the 11 legacy branches inside `_handle` (everything between the handler-first lookup added in
   C1.2 and the final `return await self._dispatch_registry_tool(...)` line);
2. `_enforce_memory_mode`, `_filter_memory_mode_output`, `_preflight_legacy_tool`, and the
   dual-load fallback branches inside `_run_dispatch_policies` /
   `_apply_after_dispatch_policies` (the `if not self._dispatch_policies:` blocks become a plain
   pass-through: no policies → no enforcement → but the flip guarantees the bundled pack supplies
   them; hosts constructed DIRECTLY with neither kwarg now get no memory-mode enforcement, so
   ALSO update `Gate5BFullToolHost.__init__` to default-load via
   `_pack_loaded_workspace_runtime()` when BOTH kwargs are `None` — tests that want a bare host
   pass `workspace_handlers={}` / `dispatch_policies=()` explicitly).

Before deleting, grep each name for external references
(`grep -rn "_enforce_memory_mode\|_preflight_legacy_tool\|_filter_memory_mode_output" magi_agent/ tests/`)
and update any direct test references to drive `dispatch` instead.

- [ ] **Step 5: The load-bearing verify — oracle BYTE-IDENTICAL + everything.**

```bash
MAGI_CONFIG="$(mktemp -d)/config.toml" uv run pytest \
  tests/fixtures/gate5b_golden/ tests/gates/ tests/firstparty/ tests/packs/ \
  tests/fixtures/neutral_runtime_golden/test_golden_regression.py -q
```

Expected: ALL PASS, BOTH oracles no-diff. The C1.0 goldens were captured on the fully-legacy
tree; them passing on the fully-pack-loaded tree IS the C1 acceptance proof. If
`dispatch_blocked` diffs on reason strings, a policy body drifted during the move — fix the pack
impl, never the golden.

- [ ] **Step 6: Commit.**

```bash
git add magi_agent/gates/gate5b_full_toolhost.py tests/gates/test_gate5b_workspace_handler_seam.py
git commit -m "refactor(gates)!: gate5b dispatch is pack-loaded; legacy _handle branches deleted

C1 flip: bundle builder + bare host default to pack-loaded workspace handlers
and tool_host dispatch policies (lru_cache'd loader). 11 legacy branches +
_enforce_memory_mode/_filter_memory_mode_output/_preflight_legacy_tool removed.
Gate5b golden byte-identical across the flip; Phase-0 golden unchanged."
```

---

# C2 — goal_loop_control: continue/stop policy → `loop_policy` pack

**Policy seam (verified):** `decide_loop_continuation(LoopControlInput) -> LoopControlResult`
(`harness/goal_loop_control.py:451`) is already a pure function over injected seams; the typed
context IS `LoopControlInput` (frozen pydantic carrying `store`/`judge`/`spend_probe`/
`evidence_gate` Protocols + scalars) — capability parity needs NO `packs/context.py` extension
beyond the C0 `LoopPolicyProvideContext`. **Mechanism (kernel keeps):**
`build_after_turn_goal_loop_hook` (`:653`) — the AFTER_TURN_END manifest+handler, fail-open
plumbing, `decision_sink` delivery — and `build_continuation_prompt` re-injection contract
(USER-role only; the prefix-cache invariant). The ONLY hardcode is the handler's direct call
`result = decide_loop_continuation(loop_input)`.

## Task C2.0 (oracle FIRST): goal-loop golden oracle

**Files (new):** `tests/fixtures/goal_loop_golden/{__init__.py,scenarios.py,capture.py,test_golden_regression.py,golden/}`

- [ ] **Step 1: Re-read the decision branches + the existing fakes.**

```bash
grep -n "decision=\"stop\"\|decision=\"continue\"\|reason=" magi_agent/harness/goal_loop_control.py | head -20
grep -n "class FakeJudge\|def judge" tests/test_goal_loop_control_b3.py | head
grep -n "parse_failure_budget_exhausted" magi_agent/harness/goal_judge.py tests/test_goal_loop_control_b3.py | head -4
```

Copy the `judge_budget` trigger (the consecutive-failure count at which `run_judge` returns
`reason="parse_failure_budget_exhausted"`) from `tests/test_goal_loop_control_b3.py` — do not
guess the budget constant.

- [ ] **Step 2: Write the scenario drivers (pure, fully deterministic — no tmp dirs, no clock).**

```python
# tests/fixtures/goal_loop_golden/scenarios.py
"""One trace entry per decide_loop_continuation branch (module-docstring state
machine §1-7). Fakes follow tests/test_goal_loop_control_b3.py. Evidence records
carry a wall-clock observedAt, so the trace records ONLY the deterministic
result scalars + a continuation-prompt digest."""
from __future__ import annotations

import hashlib
import os
from typing import Any

from magi_agent.harness.goal_loop_control import (
    LoopControlInput,
    decide_loop_continuation,
)
from magi_agent.harness.goal_state import InMemoryGoalStateStore


class _Judge:
    """GoalJudge fake: returns a JudgeVerdict-shaped object whose .raw drives
    parse_verdict (JSON-first contract from harness/goal_judge.py)."""

    def __init__(self, raw: str) -> None:
        self._raw = raw

    def judge(self, goal: str, transcript_excerpt: str) -> Any:
        from magi_agent.harness.goal_judge import JudgeVerdict

        return JudgeVerdict(satisfied=False, raw=self._raw)


class _RaisingJudge:
    def judge(self, goal: str, transcript_excerpt: str) -> Any:
        raise RuntimeError("judge exploded")


class _Probe:
    def __init__(self, capped: bool) -> None:
        self._capped = capped

    def is_capped(self) -> bool:
        return self._capped


class _FailingGate:
    def check(self, goal: str, transcript_excerpt: str, goal_state: Any) -> Any:
        from magi_agent.harness.goal_loop_control import EvidenceGateVerdict

        return EvidenceGateVerdict(passed=False, reason="evidence_missing")


def _store(max_turns: int = 8, *, status: str = "active", turns_used: int = 0):
    store = InMemoryGoalStateStore()
    state = store.set_goal("s1", "ship the feature", max_turns=max_turns)
    if status != "active" or turns_used:
        store.upsert(state.model_copy(update={"status": status, "turns_used": turns_used}))
    return store


def _decide(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = dict(
        store=_store(),
        judge=_Judge('{"satisfied": false}'),
        sessionId="s1",
        transcriptExcerpt="did a step",
        spendProbe=_Probe(False),
        enabled=True,
        shadow=False,  # explicit: acted decisions, no env dependence
    )
    base.update(overrides)
    result = decide_loop_continuation(LoopControlInput.model_validate(base))
    prompt = result.continuation_prompt or ""
    return {
        "decision": result.decision,
        "reason": result.reason,
        "observeOnly": result.observe_only,
        "turnsUsed": result.goal_state_after.turns_used,
        "statusAfter": result.goal_state_after.status,
        "failuresAfter": result.consecutive_parse_failures_after,
        "continuationDigest": (
            "sha256:" + hashlib.sha256(prompt.encode()).hexdigest()[:16] if prompt else None
        ),
    }


def run_decision_matrix_scenario() -> list[dict[str, Any]]:
    trace: list[dict[str, Any]] = []

    def add(name: str, **overrides: Any) -> None:
        entry = {"scenario": name}
        entry.update(_decide(**overrides))
        trace.append(entry)

    add("disabled", enabled=False)
    add("spend_capped", spendProbe=_Probe(True))
    add("terminal_cleared", store=_store(status="cleared"))
    add("preempted", userMessagePending=True)
    add("satisfied_gate_off", judge=_Judge('{"satisfied": true}'))
    add("not_satisfied_continue")
    add("exhausted_on_advance", store=_store(max_turns=1))
    add("parse_failure_fail_open", judge=_Judge("garbage with no verdict"))
    add("judge_raised_fail_open", judge=_RaisingJudge())
    # judge_budget: consecutiveParseFailures at the budget edge — copy the exact
    # count from tests/test_goal_loop_control_b3.py's budget test before capture.
    add("judge_budget", judge=_Judge("garbage"), consecutiveParseFailures=2)

    # evidence_unmet needs the env gate ON + a failing gate (B4 branch).
    previous = os.environ.get("MAGI_GOAL_LOOP_EVIDENCE_GATE")
    os.environ["MAGI_GOAL_LOOP_EVIDENCE_GATE"] = "1"
    try:
        add("evidence_unmet", judge=_Judge('{"satisfied": true}'),
            evidenceGate=_FailingGate())
    finally:
        if previous is None:
            os.environ.pop("MAGI_GOAL_LOOP_EVIDENCE_GATE", None)
        else:
            os.environ["MAGI_GOAL_LOOP_EVIDENCE_GATE"] = previous
    return trace
```

- [ ] **Step 3: Write capture + regression** — clone `tests/fixtures/gate5b_golden/capture.py`
and `test_golden_regression.py` verbatim with: import path
`tests.fixtures.goal_loop_golden`, `SCENARIOS = {"decision_matrix":
scenarios.run_decision_matrix_scenario}`, and this non-trivial expectations test:

```python
def test_goal_loop_golden_is_non_trivial() -> None:
    import json

    trace = json.loads(golden_path("decision_matrix").read_text())
    by_name = {e["scenario"]: e for e in trace}
    assert len(by_name) == 11
    assert by_name["disabled"]["reason"] == "disabled"
    assert by_name["spend_capped"]["reason"] == "spend_capped"
    assert by_name["satisfied_gate_off"]["statusAfter"] == "satisfied"
    assert by_name["not_satisfied_continue"]["decision"] == "continue"
    assert by_name["not_satisfied_continue"]["continuationDigest"]
    assert by_name["exhausted_on_advance"]["reason"] == "exhausted"
    assert by_name["evidence_unmet"]["reason"] == "evidence_unmet"
    assert by_name["judge_budget"]["reason"] == "judge_budget"
```

- [ ] **Step 4: Capture on the pristine tree; verify twice; fix `judge_budget` count from the b3
test if the expectations test fails.**

```bash
MAGI_CONFIG="$(mktemp -d)/config.toml" uv run python -m tests.fixtures.goal_loop_golden.capture --write
MAGI_CONFIG="$(mktemp -d)/config.toml" uv run pytest tests/fixtures/goal_loop_golden/ -q
MAGI_CONFIG="$(mktemp -d)/config.toml" uv run pytest tests/fixtures/goal_loop_golden/ -q
```

- [ ] **Step 5: Commit.**

```bash
git add tests/fixtures/goal_loop_golden/
git commit -m "test(goal-loop): decision-matrix golden oracle (11 branches)

Pack C2 oracle captured pre-decomposition: every decide_loop_continuation
branch (disabled/spend/terminal/preempted/satisfied/evidence_unmet/continue/
exhausted/2x fail-open/judge_budget) with continuation-prompt digests."
```

## Task C2.1: the `policy` parameter on the kernel hook (dual-load)

**Files:**
- Modify: `magi_agent/harness/goal_loop_control.py`
- Test: `tests/test_goal_loop_policy_seam.py` (new)

- [ ] **Step 1: Re-grep the shipped hook builder.**

```bash
grep -n "def build_after_turn_goal_loop_hook\|result = decide_loop_continuation\|LoopControlInputProvider\|LoopControlDecisionSink" \
  magi_agent/harness/goal_loop_control.py
```

Shipped signature: `build_after_turn_goal_loop_hook(*, input_provider, decision_sink=None)`;
handler body calls `result = decide_loop_continuation(loop_input)`.

- [ ] **Step 2: Failing test.**

```python
# tests/test_goal_loop_policy_seam.py
from __future__ import annotations

from magi_agent.harness.goal_loop_control import (
    LoopControlResult,
    build_after_turn_goal_loop_hook,
)
from magi_agent.harness.goal_state import GoalState
from magi_agent.hooks.context import HookContext


def _stop_result() -> LoopControlResult:
    return LoopControlResult(
        decision="stop",
        reason="disabled",
        goalStateAfter=GoalState(goal="g", sessionId="s1"),
        consecutiveParseFailuresAfter=0,
    )


def test_hook_routes_through_injected_loop_policy():
    calls: list[str] = []

    def custom_policy(loop_input):
        calls.append("custom")
        return _stop_result()

    seen: list[LoopControlResult] = []
    manifest, handler = build_after_turn_goal_loop_hook(
        input_provider=lambda ctx: object.__new__(_FakeInput),  # replaced below
        decision_sink=seen.append,
        policy=custom_policy,
    )
    # input_provider must return a LoopControlInput-shaped object; the policy is
    # the consumer, so a sentinel suffices for THIS seam test.
    result = handler(HookContext.model_construct())
    assert calls == ["custom"]
    assert seen and seen[0].reason == "disabled"
    assert result.action == "continue"


class _FakeInput:  # sentinel passed straight to the injected policy
    pass
```

> NOTE: `HookContext` is frozen pydantic; if `model_construct()` requires fields, copy the minimal
> construction from an existing `build_after_turn_goal_loop_hook` test
> (`grep -rn "build_after_turn_goal_loop_hook" tests/ | head`) — Step 1 of the implementing agent.

- [ ] **Step 3: Run, see it FAIL** (`unexpected keyword argument 'policy'`).

```bash
MAGI_CONFIG="$(mktemp -d)/config.toml" uv run pytest tests/test_goal_loop_policy_seam.py -q
```

- [ ] **Step 4: Implement.** In `goal_loop_control.py`, add the alias + parameter:

```python
#: C2 policy seam: the continue/stop decision as a swappable callable. The
#: first-party policy is ``decide_loop_continuation``; a ``loop_policy`` pack
#: registers an alternative with the SAME signature (LoopControlInput in,
#: LoopControlResult out — the typed context IS LoopControlInput).
LoopContinuationPolicy = Callable[["LoopControlInput"], "LoopControlResult"]
```

and change the builder (quoted shipped lines → new):

```python
def build_after_turn_goal_loop_hook(
    *,
    input_provider: LoopControlInputProvider,
    decision_sink: LoopControlDecisionSink | None = None,
    policy: "LoopContinuationPolicy | None" = None,
) -> tuple[HookManifest, Callable[[HookContext], HookResult]]:
```

with, inside the builder before `_handler`:

```python
    decide = policy if policy is not None else decide_loop_continuation
```

and the handler line `result = decide_loop_continuation(loop_input)` →
`result = decide(loop_input)`. Export `LoopContinuationPolicy` in `__all__`.

- [ ] **Step 5: Run, see it PASS + the legacy goal-loop suites + the C2.0 oracle.**

```bash
MAGI_CONFIG="$(mktemp -d)/config.toml" uv run pytest \
  tests/test_goal_loop_policy_seam.py tests/test_goal_loop_control_b3.py \
  tests/test_goal_loop_control_b4.py tests/test_persistent_goal_loop_contract.py \
  tests/fixtures/goal_loop_golden/ -q
```

- [ ] **Step 6: Commit.**

```bash
git add magi_agent/harness/goal_loop_control.py tests/test_goal_loop_policy_seam.py
git commit -m "feat(goal-loop): LoopContinuationPolicy seam on the after-turn hook

C2 policy/mechanism split: build_after_turn_goal_loop_hook takes policy=None
(default decide_loop_continuation, byte-identical). The typed context is
LoopControlInput itself. Oracle + b3/b4 suites unchanged."
```

## Task C2.2: bundled `goal_loop_default` pack + resolution helper + flip

**Files:**
- New: `magi_agent/firstparty/packs/goal_loop_default/{__init__.py,pack.toml,impl.py}`
- Modify: `magi_agent/harness/goal_loop_control.py` (resolution helper)
- Test: `tests/firstparty/test_goal_loop_default_pack.py` (new)

- [ ] **Step 1: Failing test.**

```python
# tests/firstparty/test_goal_loop_default_pack.py
from __future__ import annotations

from pathlib import Path


def test_bundled_loop_policy_is_decide_loop_continuation():
    from magi_agent.harness.goal_loop_control import (
        decide_loop_continuation,
        resolve_loop_policy,
    )

    assert resolve_loop_policy() is decide_loop_continuation


def test_user_pack_overrides_bundled_loop_policy(tmp_path: Path, monkeypatch):
    """§1 override: a user pack re-declaring loop_policy:ralph@1 replaces the
    bundled first-party policy through the identical loader path."""
    pack = tmp_path / "my-loop"
    pack.mkdir()
    (pack / "pack.toml").write_text(
        'packId = "user.my-loop"\ndisplayName = "mine"\n\n'
        "[[provides]]\n"
        'type = "loop_policy"\n'
        'ref = "loop_policy:ralph@1"\n'
        'impl = "tests.packs.pack_c_fixture_impls:provide_ralph_override"\n'
    )
    from magi_agent.harness.goal_loop_control import resolve_loop_policy
    from magi_agent.packs.discovery import default_search_bases

    bases = list(default_search_bases()) + [tmp_path]
    policy = resolve_loop_policy(bases=bases)
    assert getattr(policy, "__name__", "") == "_ralph_override"
```

and append to `tests/packs/pack_c_fixture_impls.py`:

```python
def _ralph_override(loop_input: Any) -> Any:
    raise AssertionError("override marker — never executed in this test")


def provide_ralph_override(context: Any) -> None:
    context.register("loop_policy:ralph@1", _ralph_override)
```

- [ ] **Step 2: Run, see it FAIL** (`ImportError: resolve_loop_policy`).

```bash
MAGI_CONFIG="$(mktemp -d)/config.toml" uv run pytest tests/firstparty/test_goal_loop_default_pack.py -q
```

- [ ] **Step 3: Implement.**

```toml
# magi_agent/firstparty/packs/goal_loop_default/pack.toml
# Bundled first-party goal-loop continuation policy (the Ralph-loop state
# machine). Same loader/format as a user pack; override/forbid by re-declaring
# or disabling this ref (§1). Mechanism (after-turn hook, fail-open plumbing,
# USER-role continuation re-injection contract) stays kernel.

packId = "openmagi.goal-loop-default"
displayName = "Goal loop policy (Ralph)"
version = "1.0.0"
description = "First-party continue/stop loop policy bundled as a removable pack."

[[provides]]
type = "loop_policy"
ref = "loop_policy:ralph@1"
impl = "magi_agent.firstparty.packs.goal_loop_default.impl:provide_ralph_policy"
```

```python
# magi_agent/firstparty/packs/goal_loop_default/impl.py
"""First-party loop policy provider (no privilege, typed-ctx only)."""
from __future__ import annotations

from magi_agent.packs.context import LoopPolicyProvideContext


def provide_ralph_policy(context: LoopPolicyProvideContext) -> None:
    from magi_agent.harness.goal_loop_control import decide_loop_continuation

    context.register("loop_policy:ralph@1", decide_loop_continuation)
```

Resolution helper in `goal_loop_control.py` (append near the hook builder; lazy imports keep this
module's forbidden-import contract — no adk/network modules enter the top-level graph):

```python
DEFAULT_LOOP_POLICY_REF = "loop_policy:ralph@1"


def resolve_loop_policy(
    *,
    ref: str = DEFAULT_LOOP_POLICY_REF,
    bases: "list[object] | None" = None,
) -> "LoopContinuationPolicy":
    """Resolve the loop policy from loaded packs; fall back to the in-module
    first-party policy when packs are unavailable (fail-open: the loop must
    never break because pack discovery failed)."""
    try:
        from magi_agent.packs.discovery import default_search_bases
        from magi_agent.packs.registries import load_into_registries

        search = list(bases) if bases is not None else list(default_search_bases())
        registries, _ = load_into_registries(search)
        policy = registries.loop_policies.resolve(ref)
        if callable(policy):
            return policy
    except Exception:  # noqa: BLE001 — fail-open to the bundled default
        pass
    return decide_loop_continuation
```

Export `resolve_loop_policy` + `DEFAULT_LOOP_POLICY_REF` in `__all__`. Then flip the default at
the seam: in `build_after_turn_goal_loop_hook`, change C2.1's line to

```python
    decide = policy if policy is not None else resolve_loop_policy()
```

(With only the bundled pack on disk this resolves to `decide_loop_continuation` — behavior
identical; the C2.0 oracle proves it.)

- [ ] **Step 4: Run, see it PASS — pack tests + full goal-loop suites + oracle + module
import-boundary test.**

```bash
MAGI_CONFIG="$(mktemp -d)/config.toml" uv run pytest \
  tests/firstparty/test_goal_loop_default_pack.py tests/test_goal_loop_policy_seam.py \
  tests/test_goal_loop_control_b3.py tests/test_goal_loop_control_b4.py \
  tests/test_persistent_goal_loop_contract.py tests/fixtures/goal_loop_golden/ -q
```

(If `test_persistent_goal_loop_contract.py` asserts the module's forbidden-import list, the lazy
imports inside `resolve_loop_policy` keep it green — verify, do not skip.)

- [ ] **Step 5: Commit.**

```bash
git add magi_agent/firstparty/packs/goal_loop_default/ magi_agent/harness/goal_loop_control.py \
        tests/firstparty/test_goal_loop_default_pack.py tests/packs/pack_c_fixture_impls.py
git commit -m "feat(goal-loop): bundle Ralph policy as loop_policy pack + pack-loaded default

C2 flip: hook resolves loop_policy:ralph@1 from packs (fail-open to the
in-module function). User-pack override proven end-to-end. Oracle unchanged."
```

---

# C3 — scheduler: "which job / when" policy → `schedule_policy` pack

**Policy seam (verified):** inside `_tick_inside_lock` (`harness/scheduler_executor.py:494`) the
only opinionated decisions are (a) which due records actually fire (today: every record
`source.due_jobs(now)` returns, in source order) and (b) the next-run computation
`job.compute_next_run(now=now)` (cron/interval/once grammar from `missions/schedule_grammar.py`)
with the `_ONCE_EXHAUSTED_NEXT_RUN` (year-9999) sentinel fallback. **Mechanism (kernel keeps):**
`acquire_tick_lock` file lock, `validate_scheduler_lease`, the at-most-once
`record_advance`-BEFORE-receipt ordering, the local_fake receipt schema, evidence digests,
`SchedulerExecutorAuthorityFlags` (all `Literal[False]`), and `execute_due_jobs`'s gating
(`scheduler_job_execution.py:437` — readiness/kill-switch/oc-cron guard stay untouched).

## Task C3.0 (oracle FIRST): scheduler golden oracle

**Files (new):** `tests/fixtures/scheduler_golden/{__init__.py,scenarios.py,capture.py,test_golden_regression.py,golden/}`

- [ ] **Step 1: Re-read the a2 fixtures you will copy.**

```bash
grep -n "SchedulerLease(\|scheduleExpr=\|owner_digest\|def test_" tests/test_scheduler_executor_a2.py | head -20
grep -n "def validate_scheduler_lease" -A20 magi_agent/harness/scheduler_runtime.py | head -26
```

Copy the valid-lease construction (`SchedulerLease(leaseId=…, ownerDigest=…, acquiredAt=…,
expiresAt=…)`) and the `"every 10m"` schedule expressions from the a2 test.

- [ ] **Step 2: Scenario drivers.**

```python
# tests/fixtures/scheduler_golden/scenarios.py
"""Three tick scenarios over the REAL tick() with InMemoryJobSource + a tmp
lock dir. Fixed `now`; receipts/evidence digests are pure functions of the
inputs, so the public projections are byte-stable. Lease/lock constructions
copied from tests/test_scheduler_executor_a2.py."""
from __future__ import annotations

import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from magi_agent.harness.scheduler_executor import (
    InMemoryJobSource,
    ScheduledJobRecord,
    acquire_tick_lock,
    tick,
)
from magi_agent.harness.scheduler_runtime import SchedulerLease

_NOW = datetime(2026, 6, 1, 12, 0, 0, tzinfo=UTC)
_NOW_MS = int(_NOW.timestamp() * 1000)
_OWNER = "owner-digest-1"


def _lease() -> SchedulerLease:
    return SchedulerLease(
        leaseId="lease-1",
        ownerDigest=_OWNER,
        acquiredAt=_NOW_MS - 1_000,
        expiresAt=_NOW_MS + 60_000,
    )


def _source() -> InMemoryJobSource:
    return InMemoryJobSource(
        [
            ScheduledJobRecord(  # due (next_run in the past)
                jobId="job-due", scheduleExpr="every 10m",
                nextRun=_NOW - timedelta(minutes=1),
            ),
            ScheduledJobRecord(  # not due
                jobId="job-later", scheduleExpr="every 10m",
                nextRun=_NOW + timedelta(hours=1),
            ),
        ]
    )


def _project(result: Any) -> dict[str, Any]:
    return result.public_projection()


def run_tick_fires_due_scenario() -> list[dict[str, Any]]:
    with tempfile.TemporaryDirectory() as tmp:
        source = _source()
        first = tick(now=_NOW, source=source, lease=_lease(),
                     lock_dir=Path(tmp), owner_digest=_OWNER)
        # Second tick at the same instant: job-due advanced, nothing fires.
        second = tick(now=_NOW, source=source, lease=_lease(),
                      lock_dir=Path(tmp), owner_digest=_OWNER)
        return [
            {"scenario": "first_tick", **_project(first)},
            {"scenario": "second_tick_no_refire", **_project(second)},
        ]


def run_tick_blocked_lease_scenario() -> list[dict[str, Any]]:
    with tempfile.TemporaryDirectory() as tmp:
        result = tick(now=_NOW, source=_source(), lease=None,
                      lock_dir=Path(tmp), owner_digest=_OWNER)
        return [{"scenario": "lease_missing", **_project(result)}]


def run_tick_lock_held_scenario() -> list[dict[str, Any]]:
    with tempfile.TemporaryDirectory() as tmp:
        with acquire_tick_lock(lock_dir=Path(tmp)):
            result = tick(now=_NOW, source=_source(), lease=_lease(),
                          lock_dir=Path(tmp), owner_digest=_OWNER)
        return [{"scenario": "lock_held", **_project(result)}]
```

> Determinism note: `evidenceDigest` is `sha256` over `{nowUtcIso, leaseState, firedJobIds,
> skippedJobIds, status, schemaVersion}` (`_build_evidence_digest`, verified) — all fixed inputs,
> so it is stable and SHOULD be recorded (it pins the at-most-once accounting).
> Same-process lock note: `acquire_tick_lock` uses `flock`, which on Linux is per-(file, fd) —
> the nested `tick` opens its own fd, so the inner acquisition fails and
> `tick_skipped_lock_held` is returned (this is exactly the shipped `_LockHeld` path; the a2
> suite already relies on it — if the lock-held scenario records `tick_completed` on your
> platform, copy the lock-contention setup from the a2 test instead).

- [ ] **Step 3: capture + regression** — clone the gate5b capture/regression pair with import path
`tests.fixtures.scheduler_golden`,
`SCENARIOS = {"fires_due": …run_tick_fires_due_scenario, "blocked_lease": …, "lock_held": …}`,
and expectations:

```python
def test_scheduler_goldens_are_non_trivial() -> None:
    import json

    fires = json.loads(golden_path("fires_due").read_text())
    assert fires[0]["firedJobIds"] == ["job-due"]
    assert fires[0]["skippedJobIds"] == ["job-later"]
    assert fires[1]["firedJobIds"] == []  # at-most-once: no re-fire
    assert all(not any(e["authorityFlags"].values()) for e in fires)
    blocked = json.loads(golden_path("blocked_lease").read_text())
    assert blocked[0]["status"] == "tick_blocked_lease"
    held = json.loads(golden_path("lock_held").read_text())
    assert held[0]["status"] == "tick_skipped_lock_held"
```

- [ ] **Step 4: Capture pristine; verify twice; commit.**

```bash
MAGI_CONFIG="$(mktemp -d)/config.toml" uv run python -m tests.fixtures.scheduler_golden.capture --write
MAGI_CONFIG="$(mktemp -d)/config.toml" uv run pytest tests/fixtures/scheduler_golden/ -q
MAGI_CONFIG="$(mktemp -d)/config.toml" uv run pytest tests/fixtures/scheduler_golden/ -q
git add tests/fixtures/scheduler_golden/
git commit -m "test(scheduler): tick golden oracle (fire+advance / lease / lock scenarios)"
```

## Task C3.1: `SchedulePolicy` seam on `tick()` (dual-load)

**Files:**
- Modify: `magi_agent/harness/scheduler_executor.py`
- Modify: `magi_agent/harness/scheduler_job_execution.py` (pass-through param)
- Test: `tests/test_schedule_policy_seam.py` (new)

- [ ] **Step 1: Re-grep the exact policy call site.** Shipped `_tick_inside_lock` loop head:

```python
    for job in due:
        # 1. Compute new next_run
        new_next_run = job.compute_next_run(now=now)
        if new_next_run is None:
            # 'once' schedule has no future run — use exhausted sentinel to prevent re-fire
            new_next_run = _ONCE_EXHAUSTED_NEXT_RUN
```

```bash
grep -n "compute_next_run(now=now)\|due = list(source.due_jobs(now))\|def tick(\|def _tick_inside_lock(" \
  magi_agent/harness/scheduler_executor.py
grep -n "tick(\n\|tick(" magi_agent/harness/scheduler_job_execution.py | head
```

- [ ] **Step 2: Failing test.**

```python
# tests/test_schedule_policy_seam.py
from __future__ import annotations

import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

from magi_agent.harness.scheduler_executor import (
    CronSchedulePolicy,
    InMemoryJobSource,
    ScheduledJobRecord,
    tick,
)
from magi_agent.harness.scheduler_runtime import SchedulerLease

_NOW = datetime(2026, 6, 1, 12, 0, 0, tzinfo=UTC)
_NOW_MS = int(_NOW.timestamp() * 1000)


def _lease():
    return SchedulerLease(leaseId="l", ownerDigest="o",
                          acquiredAt=_NOW_MS - 1000, expiresAt=_NOW_MS + 60000)


def _due_source():
    return InMemoryJobSource([
        ScheduledJobRecord(jobId="a", scheduleExpr="every 10m",
                           nextRun=_NOW - timedelta(minutes=1)),
        ScheduledJobRecord(jobId="b", scheduleExpr="every 10m",
                           nextRun=_NOW - timedelta(minutes=2)),
    ])


class _SuppressBPolicy(CronSchedulePolicy):
    """User-shaped policy: refuses to fire job 'b' this tick."""

    def select_due(self, due, *, now):
        return [j for j in due if j.job_id != "b"]


def test_injected_policy_filters_due_selection():
    with tempfile.TemporaryDirectory() as tmp:
        result = tick(now=_NOW, source=_due_source(), lease=_lease(),
                      lock_dir=Path(tmp), owner_digest="o",
                      policy=_SuppressBPolicy())
    assert result.fired_job_ids == ("a",)
    assert "b" in result.skipped_job_ids


def test_default_policy_is_byte_identical_to_legacy():
    with tempfile.TemporaryDirectory() as tmp_a, tempfile.TemporaryDirectory() as tmp_b:
        legacy = tick(now=_NOW, source=_due_source(), lease=_lease(),
                      lock_dir=Path(tmp_a), owner_digest="o")
        packed = tick(now=_NOW, source=_due_source(), lease=_lease(),
                      lock_dir=Path(tmp_b), owner_digest="o",
                      policy=CronSchedulePolicy())
    assert legacy.model_dump(by_alias=True) == packed.model_dump(by_alias=True)
```

- [ ] **Step 3: Run, see it FAIL** (`ImportError: CronSchedulePolicy`; `tick()` has no `policy`).

```bash
MAGI_CONFIG="$(mktemp -d)/config.toml" uv run pytest tests/test_schedule_policy_seam.py -q
```

- [ ] **Step 4: Implement.** In `scheduler_executor.py` add (next to `ScheduledJobSource`):

```python
@runtime_checkable
class SchedulePolicy(Protocol):
    """C3 policy seam: 'which job / when'. The kernel mechanism (lock, lease,
    at-most-once advance-before-receipt) is policy-agnostic."""

    def select_due(
        self, due: Sequence[ScheduledJobRecord], *, now: datetime
    ) -> Sequence[ScheduledJobRecord]:
        """Filter/order the records that actually fire this tick."""
        ...

    def next_run_after_fire(
        self, job: ScheduledJobRecord, *, now: datetime
    ) -> datetime | None:
        """Next run after firing at *now*; None = no future run (kernel applies
        the once-exhausted sentinel)."""
        ...


class CronSchedulePolicy:
    """First-party policy: fire everything due, advance by the schedule grammar
    (the exact legacy behavior — select_due is the identity)."""

    def select_due(
        self, due: Sequence[ScheduledJobRecord], *, now: datetime
    ) -> Sequence[ScheduledJobRecord]:
        return list(due)

    def next_run_after_fire(
        self, job: ScheduledJobRecord, *, now: datetime
    ) -> datetime | None:
        return job.compute_next_run(now=now)
```

Thread it: `tick(*, now, source, lease, lock_dir=None, owner_digest, policy: SchedulePolicy |
None = None, _on_receipt=None)` → `_tick_inside_lock(..., policy=policy or
CronSchedulePolicy(), ...)`, and in `_tick_inside_lock` replace the quoted Step-1 lines with:

```python
    due = list(policy.select_due(due, now=now))
    ...
    for job in due:
        # 1. Compute new next_run (policy decision; sentinel stays kernel)
        new_next_run = policy.next_run_after_fire(job, now=now)
        if new_next_run is None:
            new_next_run = _ONCE_EXHAUSTED_NEXT_RUN
```

(Place the `select_due` call AFTER the `fired_id_set`/`skipped_ids` computation is moved below
it — skipped accounting must reflect the policy's selection: recompute `fired_id_set` from the
post-filter list so a policy-suppressed job lands in `skipped_job_ids`, exactly what this task's
Step-2 seam test asserts.) Add `SchedulePolicy`/`CronSchedulePolicy` to `__all__`. In
`scheduler_job_execution.py`, add the same optional `policy` param to `execute_due_jobs` and pass
it to its internal `tick(...)` call (grep Step 1 located it) — default `None` keeps A2/A3
behavior byte-identical.

- [ ] **Step 5: Run, see it PASS — seam test + a2/a5 suites + oracle.**

```bash
MAGI_CONFIG="$(mktemp -d)/config.toml" uv run pytest \
  tests/test_schedule_policy_seam.py tests/test_scheduler_executor_a2.py \
  tests/test_scheduler_executor_readiness_a5.py tests/fixtures/scheduler_golden/ -q
```

- [ ] **Step 6: Commit.**

```bash
git add magi_agent/harness/scheduler_executor.py magi_agent/harness/scheduler_job_execution.py \
        tests/test_schedule_policy_seam.py
git commit -m "feat(scheduler): SchedulePolicy seam on tick (select_due + next_run_after_fire)

C3 policy/mechanism split: cron-grammar advance + due selection behind a
Protocol; CronSchedulePolicy is the byte-identical default. Lock/lease/
at-most-once ordering untouched. Oracle + a2/a5 unchanged."
```

## Task C3.2: bundled `scheduler_default` pack + flip

**Files:**
- New: `magi_agent/firstparty/packs/scheduler_default/{__init__.py,pack.toml,impl.py}`
- Modify: `magi_agent/harness/scheduler_executor.py` (resolution helper + default flip)
- Test: `tests/firstparty/test_scheduler_default_pack.py` (new)

- [ ] **Step 1: Failing test.**

```python
# tests/firstparty/test_scheduler_default_pack.py
from __future__ import annotations


def test_bundled_schedule_policy_resolves_to_cron():
    from magi_agent.harness.scheduler_executor import (
        CronSchedulePolicy,
        resolve_schedule_policy,
    )

    assert isinstance(resolve_schedule_policy(), CronSchedulePolicy)
```

- [ ] **Step 2: Run, see it FAIL → implement.**

```bash
MAGI_CONFIG="$(mktemp -d)/config.toml" uv run pytest tests/firstparty/test_scheduler_default_pack.py -q
```

Expected: FAIL (`ImportError: cannot import name 'resolve_schedule_policy'`).

```toml
# magi_agent/firstparty/packs/scheduler_default/pack.toml
packId = "openmagi.scheduler-default"
displayName = "Schedule policy (cron grammar)"
version = "1.0.0"
description = "First-party 'which job / when' policy bundled as a removable pack."

[[provides]]
type = "schedule_policy"
ref = "schedule_policy:cron@1"
impl = "magi_agent.firstparty.packs.scheduler_default.impl:provide_cron_policy"
```

```python
# magi_agent/firstparty/packs/scheduler_default/impl.py
from __future__ import annotations

from magi_agent.packs.context import SchedulePolicyProvideContext


def provide_cron_policy(context: SchedulePolicyProvideContext) -> None:
    from magi_agent.harness.scheduler_executor import CronSchedulePolicy

    context.register("schedule_policy:cron@1", CronSchedulePolicy())
```

Resolution helper + flip in `scheduler_executor.py` (mirror C2.2's fail-open shape exactly —
same try/except, registry slot `registries.schedule_policies`, ref
`"schedule_policy:cron@1"`, fallback `CronSchedulePolicy()`; name it
`resolve_schedule_policy`, export it). Flip the default inside `tick`:
`policy = policy if policy is not None else resolve_schedule_policy()`.

> Forbidden-import note (module docstring `:18`): `scheduler_executor.py` must not import
> urllib/socket/subprocess/http/requests at top level — the helper's pack imports are LAZY
> (inside the function), same as C2.2. Run the a2 import-boundary assertions to prove it.

- [ ] **Step 3: Run, see it PASS + oracle + suites; commit.**

```bash
MAGI_CONFIG="$(mktemp -d)/config.toml" uv run pytest \
  tests/firstparty/test_scheduler_default_pack.py tests/test_schedule_policy_seam.py \
  tests/test_scheduler_executor_a2.py tests/fixtures/scheduler_golden/ -q
git add magi_agent/firstparty/packs/scheduler_default/ magi_agent/harness/scheduler_executor.py \
        tests/firstparty/test_scheduler_default_pack.py
git commit -m "feat(scheduler): bundle cron policy as schedule_policy pack + pack-loaded default"
```

---

# C4 — memory: recall/compaction/review *strategies* → `memory_strategy` packs

**Policy seams (verified — the harnesses are ALREADY parameter-injected; only the DEFAULTS are
hardcoded):**

| Harness | Mechanism (kernel keeps) | Strategy (→ pack) | Hardcode today |
|---|---|---|---|
| `MemoryRecallHarness.recall` (`memory_recall.py:81`) | adapter plumbing, default-off authority pins, `execute_readonly_memory_recall` envelope | the projection/namespace policies | callers construct them inline; harness passes through |
| `MemoryCompactionHarness.compact` (`memory_compaction.py:367`) | receipt construction, digest-only redaction, authority pins, enabled/local-fake gating | the denial DECISION | `_compaction_denial_reasons(safe_request, safe_policy)` called by name (`:395` region) |
| `MemoryReviewHarness` (`memory_review.py:182`) | write-host delegation, receipt counts, authority pins | the N-turn TRIGGER | callers invoke module-level `should_run_review` by name |

This mirrors the S-A evidence split: stores/envelopes = kernel; the opinionated decision = a
named, replaceable strategy.

## Task C4.0 (oracle FIRST): memory golden oracle

**Files (new):** `tests/fixtures/memory_golden/{__init__.py,scenarios.py,capture.py,test_golden_regression.py,golden/}`

- [ ] **Step 1: Re-read the deterministic surfaces.**

```bash
grep -n "def _receipt_id\|def _output_digest\|observedAt\|datetime.now" magi_agent/harness/memory_compaction.py | head
grep -n "def execute_readonly_memory_recall" -A12 magi_agent/recipes/first_party/memory_recall.py | head -20
grep -rn "MemoryCompactionPolicy(\|MemoryCompactionRequest(" tests/test_memory_compaction_wiring_e2e.py | head -6
```

`_receipt_id(request, status)` is a pure digest (verified) — receipts carry no wall clock; if the
Step-1 grep finds an `observedAt`/`now` field anywhere in the receipt, record only the scalar
subset below (status/reasonCodes/executed), never the full projection.

- [ ] **Step 2: Scenario drivers.**

```python
# tests/fixtures/memory_golden/scenarios.py
"""Three drivers: the compaction denial matrix (the C4 policy being decomposed),
the recall disabled-gate scenario, and the review-trigger table.
Request/policy constructions copied from tests/test_memory_compaction_wiring_e2e.py
and tests/test_memory_recall_recipe_harness.py."""
from __future__ import annotations

import asyncio
from typing import Any

from magi_agent.harness.memory_compaction import (
    MemoryCompactionHarness,
    MemoryCompactionPolicy,
    MemoryCompactionRequest,
)
from magi_agent.harness.memory_review import should_run_review


def _request(**overrides: Any) -> MemoryCompactionRequest:
    base: dict[str, Any] = {
        "providerId": "hipocampus-local",
        "turnId": "turn-1",
        "sourceRefs": ("evidence:src-1",),
        "evidenceRefs": ("evidence:compaction-1",),
    }
    base.update(overrides)
    return MemoryCompactionRequest.model_validate(base)


def _policy(**overrides: Any) -> MemoryCompactionPolicy:
    base: dict[str, Any] = {
        "policyRef": "policy:memory-compaction",
        "policySnapshotRef": "policy:memory-compaction@snapshot",
        "localFakeCompactionAllowed": True,
    }
    base.update(overrides)
    return MemoryCompactionPolicy.model_validate(base)


def _compact(harness: MemoryCompactionHarness, request, policy) -> dict[str, Any]:
    result = asyncio.run(harness.compact(request=request, policy=policy))
    return {
        "status": result.status,
        "reasonCodes": list(result.reason_codes),
        "executed": result.receipt.executed,
    }


def run_compaction_denial_matrix() -> list[dict[str, Any]]:
    on = MemoryCompactionHarness({"enabled": True, "localFakeAdapterEnabled": True})
    off = MemoryCompactionHarness({})
    rows = [
        ("disabled_harness", off, _request(), _policy()),
        ("missing_policy", on, _request(), None),
        ("missing_evidence", on, _request(evidenceRefs=()), _policy()),
        ("approval_required", on, _request(), _policy(approvalRequired=True)),
        ("missing_sources", on, _request(sourceRefs=()), _policy()),
        ("private_payload", on, _request(privatePayload=True), _policy()),
        ("child_isolated", on, _request(childMemoryIsolated=True), _policy()),
        ("success_local_fake", on, _request(), _policy()),
    ]
    return [
        {"scenario": name, **_compact(h, req, pol)} for name, h, req, pol in rows
    ]


def run_recall_gate_pair() -> list[dict[str, Any]]:
    from magi_agent.harness.memory_recall import MemoryRecallHarness

    async def _recall(harness: MemoryRecallHarness) -> dict[str, Any]:
        # Request/policy shapes copied from tests/test_memory_recall_recipe_harness.py
        # (Step 1) — keep the copied literals EXACT so the trace is comparable.
        result = await harness.recall(
            request={"query": "what did we decide", "scope": {"sessionId": "s1"}},
            namespace_policy=None,
            projection_policy=None,
        )
        return {"status": getattr(result, "status", None)}

    disabled = MemoryRecallHarness({})
    trace = [{"scenario": "recall_disabled", **asyncio.run(_recall(disabled))}]
    return trace


def run_review_trigger_table() -> list[dict[str, Any]]:
    rows = [
        ("disabled", 10, 10, False),
        ("zero_turns", 0, 10, True),
        ("on_boundary", 10, 10, True),
        ("off_boundary", 11, 10, True),
        ("interval_one_every_turn", 3, 1, True),
    ]
    return [
        {
            "scenario": name,
            "fires": should_run_review(turns, interval_turns=interval, enabled=enabled),
        }
        for name, turns, interval, enabled in rows
    ]
```

> If `harness.recall(...)` rejects the copied request shape (`RecallRequest` schema drift),
> replace the dict with the EXACT construction from `tests/test_memory_recall_recipe_harness.py`
> — the oracle records only `status`, so any valid request works.

- [ ] **Step 3: capture + regression** — clone the gate5b pair with import path
`tests.fixtures.memory_golden`, `SCENARIOS = {"compaction_matrix": …, "recall_gate": …,
"review_trigger": …}`, and expectations:

```python
def test_memory_goldens_are_non_trivial() -> None:
    import json

    matrix = {e["scenario"]: e for e in json.loads(golden_path("compaction_matrix").read_text())}
    assert matrix["disabled_harness"]["status"] == "disabled"
    assert matrix["approval_required"]["status"] == "approval_required"
    assert matrix["success_local_fake"]["status"] == "success"
    assert matrix["success_local_fake"]["executed"] is True
    denial_rows = [e for e in matrix.values() if e["status"] == "blocked"]
    assert len(denial_rows) == 5  # policy/evidence/sources/private/child
    recall = json.loads(golden_path("recall_gate").read_text())
    assert recall[0]["scenario"] == "recall_disabled"
    trigger = {e["scenario"]: e["fires"] for e in json.loads(golden_path("review_trigger").read_text())}
    assert trigger == {
        "disabled": False, "zero_turns": False, "on_boundary": True,
        "off_boundary": False, "interval_one_every_turn": True,
    }
```

- [ ] **Step 4: Capture pristine; verify twice; commit.**

```bash
MAGI_CONFIG="$(mktemp -d)/config.toml" uv run python -m tests.fixtures.memory_golden.capture --write
MAGI_CONFIG="$(mktemp -d)/config.toml" uv run pytest tests/fixtures/memory_golden/ -q
MAGI_CONFIG="$(mktemp -d)/config.toml" uv run pytest tests/fixtures/memory_golden/ -q
git add tests/fixtures/memory_golden/
git commit -m "test(memory): golden oracle (8-row compaction denial matrix + recall gate + review trigger)"
```

## Task C4.1: strategy seams on the three harnesses (dual-load)

**Files:**
- Modify: `magi_agent/harness/memory_compaction.py`
- Modify: `magi_agent/harness/memory_recall.py`
- Modify: `magi_agent/harness/memory_review.py`
- Test: `tests/test_memory_strategy_seams.py` (new)

- [ ] **Step 1: Re-grep the three call sites.**

```bash
grep -n "denial_status, denial_reasons = _compaction_denial_reasons" magi_agent/harness/memory_compaction.py
grep -n "def __init__" -A12 magi_agent/harness/memory_recall.py | head -16
grep -n "def should_run_review\|class MemoryReviewHarness" magi_agent/harness/memory_review.py
```

- [ ] **Step 2: Failing test.**

```python
# tests/test_memory_strategy_seams.py
from __future__ import annotations

import asyncio

from magi_agent.harness.memory_compaction import (
    MemoryCompactionHarness,
    MemoryCompactionPolicy,
    MemoryCompactionRequest,
)
from magi_agent.harness.memory_review import MemoryReviewConfig, MemoryReviewHarness


def _req():
    return MemoryCompactionRequest.model_validate(
        {"providerId": "p", "turnId": "t",
         "sourceRefs": ("evidence:s",), "evidenceRefs": ("evidence:e",)}
    )


def _pol():
    return MemoryCompactionPolicy.model_validate(
        {"policyRef": "policy:x", "policySnapshotRef": "policy:x@snap",
         "localFakeCompactionAllowed": True}
    )


def test_injected_denial_strategy_decides_compaction():
    def always_block(request, policy):
        return "blocked", ("custom_strategy_block",)

    harness = MemoryCompactionHarness(
        {"enabled": True, "localFakeAdapterEnabled": True},
        denial_strategy=always_block,
    )
    result = asyncio.run(harness.compact(request=_req(), policy=_pol()))
    assert result.status == "blocked"
    assert result.reason_codes == ("custom_strategy_block",)


def test_default_denial_strategy_unchanged():
    harness = MemoryCompactionHarness({"enabled": True, "localFakeAdapterEnabled": True})
    result = asyncio.run(harness.compact(request=_req(), policy=_pol()))
    assert result.status == "success"


def test_review_harness_trigger_strategy():
    calls = []

    def every_turn(turn_count, *, interval_turns, enabled):
        calls.append(turn_count)
        return enabled and turn_count > 0

    harness = MemoryReviewHarness(
        MemoryReviewConfig(enabled=True, intervalTurns=10), trigger=every_turn
    )
    assert harness.should_run(turn_count=3) is True
    assert calls == [3]


def test_recall_harness_default_projection_strategy_is_used_when_none():
    from magi_agent.harness.memory_recall import MemoryRecallHarness

    sentinel = object()
    captured = {}

    async def fake_exec(**kwargs):
        captured.update(kwargs)

        class _R:
            status = "disabled"

        return _R()

    harness = MemoryRecallHarness({}, default_projection_policy=sentinel)
    import magi_agent.harness.memory_recall as mr

    original = mr.execute_readonly_memory_recall
    mr.execute_readonly_memory_recall = fake_exec  # seam is module-level (Step 1)
    try:
        asyncio.run(
            harness.recall(request={"query": "q", "scope": {"sessionId": "s"}},
                           namespace_policy=None, projection_policy=None)
        )
    finally:
        mr.execute_readonly_memory_recall = original
    assert captured["projection_policy"] is sentinel
```

> NOTE the recall test monkey-patches the module attribute — Step 1 verified
> `MemoryRecallHarness.recall` calls `execute_readonly_memory_recall` imported at module top; if
> the implementing agent finds it imported INSIDE the method, patch at that import site instead.

- [ ] **Step 3: Run, see it FAIL.**

```bash
MAGI_CONFIG="$(mktemp -d)/config.toml" uv run pytest tests/test_memory_strategy_seams.py -q
```

- [ ] **Step 4: Implement (three small dual-load edits).**

(a) `memory_compaction.py` — type alias + constructor + call-site swap:

```python
#: C4 strategy seam: (request, policy) -> (denial_status | None, reason_codes).
#: First-party default = _compaction_denial_reasons (the exact legacy decision).
CompactionDenialStrategy = Callable[
    ["MemoryCompactionRequest", "MemoryCompactionPolicy | None"],
    "tuple[MemoryCompactionStatus | None, tuple[str, ...]]",
]
```

(`from collections.abc import Callable` — grep the import block first.) Constructor (shipped
signature quoted at the C4 table) gains `denial_strategy: "CompactionDenialStrategy | None" =
None` → `self._denial_strategy = denial_strategy`. The shipped line

```python
        denial_status, denial_reasons = _compaction_denial_reasons(safe_request, safe_policy)
```

becomes

```python
        denial = self._denial_strategy or _compaction_denial_reasons
        denial_status, denial_reasons = denial(safe_request, safe_policy)
```

(b) `memory_recall.py` — constructor gains `default_projection_policy: object | None = None` and
`default_namespace_policy: object | None = None`; the shipped `recall` body

```python
        return await execute_readonly_memory_recall(
            request=request,
            namespace_policy=namespace_policy,
            projection_policy=projection_policy,
```

becomes

```python
        return await execute_readonly_memory_recall(
            request=request,
            namespace_policy=(
                namespace_policy if namespace_policy is not None
                else self._default_namespace_policy
            ),
            projection_policy=(
                projection_policy if projection_policy is not None
                else self._default_projection_policy
            ),
```

(c) `memory_review.py` — constructor (shipped: `def __init__(self, config: MemoryReviewConfig)`)
gains `trigger: object | None = None` → `self._trigger = trigger or should_run_review`, plus the
consumption method:

```python
    def should_run(self, *, turn_count: int) -> bool:
        """N-turn trigger via the injected strategy (default: should_run_review)."""
        return bool(
            self._trigger(
                turn_count,
                interval_turns=self.config.interval_turns,
                enabled=self.config.enabled,
            )
        )
```

- [ ] **Step 5: Run, see it PASS — seam tests + memory legacy suites + oracle.**

```bash
MAGI_CONFIG="$(mktemp -d)/config.toml" uv run pytest \
  tests/test_memory_strategy_seams.py tests/test_memory_review.py \
  tests/test_memory_compaction_wiring_e2e.py tests/test_memory_recall_recipe_harness.py \
  tests/fixtures/memory_golden/ -q
```

- [ ] **Step 6: Commit.**

```bash
git add magi_agent/harness/memory_compaction.py magi_agent/harness/memory_recall.py \
        magi_agent/harness/memory_review.py tests/test_memory_strategy_seams.py
git commit -m "feat(memory): strategy seams on compaction/recall/review harnesses

C4 policy/mechanism split: denial decision, default recall policies, and the
N-turn review trigger are injectable (None -> exact legacy defaults). Stores,
receipts, and authority pins untouched. Oracle unchanged."
```

## Task C4.2: bundled `memory_strategies_default` pack + resolution + flip

**Files:**
- New: `magi_agent/firstparty/packs/memory_strategies_default/{__init__.py,pack.toml,impl.py}`
- Modify: `magi_agent/harness/memory_compaction.py` (+ resolution helper; flip)
- Test: `tests/firstparty/test_memory_strategies_default_pack.py` (new)

- [ ] **Step 1: Failing test.**

```python
# tests/firstparty/test_memory_strategies_default_pack.py
from __future__ import annotations


def test_bundled_memory_strategies_load_and_resolve():
    from magi_agent.harness.memory_compaction import (
        _compaction_denial_reasons,
        resolve_memory_strategy,
    )
    from magi_agent.harness.memory_review import should_run_review

    assert resolve_memory_strategy(
        "memory_strategy:compaction-denial@1", default=None
    ) is _compaction_denial_reasons
    assert resolve_memory_strategy(
        "memory_strategy:review-trigger@1", default=None
    ) is should_run_review
    # recall default policies are registered as named strategies too
    assert resolve_memory_strategy(
        "memory_strategy:recall-projection@1", default=None
    ) is not None
```

- [ ] **Step 2: Run, see it FAIL → implement.**

```bash
MAGI_CONFIG="$(mktemp -d)/config.toml" uv run pytest tests/firstparty/test_memory_strategies_default_pack.py -q
```

Expected: FAIL (`ImportError: cannot import name 'resolve_memory_strategy'`).

```toml
# magi_agent/firstparty/packs/memory_strategies_default/pack.toml
# First-party memory strategies (mirrors the evidence-ledger split: the memory
# STORES and receipt envelopes are kernel; the opinionated recall/compaction/
# review decisions load from this removable pack — §1 no privilege).

packId = "openmagi.memory-strategies-default"
displayName = "Memory strategies"
version = "1.0.0"
description = "First-party recall projection, compaction denial, and review trigger strategies."

[[provides]]
type = "memory_strategy"
ref = "memory_strategy:compaction-denial@1"
impl = "magi_agent.firstparty.packs.memory_strategies_default.impl:provide_compaction_denial"

[[provides]]
type = "memory_strategy"
ref = "memory_strategy:recall-projection@1"
impl = "magi_agent.firstparty.packs.memory_strategies_default.impl:provide_recall_projection"

[[provides]]
type = "memory_strategy"
ref = "memory_strategy:review-trigger@1"
impl = "magi_agent.firstparty.packs.memory_strategies_default.impl:provide_review_trigger"
```

```python
# magi_agent/firstparty/packs/memory_strategies_default/impl.py
"""First-party memory strategy providers (no privilege, typed-ctx only)."""
from __future__ import annotations

from magi_agent.packs.context import MemoryStrategyProvideContext


def provide_compaction_denial(context: MemoryStrategyProvideContext) -> None:
    from magi_agent.harness.memory_compaction import _compaction_denial_reasons

    context.register("memory_strategy:compaction-denial@1", _compaction_denial_reasons)


def provide_recall_projection(context: MemoryStrategyProvideContext) -> None:
    from magi_agent.recipes.first_party.memory_recall import (
        MemoryRecallProjectionPolicy,
    )

    # Default-constructed projection policy; grep the class for required fields
    # (if construction needs args, register the CLASS and let consumers build it
    # — adjust the C4.1 default threading accordingly and say so in the commit).
    context.register("memory_strategy:recall-projection@1", MemoryRecallProjectionPolicy())


def provide_review_trigger(context: MemoryStrategyProvideContext) -> None:
    from magi_agent.harness.memory_review import should_run_review

    context.register("memory_strategy:review-trigger@1", should_run_review)
```

Resolution helper in `memory_compaction.py` (the shared memory module all three harnesses
import-reach; lazy pack imports, fail-open to `default` — mirror `resolve_loop_policy`'s body
with registry slot `registries.memory_strategies`):

```python
def resolve_memory_strategy(ref: str, *, default: object, bases: "list[object] | None" = None) -> object:
    try:
        from magi_agent.packs.discovery import default_search_bases
        from magi_agent.packs.registries import load_into_registries

        search = list(bases) if bases is not None else list(default_search_bases())
        registries, _ = load_into_registries(search)
        strategy = registries.memory_strategies.resolve(ref)
        if strategy is not None:
            return strategy
    except Exception:  # noqa: BLE001 — fail-open to the in-module default
        pass
    return default
```

Flip the defaults at the three seams (each `or`-fallback from C4.1 becomes a
`resolve_memory_strategy(<ref>, default=<legacy>)` at construction time): in
`MemoryCompactionHarness.__init__` —
`self._denial_strategy = denial_strategy or resolve_memory_strategy("memory_strategy:compaction-denial@1", default=_compaction_denial_reasons)`;
in `MemoryReviewHarness.__init__` — same shape with the review-trigger ref and
`should_run_review`; `MemoryRecallHarness` keeps `None`-passthrough defaults (live recall callers
construct policies per request — registering the projection strategy makes it ADDRESSABLE for
override without forcing it on every call).

- [ ] **Step 3: Run, see it PASS + the FULL memory suites + oracle (the flip must be invisible).**

```bash
MAGI_CONFIG="$(mktemp -d)/config.toml" uv run pytest \
  tests/firstparty/test_memory_strategies_default_pack.py tests/test_memory_strategy_seams.py \
  tests/test_memory_review.py tests/test_memory_compaction_wiring_e2e.py \
  tests/test_memory_recall_recipe_harness.py tests/test_memory_compaction_tree.py \
  tests/fixtures/memory_golden/ -q
```

- [ ] **Step 4: Commit.**

```bash
git add magi_agent/firstparty/packs/memory_strategies_default/ \
        magi_agent/harness/memory_compaction.py magi_agent/harness/memory_review.py \
        tests/firstparty/test_memory_strategies_default_pack.py
git commit -m "feat(memory): bundle the 3 first-party strategies as memory_strategy pack + flip

C4: compaction-denial / recall-projection / review-trigger load from
openmagi.memory-strategies-default with fail-open in-module defaults.
Memory oracle + full memory suites unchanged."
```

---

# Task C5 (SERIAL, last): Pack-C §1 acceptance across all four subsystems

**Files:**
- Test: `tests/packs/test_pack_c_no_privilege.py` (new)

- [ ] **Step 1: Write the acceptance tests (the §1 add/override/remove triple per new surface).**

```python
# tests/packs/test_pack_c_no_privilege.py
"""§1 'no privilege' acceptance for the Pack-C surfaces: a user pack can ADD a
primitive of each new type, OVERRIDE a first-party ref, and REMOVE (disable) a
first-party pack — through the identical loader path. Mirrors the Phase-7
acceptance shape for the original 8 types."""
from __future__ import annotations

from pathlib import Path

from magi_agent.packs.discovery import default_search_bases
from magi_agent.packs.registries import load_into_registries


def _user_pack(tmp_path: Path, body: str) -> list:
    pack = tmp_path / "user-pack"
    pack.mkdir()
    (pack / "pack.toml").write_text(body)
    return list(default_search_bases()) + [tmp_path]


def test_user_pack_adds_each_pack_c_type(tmp_path: Path):
    bases = _user_pack(
        tmp_path,
        'packId = "user.add"\ndisplayName = "add"\n\n'
        "[[provides]]\n"
        'type = "loop_policy"\nref = "loop_policy:mine@1"\n'
        'impl = "tests.packs.pack_c_fixture_impls:provide_loop_policy_mine"\n\n'
        "[[provides]]\n"
        'type = "schedule_policy"\nref = "schedule_policy:mine@1"\n'
        'impl = "tests.packs.pack_c_fixture_impls:provide_schedule_policy_mine"\n\n'
        "[[provides]]\n"
        'type = "memory_strategy"\nref = "memory_strategy:mine@1"\n'
        'impl = "tests.packs.pack_c_fixture_impls:provide_memory_strategy_mine"\n',
    )
    registries, _ = load_into_registries(bases)
    assert registries.loop_policies.resolve("loop_policy:mine@1") is not None
    assert registries.schedule_policies.resolve("schedule_policy:mine@1") is not None
    assert registries.memory_strategies.resolve("memory_strategy:mine@1") is not None


def test_user_pack_overrides_firstparty_workspace_handler(tmp_path: Path):
    bases = _user_pack(
        tmp_path,
        'packId = "user.override"\ndisplayName = "override"\n\n'
        "[[provides]]\n"
        'type = "tool"\nref = "workspace:Clock@1"\n'
        'impl = "tests.packs.pack_c_fixture_impls:provide_clock_override"\n',
    )
    registries, _ = load_into_registries(bases)
    handler = registries.workspace_tool_handlers.resolve("Clock")
    assert handler({}, None) == {"nowMs": -1, "overridden": True}


def test_disabling_firstparty_pack_removes_its_policies(tmp_path: Path, monkeypatch):
    """REMOVE: config.toml [packs] disable — the shipped removal convention."""
    config_dir = tmp_path / "magi"
    config_dir.mkdir()
    (config_dir / "config.toml").write_text(
        '[packs]\ndisable = ["openmagi.goal-loop-default"]\n'
    )
    monkeypatch.setenv("MAGI_CONFIG", str(config_dir / "config.toml"))
    registries, _ = load_into_registries(list(default_search_bases()))
    assert registries.loop_policies.resolve("loop_policy:ralph@1") is None
```

Append to `tests/packs/pack_c_fixture_impls.py`:

```python
def provide_loop_policy_mine(context: Any) -> None:
    context.register("loop_policy:mine@1", lambda loop_input: loop_input)


def provide_schedule_policy_mine(context: Any) -> None:
    context.register("schedule_policy:mine@1", object())


def provide_memory_strategy_mine(context: Any) -> None:
    context.register("memory_strategy:mine@1", object())


def provide_clock_override(context: Any) -> None:
    if context.register_workspace_handler is not None:
        context.register_workspace_handler(
            "Clock", lambda args, view: {"nowMs": -1, "overridden": True}
        )
```

> The disable test assumes `load_packs_config()` honors `MAGI_CONFIG` — grep
> `magi_agent/packs/discovery.py` for how `[packs]` config is located (the Phase-3 convention);
> if it reads a different path, construct the config the way the existing
> `resolve_enabled_packs` tests do (`grep -rn "disable" tests/packs/ | head`).

- [ ] **Step 2: Run the acceptance + the kernel-minimal grep gates.**

```bash
MAGI_CONFIG="$(mktemp -d)/config.toml" uv run pytest tests/packs/test_pack_c_no_privilege.py -q
# No legacy tool branches remain in gate5b:
grep -n "if tool_name == \"FileEdit\"\|if tool_name == \"Bash\"\|def _enforce_memory_mode" \
  magi_agent/gates/gate5b_full_toolhost.py && echo "FAIL: legacy branches remain" || echo "OK gate5b minimal"
# No direct policy hardcodes remain at the seams:
grep -n "result = decide_loop_continuation(loop_input)" magi_agent/harness/goal_loop_control.py \
  && echo "FAIL" || echo "OK goal-loop seam"
grep -n "job.compute_next_run(now=now)" magi_agent/harness/scheduler_executor.py \
  | grep -v "CronSchedulePolicy\|next_run_after_fire" && echo "FAIL" || echo "OK scheduler seam"
```

- [ ] **Step 3: Full-suite + all five oracles (the Pack-C exit gate).**

```bash
MAGI_CONFIG="$(mktemp -d)/config.toml" uv run pytest \
  tests/fixtures/neutral_runtime_golden/ tests/fixtures/gate5b_golden/ \
  tests/fixtures/goal_loop_golden/ tests/fixtures/scheduler_golden/ \
  tests/fixtures/memory_golden/ -q
MAGI_CONFIG="$(mktemp -d)/config.toml" uv run pytest -q
```

Expected: all five oracles no-diff; full suite green (the repo-known import-boundary 2-test
flake on a contaminated `~/.magi/config.toml` is excluded by the `MAGI_CONFIG` prefix).

- [ ] **Step 4: Commit.**

```bash
git add tests/packs/test_pack_c_no_privilege.py tests/packs/pack_c_fixture_impls.py
git commit -m "test(packs): Pack-C §1 no-privilege acceptance (add/override/remove x 4 subsystems)

Kernel-minimal proven: gates/goal-loop/scheduler/memory policies all load from
removable packs; user packs add/override/disable through the identical path.
All 5 golden oracles byte-identical across the decomposition."
```

---

## Acceptance criteria (Pack C done)

- [ ] **Oracles:** `gate5b_golden` (2 scenarios), `goal_loop_golden` (11-branch matrix),
  `scheduler_golden` (3 scenarios), `memory_golden` (3 scenarios) committed from the PRISTINE
  pre-decomposition tree and byte-identical at HEAD; Phase-0 `neutral_runtime_golden` unchanged
  throughout.
- [ ] **C1:** all 11 legacy gate5b tool impls live in `openmagi.workspace-tools-default`
  (handlers over `WorkspaceHostView` only); memory-mode + permission-preflight live in
  `openmagi.gates-policy-default` (`phase="tool_host"`, `gatePosition="before"` ctx-callables);
  the legacy `_handle` branches and `_enforce_memory_mode`/`_filter_memory_mode_output`/
  `_preflight_legacy_tool` are DELETED; `build_control_plane_from_packs` skips `tool_host`
  entries; dispatch envelope/counter/receipts/path-safety/read-ledger store byte-identical.
- [ ] **C2:** `build_after_turn_goal_loop_hook` consumes `loop_policy:ralph@1` via
  `resolve_loop_policy` (fail-open); `openmagi.goal-loop-default` is the only registrar of the
  first-party policy; user override proven end-to-end.
- [ ] **C3:** `tick`/`execute_due_jobs` consume `SchedulePolicy`
  (`schedule_policy:cron@1` from `openmagi.scheduler-default`); lock/lease/at-most-once
  mechanism untouched; module forbidden-import contract still holds (lazy pack imports).
- [ ] **C4:** compaction-denial / review-trigger resolve from
  `openmagi.memory-strategies-default` with in-module fail-open defaults; recall default
  policies addressable as a named strategy; authority pins (`Literal[False]`) untouched.
- [ ] **§1 across all four:** `tests/packs/test_pack_c_no_privilege.py` green — add/override/
  disable per new surface through the identical loader.
- [ ] Full suite green, headless, no API keys (`MAGI_CONFIG` isolated everywhere).
- [ ] The irreducible kernel matches 08-ROADMAP §C acceptance: `{loader, registries,
  typed-context dispatcher, ADK loop}` + bare stores/dispatch (evidence store, hook bus, memory
  store, session service, event sink, gate5b dispatch envelope, scheduler lock/lease, goal-state
  store).

## Rollback

Every task is additive-then-flip and independently revertible by commit:

- **Before each subsystem's flip task** (C1.7 / C2.2 / C3.2 / C4.2): the kernel still runs the
  legacy in-module defaults whenever the new kwargs are `None`/empty — reverting any single
  decomposition commit restores prior behavior with no other change.
- **After a flip:** `git revert` the flip commit alone (it only swaps the default resolution +
  deletes dead code; the pack files and seams keep working for explicit callers).
- Whole-pack rollback: revert C5 → C4.2…C0 in reverse order, or
  `git checkout <pre-pack-c> -- magi_agent/gates/gate5b_full_toolhost.py magi_agent/harness/goal_loop_control.py magi_agent/harness/scheduler_executor.py magi_agent/harness/scheduler_job_execution.py magi_agent/harness/memory_recall.py magi_agent/harness/memory_compaction.py magi_agent/harness/memory_review.py magi_agent/packs/manifest.py magi_agent/packs/context.py magi_agent/packs/registries.py`
  and delete `magi_agent/firstparty/packs/{workspace_tools_default,gates_policy_default,goal_loop_default,scheduler_default,memory_strategies_default}/`
  + the new `tests/fixtures/*_golden/` dirs + the new test files. The four subsystem oracles are
  independent of the decomposition and may be kept regardless (they are pure regression value).
- No production default-ON flips anywhere in Pack C: every subsystem's env gates
  (`MAGI_GOAL_LOOP_ENABLED`, `MAGI_SCHEDULER_EXECUTOR_ENABLED`, memory harness `enabled=False`,
  gate5b `enabled`/`kill_switch`) keep their shipped default-OFF semantics — Pack C changes WHERE
  policy code lives, never WHETHER it runs.

## Hand-off

- **To Pack B (docs/scaffolding):** `magi pack new loop_policy|schedule_policy|memory_strategy`
  templates should generate against the C0 provide contexts; the B2 typed-context API reference
  gains `WorkspaceHostView` (the gate5b capability ceiling) and the three `register(ref, value)`
  contexts. The C1.3/C1.4 impls are the canonical "first pack" examples for tool authors.
- **To the final §1 acceptance / pre-merge report:** C5's no-privilege tests + the five oracles
  are the evidence bundle; cite the C1.7 flip commit (gate5b golden byte-identical across
  fully-legacy → fully-pack-loaded) as the headline proof that decomposition preserved behavior.
- **Deferred (explicitly out of Pack C):** per-tool `ToolManifest` re-declaration for the 11
  workspace handlers (they remain allowlist-driven via `Gate5BFullToolHostConfig.allowed_tool_names`
  — unifying that with `ToolRegistry` manifests is a follow-up); a live goal-loop DRIVER that acts
  on `continue` decisions (Track-A obligation, unchanged); scheduler persistent job sources
  (`_validate_local_fake_source` stays); hosted capability-set restriction on the new contexts
  (D5 reserved seam, no signature change needed).





