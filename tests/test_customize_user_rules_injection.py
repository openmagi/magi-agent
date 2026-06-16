"""USER-RULES.md (Customize tab) injected into the system prompt, flag-gated."""
from __future__ import annotations

from magi_agent.customize.store import set_user_rules
from magi_agent.runtime.message_builder import _user_rules_block


def test_block_empty_when_flag_off(monkeypatch, tmp_path):
    monkeypatch.delenv("MAGI_CUSTOMIZE_VERIFICATION_ENABLED", raising=False)
    cfile = tmp_path / "customize.json"
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(cfile))
    set_user_rules("Always cite sources.", path=cfile)
    assert _user_rules_block() == ""  # byte-identical to main when off


def test_block_injected_when_flag_on_and_rules_set(monkeypatch, tmp_path):
    monkeypatch.setenv("MAGI_CUSTOMIZE_VERIFICATION_ENABLED", "1")
    cfile = tmp_path / "customize.json"
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(cfile))
    set_user_rules("Always cite sources.", path=cfile)
    block = _user_rules_block()
    assert "User Rules" in block
    assert "Always cite sources." in block


def test_block_empty_when_no_rules(monkeypatch, tmp_path):
    monkeypatch.setenv("MAGI_CUSTOMIZE_VERIFICATION_ENABLED", "1")
    cfile = tmp_path / "customize.json"
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(cfile))
    assert _user_rules_block() == ""
