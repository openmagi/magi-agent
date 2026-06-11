# Phase 2 — Typed-Context ABI + Primitive Registries + Dispatcher

> **For agentic workers:** REQUIRED SUB-SKILL: `superpowers:subagent-driven-development` or
> `superpowers:executing-plans`. Read `00-BLUEPRINT.md` first (it defines the §1 "no privilege"
> spec, the D1–D7 contract, the file-structure map with these exact module/type names, and the
> phase graph). This phase depends on Phase 1 (`02-phase1-manifest-discovery-loader.md`) for
> `PackManifest`/`ProvidesEntry`, and its control-plane-touching verify steps run the Phase 0
> golden regression (`tests/fixtures/neutral_runtime_golden/test_golden_regression.py`).

**Goal:** Build the **D5 typed-context ABI** and the **D3 registration ABI** of the neutral
microkernel. Implement `magi_agent/packs/context.py` (one narrow, typed, read-mostly context
dataclass per primitive type, plus a `ContextDispatcher` that builds a context from raw ADK
callback args and applies the impl's decision back to the ADK objects) and
`magi_agent/packs/registries.py` (one keyed `PrimitiveRegistry` with `register(ref, impl)` /
`resolve(ref)` / `list()` where **first-party and user share the identical path** — no privileged
tier). Each impl receives **only** its type's capabilities. Contexts reserve a `capabilities`
slot so they can later carry a hosted capability-set **without signature change**, but capability
gating is **not** added now (full-trust local, D6).

**Architecture:** The dispatcher is the seam that lets a registered impl (first-party or user)
observe/decide during the live ADK turn loop. For control-plane impls it preserves the existing
`ControlPlane` fan-out semantics exactly (first-deny-wins on before-tool, rewrite-mutates-args,
after-tool-first-non-None-wins, before-model accumulate-mutations) and honors `gate_position`
(default `after` — i.e. the impl runs at the **plugin level but must NOT bypass the agent-level
permission gate**, mirroring the existing `ControlPlane.register` footgun guard). This phase is
**greenfield + additive**: it does not yet rewire `build_default_plugin` or `App(plugins=…)` (that
is Phase 5/6) — it builds the ABI the later phases consume, and proves it round-trips against the
real ADK `ToolContext`/`CallbackContext`/`LlmRequest` shapes and the real `ToolDecision` type.

**Tech stack:** Python 3.12+, pydantic v2 (frozen, `extra="forbid"`, `populate_by_name=True` — match
`authoring/compiler.py:_MODEL_CONFIG`), Google ADK (`google.adk`), pytest, fake-model headless
(no API keys). All verify steps prefix pytest with `MAGI_CONFIG="$(mktemp -d)/config.toml"` to
avoid `~/.magi/config.toml` contamination (known env gotcha); runtime runs use
`LOCAL_DEV_MODEL_SENTINEL="local-dev"`.

---

## Ground truth grounded for this phase (re-grep — HEAD-802e707b snapshots may have drifted)

These were read at authoring time; **Step 1 of every modifying task re-greps before editing**.

- **`ToolDecision`** (`magi_agent/adk_bridge/control_plane.py` ~`:113`): frozen dataclass,
  `action: Literal["allow","deny","rewrite"] = "allow"`, `deny_result: dict|None`,
  `updated_args: dict|None`. **Reuse this type** — do not redefine a decision type.
- **`LoopControl` Protocol** (~`:133`) hooks: `on_before_tool(*, tool, args, tool_context) ->
  ToolDecision|None`, `on_after_tool(*, tool, args, tool_context, result) -> dict|None`,
  `on_before_model(*, callback_context, llm_request) -> None`, `on_after_agent(*, agent,
  callback_context) -> None`.
- **`ControlPlane` fan-out** (~`:267`–`:332`): before_tool → first non-allow decision: `deny`
  returns `deny_result`; `rewrite` does `args.clear(); args.update(updated_args)` and **continues**.
  after_tool → first non-None override wins. before_model → all run, mutations accumulate, returns
  None. after_agent → all observers run, return None.
- **Gate-ordering footgun** (~`:44`–`:66`, enforced at `ControlPlane.register` ~`:253`):
  `ControlPlanePlugin` runs at ADK **plugin level** (Step 1), the permission gate runs **agent
  level** (Step 2, `engine.py:_attach_gate_callback`). A plugin-level `on_before_tool` that returns
  deny/rewrite would short-circuit and bypass the gate. `ControlPlane.register` raises `ValueError`
  if a control overrides `on_before_tool`. **Our `gate_position` default (`"after"`) preserves this
  guard**: an impl with `before_tool` capability and `gate_position="after"` is rejected exactly
  like today; only an explicit `gate_position="before"` opt-in is allowed to deny/rewrite at the
  plugin level (a deliberate, loud opt-in for full-trust local).
- **ADK type shapes** (verified via `uv run python`): `ToolContext` and `CallbackContext` both
  expose `.state`, `.session`, `.invocation_id`, `.agent_name`, `.user_content`, `.actions`,
  `.user_id`. `LlmRequest` (pydantic) fields: `model, contents, config, live_connect_config,
  tools_dict, …`; `llm_request.config.tools` is the tool list a before-model impl clears.
- **`ToolRegistry` pattern** (`magi_agent/tools/registry.py`): `register(...)` raises on dup,
  `resolve(name)`, `replace(...)`, `unregister(...)`, `list_all()`. Our `PrimitiveRegistry`
  **aligns** with this `register/resolve/list` shape but is keyed by `(ptype, ref)` and supports
  `override` + `forbid` per the §1 spec.
- **Phase 1 dependency:** `magi_agent/packs/manifest.py` defines `PackManifest`, `ProvidesEntry`
  with at least: `ptype` (the 8 `provides` types), `ref` (str), `impl` (`"module:symbol"` str|None),
  `spec` (relpath str|None), and for ordered types `priority: int`, `phase: str|None`,
  `gate_position: Literal["before","after"]|None`. **If Phase 1's field names differ, adapt
  imports** — re-read `magi_agent/packs/manifest.py` in Task 2.5 Step 1.

> **Blueprint deviation note:** The blueprint file map lists `ValidatorCtx` / `EvidenceProducerCtx`
> as primitive types. The real validator/evidence layer (`harness/verifier_bus.py`) is
> metadata-heavy and its **live enforcement is Phase 3's job** (`cli/engine.py`). So Phase 2
> defines these two contexts as **neutral, self-contained primitives** (read-view + result/evidence
> emitter) that Phase 3 wires to the live enforce path. This phase does not import
> `verifier_bus.py`.

---

## Task 2.1: The primitive-type enum + capability tokens (shared vocabulary)

**Files:**
- Create: `magi_agent/packs/__init__.py` (empty package marker — only if Phase 1 didn't create it)
- Create: `magi_agent/packs/context.py` (this task seeds the top of the file)
- Create: `tests/packs/__init__.py` (empty)
- Test: `tests/packs/test_primitive_types.py`

- [ ] **Step 1: Ensure the package markers exist** (re-check; Phase 1 may have created `packs/`)

```bash
cd /Users/kevin/Desktop/claude_code/magi-agent/.worktrees/neutral-runtime
ls magi_agent/packs/__init__.py 2>/dev/null || : > magi_agent/packs/__init__.py
mkdir -p tests/packs && : > tests/packs/__init__.py
```

- [ ] **Step 2: Write the failing test**

```python
# tests/packs/test_primitive_types.py
from magi_agent.packs.context import PrimitiveType, Capability


def test_eight_provides_types_present():
    values = {t.value for t in PrimitiveType}
    assert values == {
        "tool",
        "callback",
        "validator",
        "harness",
        "control_plane",
        "evidence_producer",
        "recipe",
        "connector",
    }


def test_capabilities_are_frozenset_tokens():
    # Capabilities are opaque string tokens; full-trust local does not gate on them,
    # but contexts reserve a frozenset slot so a hosted build can later restrict.
    assert Capability.READ_SESSION in Capability.all_tokens()
    assert isinstance(Capability.all_tokens(), frozenset)
    assert Capability.DECIDE_TOOL in Capability.all_tokens()
```

- [ ] **Step 3: Run it, see it fail**

```bash
MAGI_CONFIG="$(mktemp -d)/config.toml" uv run pytest tests/packs/test_primitive_types.py -q
```
Expected: FAIL (`ModuleNotFoundError: magi_agent.packs.context`).

- [ ] **Step 4: Implement the top of `context.py`**

```python
# magi_agent/packs/context.py
"""D5 typed-context ABI + dispatcher for the neutral microkernel.

Each primitive impl receives ONLY a narrow, typed, read-mostly context exposing
exactly its type's capabilities. First-party and user impls receive the SAME
object (no privileged handle). Contexts carry a frozen ``capabilities`` set that
is NOT gated in full-trust local (D6) but reserves the seam for a hosted build
to restrict capability without changing any impl signature.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Literal

# Reuse the existing decision type — do NOT redefine.
from magi_agent.adk_bridge.control_plane import ToolDecision


class PrimitiveType(str, Enum):
    """The 8 unified ``provides`` types (D2)."""

    TOOL = "tool"
    CALLBACK = "callback"
    VALIDATOR = "validator"
    HARNESS = "harness"
    CONTROL_PLANE = "control_plane"
    EVIDENCE_PRODUCER = "evidence_producer"
    RECIPE = "recipe"
    CONNECTOR = "connector"


class Capability(str, Enum):
    """Opaque capability tokens. Full-trust local does not enforce these; a hosted
    build can later pass a restricted set per impl WITHOUT changing signatures."""

    READ_SESSION = "read_session"
    READ_EVIDENCE = "read_evidence"
    DECIDE_TOOL = "decide_tool"
    REWRITE_TOOL_ARGS = "rewrite_tool_args"
    OVERRIDE_TOOL_RESULT = "override_tool_result"
    MUTATE_MODEL_REQUEST = "mutate_model_request"
    REINJECT_MESSAGE = "reinject_message"
    CLEAR_TOOLS = "clear_tools"
    EMIT_VALIDATION = "emit_validation"
    EMIT_EVIDENCE = "emit_evidence"
    SPAWN_AGENT = "spawn_agent"

    @classmethod
    def all_tokens(cls) -> frozenset["Capability"]:
        return frozenset(cls)
```

- [ ] **Step 5: Run it, see it pass**

```bash
MAGI_CONFIG="$(mktemp -d)/config.toml" uv run pytest tests/packs/test_primitive_types.py -q
```
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add magi_agent/packs/__init__.py magi_agent/packs/context.py \
        tests/packs/__init__.py tests/packs/test_primitive_types.py
git commit -m "feat(packs): seed primitive-type enum and capability tokens for typed-context ABI"
```

---

## Task 2.2: Read-views (narrow, frozen snapshots of session + evidence)

**Files:**
- Modify: `magi_agent/packs/context.py` (append read-view dataclasses)
- Test: `tests/packs/test_read_views.py`

**Goal:** A `SessionReadView` and an `EvidenceReadView` — frozen, read-only projections so no impl
gets the ADK god-object. The dispatcher builds these from ADK `ToolContext`/`CallbackContext`.

- [ ] **Step 1: Write the failing test**

```python
# tests/packs/test_read_views.py
import pytest

from magi_agent.packs.context import SessionReadView, EvidenceReadView


def test_session_read_view_is_frozen_and_narrow():
    sv = SessionReadView(
        invocation_id="inv-1",
        agent_name="root",
        turn_index=3,
        state={"k": "v"},
    )
    assert sv.invocation_id == "inv-1"
    assert sv.turn_index == 3
    assert sv.get_state("k") == "v"
    assert sv.get_state("missing") is None
    with pytest.raises(AttributeError):
        sv.turn_index = 4  # frozen


def test_session_state_is_a_copy_not_a_live_handle():
    src = {"k": "v"}
    sv = SessionReadView(invocation_id="i", agent_name="a", turn_index=0, state=src)
    src["k"] = "MUTATED"
    assert sv.get_state("k") == "v"  # snapshot, not live alias


def test_evidence_read_view_lists_owed_and_present():
    ev = EvidenceReadView(present=("file_write",), owed=("test_run",))
    assert ev.has("file_write") is True
    assert ev.has("test_run") is False
    assert ev.owed == ("test_run",)
```

- [ ] **Step 2: Run, see it fail**

```bash
MAGI_CONFIG="$(mktemp -d)/config.toml" uv run pytest tests/packs/test_read_views.py -q
```
Expected: FAIL (`ImportError: cannot import name 'SessionReadView'`).

- [ ] **Step 3: Implement — append to `magi_agent/packs/context.py`**

```python
@dataclass(frozen=True)
class SessionReadView:
    """Narrow, frozen projection of the ADK session for read-only impl access."""

    invocation_id: str
    agent_name: str
    turn_index: int
    _state: Mapping[str, Any] = field(default_factory=dict)

    def __init__(self, *, invocation_id: str, agent_name: str, turn_index: int,
                 state: Mapping[str, Any] | None = None) -> None:
        object.__setattr__(self, "invocation_id", invocation_id)
        object.__setattr__(self, "agent_name", agent_name)
        object.__setattr__(self, "turn_index", turn_index)
        # snapshot copy — never alias the live ADK state dict
        object.__setattr__(self, "_state", dict(state or {}))

    def get_state(self, key: str, default: Any = None) -> Any:
        return self._state.get(key, default)

    def state_keys(self) -> tuple[str, ...]:
        return tuple(self._state.keys())


@dataclass(frozen=True)
class EvidenceReadView:
    """Read-only view of evidence already present and still owed this turn."""

    present: tuple[str, ...] = ()
    owed: tuple[str, ...] = ()

    def has(self, evidence_type: str) -> bool:
        return evidence_type in self.present
```

- [ ] **Step 4: Run, see it pass**

```bash
MAGI_CONFIG="$(mktemp -d)/config.toml" uv run pytest tests/packs/test_read_views.py -q
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add magi_agent/packs/context.py tests/packs/test_read_views.py
git commit -m "feat(packs): add frozen SessionReadView + EvidenceReadView read-views"
```

---

## Task 2.3: The 4 control-plane/callback contexts (`BeforeToolCtx` … `AfterAgentCtx`)

**Files:**
- Modify: `magi_agent/packs/context.py`
- Test: `tests/packs/test_control_plane_contexts.py`

**Goal:** Four narrow contexts, each exposing ONLY its hook's capabilities. `BeforeToolCtx`
carries `tool_name`, read-only `tool_args`, a `SessionReadView`, an `EvidenceReadView`, and a
`decide(action, reason=…, updated_args=…)` emitter producing a `ToolDecision`. `AfterToolCtx`
exposes the result read-view + `override(result)`. `BeforeModelCtx` exposes `reinject(role, text)`
+ `clear_tools()` helpers (mutation intents collected, applied by the dispatcher). `AfterAgentCtx`
is observe-only.

- [ ] **Step 1: Write the failing test**

```python
# tests/packs/test_control_plane_contexts.py
from magi_agent.adk_bridge.control_plane import ToolDecision
from magi_agent.packs.context import (
    BeforeToolCtx, AfterToolCtx, BeforeModelCtx, AfterAgentCtx,
    SessionReadView, EvidenceReadView,
)


def _sv():
    return SessionReadView(invocation_id="i", agent_name="a", turn_index=0, state={})


def test_before_tool_ctx_args_read_only_and_decide_allow_default():
    ctx = BeforeToolCtx(tool_name="FileEdit", tool_args={"path": "a.py"},
                        session=_sv(), evidence=EvidenceReadView())
    # read-only view of args
    assert ctx.tool_args["path"] == "a.py"
    assert ctx.tool_name == "FileEdit"
    # default decision (no call) is allow
    assert ctx.decision() == ToolDecision(action="allow")


def test_before_tool_ctx_deny_and_rewrite_produce_tool_decision():
    ctx = BeforeToolCtx(tool_name="Bash", tool_args={"cmd": "rm -rf /"},
                        session=_sv(), evidence=EvidenceReadView())
    ctx.decide("deny", reason="dangerous", deny_result={"error": "blocked"})
    d = ctx.decision()
    assert d.action == "deny"
    assert d.deny_result == {"error": "blocked"}

    ctx2 = BeforeToolCtx(tool_name="Bash", tool_args={"cmd": "ls"},
                         session=_sv(), evidence=EvidenceReadView())
    ctx2.decide("rewrite", updated_args={"cmd": "ls -la"})
    assert ctx2.decision().updated_args == {"cmd": "ls -la"}


def test_after_tool_ctx_override_is_first_non_none_semantics_input():
    ctx = AfterToolCtx(tool_name="Bash", tool_args={}, result={"ok": True}, session=_sv())
    assert ctx.override_result() is None  # no override by default
    ctx.override({"ok": False, "patched": True})
    assert ctx.override_result() == {"ok": False, "patched": True}


def test_before_model_ctx_collects_reinjects_and_clear_tools():
    ctx = BeforeModelCtx(session=_sv())
    assert ctx.pending_reinjections() == ()
    assert ctx.wants_clear_tools() is False
    ctx.reinject(role="user", text="wrap up now")
    ctx.clear_tools()
    assert ctx.pending_reinjections() == (("user", "wrap up now"),)
    assert ctx.wants_clear_tools() is True


def test_after_agent_ctx_is_observe_only():
    ctx = AfterAgentCtx(agent_name="root", session=_sv())
    assert ctx.agent_name == "root"
    # no decide/override/reinject surface
    assert not hasattr(ctx, "decide")
    assert not hasattr(ctx, "override")
```

- [ ] **Step 2: Run, see it fail**

```bash
MAGI_CONFIG="$(mktemp -d)/config.toml" uv run pytest tests/packs/test_control_plane_contexts.py -q
```
Expected: FAIL (`ImportError: cannot import name 'BeforeToolCtx'`).

- [ ] **Step 3: Implement — append to `magi_agent/packs/context.py`**

```python
class _ReadOnlyMapping(Mapping[str, Any]):
    """A read-only view over a dict (impls cannot mutate tool_args directly)."""

    def __init__(self, data: Mapping[str, Any]) -> None:
        self._data = dict(data)

    def __getitem__(self, key: str) -> Any:
        return self._data[key]

    def __iter__(self):
        return iter(self._data)

    def __len__(self) -> int:
        return len(self._data)


class BeforeToolCtx:
    """Capabilities: read tool_name/tool_args, read session+evidence, decide()."""

    capabilities: frozenset[Capability] = frozenset(
        {Capability.READ_SESSION, Capability.READ_EVIDENCE,
         Capability.DECIDE_TOOL, Capability.REWRITE_TOOL_ARGS}
    )

    def __init__(self, *, tool_name: str, tool_args: Mapping[str, Any],
                 session: SessionReadView, evidence: EvidenceReadView,
                 capabilities: frozenset[Capability] | None = None) -> None:
        self.tool_name = tool_name
        self.tool_args: Mapping[str, Any] = _ReadOnlyMapping(tool_args)
        self.session = session
        self.evidence = evidence
        if capabilities is not None:  # hosted may restrict; local passes full set
            self.capabilities = capabilities
        self._decision = ToolDecision(action="allow")

    def decide(self, action: Literal["allow", "deny", "rewrite"], *,
               reason: str | None = None,
               deny_result: dict[str, Any] | None = None,
               updated_args: dict[str, Any] | None = None) -> None:
        if action == "deny" and deny_result is None:
            deny_result = {"error": reason or "denied by control-plane impl"}
        self._decision = ToolDecision(
            action=action, deny_result=deny_result, updated_args=updated_args
        )

    def decision(self) -> ToolDecision:
        return self._decision


class AfterToolCtx:
    """Capabilities: read result, override() it."""

    capabilities: frozenset[Capability] = frozenset(
        {Capability.READ_SESSION, Capability.OVERRIDE_TOOL_RESULT}
    )

    def __init__(self, *, tool_name: str, tool_args: Mapping[str, Any],
                 result: Any, session: SessionReadView,
                 capabilities: frozenset[Capability] | None = None) -> None:
        self.tool_name = tool_name
        self.tool_args: Mapping[str, Any] = _ReadOnlyMapping(tool_args)
        self.result = result
        self.session = session
        if capabilities is not None:
            self.capabilities = capabilities
        self._override: dict[str, Any] | None = None

    def override(self, result: dict[str, Any]) -> None:
        self._override = result

    def override_result(self) -> dict[str, Any] | None:
        return self._override


class BeforeModelCtx:
    """Capabilities: mutate the outgoing model request via reinject()/clear_tools()."""

    capabilities: frozenset[Capability] = frozenset(
        {Capability.READ_SESSION, Capability.MUTATE_MODEL_REQUEST,
         Capability.REINJECT_MESSAGE, Capability.CLEAR_TOOLS}
    )

    def __init__(self, *, session: SessionReadView,
                 capabilities: frozenset[Capability] | None = None) -> None:
        self.session = session
        if capabilities is not None:
            self.capabilities = capabilities
        self._reinjections: list[tuple[str, str]] = []
        self._clear_tools = False

    def reinject(self, *, role: str, text: str) -> None:
        self._reinjections.append((role, text))

    def clear_tools(self) -> None:
        self._clear_tools = True

    def pending_reinjections(self) -> tuple[tuple[str, str], ...]:
        return tuple(self._reinjections)

    def wants_clear_tools(self) -> bool:
        return self._clear_tools


class AfterAgentCtx:
    """Observe-only: a completed turn. No decision surface."""

    capabilities: frozenset[Capability] = frozenset({Capability.READ_SESSION})

    def __init__(self, *, agent_name: str, session: SessionReadView,
                 capabilities: frozenset[Capability] | None = None) -> None:
        self.agent_name = agent_name
        self.session = session
        if capabilities is not None:
            self.capabilities = capabilities
```

- [ ] **Step 4: Run, see it pass**

```bash
MAGI_CONFIG="$(mktemp -d)/config.toml" uv run pytest tests/packs/test_control_plane_contexts.py -q
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add magi_agent/packs/context.py tests/packs/test_control_plane_contexts.py
git commit -m "feat(packs): add BeforeTool/AfterTool/BeforeModel/AfterAgent typed contexts"
```

---

## Task 2.4: The remaining contexts (`ToolCtx`, `ValidatorCtx`, `EvidenceProducerCtx`)

**Files:**
- Modify: `magi_agent/packs/context.py`
- Test: `tests/packs/test_tool_validator_evidence_contexts.py`

**Goal:** `ToolCtx` — what a `tool` impl receives (read session + args, emit progress, return a
result). `ValidatorCtx` — read the produced artifact/transcript view + `emit(passed, detail)`
(Phase 3 wires this to `cli/engine.py`'s live `required_validators` enforce path).
`EvidenceProducerCtx` — read session + `emit(evidence_type, payload)`.

- [ ] **Step 1: Write the failing test**

```python
# tests/packs/test_tool_validator_evidence_contexts.py
from magi_agent.packs.context import (
    ToolCtx, ValidatorCtx, EvidenceProducerCtx, SessionReadView,
)


def _sv():
    return SessionReadView(invocation_id="i", agent_name="a", turn_index=0, state={})


def test_tool_ctx_exposes_args_and_progress_sink():
    seen: list[str] = []
    ctx = ToolCtx(tool_name="Echo", tool_args={"msg": "hi"}, session=_sv(),
                  emit_progress=seen.append)
    assert ctx.tool_args["msg"] == "hi"
    ctx.progress("step 1")
    assert seen == ["step 1"]


def test_tool_ctx_progress_is_noop_when_no_sink():
    ctx = ToolCtx(tool_name="Echo", tool_args={}, session=_sv())
    ctx.progress("ignored")  # must not raise


def test_validator_ctx_emit_records_verdict():
    ctx = ValidatorCtx(ref="builtin:python_syntax",
                       artifact={"path": "a.py", "content": "x ="}, session=_sv())
    ctx.emit(passed=False, detail="SyntaxError: invalid syntax")
    v = ctx.verdict()
    assert v.passed is False
    assert v.detail == "SyntaxError: invalid syntax"
    assert v.ref == "builtin:python_syntax"


def test_validator_ctx_default_verdict_is_unset():
    ctx = ValidatorCtx(ref="r", artifact={}, session=_sv())
    assert ctx.verdict() is None


def test_evidence_producer_ctx_collects_emitted_evidence():
    ctx = EvidenceProducerCtx(session=_sv())
    ctx.emit(evidence_type="test_run", payload={"passed": 3, "failed": 0})
    items = ctx.emitted()
    assert items == ({"evidence_type": "test_run", "payload": {"passed": 3, "failed": 0}},)
```

- [ ] **Step 2: Run, see it fail**

```bash
MAGI_CONFIG="$(mktemp -d)/config.toml" uv run pytest tests/packs/test_tool_validator_evidence_contexts.py -q
```
Expected: FAIL (`ImportError: cannot import name 'ToolCtx'`).

- [ ] **Step 3: Implement — append to `magi_agent/packs/context.py`**

```python
@dataclass(frozen=True)
class ValidatorVerdict:
    ref: str
    passed: bool
    detail: str | None = None


class ToolCtx:
    """What a ``tool`` impl receives: read args + session, a progress sink."""

    capabilities: frozenset[Capability] = frozenset({Capability.READ_SESSION})

    def __init__(self, *, tool_name: str, tool_args: Mapping[str, Any],
                 session: SessionReadView,
                 emit_progress: Callable[[str], Any] | None = None,
                 capabilities: frozenset[Capability] | None = None) -> None:
        self.tool_name = tool_name
        self.tool_args: Mapping[str, Any] = _ReadOnlyMapping(tool_args)
        self.session = session
        if capabilities is not None:
            self.capabilities = capabilities
        self._emit_progress = emit_progress

    def progress(self, message: str) -> None:
        if self._emit_progress is not None:
            self._emit_progress(message)


class ValidatorCtx:
    """A ``validator`` impl reads the produced artifact and emits a verdict.

    Phase 3 wires ``verdict()`` into ``cli/engine.py``'s live ``required_validators``
    enforce path. Phase 2 keeps it self-contained (no verifier_bus coupling)."""

    capabilities: frozenset[Capability] = frozenset(
        {Capability.READ_SESSION, Capability.EMIT_VALIDATION}
    )

    def __init__(self, *, ref: str, artifact: Mapping[str, Any],
                 session: SessionReadView,
                 capabilities: frozenset[Capability] | None = None) -> None:
        self.ref = ref
        self.artifact: Mapping[str, Any] = _ReadOnlyMapping(artifact)
        self.session = session
        if capabilities is not None:
            self.capabilities = capabilities
        self._verdict: ValidatorVerdict | None = None

    def emit(self, *, passed: bool, detail: str | None = None) -> None:
        self._verdict = ValidatorVerdict(ref=self.ref, passed=passed, detail=detail)

    def verdict(self) -> ValidatorVerdict | None:
        return self._verdict


class EvidenceProducerCtx:
    """An ``evidence_producer`` impl reads session and emits evidence records."""

    capabilities: frozenset[Capability] = frozenset(
        {Capability.READ_SESSION, Capability.EMIT_EVIDENCE}
    )

    def __init__(self, *, session: SessionReadView,
                 capabilities: frozenset[Capability] | None = None) -> None:
        self.session = session
        if capabilities is not None:
            self.capabilities = capabilities
        self._emitted: list[dict[str, Any]] = []

    def emit(self, *, evidence_type: str, payload: Mapping[str, Any]) -> None:
        self._emitted.append({"evidence_type": evidence_type, "payload": dict(payload)})

    def emitted(self) -> tuple[dict[str, Any], ...]:
        return tuple(self._emitted)
```

- [ ] **Step 4: Run, see it pass**

```bash
MAGI_CONFIG="$(mktemp -d)/config.toml" uv run pytest tests/packs/test_tool_validator_evidence_contexts.py -q
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add magi_agent/packs/context.py tests/packs/test_tool_validator_evidence_contexts.py
git commit -m "feat(packs): add ToolCtx, ValidatorCtx, EvidenceProducerCtx typed contexts"
```

---

## Task 2.5: The keyed `PrimitiveRegistry` (same path for first-party + user)

**Files:**
- Create: `magi_agent/packs/registries.py`
- Test: `tests/packs/test_registries.py`

**Goal:** One keyed registry: `register(ref, impl, *, ptype, priority=0, phase=None,
gate_position=None, origin="user")`, `resolve(ref, *, ptype)`, `list(ptype=None)`. The §1 spec
requires: a user pack can **add**, **override** (replace a first-party ref), and **forbid** (remove)
any ref — and first-party uses the **identical** `register` path (no privileged tier, no
first-party-only flag that changes registration behavior). `origin` is metadata only — it MUST NOT
grant any capability or block override/forbid. Ordered types resolve in `(priority, registration
order)`.

- [ ] **Step 1: Confirm Phase 1's manifest field names match this registry's kwargs**

```bash
cd /Users/kevin/Desktop/claude_code/magi-agent/.worktrees/neutral-runtime
grep -n "class ProvidesEntry\|ptype\|gate_position\|priority\|phase\|impl\|spec\|ref" magi_agent/packs/manifest.py
```
This registry does **not** import `ProvidesEntry` (Phase 1's loader is what maps a `ProvidesEntry`
into `PrimitiveRegistry.register(...)` calls). This grep is a sanity check that the `register`
keyword names below (`ref`, `impl`, `ptype`, `priority`, `phase`, `gate_position`, `origin`) line up
with the `ProvidesEntry` field names so the Phase-1 loader can pass them through unchanged. If a
`ProvidesEntry` field is named differently (e.g. `type` vs `ptype`), note it so Phase 1's loader does
the rename at the call site — **do not** invent fields or add a `ProvidesEntry`-coupling import here.

- [ ] **Step 2: Write the failing test**

```python
# tests/packs/test_registries.py
import pytest

from magi_agent.packs.context import PrimitiveType
from magi_agent.packs.registries import PrimitiveRegistry, ForbiddenRefError


def _impl(tag):
    def fn(ctx):  # impls take only their typed ctx
        return tag
    return fn


def test_register_resolve_list_basic():
    reg = PrimitiveRegistry()
    reg.register("builtin:echo", _impl("a"), ptype=PrimitiveType.TOOL)
    assert reg.resolve("builtin:echo", ptype=PrimitiveType.TOOL)("x") == "a"
    assert [e.ref for e in reg.list(ptype=PrimitiveType.TOOL)] == ["builtin:echo"]


def test_user_can_override_a_first_party_ref_via_same_path():
    reg = PrimitiveRegistry()
    reg.register("builtin:gate", _impl("fp"), ptype=PrimitiveType.CONTROL_PLANE,
                 origin="first_party")
    # user override uses the SAME register call (no privileged path)
    reg.register("builtin:gate", _impl("user"), ptype=PrimitiveType.CONTROL_PLANE,
                 origin="user", override=True)
    assert reg.resolve("builtin:gate", ptype=PrimitiveType.CONTROL_PLANE)("c") == "user"


def test_register_dup_without_override_raises():
    reg = PrimitiveRegistry()
    reg.register("r", _impl("a"), ptype=PrimitiveType.TOOL)
    with pytest.raises(ValueError):
        reg.register("r", _impl("b"), ptype=PrimitiveType.TOOL)


def test_user_can_forbid_a_first_party_ref():
    reg = PrimitiveRegistry()
    reg.register("builtin:perm_gate", _impl("fp"), ptype=PrimitiveType.CONTROL_PLANE,
                 origin="first_party")
    reg.forbid("builtin:perm_gate", ptype=PrimitiveType.CONTROL_PLANE)
    with pytest.raises(ForbiddenRefError):
        reg.resolve("builtin:perm_gate", ptype=PrimitiveType.CONTROL_PLANE)
    assert reg.list(ptype=PrimitiveType.CONTROL_PLANE) == []


def test_ordered_types_sort_by_priority_then_registration_order():
    reg = PrimitiveRegistry()
    reg.register("c:low", _impl("low"), ptype=PrimitiveType.CONTROL_PLANE, priority=10)
    reg.register("c:high", _impl("high"), ptype=PrimitiveType.CONTROL_PLANE, priority=1)
    reg.register("c:tie", _impl("tie"), ptype=PrimitiveType.CONTROL_PLANE, priority=1)
    ordered = [e.ref for e in reg.list(ptype=PrimitiveType.CONTROL_PLANE)]
    assert ordered == ["c:high", "c:tie", "c:low"]


def test_origin_is_metadata_only_no_privilege():
    # A first_party entry is NOT protected from override/forbid — no privilege (§1).
    reg = PrimitiveRegistry()
    reg.register("x", _impl("fp"), ptype=PrimitiveType.TOOL, origin="first_party")
    reg.register("x", _impl("u"), ptype=PrimitiveType.TOOL, origin="user", override=True)
    assert reg.resolve("x", ptype=PrimitiveType.TOOL)("c") == "u"
```

- [ ] **Step 3: Run, see it fail**

```bash
MAGI_CONFIG="$(mktemp -d)/config.toml" uv run pytest tests/packs/test_registries.py -q
```
Expected: FAIL (`ModuleNotFoundError: magi_agent.packs.registries`).

- [ ] **Step 4: Implement `registries.py`**

```python
# magi_agent/packs/registries.py
"""Typed primitive registries (D3/D4). One keyed registry for all 8 provides types.

First-party and user impls register through the IDENTICAL path (§1 "no privilege"):
``origin`` is metadata only and never blocks override/forbid or grants capability.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from itertools import count
from typing import Any, Literal

from magi_agent.packs.context import PrimitiveType

Origin = Literal["first_party", "user"]
PrimitiveImpl = Callable[..., Any]


class ForbiddenRefError(KeyError):
    """Raised by ``resolve`` when a ref has been explicitly forbidden by a pack."""


@dataclass(frozen=True)
class RegistryEntry:
    ptype: PrimitiveType
    ref: str
    impl: PrimitiveImpl
    priority: int
    phase: str | None
    gate_position: Literal["before", "after"] | None
    origin: Origin
    _seq: int  # registration order tiebreaker (ascending)


class PrimitiveRegistry:
    """Keyed registry over ``(ptype, ref)``."""

    def __init__(self) -> None:
        self._entries: dict[tuple[PrimitiveType, str], RegistryEntry] = {}
        self._forbidden: set[tuple[PrimitiveType, str]] = set()
        self._seq = count()

    def register(self, ref: str, impl: PrimitiveImpl, *, ptype: PrimitiveType,
                 priority: int = 0, phase: str | None = None,
                 gate_position: Literal["before", "after"] | None = None,
                 origin: Origin = "user", override: bool = False) -> None:
        key = (ptype, ref)
        if key in self._entries and not override:
            raise ValueError(f"primitive already registered: {ptype.value}:{ref} "
                             f"(pass override=True to replace)")
        self._forbidden.discard(key)
        self._entries[key] = RegistryEntry(
            ptype=ptype, ref=ref, impl=impl, priority=priority, phase=phase,
            gate_position=gate_position, origin=origin, _seq=next(self._seq),
        )

    def forbid(self, ref: str, *, ptype: PrimitiveType) -> None:
        key = (ptype, ref)
        self._entries.pop(key, None)
        self._forbidden.add(key)

    def resolve(self, ref: str, *, ptype: PrimitiveType) -> PrimitiveImpl:
        key = (ptype, ref)
        if key in self._forbidden:
            raise ForbiddenRefError(f"{ptype.value}:{ref} forbidden by a loaded pack")
        entry = self._entries.get(key)
        if entry is None:
            raise KeyError(f"unknown primitive: {ptype.value}:{ref}")
        return entry.impl

    def resolve_entry(self, ref: str, *, ptype: PrimitiveType) -> RegistryEntry:
        key = (ptype, ref)
        if key in self._forbidden:
            raise ForbiddenRefError(f"{ptype.value}:{ref} forbidden by a loaded pack")
        entry = self._entries.get(key)
        if entry is None:
            raise KeyError(f"unknown primitive: {ptype.value}:{ref}")
        return entry

    def list(self, *, ptype: PrimitiveType | None = None) -> list[RegistryEntry]:
        entries = [e for e in self._entries.values()
                   if ptype is None or e.ptype is ptype]
        # ordered types are sorted by (priority asc, registration order asc);
        # unordered types share the same stable key (priority defaults to 0).
        return sorted(entries, key=lambda e: (e.priority, e._seq))
```

- [ ] **Step 5: Run, see it pass**

```bash
MAGI_CONFIG="$(mktemp -d)/config.toml" uv run pytest tests/packs/test_registries.py -q
```
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add magi_agent/packs/registries.py tests/packs/test_registries.py
git commit -m "feat(packs): add keyed PrimitiveRegistry with shared first-party/user path"
```

---

## Task 2.6: `ContextDispatcher` — build contexts from ADK args + apply decisions

**Files:**
- Modify: `magi_agent/packs/context.py` (append the dispatcher)
- Test: `tests/packs/test_dispatcher.py`

**Goal:** A `ContextDispatcher` that, given a `PrimitiveRegistry` and raw ADK callback args
(`tool`, `tool_args`, `tool_context`, `callback_context`, `llm_request`, `result`), (a) builds the
matching typed context, (b) invokes the registered control_plane/callback impls in registry order,
and (c) applies the collected decision back to the ADK objects honoring the existing `ControlPlane`
fan-out semantics: **before_tool** first-deny-wins (return `deny_result`), rewrite mutates `args`
in place and continues; **after_tool** first non-None override wins; **before_model** all run,
reinjections appended to `llm_request.contents`, `clear_tools()` clears `llm_request.config.tools`.
The dispatcher honors `gate_position`: an impl that *decides* on before_tool with
`gate_position="after"` (the default) is **rejected at register-validate time** exactly like
`ControlPlane.register` (so the agent-level permission gate is never bypassed); only an explicit
`gate_position="before"` opt-in may deny/rewrite at the plugin level.

- [ ] **Step 1: Re-grep the real `ControlPlane._before_tool` semantics to mirror them exactly**

```bash
cd /Users/kevin/Desktop/claude_code/magi-agent/.worktrees/neutral-runtime
grep -n "_before_tool\|_after_tool\|_before_model\|args.clear()\|args.update\|first non-None" \
  magi_agent/adk_bridge/control_plane.py
```
Confirm: rewrite does `args.clear(); args.update(updated_args)` and **continues**; deny returns
`deny_result` immediately; after_tool returns the **first** non-None override. Mirror these.

- [ ] **Step 2: Write the failing test**

```python
# tests/packs/test_dispatcher.py
import pytest

from magi_agent.packs.context import (
    PrimitiveType, ContextDispatcher, GatePositionViolation,
)
from magi_agent.packs.registries import PrimitiveRegistry


class _FakeLlmRequest:
    """Minimal stand-in for ADK LlmRequest (contents list + config.tools)."""
    class _Cfg:
        def __init__(self):
            self.tools = ["FileEdit", "Bash"]

    def __init__(self):
        self.contents = []
        self.config = self._Cfg()


class _FakeToolContext:
    invocation_id = "inv-9"
    agent_name = "root"

    def __init__(self):
        self.state = {"turn": 2}


def test_before_tool_first_deny_wins_and_returns_deny_result():
    reg = PrimitiveRegistry()

    def allow_impl(ctx):
        ctx.decide("allow")

    def deny_impl(ctx):
        ctx.decide("deny", reason="loop guard", deny_result={"error": "blocked"})

    reg.register("c:allow", allow_impl, ptype=PrimitiveType.CONTROL_PLANE,
                 priority=1, gate_position="before")
    reg.register("c:deny", deny_impl, ptype=PrimitiveType.CONTROL_PLANE,
                 priority=2, gate_position="before")
    d = ContextDispatcher(reg)
    args = {"path": "a.py"}
    out = d.dispatch_before_tool(tool_name="FileEdit", args=args,
                                 tool_context=_FakeToolContext())
    assert out == {"error": "blocked"}


def test_before_tool_rewrite_mutates_args_in_place_and_continues():
    reg = PrimitiveRegistry()

    def rewrite_impl(ctx):
        ctx.decide("rewrite", updated_args={"cmd": "ls -la"})

    reg.register("c:rw", rewrite_impl, ptype=PrimitiveType.CONTROL_PLANE,
                 gate_position="before")
    d = ContextDispatcher(reg)
    args = {"cmd": "ls"}
    out = d.dispatch_before_tool(tool_name="Bash", args=args,
                                 tool_context=_FakeToolContext())
    assert out is None
    assert args == {"cmd": "ls -la"}  # mutated in place


def test_gate_position_after_rejects_deciding_before_tool_impl():
    # default gate_position 'after' must NOT allow plugin-level deny (gate preserved)
    reg = PrimitiveRegistry()

    def deny_impl(ctx):
        ctx.decide("deny")

    reg.register("c:bad", deny_impl, ptype=PrimitiveType.CONTROL_PLANE)  # gate_position None -> after
    d = ContextDispatcher(reg)
    with pytest.raises(GatePositionViolation):
        d.dispatch_before_tool(tool_name="Bash", args={"cmd": "x"},
                               tool_context=_FakeToolContext())


def test_after_tool_first_non_none_override_wins():
    reg = PrimitiveRegistry()

    def noop_impl(ctx):
        return None

    def patch_impl(ctx):
        ctx.override({"ok": False})

    reg.register("c:noop", noop_impl, ptype=PrimitiveType.CONTROL_PLANE, priority=1)
    reg.register("c:patch", patch_impl, ptype=PrimitiveType.CONTROL_PLANE, priority=2)
    d = ContextDispatcher(reg)
    out = d.dispatch_after_tool(tool_name="Bash", args={}, result={"ok": True},
                                tool_context=_FakeToolContext())
    assert out == {"ok": False}


def test_before_model_reinject_appends_and_clear_tools_clears():
    reg = PrimitiveRegistry()

    def wrapup_impl(ctx):
        ctx.reinject(role="user", text="wrap up")
        ctx.clear_tools()

    reg.register("c:wrap", wrapup_impl, ptype=PrimitiveType.CONTROL_PLANE)
    d = ContextDispatcher(reg)
    req = _FakeLlmRequest()
    d.dispatch_before_model(callback_context=_FakeToolContext(), llm_request=req)
    assert req.contents == [{"role": "user", "content": "wrap up"}]
    assert req.config.tools == []
```

- [ ] **Step 3: Run, see it fail**

```bash
MAGI_CONFIG="$(mktemp -d)/config.toml" uv run pytest tests/packs/test_dispatcher.py -q
```
Expected: FAIL (`ImportError: cannot import name 'ContextDispatcher'`).

- [ ] **Step 4: Implement — append to `magi_agent/packs/context.py`**

```python
class GatePositionViolation(ValueError):
    """A before_tool deciding impl ran with gate_position 'after' (would bypass the
    agent-level permission gate). Mirrors ControlPlane.register's footgun guard."""


def _build_session_view(adk_ctx: Any) -> SessionReadView:
    """Build a narrow read-view from an ADK ToolContext/CallbackContext duck-type."""
    state = getattr(adk_ctx, "state", None)
    state_map: Mapping[str, Any]
    try:
        state_map = dict(state) if state is not None else {}
    except TypeError:
        state_map = {}
    turn = state_map.get("turn", 0)
    return SessionReadView(
        invocation_id=str(getattr(adk_ctx, "invocation_id", "") or ""),
        agent_name=str(getattr(adk_ctx, "agent_name", "") or ""),
        turn_index=int(turn) if isinstance(turn, int) else 0,
        state=state_map,
    )


class ContextDispatcher:
    """Builds typed contexts from raw ADK args and applies impl decisions back to ADK.

    Mirrors the existing ``ControlPlane`` fan-out exactly so Phase 5 can swap the
    hand-assembled controls for registry-loaded impls with no behavior change.
    """

    def __init__(self, registry: Any) -> None:
        self._reg = registry

    def _control_entries(self) -> list[Any]:
        return self._reg.list(ptype=PrimitiveType.CONTROL_PLANE)

    def dispatch_before_tool(self, *, tool_name: str, args: dict[str, Any],
                             tool_context: Any,
                             evidence: EvidenceReadView | None = None) -> dict[str, Any] | None:
        session = _build_session_view(tool_context)
        ev = evidence or EvidenceReadView()
        for entry in self._control_entries():
            ctx = BeforeToolCtx(tool_name=tool_name, tool_args=args,
                                session=session, evidence=ev)
            entry.impl(ctx)
            decision = ctx.decision()
            if decision.action == "allow":
                continue
            # gate_position guard: a deciding before_tool impl MUST opt into
            # plugin-level execution ('before'); the default ('after'/None) preserves
            # the agent-level permission gate by forbidding the decision.
            if entry.gate_position != "before":
                raise GatePositionViolation(
                    f"control_plane:{entry.ref} decided '{decision.action}' on before_tool "
                    f"with gate_position={entry.gate_position!r}; set gate_position='before' "
                    f"to run at plugin level (this bypasses the permission gate — opt in "
                    f"explicitly) or move the decision to a later hook."
                )
            if decision.action == "deny":
                return decision.deny_result
            if decision.action == "rewrite" and decision.updated_args is not None:
                args.clear()
                args.update(decision.updated_args)
                # continue (no short-circuit on rewrite) — mirrors ControlPlane
        return None

    def dispatch_after_tool(self, *, tool_name: str, args: dict[str, Any],
                            result: Any, tool_context: Any) -> dict[str, Any] | None:
        session = _build_session_view(tool_context)
        for entry in self._control_entries():
            ctx = AfterToolCtx(tool_name=tool_name, tool_args=args,
                               result=result, session=session)
            entry.impl(ctx)
            override = ctx.override_result()
            if override is not None:
                return override  # first non-None wins
        return None

    def dispatch_before_model(self, *, callback_context: Any, llm_request: Any) -> None:
        session = _build_session_view(callback_context)
        for entry in self._control_entries():
            ctx = BeforeModelCtx(session=session)
            entry.impl(ctx)
            for role, text in ctx.pending_reinjections():
                llm_request.contents.append({"role": role, "content": text})
            if ctx.wants_clear_tools():
                cfg = getattr(llm_request, "config", None)
                if cfg is not None and getattr(cfg, "tools", None) is not None:
                    cfg.tools = []
        return None

    def dispatch_after_agent(self, *, agent_name: str, callback_context: Any) -> None:
        session = _build_session_view(callback_context)
        for entry in self._control_entries():
            ctx = AfterAgentCtx(agent_name=agent_name, session=session)
            entry.impl(ctx)
        return None
```

Then extend `__all__` at the bottom of `context.py` (create it if absent):

```python
__all__ = [
    "PrimitiveType", "Capability",
    "SessionReadView", "EvidenceReadView",
    "BeforeToolCtx", "AfterToolCtx", "BeforeModelCtx", "AfterAgentCtx",
    "ToolCtx", "ValidatorCtx", "ValidatorVerdict", "EvidenceProducerCtx",
    "ContextDispatcher", "GatePositionViolation",
]
```

- [ ] **Step 5: Run, see it pass**

```bash
MAGI_CONFIG="$(mktemp -d)/config.toml" uv run pytest tests/packs/test_dispatcher.py -q
```
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add magi_agent/packs/context.py tests/packs/test_dispatcher.py
git commit -m "feat(packs): add ContextDispatcher with ControlPlane-parity fan-out + gate guard"
```

---

## Task 2.7: Dispatcher round-trips against the REAL ADK + real `ToolDecision`

**Files:**
- Test: `tests/packs/test_dispatcher_adk_roundtrip.py`

**Goal:** Prove the dispatcher works against the **real** ADK `LlmRequest`/`ToolContext` shapes
(not just the fakes) and that its before-tool decision is a real `ToolDecision`, so Phase 5 can
trust the ABI. No API keys — ADK type construction only.

- [ ] **Step 1: Re-grep how existing tests build a real ADK `LlmRequest`/`ToolContext`**

```bash
cd /Users/kevin/Desktop/claude_code/magi-agent/.worktrees/neutral-runtime
grep -rn "LlmRequest(\|ToolContext(\|CallbackContext(\|InvocationContext(" \
  tests/adk_bridge/ magi_agent/cli/tests/ | head -20
```
Reuse the real construction these tests use; if a constructor needs an `InvocationContext`, copy
that setup. If construction is heavyweight, keep the `tool_context` duck-typed (the dispatcher only
reads `.state`/`.invocation_id`/`.agent_name`) and use the **real `LlmRequest`** for the
before-model assertion (that is the load-bearing ADK contract).

- [ ] **Step 2: Write the test (passes once impl from 2.6 exists)**

```python
# tests/packs/test_dispatcher_adk_roundtrip.py
from google.adk.models.llm_request import LlmRequest

from magi_agent.adk_bridge.control_plane import ToolDecision
from magi_agent.packs.context import (
    PrimitiveType, ContextDispatcher, BeforeToolCtx,
)
from magi_agent.packs.registries import PrimitiveRegistry


class _DuckCtx:
    invocation_id = "inv-real"
    agent_name = "root"
    state = {"turn": 1}


def test_before_tool_ctx_emits_real_tool_decision_type():
    ctx = BeforeToolCtx(tool_name="Bash", tool_args={"cmd": "ls"},
                        session=__import__("magi_agent.packs.context",
                                           fromlist=["SessionReadView"]).SessionReadView(
                            invocation_id="i", agent_name="a", turn_index=0, state={}),
                        evidence=__import__("magi_agent.packs.context",
                                            fromlist=["EvidenceReadView"]).EvidenceReadView())
    ctx.decide("deny", reason="x")
    assert isinstance(ctx.decision(), ToolDecision)


def test_before_model_clears_real_llm_request_tools_and_reinjects():
    reg = PrimitiveRegistry()

    def wrapup(ctx):
        ctx.reinject(role="user", text="finish")
        ctx.clear_tools()

    reg.register("c:wrap", wrapup, ptype=PrimitiveType.CONTROL_PLANE)
    # Real ADK LlmRequest (pydantic). config defaults to a GenerateContentConfig.
    req = LlmRequest(model="local-dev")
    # ensure config + tools attribute exists in the ADK shape we mutate
    if req.config is None:
        from google.genai import types as genai_types
        req.config = genai_types.GenerateContentConfig()
    req.config.tools = ["placeholder"]
    d = ContextDispatcher(reg)
    d.dispatch_before_model(callback_context=_DuckCtx(), llm_request=req)
    assert req.config.tools == []
    assert req.contents and req.contents[-1] == {"role": "user", "content": "finish"}
```

- [ ] **Step 3: Run, see it pass** (impl already exists from Task 2.6)

```bash
MAGI_CONFIG="$(mktemp -d)/config.toml" uv run pytest tests/packs/test_dispatcher_adk_roundtrip.py -q
```
Expected: PASS. If the real `LlmRequest.config` shape differs from this snapshot (re-grep Step 1),
adapt the `config`/`tools` access to match the installed ADK — the dispatcher reads
`llm_request.config.tools`, so the test must construct that path on the real object.

- [ ] **Step 4: Commit**

```bash
git add tests/packs/test_dispatcher_adk_roundtrip.py
git commit -m "test(packs): dispatcher round-trips against real ADK LlmRequest + ToolDecision"
```

---

## Task 2.8: Golden-regression guard (no control-plane behavior drift)

**Files:** none created — verification only.

**Goal:** This phase is greenfield/additive and does **not** modify the 6 control-plane LoopControls
or `build_default_plugin`/`App(plugins=…)`. Confirm the Phase-0 oracle is still green (i.e. nothing
in this phase accidentally imported-with-side-effects or mutated control-plane behavior).

- [ ] **Step 1: Run the Phase-0 golden regression**

```bash
cd /Users/kevin/Desktop/claude_code/magi-agent/.worktrees/neutral-runtime
MAGI_CONFIG="$(mktemp -d)/config.toml" uv run pytest \
  tests/fixtures/neutral_runtime_golden/test_golden_regression.py -q
```
Expected: PASS (4 goldens unchanged). **A diff here = a behavior change to review; this phase must
not produce one. Regenerate via `python -m tests.fixtures.neutral_runtime_golden.capture --write`
only if the change is intentional — for Phase 2 it must NOT be.**

- [ ] **Step 2: Run the full new packs suite + the existing control-plane suite together**

```bash
MAGI_CONFIG="$(mktemp -d)/config.toml" uv run pytest tests/packs/ \
  tests/adk_bridge/test_control_plane.py -q
```
Expected: all PASS. (Asserts the new ABI imports cleanly alongside the live control plane.)

- [ ] **Step 3: Commit a phase-complete marker** (only if anything changed; otherwise skip)

```bash
git commit --allow-empty -m "test(packs): phase 2 ABI green against phase-0 golden oracle"
```

---

## Acceptance criteria (Phase 2 done)

- [ ] `MAGI_CONFIG="$(mktemp -d)/config.toml" uv run pytest tests/packs/ -q` is green, headless, **no API keys**.
- [ ] `magi_agent/packs/context.py` defines a narrow, frozen/read-mostly typed context per
  primitive type: `BeforeToolCtx`, `AfterToolCtx`, `BeforeModelCtx`, `AfterAgentCtx`, `ToolCtx`,
  `ValidatorCtx`, `EvidenceProducerCtx` — each exposing ONLY its type's capabilities.
- [ ] Each context carries a `capabilities: frozenset[Capability]` slot accepted via constructor
  **without** changing any impl signature; **no gating is enforced** (D6 full-trust local).
- [ ] `magi_agent/packs/registries.py` `PrimitiveRegistry` supports `register(ref, impl, …)` /
  `resolve(ref, …)` / `list(…)`; first-party and user use the **identical** `register` path;
  `origin` is metadata-only (no privilege); `override=True` and `forbid()` work for any ref (§1).
- [ ] `ContextDispatcher` mirrors `ControlPlane` fan-out exactly (first-deny-wins, rewrite-mutates-
  args-in-place-and-continues, after-tool-first-non-None-wins, before-model accumulate + clear-tools)
  and **preserves the permission gate**: a deciding before-tool impl with default `gate_position`
  raises `GatePositionViolation`; only explicit `gate_position="before"` may deny/rewrite.
- [ ] Dispatcher round-trips against the **real** ADK `LlmRequest` and emits the **real**
  `ToolDecision` type (Task 2.7).
- [ ] The Phase-0 golden regression is unchanged (Task 2.8) — no control-plane behavior drift.

## Rollback

Phase 2 is purely additive under `magi_agent/packs/{context.py,registries.py}` and `tests/packs/`.
Nothing in the live runtime imports it yet (Phase 5/6 do the wiring). Revert =
`git revert` the Task 2.1–2.8 commits, or delete `magi_agent/packs/context.py`,
`magi_agent/packs/registries.py`, and `tests/packs/`. No existing behavior depends on this phase,
so rollback cannot regress the control plane (Task 2.8 proves the oracle is untouched).

## Hand-off to later phases

- **Phase 3 (validator vertical slice)** consumes `ValidatorCtx`/`ValidatorVerdict` and registers a
  validator impl via `PrimitiveRegistry.register(..., ptype=PrimitiveType.VALIDATOR)`, then wires
  `verdict()` into the **already-live** `required_validators` enforce point in `cli/engine.py`
  (re-grep `evidence_requirements`/`required_validators` ~`:2138`). The Phase-2 contexts are
  deliberately verifier_bus-free so Phase 3 owns the engine coupling.
- **Phase 4 (easy types)** registers `tool` / `evidence_producer` / `recipe` / `connector` /
  `harness` / `callback` impls through the same `PrimitiveRegistry` + their typed contexts
  (`ToolCtx`, `EvidenceProducerCtx`, `BeforeModelCtx`/`AfterAgentCtx` for callback).
- **Phase 5 (control-plane migration)** replaces the 6 hand-assembled LoopControls in
  `build_default_plane`/`build_default_plugin` with registry-loaded impls invoked through
  `ContextDispatcher`. Because the dispatcher mirrors `ControlPlane` fan-out and the `gate_position`
  guard mirrors `ControlPlane.register`, the Phase-0 golden regression must stay green per migrated
  control; an intentional change regenerates the affected golden via `capture --write` with the diff
  called out (per `01-phase0-golden-oracle.md` hand-off).
- **Phase 6** flips `build_default_plugin`/`App(plugins=…)` to pack-loaded registries; it relies on
  `PrimitiveRegistry.list(ptype=CONTROL_PLANE)` ordering (priority, then registration order) being
  the ordered fan-out the dispatcher consumes, and on `origin` carrying **no** privilege so the §1
  "no first-party tier" assertions in Phase 7 hold.
- **Hosted extension seam:** the `capabilities` constructor slot on every context is the agreed
  point where a hosted build passes a restricted capability-set; OSS local always passes the full
  set (default), so impl signatures never change between OSS and hosted.
