"""WS6 PR6b: MAGI_FINAL_OUTPUT_GATE_LOCAL_ENABLED activates the live finalizer.

Design: WS6 deterministic-verification activation, PR6b. The live-finalizer
``FinalOutputGate`` defaults disabled and short-circuits to ``skipped`` via
``gate_is_live``. With ``MAGI_FINAL_OUTPUT_GATE_LOCAL_ENABLED`` ON, the engine
path resolves a live config (both booleans True) so an opted-in recipe's gate
evaluates locally; with it OFF the gate stays ``skipped`` (byte-identical to
``main``).
"""
from __future__ import annotations

from magi_agent.evidence.final_output_gate import (
    FinalOutputGate,
    FinalOutputGateRequest,
    resolve_local_final_output_gate_config,
)
from magi_agent.evidence.gate_activation import gate_is_live

_FLAG = "MAGI_FINAL_OUTPUT_GATE_LOCAL_ENABLED"


def _request() -> FinalOutputGateRequest:
    return FinalOutputGateRequest(
        domain="research",
        outputText="The launch shipped in 2026.",
        citations=(),
        evidenceRecords=(),
        requiredEvidence=("source_ledger",),
        modelTier="standard",
        uncertainty="unknown",
    )


def test_final_output_gate_local_enable(monkeypatch) -> None:
    monkeypatch.delenv(_FLAG, raising=False)

    # OFF: the resolved config is not live, the gate short-circuits to skipped.
    off_config = resolve_local_final_output_gate_config(env={})
    assert gate_is_live(off_config) is False
    off_decision = FinalOutputGate(off_config).evaluate(_request())
    assert off_decision.status == "skipped"

    # ON: the resolved config is live, the gate evaluates (not skipped).
    monkeypatch.setenv(_FLAG, "1")
    on_config = resolve_local_final_output_gate_config()
    assert gate_is_live(on_config) is True
    on_decision = FinalOutputGate(on_config).evaluate(_request())
    assert on_decision.status != "skipped"


def test_resolver_off_is_disabled_default(monkeypatch) -> None:
    # Explicit env mapping path: an empty mapping resolves to the disabled default.
    monkeypatch.delenv(_FLAG, raising=False)
    assert gate_is_live(resolve_local_final_output_gate_config(env={})) is False
    assert (
        gate_is_live(resolve_local_final_output_gate_config(env={_FLAG: "0"})) is False
    )
    assert gate_is_live(resolve_local_final_output_gate_config(env={_FLAG: "1"})) is True
