from __future__ import annotations

from magi_agent.customize.runtime_gate import preset_enabled
from magi_agent.customize.store import set_verification_override


def test_preset_enabled_false_when_flag_off(monkeypatch, tmp_path):
    # Profile-aware default-ON, so OFF is explicit "0".
    monkeypatch.setenv("MAGI_CUSTOMIZE_VERIFICATION_ENABLED", "0")
    cfile = tmp_path / "customize.json"
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(cfile))
    set_verification_override("harness_presets", "fact-grounding", True, path=cfile)
    assert preset_enabled("fact-grounding", default=False) is False


def test_preset_enabled_true_when_flag_on_and_enabled(monkeypatch, tmp_path):
    monkeypatch.setenv("MAGI_CUSTOMIZE_VERIFICATION_ENABLED", "1")
    cfile = tmp_path / "customize.json"
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(cfile))
    set_verification_override("harness_presets", "fact-grounding", True, path=cfile)
    assert preset_enabled("fact-grounding", default=False) is True


def test_preset_enabled_unset_uses_default(monkeypatch, tmp_path):
    monkeypatch.setenv("MAGI_CUSTOMIZE_VERIFICATION_ENABLED", "1")
    cfile = tmp_path / "customize.json"
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(cfile))
    # never set → falls back to the supplied runtime default
    assert preset_enabled("fact-grounding", default=False) is False
    assert preset_enabled("coding-verification", default=True) is True


def test_preset_enabled_explicit_disable(monkeypatch, tmp_path):
    monkeypatch.setenv("MAGI_CUSTOMIZE_VERIFICATION_ENABLED", "1")
    cfile = tmp_path / "customize.json"
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(cfile))
    set_verification_override("harness_presets", "coding-verification", False, path=cfile)
    assert preset_enabled("coding-verification", default=True) is False
