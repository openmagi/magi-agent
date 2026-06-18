"""H2-C6: parallel-research source-count cross-check + opt_in seam.

``_parallel_research_missing_labels`` blocks a research-recipe turn that
synthesized from fewer than ``_PARALLEL_RESEARCH_MIN_SOURCES`` inspected sources.
Gated by ``MAGI_VERIFY_PARALLEL_RESEARCH`` OR the ``parallel-research`` Customize
preset, and scoped to research packs so a coding/chat turn that incidentally ran
one search is never blocked. Byte-identical when both gates are off.

Driven directly at the method (no engine-driver harness) so the result is
governed entirely by the source count + pack scope.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from magi_agent.cli.engine import MagiEngineDriver, RunnerPolicyAssembly
from magi_agent.customize.store import set_verification_override

_BLOCK = "parallel_research:insufficient_sources"


def _driver(*, pack: str = "openmagi.research") -> MagiEngineDriver:
    return MagiEngineDriver(
        runner=None,
        runner_policy_assembly=RunnerPolicyAssembly(
            modelProvider="local",
            modelLabel="local-dev",
            selectedPackIds=(pack,),
            evidenceRequirements=(),
            requiredValidators=(),
            missingEvidenceAction="block",
        ),
        evidence_collector=lambda _turn: (),
    )


def _sources(n: int) -> tuple[dict[str, object], ...]:
    return tuple(
        {"type": "SourceInspection", "status": "ok", "preview": f"src {i}"}
        for i in range(n)
    )


@pytest.fixture
def cfile(monkeypatch, tmp_path) -> Path:
    path = tmp_path / "customize.json"
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(path))
    return path


def _enable_preset(path: Path) -> None:
    set_verification_override("harness_presets", "parallel-research", True, path=path)


def _labels(driver: MagiEngineDriver, records) -> list[str]:
    return driver._parallel_research_missing_labels(records)


def test_inert_when_all_off(monkeypatch, cfile):
    # Byte-identical to main: flag off + no customize ⇒ no block even on a
    # single-source research turn.
    monkeypatch.setenv("MAGI_VERIFY_PARALLEL_RESEARCH", "0")
    monkeypatch.delenv("MAGI_CUSTOMIZE_VERIFICATION_ENABLED", raising=False)
    assert _labels(_driver(), _sources(1)) == []


def test_env_flag_blocks_single_source_research_turn(monkeypatch, cfile):
    monkeypatch.setenv("MAGI_VERIFY_PARALLEL_RESEARCH", "1")
    assert _labels(_driver(), _sources(1)) == [_BLOCK]
    assert _labels(_driver(), _sources(0)) == [_BLOCK]


def test_env_flag_passes_when_enough_sources(monkeypatch, cfile):
    monkeypatch.setenv("MAGI_VERIFY_PARALLEL_RESEARCH", "1")
    assert _labels(_driver(), _sources(2)) == []
    assert _labels(_driver(), _sources(5)) == []


def test_preset_toggle_activates(monkeypatch, cfile):
    monkeypatch.setenv("MAGI_VERIFY_PARALLEL_RESEARCH", "0")
    monkeypatch.setenv("MAGI_CUSTOMIZE_VERIFICATION_ENABLED", "1")
    _enable_preset(cfile)
    assert _labels(_driver(), _sources(1)) == [_BLOCK]


def test_not_activated_when_master_flag_off(monkeypatch, cfile):
    # Profile-aware default-ON customize master; explicit "0" exercises off path.
    monkeypatch.setenv("MAGI_VERIFY_PARALLEL_RESEARCH", "0")
    monkeypatch.setenv("MAGI_CUSTOMIZE_VERIFICATION_ENABLED", "0")
    _enable_preset(cfile)
    assert _labels(_driver(), _sources(1)) == []


def test_non_research_turn_never_blocked(monkeypatch, cfile):
    # A coding turn that incidentally inspected one source must NOT be blocked —
    # the check is scoped to research recipe packs only.
    monkeypatch.setenv("MAGI_VERIFY_PARALLEL_RESEARCH", "1")
    assert _labels(_driver(pack="openmagi.dev-coding"), _sources(1)) == []


def test_other_research_packs_in_scope(monkeypatch, cfile):
    monkeypatch.setenv("MAGI_VERIFY_PARALLEL_RESEARCH", "1")
    assert _labels(_driver(pack="openmagi.source-grounded"), _sources(1)) == [_BLOCK]
    assert _labels(_driver(pack="openmagi.web-acquisition"), _sources(1)) == [_BLOCK]
