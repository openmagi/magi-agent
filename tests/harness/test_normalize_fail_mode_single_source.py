"""F-14 — single fail-mode normalization seam.

``FailureRoutingMetadata._infer_fail_mode`` and
``VerifierMetadata._apply_hard_safety_defaults`` used to inline the
same ``failOpen``/``failClosed`` boolean-inversion block verbatim.
F-14 collapses both into ``harness/verifier_bus._normalize_fail_mode``.
This module locks the consolidation:

1. The helper inverts correctly across the 4 combinations of supplied
   booleans (only ``failOpen`` / only ``failClosed`` / both / neither).
2. The helper is alias-aware (snake_case ``fail_open`` vs camelCase
   ``failOpen``).
3. Both real model validators agree byte-for-byte on the derived
   fail-mode boolean — proving they share the seam.
"""

from __future__ import annotations

import pytest

from magi_agent.harness.verifier_bus import (
    FailureRoutingMetadata,
    VerifierMetadata,
    _normalize_fail_mode,
)


# ---------------------------------------------------------------------------
# Helper truth table (4 cells per the F-14 plan).
# ---------------------------------------------------------------------------


def test_only_fail_open_true_derives_fail_closed_false() -> None:
    out = _normalize_fail_mode({"failOpen": True})
    assert out["failOpen"] is True
    assert out["failClosed"] is False


def test_only_fail_open_false_derives_fail_closed_true() -> None:
    out = _normalize_fail_mode({"failOpen": False})
    assert out["failOpen"] is False
    assert out["failClosed"] is True


def test_only_fail_closed_true_derives_fail_open_false() -> None:
    out = _normalize_fail_mode({"failClosed": True})
    assert out["failClosed"] is True
    assert out["failOpen"] is False


def test_only_fail_closed_false_derives_fail_open_true() -> None:
    out = _normalize_fail_mode({"failClosed": False})
    assert out["failClosed"] is False
    assert out["failOpen"] is True


def test_both_supplied_unchanged() -> None:
    """When both are supplied the helper does not override either — the
    after-validator rejects the ``failOpen==failClosed`` ambiguity
    downstream."""

    out = _normalize_fail_mode({"failOpen": True, "failClosed": True})
    assert out["failOpen"] is True
    assert out["failClosed"] is True


def test_neither_supplied_unchanged() -> None:
    """When neither is supplied the helper makes no decision — the
    model's defaults (``fail_open=False``, ``fail_closed=True``) win."""

    out = _normalize_fail_mode({"otherField": "value"})
    assert "failOpen" not in out
    assert "failClosed" not in out
    assert out["otherField"] == "value"


# ---------------------------------------------------------------------------
# Alias-aware: snake_case input -> snake_case derived key.
# ---------------------------------------------------------------------------


def test_snake_case_fail_open_derives_snake_case_fail_closed() -> None:
    out = _normalize_fail_mode({"fail_open": True})
    assert out["fail_open"] is True
    assert out["fail_closed"] is False
    assert "failClosed" not in out


def test_camel_case_fail_open_derives_camel_case_fail_closed() -> None:
    out = _normalize_fail_mode({"failOpen": True})
    assert out["failOpen"] is True
    assert out["failClosed"] is False
    assert "fail_closed" not in out


# ---------------------------------------------------------------------------
# Real model validator parity — both consult the same seam.
# ---------------------------------------------------------------------------


_VERIFIER_BASE: dict[str, object] = {
    "verifierId": "vf-test",
    "description": "Test verifier",
    "stage": "tool_evidence_contract",
    "phase": "deterministic",
    "priority": 50,
}


@pytest.mark.parametrize(
    "supplied",
    [
        {"failOpen": True},
        {"failOpen": False},
        {"failClosed": True},
        {"failClosed": False},
        {"fail_open": True},
        {"fail_closed": False},
    ],
)
def test_failure_routing_metadata_and_verifier_metadata_agree(
    supplied: dict[str, object],
) -> None:
    """Construct both models from the same fail-mode payload and
    assert their derived ``fail_open``/``fail_closed`` snake_case
    fields end up byte-equal — proving they share ``_normalize_fail_mode``."""

    failure = FailureRoutingMetadata.model_validate(dict(supplied))
    verifier = VerifierMetadata.model_validate({**_VERIFIER_BASE, **supplied})
    assert failure.fail_open == verifier.fail_open
    assert failure.fail_closed == verifier.fail_closed


def test_verifier_metadata_hard_safety_branch_still_force_closes() -> None:
    """Regression guard: ``hardSafety=True`` triggers the dedicated
    hard-safety branch in ``_apply_hard_safety_defaults`` (which
    deliberately bypasses the fail-mode seam) and force-sets
    ``fail_closed=True``."""

    vf = VerifierMetadata.model_validate(
        {
            **_VERIFIER_BASE,
            "verifierId": "vf-hard",
            "stage": "security_policy",
            "hardSafety": True,
        }
    )
    assert vf.hard_safety is True
    assert vf.fail_closed is True
    assert vf.fail_open is False


# ---------------------------------------------------------------------------
# Meta-test: forbid a third inlined copy from appearing.
# ---------------------------------------------------------------------------


def test_no_third_copy_of_fail_mode_inversion() -> None:
    """Forbid the inversion line ``"failOpen" in data or "fail_open" in
    data`` from appearing anywhere under ``magi_agent/`` outside
    ``verifier_bus.py``. Any new caller must route through
    ``_normalize_fail_mode``."""

    from pathlib import Path

    package_root = Path(__file__).resolve().parents[1] / "magi_agent"
    if not package_root.exists():
        package_root = Path(__file__).resolve().parents[2] / "magi_agent"
    assert package_root.exists()

    canonical = {"verifier_bus.py"}
    offenders: list[str] = []
    for path in package_root.rglob("*.py"):
        if path.name in canonical:
            continue
        if "tests" in path.relative_to(package_root).parts:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if '"failOpen" in data or "fail_open" in data' in text:
            offenders.append(str(path.relative_to(package_root)))
    assert offenders == [], (
        "Found a third inlined fail-mode inversion line. Route through "
        "``harness/verifier_bus._normalize_fail_mode`` instead. "
        f"Offenders: {offenders}"
    )
