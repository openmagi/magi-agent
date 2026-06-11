# Phase 5 — Control-Plane Migration (6 LoopControls / 4 internal seams)

> **For agentic workers:** REQUIRED SUB-SKILL: `superpowers:subagent-driven-development` or
> `superpowers:executing-plans`. Read `00-BLUEPRINT.md` first (§1 "no privilege" spec, D1–D7, the
> file-structure map, conventions). This phase **depends on** Phase 0's golden oracle
> (`01-phase0-golden-oracle.md`) and Phase 2's typed-context ABI (`magi_agent/packs/context.py`,
> `magi_agent/packs/registries.py`). Every task that touches one of the 6 controls **MUST** keep
> the Phase-0 golden regression green.

**Goal:** De-privilege the control plane. Migrate the 6 first-party `LoopControl`s so each receives
**only** a narrow Phase-2 typed context (`ControlPlaneContext` + per-seam capabilities) — giving a
user pack the **same** handle first-party gets (the §1 "no privilege" keystone for `control_plane`).
No control may reach into a god-object, a privileged receipt-store handle, or per-invocation mutable
`self.*` state that a user pack cannot replicate.

**Architecture:** Add capability fields to the Phase-2 `ControlPlaneContext` (shared serial task,
S-0), then rewrite each control to read those fields instead of constructor-injected privileged
objects. Mutable per-invocation state (`_attempts`, `_detectors`, `_recovery_state`) moves into a
runtime-owned `PerInvocationState` struct carried on the context, with a clear-on-turn-complete
hook and the existing LRU bound preserved. `ForkRunner` is exposed on the context as a public
capability (full-trust decision per 00-BLUEPRINT §0). Compaction's `ContextLifecycleBoundary` +
`WorkspaceSessionService` are narrowed behind a single context capability. `gates/` (gate5b 89KB)
is **out of scope**.

**Tech stack:** Python 3.12+, `uv`, pydantic v2 (frozen, `extra="forbid"`), Google ADK, pytest.
Touch points: `magi_agent/adk_bridge/control_plane.py` (the 6 controls + `build_default_plane`),
`magi_agent/packs/context.py` (Phase-2, extended here), `magi_agent/packs/registries.py`.

**Dispatch shape (drive with `/workflows`):** S-0 is serial (one agent, defines the surface). Then
S-A / S-B / S-C / S-D are **independent** and parallelizable, one agent each. `MaxStepsBrakeControl`
(Task 5.1) is trivial (`LlmRequest`-only) — **do it first as the template** before the parallel
fan-out. Barrier after each seam = the golden-oracle diff.

---

## Conventions recap (do not skip)

- **TDD, bite-sized:** failing test → run (FAIL) → minimal impl → run (PASS) → commit. One logical
  change per commit. Conventional-commit (`refactor(control-plane): …`, `feat(packs): …`,
  `test(control-plane): …`).
- **No API keys, isolated config** — prefix EVERY pytest with
  `MAGI_CONFIG="$(mktemp -d)/config.toml"` (avoids `~/.magi/config.toml` contamination). Use
  `LOCAL_DEV_MODEL_SENTINEL="local-dev"` for any runtime run.
- **Re-grep first:** every modify-task's Step 1 is a `grep`/read to locate the current target —
  `:NNN` refs below are HEAD-802e707b snapshots and **may have drifted**.
- **Golden gate (load-bearing):** for any change to the 6 controls, a verify step runs
  `tests/fixtures/neutral_runtime_golden/test_golden_regression.py`. **A diff = a behavior change
  to review.** Regenerate via `python -m tests.fixtures.neutral_runtime_golden.capture --write`
  **only** when the change is intentional + correct, and call out the diff in the commit body.
- **Reversibility / dual-load:** the typed context is read with backward-compatible fallbacks so
  pre-migration call sites keep working until Phase 6 flips the default. Each task is independently
  revertible by commit.

---

## Task 5.0 (S-0, SERIAL): Extend the Phase-2 `ControlPlaneContext` with the four seam surfaces

**Files:**
- Modify: `magi_agent/packs/context.py`
- Test: `tests/packs/test_control_plane_context_surface.py`

This is the shared barrier task. It adds the **public** capability fields each of S-A…S-D needs,
all on the existing Phase-2 `ControlPlaneContext`, so first-party and third-party `control_plane`
impls receive the identical object. **No control is migrated here** — only the surface is widened.

- [ ] **Step 1: Re-grep the Phase-2 context to confirm the current shape.**

```bash
cd /Users/kevin/Desktop/claude_code/magi-agent/.worktrees/neutral-runtime
grep -n "class ControlPlaneContext\|class ToolCallView\|class BeforeModelView\|class AfterAgentView\|model_config" magi_agent/packs/context.py
```
Expected: `ControlPlaneContext` (frozen pydantic, `extra="forbid"`) exists from Phase 2 with at
least the ADK callback-arg views (`callback_context`, `llm_request`, `tool`, `args`,
`tool_context`, `result`, `agent`, `session_id`, `turn_id`). If the field names differ, use the
**actual** names in the code below.

- [ ] **Step 2: Write the failing surface test.**

```python
# tests/packs/test_control_plane_context_surface.py
from __future__ import annotations

from magi_agent.packs.context import (
    ControlPlaneContext,
    EvidenceLedgerView,
    PerInvocationState,
    TurnSnapshot,
)


def test_context_exposes_evidence_ledger_view_for_s_a():
    """S-A: a control reads the evidence ledger + open controls off the context,
    NOT a privileged receipt-store object."""
    view = EvidenceLedgerView(
        ledger=None,
        open_controls=(),
        contract_required=None,
        agent_role="general",
    )
    ctx = ControlPlaneContext.minimal(evidence=view)
    assert ctx.evidence is view
    assert ctx.evidence.agent_role == "general"


def test_context_exposes_turn_snapshot_and_fork_runner_for_s_b():
    """S-B: a pre-extracted typed snapshot + a public ForkRunner capability."""
    snap = TurnSnapshot(
        session_id="s1",
        turn_id="t1",
        system_prompt_blocks=({"type": "text", "text": "you are"},),
        parent_assistant_message={"role": "assistant", "content": []},
    )
    ctx = ControlPlaneContext.minimal(turn_snapshot=snap, fork_runner=object())
    assert ctx.turn_snapshot.turn_id == "t1"
    assert ctx.fork_runner is not None


def test_context_exposes_per_invocation_state_for_s_c():
    """S-C: runtime-owned mutable per-invocation state with a clear hook + LRU bound."""
    state = PerInvocationState(max_scopes=2)
    state.set_scoped("inv-1", "FileEdit", 3)
    assert state.get_scoped("inv-1", "FileEdit", default=0) == 3
    state.clear_invocation("inv-1")
    assert state.get_scoped("inv-1", "FileEdit", default=0) == 0


def test_per_invocation_state_is_lru_bounded():
    state = PerInvocationState(max_scopes=2)
    state.set_scoped("a", "k", 1)
    state.set_scoped("b", "k", 1)
    state.set_scoped("c", "k", 1)  # evicts oldest ("a")
    assert state.get_scoped("a", "k", default=0) == 0
    assert state.get_scoped("c", "k", default=0) == 1


def test_context_exposes_compaction_capability_for_s_d():
    """S-D: compaction decision narrowed behind one callable capability."""
    sentinel = object()
    ctx = ControlPlaneContext.minimal(compaction=sentinel)
    assert ctx.compaction is sentinel
```

- [ ] **Step 3: Run, see it FAIL.**

```bash
MAGI_CONFIG="$(mktemp -d)/config.toml" uv run pytest tests/packs/test_control_plane_context_surface.py -q
```
Expected: FAIL (`ImportError`: `EvidenceLedgerView` / `PerInvocationState` / `TurnSnapshot` not
defined, and `ControlPlaneContext` has no `evidence`/`turn_snapshot`/`fork_runner`/`compaction`).

- [ ] **Step 4: Implement the surface additions in `magi_agent/packs/context.py`.**

Add these (after the existing Phase-2 dataclasses; keep all `extra="forbid"` / frozen conventions).
`EvidenceLedgerView` and `TurnSnapshot` are **frozen** read-only views; `PerInvocationState` is the
deliberate mutable, runtime-owned struct (the ONE place mutation is allowed, and it is owned by the
runtime, not by any control's `self`).

```python
# --- Phase 5 seam capabilities (added to the Phase-2 ControlPlaneContext) ----

from __future__ import annotations  # (already present at top — do not duplicate)

import dataclasses
from typing import Any


@dataclasses.dataclass(frozen=True)
class EvidenceLedgerView:
    """S-A read-only view: the per-turn evidence ledger + open controls.

    Surfaces exactly the two reads ``GaConstraintReinjectionControl`` needs
    (``ledger_for_turn`` / ``open_controls_for_turn`` already resolved for the
    active turn) WITHOUT handing the control the mutable receipt-store object.
    A user pack receives the same view, so it can author an equivalent reminder
    control with zero privileged access.
    """

    ledger: Any  # EvidenceLedger | None (already resolved for this turn)
    open_controls: tuple[Any, ...]  # GeneralAutomationControlProjection, resolved
    contract_required: Any  # RequiredDeliverableEvidence | None
    agent_role: str = "general"


@dataclasses.dataclass(frozen=True)
class TurnSnapshot:
    """S-B pre-extracted typed snapshot of the just-finished turn.

    The runtime extracts this from the ADK session/event tree ONCE and places it
    on the context, so a control never has to traverse ``session.events`` itself
    (the privileged nested traversal). Mirrors ``_SelfReviewTurnSnapshot``.
    """

    session_id: str
    turn_id: str
    system_prompt_blocks: tuple[dict[str, Any], ...]
    parent_assistant_message: dict[str, Any]


class PerInvocationState:
    """S-C runtime-owned mutable per-invocation state with LRU bound + clear hook.

    The ONLY mutable struct in the control-plane context. It replaces each
    control's private ``self._attempts`` / ``self._detectors`` /
    ``self._recovery_state`` so per-invocation state lives in the runtime, not in
    a control instance a user pack cannot reach. Bounded (LRU-ish: dict insertion
    order, evict oldest) so it never leaks across turns whose clear hook never
    fires (e.g. a turn that raised). ``clear_invocation`` is the
    clear-on-turn-complete hook the dispatcher calls from ``after_run``.
    """

    def __init__(self, *, max_scopes: int = 256) -> None:
        self._max_scopes = max_scopes
        # keyed (invocation_id, name) -> value
        self._store: dict[tuple[str, str], Any] = {}
        # opaque per-invocation objects (e.g. a loop detector) keyed by invocation_id
        self._objects: dict[str, dict[str, Any]] = {}

    # -- scalar scoped counters (edit-retry attempts, recovery attempt counts) --
    def get_scoped(self, invocation_id: str, name: str, *, default: Any = None) -> Any:
        return self._store.get((invocation_id, name), default)

    def set_scoped(self, invocation_id: str, name: str, value: Any) -> None:
        self._store[(invocation_id, name)] = value
        self._bound()

    def pop_scoped(self, invocation_id: str, name: str) -> None:
        self._store.pop((invocation_id, name), None)

    # -- per-invocation opaque objects (loop detectors / recovery state objs) ----
    def get_object(self, invocation_id: str, name: str, factory) -> Any:
        bucket = self._objects.setdefault(invocation_id, {})
        if name not in bucket:
            bucket[name] = factory()
            self._bound()
        return bucket[name]

    def peek_object(self, invocation_id: str, name: str, *, default: Any = None) -> Any:
        return self._objects.get(invocation_id, {}).get(name, default)

    def set_object(self, invocation_id: str, name: str, value: Any) -> None:
        self._objects.setdefault(invocation_id, {})[name] = value
        self._bound()

    # -- clear-on-turn-complete hook (called by the dispatcher's after_run) ------
    def clear_invocation(self, invocation_id: str) -> None:
        self._store = {
            k: v for k, v in self._store.items() if k[0] != invocation_id
        }
        self._objects.pop(invocation_id, None)

    def _bound(self) -> None:
        # Bound the scalar store by distinct invocation id (oldest-first eviction).
        while self._distinct_scalar_invocations() > self._max_scopes:
            oldest = next(iter(self._store))[0]
            self._store = {k: v for k, v in self._store.items() if k[0] != oldest}
        while len(self._objects) > self._max_scopes:
            self._objects.pop(next(iter(self._objects)), None)

    def _distinct_scalar_invocations(self) -> int:
        return len({k[0] for k in self._store})
```

Now extend `ControlPlaneContext` itself. Add the four optional fields and a `minimal(...)`
constructor used by tests + by controls in isolation (the real dispatcher fills them in S-A…S-D
integration). Locate the existing `class ControlPlaneContext` (Step 1) and add:

```python
# inside ControlPlaneContext (a frozen pydantic model from Phase 2): add fields

    evidence: EvidenceLedgerView | None = None
    turn_snapshot: TurnSnapshot | None = None
    fork_runner: Any | None = None        # public ForkRunner capability (full-trust)
    per_invocation: PerInvocationState | None = None
    compaction: Any | None = None         # narrowed compaction-decision capability

    @classmethod
    def minimal(cls, **overrides: Any) -> "ControlPlaneContext":
        """Build a context with only the supplied seam fields populated.

        Used by control unit tests and by per-control isolation. The live
        dispatcher (S-A…S-D integration) builds the full context from ADK args.
        """
        base: dict[str, Any] = {
            "evidence": None,
            "turn_snapshot": None,
            "fork_runner": None,
            "per_invocation": None,
            "compaction": None,
        }
        base.update(overrides)
        return cls.model_construct(**base)
```

> NOTE if `ControlPlaneContext` is a **frozen dataclass** (not pydantic) in the Phase-2 impl: use
> `dataclasses.replace`-friendly `field(default=None)` for the four fields and a plain classmethod
> returning `cls(**base)` instead of `model_construct`. Match whichever the Phase-2 code actually
> used (Step 1 told you).

- [ ] **Step 5: Run, see it PASS.**

```bash
MAGI_CONFIG="$(mktemp -d)/config.toml" uv run pytest tests/packs/test_control_plane_context_surface.py -q
```
Expected: PASS (5 tests).

- [ ] **Step 6: Golden gate (no control touched yet — must still be green).**

```bash
MAGI_CONFIG="$(mktemp -d)/config.toml" uv run pytest tests/fixtures/neutral_runtime_golden/test_golden_regression.py -q
```
Expected: PASS, **no diff** (this task only adds context fields; no control reads them yet).

- [ ] **Step 7: Commit.**

```bash
git add magi_agent/packs/context.py tests/packs/test_control_plane_context_surface.py
git commit -m "feat(packs): add S-A…S-D capability surfaces to ControlPlaneContext

Adds EvidenceLedgerView (S-A), TurnSnapshot + public ForkRunner (S-B),
PerInvocationState runtime-owned mutable state with LRU bound + clear hook
(S-C), and a narrowed compaction capability (S-D). No control migrated yet;
golden regression unchanged."
```

**Barrier:** S-A, S-B, S-C, S-D may now run in parallel. Task 5.1 (MaxStepsBrake) is the warm-up
template — do it first.

---

## Task 5.1 (TEMPLATE, do first): Migrate `MaxStepsBrakeControl` to the typed context

`MaxStepsBrakeControl` reads **only** `llm_request` (and `callback_context`, unused). It is the
trivial case and the pattern for all others: the control's hook keeps the same ADK signature, but
its body reads from a `ControlPlaneContext` the dispatcher builds.

**Files:**
- Modify: `magi_agent/adk_bridge/control_plane.py`
- Test: `tests/adk_bridge/test_max_steps_brake_control.py` (extend; do not delete existing cases)

- [ ] **Step 1: Re-grep the current control.**

```bash
grep -n "class MaxStepsBrakeControl\|async def on_before_model\|max_iterations\|MAX_STEPS_WRAP_UP_MESSAGE" \
  magi_agent/adk_bridge/control_plane.py
```
Confirm the current `on_before_model(self, *, callback_context, llm_request)` body (snapshot
`~:452-494`) injects `MAX_STEPS_WRAP_UP_MESSAGE` and clears `llm_request.config.tools`.

- [ ] **Step 2: Write the failing test — the control accepts a typed context.**

```python
# tests/adk_bridge/test_max_steps_brake_control.py  (append)
import asyncio

from magi_agent.adk_bridge.control_plane import MaxStepsBrakeControl
from magi_agent.packs.context import ControlPlaneContext


class _FakeConfig:
    def __init__(self):
        self.tools = [{"type": "function", "name": "Read"}]


class _FakeReq:
    def __init__(self):
        self.contents = [{"role": "user", "content": "go"}]
        self.config = _FakeConfig()


def test_max_steps_brake_reads_request_from_typed_context():
    ctrl = MaxStepsBrakeControl(max_iterations=2, iteration=1)  # final iteration
    req = _FakeReq()
    ctx = ControlPlaneContext.minimal()  # llm_request supplied via apply()
    asyncio.run(ctrl.apply_before_model(ctx, llm_request=req))
    # wrap-up injected + tools cleared (capability parity: same effect, typed input)
    assert any(
        isinstance(c, dict) and "content" in c for c in req.contents
    )
    assert req.config.tools == []
```

- [ ] **Step 3: Run, see it FAIL.**

```bash
MAGI_CONFIG="$(mktemp -d)/config.toml" uv run pytest \
  tests/adk_bridge/test_max_steps_brake_control.py::test_max_steps_brake_reads_request_from_typed_context -q
```
Expected: FAIL (`MaxStepsBrakeControl` has no `apply_before_model`).

- [ ] **Step 4: Implement — add a context-taking method; keep the ADK hook delegating to it.**

In `control_plane.py`, inside `MaxStepsBrakeControl`, factor the body into a context-taking method
and make `on_before_model` a thin delegate (preserves the existing fan-out + golden behavior):

```python
    async def on_before_model(
        self,
        *,
        callback_context: Any,
        llm_request: Any,
    ) -> None:
        from magi_agent.packs.context import ControlPlaneContext

        ctx = ControlPlaneContext.minimal()
        return await self.apply_before_model(ctx, llm_request=llm_request)

    async def apply_before_model(
        self,
        ctx: Any,
        *,
        llm_request: Any,
    ) -> None:
        """Typed-context entry point. ``ctx`` is a ControlPlaneContext; this
        control needs only the outgoing request (the wrap-up brake)."""
        from magi_agent.runtime.turn_policy import MAX_STEPS_WRAP_UP_MESSAGE

        if self.max_iterations <= 0:
            return None
        if self.iteration < self.max_iterations - 1:
            return None

        contents = getattr(llm_request, "contents", None)
        if isinstance(contents, list):
            try:
                from google.genai import types as _genai_types

                contents.append(
                    _genai_types.Content(
                        role="user",
                        parts=[_genai_types.Part(text=MAX_STEPS_WRAP_UP_MESSAGE)],
                    )
                )
            except Exception:
                contents.append(
                    {"role": "user", "content": MAX_STEPS_WRAP_UP_MESSAGE}
                )
        elif isinstance(llm_request, dict):
            llm_request.setdefault("contents", [])
            llm_request["contents"].append(
                {"role": "user", "content": MAX_STEPS_WRAP_UP_MESSAGE}
            )

        config = getattr(llm_request, "config", None)
        if config is not None:
            tools = getattr(config, "tools", None)
            if tools is not None:
                try:
                    config.tools = []
                except Exception:
                    pass
        return None
```

The old `on_before_model` body is now `apply_before_model`; `on_before_model` builds a `minimal`
context and delegates. Behavior is byte-identical.

- [ ] **Step 5: Run, see it PASS** (the new test AND the pre-existing cases):

```bash
MAGI_CONFIG="$(mktemp -d)/config.toml" uv run pytest tests/adk_bridge/test_max_steps_brake_control.py -q
```
Expected: PASS (all, including the original brake tests).

- [ ] **Step 6: Golden gate.**

```bash
MAGI_CONFIG="$(mktemp -d)/config.toml" uv run pytest tests/fixtures/neutral_runtime_golden/test_golden_regression.py -q
```
Expected: PASS, no diff. (MaxStepsBrake is not one of the 4 golden scenarios, but run anyway — it
shares `control_plane.py`.) If a diff appears, it is unintended — revert and investigate; do NOT
`capture --write`.

- [ ] **Step 7: Commit.**

```bash
git add magi_agent/adk_bridge/control_plane.py tests/adk_bridge/test_max_steps_brake_control.py
git commit -m "refactor(control-plane): MaxStepsBrakeControl reads typed context

apply_before_model takes a ControlPlaneContext; on_before_model delegates.
Template for the four seam migrations. Behavior byte-identical; golden green."
```

---

## S-A (PARALLEL): `GaConstraintReinjectionControl` — surface the ledger, not the receipt store

**Seam:** evidence ledger / receipt store. Today the control holds `self._receipts` (the mutable
`GeneralAutomationReceiptLedgerStore`) and calls `ledger_for_turn` / `open_controls_for_turn`
**itself** (`control_plane.py:~555-561`). That is the privileged handle. Migrate so the control
reads an already-resolved `EvidenceLedgerView` off the context (S-0). The runtime does the store
lookup; the control gets the read-only result — capability parity for user packs.

### Task S-A.1: `GaConstraintReinjectionControl.apply_before_model(ctx)` reads `ctx.evidence`

**Files:**
- Modify: `magi_agent/adk_bridge/control_plane.py`
- Test: `tests/adk_bridge/test_ga_constraint_context.py` (new)

- [ ] **Step 1: Re-grep current control.**

```bash
grep -n "class GaConstraintReinjectionControl\|self._receipts\|ledger_for_turn\|open_controls_for_turn\|ga_constraint_reinjection" \
  magi_agent/adk_bridge/control_plane.py
```
Confirm the current `on_before_model` (snapshot `~:537-592`): resolves `session_id`/`turn_id` from
`callback_context`, calls `self._receipts.ledger_for_turn(...)` + `.open_controls_for_turn(...)`,
then `ga_constraint_reinjection(contract_required=..., ledger=..., open_controls=..., agent_role=..., env=...)`,
and appends the reminder to `llm_request.contents`.

- [ ] **Step 2: Write the failing test — control reads the resolved view, no store handle.**

```python
# tests/adk_bridge/test_ga_constraint_context.py
from __future__ import annotations

import asyncio

from magi_agent.adk_bridge.control_plane import GaConstraintReinjectionControl
from magi_agent.packs.context import ControlPlaneContext, EvidenceLedgerView
from magi_agent.harness.general_automation.task_completion import (
    RequiredDeliverableEvidence,
)
from magi_agent.evidence.ledger import EvidenceLedger

_FLAG_ON = {"MAGI_GA_LIVE_ENABLED": "1"}


class _Req:
    def __init__(self):
        self.contents = [{"role": "user", "content": "go"}]


def _ledger():
    return EvidenceLedger(
        ledgerId="ledger-s1-t1",
        sessionId="s1",
        turnId="t1",
        runOn="main",
        agentRole="general",
        spawnDepth=0,
        sourceKind="tool_trace",
        producerSurface="tool_host",
    )


def test_ga_control_appends_reminder_from_context_view_no_store():
    # The control is constructed WITHOUT a receipts store; it reads the resolved view.
    ctrl = GaConstraintReinjectionControl(env=_FLAG_ON)
    view = EvidenceLedgerView(
        ledger=_ledger(),  # ledger lacks the owed artifact ref
        open_controls=(),
        contract_required=RequiredDeliverableEvidence(requires_artifact_ref=True),
        agent_role="general",
    )
    req = _Req()
    ctx = ControlPlaneContext.minimal(evidence=view)
    asyncio.run(ctrl.apply_before_model(ctx, llm_request=req))
    # reminder appended (artifactRef is the owed label)
    assert any("artifactRef" in str(c.get("content", "")) for c in req.contents)


def test_ga_control_no_view_is_noop():
    ctrl = GaConstraintReinjectionControl(env=_FLAG_ON)
    req = _Req()
    ctx = ControlPlaneContext.minimal(evidence=None)
    asyncio.run(ctrl.apply_before_model(ctx, llm_request=req))
    assert req.contents == [{"role": "user", "content": "go"}]
```

- [ ] **Step 3: Run, see it FAIL.**

```bash
MAGI_CONFIG="$(mktemp -d)/config.toml" uv run pytest tests/adk_bridge/test_ga_constraint_context.py -q
```
Expected: FAIL (no `apply_before_model`; constructor still requires `receipts`/`contract_required`).

- [ ] **Step 4: Implement — add `apply_before_model(ctx)`; relax the constructor.**

Make `receipts`/`contract_required` optional (so a control can be built for the typed-context path
without a store handle), and route the body through the context view. Keep the old
`on_before_model` working by building a view from `self._receipts` when present (dual-load until
Phase 6).

```python
    def __init__(
        self,
        *,
        receipts: Any | None = None,
        contract_required: Any | None = None,
        agent_role: str = "general",
        env: dict[str, str] | None = None,
    ) -> None:
        self._receipts = receipts
        self._contract_required = contract_required
        self._agent_role = agent_role
        self._env = env

    async def on_before_model(
        self,
        *,
        callback_context: Any,
        llm_request: Any,
    ) -> None:
        # Dual-load: resolve the view from the legacy store handle, then delegate
        # to the typed-context path. Phase 6 builds the view in the dispatcher and
        # this branch goes away.
        from magi_agent.packs.context import ControlPlaneContext, EvidenceLedgerView

        view = self._resolve_view_from_store(callback_context)
        ctx = ControlPlaneContext.minimal(evidence=view)
        return await self.apply_before_model(ctx, llm_request=llm_request)

    def _resolve_view_from_store(self, callback_context: Any) -> Any:
        from magi_agent.packs.context import EvidenceLedgerView

        if self._receipts is None or self._contract_required is None:
            return None
        session = getattr(callback_context, "session", None)
        session_id = _non_empty_str(getattr(session, "id", None))
        turn_id = _non_empty_str(getattr(callback_context, "invocation_id", None))
        if turn_id is None:
            turn_id = _latest_event_invocation_id(session)
        if session_id is None or turn_id is None:
            return None
        ledger = self._receipts.ledger_for_turn(session_id=session_id, turn_id=turn_id)
        if ledger is None:
            return None
        open_controls = tuple(
            self._receipts.open_controls_for_turn(
                session_id=session_id, turn_id=turn_id
            )
        )
        return EvidenceLedgerView(
            ledger=ledger,
            open_controls=open_controls,
            contract_required=self._contract_required,
            agent_role=self._agent_role,
        )

    async def apply_before_model(
        self,
        ctx: Any,
        *,
        llm_request: Any,
    ) -> None:
        """Typed-context path: read the resolved EvidenceLedgerView, never a store."""
        from magi_agent.harness.general_automation.constraint_reinjection import (
            ga_constraint_reinjection,
        )

        view = getattr(ctx, "evidence", None)
        if view is None or getattr(view, "ledger", None) is None:
            return None

        reminder = ga_constraint_reinjection(
            contract_required=view.contract_required,
            ledger=view.ledger,
            open_controls=view.open_controls,
            agent_role=view.agent_role,
            env=self._env if self._env is not None else dict(os.environ),
        )
        if not reminder:
            return None

        contents = getattr(llm_request, "contents", None)
        if isinstance(contents, list):
            try:
                from google.genai import types as _genai_types

                contents.append(
                    _genai_types.Content(
                        role="user",
                        parts=[_genai_types.Part(text=reminder)],
                    )
                )
            except Exception:
                contents.append({"role": "user", "content": reminder})
        elif isinstance(llm_request, dict):
            llm_request.setdefault("contents", [])
            llm_request["contents"].append({"role": "user", "content": reminder})
        return None
```

- [ ] **Step 5: Run new test → PASS, and the legacy suite → PASS.**

```bash
MAGI_CONFIG="$(mktemp -d)/config.toml" uv run pytest \
  tests/adk_bridge/test_ga_constraint_context.py \
  tests/test_ga_constraint_control.py -q
```
Expected: PASS (the legacy `test_ga_constraint_control.py` exercises the store-backed
`on_before_model` path, which now flows through `_resolve_view_from_store` → `apply_before_model`,
byte-identical reminder output).

- [ ] **Step 6: Golden gate (S-A is the `ga_constraint` golden scenario — load-bearing).**

```bash
MAGI_CONFIG="$(mktemp -d)/config.toml" uv run pytest \
  tests/fixtures/neutral_runtime_golden/test_golden_regression.py -q
```
Expected: PASS, no diff. The `ga_constraint.json` trace (a `reinject` with `source` containing
`ga`/`constraint`) must be unchanged. If it differs, the reminder text or trigger changed
unintentionally — revert; do NOT `capture --write`.

- [ ] **Step 7: Commit.**

```bash
git add magi_agent/adk_bridge/control_plane.py tests/adk_bridge/test_ga_constraint_context.py
git commit -m "refactor(control-plane): GA reinjection reads EvidenceLedgerView, not store

S-A: GaConstraintReinjectionControl.apply_before_model consumes the resolved
EvidenceLedgerView off the typed context; the legacy receipt-store lookup is
done by _resolve_view_from_store (dual-load, removed in Phase 6). A user pack
can now author an equivalent reminder with no privileged store handle.
Golden ga_constraint trace unchanged."
```

---

## S-B (PARALLEL): `SelfReviewAfterTurnControl` — pre-extracted `TurnSnapshot` + public `ForkRunner`

**Seam:** nested session/event traversal + the privileged `ForkRunner`. Today the control calls
`_extract_self_review_turn_snapshot(agent, callback_context)` itself (walking `session.events`,
`control_plane.py:~642-664`, `:717-739`) and lazily builds `ForkRunner()` (`:709-714`). Migrate so:
(1) the runtime pre-extracts a `TurnSnapshot` onto the context, (2) `ForkRunner` is exposed on the
context as a public capability (full-trust decision per 00-BLUEPRINT §0).

### Task S-B.1: `SelfReviewAfterTurnControl.apply_after_agent(ctx)` consumes `ctx.turn_snapshot` + `ctx.fork_runner`

**Files:**
- Modify: `magi_agent/adk_bridge/control_plane.py`
- Test: `tests/adk_bridge/test_self_review_context.py` (new)

- [ ] **Step 1: Re-grep current control.**

```bash
grep -n "class SelfReviewAfterTurnControl\|on_after_agent\|_extract_self_review_turn_snapshot\|_fork_runner_or_default\|_run_self_review\|_SelfReviewTurnSnapshot" \
  magi_agent/adk_bridge/control_plane.py
```
Confirm `on_after_agent` extracts a `_SelfReviewTurnSnapshot` then `self._schedule(self._run_self_review(snapshot))`,
and `_run_self_review` calls `run_self_review_hook(..., fork_runner=self._fork_runner_or_default(), ...)`.

- [ ] **Step 2: Write the failing test.**

```python
# tests/adk_bridge/test_self_review_context.py
from __future__ import annotations

import asyncio

from magi_agent.adk_bridge.control_plane import SelfReviewAfterTurnControl
from magi_agent.packs.context import ControlPlaneContext, TurnSnapshot


class _RecordingForkRunner:
    def __init__(self):
        self.called = False

    async def fork(self, **kwargs):  # matches ForkRunner.fork signature surface
        self.called = True
        return ([], None)


def test_self_review_uses_context_snapshot_and_fork_runner():
    fork = _RecordingForkRunner()
    captured = {}

    def _scheduler(coro):
        # run synchronously so the test can assert without a background loop
        asyncio.get_event_loop().run_until_complete(coro)

    ctrl = SelfReviewAfterTurnControl(scheduler=_scheduler)
    snap = TurnSnapshot(
        session_id="s1",
        turn_id="t1",
        system_prompt_blocks=({"type": "text", "text": "you are"},),
        parent_assistant_message={"role": "assistant", "content": [{"type": "text", "text": "done"}]},
    )
    ctx = ControlPlaneContext.minimal(turn_snapshot=snap, fork_runner=fork)
    asyncio.run(ctrl.apply_after_agent(ctx))
    assert fork.called is True


def test_self_review_no_snapshot_is_noop():
    ctrl = SelfReviewAfterTurnControl()
    ctx = ControlPlaneContext.minimal(turn_snapshot=None)
    # No snapshot -> no scheduling, no crash.
    asyncio.run(ctrl.apply_after_agent(ctx))
```

- [ ] **Step 3: Run, see it FAIL.**

```bash
MAGI_CONFIG="$(mktemp -d)/config.toml" uv run pytest tests/adk_bridge/test_self_review_context.py -q
```
Expected: FAIL (no `apply_after_agent`).

- [ ] **Step 4: Implement — add `apply_after_agent(ctx)`; keep `on_after_agent` dual-loading.**

```python
    async def on_after_agent(
        self,
        *,
        agent: Any,
        callback_context: Any,
    ) -> None:
        # Dual-load: extract the snapshot the legacy way, then delegate to the
        # typed-context path with the control's own fork_runner. Phase 6 has the
        # dispatcher pre-extract the snapshot + supply fork_runner on the context.
        from magi_agent.packs.context import ControlPlaneContext, TurnSnapshot

        try:
            legacy = _extract_self_review_turn_snapshot(
                agent=agent, callback_context=callback_context
            )
        except Exception:
            logger.debug(
                "self-review after-turn context extraction failed", exc_info=True
            )
            return None
        if legacy is None:
            return None
        snap = TurnSnapshot(
            session_id=legacy.session_id,
            turn_id=legacy.turn_id,
            system_prompt_blocks=tuple(legacy.system_prompt_blocks),
            parent_assistant_message=dict(legacy.parent_assistant_message),
        )
        ctx = ControlPlaneContext.minimal(
            turn_snapshot=snap, fork_runner=self._fork_runner_or_default()
        )
        return await self.apply_after_agent(ctx)

    async def apply_after_agent(self, ctx: Any) -> None:
        """Typed-context path: schedule the C1 fork from a pre-extracted snapshot
        using the ForkRunner exposed on the context (full-trust public capability)."""
        snap = getattr(ctx, "turn_snapshot", None)
        if snap is None:
            return None
        fork_runner = getattr(ctx, "fork_runner", None) or self._fork_runner_or_default()
        self._schedule(self._run_self_review_with(snap, fork_runner))
        return None

    async def _run_self_review_with(self, snapshot: Any, fork_runner: Any) -> None:
        from magi_agent.harness.self_review import run_self_review_hook

        await run_self_review_hook(
            session_id=snapshot.session_id,
            turn_id=snapshot.turn_id,
            system_prompt_blocks=list(snapshot.system_prompt_blocks),
            parent_assistant_message=dict(snapshot.parent_assistant_message),
            fork_runner=fork_runner,
            candidate_sink=self._candidate_sink,
            config=self._config,
            now=self._now,
        )
```

(Leave the existing `_run_self_review`, `_fork_runner_or_default`, `_schedule`,
`_on_background_task_done` in place — `_run_self_review_with` is the new context-driven variant;
`apply_after_agent` no longer needs the old `_run_self_review`. Keep `_run_self_review` only if any
other caller/test references it — grep first; if nothing references it, remove it in this commit.)

- [ ] **Step 5: Run new test → PASS, legacy self-review suite → PASS.**

```bash
MAGI_CONFIG="$(mktemp -d)/config.toml" uv run pytest \
  tests/adk_bridge/test_self_review_context.py \
  tests/adk_bridge/test_self_review_after_turn_wiring.py -q
```
Expected: PASS (the legacy wiring test drives `on_after_agent`, which now flows through
`apply_after_agent`).

- [ ] **Step 6: Golden gate.**

```bash
MAGI_CONFIG="$(mktemp -d)/config.toml" uv run pytest \
  tests/fixtures/neutral_runtime_golden/test_golden_regression.py -q
```
Expected: PASS, no diff. (Self-review is observational — `on_after_agent` returns `None` and cannot
alter the turn — so it is not a golden scenario, but the file is shared; run anyway.)

- [ ] **Step 7: Commit.**

```bash
git add magi_agent/adk_bridge/control_plane.py tests/adk_bridge/test_self_review_context.py
git commit -m "refactor(control-plane): self-review consumes TurnSnapshot + public ForkRunner

S-B: apply_after_agent reads a pre-extracted TurnSnapshot and the ForkRunner
exposed on the typed context (full-trust public capability per blueprint).
on_after_agent dual-loads the legacy session-tree extraction until Phase 6.
No privileged session traversal in the control body. Golden unchanged."
```

---

## S-C (PARALLEL): `_EditRetryLoopControl` + `_ResilienceLoopControl` — per-invocation state into the context

**Seam:** per-invocation mutable state lifecycle. Today the state lives **inside the wrapped
plugins**: `MagiEditRetryReflectionPlugin._attempts` (`edit_retry_reflection.py:~122`) and
`MagiResiliencePlugin._detectors` / `_recovery_state` (`resilience_plugin.py:~160-164`), each with
its own `after_run_callback` sweep + (resilience) `_MAX_TRACKED_SCOPES` LRU bound. Migrate so the
mutable state is the runtime-owned `PerInvocationState` (S-0) on the context, with
`clear_invocation` as the clear-on-turn-complete hook and the LRU bound preserved. The adapters
become stateless pass-throughs that read/write `ctx.per_invocation`.

> **Scope note:** the wrapped plugins (`MagiEditRetryReflectionPlugin`, `MagiResiliencePlugin`) keep
> their pure decision logic (`RetryController`, `ToolCallLoopDetector`, `RecoveryEngine`). S-C moves
> only the **mutable counters/objects** out of `self` and into `PerInvocationState`, exposed via new
> stateless decision helpers, so a user pack authoring an equivalent control gets the same
> runtime-owned state struct rather than hiding mutable state in its own instance.

### Task S-C.1: Edit-retry attempts → `PerInvocationState`

**Files:**
- Modify: `magi_agent/adk_bridge/edit_retry_reflection.py`
- Modify: `magi_agent/adk_bridge/control_plane.py` (`_EditRetryLoopControl`)
- Test: `tests/adk_bridge/test_edit_retry_state_context.py` (new)

- [ ] **Step 1: Re-grep current state usage.**

```bash
grep -n "_attempts\|def _maybe_reflect\|def _reset\|after_run_callback\|_scope_key" \
  magi_agent/adk_bridge/edit_retry_reflection.py
grep -n "class _EditRetryLoopControl\|after_tool_callback\|self._plugin" \
  magi_agent/adk_bridge/control_plane.py
```
Confirm `_maybe_reflect` increments `self._attempts[(scope_key, tool_name)]`, `_reset` pops it, and
`after_run_callback` drops all keys for the finished invocation.

- [ ] **Step 2: Write the failing test — attempts live in PerInvocationState.**

```python
# tests/adk_bridge/test_edit_retry_state_context.py
from __future__ import annotations

import asyncio

from magi_agent.adk_bridge.edit_retry_reflection import (
    MagiEditRetryReflectionPlugin,
    EDIT_RETRY_REFLECTION_RESPONSE_TYPE,
)
from magi_agent.packs.context import PerInvocationState


class _Tool:
    name = "FileEdit"


class _ToolCtx:
    invocation_id = "inv-1"


def test_edit_retry_attempts_recorded_in_per_invocation_state():
    plugin = MagiEditRetryReflectionPlugin(max_attempts=2)
    state = PerInvocationState()
    # first failure -> reflection guidance, attempt=1 recorded in the shared state
    out = plugin.reflect_with_state(
        state=state,
        tool=_Tool(),
        tool_args={"old_string": "x", "new_string": "y"},
        tool_context=_ToolCtx(),
        reason="old_text_not_found",
    )
    assert out is not None
    assert out["response_type"] == EDIT_RETRY_REFLECTION_RESPONSE_TYPE
    assert state.get_scoped("inv-1", "FileEdit", default=0) == 1
    # clearing the invocation drops the attempt counter
    state.clear_invocation("inv-1")
    assert state.get_scoped("inv-1", "FileEdit", default=0) == 0
```

- [ ] **Step 3: Run, see it FAIL.**

```bash
MAGI_CONFIG="$(mktemp -d)/config.toml" uv run pytest tests/adk_bridge/test_edit_retry_state_context.py -q
```
Expected: FAIL (no `reflect_with_state`).

- [ ] **Step 4: Implement — add `reflect_with_state(state, ...)`; keep `_maybe_reflect` delegating
to an internal default state for backward compat.**

In `edit_retry_reflection.py`, add a state-taking variant and route the existing instance-state path
through it (so the plugin's own `_attempts` becomes a private default `PerInvocationState` used only
by the legacy callbacks until Phase 6):

```python
    def __init__(
        self,
        *,
        max_attempts: int,
        name: str = EDIT_RETRY_REFLECTION_PLUGIN_NAME,
    ) -> None:
        super().__init__(name)
        if max_attempts < 1:
            raise ValueError("max_attempts must be >= 1")
        self.max_attempts = max_attempts
        # Legacy default state (used by the ADK callbacks until Phase 6 supplies a
        # context-owned PerInvocationState). Replaces the old self._attempts dict.
        from magi_agent.packs.context import PerInvocationState

        self._default_state = PerInvocationState()
        self._controller = RetryController(
            max_attempts=self.max_attempts,
            repair_rules=coding_edit_retry_repair_rules(),
        )

    def reflect_with_state(
        self,
        *,
        state: Any,
        tool: Any,
        tool_args: dict[str, Any],
        tool_context: Any,
        reason: str,
    ) -> dict[str, Any] | None:
        """Pure decision over a runtime-owned PerInvocationState (S-C).

        Replaces the instance-private ``self._attempts`` mutation; the caller
        (control adapter) supplies the shared state from ``ctx.per_invocation``.
        """
        tool_name = _tool_name(tool)
        if tool_name not in _EDIT_TOOL_NAMES:
            return None

        scope_key = _scope_key(tool_context)
        attempt = state.get_scoped(scope_key, tool_name, default=0) + 1
        state.set_scoped(scope_key, tool_name, attempt)

        error_code = _classify_edit_error_code(reason, tool_args)
        decision = self._controller.next(
            {
                "kind": "edit_apply_failed",
                "reason": reason,
                "attempt": attempt,
                "errorCode": error_code,
            }
        )
        if decision.action != "resample" or not decision.hidden_user_message:
            return None
        return {
            "response_type": EDIT_RETRY_REFLECTION_RESPONSE_TYPE,
            "error_type": "edit_apply_failed",
            "error_code": error_code,
            "retry_attempt": attempt,
            "max_attempts": self.max_attempts,
            "reflection_guidance": decision.hidden_user_message,
        }
```

Now point the existing `_maybe_reflect` at the default state (replace the `self._attempts` lines):

```python
    def _maybe_reflect(
        self,
        *,
        tool: Any,
        tool_args: dict[str, Any],
        tool_context: Any,
        reason: str,
    ) -> dict[str, Any] | None:
        return self.reflect_with_state(
            state=self._default_state,
            tool=tool,
            tool_args=tool_args,
            tool_context=tool_context,
            reason=reason,
        )

    def _reset(self, tool_context: Any, tool_name: str) -> None:
        self._default_state.pop_scoped(_scope_key(tool_context), tool_name)

    async def after_run_callback(self, *, invocation_context: Any) -> None:
        inv = getattr(invocation_context, "invocation_id", None)
        if isinstance(inv, str) and inv:
            self._default_state.clear_invocation(inv)
```

Then in `control_plane.py`, give `_EditRetryLoopControl` a context-taking method that uses
`ctx.per_invocation` when present, falling back to the plugin's default state:

```python
    async def apply_after_tool(
        self,
        ctx: Any,
        *,
        tool: Any,
        args: dict[str, Any],
        tool_context: Any,
        result: Any,
    ) -> dict[str, Any] | None:
        from collections.abc import Mapping

        from magi_agent.adk_bridge.edit_retry_reflection import (
            EDIT_RETRY_REFLECTION_RESPONSE_TYPE,
            _error_reason_from_result,
            _tool_name,
        )

        if (
            isinstance(result, Mapping)
            and result.get("response_type") == EDIT_RETRY_REFLECTION_RESPONSE_TYPE
        ):
            return None
        state = getattr(ctx, "per_invocation", None) or self._plugin._default_state
        reason = _error_reason_from_result(result)
        if reason is None:
            state.pop_scoped(
                getattr(tool_context, "invocation_id", "") or "__magi_edit_retry_global__",
                _tool_name(tool),
            )
            return None
        return self._plugin.reflect_with_state(
            state=state, tool=tool, tool_args=args, tool_context=tool_context, reason=reason
        )
```

- [ ] **Step 5: Run new test → PASS, legacy edit-retry suite → PASS.**

```bash
MAGI_CONFIG="$(mktemp -d)/config.toml" uv run pytest \
  tests/adk_bridge/test_edit_retry_state_context.py \
  tests/adk_bridge/test_extended_plugin_on_tool_error.py -q
```
Expected: PASS.

- [ ] **Step 6: Golden gate (S-C edit-retry is the `edit_retry` golden scenario — load-bearing).**

```bash
MAGI_CONFIG="$(mktemp -d)/config.toml" uv run pytest \
  tests/fixtures/neutral_runtime_golden/test_golden_regression.py -q
```
Expected: PASS, no diff. The `edit_retry.json` trace (an `after_tool` override) must be unchanged.

- [ ] **Step 7: Commit.**

```bash
git add magi_agent/adk_bridge/edit_retry_reflection.py magi_agent/adk_bridge/control_plane.py \
        tests/adk_bridge/test_edit_retry_state_context.py
git commit -m "refactor(control-plane): edit-retry attempts in runtime-owned PerInvocationState

S-C(1): reflect_with_state mutates ctx.per_invocation (LRU-bounded, clear-on-
turn-complete) instead of plugin-private self._attempts. _EditRetryLoopControl.
apply_after_tool reads the shared state. Golden edit_retry trace unchanged."
```

### Task S-C.2: Resilience detectors + recovery state → `PerInvocationState`

**Files:**
- Modify: `magi_agent/adk_bridge/resilience_plugin.py`
- Modify: `magi_agent/adk_bridge/control_plane.py` (`_ResilienceLoopControl`)
- Test: `tests/adk_bridge/test_resilience_state_context.py` (new)

- [ ] **Step 1: Re-grep current state usage.**

```bash
grep -n "_detectors\|_recovery_state\|_bound_state_dicts\|_MAX_TRACKED_SCOPES\|after_run_callback\|def after_tool_callback" \
  magi_agent/adk_bridge/resilience_plugin.py
```
Confirm `after_tool_callback` reads/creates `self._detectors[scope]`, `_note_recovery_classification`
writes `self._recovery_state[scope]`, `after_run_callback` pops both, `_bound_state_dicts` LRU-caps
both at `_MAX_TRACKED_SCOPES` (256).

- [ ] **Step 2: Write the failing test — detector lives in PerInvocationState.**

```python
# tests/adk_bridge/test_resilience_state_context.py
from __future__ import annotations

from magi_agent.adk_bridge.resilience_plugin import (
    LOOP_GUARD_RESPONSE_TYPE,
    build_resilience_plugin,
)
from magi_agent.packs.context import PerInvocationState


class _Tool:
    name = "Search"


class _ToolCtx:
    invocation_id = "inv-1"


def test_loop_guard_detector_lives_in_per_invocation_state():
    plugin = build_resilience_plugin(
        loop_guard_enabled=True,
        loop_guard_soft_threshold=2,
        loop_guard_hard_threshold=3,
        error_recovery_enabled=False,
    )
    state = PerInvocationState()
    args = {"query": "same"}
    # Drive identical calls until the hard threshold fires; detector stored in state.
    last = None
    for _ in range(3):
        last = plugin.guard_with_state(
            state=state, tool=_Tool(), tool_args=args, tool_context=_ToolCtx(), result={"ok": 1}
        )
    assert last is not None and last.get("response_type") == LOOP_GUARD_RESPONSE_TYPE
    assert state.peek_object("inv-1", "loop_detector") is not None
    state.clear_invocation("inv-1")
    assert state.peek_object("inv-1", "loop_detector") is None
```

- [ ] **Step 3: Run, see it FAIL.**

```bash
MAGI_CONFIG="$(mktemp -d)/config.toml" uv run pytest tests/adk_bridge/test_resilience_state_context.py -q
```
Expected: FAIL (no `guard_with_state`).

- [ ] **Step 4: Implement — add `guard_with_state(state, ...)`; route the legacy callback through a
default state.**

In `resilience_plugin.py` `__init__`, replace `self._detectors` / `self._recovery_state` with a
private default `PerInvocationState` (preserving the 256 LRU bound) and keep the factory/engine:

```python
        from magi_agent.packs.context import PerInvocationState

        self._default_state = PerInvocationState(max_scopes=_MAX_TRACKED_SCOPES)
```

Add the state-taking loop-guard helper (the detector is the per-invocation object):

```python
    def guard_with_state(
        self,
        *,
        state: Any,
        tool: Any,
        tool_args: dict[str, Any],
        tool_context: Any,
        result: Any,
    ) -> dict[str, Any] | None:
        from collections.abc import Mapping

        if self._loop_detector_factory is None:
            return None
        if (
            isinstance(result, Mapping)
            and result.get("response_type") == LOOP_GUARD_RESPONSE_TYPE
        ):
            return None
        scope = _scope_key(tool_context)
        detector = state.get_object(scope, "loop_detector", self._loop_detector_factory)
        check = detector.check(_tool_name(tool), tool_args)
        if check.action == "ok":
            return None
        if check.action == "hard_escalation":
            return _hard_stop_response(_tool_name(tool), check)
        return _soft_nudge_response(result, _tool_name(tool), check)
```

Point the existing `after_tool_callback` at the default state:

```python
    async def after_tool_callback(
        self,
        *,
        tool: Any,
        tool_args: dict[str, Any],
        tool_context: Any,
        result: Any,
    ) -> dict[str, Any] | None:
        return self.guard_with_state(
            state=self._default_state,
            tool=tool,
            tool_args=tool_args,
            tool_context=tool_context,
            result=result,
        )
```

Update `_note_recovery_classification` + `after_run_callback` to use the default state's object map
(recovery attempt state is a per-invocation object):

```python
    def _note_recovery_classification(self, scope: str, kind: ErrorKind) -> None:
        prev = self._default_state.peek_object(scope, "recovery_state")
        new_state = (prev or RecoveryAttemptState()).model_copy(
            update={"attempt_number": (prev.attempt_number if prev else 0) + 1}
        )
        self._default_state.set_object(scope, "recovery_state", new_state)

    async def after_run_callback(self, *, invocation_context: Any) -> None:
        inv = getattr(invocation_context, "invocation_id", None)
        if isinstance(inv, str) and inv:
            self._default_state.clear_invocation(inv)
```

(`on_model_error_callback` keeps calling `_note_recovery_classification`; remove the now-dead
`_bound_state_dicts` method and the `self._detectors` / `self._recovery_state` attributes — grep to
confirm nothing else references them, then delete.)

Then in `control_plane.py`, give `_ResilienceLoopControl` a context-taking method:

```python
    async def apply_after_tool(
        self,
        ctx: Any,
        *,
        tool: Any,
        args: dict[str, Any],
        tool_context: Any,
        result: Any,
    ) -> dict[str, Any] | None:
        state = getattr(ctx, "per_invocation", None) or self._plugin._default_state
        return self._plugin.guard_with_state(
            state=state, tool=tool, tool_args=args, tool_context=tool_context, result=result
        )
```

- [ ] **Step 5: Run new test → PASS, legacy resilience suite → PASS.**

```bash
MAGI_CONFIG="$(mktemp -d)/config.toml" uv run pytest \
  tests/adk_bridge/test_resilience_state_context.py \
  tests/test_resilience_plugin_wiring.py -q
```
Expected: PASS.

- [ ] **Step 6: Golden gate (S-C loop-guard is the `loop_guard` golden scenario — load-bearing).**

```bash
MAGI_CONFIG="$(mktemp -d)/config.toml" uv run pytest \
  tests/fixtures/neutral_runtime_golden/test_golden_regression.py -q
```
Expected: PASS, no diff. The `loop_guard.json` trace (a `before_tool`/`deny`-equivalent loop-guard
hard stop surfaced as an after-tool override) must be unchanged.

- [ ] **Step 7: Commit.**

```bash
git add magi_agent/adk_bridge/resilience_plugin.py magi_agent/adk_bridge/control_plane.py \
        tests/adk_bridge/test_resilience_state_context.py
git commit -m "refactor(control-plane): resilience detectors/recovery in PerInvocationState

S-C(2): guard_with_state stores the per-turn ToolCallLoopDetector and recovery
attempt state in the runtime-owned PerInvocationState (LRU max_scopes=256,
clear-on-turn-complete). Removes plugin-private self._detectors/_recovery_state
+ _bound_state_dicts. Golden loop_guard trace unchanged."
```

---

## S-D (PARALLEL): `_CompactionLoopControl` — narrow the boundary services behind a context capability

**Seam:** boundary services. Today `MagiContextCompactionPlugin` owns a `ContextLifecycleBoundary`
and lazily builds its own `WorkspaceSessionService` + `QueryState` (`context_compaction.py:~119-126`,
`:200-237`) — privileged services baked into the control. Migrate so the **compaction decision** is
exposed on the context as one narrow callable capability (`ctx.compaction`), so a user pack can
supply an equivalent decision function without inheriting the boundary/session plumbing.

### Task S-D.1: `_CompactionLoopControl.apply_before_model(ctx)` calls `ctx.compaction`

**Files:**
- Modify: `magi_agent/adk_bridge/context_compaction.py`
- Modify: `magi_agent/adk_bridge/control_plane.py` (`_CompactionLoopControl`)
- Test: `tests/adk_bridge/test_compaction_context_capability.py` (new)

- [ ] **Step 1: Re-grep current plumbing.**

```bash
grep -n "class MagiContextCompactionPlugin\|before_model_callback\|_decision_inputs\|WorkspaceSessionService\|compact_if_needed\|_adjust_split_to_avoid_orphan_response" \
  magi_agent/adk_bridge/context_compaction.py
grep -n "class _CompactionLoopControl\|on_before_model\|self._plugin" \
  magi_agent/adk_bridge/control_plane.py
```
Confirm `before_model_callback` builds `events`, calls `self._boundary.compact_if_needed(...)`, and
on `status == "compacted"` trims `llm_request.contents` to the orphan-adjusted tail.

- [ ] **Step 2: Write the failing test — a compaction capability decides, control applies.**

```python
# tests/adk_bridge/test_compaction_context_capability.py
from __future__ import annotations

import asyncio

from magi_agent.adk_bridge.context_compaction import (
    build_context_compaction_plugin,
    CompactionCapability,
)
from magi_agent.packs.context import ControlPlaneContext


class _Part:
    def __init__(self, text):
        self.text = text
        self.function_response = None
        self.function_call = None


class _Content:
    def __init__(self, text, role="user"):
        self.parts = [_Part(text)]
        self.role = role


class _Req:
    def __init__(self, n):
        self.contents = [_Content(f"msg {i}" * 50) for i in range(n)]


def test_compaction_capability_trims_contents_when_over_budget():
    plugin = build_context_compaction_plugin(
        enabled=True, token_threshold=1, tail_events=2
    )
    cap = CompactionCapability(plugin)
    req = _Req(10)
    ctx = ControlPlaneContext.minimal(compaction=cap)
    asyncio.run(plugin.apply_before_model(ctx, llm_request=req))
    assert len(req.contents) <= 4  # trimmed toward the tail (orphan widening allowed)
```

- [ ] **Step 3: Run, see it FAIL.**

```bash
MAGI_CONFIG="$(mktemp -d)/config.toml" uv run pytest tests/adk_bridge/test_compaction_context_capability.py -q
```
Expected: FAIL (`CompactionCapability` undefined; no `apply_before_model`).

- [ ] **Step 4: Implement — wrap the boundary decision in `CompactionCapability`; control calls it.**

In `context_compaction.py`, extract the decision+trim into a method that takes the request, then add
a thin `CompactionCapability` wrapper and an `apply_before_model(ctx, llm_request)` that calls the
capability from the context (falling back to the plugin's own capability for the legacy path):

```python
class CompactionCapability:
    """Narrow context capability wrapping the boundary-backed compaction decision.

    A user pack can supply any object with the same ``trim(llm_request)`` async
    method; first-party wraps :class:`MagiContextCompactionPlugin`. This is the
    only handle a control needs — the ContextLifecycleBoundary +
    WorkspaceSessionService stay encapsulated behind it.
    """

    def __init__(self, plugin: "MagiContextCompactionPlugin") -> None:
        self._plugin = plugin

    async def trim(self, llm_request: Any) -> None:
        await self._plugin._trim_request(llm_request)
```

Refactor the existing `before_model_callback` body into `_trim_request` (move the whole
`try: ... except Exception: return None` block verbatim, operating on `llm_request`), and add the
context entry point + keep the ADK callback delegating:

```python
    async def before_model_callback(
        self,
        *,
        callback_context: Any,
        llm_request: Any,
    ) -> None:
        # Legacy ADK path: build a context carrying this plugin's own capability.
        from magi_agent.packs.context import ControlPlaneContext

        ctx = ControlPlaneContext.minimal(compaction=CompactionCapability(self))
        return await self.apply_before_model(ctx, llm_request=llm_request)

    async def apply_before_model(self, ctx: Any, *, llm_request: Any) -> None:
        cap = getattr(ctx, "compaction", None) or CompactionCapability(self)
        await cap.trim(llm_request)
        return None

    async def _trim_request(self, llm_request: Any) -> None:
        """The EXACT body lifted verbatim from the legacy ``before_model_callback``
        (HEAD-802e707b ``context_compaction.py:~148-198``: contents-build →
        ``compact_if_needed`` → orphan-adjusted tail-trim → fail-open). Re-grep
        Step 1 and move the current body unchanged — do not retype from memory.
        Reproduced here so the migration is provably behavior-identical:"""
        try:
            contents = list(getattr(llm_request, "contents", None) or [])
            if len(contents) <= self.tail_events:
                # Nothing to trim even in the worst case; skip the boundary call.
                return None

            events = tuple(
                ContextLifecycleEvent(
                    eventRef=_content_event_ref(index),
                    tokenEstimate=_content_token_estimate(content),
                )
                for index, content in enumerate(contents)
            )

            session_service, session, state = await self._decision_inputs()
            decision = await self._boundary.compact_if_needed(
                session_service=session_service,
                session=session,
                state=state,
                events=events,
                approvedSummaryRef=_COMPACTION_SUMMARY_REF,
                approvedSummaryDigest=_COMPACTION_SUMMARY_DIGEST,
                config=self._config,
            )
            if decision.status != "compacted":
                return None

            keep = min(self.tail_events, len(contents))
            split_index = len(contents) - keep
            split_index = _adjust_split_to_avoid_orphan_response(contents, split_index)
            if split_index <= 0:
                return None
            kept = len(contents) - split_index
            if kept > 2 * self.tail_events:
                logger.debug(
                    "context compaction near no-op: orphan widening kept %d contents "
                    "(tail_events=%d) from %d total",
                    kept,
                    self.tail_events,
                    len(contents),
                )
            llm_request.contents = contents[split_index:]
        except Exception:
            # Fail-open: compaction must never break a live model turn.
            return None
        return None
```

Add `CompactionCapability` to `__all__`. Then in `control_plane.py`, give `_CompactionLoopControl`
a context-taking method:

```python
    async def apply_before_model(self, ctx: Any, *, llm_request: Any) -> None:
        await self._plugin.apply_before_model(ctx, llm_request=llm_request)
        return None
```

- [ ] **Step 5: Run new test → PASS, legacy compaction suite → PASS.**

```bash
MAGI_CONFIG="$(mktemp -d)/config.toml" uv run pytest \
  tests/adk_bridge/test_compaction_context_capability.py \
  tests/adk_bridge/test_context_compaction_plugin.py -q
```
Expected: PASS.

- [ ] **Step 6: Golden gate (S-D compaction is the `compaction` golden scenario — load-bearing).**

```bash
MAGI_CONFIG="$(mktemp -d)/config.toml" uv run pytest \
  tests/fixtures/neutral_runtime_golden/test_golden_regression.py -q
```
Expected: PASS, no diff. The `compaction.json` trace (a `compaction`/`fired:true`) must be
unchanged.

- [ ] **Step 7: Commit.**

```bash
git add magi_agent/adk_bridge/context_compaction.py magi_agent/adk_bridge/control_plane.py \
        tests/adk_bridge/test_compaction_context_capability.py
git commit -m "refactor(control-plane): compaction behind a CompactionCapability on the context

S-D: the ContextLifecycleBoundary + WorkspaceSessionService are encapsulated in
CompactionCapability; _CompactionLoopControl.apply_before_model calls
ctx.compaction.trim(). A user pack can supply an equivalent capability. Golden
compaction trace unchanged."
```

---

## Task 5.2 (post-fan-out, SERIAL): dispatcher fills the context + clear-on-turn-complete hook

After S-A…S-D, the controls have `apply_*` methods reading the typed context, but the live ADK
fan-out (`ControlPlanePlugin` / `_ExtendedControlPlanePlugin`) still calls the legacy
`on_*` hooks. This task wires the Phase-2 dispatcher to build the full `ControlPlaneContext`
(populating all four seam fields) once per callback and call the `apply_*` methods, and to call
`per_invocation.clear_invocation(inv)` from `after_run_callback`.

**Files:**
- Modify: `magi_agent/packs/registries.py` (or wherever the Phase-2 dispatcher lives — grep)
- Modify: `magi_agent/adk_bridge/control_plane.py` (`ControlPlane._before_model` / `_after_tool` /
  `_after_agent` route through `apply_*` when the control defines it)
- Test: `tests/packs/test_control_plane_dispatch_typed.py` (new)

- [ ] **Step 1: Re-grep the dispatcher + fan-out.**

```bash
grep -n "def _before_model\|def _after_tool\|def _after_agent\|class ControlPlane\b" \
  magi_agent/adk_bridge/control_plane.py
grep -rn "build_control_plane_context\|ControlPlaneContext\|class .*Dispatcher\|def dispatch" magi_agent/packs/ | head
```
Identify the Phase-2 dispatcher entry that builds a `ControlPlaneContext` from ADK callback args. If
Phase 2 left a `build_control_plane_context(...)` factory, extend it to populate `evidence`,
`turn_snapshot`, `fork_runner`, `per_invocation`, `compaction`. If no factory exists, add one in
`magi_agent/packs/context.py` named `build_control_plane_context`.

- [ ] **Step 2: Write the failing dispatch test.**

```python
# tests/packs/test_control_plane_dispatch_typed.py
from __future__ import annotations

import asyncio

from magi_agent.adk_bridge.control_plane import ControlPlane, BaseLoopControl
from magi_agent.packs.context import ControlPlaneContext, PerInvocationState


class _CtxAwareControl(BaseLoopControl):
    name = "ctx_aware"

    def __init__(self):
        self.saw_state = None

    async def apply_before_model(self, ctx, *, llm_request):
        self.saw_state = ctx.per_invocation
        return None


class _Req:
    contents = []


def test_dispatcher_routes_apply_before_model_with_shared_state():
    state = PerInvocationState()
    plane = ControlPlane(per_invocation=state)
    ctrl = _CtxAwareControl()
    plane.register(ctrl)
    asyncio.run(
        plane._before_model(callback_context=object(), llm_request=_Req())
    )
    assert ctrl.saw_state is state  # the SAME runtime-owned state, not a per-control dict
```

- [ ] **Step 3: Run, see it FAIL.**

```bash
MAGI_CONFIG="$(mktemp -d)/config.toml" uv run pytest tests/packs/test_control_plane_dispatch_typed.py -q
```
Expected: FAIL (`ControlPlane.__init__` takes no `per_invocation`; `_before_model` does not route to
`apply_before_model`).

- [ ] **Step 4: Implement — `ControlPlane` owns one `PerInvocationState`; fan-out prefers `apply_*`.**

```python
    def __init__(self, *, per_invocation: Any | None = None) -> None:
        from magi_agent.packs.context import PerInvocationState

        self._controls: list[LoopControl] = []
        self._per_invocation = per_invocation or PerInvocationState()

    def _context(self, **fields: Any) -> Any:
        from magi_agent.packs.context import ControlPlaneContext

        return ControlPlaneContext.minimal(
            per_invocation=self._per_invocation, **fields
        )
```

In `_before_model`, prefer the typed entry when a control defines it:

```python
    async def _before_model(self, *, callback_context: Any, llm_request: Any) -> None:
        ctx = self._context()
        for control in self._controls:
            apply = getattr(control, "apply_before_model", None)
            if callable(apply):
                await apply(ctx, llm_request=llm_request)
            else:
                await control.on_before_model(
                    callback_context=callback_context, llm_request=llm_request
                )
        return None
```

Mirror the same "prefer `apply_after_tool` / `apply_after_agent`" routing in `_after_tool` and
`_after_agent` (keep the existing "first non-None wins" / observer semantics). Add the clear hook in
`_ExtendedControlPlanePlugin.after_run_callback`:

```python
    async def after_run_callback(self, *, invocation_context: Any) -> None:
        inv = getattr(invocation_context, "invocation_id", None)
        if isinstance(inv, str) and inv:
            self._p._per_invocation.clear_invocation(inv)
        for ctrl in self._p._controls:
            plugin = getattr(ctrl, "_plugin", None)
            if plugin is not None:
                after_run = getattr(plugin, "after_run_callback", None)
                if callable(after_run):
                    await after_run(invocation_context=invocation_context)
```

- [ ] **Step 5: Run new test → PASS; run the FULL control-plane suite → PASS.**

```bash
MAGI_CONFIG="$(mktemp -d)/config.toml" uv run pytest \
  tests/packs/test_control_plane_dispatch_typed.py \
  tests/adk_bridge/test_control_plane.py \
  tests/adk_bridge/test_control_plane_plugin.py \
  tests/test_runner_plugin_composition.py \
  tests/adk_bridge/test_runner_plugin_parity.py -q
```
Expected: PASS.

- [ ] **Step 6: Golden gate (ALL four scenarios — this is the integration barrier).**

```bash
MAGI_CONFIG="$(mktemp -d)/config.toml" uv run pytest \
  tests/fixtures/neutral_runtime_golden/test_golden_regression.py -q
```
Expected: PASS, no diff across all 4 goldens. If any diff: the dispatch reorder changed behavior —
revert and investigate; do NOT `capture --write` (the migration must be behavior-preserving).

- [ ] **Step 7: Commit.**

```bash
git add magi_agent/packs/context.py magi_agent/packs/registries.py \
        magi_agent/adk_bridge/control_plane.py tests/packs/test_control_plane_dispatch_typed.py
git commit -m "feat(packs): dispatcher routes apply_* with runtime-owned PerInvocationState

ControlPlane owns one PerInvocationState and builds the typed context per
callback; fan-out prefers apply_before_model/apply_after_tool/apply_after_agent
when a control defines them, else the legacy on_* hooks. after_run clears the
per-invocation state. All 4 goldens unchanged."
```

---

## Task 5.3 (SERIAL): full Phase-5 control-plane regression sweep

**Files:** none created — final verification.

- [ ] **Step 1: Run the entire control-plane + oracle suite.**

```bash
MAGI_CONFIG="$(mktemp -d)/config.toml" uv run pytest \
  tests/adk_bridge/test_control_plane.py \
  tests/adk_bridge/test_control_plane_plugin.py \
  tests/adk_bridge/test_max_steps_brake_control.py \
  tests/adk_bridge/test_context_compaction_plugin.py \
  tests/adk_bridge/test_extended_plugin_on_tool_error.py \
  tests/adk_bridge/test_self_review_after_turn_wiring.py \
  tests/adk_bridge/test_runner_plugin_parity.py \
  tests/test_resilience_plugin_wiring.py \
  tests/test_ga_constraint_control.py \
  tests/test_ga_constraint_reinjection.py \
  tests/test_runner_plugin_composition.py \
  tests/adk_bridge/test_ga_constraint_context.py \
  tests/adk_bridge/test_self_review_context.py \
  tests/adk_bridge/test_edit_retry_state_context.py \
  tests/adk_bridge/test_resilience_state_context.py \
  tests/adk_bridge/test_compaction_context_capability.py \
  tests/packs/test_control_plane_context_surface.py \
  tests/packs/test_control_plane_dispatch_typed.py \
  tests/fixtures/neutral_runtime_golden/ -q
```
Expected: ALL pass (~96 legacy control-plane tests + the ~16 new Phase-5 tests + 4 goldens green,
no diff).

- [ ] **Step 2: Confirm no `self._attempts` / `self._detectors` / `self._recovery_state` remain as
the live state owner.**

```bash
grep -rn "self\._attempts\b\|self\._detectors\b\|self\._recovery_state\b" \
  magi_agent/adk_bridge/edit_retry_reflection.py \
  magi_agent/adk_bridge/resilience_plugin.py
```
Expected: **no matches** (all per-invocation mutable state now lives in `PerInvocationState`).

- [ ] **Step 3: Commit (docs/status touch only if a tracking file exists; otherwise skip).** No code
change in this task — it is a gate.

---

## Acceptance criteria (Phase 5 done)

- [ ] All 6 first-party `LoopControl`s expose an `apply_*` method that takes **only** a
  `ControlPlaneContext` (typed context) — no `RuntimeContext`/god-object, no privileged kwargs that a
  user pack cannot reproduce.
- [ ] **S-A:** `GaConstraintReinjectionControl.apply_before_model` reads an `EvidenceLedgerView` off
  the context; it never touches the `GeneralAutomationReceiptLedgerStore` object directly.
- [ ] **S-B:** `SelfReviewAfterTurnControl.apply_after_agent` reads a pre-extracted `TurnSnapshot`
  and the `ForkRunner` exposed as a **public** capability on the context.
- [ ] **S-C:** edit-retry attempts and resilience detectors/recovery-state live in the runtime-owned
  `PerInvocationState` (LRU-bounded, `clear_invocation` on turn complete); no plugin-private
  `self._attempts`/`self._detectors`/`self._recovery_state` remain as the state owner (Task 5.3
  Step 2 grep is clean).
- [ ] **S-D:** `_CompactionLoopControl.apply_before_model` calls `ctx.compaction.trim(...)`; the
  `ContextLifecycleBoundary` + `WorkspaceSessionService` are encapsulated behind `CompactionCapability`.
- [ ] The dispatcher (Task 5.2) builds the typed context per callback and prefers `apply_*`, with one
  shared `PerInvocationState`.
- [ ] `MaxStepsBrakeControl` (template) migrated.
- [ ] **All 4 Phase-0 goldens green with NO diff** (`loop_guard`, `compaction`, `edit_retry`,
  `ga_constraint`) — the migration is behavior-preserving.
- [ ] `gates/` (gate5b) untouched: `git diff --name-only` shows no file under `magi_agent/gates/`.
- [ ] Every test runs headless, no API keys (`MAGI_CONFIG` isolated, `LOCAL_DEV_MODEL_SENTINEL`).

## Rollback

This phase is additive + dual-loaded: each control keeps its legacy `on_*` hook working (it builds a
`minimal` context internally), so reverting any single seam restores prior behavior. To revert the
whole phase: `git revert` the Phase-5 commits (Task 5.0 → 5.3) in reverse order, or
`git checkout main -- magi_agent/adk_bridge/control_plane.py magi_agent/adk_bridge/edit_retry_reflection.py magi_agent/adk_bridge/resilience_plugin.py magi_agent/adk_bridge/context_compaction.py`
and drop the new `tests/adk_bridge/test_*_context*.py` + `tests/packs/test_control_plane_*` files and
the Phase-5 additions in `magi_agent/packs/context.py`. The golden oracle (Phase 0) is independent
and need not be touched. No production default flips here — `build_default_plane` still registers the
same default-OFF controls.

## Hand-off to later phases

- **Phase 6 (first-party migration + flat-catalog flip)** consumes Phase 5's `apply_*` controls:
  - The dispatcher (Task 5.2) and `apply_*` methods let `build_default_plugin` stop hand-assembling
    controls and instead load them from bundled packs under `magi_agent/firstparty/packs/` — the
    `control_plane` `provides` entries each point at a control whose impl already takes only the typed
    context, so loading them from a manifest requires no further interface change.
  - The dual-load legacy `on_*` branches (and `_resolve_view_from_store`, the plugin
    `_default_state`s) are **removed in Phase 6** once the dispatcher is the sole path — they exist
    only so Phase 5 lands without flipping the live build site.
  - `EvidenceLedgerView` / `TurnSnapshot` / `PerInvocationState` / `CompactionCapability` are the
    stable capability surfaces a user `control_plane` pack authors against (capability parity for the
    §1 "no privilege" acceptance tests in Phase 7).
- **Phase 7 (acceptance)** asserts a user pack in `~/.magi/packs/` can add/override/remove a
  `control_plane` ref — relying on the typed-context-only signatures this phase established.
