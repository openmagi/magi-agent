"""Integration tests for the fuzzy-edit cascade wired into gate5b FileEdit.

Four cases (per spec):
  (a) old_text with wrong indentation succeeds when flag is ON.
  (b) Genuinely absent old_text → old_text_not_found.
  (c) Ambiguous duplicate region → old_text_not_unique.
  (d) Flag OFF preserves exact-only behaviour (indentation mismatch fails).
"""
from __future__ import annotations

import os
import importlib
import pytest

from magi_agent.gates.gate5b_full_toolhost import (
    GATE5B_FULL_TOOLHOST_TOOL_NAMES,
    Gate5BFullToolHostConfig,
    build_gate5b_full_toolhost_bundle,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sha256(value: str) -> str:
    import hashlib
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _ready_bundle(tmp_path, *, extra_config: dict | None = None):
    """Build a ready gate5b bundle targeting tmp_path."""
    config_data = {
        "enabled": True,
        "killSwitchEnabled": False,
        "routeAttachmentEnabled": True,
        "selectedBotDigest": _sha256("bot-fuzzy-test"),
        "selectedOwnerDigest": _sha256("user-fuzzy-test"),
        "environment": "production",
        "environmentAllowlist": ("production",),
        "allowedToolNames": GATE5B_FULL_TOOLHOST_TOOL_NAMES,
        "maxToolCallsPerTurn": 16,
    }
    if extra_config:
        config_data.update(extra_config)
    config = Gate5BFullToolHostConfig.model_validate(config_data)
    scope = {
        "selectedBotDigest": _sha256("bot-fuzzy-test"),
        "selectedOwnerDigest": _sha256("user-fuzzy-test"),
        "environment": "production",
    }
    return build_gate5b_full_toolhost_bundle(
        config=config,
        scope=scope,
        workspace_root=tmp_path,
    )


def _write_file(tmp_path, name: str, content: str) -> None:
    target = tmp_path / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


# ---------------------------------------------------------------------------
# (a) Flag ON — indentation mismatch is absorbed by fuzzy match
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fuzzy_edit_flag_on_indentation_mismatch_succeeds(tmp_path, monkeypatch):
    """FileEdit with wrong indentation in old_text succeeds when flag is ON."""
    monkeypatch.setenv("MAGI_EDIT_FUZZY_MATCH_ENABLED", "1")

    # We must re-read the module-level flag after monkeypatching the env.
    # gate5b reads _EDIT_FUZZY_MATCH_ENABLED at import time, so we patch it.
    import magi_agent.gates.gate5b_full_toolhost as mod
    monkeypatch.setattr(mod, "_EDIT_FUZZY_MATCH_ENABLED", True)

    bundle = _ready_bundle(tmp_path)
    assert bundle.status == "ready"

    content = (
        "def greet(name):\n"
        "    message = 'Hello, ' + name\n"
        "    return message\n"
    )
    _write_file(tmp_path, "greet.py", content)

    # old_text uses 2-space indentation instead of 4-space (intentional mismatch)
    old_text_wrong_indent = (
        "def greet(name):\n"
        "  message = 'Hello, ' + name\n"
        "  return message\n"
    )
    new_text = (
        "def greet(name):\n"
        "    message = 'Hi, ' + name\n"
        "    return message\n"
    )

    outcome = await bundle.host.dispatch(
        "FileEdit",
        {"path": "greet.py", "oldText": old_text_wrong_indent, "newText": new_text},
        request_digest=_sha256("req-a-1"),
        tool_call_id="call-a-1",
    )

    assert outcome.status == "ok", f"Expected ok, got {outcome.status}: {outcome.reason}"
    result = (tmp_path / "greet.py").read_text(encoding="utf-8")
    assert "Hi, " in result, "Replacement should have been applied"
    assert "Hello, " not in result, "Original should have been replaced"


# ---------------------------------------------------------------------------
# (b) Flag ON — genuinely absent old_text → old_text_not_found
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fuzzy_edit_flag_on_absent_old_text_returns_not_found(tmp_path, monkeypatch):
    """FileEdit with genuinely absent old_text → error status (old_text_not_found)."""
    import magi_agent.gates.gate5b_full_toolhost as mod
    monkeypatch.setattr(mod, "_EDIT_FUZZY_MATCH_ENABLED", True)

    bundle = _ready_bundle(tmp_path)
    _write_file(tmp_path, "data.py", "x = 1\ny = 2\n")

    outcome = await bundle.host.dispatch(
        "FileEdit",
        {
            "path": "data.py",
            "oldText": "this_text_does_not_exist_anywhere_in_the_file\n",
            "newText": "replaced\n",
        },
        request_digest=_sha256("req-b-1"),
        tool_call_id="call-b-1",
    )

    # gate5b maps ValueError to status="error"
    assert outcome.status == "error", f"Expected error, got {outcome.status}"
    # File must be unchanged — confirms the error was a match failure, not a
    # partial write followed by an error (which would be a worse outcome).
    assert (tmp_path / "data.py").read_text(encoding="utf-8") == "x = 1\ny = 2\n"


def test_fuzzy_edit_handle_absent_old_text_raises_old_text_not_found(tmp_path, monkeypatch):
    """Direct unit test: _handle raises ValueError('old_text_not_found') on NoMatchError.

    This supplements the integration test above by asserting the *specific*
    error code rather than just a generic status=="error", which any ValueError
    would produce.  If the wiring were broken (e.g. NoMatchError swallowed
    silently, or mapped to a different code), this test fails.
    """
    import magi_agent.gates.gate5b_full_toolhost as mod
    monkeypatch.setattr(mod, "_EDIT_FUZZY_MATCH_ENABLED", True)

    bundle = _ready_bundle(tmp_path)
    _write_file(tmp_path, "data2.py", "x = 1\ny = 2\n")

    with pytest.raises(ValueError, match="old_text_not_found"):
        bundle.host._handle(
            "FileEdit",
            {
                "path": "data2.py",
                "oldText": "this_text_does_not_exist_anywhere_in_the_file\n",
                "newText": "replaced\n",
            },
        )


def test_fuzzy_edit_handle_ambiguous_raises_old_text_not_unique(tmp_path, monkeypatch):
    """Direct unit test: _handle raises ValueError('old_text_not_unique') on MultipleMatchesError.

    Complements test (c) — asserts the specific error code, which would fail if
    MultipleMatchesError were mapped to 'old_text_not_found' or silently ignored.
    """
    import magi_agent.gates.gate5b_full_toolhost as mod
    monkeypatch.setattr(mod, "_EDIT_FUZZY_MATCH_ENABLED", True)

    bundle = _ready_bundle(tmp_path)

    repeated_block = "    def process(self):\n        pass\n"
    content = repeated_block + "\n" + repeated_block
    _write_file(tmp_path, "service2.py", content)

    with pytest.raises(ValueError, match="old_text_not_unique"):
        bundle.host._handle(
            "FileEdit",
            {
                "path": "service2.py",
                "oldText": "def process(self):\n    pass\n",
                "newText": "def process(self):\n    return True\n",
            },
        )


# ---------------------------------------------------------------------------
# (c) Flag ON — ambiguous duplicate region → old_text_not_unique
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fuzzy_edit_flag_on_ambiguous_duplicate_returns_not_unique(tmp_path, monkeypatch):
    """FileEdit with ambiguous duplicate → error status (old_text_not_unique)."""
    import magi_agent.gates.gate5b_full_toolhost as mod
    monkeypatch.setattr(mod, "_EDIT_FUZZY_MATCH_ENABLED", True)

    bundle = _ready_bundle(tmp_path)

    # File has two identical blocks
    repeated_block = "    def process(self):\n        pass\n"
    content = repeated_block + "\n" + repeated_block
    _write_file(tmp_path, "service.py", content)

    # old_text with stripped indentation — will fuzzy-match both occurrences
    old_text_stripped = "def process(self):\n    pass\n"

    outcome = await bundle.host.dispatch(
        "FileEdit",
        {
            "path": "service.py",
            "oldText": old_text_stripped,
            "newText": "def process(self):\n    return True\n",
        },
        request_digest=_sha256("req-c-1"),
        tool_call_id="call-c-1",
    )

    assert outcome.status == "error", f"Expected error, got {outcome.status}"
    # File unchanged
    assert (tmp_path / "service.py").read_text(encoding="utf-8") == content


# ---------------------------------------------------------------------------
# (d) Flag OFF — exact-only behaviour, indentation mismatch fails
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fuzzy_edit_flag_off_preserves_exact_only_behavior(tmp_path, monkeypatch):
    """Flag OFF: indentation-mismatched old_text fails as before."""
    import magi_agent.gates.gate5b_full_toolhost as mod
    monkeypatch.setattr(mod, "_EDIT_FUZZY_MATCH_ENABLED", False)

    bundle = _ready_bundle(tmp_path)
    content = "def hello():\n    return 'world'\n"
    _write_file(tmp_path, "hello.py", content)

    # old_text has wrong indentation (2-space vs 4-space)
    wrong_indent = "def hello():\n  return 'world'\n"

    outcome = await bundle.host.dispatch(
        "FileEdit",
        {
            "path": "hello.py",
            "oldText": wrong_indent,
            "newText": "def hello():\n    return 'earth'\n",
        },
        request_digest=_sha256("req-d-1"),
        tool_call_id="call-d-1",
    )

    assert outcome.status == "error", (
        f"Flag OFF should fail on indentation mismatch, got {outcome.status}"
    )
    # File unchanged
    assert (tmp_path / "hello.py").read_text(encoding="utf-8") == content


# ---------------------------------------------------------------------------
# (d-extra) Flag OFF — exact match still works
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fuzzy_edit_flag_off_exact_match_still_works(tmp_path, monkeypatch):
    """Flag OFF: exact old_text succeeds (no regression on happy path)."""
    import magi_agent.gates.gate5b_full_toolhost as mod
    monkeypatch.setattr(mod, "_EDIT_FUZZY_MATCH_ENABLED", False)

    bundle = _ready_bundle(tmp_path)
    content = "def hello():\n    return 'world'\n"
    _write_file(tmp_path, "hello2.py", content)

    outcome = await bundle.host.dispatch(
        "FileEdit",
        {
            "path": "hello2.py",
            "oldText": "return 'world'",
            "newText": "return 'earth'",
        },
        request_digest=_sha256("req-d-2"),
        tool_call_id="call-d-2",
    )

    assert outcome.status == "ok", f"Exact match should succeed, got {outcome.status}"
    assert "earth" in (tmp_path / "hello2.py").read_text(encoding="utf-8")
