"""Bucket-A seam: deterministic-evidence is an EVIDENCE-kind opt-out.

The dev-coding pack requires the recorded ``evidence:git-diff`` /
``evidence:test-run`` evidence on coding turns (emitted by ``_inferred_refs``).
The ``deterministic-evidence`` preset (default-ON, opt_out, controls_kind=
"evidence") lets a user opt OUT of that requirement: disabling it subtracts those
refs from the assembled ``required_evidence`` via
``_apply_customize_evidence_overrides``. Remove-only and flag-gated, so with no
override the evidence list is byte-identical.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from magi_agent.cli.real_runner import _apply_customize_evidence_overrides
from magi_agent.customize.store import set_verification_override

_GIT = "evidence:git-diff"
_TEST = "evidence:test-run"
_SEED = ("evidence:git-diff", "evidence:test-run", "evidence:doc-write")


@pytest.fixture
def cfile(monkeypatch, tmp_path) -> Path:
    path = tmp_path / "customize.json"
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(path))
    return path


def _disable(path: Path) -> None:
    set_verification_override("harness_presets", "deterministic-evidence", False, path=path)


def test_noop_when_master_flag_off(monkeypatch, cfile):
    # Profile-aware default-ON master flag; explicit "0" exercises the off path.
    monkeypatch.setenv("MAGI_CUSTOMIZE_VERIFICATION_ENABLED", "0")
    _disable(cfile)  # even a disable override is ignored while the master is off
    assert _apply_customize_evidence_overrides(list(_SEED)) == list(_SEED)


def test_noop_when_enabled_default(monkeypatch, cfile):
    # Master on, preset at its default (enabled) ⇒ remove-only ⇒ byte-identical.
    monkeypatch.setenv("MAGI_CUSTOMIZE_VERIFICATION_ENABLED", "1")
    assert _apply_customize_evidence_overrides(list(_SEED)) == list(_SEED)


def test_disable_removes_git_and_test_evidence(monkeypatch, cfile):
    monkeypatch.setenv("MAGI_CUSTOMIZE_VERIFICATION_ENABLED", "1")
    _disable(cfile)
    out = _apply_customize_evidence_overrides(list(_SEED))
    assert _GIT not in out
    assert _TEST not in out
    # Unrelated evidence refs are preserved.
    assert "evidence:doc-write" in out


def test_disable_is_inert_when_refs_absent(monkeypatch, cfile):
    # A non-coding turn has no git/test evidence required ⇒ nothing to remove.
    monkeypatch.setenv("MAGI_CUSTOMIZE_VERIFICATION_ENABLED", "1")
    _disable(cfile)
    assert _apply_customize_evidence_overrides(["evidence:doc-write"]) == ["evidence:doc-write"]
