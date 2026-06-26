"""Per-slot synthetic trigger drivers for the F-QA matrix harness.

Each trigger function exercises the runtime chokepoint that fires the
specified lifecycle slot using the smallest possible setup. F-QA1 covers
three slots; F-QA2 adds the turn-boundary set.

* :func:`trigger_pre_final` — pre-final gate. Routes by kind:
  - ``deterministic_ref`` / shacl_constraint via the gate compile seam
    (``magi_agent.cli.real_runner._apply_customize_verification`` for
    deterministic_ref; the shacl verifier kernel directly for
    shacl_constraint).
  - ``llm_criterion`` via ``MagiEngineDriver._maybe_llm_criterion_block``
    with a patched judge that returns the verdict the matrix expects.
  - ``shell_command`` / ``shell_check`` via the lifecycle_audit fan-out
    helpers ``run_shell_command_at_pre_final`` /
    ``run_shell_check_at_pre_final``.

* :func:`trigger_before_tool_use` — wrap
  :func:`magi_agent.facades.execute_tool_with_hooks` with a stub
  :class:`ToolDispatcher` and :class:`HookBus`. The facade's
  before-tool branch invokes the prompt_injection applier, the
  tool_perm matcher (consulted by the production permission layer
  separately — we drive it directly for tool_perm rules), and the
  shell_command/shell_check before-tool helpers.

* :func:`trigger_after_tool_use` — same facade rig, asserts on
  mutations (output_rewrite) / overrides (llm_criterion after-tool gate)
  applied to the returned :class:`ToolResult`.

F-QA2 turn-boundary drivers (drive ``run_governed_turn`` directly):

* :func:`trigger_before_turn_start` — drives the
  ``_maybe_run_before_turn_start_gate`` short-circuit; ``block`` action
  asserts the FIRST yielded item is the synthetic
  ``customize_policy_blocked`` ``EngineResult(terminal=Terminal.aborted)``
  AND that the fake engine's ``run_turn_stream`` was NEVER consumed.
* :func:`trigger_on_user_prompt_submit` — same short-circuit shape as
  before_turn_start, but keyed on the F-UX1 master flag and the
  ``on_user_prompt_submit`` gate wrapper.
* :func:`trigger_after_turn_end` — audit-only by ``_LEGAL`` (block is
  excluded). Drives a top-level turn (``ctx.depth == 0``) through
  completion so the ``_AfterTurnEndCollector.run_audit`` finally block
  fires, then asserts an audit record was recorded.
* :func:`trigger_on_subagent_stop` — drives a CHILD turn
  (``ctx.depth > 0``) through completion. ``_LEGAL`` lifts the slot to
  ``{audit, block, ask_approval}`` for authorability per the F-LIFE1
  TODO note, but the runtime parent-surfacing wire is NOT built yet.
  The driver asserts the audit ledger captured the verdict and does
  NOT assert any parent-side block.

F-QA3 per-LLM-call drivers (drive the ADK plugin directly):

* :func:`trigger_before_llm_call` — drives
  ``LifecycleLlmCallAuditControl.on_before_model`` with a synthetic
  ADK callback_context + ``LlmRequest`` stub. F-LIFE4a lifted the
  matrix to ``{audit, block}``; the block path returns the synthetic
  policy-blocked ``LlmResponse`` and the audit path returns ``None``.
* :func:`trigger_after_llm_call` — mirror of before_llm_call but for
  ``on_after_model``; block REPLACES the just-emitted response with
  the synthetic refusal (per F-LIFE4a — already-streamed tokens
  cannot be un-rung but the consumer never sees the offending text).

The trigger functions return a :class:`TriggerOutcome` the asserter
inspects. They never raise on a rule failing to fire — the *asserter*
is responsible for translating "did the rule fire as expected?" into a
test pass/fail.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from magi_agent.harness.resolved import build_default_resolved_harness_state
from magi_agent.hooks.bus import HookBus, HookBusObservation, HookBusRunResult
from magi_agent.hooks.context import HookContext
from magi_agent.tools.context import ToolContext
from magi_agent.tools.dispatcher import ToolDispatcher
from magi_agent.tools.result import ToolResult


# ---------------------------------------------------------------------------
# Outcome dataclass
# ---------------------------------------------------------------------------


@dataclass
class TriggerOutcome:
    """Captured runtime side effects from one synthetic trigger."""

    # Audit records the lifecycle_audit fan-out emitted (may be empty when
    # the slot's runtime fan-out is in-band — e.g. tool_perm returns a
    # (decision, rule_id) pair, not an audit ledger record).
    audit_records: list[dict[str, Any]] = field(default_factory=list)
    # ``proceed`` / ``block`` / ``ask`` / ``override`` / ``error``. The
    # asserter maps the matrix-declared action onto this verdict.
    runtime_verdict: str = "proceed"
    # Arbitrary per-trigger side-effect bag (the asserter inspects this for
    # kind-specific evidence, e.g. ``tool_result`` for after_tool_use,
    # ``required_validators`` for deterministic_ref, ``decision`` for
    # tool_perm). Keeping the bag open avoids one TriggerOutcome subclass
    # per (kind, slot) combo.
    side_effects: dict[str, Any] = field(default_factory=dict)


def _continue_hookbus() -> HookBus:
    """Build a ``HookBus`` mock that returns ``continue`` for every point.

    The facade's before/after hooks only consult ``HookBus.run`` for
    BEFORE_TOOL_USE / AFTER_TOOL_USE points; everything else is bypassed.
    """
    bus = MagicMock(spec=HookBus)
    bus.run = MagicMock(
        return_value=HookBusRunResult(
            final_action="continue",
            results=(),
            observation=HookBusObservation(),
            harness_state=build_default_resolved_harness_state(),
        )
    )
    return bus


def _stub_dispatcher(
    *, status: str = "ok", output: str = "PASS"
) -> AsyncMock:
    """Return an ``AsyncMock(spec=ToolDispatcher)`` with a deterministic dispatch.

    Default ``output="PASS"`` so an ``llm_criterion`` rule with a binary
    criterion can pass in the after-tool path without needing a real LLM
    call (the asserter monkeypatches the judge separately when ``override``
    is the expected action).
    """
    dispatcher = AsyncMock(spec=ToolDispatcher)
    dispatcher.dispatch = AsyncMock(
        return_value=ToolResult(status=status, output=output)
    )
    return dispatcher


# ---------------------------------------------------------------------------
# pre_final
# ---------------------------------------------------------------------------


async def trigger_pre_final(
    *,
    kind: str,
    rule_id: str,
    expected_action: str,
    judge_factory: Callable[[], Callable[..., Awaitable[tuple[bool, str]]]]
    | None = None,
) -> TriggerOutcome:
    """Drive whatever pre-final chokepoint the rule's kind fans out at.

    The branches mirror the production runtime entry points; they do not
    invent any new wire. ``judge_factory`` is only consulted for
    ``llm_criterion`` rules — the conftest fixture monkeypatches the
    criterion engine so we never need a live LLM call.
    """
    if kind == "deterministic_ref":
        return _trigger_pre_final_deterministic_ref()
    if kind == "shacl_constraint":
        return _trigger_pre_final_shacl_constraint(expected_action=expected_action)
    if kind == "llm_criterion":
        return await _trigger_pre_final_llm_criterion(
            rule_id=rule_id,
            expected_action=expected_action,
            judge_factory=judge_factory,
        )
    if kind in {"shell_command", "shell_check"}:
        return await _trigger_pre_final_shell(kind=kind)
    raise ValueError(f"trigger_pre_final has no branch for kind={kind!r}")


def _trigger_pre_final_deterministic_ref() -> TriggerOutcome:
    """Drive ``_apply_customize_verification`` with a seed validator list.

    Firing semantics: the rule's ``ref`` is injected into the returned
    list when both master flags are ON (and the producer registry knows
    the ref). Asserter compares ``required_validators`` against the
    seed list to detect the injection.
    """
    from magi_agent.cli.real_runner import _apply_customize_verification

    seed = ["seed:ref"]
    out = _apply_customize_verification(list(seed))
    return TriggerOutcome(
        side_effects={
            "required_validators": list(out),
            "seed_validators": list(seed),
        },
    )


def _trigger_pre_final_shacl_constraint(
    *, expected_action: str
) -> TriggerOutcome:
    """Run the SHACL kernel against a synthetic TestRun record.

    For ``expected_action == "block"`` we feed a non-conforming record
    (exit_code=1) so the verifier reports a violation. The action label
    on the rule is honored by the wrapping runtime gate, not by
    ``run_shacl_rule`` itself — this function captures the verifier's
    raw verdict so the asserter can mirror the "block-on-violation"
    contract.
    """
    try:
        from magi_agent.evidence.shacl_verifier import run_shacl_rule
        from magi_agent.evidence.types import EvidenceRecord, EvidenceSource
    except ImportError:
        # Optional deps (rdflib + pyshacl) absent on minimal envs — surface
        # via verdict so the conftest skip path can fire.
        return TriggerOutcome(
            runtime_verdict="error",
            side_effects={"reason": "shacl_deps_missing"},
        )

    from tests.e2e.customize.payload_factory import _SHACL_TEST_RUN_EXIT_ZERO

    exit_code = 1 if expected_action == "block" else 0
    record = EvidenceRecord(
        type="TestRun",
        status="ok",
        observedAt=1_718_000_000,
        source=EvidenceSource(kind="verifier"),
        fields={"exitCode": exit_code},
    )
    result = run_shacl_rule(
        [record],
        _SHACL_TEST_RUN_EXIT_ZERO,
        "fqa1-shacl",
        observed_at=1_718_000_000,
    )
    verdict = "block" if result.status == "failed" else "proceed"
    return TriggerOutcome(
        runtime_verdict=verdict,
        side_effects={"shacl_status": result.status},
    )


async def _trigger_pre_final_llm_criterion(
    *,
    rule_id: str,
    expected_action: str,
    judge_factory: Callable[[], Callable[..., Awaitable[tuple[bool, str]]]]
    | None,
) -> TriggerOutcome:
    """Drive ``MagiEngineDriver._maybe_llm_criterion_block`` with a patched judge.

    The asserter's harness installs the patched judge before calling us.
    We simply drive the engine seam and capture the returned block-reason
    (``None`` ⇒ no block) so the asserter can map matrix-action onto
    "expected verdict".

    Drives via the same code path
    ``tests/customize_firing/test_llm_criterion_firing.py`` exercises
    so the trigger never invents a new wire.
    """
    _ = (rule_id, expected_action, judge_factory)  # asserter installed the patch
    from magi_agent.cli.engine import MagiEngineDriver

    driver = MagiEngineDriver(criterion_model_factory=lambda: object())
    reason = await driver._maybe_llm_criterion_block(
        final_text="The market grew 40% last year according to internal estimates."
    )
    verdict = "block" if reason else "proceed"
    return TriggerOutcome(
        runtime_verdict=verdict,
        side_effects={"reason": reason},
    )


async def _trigger_pre_final_shell(*, kind: str) -> TriggerOutcome:
    """Drive ``run_shell_*_at_pre_final`` fan-out.

    Returns the audit records the runtime would record. The matrix is
    audit-shaped (a ``failed`` shell_check at action=block returns
    verdict ``block``; otherwise the helper returns ``proceed``).
    """
    if kind == "shell_command":
        from magi_agent.customize.lifecycle_audit import (
            run_shell_command_at_pre_final,
        )

        audits, _verdict = await run_shell_command_at_pre_final(draft_text="x")
        # shell_command fan-out does not surface a runtime verdict — block
        # at this slot is honored by the governed_turn caller, not the
        # helper. We surface "block" if any audit record's exit_code != 0.
        verdict = "proceed"
        for record in audits:
            if (
                record.get("status") == "executed"
                and record.get("exit_code", 0) != 0
            ):
                verdict = "block"
                break
        return TriggerOutcome(
            audit_records=list(audits),
            runtime_verdict=verdict,
        )

    # shell_check
    from magi_agent.customize.lifecycle_audit import (
        run_shell_check_at_pre_final,
    )

    audits, verdict = await run_shell_check_at_pre_final(draft_text="x")
    return TriggerOutcome(
        audit_records=list(audits),
        runtime_verdict=verdict,
    )


# ---------------------------------------------------------------------------
# before_tool_use
# ---------------------------------------------------------------------------


async def trigger_before_tool_use(
    *,
    kind: str,
    rule_id: str,
    expected_action: str,
    tool_name: str = "shell_exec",
    arguments: dict[str, Any] | None = None,
) -> TriggerOutcome:
    """Drive the before-tool chokepoint for the rule's kind.

    Routing:
      * ``tool_perm`` → ``customize.tool_perm.matched_decision`` (the
        seam the production permission layer consults pre-dispatch).
      * ``prompt_injection`` → ``execute_tool_with_hooks`` with a stub
        dispatcher; the asserter inspects the dispatcher's recorded
        arguments to detect the mutation.
      * ``shell_command`` / ``shell_check`` → ``execute_tool_with_hooks``
        which calls ``run_shell_*_at_before_tool_use`` internally; the
        asserter inspects the returned ``ToolResult.status`` for
        ``"blocked"`` when action == ``"block"``.
    """
    _ = expected_action  # asserter consumes this; we always drive deterministically
    if arguments is None:
        arguments = {"command": "ls"}

    if kind == "tool_perm":
        return _trigger_before_tool_use_tool_perm(
            tool_name=tool_name, arguments=arguments
        )

    if kind in {"prompt_injection", "shell_command", "shell_check"}:
        return await _trigger_before_tool_use_via_facade(
            tool_name=tool_name,
            arguments=arguments,
        )

    raise ValueError(
        f"trigger_before_tool_use has no branch for kind={kind!r}"
    )


def _trigger_before_tool_use_tool_perm(
    *, tool_name: str, arguments: dict[str, Any]
) -> TriggerOutcome:
    """Call ``matched_decision`` directly — the seam runtime consults pre-dispatch."""
    from magi_agent.customize.tool_perm import matched_decision

    decision = matched_decision(
        tool_name=tool_name,
        arguments=arguments,
        current_scope="always",
    )
    if decision is None:
        return TriggerOutcome(
            runtime_verdict="proceed",
            side_effects={"decision": None},
        )
    verdict_label, rule_id = decision
    runtime_verdict = "block" if verdict_label == "deny" else "ask"
    return TriggerOutcome(
        runtime_verdict=runtime_verdict,
        side_effects={"decision": decision, "matched_rule_id": rule_id},
    )


async def _trigger_before_tool_use_via_facade(
    *,
    tool_name: str,
    arguments: dict[str, Any],
) -> TriggerOutcome:
    """Run a single tool dispatch through the facade and capture the result."""
    from magi_agent.facades import execute_tool_with_hooks

    dispatcher = _stub_dispatcher(status="ok", output="ok")
    bus = _continue_hookbus()

    result, before, after = await execute_tool_with_hooks(
        dispatcher,
        bus,
        tool_name=tool_name,
        arguments=arguments,
        context=ToolContext(botId="b"),
        hook_context=HookContext(botId="b"),
        harness_state=build_default_resolved_harness_state(),
        mode="act",
    )
    verdict = "block" if result.status == "blocked" else "proceed"
    return TriggerOutcome(
        runtime_verdict=verdict,
        side_effects={
            "tool_result": result,
            "dispatched_arguments": (
                dispatcher.dispatch.call_args.args[1]
                if dispatcher.dispatch.call_args is not None
                else None
            ),
            "before_hook_result": before,
            "after_hook_result": after,
        },
    )


# ---------------------------------------------------------------------------
# after_tool_use
# ---------------------------------------------------------------------------


async def trigger_after_tool_use(
    *,
    kind: str,
    rule_id: str,
    expected_action: str,
    tool_name: str = "shell_exec",
    dispatch_output: str = "hello AKIABCDEFGHIJKLMNOPQ world PASS",
) -> TriggerOutcome:
    """Drive the after-tool chokepoint for the rule's kind.

    The facade composes ``output_rewrite``'s redact, the after-tool
    HookBus replace branch, and (for ``llm_criterion`` override rules)
    the after-tool ingestion gate. The default dispatch output carries
    both an AWS-key-shaped substring (so ``output_rewrite`` has
    something to redact) and the literal ``"PASS"`` (so an
    ``llm_criterion`` criterion authored as "Does the output contain
    the literal PASS?" can pass without an LLM call).
    """
    _ = (rule_id, expected_action)  # asserter inspects side_effects

    if kind in {"output_rewrite", "shell_command", "shell_check"}:
        return await _trigger_after_tool_use_via_facade(
            tool_name=tool_name,
            dispatch_output=dispatch_output,
        )
    if kind == "llm_criterion":
        return await _trigger_after_tool_use_llm_criterion(
            tool_name=tool_name,
            dispatch_output=dispatch_output,
        )
    raise ValueError(f"trigger_after_tool_use has no branch for kind={kind!r}")


async def _trigger_after_tool_use_via_facade(
    *,
    tool_name: str,
    dispatch_output: str,
) -> TriggerOutcome:
    """Generic after-tool driver — the facade applies the rule mutators."""
    from magi_agent.facades import execute_tool_with_hooks

    dispatcher = _stub_dispatcher(status="ok", output=dispatch_output)
    bus = _continue_hookbus()

    result, before, after = await execute_tool_with_hooks(
        dispatcher,
        bus,
        tool_name=tool_name,
        arguments={"command": "ls"},
        context=ToolContext(botId="b"),
        hook_context=HookContext(botId="b"),
        harness_state=build_default_resolved_harness_state(),
        mode="act",
    )
    return TriggerOutcome(
        runtime_verdict="proceed",
        side_effects={
            "tool_result": result,
            "original_output": dispatch_output,
            "before_hook_result": before,
            "after_hook_result": after,
        },
    )


async def _trigger_after_tool_use_llm_criterion(
    *,
    tool_name: str,
    dispatch_output: str,
) -> TriggerOutcome:
    """Drive the after-tool ingestion gate (``CustomizeAfterToolControl``).

    The gate's ``apply_after_tool`` returns an override dict on a fail
    verdict / content-match hit and ``None`` otherwise. The conftest's
    ``judge_factory`` controls the verdict.
    """
    from magi_agent.customize.after_tool_gate import CustomizeAfterToolControl

    control = CustomizeAfterToolControl(model_factory=lambda: object())

    tool_stub = MagicMock()
    tool_stub.name = tool_name

    override = await control.apply_after_tool(
        ctx=None,
        tool=tool_stub,
        args={},
        tool_context=None,
        result=dispatch_output,
    )
    verdict = "override" if override is not None else "proceed"
    return TriggerOutcome(
        runtime_verdict=verdict,
        side_effects={"override": override, "original_output": dispatch_output},
    )


# ---------------------------------------------------------------------------
# F-QA2 turn-boundary drivers
# ---------------------------------------------------------------------------
#
# These drivers run a REAL ``run_governed_turn`` with a fake engine so we
# exercise the production wrappers (``_maybe_run_before_turn_start_gate``,
# ``_maybe_run_user_prompt_submit_gate``, ``_AfterTurnEndCollector``,
# ``_SubagentStopCollector``) rather than calling the fan-out helpers
# directly (those are covered by sibling unit tests in
# ``tests/customize_firing/``). The fake engine's stream is "poisoned"
# with a sentinel so the asserter can detect short-circuits — if the
# sentinel item ever surfaces, the gate failed to short-circuit and the
# row is a failure.


class _PoisonRecordingEngine:
    """Fake engine that records EVERY call to ``run_turn_stream``.

    The yielded items include a sentinel ``RuntimeEvent`` so the asserter
    can distinguish "the gate short-circuited before the engine was
    invoked" from "the gate proceeded and the engine streamed normally".
    """

    POISON_DELTA = "POISON-ENGINE-RAN"

    def __init__(self, child_final_text: str = "child summary") -> None:
        self._child_final_text = child_final_text
        self.run_turn_stream_calls: list[dict[str, object]] = []

    async def run_turn_stream(
        self,
        _none: object,
        turn_input: object,
        *,
        cancel: object,
        gate: object,
    ):
        # Late import keeps tests importable on stripped-down envs.
        from magi_agent.cli.contracts import EngineResult, Terminal
        from magi_agent.runtime.events import RuntimeEvent

        self.run_turn_stream_calls.append(
            {"turn_input": turn_input, "cancel": cancel, "gate": gate}
        )
        yield RuntimeEvent(
            type="token",
            payload={
                "type": "text_delta",
                "delta": self._child_final_text,
            },
        )
        yield EngineResult(
            terminal=Terminal.completed,
            usage={"input_tokens": 1, "output_tokens": 1},
            cost_usd=0.0,
            session_id="sess-fqa2",
            turn_id="turn-1",
        )


class _PoisonRecordingRuntime:
    def __init__(self, engine: _PoisonRecordingEngine) -> None:
        self.engine = engine
        self.gate = None


def _build_turn_ctx(
    *,
    session_id: str,
    prompt: str,
    depth: int,
):
    """Build a ``TurnContext`` for the F-QA2 drivers."""
    from magi_agent.runtime.turn_context import TurnContext  # noqa: PLC0415

    return TurnContext(
        prompt=prompt,
        session_id=session_id,
        turn_id=f"turn_{session_id}",
        depth=depth,
    )


async def _drive_governed_turn(
    *, ctx, engine: _PoisonRecordingEngine
) -> list[object]:
    """Run ``run_governed_turn`` to completion and return the yielded items."""
    from magi_agent.runtime.governed_turn import run_governed_turn  # noqa: PLC0415

    runtime = _PoisonRecordingRuntime(engine)
    items: list[object] = []
    async for item in run_governed_turn(ctx, runtime=runtime):
        items.append(item)
    return items


def _is_policy_blocked_terminal(item: object, *, slot: str) -> bool:
    """Return True iff *item* is the synthetic policy-blocked terminal."""
    try:
        from magi_agent.cli.contracts import EngineResult, Terminal  # noqa: PLC0415
    except Exception:
        return False
    if not isinstance(item, EngineResult):
        return False
    if item.terminal is not Terminal.aborted:
        return False
    error = item.error or ""
    if "customize_policy_blocked" not in error:
        return False
    if slot not in error:
        return False
    return True


async def trigger_before_turn_start(
    *,
    kind: str,
    rule_id: str,
    expected_action: str,
    session_id: str | None = None,
) -> TriggerOutcome:
    """Drive ``run_governed_turn`` and observe the before_turn_start gate.

    For ``action=block``: assert the FIRST (and only) yielded item is the
    synthetic ``EngineResult(terminal=Terminal.aborted)`` with error
    containing ``customize_policy_blocked`` AND ``before_turn_start``,
    AND that the fake engine's ``run_turn_stream`` was NEVER consumed.

    For ``action=ask_approval``: honest-degrade — the turn proceeds and
    the gate verdict is recorded only via the audit ledger. The driver
    asserts the engine WAS invoked (turn proceeded) and records the
    verdict label so the asserter can match the
    ``requires_approval``-shaped contract.

    For ``action=audit``: turn proceeds normally, judge fired, no
    short-circuit.
    """
    _ = (kind, rule_id)  # asserter consumes these; we drive deterministically
    sid = session_id or "sess_fqa2_bts"
    ctx = _build_turn_ctx(session_id=sid, prompt="hello from fqa2", depth=0)
    engine = _PoisonRecordingEngine()
    items = await _drive_governed_turn(ctx=ctx, engine=engine)

    engine_invoked = bool(engine.run_turn_stream_calls)
    blocked = (
        len(items) == 1
        and _is_policy_blocked_terminal(items[0], slot="before_turn_start")
    )
    verdict = "block" if blocked else "proceed"
    return TriggerOutcome(
        runtime_verdict=verdict,
        side_effects={
            "items": items,
            "engine_invoked": engine_invoked,
            "engine_run_turn_stream_calls": len(engine.run_turn_stream_calls),
            "poison_delta": _PoisonRecordingEngine.POISON_DELTA,
            "slot": "before_turn_start",
        },
    )


async def trigger_on_user_prompt_submit(
    *,
    kind: str,
    rule_id: str,
    expected_action: str,
    session_id: str | None = None,
) -> TriggerOutcome:
    """Drive ``run_governed_turn`` and observe the on_user_prompt_submit gate.

    Mirrors :func:`trigger_before_turn_start` but the synthetic terminal's
    error string carries ``on_user_prompt_submit`` instead.
    """
    _ = (kind, rule_id)
    sid = session_id or "sess_fqa2_ups"
    ctx = _build_turn_ctx(session_id=sid, prompt="hello from fqa2", depth=0)
    engine = _PoisonRecordingEngine()
    items = await _drive_governed_turn(ctx=ctx, engine=engine)

    engine_invoked = bool(engine.run_turn_stream_calls)
    blocked = (
        len(items) == 1
        and _is_policy_blocked_terminal(items[0], slot="on_user_prompt_submit")
    )
    verdict = "block" if blocked else "proceed"
    return TriggerOutcome(
        runtime_verdict=verdict,
        side_effects={
            "items": items,
            "engine_invoked": engine_invoked,
            "engine_run_turn_stream_calls": len(engine.run_turn_stream_calls),
            "poison_delta": _PoisonRecordingEngine.POISON_DELTA,
            "slot": "on_user_prompt_submit",
        },
    )


async def trigger_after_turn_end(
    *,
    kind: str,
    rule_id: str,
    expected_action: str,
    session_id: str | None = None,
) -> TriggerOutcome:
    """Drive ``run_governed_turn`` through completion + observe after_turn_end.

    Audit-only by ``_LEGAL`` (block is excluded). The
    ``_AfterTurnEndCollector.run_audit`` finally block fires the audit
    fan-out with the aggregated final text. The driver verifies the
    turn completed normally (sentinel item appeared, terminal not
    aborted) and that the engine WAS invoked exactly once.
    """
    _ = (kind, rule_id, expected_action)
    sid = session_id or "sess_fqa2_ate"
    ctx = _build_turn_ctx(session_id=sid, prompt="hello from fqa2", depth=0)
    engine = _PoisonRecordingEngine()
    items = await _drive_governed_turn(ctx=ctx, engine=engine)

    engine_invoked = bool(engine.run_turn_stream_calls)
    # after_turn_end is audit-only — the synthetic terminal MUST NOT have
    # been emitted in place of the engine's natural terminal.
    blocked_terminals = [
        i for i in items
        if _is_policy_blocked_terminal(i, slot="after_turn_end")
    ]
    return TriggerOutcome(
        runtime_verdict="proceed",
        side_effects={
            "items": items,
            "engine_invoked": engine_invoked,
            "blocked_terminals": blocked_terminals,
            "poison_delta": _PoisonRecordingEngine.POISON_DELTA,
            "slot": "after_turn_end",
        },
    )


async def trigger_before_llm_call(
    *,
    kind: str,
    rule_id: str,
    expected_action: str,
    session_id: str | None = None,
    turn_id: str | None = None,
    prompt_text: str = "please answer this question",
) -> TriggerOutcome:
    """Drive ``LifecycleLlmCallAuditControl.on_before_model`` for ``before_llm_call``.

    F-LIFE2 + F-LIFE4a contract:

    * v1 ``_LEGAL`` accepts ``{audit, block}`` for ``llm_criterion`` at the
      per-LLM-call boundary. ``block`` synthesizes an
      :class:`google.adk.models.llm_response.LlmResponse` carrying
      ``custom_metadata.policy_blocked=True`` (the
      ``_build_policy_blocked_llm_response`` helper).
    * ``audit`` returns ``None`` to the ADK callback dispatcher — the
      model call proceeds. The audit ledger captures the judge's verdict;
      the patched_judge sentinel records the invocation count.

    Drives the plugin directly with a synthetic ADK callback_context
    (mock with ``session.id`` + ``invocation_id``) + ``LlmRequest`` stub
    so we exercise the production wire (identity resolution + budget
    decrement + gate derivation) without spinning up a real ADK runner.
    """
    _ = (kind, rule_id)  # asserter consumes these; we drive deterministically
    from magi_agent.adk_bridge.lifecycle_llm_call_control import (  # noqa: PLC0415
        LifecycleLlmCallAuditControl,
    )

    sid = session_id or "sess_fqa3_blc"
    tid = turn_id or f"turn_{sid}"

    control = LifecycleLlmCallAuditControl()
    callback_context = _build_llm_call_callback_context(sid, tid)
    llm_request = _build_llm_request(prompt_text)

    result = await control.on_before_model(
        callback_context=callback_context,
        llm_request=llm_request,
    )

    verdict, blocked_response = _classify_llm_call_result(
        result, slot="before_llm_call"
    )
    return TriggerOutcome(
        runtime_verdict=verdict,
        side_effects={
            "result": result,
            "blocked_response": blocked_response,
            "callback_context": callback_context,
            "llm_request": llm_request,
            "session_id": sid,
            "turn_id": tid,
            "slot": "before_llm_call",
        },
    )


async def trigger_after_llm_call(
    *,
    kind: str,
    rule_id: str,
    expected_action: str,
    session_id: str | None = None,
    turn_id: str | None = None,
    response_text: str = "the answer is 42",
) -> TriggerOutcome:
    """Drive ``LifecycleLlmCallAuditControl.on_after_model`` for ``after_llm_call``.

    F-LIFE4a contract: at after_llm_call, a ``block`` verdict REPLACES the
    just-emitted ``LlmResponse`` with the synthetic policy-blocked refusal
    (tokens already streamed cannot be un-rung — the helper relies on
    ADK's after_model returning a new response). The audit-only path
    returns ``None`` so the original response surfaces unchanged.
    """
    _ = (kind, rule_id)
    from magi_agent.adk_bridge.lifecycle_llm_call_control import (  # noqa: PLC0415
        LifecycleLlmCallAuditControl,
    )

    sid = session_id or "sess_fqa3_alc"
    tid = turn_id or f"turn_{sid}"

    control = LifecycleLlmCallAuditControl()
    callback_context = _build_llm_call_callback_context(sid, tid)
    llm_response = _build_llm_response(response_text)

    result = await control.on_after_model(
        callback_context=callback_context,
        llm_response=llm_response,
    )

    verdict, blocked_response = _classify_llm_call_result(
        result, slot="after_llm_call"
    )
    return TriggerOutcome(
        runtime_verdict=verdict,
        side_effects={
            "result": result,
            "blocked_response": blocked_response,
            "callback_context": callback_context,
            "llm_response": llm_response,
            "session_id": sid,
            "turn_id": tid,
            "slot": "after_llm_call",
        },
    )


# ---------------------------------------------------------------------------
# F-QA3 helpers — ADK callback context + LlmRequest / LlmResponse stubs
# ---------------------------------------------------------------------------


def _build_llm_call_callback_context(session_id: str, invocation_id: str):
    """Build a SimpleNamespace mock matching the ADK callback_context shape.

    The plugin's ``_resolve_identity`` reads ``callback_context.session.id``
    (uuid-shaped) + ``callback_context.invocation_id`` (turn-scoped). It
    falls back to ``_latest_event_invocation_id(session)`` when
    ``invocation_id`` is missing — we set both so identity resolution is
    deterministic.
    """
    from types import SimpleNamespace  # noqa: PLC0415

    session = SimpleNamespace(id=session_id, events=[])
    return SimpleNamespace(session=session, invocation_id=invocation_id)


def _build_llm_request(text: str):
    """Build a minimal ADK-shaped ``LlmRequest`` carrying ``text``.

    ``_extract_request_text`` walks ``llm_request.contents`` backwards for
    the most-recent ``role="user"`` content. One chunk suffices.
    """
    from types import SimpleNamespace  # noqa: PLC0415

    part = SimpleNamespace(text=text)
    content = SimpleNamespace(role="user", parts=[part])
    return SimpleNamespace(contents=[content])


def _build_llm_response(text: str):
    """Build a minimal ADK-shaped ``LlmResponse`` carrying ``text``.

    ``_extract_response_text`` walks ``llm_response.content.parts`` for
    ``Part.text`` chunks. One chunk suffices.
    """
    from types import SimpleNamespace  # noqa: PLC0415

    part = SimpleNamespace(text=text)
    content = SimpleNamespace(parts=[part])
    return SimpleNamespace(content=content)


def _classify_llm_call_result(
    result: object, *, slot: str
) -> tuple[str, object | None]:
    """Translate the plugin's return value into a ``(verdict, blocked_response)`` pair.

    The plugin returns ``None`` on proceed and a synthetic ``LlmResponse``
    (built via :func:`_build_policy_blocked_llm_response`) on block. The
    synthetic response carries
    ``custom_metadata = {"policy_blocked": True, "reason": "<slot> llm_criterion verdict=block"}``
    (the canonical honest-degrade marker the asserter inspects). The
    ``error_message`` field is NOT set — earlier asserter passes that
    grepped ``error_message`` were stale (the helper was reshaped to
    use ``custom_metadata`` so downstream telemetry / audit can
    attribute the block; see
    ``magi_agent.adk_bridge.lifecycle_llm_call_control``).
    """
    if result is None:
        return ("proceed", None)
    metadata = getattr(result, "custom_metadata", None) or {}
    if isinstance(metadata, dict) and metadata.get("policy_blocked") is True:
        reason = metadata.get("reason") or ""
        if not isinstance(reason, str) or slot in reason or not reason:
            return ("block", result)
    # Fall back to the legacy ``error_message`` shape in case a different
    # plugin happens to use that path; keep it so a non-llm_call block
    # slot's plugin stays detectable here.
    error_message = getattr(result, "error_message", None) or ""
    if "customize_policy_blocked" in error_message and slot in error_message:
        return ("block", result)
    # On any unexpected non-None we surface ``error`` so the asserter raises
    # a clear failure instead of a silent pass.
    return ("error", result)


async def trigger_on_subagent_stop(
    *,
    kind: str,
    rule_id: str,
    expected_action: str,
    session_id: str | None = None,
) -> TriggerOutcome:
    """Drive a CHILD ``run_governed_turn`` and observe on_subagent_stop.

    ``ctx.depth = 1`` so the ``_SubagentStopCollector`` fires (the
    top-level ``_AfterTurnEndCollector`` is inert for child turns —
    the two collectors are disjoint).

    Authorability-lift contract from F-LIFE1: ``_LEGAL`` accepts
    ``{audit, block, ask_approval}`` but runtime parent-surfacing is
    NOT built yet (TODO per F-LIFE1 review pass — parent SpawnAgent
    does not yet consume the verdict). The driver asserts the audit
    ran (engine was invoked, finally block ran) and records the
    block-action verdict for the asserter; it explicitly does NOT
    assert any parent-side block.
    """
    _ = (kind, rule_id, expected_action)
    sid = session_id or "sess_fqa2_oss"
    ctx = _build_turn_ctx(session_id=sid, prompt="child task", depth=1)
    engine = _PoisonRecordingEngine(child_final_text="child final answer text")
    items = await _drive_governed_turn(ctx=ctx, engine=engine)

    engine_invoked = bool(engine.run_turn_stream_calls)
    # F-LIFE1 TODO: parent-surfacing not built. The on_subagent_stop slot
    # cannot short-circuit the parent today, so even with action=block we
    # expect the engine to have streamed normally. The audit ledger
    # captures the verdict for follow-up surfacing.
    blocked_terminals = [
        i for i in items
        if _is_policy_blocked_terminal(i, slot="on_subagent_stop")
    ]
    return TriggerOutcome(
        runtime_verdict="proceed",
        side_effects={
            "items": items,
            "engine_invoked": engine_invoked,
            "blocked_terminals": blocked_terminals,
            "poison_delta": _PoisonRecordingEngine.POISON_DELTA,
            "slot": "on_subagent_stop",
            "depth": 1,
        },
    )


# ---------------------------------------------------------------------------
# F-QA4 late-lifecycle drivers
# ---------------------------------------------------------------------------
#
# These drivers exercise the runtime chokepoints that fan out the F-LIFE3
# (before/after_compaction, on_task_checkpoint, on_artifact_created) and
# F-LIFE4b (on_task_complete, on_session_start) audit + gate slots, plus
# the F4 ``capability_scope`` ``spawn`` slot. ``on_session_end`` is
# *intentionally* not driven — F-LIFE4b ships no transport-side emit
# wire in v1 (validator + helper round-trip only) — and the SKIP marker
# at the bottom of this section documents the honest-degrade.
#
# Each driver invokes its production chokepoint directly and returns the
# observable evidence the asserter inspects:
#
# * compaction drivers: the post-call contents list + an
#   ``audit_records`` proxy populated from a patched judge-call counter
#   (the fan-out helpers do not return audit records to the
#   ``_apply_tail_trim`` caller — the asserter relies on the
#   :class:`JudgePatcher` fixture's ``calls`` list to detect firing and
#   on the contents identity to detect a block-action no-mutation).
# * work-queue checkpoint driver: drives a single-task ``run_once`` and
#   returns the resulting tick tally + the post-tick task status.
# * artifact-created driver: invokes
#   :meth:`FileDeliveryBoundary.execute` with a stub trusted provider
#   returning ``status=ok`` and returns the resulting
#   :class:`FileDeliveryDecision` (asserter inspects the
#   ``reason_codes`` / ``diagnostic_metadata`` for the ``ask`` honest-
#   degrade marker).
# * task-complete driver: drives a real top-level ``run_governed_turn``
#   whose final assistant text carries the line-anchored ``<task_done>``
#   marker so :class:`_OnTaskCompleteCollector` fires.
# * session-start driver: invokes
#   :meth:`LifecycleSessionControl.on_before_model` twice for the same
#   session id and once for a different session id so the asserter can
#   pin the first-fire-per-session contract.
# * spawn driver: invokes
#   :func:`magi_agent.customize.capability_scope.apply_capability_scope`
#   with a single ``denyTools=["shell_exec"]`` rule and asserts the
#   tool was subtracted from the resolved toolset.


async def trigger_before_compaction(
    *,
    kind: str,
    rule_id: str,
    expected_action: str,
    contents_count: int = 8,
    tail_events: int = 2,
) -> TriggerOutcome:
    """Drive :meth:`MagiContextCompactionPlugin._apply_tail_trim` for ``before_compaction``.

    Builds a stub ``LlmRequest`` with ``contents_count`` user/text
    contents, then calls ``_apply_tail_trim`` directly so the
    before_compaction audit + gate emit fires. The asserter detects
    firing via the ``JudgePatcher`` fixture's ``calls`` list (the
    fan-out helpers do not return the audit records to the caller) and
    detects a ``block`` honest-degrade via the post-call ``contents``
    list being unchanged (``_apply_tail_trim`` returns early without
    mutating contents on ``before_compaction`` gate block).
    """
    _ = (kind, rule_id, expected_action)  # asserter consumes these
    plugin, llm_request, original_contents = _build_compaction_fixture(
        contents_count=contents_count, tail_events=tail_events
    )
    contents_after = list(llm_request.contents)
    await plugin._apply_tail_trim(llm_request, contents_after)
    return TriggerOutcome(
        side_effects={
            "slot": "before_compaction",
            "original_contents": original_contents,
            "post_contents": list(llm_request.contents),
            "drove_contents": contents_after,
            "tail_events": tail_events,
        },
    )


async def trigger_after_compaction(
    *,
    kind: str,
    rule_id: str,
    expected_action: str,
    contents_count: int = 8,
    tail_events: int = 2,
) -> TriggerOutcome:
    """Drive :meth:`MagiContextCompactionPlugin._apply_tail_trim` for ``after_compaction``.

    Same plugin call as :func:`trigger_before_compaction` — both emits
    fire from the same ``_apply_tail_trim`` body, so a single drive
    exercises both fan-outs. Audit-only by ``_LEGAL`` (block excluded);
    the asserter only checks that the judge fired, not a no-mutation
    contract.
    """
    _ = (kind, rule_id, expected_action)
    plugin, llm_request, original_contents = _build_compaction_fixture(
        contents_count=contents_count, tail_events=tail_events
    )
    contents_after = list(llm_request.contents)
    await plugin._apply_tail_trim(llm_request, contents_after)
    return TriggerOutcome(
        side_effects={
            "slot": "after_compaction",
            "original_contents": original_contents,
            "post_contents": list(llm_request.contents),
            "drove_contents": contents_after,
        },
    )


def _build_compaction_fixture(
    *, contents_count: int, tail_events: int
):
    """Build the (plugin, llm_request_stub, original_contents) triple.

    Late import keeps the F-QA4 module importable on minimal envs where
    ``google.adk`` is absent (the rest of the matrix can still run).
    """
    from magi_agent.adk_bridge.context_compaction import (  # noqa: PLC0415
        MagiContextCompactionPlugin,
    )

    plugin = MagiContextCompactionPlugin(
        token_threshold=1,
        tail_events=tail_events,
    )
    # Synthetic contents — opaque marker strings keep the fixture cheap.
    # _apply_tail_trim's tail-drop math only needs ``len(contents)`` +
    # the ability to slice; the orphan-adjusted split skips function-
    # response pairs which the marker strings do not match.
    contents = [f"content_{idx}" for idx in range(contents_count)]
    llm_request_stub = SimpleNamespace_for_compaction(contents=list(contents))
    return plugin, llm_request_stub, list(contents)


def SimpleNamespace_for_compaction(*, contents):  # noqa: N802 — helper name
    """Return a SimpleNamespace with a mutable ``contents`` list.

    _apply_tail_trim mutates ``llm_request.contents`` in place on a
    real tail-drop. We hand it a sliceable list so the asserter can
    detect the (lack of) mutation on a block-action row.
    """
    from types import SimpleNamespace  # noqa: PLC0415

    return SimpleNamespace(contents=list(contents))


async def trigger_on_task_checkpoint(
    *,
    kind: str,
    rule_id: str,
    expected_action: str,
    task_title: str = "fqa4 task",
) -> TriggerOutcome:
    """Drive :meth:`WorkQueueDriver.run_once` for ``on_task_checkpoint``.

    Enqueues a single task into an in-memory store, runs one dispatcher
    tick, and returns the resulting :class:`WorkQueueTickResult` tally +
    the post-tick task status. The asserter routes the outcome through
    a dedicated branch:

    * ``audit`` rows: assert at least one transition fired (claimed +
      completed = 2 emits; the judge calls capture the audit).
    * ``block`` rows: the F-LIFE4a wire only honors block at the
      ``claimed`` transition (per the custom_rules._LEGAL review pass
      NOTE in driver.py). Asserter verifies the task moved to
      ``failed`` with the ``customize_policy_blocked`` error sentinel.
    * ``ask_approval`` rows: honest-degrade (audit-only); the task
      proceeds and the audit ledger captures the verdict.
    """
    _ = (kind, rule_id, expected_action)
    from magi_agent.missions.work_queue.driver import WorkQueueDriver  # noqa: PLC0415
    from magi_agent.missions.work_queue.models import WorkTask  # noqa: PLC0415
    from magi_agent.missions.work_queue.runner import (  # noqa: PLC0415
        WorkTaskRunResult,
    )
    from magi_agent.missions.work_queue.store import (  # noqa: PLC0415
        InMemoryWorkQueueStore,
    )

    store = InMemoryWorkQueueStore()
    task = WorkTask(
        id="task_fqa4_001",
        title=task_title,
        status="ready",
        created_at=1_718_000_000,
    )
    store.create(task)

    class _StubRunner:
        async def run_task(self, _task):
            return WorkTaskRunResult(outcome="completed", summary="ok")

    driver = WorkQueueDriver(
        store,
        _StubRunner(),
        claimer="fqa4-dispatcher",
        max_spawn=1,
    )
    # ``WorkQueueDriver.run_once`` is sync and internally drives the
    # async runner + audit fan-outs via ``asyncio.run``. Calling that
    # from our outer ``asyncio.run`` test driver would nest event loops
    # and raise ``RuntimeError: asyncio.run() cannot be called from a
    # running event loop``. Offload to a thread so the driver gets a
    # fresh loop (mirrors the production ``run_forever`` path which
    # uses ``asyncio.to_thread``).
    tick = await asyncio.to_thread(driver.run_once, now=1_718_000_000)
    post = store.get(task.id)
    post_status = getattr(post, "status", None)
    post_error = getattr(post, "last_failure_error", None) if post else None
    return TriggerOutcome(
        side_effects={
            "slot": "on_task_checkpoint",
            "tick_result": tick,
            "post_status": post_status,
            "post_error": post_error,
            "task_id": task.id,
        },
    )


async def trigger_on_artifact_created(
    *,
    kind: str,
    rule_id: str,
    expected_action: str,
) -> TriggerOutcome:
    """Drive :meth:`FileDeliveryBoundary.execute` for ``on_artifact_created``.

    Hands the boundary a fake local-fake artifact provider whose
    ``write_artifact`` returns ``status="ok"`` so the
    ``on_artifact_created`` emit fires. The asserter inspects the
    returned :class:`FileDeliveryDecision`:

    * ``audit`` rows: ``reason_codes`` should NOT carry
      ``artifact_review_pending``; the judge call records evidence the
      rule fired.
    * ``ask_approval`` rows: the boundary's ``_check_artifact_created_gate_sync``
      should derive ``"ask"`` from the audit's ``passed=False`` verdict,
      which the boundary translates into a ``delivery_intent`` decision
      with ``diagnostic_metadata['requires_approval'] is True``. The
      asserter treats this as the audit-ledger requires_approval marker.
    """
    _ = (kind, rule_id, expected_action)
    from magi_agent.artifacts.file_delivery import (  # noqa: PLC0415
        FileDeliveryBoundary,
        FileDeliveryConfig,
        FileDeliveryRequest,
    )

    config = FileDeliveryConfig.model_validate(
        {
            "enabled": True,
            "localFakeArtifactServiceEnabled": True,
        }
    )
    boundary = FileDeliveryBoundary(config)

    class _LocalFakeProvider:
        openmagi_local_fake_provider = True

        def write_artifact(self, request):
            # Return a minimal ok-status payload — _raw_status / digest
            # helpers in file_delivery only inspect ``status`` +
            # ``artifactRef`` so an empty receipt body is sufficient.
            return {
                "status": "ok",
                "artifactRef": "fqa4-artifact-001",
            }

    request = FileDeliveryRequest.model_validate(
        {
            "operation": "file.deliver",
            "requestId": "fqa4-req-001",
            "sessionKey": "fqa4-session-001",
            "artifactRefs": ("fqa4-artifact-001",),
            "filename": "fqa4.txt",
            "mimeType": "text/plain",
            "contentDigest": (
                "sha256:" + "0" * 64
            ),
        }
    )
    # ``FileDeliveryBoundary.execute`` is sync and the F-LIFE3 /
    # F-LIFE4a audit + gate emits inside it call ``asyncio.run`` (the
    # boundary is sync; the fan-out is async). Calling that from our
    # outer ``asyncio.run`` test driver would nest event loops and the
    # boundary would honest-degrade to ``"proceed"``. Offload to a
    # thread so the inner ``asyncio.run`` gets a fresh loop (mirrors
    # the production sync-callsite contract).
    decision = await asyncio.to_thread(
        boundary.execute, request, artifact_provider=_LocalFakeProvider()
    )
    diagnostics = dict(decision.diagnostic_metadata or {})
    return TriggerOutcome(
        side_effects={
            "slot": "on_artifact_created",
            "decision": decision,
            "decision_status": decision.status,
            "decision_reason_codes": tuple(decision.reason_codes or ()),
            "decision_requires_approval": bool(
                diagnostics.get("requires_approval")
            ),
            "decision_approval_slot": diagnostics.get("approval_slot"),
        },
    )


async def trigger_on_task_complete(
    *,
    kind: str,
    rule_id: str,
    expected_action: str,
    session_id: str | None = None,
) -> TriggerOutcome:
    """Drive a top-level ``run_governed_turn`` for ``on_task_complete``.

    The fake engine yields a final ``<task_done>`` marker on its own
    line so :class:`_OnTaskCompleteCollector` fires its audit (the
    marker is line-anchored — prose mentioning the literal string does
    not stale-fire). The asserter:

    * ``audit`` rows: judge fires (recorded by ``JudgePatcher.calls``)
      AND the engine ran normally (no synthetic policy-blocked terminal).
    * ``block`` / ``ask_approval`` rows: F-LIFE4b review pass note —
      the collector annotates audit records with ``requires_approval`` /
      ``gate_verdict`` but the compensating-action wire (turn rollback)
      is deferred. Asserter verifies the engine still ran (proceed) and
      the judge fired; ledger annotations are observability-only in v1.
    """
    _ = (kind, rule_id, expected_action)
    sid = session_id or "sess_fqa4_otc"
    ctx = _build_turn_ctx(session_id=sid, prompt="hello fqa4", depth=0)

    # Custom poison engine that emits the <task_done> marker so the
    # F-LIFE4b collector's line-anchored detector fires.
    class _TaskDoneEngine(_PoisonRecordingEngine):
        async def run_turn_stream(
            self, _none, turn_input, *, cancel, gate
        ):
            from magi_agent.cli.contracts import EngineResult, Terminal
            from magi_agent.runtime.events import RuntimeEvent

            self.run_turn_stream_calls.append(
                {"turn_input": turn_input, "cancel": cancel, "gate": gate}
            )
            yield RuntimeEvent(
                type="token",
                payload={
                    "type": "text_delta",
                    "delta": "task summary\n<task_done>\n",
                },
            )
            yield EngineResult(
                terminal=Terminal.completed,
                usage={"input_tokens": 1, "output_tokens": 1},
                cost_usd=0.0,
                session_id=sid,
                turn_id=f"turn_{sid}",
            )

    engine = _TaskDoneEngine()
    items = await _drive_governed_turn(ctx=ctx, engine=engine)
    engine_invoked = bool(engine.run_turn_stream_calls)
    return TriggerOutcome(
        runtime_verdict="proceed",
        side_effects={
            "slot": "on_task_complete",
            "items": items,
            "engine_invoked": engine_invoked,
            "engine_run_turn_stream_calls": len(engine.run_turn_stream_calls),
        },
    )


async def trigger_on_session_start(
    *,
    kind: str,
    rule_id: str,
    expected_action: str,
    session_id: str | None = None,
) -> TriggerOutcome:
    """Drive :meth:`LifecycleSessionControl.on_before_model` for ``on_session_start``.

    First-fire-per-session contract — invokes ``on_before_model`` TWICE
    on the same ``session_id``. The asserter verifies:

    * First call: the judge fired (audit recorded the verdict). For
      ``block`` rows, the plugin returns a synthetic policy-blocked
      :class:`LlmResponse` carrying ``error_message`` starting with
      ``"customize_policy_blocked:"``.
    * Second call: the judge MUST NOT re-fire (membership check
      short-circuits before the policy load).

    The driver also notes whether each call returned ``None`` (proceed)
    or a synthetic policy-blocked response so the asserter can pin the
    block contract independently of the judge invocation count.
    """
    _ = (kind, rule_id, expected_action)
    from magi_agent.adk_bridge.lifecycle_session_control import (  # noqa: PLC0415
        LifecycleSessionControl,
    )

    sid = session_id or "sess_fqa4_oss_start"
    control = LifecycleSessionControl()
    first_cb = _build_llm_call_callback_context(sid, f"inv_{sid}_1")
    second_cb = _build_llm_call_callback_context(sid, f"inv_{sid}_2")
    first_request = _build_llm_request("first prompt of session")
    second_request = _build_llm_request("second prompt of session")

    first_result = await control.on_before_model(
        callback_context=first_cb, llm_request=first_request
    )
    second_result = await control.on_before_model(
        callback_context=second_cb, llm_request=second_request
    )

    first_verdict, first_blocked = _classify_llm_call_result(
        first_result, slot="on_session_start"
    )
    # The "second call" cheap membership check returns None whether the
    # action is audit or block — the first-fire-per-session contract
    # demands silence on the second call. We surface that observation
    # so the asserter can pin it.
    second_was_silent = second_result is None
    return TriggerOutcome(
        runtime_verdict=first_verdict,
        side_effects={
            "slot": "on_session_start",
            "first_result": first_result,
            "first_blocked_response": first_blocked,
            "second_result": second_result,
            "second_was_silent": second_was_silent,
            "session_id": sid,
        },
    )


async def trigger_spawn(
    *,
    kind: str,
    rule_id: str,
    expected_action: str,
) -> TriggerOutcome:
    """Drive :func:`apply_capability_scope` for the ``spawn`` slot.

    The F4 production wire (:mod:`magi_agent.runtime.child_runner_live`)
    composes ``policy.enabled_capability_scope_rules()`` with the
    resolved profile toolset and threads the result into
    :func:`apply_capability_scope`. Driving the function directly with
    the persisted rule + a synthetic toolset covers the deterministic
    subtraction contract; the production composition (parent_cap →
    capability_scope → allowedTools → spawn_cap) is covered by the
    ``test_capability_scope_*`` firing tests.

    The matrix only exposes ``capability_scope`` at ``spawn`` with
    action ``block`` (no audit / ask_approval); asserter verifies the
    denied tool was subtracted from the post-call toolset.
    """
    _ = (kind, rule_id, expected_action)
    from magi_agent.customize.capability_scope import (  # noqa: PLC0415
        apply_capability_scope,
    )
    from magi_agent.customize.store import load_overrides  # noqa: PLC0415
    from magi_agent.customize.verification_policy import (  # noqa: PLC0415
        CustomizeVerificationPolicy,
    )

    policy = CustomizeVerificationPolicy.from_overrides(load_overrides())
    rules = policy.enabled_capability_scope_rules()

    # Synthetic toolset: opaque objects with a ``name`` attribute so
    # the trigger does not depend on the production tool registry.
    class _ToolStub:
        def __init__(self, name: str) -> None:
            self.name = name

    def _tool_name(tool: object) -> str:
        return getattr(tool, "name", "")

    tools = [_ToolStub("shell_exec"), _ToolStub("read_file")]
    original_names = [_tool_name(t) for t in tools]
    narrowed, _capped_class = apply_capability_scope(
        tools, rules=rules, tool_name_fn=_tool_name
    )
    narrowed_names = [_tool_name(t) for t in narrowed]
    return TriggerOutcome(
        side_effects={
            "slot": "spawn",
            "rules_loaded": len(rules),
            "original_tool_names": original_names,
            "narrowed_tool_names": narrowed_names,
            "removed_tool_names": [
                n for n in original_names if n not in narrowed_names
            ],
        },
    )


# F-QA4 honest-degrade SKIP marker for ``on_session_end`` — kept here as
# a documentation seam so a future PR that lands the transport-side emit
# wire can swap this placeholder for a real driver. The matrix test file
# uses ``pytest.mark.skipif`` keyed on the slot value so the row is
# collected (visible) but skipped (not executed) with a deterministic
# reason.
ON_SESSION_END_SKIP_REASON = (
    "F-LIFE4b honest-degrade — no transport-side emit wire shipped in v1; "
    "validator + helper round-trip only (see custom_rules._LEGAL)."
)


# ---------------------------------------------------------------------------
# Sync wrappers
# ---------------------------------------------------------------------------


def run_async(coro: Awaitable[TriggerOutcome]) -> TriggerOutcome:
    """Sync entry for pytest test functions that want to remain non-async."""
    return asyncio.run(coro)  # type: ignore[arg-type]
