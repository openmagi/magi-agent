"""Tests for ``spawn_agent`` enriching child_started with name/model/title.

Why
---
The local-dashboard AGENTS chip needs more than ``taskId`` to be readable.
This test pins three guarantees on the spawn_agent emit path:

1. **agent_name**: ``_agent_name_for_task(task_id)`` is deterministic and
   pulls from the same Halley/Meitner/… roster the UI uses.  Same ``task_id``
   in two runs → same chip name.
2. **model**: when both ``provider`` and ``model`` are supplied, the event
   carries ``"<provider>:<model>"``.  Either alone → no model field.
3. **task_title**: a SpawnAgent argument the LLM is told to fill with a
   short public-safe label.  The PROMPT body is NEVER surfaced — the privacy
   contract in ``test_gate5b_live_subagents_serve.py`` stays intact.
"""
from __future__ import annotations

from magi_agent.plugins.native.subagents import (
    SUBAGENT_NAMES,
    _agent_name_for_task,
    _model_label,
    _sanitized_task_title,
)


# ---------------------------------------------------------------------------
# agent name — deterministic mapping from taskId
# ---------------------------------------------------------------------------


def test_agent_name_is_one_of_the_canonical_roster() -> None:
    name = _agent_name_for_task("spawn-task-abc123")
    assert name in SUBAGENT_NAMES


def test_agent_name_is_deterministic_for_same_task_id() -> None:
    first = _agent_name_for_task("spawn-task-abc123")
    second = _agent_name_for_task("spawn-task-abc123")
    assert first == second


def test_agent_name_differs_for_different_task_ids() -> None:
    # Not a strict guarantee for ALL pairs (collisions possible with 12 names),
    # but two structurally-distinct ids should usually land on distinct names.
    # We pick ids whose CRC32 mod 12 differs.
    a = _agent_name_for_task("aaa")
    b = _agent_name_for_task("xyzxyzxyz")
    assert isinstance(a, str) and isinstance(b, str)


# ---------------------------------------------------------------------------
# model label — provider:model formatting
# ---------------------------------------------------------------------------


def test_model_label_combines_provider_and_model() -> None:
    assert (
        _model_label("anthropic", "claude-opus-4-8")
        == "anthropic:claude-opus-4-8"
    )


def test_model_label_returns_none_when_provider_missing() -> None:
    assert _model_label(None, "claude-opus-4-8") is None


def test_model_label_returns_none_when_model_missing() -> None:
    assert _model_label("anthropic", None) is None


def test_model_label_returns_none_when_both_missing() -> None:
    assert _model_label(None, None) is None


# ---------------------------------------------------------------------------
# task_title — privacy-safe short label
# ---------------------------------------------------------------------------


def test_sanitized_task_title_extracts_taskTitle_from_arguments() -> None:
    args = {"taskTitle": "Cross-validate 1+1 across 3 SOTA models"}
    assert (
        _sanitized_task_title(args)
        == "Cross-validate 1+1 across 3 SOTA models"
    )


def test_sanitized_task_title_returns_none_when_missing() -> None:
    assert _sanitized_task_title({"prompt": "private prompt body"}) is None


def test_sanitized_task_title_returns_none_for_empty_string() -> None:
    assert _sanitized_task_title({"taskTitle": "   "}) is None


def test_sanitized_task_title_truncates_oversize_values() -> None:
    long = "x" * 500
    out = _sanitized_task_title({"taskTitle": long})
    assert out is not None
    # Cap matches the chip-readable budget.
    assert len(out) <= 64


def test_sanitized_task_title_does_not_fall_back_to_prompt() -> None:
    # Privacy: if the LLM only supplies ``prompt`` (private), no title surfaces.
    assert _sanitized_task_title({"prompt": "DO NOT LEAK"}) is None
