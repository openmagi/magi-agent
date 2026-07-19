"""Hosted-runtime foundation for governed-turn serving.

Exports ``HostedRuntime`` and ``build_hosted_runtime``.  The runtime mirrors
the ``HeadlessRuntime`` shape from :mod:`magi_agent.cli.wiring` but is
purpose-built for the hosted (gate5b4c3) serving path:

* Uses a caller-provided ADK primitives loader (same pattern as
  ``Gate5B4C3LiveRunnerBoundary``).
* Wires ``wire_profile=HOSTED_PROFILE`` into ``MagiEngineDriver`` so the
  event bridge emits the gate5b4c3 wire shape (``tu_<hash>`` tool ids, etc.).
* Exposes a no-op ``gate`` — hosted turns bypass the interactive CLI
  permission-prompt protocol; the pod-level egress controls are the
  enforcement boundary.

PR2/PR3 callers will wrap ``HostedRuntime`` in the serve path and wire
``run_governed_turn(ctx, runtime=hosted_rt)`` instead of the legacy
gate5b4c3 loop.  No live chat_routes are changed in PR1.
"""

from __future__ import annotations

from dataclasses import dataclass

from magi_agent.adk_bridge.wire_profile import HOSTED_PROFILE
from magi_agent.engine.driver import MagiEngineDriver
from magi_agent.runtime.adk_instruction import state_injection_safe_instruction


# ---------------------------------------------------------------------------
# Legacy gate5b4c3 session-identity constants
#
# The durable ADK session service keys rows by (app_name, user_id, session_id).
# To preserve history across flag flips (governed-turn ON/OFF), the governed
# path must adopt the same app_name and user_id as the legacy boundary.
# The session_id is already shared via _shadow_session_id and is NOT changed.
# ---------------------------------------------------------------------------

GATE5B_SHADOW_APP_NAME: str = "openmagi-gate5b4c3-shadow-generation"
GATE5B_SHADOW_USER_ID: str = "gate5b4c3-shadow-user"


def _agent_identifier(app_name: str) -> str:
    """Coerce ``app_name`` into a valid identifier for ``Agent(name=...)``.

    Real ADK validates ``Agent.name.isidentifier()`` (rejecting hyphens),
    while the Runner's visible ``app_name`` (which keys durable session rows)
    may contain hyphens. The legacy boundary keeps a hyphenated app_name for
    session keying but a distinct identifier-safe Agent name. We derive the
    Agent name from the app_name so the two never drift, without introducing a
    second literal to keep in sync. Mirrors ``model_runner._app_identifier``.
    """

    sanitized = "".join(c if c.isalnum() or c == "_" else "_" for c in app_name)
    if not sanitized or (not sanitized[0].isalpha() and sanitized[0] != "_"):
        sanitized = f"_{sanitized}"
    return sanitized if sanitized.isidentifier() else "hosted_governed_turn_agent"


# ---------------------------------------------------------------------------
# No-op gate (hosted enforcement is at the pod / egress level)
# ---------------------------------------------------------------------------


class _NoOpGate:
    """Hosted permission gate — always grants without prompting.

    The interactive CLI gate (``RulesPermissionGate``) is designed for the
    human-in-the-loop REPL loop.  Hosted pods run behind egress controls
    (gate1a / network policy), so a no-op gate is the correct hosted
    default.  PR3 can substitute a stricter gate if needed.

    ``check`` must honor the ``PermissionGate`` contract and return a
    ``PermissionDecision`` object: the driver's before-tool callback reads
    ``decision.kind`` / ``decision.updated_input``. The original str
    ``"allow"`` return killed EVERY tool call on the hosted governed path
    with ``AttributeError: 'str' object has no attribute 'kind'`` once
    MAGI_HOSTED_GOVERNED_TURN_ENABLED went fleet-default-ON (live incident,
    canary 186bf3d7, 2026-07-19).
    """

    async def check(self, *args: object, **kwargs: object):  # noqa: ARG002
        from magi_agent.engine.contracts import PermissionDecision  # noqa: PLC0415

        return PermissionDecision(kind="allow")


_HOSTED_NOOP_GATE = _NoOpGate()


# ---------------------------------------------------------------------------
# HostedRuntime dataclass
# ---------------------------------------------------------------------------


@dataclass
class HostedRuntime:
    """Minimal dependency set for the hosted governed-turn path.

    Attributes
    ----------
    engine:
        The ``MagiEngineDriver`` wired with ``wire_profile=HOSTED_PROFILE``
        and the caller-assembled ADK runner.
    gate:
        A no-op permission gate (hosted enforcement is at the pod level).
        ``run_governed_turn`` reads ``rt.gate`` via ``getattr`` — any object
        with a ``check`` method is acceptable.
    """

    engine: MagiEngineDriver
    gate: object


# ---------------------------------------------------------------------------
# build_hosted_runtime
# ---------------------------------------------------------------------------


def build_hosted_runtime(
    *,
    adk_primitives_loader: object,
    adk_tools: tuple | list = (),
    model: object,
    instruction: str,
    generate_content_config: object,
    control_plane_plugins: tuple | list = (),
    public_event_sink: object | None = None,
    app_name: str = "openmagi-hosted-governed-turn",
    session_service: object | None = None,
    user_id: str = "cli",
    # B5: durable-session event-fetch bound (driver-owned, anti-side-channel safe).
    # ``None`` (default) -> byte-identical to pre-B5 (no GetSessionConfig on
    # RunConfig). ``gate5b_serving`` passes ``DEFAULT_NUM_RECENT_EVENTS`` when
    # the durable session substrate is active, mirroring the legacy condition at
    # gate5b4c3:770-774. CLI and non-durable paths leave this as ``None``.
    num_recent_events: int | None = None,
    # U9 (P1-1): truncated-output auto-continue config. ``None`` (default) leaves
    # the driver's continuation path OFF, byte-identical to pre-U9. The serving
    # call site resolves the config from env, gated on ``selected_full_toolhost``,
    # to mirror the legacy boundary (gate5b4c3:834-836). Env is read at the call
    # site (see ``_resolve_output_continuation_config``); this ctor stays
    # env-pure and simply forwards the resolved object to the driver.
    output_continuation: "object | None" = None,
    # B9 backstop: one tool-less finalizer pass on a blank tool turn. ``None``
    # (default) is byte-identical. The serving call site resolves the config
    # from env gated on ``selected_full_toolhost`` (legacy always-on parity);
    # this ctor stays env-pure and forwards the resolved object to the driver.
    no_tool_finalizer: "object | None" = None,
) -> HostedRuntime:
    """Assemble a ``HostedRuntime`` from caller-provided ADK primitives.

    Parameters
    ----------
    adk_primitives_loader:
        Zero-argument callable returning a ``Gate5B4C3LiveAdkPrimitives``
        (or any duck-type with ``.Agent``, ``.Runner``,
        ``.InMemorySessionService``).  Same pattern as
        ``Gate5B4C3LiveRunnerBoundary._adk_primitives_loader``.
    adk_tools:
        Sequence of ADK tools to register on the agent.  PR1 default is
        empty (no tools); the real serve path (PR2) will supply the
        first-party toolset.
    model:
        Final model object / label for ``primitives.Agent(model=…)``.
        The caller is responsible for any gate1a wrapping — this function
        does NOT re-wrap the model.
    instruction:
        System instruction string forwarded as ``primitives.Agent(instruction=…)``.
    generate_content_config:
        ``GenerateContentConfig``-compatible object forwarded to the agent.
    control_plane_plugins:
        Sequence of ADK runner plugins (e.g. the governance control-plane).
        When non-empty, forwarded as ``primitives.Runner(plugins=[…])``.
        When empty, the ``plugins`` kwarg is **omitted entirely** from the
        Runner call — byte-identical to the flag-OFF gate5b4c3 path.
    public_event_sink:
        Optional event sink forwarded to ``MagiEngineDriver(event_sink=…)``.
    app_name:
        ADK runner app name (default: ``"openmagi-hosted-governed-turn"``).
        Pass ``GATE5B_SHADOW_APP_NAME`` to adopt the legacy identity so the
        governed path reads/writes the same session rows as the gate5b4c3
        boundary (zero-migration flip-forward / flip-back parity, B6).
    session_service:
        Optional pre-built session service.  When ``None`` (default),
        ``primitives.InMemorySessionService()`` is used (PR1 inline;
        per-bot session-reuse pool from gate5b4c3 is out of scope until PR2).
    user_id:
        ADK user_id stamped on the engine driver and forwarded to
        ``runner.run_async(user_id=...)`` (default: ``"cli"``).
        Pass ``GATE5B_SHADOW_USER_ID`` to adopt the legacy identity (B6).

    Returns
    -------
    HostedRuntime
        Ready for ``run_governed_turn(ctx, runtime=hosted_rt)``.
    """
    # Resolve primitives (matches gate5b4c3 pattern).
    primitives = adk_primitives_loader()  # type: ignore[call-arg,operator]

    # Build session service.
    svc = session_service if session_service is not None else primitives.InMemorySessionService()

    # Build ADK agent. The instruction is wrapped state-injection-safe: a plain
    # str is template-substituted by ADK ({identifier} placeholders raise
    # KeyError when absent from session state), and the composed instruction can
    # embed an activated SKILL.md body carrying brace-wrapped tokens.
    agent = primitives.Agent(
        name=_agent_identifier(app_name),
        description="OpenMagi hosted governed-turn agent.",
        model=model,
        instruction=state_injection_safe_instruction(instruction),
        tools=list(adk_tools),
        generate_content_config=generate_content_config,
    )

    # Build ADK runner — omit ``plugins`` kwarg entirely when empty
    # (mirrors gate5b4c3's flag-OFF runner construction).
    runner_kwargs: dict[str, object] = {
        "app_name": app_name,
        "agent": agent,
        "session_service": svc,
        "auto_create_session": True,
    }
    if control_plane_plugins:
        runner_kwargs["plugins"] = list(control_plane_plugins)
    runner = primitives.Runner(**runner_kwargs)

    # Build engine with HOSTED_PROFILE so the bridge emits the gate5b4c3
    # wire shape (tu_<hash> tool ids, public_events field shapes).
    # user_id is forwarded so runner.run_async receives the correct ADK
    # identity (B6: governed path must match the legacy boundary identity).
    engine = MagiEngineDriver(
        runner=runner,
        event_sink=public_event_sink,
        wire_profile=HOSTED_PROFILE,
        user_id=user_id,
        num_recent_events=num_recent_events,
        output_continuation=output_continuation,
        no_tool_finalizer=no_tool_finalizer,
    )

    return HostedRuntime(engine=engine, gate=_HOSTED_NOOP_GATE)
