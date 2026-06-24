"""user_rules (Customize Guidance tab) injected into the system prompt.

PR-F1 retired the prior '## User Rules ... Follow them' markdown framing in
favor of an honest '<user_advisory_rules>' envelope that names the rules as
advisory (not enforced by a hard gate). Same flag (MAGI_CUSTOMIZE_VERIFICATION_ENABLED)
gates the wire.
"""
from __future__ import annotations

from magi_agent.customize.store import set_user_rules
from magi_agent.runtime.message_builder import _user_rules_block


def test_block_empty_when_flag_off(monkeypatch, tmp_path):
    # Profile-aware default-ON, so OFF is explicit "0".
    monkeypatch.setenv("MAGI_CUSTOMIZE_VERIFICATION_ENABLED", "0")
    cfile = tmp_path / "customize.json"
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(cfile))
    set_user_rules("Always cite sources.", path=cfile)
    assert _user_rules_block() == ""  # byte-identical when off


def test_block_injected_when_flag_on_and_rules_set(monkeypatch, tmp_path):
    monkeypatch.setenv("MAGI_CUSTOMIZE_VERIFICATION_ENABLED", "1")
    cfile = tmp_path / "customize.json"
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(cfile))
    set_user_rules("Always cite sources.", path=cfile)
    block = _user_rules_block()
    assert block.startswith("<user_advisory_rules>")
    assert block.endswith("</user_advisory_rules>")
    assert "Operator advisory rules" in block
    assert "Always cite sources." in block


def test_block_empty_when_no_rules(monkeypatch, tmp_path):
    monkeypatch.setenv("MAGI_CUSTOMIZE_VERIFICATION_ENABLED", "1")
    cfile = tmp_path / "customize.json"
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(cfile))
    assert _user_rules_block() == ""
