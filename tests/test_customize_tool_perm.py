from __future__ import annotations

from pathlib import Path

import pytest

from magi_agent.customize.store import set_custom_rule
from magi_agent.customize.tool_perm import matched_decision


@pytest.fixture
def cfg(monkeypatch, tmp_path):
    monkeypatch.setenv("MAGI_CUSTOMIZE_VERIFICATION_ENABLED", "1")
    monkeypatch.setenv("MAGI_CUSTOMIZE_CUSTOM_RULES_ENABLED", "1")
    cfile = tmp_path / "customize.json"
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(cfile))
    return cfile


def _tool_rule(match: dict, decision: str = "deny", rid: str = "cr_t", enabled: bool = True):
    return {
        "id": rid,
        "scope": "always",
        "enabled": enabled,
        "what": {"kind": "tool_perm", "payload": {"match": match, "decision": decision}},
        "firesAt": "before_tool_use",
        "action": "block" if decision == "deny" else "ask_approval",
    }


def test_inert_when_flags_off(monkeypatch, tmp_path):
    # Profile-aware default-ON, so OFF is explicit "0".
    monkeypatch.setenv("MAGI_CUSTOMIZE_CUSTOM_RULES_ENABLED", "0")
    cfile = tmp_path / "customize.json"
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(cfile))
    set_custom_rule(_tool_rule({"tool": "web_fetch"}), path=cfile)
    assert matched_decision(tool_name="web_fetch", arguments={}) is None


def test_tool_match_deny(cfg: Path):
    set_custom_rule(_tool_rule({"tool": "web_fetch"}, "deny"), path=cfg)
    assert matched_decision(tool_name="web_fetch", arguments={}) == ("deny", "cr_t")
    assert matched_decision(tool_name="other_tool", arguments={}) is None


def test_tool_match_ask(cfg: Path):
    set_custom_rule(_tool_rule({"tool": "Bash"}, "ask"), path=cfg)
    out = matched_decision(tool_name="Bash", arguments={})
    assert out is not None and out[0] == "ask"


def test_domain_denylist_match(cfg: Path):
    set_custom_rule(_tool_rule({"domain": "evil.com"}, "deny"), path=cfg)
    assert matched_decision(tool_name="web_fetch", arguments={"url": "https://evil.com/x"})[0] == "deny"
    assert matched_decision(tool_name="web_fetch", arguments={"url": "https://sub.evil.com/x"})[0] == "deny"
    assert matched_decision(tool_name="web_fetch", arguments={"url": "https://ok.com/x"}) is None


def test_domain_allowlist_blocks_non_listed(cfg: Path):
    set_custom_rule(_tool_rule({"domainAllowlist": ["sec.gov"]}, "deny"), path=cfg)
    # non-listed domain → violates allowlist → deny
    assert matched_decision(tool_name="web_fetch", arguments={"url": "https://nasdaq.com/q"})[0] == "deny"
    # listed domain → allowed (no match)
    assert matched_decision(tool_name="web_fetch", arguments={"url": "https://www.sec.gov/cgi"}) is None
    # no URL at all → allowlist not triggered
    assert matched_decision(tool_name="some_tool", arguments={}) is None


def test_disabled_rule_inert(cfg: Path):
    set_custom_rule(_tool_rule({"tool": "web_fetch"}, "deny", enabled=False), path=cfg)
    assert matched_decision(tool_name="web_fetch", arguments={}) is None


# ---- H4-C11: path / pathAllowlist (workspace-lock) -----------------------


def test_path_denylist_matches_subpath(cfg: Path):
    set_custom_rule(_tool_rule({"path": "/Users/me/secret"}, "deny"), path=cfg)
    # the path itself, and any subpath, denied
    assert matched_decision(
        tool_name="FileRead", arguments={"path": "/Users/me/secret"}
    ) == ("deny", "cr_t")
    assert matched_decision(
        tool_name="FileRead", arguments={"path": "/Users/me/secret/key.pem"}
    ) == ("deny", "cr_t")
    assert matched_decision(
        tool_name="FileEdit", arguments={"path": "/Users/me/secret/sub/dir/x.py"}
    ) == ("deny", "cr_t")


def test_path_denylist_respects_segment_boundary(cfg: Path):
    # ``/Users/me/sec`` must NOT match ``/Users/me/secret`` — boundary required.
    set_custom_rule(_tool_rule({"path": "/Users/me/sec"}, "deny"), path=cfg)
    assert matched_decision(
        tool_name="FileRead", arguments={"path": "/Users/me/secret"}
    ) is None


def test_path_denylist_does_not_match_other_paths(cfg: Path):
    set_custom_rule(_tool_rule({"path": "/Users/me/secret"}, "deny"), path=cfg)
    assert matched_decision(
        tool_name="FileRead", arguments={"path": "/Users/me/other/x.py"}
    ) is None


def test_path_denylist_inert_when_no_path_argument(cfg: Path):
    # A URL is not a path; ``Bash`` calls typically have no path arg.
    set_custom_rule(_tool_rule({"path": "/Users/me/secret"}, "deny"), path=cfg)
    assert matched_decision(tool_name="Bash", arguments={"cmd": "ls"}) is None
    assert matched_decision(
        tool_name="WebFetch", arguments={"url": "https://example.com/path/x"}
    ) is None


def test_path_allowlist_denies_outside_paths(cfg: Path):
    set_custom_rule(
        _tool_rule({"pathAllowlist": ["/Users/me/proj"]}, "deny"), path=cfg
    )
    # inside the allowlist → no decision (allowed by absence of match)
    assert matched_decision(
        tool_name="FileEdit", arguments={"path": "/Users/me/proj/main.py"}
    ) is None
    # OUTSIDE the allowlist → denied (allowlist match fires)
    out = matched_decision(
        tool_name="FileEdit", arguments={"path": "/Users/me/elsewhere/x.py"}
    )
    assert out == ("deny", "cr_t")


def test_path_allowlist_inert_when_no_path_argument(cfg: Path):
    set_custom_rule(
        _tool_rule({"pathAllowlist": ["/Users/me/proj"]}, "deny"), path=cfg
    )
    # No path argument at all ⇒ allowlist cannot judge ⇒ no decision.
    assert matched_decision(tool_name="Bash", arguments={"cmd": "ls"}) is None


def test_path_normalises_dot_segments(cfg: Path):
    # ``..`` is resolved before prefix-checking so a denylist can't be bypassed.
    set_custom_rule(_tool_rule({"path": "/Users/me/secret"}, "deny"), path=cfg)
    assert matched_decision(
        tool_name="FileRead",
        arguments={"path": "/Users/me/other/../secret/key.pem"},
    ) == ("deny", "cr_t")


def test_path_aliases_recognised(cfg: Path):
    # Tools using ``file`` instead of ``path`` are also matched.
    set_custom_rule(_tool_rule({"path": "/Users/me/secret"}, "deny"), path=cfg)
    assert matched_decision(
        tool_name="ImageUnderstand",
        arguments={"file": "/Users/me/secret/img.png"},
    ) == ("deny", "cr_t")


def test_path_match_ignores_url_in_path_field(cfg: Path):
    # If a tool surfaces a URL via ``path``, _path_from_arguments must NOT
    # treat it as a filesystem path (so a path-rule can't be tricked).
    set_custom_rule(_tool_rule({"path": "/Users/me/secret"}, "deny"), path=cfg)
    assert matched_decision(
        tool_name="Browse",
        arguments={"path": "https://example.com/Users/me/secret/x"},
    ) is None
