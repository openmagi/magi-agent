"""Tests for the CC-style ``settings.json`` hooks loader (cluster doc 11 PR1).

The loader reads a Claude-Code-style ``settings.json`` with a top-level
``hooks`` block keyed by CC event names (``PreToolUse``, ``PostToolUse``,
``Stop``, ``UserPromptSubmit`` …) and returns a list of ``RegisteredHook``
instances, reusing the ``external_config`` normalisation logic (env-var
substitution, SSRF protection, manifest validation).

This PR is a *pure loader* — no execution wiring. Nobody calls it from
production yet (that is doc 11 PR2/PR3).
"""
from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Iterator

import pytest

from magi_agent.hooks.external_config import ExternalHookConfig
from magi_agent.hooks.manifest import HookPoint
from magi_agent.hooks.settings_loader import load_settings_hooks


@pytest.fixture()
def tmp_settings(tmp_path) -> Iterator[str]:
    """Return a path inside a tmp dir; tests write JSON to it."""
    yield str(tmp_path / "settings.json")


def _write(path: str, payload: dict) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh)


# ---------------------------------------------------------------------------
# 1. Missing file → []
# ---------------------------------------------------------------------------
def test_missing_path_returns_empty(tmp_path) -> None:
    missing = str(tmp_path / "does-not-exist.json")
    assert load_settings_hooks(missing, None) == []


# ---------------------------------------------------------------------------
# 2. No hooks key / empty dict → []
# ---------------------------------------------------------------------------
def test_no_hooks_key_returns_empty(tmp_settings) -> None:
    _write(tmp_settings, {"theme": "dark"})
    assert load_settings_hooks(tmp_settings, None) == []


def test_empty_hooks_block_returns_empty(tmp_settings) -> None:
    _write(tmp_settings, {"hooks": {}})
    assert load_settings_hooks(tmp_settings, None) == []


# ---------------------------------------------------------------------------
# 3. PreToolUse command hook → BEFORE_TOOL_USE, execution_type command
# ---------------------------------------------------------------------------
def test_pretooluse_command_hook(tmp_settings) -> None:
    _write(
        tmp_settings,
        {
            "hooks": {
                "PreToolUse": [
                    {"command": "/bin/echo hi", "matcher": "Edit|Write"}
                ]
            }
        },
    )
    hooks = load_settings_hooks(tmp_settings, None)
    assert len(hooks) == 1
    manifest = hooks[0].manifest
    assert manifest.point == HookPoint.BEFORE_TOOL_USE
    assert manifest.execution_type == "command"
    assert manifest.command == "/bin/echo hi"
    # matcher preserved as description for future scope feature
    assert "Edit|Write" in manifest.description


def test_cc_canonical_nested_shape(tmp_settings) -> None:
    """CC canonical form: matcher-group with a nested ``hooks`` command list."""
    _write(
        tmp_settings,
        {
            "hooks": {
                "PostToolUse": [
                    {
                        "matcher": "Edit",
                        "hooks": [
                            {"type": "command", "command": "/bin/lint"}
                        ],
                    }
                ]
            }
        },
    )
    hooks = load_settings_hooks(tmp_settings, None)
    assert len(hooks) == 1
    manifest = hooks[0].manifest
    assert manifest.point == HookPoint.AFTER_TOOL_USE
    assert manifest.execution_type == "command"
    assert manifest.command == "/bin/lint"
    assert "Edit" in manifest.description


def test_event_name_mapping(tmp_settings) -> None:
    _write(
        tmp_settings,
        {
            "hooks": {
                "Stop": [{"command": "/bin/notify"}],
                "UserPromptSubmit": [{"command": "/bin/prep"}],
            }
        },
    )
    hooks = load_settings_hooks(tmp_settings, None)
    points = {h.manifest.point for h in hooks}
    assert HookPoint.AFTER_TURN_END in points
    assert HookPoint.BEFORE_SYSTEM_PROMPT in points


# ---------------------------------------------------------------------------
# 4. Unsupported event key → skip + warn, others register
# ---------------------------------------------------------------------------
def test_unsupported_event_skipped(tmp_settings, caplog) -> None:
    _write(
        tmp_settings,
        {
            "hooks": {
                "NotARealEvent": [{"command": "/bin/x"}],
                "PreToolUse": [{"command": "/bin/echo ok"}],
            }
        },
    )
    with caplog.at_level("WARNING"):
        hooks = load_settings_hooks(tmp_settings, None)
    assert len(hooks) == 1
    assert hooks[0].manifest.point == HookPoint.BEFORE_TOOL_USE
    assert any("NotARealEvent" in rec.message for rec in caplog.records)


# ---------------------------------------------------------------------------
# 5. MAGI_HOOK_* env substitution; non-MAGI_HOOK_ tokens NOT substituted
# ---------------------------------------------------------------------------
def test_magi_hook_env_substitution(tmp_settings, monkeypatch) -> None:
    monkeypatch.setenv("MAGI_HOOK_DIR", "/safe/bin")
    _write(
        tmp_settings,
        {"hooks": {"PreToolUse": [{"command": "${MAGI_HOOK_DIR}/run.sh"}]}},
    )
    hooks = load_settings_hooks(tmp_settings, None)
    assert hooks[0].manifest.command == "/safe/bin/run.sh"


def test_non_magi_hook_env_not_substituted(tmp_settings, monkeypatch) -> None:
    monkeypatch.setenv("SECRET_KEY", "leaked")
    _write(
        tmp_settings,
        {"hooks": {"PreToolUse": [{"command": "echo ${SECRET_KEY}"}]}},
    )
    hooks = load_settings_hooks(tmp_settings, None)
    # token left unchanged — secret not leaked into the command
    assert "leaked" not in hooks[0].manifest.command
    assert "${SECRET_KEY}" in hooks[0].manifest.command


# ---------------------------------------------------------------------------
# 6. SSRF: internal http hook → ValueError (external_config reuse)
# ---------------------------------------------------------------------------
def test_ssrf_internal_http_hook_skipped(tmp_settings, caplog) -> None:
    _write(
        tmp_settings,
        {
            "hooks": {
                "PreToolUse": [
                    {
                        "type": "http",
                        "url": "http://169.254.169.254/latest/meta-data",
                    }
                ]
            }
        },
    )
    with caplog.at_level("WARNING"):
        hooks = load_settings_hooks(tmp_settings, None)
    # internal-URL entry rejected by external_config SSRF guard → skipped
    assert hooks == []


# ---------------------------------------------------------------------------
# 7. config.llm_hooks_enabled=False → llm hook filtered
# ---------------------------------------------------------------------------
def test_llm_hooks_filtered_when_disabled(tmp_settings) -> None:
    _write(
        tmp_settings,
        {
            "hooks": {
                "PreToolUse": [
                    {
                        "type": "llm",
                        "prompt_template": "is this safe? {tool_input}",
                    },
                    {"type": "command", "command": "/bin/ok"},
                ]
            }
        },
    )
    cfg = ExternalHookConfig(enabled=True, llm_hooks_enabled=False)
    hooks = load_settings_hooks(tmp_settings, cfg)
    assert len(hooks) == 1
    assert hooks[0].manifest.execution_type == "command"


def test_llm_hooks_kept_when_enabled(tmp_settings) -> None:
    _write(
        tmp_settings,
        {
            "hooks": {
                "PreToolUse": [
                    {
                        "type": "llm",
                        "prompt_template": "is this safe? {tool_input}",
                    }
                ]
            }
        },
    )
    cfg = ExternalHookConfig(enabled=True, llm_hooks_enabled=True)
    hooks = load_settings_hooks(tmp_settings, cfg)
    assert len(hooks) == 1
    assert hooks[0].manifest.execution_type == "llm"


# ---------------------------------------------------------------------------
# Robustness: malformed JSON → [] (no raise)
# ---------------------------------------------------------------------------
def test_malformed_json_returns_empty(tmp_settings) -> None:
    with open(tmp_settings, "w", encoding="utf-8") as fh:
        fh.write("{ not valid json ")
    assert load_settings_hooks(tmp_settings, None) == []


def test_hooks_not_a_dict_returns_empty(tmp_settings) -> None:
    _write(tmp_settings, {"hooks": [{"command": "/bin/x"}]})
    assert load_settings_hooks(tmp_settings, None) == []
