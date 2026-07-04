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
from typing import TYPE_CHECKING, Any, Literal

# Reuse the existing decision type — do NOT redefine. Imported lazily at the two
# instantiation sites (and under TYPE_CHECKING for annotations) because
# ``adk_bridge.control_plane`` top-level-imports ``google.adk.plugins.base_plugin``
# (its ``ControlPlanePlugin`` subclasses the ADK ``BasePlugin``). A top-level
# import here dragged the entire ``google.adk`` runtime into default runtime
# construction (pack loading imports this module), tripping the
# import-boundary probe. ``ToolDecision`` itself is a light frozen dataclass; the
# lazy import keeps the type identical while deferring the heavy ADK import to the
# rare control-plane decision path.
if TYPE_CHECKING:
    from magi_agent.adk_bridge.control_plane import ToolDecision


class PrimitiveType(str, Enum):
    """The unified ``provides`` types (D2) + the 3 Pack-C policy types."""

    TOOL = "tool"
    CALLBACK = "callback"
    VALIDATOR = "validator"
    HARNESS = "harness"
    CONTROL_PLANE = "control_plane"
    EVIDENCE_PRODUCER = "evidence_producer"
    RECIPE = "recipe"
    CONNECTOR = "connector"
    # Declarative scope-label type: a namespaced agent role (D2 extension).
    ROLE = "role"
    # Pack C policy types (decomposed-subsystem policies; same loader, no privilege)
    LOOP_POLICY = "loop_policy"
    SCHEDULE_POLICY = "schedule_policy"
    MEMORY_STRATEGY = "memory_strategy"


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


class CapabilityError(PermissionError):
    """A capability-bearing context method was called without its required token.

    Raised by the gated methods below when ``self.capabilities`` is missing the
    token that method needs. DEFENSE-IN-DEPTH, NOT ISOLATION: this is an
    ABI-surface contract that narrows what a pack can do THROUGH the typed
    context surface (decide/override/reinject/clear_tools/emit). It is NOT a true
    isolation boundary -- a malicious impl can still ``import os``, touch the
    filesystem, or reach the network directly. Real hosted isolation needs
    process/container sandboxing (a separate effort). The check makes an
    honest-but-overreaching pack fail closed instead of silently exceeding its
    declared role.

    Because the DEFAULT capability sets on each context already include that
    context's own tokens, a context built with defaults NEVER raises -- the OFF
    path (no ``capabilities=`` passed) stays byte-identical to before.
    """


# Per-primitive RESTRICTED capability policy (for untrusted/user packs). Each
# entry grants ONLY the tokens that primitive type legitimately needs through the
# typed surface; everything else is withheld so an overreaching pack fails closed.
# (control_plane et al. get a conservative READ-only set; threading per-hook
# control_plane restriction through ContextDispatcher is a follow-up -- see the
# TODO at the dispatcher.)
_RESTRICTED_CAPABILITIES: "dict[PrimitiveType, frozenset[Capability]]" = {
    # a tool impl only reads session through ToolCtx (progress needs no token)
    PrimitiveType.TOOL: frozenset({Capability.READ_SESSION}),
    # a validator reads session + emits exactly one verdict
    PrimitiveType.VALIDATOR: frozenset(
        {Capability.READ_SESSION, Capability.EMIT_VALIDATION}
    ),
    # an evidence_producer reads session + emits evidence records
    PrimitiveType.EVIDENCE_PRODUCER: frozenset(
        {Capability.READ_SESSION, Capability.EMIT_EVIDENCE}
    ),
}

# Conservative minimal set for primitive types without an explicit policy above
# (e.g. control_plane / callback / harness): read-only, no decision/mutation.
_RESTRICTED_DEFAULT: "frozenset[Capability]" = frozenset(
    {Capability.READ_SESSION, Capability.READ_EVIDENCE}
)


def restricted_capabilities_for(
    primitive_type: "str | PrimitiveType",
) -> frozenset["Capability"]:
    """Return the RESTRICTED capability set an untrusted/user pack should receive.

    Policy (defense-in-depth, see :class:`CapabilityError`):

    * ``tool`` -> ``{READ_SESSION}``
    * ``validator`` -> ``{READ_SESSION, EMIT_VALIDATION}``
    * ``evidence_producer`` -> ``{READ_SESSION, EMIT_EVIDENCE}``
    * any other type (control_plane, callback, harness, ...) -> the conservative
      read-only ``{READ_SESSION, READ_EVIDENCE}`` minimal set.

    The returned set deliberately withholds cross-surface tokens (a validator
    cannot ``EMIT_EVIDENCE``; a tool cannot ``DECIDE_TOOL``), so a pack that
    reaches outside its declared role hits :class:`CapabilityError`.
    """
    ptype = (
        primitive_type
        if isinstance(primitive_type, PrimitiveType)
        else PrimitiveType(primitive_type)
    )
    return _RESTRICTED_CAPABILITIES.get(ptype, _RESTRICTED_DEFAULT)


def _require(capabilities: "frozenset[Capability]", token: "Capability") -> None:
    """Raise :class:`CapabilityError` if ``token`` is absent from ``capabilities``.

    Defense-in-depth gate (NOT isolation). A context built with the default full
    set always carries the token, so this is a no-op on the OFF path.
    """
    if token not in capabilities:
        raise CapabilityError(
            f"capability {token.value!r} not granted to this context "
            f"(granted: {sorted(c.value for c in capabilities)})"
        )


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
        from magi_agent.adk_bridge.control_plane import ToolDecision  # noqa: PLC0415

        self._decision = ToolDecision(action="allow")

    def decide(self, action: Literal["allow", "deny", "rewrite"], *,
               reason: str | None = None,
               deny_result: dict[str, Any] | None = None,
               updated_args: dict[str, Any] | None = None) -> None:
        # Defense-in-depth (not isolation): gate the decision surface.
        _require(self.capabilities, Capability.DECIDE_TOOL)
        if action == "rewrite":
            _require(self.capabilities, Capability.REWRITE_TOOL_ARGS)
        if action == "deny" and deny_result is None:
            deny_result = {"error": reason or "denied by control-plane impl"}
        from magi_agent.adk_bridge.control_plane import ToolDecision  # noqa: PLC0415

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
        # Defense-in-depth (not isolation): gate the result-override surface.
        _require(self.capabilities, Capability.OVERRIDE_TOOL_RESULT)
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
        # Defense-in-depth (not isolation): gate the message-reinjection surface.
        _require(self.capabilities, Capability.REINJECT_MESSAGE)
        self._reinjections.append((role, text))

    def clear_tools(self) -> None:
        # Defense-in-depth (not isolation): gate the tool-clearing surface.
        _require(self.capabilities, Capability.CLEAR_TOOLS)
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
        # Defense-in-depth (not isolation): gate the validation-emit surface.
        _require(self.capabilities, Capability.EMIT_VALIDATION)
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
        # Defense-in-depth (not isolation): gate the evidence-emit surface.
        _require(self.capabilities, Capability.EMIT_EVIDENCE)
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
    """D5 typed context a ``tool`` impl receives.

    ``register`` accepts a ``ToolManifest`` (unchanged from Phase 4).
    ``register_workspace_handler`` (Pack C1) additionally lets a tool pack bind a
    WORKSPACE handler ``(args, WorkspaceHostView) -> output`` keyed by tool name —
    the gate5b toolhost executes it inside its unchanged dispatch envelope.
    ``register_handler`` (PR6) lets a tool pack ship a PLAIN inline handler
    ``(args: Mapping[str, object], tool_ctx: ToolCtx) -> output`` (sync or async)
    that needs no WorkspaceHostView (path safety / read ledger / bounded shell are
    workspace-file concerns); it registers the manifest AND the handler in one
    call. ``None`` when the projector predates the matching seam (both backward
    compatible). No god-object, no first-party-only kwarg."""

    register: Callable[[Any], None]
    register_workspace_handler: Callable[[str, Any], None] | None = None
    register_handler: Callable[[Any, Any], None] | None = None


@dataclass(frozen=True)
class LoopPolicyProvideContext:
    """D5 typed context a ``loop_policy`` impl receives: ``register(ref, policy)``
    where ``policy`` is ``Callable[[LoopControlInput], LoopControlResult]``."""

    register: Callable[[str, Any], None]


@dataclass(frozen=True)
class SchedulePolicyProvideContext:
    """D5 typed context a ``schedule_policy`` impl receives: ``register(ref, policy)``
    where ``policy`` satisfies the scheduler-executor schedule-policy contract."""

    register: Callable[[str, Any], None]


@dataclass(frozen=True)
class MemoryStrategyProvideContext:
    """D5 typed context a ``memory_strategy`` impl receives: ``register(ref, strategy)``."""

    register: Callable[[str, Any], None]


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

    # -- kernel path safety -----------------------------------------------------
    def resolve_path(self, path_text: str, *, allow_missing: bool = False) -> Any:
        from magi_agent.gates.gate5b_full_toolhost import _safe_child_path

        return _safe_child_path(
            self._host.workspace_root, path_text, allow_missing=allow_missing
        )

    def path_digest(self, target: Any) -> str:
        from magi_agent.gates.gate5b_full_toolhost import _digest

        return _digest(target.relative_to(self._host.workspace_root).as_posix())

    # -- kernel read-ledger store (policy stays kernel; handlers consume) --------
    def enforce_read_before_mutation(self, target: Any) -> None:
        self._host._enforce_read_before_mutation(target)

    def record_full_read(self, target: Any, content: str) -> None:
        self._host._record_full_read(target, content)

    # -- kernel write-side services -----------------------------------------------
    def format_after_write(self, target: Any) -> None:
        self._host._format_after_write(target)

    def content_digest(self, target: Any) -> str | None:
        return self._host._content_digest(target)

    def store_edit_match_result(self, match: Any) -> None:
        """Hand the EditMatchResult back so dispatch() builds the EditMatch
        evidence receipt exactly as the legacy branch did."""
        self._host._last_edit_match_result = match

    # -- kernel bounded/redacted shell ----------------------------------------------
    def run_command(self, command: str, *, timeout_s: float) -> dict[str, Any]:
        return self._host._run_shell_command(command, timeout_s=timeout_s)


@dataclass(frozen=True)
class EvidenceProducerProvideContext:
    """D5 typed context an ``evidence_producer`` impl receives: ``register(ref, spec)``."""

    register: Callable[[str, ProducerSpec], None]


@dataclass(frozen=True)
class RecipeProvideContext:
    """D5 typed context a ``recipe`` impl receives: ``register(ref, manifest)``."""

    register: Callable[[str, Any], None]


@dataclass(frozen=True)
class RoleProvideContext:
    """D5 typed context a ``role`` impl receives: ``register(ref, manifest)``.

    A ``role`` is a declarative scope label (a ``RoleManifest``), not executable
    code. It buckets which harness packs / hooks / contracts apply; it does not
    itself enforce anything."""

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


@dataclass(frozen=True)
class ControlPlaneProvideContext:
    """D5 typed context a ``control_plane`` impl receives at registration time.

    A ``control_plane`` provider impl assembles whatever ``LoopControl``s it
    declares (env-gated, with the runtime collaborators below) and registers each
    via ``register``. First-party and user ``control_plane`` packs receive the
    IDENTICAL object — no first-party-only handle (§1 no privilege). The bundled
    first-party entries delegate to the single-source builders in
    ``adk_bridge/control_plane.py`` (``build_core_default_plane`` plus the
    per-feature ``build_*_controls`` — the exact legacy env-gated assemblies) so
    the migration is a move, not a rewrite. A user pack can author its own
    controls and read the same ``env`` to gate them.

    Fields beyond ``register`` are the same collaborators ``build_default_plane``
    accepts; they are ``None`` unless the live runner injects them (e.g. GA
    receipts/contract, self-review fork-runner/sink/config,
    ``tool_synthesis_model_label``). They are read-only inputs the provider may
    pass through to a control it builds.
    """

    register: Callable[[Any], None]
    env: Mapping[str, str] = field(default_factory=dict)
    general_automation_receipts: Any | None = None
    contract_required: Any | None = None
    agent_role: str = "general"
    self_review_fork_runner: Any | None = None
    self_review_candidate_sink: Any | None = None
    self_review_config: Any | None = None
    self_review_now: Any | None = None
    self_review_scheduler: Any | None = None
    tool_synthesis_model_label: str | None = None


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
        # TODO(2a follow-up): thread RegistryEntry.origin ("first_party"/"user")
        # into the per-hook context construction below so a USER control_plane
        # pack receives ``restricted_capabilities_for(...)`` when
        # ``pack_capability_enforcement_enabled()`` is ON, mirroring the
        # validator/evidence/tool construction sites. Out of scope for this PR
        # (origin does not reach context construction here yet).

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
    "PrimitiveType", "Capability", "CapabilityError",
    "restricted_capabilities_for",
    "SessionReadView", "EvidenceReadView",
    "BeforeToolCtx", "AfterToolCtx", "BeforeModelCtx", "AfterAgentCtx",
    "ToolCtx", "ValidatorCtx", "ValidatorVerdict", "EvidenceProducerCtx",
    "ControlPlaneContext", "EvidenceLedgerView", "TurnSnapshot",
    "PerInvocationState",
    "ContextDispatcher", "GatePositionViolation",
    "ProducerSpec", "ConnectorSpec",
    "ToolProvideContext", "EvidenceProducerProvideContext", "RecipeProvideContext",
    "RoleProvideContext",
    "ConnectorProvideContext", "HarnessProvideContext", "CallbackProvideContext",
    "LoopPolicyProvideContext", "SchedulePolicyProvideContext",
    "MemoryStrategyProvideContext", "WorkspaceHostView",
]
