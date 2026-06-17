from __future__ import annotations

import pytest

from magi_agent.customize.store import set_custom_rule
from magi_agent.tools.context import ToolContext
from magi_agent.tools.manifest import ToolManifest, ToolSource
from magi_agent.tools.permission import ToolPermissionPolicy


def _manifest(name: str = "Fetcher") -> ToolManifest:
    return ToolManifest(
        name=name,
        description=f"{name} test tool",
        kind="core",
        source=ToolSource(kind="builtin", package="tests.tools"),
        permission="read",  # safety-allowed → custom rule is the deciding layer
        input_schema={"type": "object", "additionalProperties": True},
        timeout_ms=120_000,
        available_in_modes=("act",),
        dangerous=False,
        mutates_workspace=False,
    )


def _ctx() -> ToolContext:
    return ToolContext(
        bot_id="bot-1",
        turn_id="turn-1",
        workspace_root="/tmp/ws",
        permission_scope=None,
    )


def _decide(arguments: dict, name: str = "Fetcher") -> str:
    return ToolPermissionPolicy().decide(
        _manifest(name), arguments, _ctx(), mode="act"
    ).action


def _tool_rule(match: dict, decision: str = "deny", rid: str = "cr_t"):
    return {
        "id": rid,
        "scope": "always",
        "enabled": True,
        "what": {"kind": "tool_perm", "payload": {"match": match, "decision": decision}},
        "firesAt": "before_tool_use",
        "action": "block" if decision == "deny" else "ask_approval",
    }


@pytest.fixture
def cfg(monkeypatch, tmp_path):
    monkeypatch.setenv("MAGI_CUSTOMIZE_VERIFICATION_ENABLED", "1")
    monkeypatch.setenv("MAGI_CUSTOMIZE_CUSTOM_RULES_ENABLED", "1")
    cfile = tmp_path / "customize.json"
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(cfile))
    return cfile


def test_baseline_allow_without_rule(cfg):
    assert _decide({}) == "allow"


def test_custom_tool_deny(cfg):
    set_custom_rule(_tool_rule({"tool": "Fetcher"}, "deny"), path=cfg)
    assert _decide({}) == "deny"


def test_custom_tool_ask(cfg):
    set_custom_rule(_tool_rule({"tool": "Fetcher"}, "ask"), path=cfg)
    assert _decide({}) == "ask"


def test_inert_when_flags_off(monkeypatch, tmp_path):
    # rule on disk but custom-rules flag OFF → byte-identical (allow)
    monkeypatch.setenv("MAGI_CUSTOMIZE_VERIFICATION_ENABLED", "1")
    monkeypatch.setenv("MAGI_CUSTOMIZE_CUSTOM_RULES_ENABLED", "0")
    cfile = tmp_path / "customize.json"
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(cfile))
    set_custom_rule(_tool_rule({"tool": "Fetcher"}, "deny"), path=cfile)
    assert _decide({}) == "allow"


def test_domain_allowlist_denies_non_listed(cfg):
    set_custom_rule(_tool_rule({"domainAllowlist": ["sec.gov"]}, "deny"), path=cfg)
    assert _decide({"url": "https://nasdaq.com/q"}) == "deny"
    assert _decide({"url": "https://www.sec.gov/cgi"}) == "allow"
