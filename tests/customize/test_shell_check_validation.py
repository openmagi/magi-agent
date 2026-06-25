"""F-EXEC2 — ``shell_check`` custom_rule validator tests.

Asserts the backend ``_LEGAL`` matrix + payload validator accepts the
operator-authored ``shell_check`` shape at every v1 slot that mirrors the
``llm_criterion`` matrix, rejects at slots without a runtime fan-out
(``spawn`` — capability_scope owns it; ``before_llm_call`` /
``after_llm_call`` — per-call hot path excluded in v1; ``on_task_complete``
/ ``on_session_start`` / ``on_session_end`` — session/task boundary slots
left to llm_criterion-only in v1), and surfaces meaningful errors for
malformed payloads.

Pure unit tests — no subprocess spawn (the runner is exercised by the
firing tests in ``tests/customize_firing/test_shell_check_firing.py``).
"""

from __future__ import annotations

import pytest

from magi_agent.customize.custom_rules import KINDS, validate_custom_rule


# v1 _LEGAL matrix slot allow-list (matches custom_rules.py for shell_check).
ALLOWED_SLOTS_BLOCK = ["pre_final", "before_tool_use"]
ALLOWED_SLOTS_AUDIT_ALL = [
    "pre_final",
    "before_tool_use",
    "after_tool_use",
    "on_user_prompt_submit",
    "on_subagent_stop",
    "before_turn_start",
    "after_turn_end",
    "before_compaction",
    "after_compaction",
    "on_task_checkpoint",
    "on_artifact_created",
]

# Slots that v1 _LEGAL keeps off the shell_check map. ``before_llm_call`` /
# ``after_llm_call`` are excluded for the same per-call-hot-path reason as
# shell_command. ``spawn`` is owned by capability_scope. The three task /
# session boundary slots remain llm_criterion-only in v1 (no shell-shaped
# runtime fan-out yet; matches the F-LIFE4b honest-degrade pattern).
EXCLUDED_SLOTS = [
    "before_llm_call",
    "after_llm_call",
    "spawn",
    "on_task_complete",
    "on_session_start",
    "on_session_end",
]


def _rule(*, fires_at: str, action: str, payload: dict | None = None) -> dict:
    return {
        "id": f"cr_check_{fires_at}_{action}",
        "scope": "always",
        "enabled": True,
        "firesAt": fires_at,
        "action": action,
        "what": {
            "kind": "shell_check",
            "payload": payload
            if payload is not None
            else {"source": "inline", "inline": "echo '{\"passed\": true}'"},
        },
    }


def test_shell_check_kind_registered_in_kinds():
    assert "shell_check" in KINDS


@pytest.mark.parametrize("slot", ALLOWED_SLOTS_BLOCK)
def test_block_slots_accept_block_action(slot):
    errors = validate_custom_rule(_rule(fires_at=slot, action="block"))
    assert errors == [], errors


@pytest.mark.parametrize("slot", ALLOWED_SLOTS_AUDIT_ALL)
def test_all_v1_slots_accept_audit_action(slot):
    errors = validate_custom_rule(_rule(fires_at=slot, action="audit"))
    assert errors == [], (slot, errors)


@pytest.mark.parametrize("slot", EXCLUDED_SLOTS)
def test_excluded_slots_reject_shell_check(slot):
    errors = validate_custom_rule(_rule(fires_at=slot, action="audit"))
    assert any("shell_check" in e and slot in e for e in errors), (slot, errors)


def test_after_tool_use_rejects_block_action():
    # The matrix above keeps after_tool_use audit-only (the tool already
    # ran, so a verifier verdict cannot honestly gate dispatch).
    errors = validate_custom_rule(
        _rule(fires_at="after_tool_use", action="block")
    )
    assert any("block" in e for e in errors), errors


def test_rejects_empty_inline_script():
    errors = validate_custom_rule(
        _rule(
            fires_at="pre_final",
            action="block",
            payload={"source": "inline", "inline": "   "},
        )
    )
    assert errors, "empty inline should fail"


def test_rejects_missing_inline_source():
    errors = validate_custom_rule(
        _rule(
            fires_at="pre_final",
            action="block",
            payload={"source": "inline"},
        )
    )
    assert errors


def test_rejects_missing_file_path():
    errors = validate_custom_rule(
        _rule(
            fires_at="pre_final",
            action="block",
            payload={"source": "file"},
        )
    )
    assert errors


def test_rejects_out_of_range_timeout():
    errors = validate_custom_rule(
        _rule(
            fires_at="pre_final",
            action="block",
            payload={
                "source": "inline",
                "inline": "echo '{\"passed\": true}'",
                "timeout_seconds": 9999,
            },
        )
    )
    assert errors


def test_rejects_unknown_shell():
    errors = validate_custom_rule(
        _rule(
            fires_at="pre_final",
            action="block",
            payload={
                "source": "inline",
                "inline": "echo '{\"passed\": true}'",
                "shell": "zsh",
            },
        )
    )
    assert errors


def test_accepts_valid_file_source_rule():
    errors = validate_custom_rule(
        _rule(
            fires_at="before_tool_use",
            action="block",
            payload={
                "source": "file",
                "path": "/abs/script.sh",
                "timeout_seconds": 60,
                "shell": "sh",
                "env_vars": ["MY_KEY"],
            },
        )
    )
    assert errors == [], errors
