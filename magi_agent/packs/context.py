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


@dataclass(frozen=True)
class ProducerSpec:
    """Declarative descriptor for an ``evidence_producer`` primitive.

    Frozen, capability-parity data: the evidence type it emits, the public ref it
    contributes to the live ``observed_public_refs`` set, and the surfaces that
    may emit it. ``public_ref`` MUST carry a recognized public-ref prefix
    (``evidence:``/``verifier:``/``receipt:sha256:``/``sha256:``) so it reaches
    the live ``harness/verifier_bus`` enforce path.
    """

    evidence_type: str
    public_ref: str
    producer_surfaces: tuple[str, ...] = ()


@dataclass(frozen=True)
class ConnectorSpec:
    """Declarative descriptor for a ``connector`` (MCP) primitive: a server ref
    plus the ``ToolManifest``s it projects into the live tool registry."""

    server_ref: str
    tool_manifests: tuple[Any, ...] = ()
    readonly: bool = True


@dataclass(frozen=True)
class ToolProvideContext:
    """D5 typed context a ``tool`` impl receives: a single ``register`` capability
    that accepts a ``ToolManifest``. No god-object, no first-party-only kwarg."""

    register: Callable[[Any], None]


@dataclass(frozen=True)
class EvidenceProducerProvideContext:
    """D5 typed context an ``evidence_producer`` impl receives: ``register(ref, spec)``."""

    register: Callable[[str, ProducerSpec], None]


@dataclass(frozen=True)
class RecipeProvideContext:
    """D5 typed context a ``recipe`` impl receives: ``register(ref, manifest)``."""

    register: Callable[[str, Any], None]


@dataclass(frozen=True)
class ConnectorProvideContext:
    """D5 typed context a ``connector`` impl receives: ``register(ref, spec)``."""

    register: Callable[[str, ConnectorSpec], None]


@dataclass(frozen=True)
class HarnessProvideContext:
    """D5 typed context a ``harness`` impl receives: ``register(ref, pack)``."""

    register: Callable[[str, Any], None]


@dataclass(frozen=True)
class CallbackProvideContext:
    """D5 typed context a ``callback`` impl receives: ``register(manifest, handler)``."""

    register: Callable[[Any, Any], None]


# ---------------------------------------------------------------------------
# Phase 5 (S-0): shared control-plane seam surface.
#
# The four hard control-plane seams (S-A evidence ledger, S-B turn snapshot +
# fork runner, S-C per-invocation mutable state, S-D compaction) each need a
# capability the per-hook ctx classes above don't carry. ControlPlaneContext is
# the SHARED carrier all four reuse: first-party and user ``control_plane`` impls
# receive the identical object (the §1 "no privilege" keystone). The dispatcher
# fills the relevant fields in per seam; everything else stays ``None``.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EvidenceLedgerView:
    """S-A read-only view: the per-turn evidence ledger + open controls.

    Surfaces exactly the two reads ``GaConstraintReinjectionControl`` needs
    (``ledger_for_turn`` / ``open_controls_for_turn`` already resolved for the
    active turn) WITHOUT handing the control the mutable receipt-store object. A
    user pack receives the same view, so it can author an equivalent reminder
    control with zero privileged access.
    """

    ledger: Any  # EvidenceLedger | None (already resolved for this turn)
    open_controls: tuple[Any, ...]  # resolved open controls for the turn
    contract_required: Any  # RequiredDeliverableEvidence | None
    agent_role: str = "general"


@dataclass(frozen=True)
class TurnSnapshot:
    """S-B pre-extracted typed snapshot of the just-finished turn.

    The runtime extracts this from the ADK session/event tree ONCE and places it
    on the context, so a control never has to traverse ``session.events`` itself
    (the privileged nested traversal). Mirrors the legacy
    ``_SelfReviewTurnSnapshot``.
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
        # keyed (invocation_id, name) -> scalar value
        self._store: dict[tuple[str, str], Any] = {}
        # opaque per-invocation objects (e.g. a loop detector) keyed by id then name
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
    def get_object(self, invocation_id: str, name: str, factory: Callable[[], Any]) -> Any:
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


@dataclass(frozen=True)
class ControlPlaneContext:
    """The SHARED Phase-5 seam carrier for ``control_plane`` impls.

    Every first-party LoopControl (and every user-authored control_plane impl)
    receives this identical object — no privileged receipt-store handle, no
    god-object, no per-control private mutable ``self.*`` state. Each seam reads
    only the field it needs; the dispatcher populates the relevant field(s) and
    leaves the rest ``None`` (built via ``minimal`` for control-isolation tests).

    - ``evidence``       — S-A resolved :class:`EvidenceLedgerView`.
    - ``turn_snapshot``  — S-B pre-extracted :class:`TurnSnapshot`.
    - ``fork_runner``    — S-B public ForkRunner capability (full-trust local).
    - ``per_invocation`` — S-C runtime-owned :class:`PerInvocationState`.
    - ``compaction``     — S-D narrowed compaction-decision capability.
    """

    evidence: EvidenceLedgerView | None = None
    turn_snapshot: TurnSnapshot | None = None
    fork_runner: Any | None = None        # public ForkRunner capability (full-trust)
    per_invocation: PerInvocationState | None = None
    compaction: Any | None = None         # narrowed compaction-decision capability

    @classmethod
    def minimal(cls, **overrides: Any) -> "ControlPlaneContext":
        """Build a context with only the supplied seam fields populated.

        Used by control unit tests and per-control isolation. The live dispatcher
        (S-A…S-D integration, Phase 6) builds the full context from ADK args.
        """
        return cls(**overrides)


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


__all__ = [
    "PrimitiveType", "Capability",
    "SessionReadView", "EvidenceReadView",
    "BeforeToolCtx", "AfterToolCtx", "BeforeModelCtx", "AfterAgentCtx",
    "ToolCtx", "ValidatorCtx", "ValidatorVerdict", "EvidenceProducerCtx",
    "ControlPlaneContext", "EvidenceLedgerView", "TurnSnapshot",
    "PerInvocationState",
    "ContextDispatcher", "GatePositionViolation",
    "ProducerSpec", "ConnectorSpec",
    "ToolProvideContext", "EvidenceProducerProvideContext", "RecipeProvideContext",
    "ConnectorProvideContext", "HarnessProvideContext", "CallbackProvideContext",
]
