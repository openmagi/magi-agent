from __future__ import annotations

import importlib
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
DEAD_MODULE_PATH = (
    REPO_ROOT / "magi_agent" / "evidence" / "enforcement_boundary.py"
)
DEAD_SYMBOLS = (
    "EvidenceEnforcementBoundary",
    "EvidenceEnforcementConfig",
    "EvidenceEnforcementDecision",
    "EvidenceEnforcementRequest",
    "EvidenceEnforcementAuthorityFlags",
)


def test_dead_enforcement_boundary_module_is_deleted() -> None:
    """The dead EvidenceEnforcementBoundary parallel stack (consumer-0,
    export-0, permanent Literal[False] authority) must be removed; the live
    enforcement path is the engine pre-final gate / verifier bus."""
    assert not DEAD_MODULE_PATH.exists(), (
        f"{DEAD_MODULE_PATH} should be deleted (dead parallel enforcement stack)"
    )


def test_dead_enforcement_boundary_module_is_not_importable() -> None:
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("magi_agent.evidence.enforcement_boundary")


def test_dead_enforcement_boundary_symbols_absent_from_evidence_package() -> None:
    package = importlib.import_module("magi_agent.evidence")
    for symbol in DEAD_SYMBOLS:
        assert not hasattr(package, symbol), (
            f"magi_agent.evidence should not expose dead symbol {symbol!r}"
        )


def test_no_source_reference_to_dead_boundary_symbol() -> None:
    """No first-party source module may reference the deleted boundary class
    (the package should converge on a single live enforcement path)."""
    pkg_root = REPO_ROOT / "magi_agent"
    offenders: list[str] = []
    for path in pkg_root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if "EvidenceEnforcementBoundary" in text:
            offenders.append(str(path.relative_to(REPO_ROOT)))
    assert not offenders, (
        "EvidenceEnforcementBoundary still referenced in source: " + ", ".join(offenders)
    )
