"""S-A: GaConstraintReinjectionControl reads a resolved EvidenceLedgerView off
the typed ControlPlaneContext instead of holding the mutable receipt-store.

These tests pin the *typed-context path* (``apply_before_model``): the control is
constructed WITHOUT a receipts store and reads the already-resolved view. The
legacy store-backed ``on_before_model`` path is covered by
``tests/test_ga_constraint_control.py`` (kept byte-identical) and by the Phase-0
``ga_constraint`` golden.
"""

from __future__ import annotations

import asyncio

from magi_agent.adk_bridge.control_plane import GaConstraintReinjectionControl
from magi_agent.packs.context import ControlPlaneContext, EvidenceLedgerView
from magi_agent.harness.general_automation.task_completion import (
    RequiredDeliverableEvidence,
)
from magi_agent.evidence.ledger import EvidenceLedger

_FLAG_ON = {"MAGI_GA_LIVE_ENABLED": "1"}
_FLAG_OFF = {"MAGI_GA_LIVE_ENABLED": "0"}


class _Req:
    def __init__(self):
        self.contents = [{"role": "user", "content": "go"}]


def _ledger() -> EvidenceLedger:
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


def test_ga_control_constructs_without_store() -> None:
    # The S-A migration relaxes the constructor: a control can be built for the
    # typed-context path with no receipt-store handle at all.
    ctrl = GaConstraintReinjectionControl(env=_FLAG_ON)
    assert isinstance(ctrl, GaConstraintReinjectionControl)


def test_ga_control_appends_reminder_from_context_view_no_store() -> None:
    # The control reads the resolved EvidenceLedgerView; no store object involved.
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
    assert _contents_contains(req.contents, "artifactRef")


def test_ga_control_no_view_is_noop() -> None:
    ctrl = GaConstraintReinjectionControl(env=_FLAG_ON)
    req = _Req()
    ctx = ControlPlaneContext.minimal(evidence=None)
    asyncio.run(ctrl.apply_before_model(ctx, llm_request=req))
    assert req.contents == [{"role": "user", "content": "go"}]


def test_ga_control_view_without_ledger_is_noop() -> None:
    ctrl = GaConstraintReinjectionControl(env=_FLAG_ON)
    view = EvidenceLedgerView(
        ledger=None,
        open_controls=(),
        contract_required=RequiredDeliverableEvidence(requires_artifact_ref=True),
        agent_role="general",
    )
    req = _Req()
    ctx = ControlPlaneContext.minimal(evidence=view)
    asyncio.run(ctrl.apply_before_model(ctx, llm_request=req))
    assert req.contents == [{"role": "user", "content": "go"}]


def test_ga_control_flag_off_view_is_noop() -> None:
    # Even with an owed ledger on the view, flag-OFF means ga_constraint_reinjection
    # returns None -> no append (byte-identical to the store path's flag-OFF case).
    ctrl = GaConstraintReinjectionControl(env=_FLAG_OFF)
    view = EvidenceLedgerView(
        ledger=_ledger(),
        open_controls=(),
        contract_required=RequiredDeliverableEvidence(requires_artifact_ref=True),
        agent_role="general",
    )
    req = _Req()
    ctx = ControlPlaneContext.minimal(evidence=view)
    asyncio.run(ctrl.apply_before_model(ctx, llm_request=req))
    assert req.contents == [{"role": "user", "content": "go"}]


def test_ga_control_non_general_role_view_is_noop() -> None:
    ctrl = GaConstraintReinjectionControl(env=_FLAG_ON)
    view = EvidenceLedgerView(
        ledger=_ledger(),
        open_controls=(),
        contract_required=RequiredDeliverableEvidence(requires_artifact_ref=True),
        agent_role="coding",
    )
    req = _Req()
    ctx = ControlPlaneContext.minimal(evidence=view)
    asyncio.run(ctrl.apply_before_model(ctx, llm_request=req))
    assert req.contents == [{"role": "user", "content": "go"}]
