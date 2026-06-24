"""Tests for ``tool_input_preview`` allow-listed key extraction.

Why this matters
----------------
``tool_input_preview`` is the privacy-conscious extractor used to populate the
``input_preview`` field of every ``tool_start`` public event.  Its allow-list
(``_TOOL_INPUT_PREVIEW_KEYS``) limits which argument keys are exposed to the
public stream — anything outside the list is stripped, so private prompts,
secrets, or raw model content never reach UIs.

For SpawnAgent the allow-list MUST include ``prompt`` and ``task`` so that the
local-dashboard Work pane can render a meaningful action label.  Without those
keys, ``tool_input_preview`` returns ``None`` and the UI falls through to the
generic "Assigning helper" / "도우미 배정" placeholder — which is what was
observed against magi-agent v0.1.82 (no per-subagent target showing in the
right panel).
"""
from __future__ import annotations

import json

from magi_agent.runtime.public_events import tool_input_preview


def test_returns_none_for_no_arguments() -> None:
    assert tool_input_preview(None) is None


def test_returns_none_for_args_with_no_allow_listed_keys() -> None:
    # ``content`` is intentionally excluded (private prompt body).
    assert tool_input_preview({"content": "secret"}) is None


def test_allows_query_key_for_websearch() -> None:
    out = tool_input_preview({"query": "openmagi gate5b streaming"})
    assert out is not None
    assert json.loads(out) == {"query": "openmagi gate5b streaming"}


def test_allows_prompt_key_for_spawnagent() -> None:
    """SpawnAgent passes its subtask brief via ``prompt`` — must be visible."""
    out = tool_input_preview({"prompt": "Calculate 1+1 with cross-validation"})
    assert out is not None
    assert json.loads(out) == {"prompt": "Calculate 1+1 with cross-validation"}


def test_allows_task_key_for_spawnagent() -> None:
    """SpawnAgent also accepts ``task`` as an alias for ``prompt``."""
    out = tool_input_preview({"task": "Summarize the open PRs"})
    assert out is not None
    assert json.loads(out) == {"task": "Summarize the open PRs"}


def test_spawnagent_with_prompt_and_unrelated_private_arg() -> None:
    """Privacy contract: only the allow-listed key is surfaced."""
    out = tool_input_preview({
        "prompt": "Run the panel-of-models check",
        "content": "private prompt content that must not be shown",
    })
    assert out is not None
    parsed = json.loads(out)
    assert parsed == {"prompt": "Run the panel-of-models check"}
    assert "content" not in parsed


def test_skips_blank_prompt() -> None:
    assert tool_input_preview({"prompt": "   "}) is None


def test_truncates_long_prompt_to_preview_value_limit() -> None:
    long_prompt = "x" * 500
    out = tool_input_preview({"prompt": long_prompt})
    assert out is not None
    parsed = json.loads(out)
    # 160-char value limit from ``_TOOL_INPUT_PREVIEW_VALUE_LIMIT``.
    assert len(parsed["prompt"]) <= 160
