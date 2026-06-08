"""Track 19 — wire the GA per-turn constraint reminder into the live ControlPlane.

These tests exercise :class:`GaConstraintReinjectionControl` (the ADK
``on_before_model`` adapter) end-to-end against the *real* control plane, the
*real* :class:`GeneralAutomationReceiptLedgerStore`, and the *real*
:func:`ga_constraint_reinjection` reminder builder — no reminder logic is
re-implemented here.

Default-OFF / inert contract:

* ``build_default_plugin()`` with no GA data registers no constraint control, so
  every existing no-arg caller (local_runner + all other tests) is byte-identical.
* ``MAGI_GA_LIVE_ENABLED`` OFF, a non-``general`` role, or a turn whose ledger
  already carries the owed evidence → ``on_before_model`` makes no mutation.

Activation (flag ON + ``general`` + something still owed) appends a reminder to
``llm_request.contents`` *without clearing tools* (unlike the max-steps brake).
"""

from __future__ import annotations

import asyncio

from magi_agent.adk_bridge.control_plane import (
    GaConstraintReinjectionControl,
    build_default_plane,
    build_default_plugin,
)
from magi_agent.harness.general_automation.control_projection import (
    GeneralAutomationControlProjection,
    GeneralAutomationControlProjectionRequest,
    build_general_automation_control_projection,
)
from magi_agent.harness.general_automation.live_gate import (
    GeneralAutomationReceiptLedgerStore,
)
from magi_agent.harness.general_automation.task_completion import (
    RequiredDeliverableEvidence,
)
from magi_agent.evidence.ledger import EvidenceLedger


_SESSION_ID = "session-1"
_TURN_ID = "turn-1"
_FLAG_ON = {"MAGI_GA_LIVE_ENABLED": "1"}
_FLAG_OFF = {"MAGI_GA_LIVE_ENABLED": "0"}


def _run(coro):
    return asyncio.run(coro)


class _FakeLlmRequest:
    """Fake LlmRequest-like with a mutable contents list and tools config."""

    def __init__(self, contents=None, tools=None):
        self.contents = list(contents or [])
        self.config = _FakeConfig(tools=list(tools or []))


class _FakeConfig:
    def __init__(self, tools=None):
        self.tools = list(tools or [])


class _FakeSession:
    def __init__(self, session_id: str):
        self.id = session_id


class _FakeCallbackContext:
    def __init__(self, session_id: str, invocation_id: str):
        self.session = _FakeSession(session_id)
        self.invocation_id = invocation_id


def _ledger() -> EvidenceLedger:
    return EvidenceLedger(
        ledgerId=f"ledger-{_SESSION_ID}-{_TURN_ID}",
        sessionId=_SESSION_ID,
        turnId=_TURN_ID,
        runOn="main",
        agentRole="general",
        spawnDepth=0,
        sourceKind="tool_trace",
        producerSurface="tool_host",
    )


def _required_artifact() -> RequiredDeliverableEvidence:
    return RequiredDeliverableEvidence(requires_artifact_ref=True)


def _approval_control() -> GeneralAutomationControlProjection:
    digest = "sha256:" + "a" * 64
    request = GeneralAutomationControlProjectionRequest(
        controlType="approval_required",
        subjectRef=digest,
        policyRef="policy:general-automation:path-policy",
        payloadDigest=digest,
        reasonCodes=("external_directory_requires_approval",),
        approvalRef="approval:external-directory:" + digest,
    )
    return build_general_automation_control_projection(request)


class _FakeToolContext:
    """Minimal ToolContext stand-in for the store's _ledger_key lookup."""

    def __init__(self, session_id: str, turn_id: str):
        self.session_id = session_id
        self.session_key = None
        self.bot_id = None
        self.turn_id = turn_id


# ---------------------------------------------------------------------------
# (e) open-controls accumulator on the store
# ---------------------------------------------------------------------------


def test_store_accumulates_and_returns_open_controls() -> None:
    store = GeneralAutomationReceiptLedgerStore()
    control = _approval_control()
    context = _FakeToolContext(_SESSION_ID, _TURN_ID)

    assert store.open_controls_for_turn(session_id=_SESSION_ID, turn_id=_TURN_ID) == []

    store.append_control(context, control)

    retained = store.open_controls_for_turn(session_id=_SESSION_ID, turn_id=_TURN_ID)
    assert retained == [control]


def test_store_open_controls_empty_for_unknown_turn() -> None:
    store = GeneralAutomationReceiptLedgerStore()
    assert store.open_controls_for_turn(session_id="x", turn_id="y") == []


# ---------------------------------------------------------------------------
# (a) no-arg build → control NOT registered (byte-identical)
# ---------------------------------------------------------------------------


def test_constraint_control_not_registered_with_no_args() -> None:
    plane = build_default_plane(os_environ=_FLAG_ON)
    assert not any(
        isinstance(c, GaConstraintReinjectionControl) for c in plane._controls
    )


def test_constraint_control_not_registered_without_contract() -> None:
    plane = build_default_plane(
        os_environ=_FLAG_ON,
        general_automation_receipts=GeneralAutomationReceiptLedgerStore(),
        contract_required=None,
    )
    assert not any(
        isinstance(c, GaConstraintReinjectionControl) for c in plane._controls
    )


def test_constraint_control_not_registered_without_receipts() -> None:
    plane = build_default_plane(
        os_environ=_FLAG_ON,
        general_automation_receipts=None,
        contract_required=_required_artifact(),
    )
    assert not any(
        isinstance(c, GaConstraintReinjectionControl) for c in plane._controls
    )


def test_build_default_plugin_no_arg_has_no_constraint_control() -> None:
    plugin = build_default_plugin()
    assert not any(
        isinstance(c, GaConstraintReinjectionControl) for c in plugin._p._controls
    )


def test_constraint_control_registered_with_receipts_and_contract() -> None:
    plane = build_default_plane(
        os_environ=_FLAG_ON,
        general_automation_receipts=GeneralAutomationReceiptLedgerStore(),
        contract_required=_required_artifact(),
    )
    assert any(
        isinstance(c, GaConstraintReinjectionControl) for c in plane._controls
    )


# ---------------------------------------------------------------------------
# (b) flag-ON + general + ledger lacks owed artifact → reminder appended
# ---------------------------------------------------------------------------


def _build_control(
    *,
    env,
    store: GeneralAutomationReceiptLedgerStore,
    contract=None,
    agent_role: str = "general",
) -> GaConstraintReinjectionControl:
    return GaConstraintReinjectionControl(
        receipts=store,
        contract_required=contract if contract is not None else _required_artifact(),
        agent_role=agent_role,
        env=env,
    )


def test_appends_reminder_when_owed_and_flag_on() -> None:
    store = GeneralAutomationReceiptLedgerStore()
    # Seed a ledger for this turn that does NOT carry the owed artifact ref.
    store._ledgers[(_SESSION_ID, _TURN_ID)] = _ledger()
    ctrl = _build_control(env=_FLAG_ON, store=store)
    request = _FakeLlmRequest(
        contents=[{"role": "user", "content": "go"}],
        tools=[{"type": "function", "name": "Read"}],
    )

    _run(
        ctrl.on_before_model(
            callback_context=_FakeCallbackContext(_SESSION_ID, _TURN_ID),
            llm_request=request,
        )
    )

    # A reminder was appended (artifactRef is the owed label).
    assert _contents_contains(request.contents, "artifactRef")
    # Tools are NOT cleared (unlike the max-steps brake).
    assert len(request.config.tools) == 1


def test_appends_open_approval_control_reminder() -> None:
    store = GeneralAutomationReceiptLedgerStore()
    # A turn with the artifact satisfied but an open approval control still owed.
    satisfied = _ledger().append_artifact_ref("artifact:spreadsheet:out")
    store._ledgers[(_SESSION_ID, _TURN_ID)] = satisfied
    store.append_control(
        _FakeToolContext(_SESSION_ID, _TURN_ID), _approval_control()
    )
    ctrl = _build_control(env=_FLAG_ON, store=store)
    request = _FakeLlmRequest(contents=[], tools=[])

    _run(
        ctrl.on_before_model(
            callback_context=_FakeCallbackContext(_SESSION_ID, _TURN_ID),
            llm_request=request,
        )
    )

    assert _contents_contains(request.contents, _approval_control().control_ref)


# ---------------------------------------------------------------------------
# (c) flag-OFF → no append
# ---------------------------------------------------------------------------


def test_no_append_when_flag_off() -> None:
    store = GeneralAutomationReceiptLedgerStore()
    store._ledgers[(_SESSION_ID, _TURN_ID)] = _ledger()
    ctrl = _build_control(env=_FLAG_OFF, store=store)
    request = _FakeLlmRequest(contents=[{"role": "user", "content": "go"}])

    _run(
        ctrl.on_before_model(
            callback_context=_FakeCallbackContext(_SESSION_ID, _TURN_ID),
            llm_request=request,
        )
    )

    assert request.contents == [{"role": "user", "content": "go"}]


# ---------------------------------------------------------------------------
# (d) nothing owed (ledger has the artifact) → no append
# ---------------------------------------------------------------------------


def test_no_append_when_nothing_owed() -> None:
    store = GeneralAutomationReceiptLedgerStore()
    satisfied = _ledger().append_artifact_ref("artifact:spreadsheet:out")
    store._ledgers[(_SESSION_ID, _TURN_ID)] = satisfied
    ctrl = _build_control(env=_FLAG_ON, store=store)
    request = _FakeLlmRequest(contents=[{"role": "user", "content": "go"}])

    _run(
        ctrl.on_before_model(
            callback_context=_FakeCallbackContext(_SESSION_ID, _TURN_ID),
            llm_request=request,
        )
    )

    assert request.contents == [{"role": "user", "content": "go"}]


def test_no_append_when_ledger_missing_for_turn() -> None:
    store = GeneralAutomationReceiptLedgerStore()  # no ledger seeded
    ctrl = _build_control(env=_FLAG_ON, store=store)
    request = _FakeLlmRequest(contents=[{"role": "user", "content": "go"}])

    _run(
        ctrl.on_before_model(
            callback_context=_FakeCallbackContext(_SESSION_ID, _TURN_ID),
            llm_request=request,
        )
    )

    assert request.contents == [{"role": "user", "content": "go"}]


# ---------------------------------------------------------------------------
# (f) non-general role → no append
# ---------------------------------------------------------------------------


def test_no_append_when_non_general_role() -> None:
    store = GeneralAutomationReceiptLedgerStore()
    store._ledgers[(_SESSION_ID, _TURN_ID)] = _ledger()
    ctrl = _build_control(env=_FLAG_ON, store=store, agent_role="coding")
    request = _FakeLlmRequest(contents=[{"role": "user", "content": "go"}])

    _run(
        ctrl.on_before_model(
            callback_context=_FakeCallbackContext(_SESSION_ID, _TURN_ID),
            llm_request=request,
        )
    )

    assert request.contents == [{"role": "user", "content": "go"}]


def _contents_contains(contents, needle: str) -> bool:
    for c in contents:
        if isinstance(c, dict) and needle in str(c.get("content", "")):
            return True
        parts = getattr(c, "parts", None) or []
        for p in parts:
            text = getattr(p, "text", None)
            if isinstance(text, str) and needle in text:
                return True
    return False
