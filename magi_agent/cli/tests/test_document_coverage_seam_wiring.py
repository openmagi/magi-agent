"""Customize opt-in seam for the document-authoring-coverage gate.

The 3-mode coverage gate (off|advisory|block) was env-only
(``MAGI_DOCUMENT_AUTHORING_COVERAGE``). The Customize ``document-authoring-coverage``
preset now promotes an otherwise-off gate to ``block`` for the runtime, via
``_resolve_document_coverage_mode_with_preset`` — the same opt-in pattern (env OR
preset) as the other satisfier seams. Byte-identical when the preset (and env
flag) are off.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from magi_agent.cli.engine import _resolve_document_coverage_mode_with_preset
from magi_agent.customize.store import set_verification_override

_PRESET = "document-authoring-coverage"


@pytest.fixture
def cfile(monkeypatch, tmp_path) -> Path:
    path = tmp_path / "customize.json"
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(path))
    return path


def _enable(path: Path) -> None:
    set_verification_override("harness_presets", _PRESET, True, path=path)


def test_off_when_preset_and_env_off(monkeypatch, cfile):
    # Byte-identical to main: env mode off + no customize ⇒ gate stays off.
    monkeypatch.delenv("MAGI_DOCUMENT_AUTHORING_COVERAGE", raising=False)
    monkeypatch.delenv("MAGI_CUSTOMIZE_VERIFICATION_ENABLED", raising=False)
    assert _resolve_document_coverage_mode_with_preset() == "off"


def test_preset_toggle_promotes_off_to_block(monkeypatch, cfile):
    # env mode off, but the preset is enabled (+ master flag) ⇒ block.
    monkeypatch.delenv("MAGI_DOCUMENT_AUTHORING_COVERAGE", raising=False)
    monkeypatch.setenv("MAGI_CUSTOMIZE_VERIFICATION_ENABLED", "1")
    _enable(cfile)
    assert _resolve_document_coverage_mode_with_preset() == "block"


def test_not_activated_when_master_flag_off(monkeypatch, cfile):
    # Preset enabled in the file but the customize MASTER flag is off ⇒ inert.
    # The master flag is profile-aware default-ON (#664), so OFF must be explicit.
    monkeypatch.delenv("MAGI_DOCUMENT_AUTHORING_COVERAGE", raising=False)
    monkeypatch.setenv("MAGI_CUSTOMIZE_VERIFICATION_ENABLED", "0")
    _enable(cfile)
    assert _resolve_document_coverage_mode_with_preset() == "off"


def test_env_value_unchanged_when_set(monkeypatch, cfile):
    # The preset only upgrades an OFF gate; an explicit env mode is never
    # downgraded or overridden.
    monkeypatch.setenv("MAGI_CUSTOMIZE_VERIFICATION_ENABLED", "1")
    _enable(cfile)
    monkeypatch.setenv("MAGI_DOCUMENT_AUTHORING_COVERAGE", "advisory")
    assert _resolve_document_coverage_mode_with_preset() == "advisory"
    monkeypatch.setenv("MAGI_DOCUMENT_AUTHORING_COVERAGE", "block")
    assert _resolve_document_coverage_mode_with_preset() == "block"


def test_env_block_without_preset(monkeypatch, cfile):
    # Regression: the existing env path is unchanged (no preset needed).
    monkeypatch.setenv("MAGI_DOCUMENT_AUTHORING_COVERAGE", "block")
    monkeypatch.delenv("MAGI_CUSTOMIZE_VERIFICATION_ENABLED", raising=False)
    assert _resolve_document_coverage_mode_with_preset() == "block"
