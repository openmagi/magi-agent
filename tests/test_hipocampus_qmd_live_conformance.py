from __future__ import annotations

import pytest

from magi_agent.memory.conformance import (
    HipocampusQmdLiveRecallConformance,
    check_hipocampus_qmd_live_recall_conformance,
)


def test_gate_off_field_false_and_parity_pin_false(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MAGI_MEMORY_QMD_LIVE_ENABLED", raising=False)

    report = check_hipocampus_qmd_live_recall_conformance()

    # The NEW gated field reflects the adapter recall gate (off here).
    assert report.hipocampus_qmd_live_recall_gated is False
    # The shadow/parity pin stays False and is NEVER coupled to the gate.
    assert report.hipocampus_qmd_calls is False


def test_gate_on_field_true_but_parity_pin_stays_false(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MAGI_MEMORY_QMD_LIVE_ENABLED", "1")

    report = check_hipocampus_qmd_live_recall_conformance()

    assert report.hipocampus_qmd_live_recall_gated is True
    # Critical: enabling gated live recall must NOT flip the parity pin.
    assert report.hipocampus_qmd_calls is False


def test_parity_pin_is_pinned_false_literal() -> None:
    # The parity pin must be force-falsed even when a caller asserts True
    # via direct construction. Post C-4 PR-H the migration to
    # ``FalseOnlyAuthorityModel`` converts the legacy raise-on-True semantic
    # to a coerce-to-False semantic (the kernel's ``_force_false`` validator
    # rewrites True->False BEFORE pydantic's Literal[False] validator runs).
    # The end-state invariant is preserved: ``hipocampus_qmd_calls`` reads
    # False on the constructed instance.
    report = HipocampusQmdLiveRecallConformance(
        hipocampusQmdLiveRecallGated=True,
        hipocampusQmdCalls=True,  # type: ignore[arg-type]
    )
    assert report.hipocampus_qmd_calls is False


def test_gated_field_can_be_true_while_pin_false_via_construction() -> None:
    report = HipocampusQmdLiveRecallConformance(
        hipocampusQmdLiveRecallGated=True,
    )
    assert report.hipocampus_qmd_live_recall_gated is True
    assert report.hipocampus_qmd_calls is False
