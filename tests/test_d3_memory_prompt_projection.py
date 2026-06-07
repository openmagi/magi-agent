"""D3 — gated memory prompt projection into dynamic tier.

Test contract:
  1. gate-OFF  → no <memory-context> in prompt, prompt_projection_allowed=False,
                  static prefix byte-identical
  2. gate-ON   → snapshot injected in dynamic tier, fenced, bounded
  3. incognito → no projection even when gate is on
  4. snapshot  → reflects MEMORY.md/USER.md content (redacted, bounded)
  5. cache-stability → static prefix unchanged by projection
  6. evidence/redaction → sensitive tokens stripped, digest recorded
  7. evaluate_memory_policy_with_gate → only gate-ON+non-incognito allows True
"""
from __future__ import annotations

import os
import textwrap
from pathlib import Path
from datetime import UTC, datetime

import pytest

from magi_agent.memory.policy import (
    MemoryPolicy,
    evaluate_memory_policy_with_gate,
)
from magi_agent.memory.contracts import RecallRequest
from magi_agent.memory.prompt_projection import (
    MAGI_MEMORY_PROJECTION_ENABLED_ENV,
    MEMORY_CONTEXT_OPEN,
    MEMORY_CONTEXT_CLOSE,
    MemoryPromptProjector,
    MemoryProjectionResult,
    project_memory_snapshot,
)
from magi_agent.runtime.message_builder import (
    build_system_prompt,
    build_system_prompt_blocks,
    PROMPT_DYNAMIC_BOUNDARY,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_workspace(tmp_path: Path, *, memory: str = "", user: str = "") -> Path:
    if memory:
        (tmp_path / "MEMORY.md").write_text(memory, encoding="utf-8")
    if user:
        (tmp_path / "USER.md").write_text(user, encoding="utf-8")
    return tmp_path


def _recall_request() -> RecallRequest:
    return RecallRequest(
        scope={},
        query="recall",
        purpose="plan_task",
        limit=5,
        max_bytes=32768,
    )


def _gate_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(MAGI_MEMORY_PROJECTION_ENABLED_ENV, "1")


def _gate_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(MAGI_MEMORY_PROJECTION_ENABLED_ENV, raising=False)


# ---------------------------------------------------------------------------
# 1. Gate-OFF: no projection, policy still False
# ---------------------------------------------------------------------------


def test_gate_off_projection_result_is_disabled(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _gate_off(monkeypatch)
    _make_workspace(tmp_path, memory="## User preferences\n- Prefers dark mode")
    result = project_memory_snapshot(workspace_root=tmp_path)
    assert result.enabled is False
    assert result.snapshot_block == ""
    assert result.prompt_projection_allowed is False


def test_gate_off_no_memory_context_in_system_prompt(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _gate_off(monkeypatch)
    _make_workspace(tmp_path, memory="User prefers early morning meetings.")
    prompt = build_system_prompt(
        session_key="s",
        turn_id="t",
        memory_snapshot_block="",  # gate-off passes empty
    )
    assert MEMORY_CONTEXT_OPEN not in prompt
    assert MEMORY_CONTEXT_CLOSE not in prompt


def test_gate_off_evaluate_policy_with_gate_returns_false(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _gate_off(monkeypatch)
    policy = MemoryPolicy(memory_mode="normal", source_authority="long_term_allowed")
    decision = evaluate_memory_policy_with_gate(_recall_request(), policy)
    assert decision.prompt_projection_allowed is False
    assert "projection_gate_off" in decision.reason_codes


# ---------------------------------------------------------------------------
# 2. Gate-ON: snapshot injected in dynamic tier, fenced, bounded
# ---------------------------------------------------------------------------


def test_gate_on_projection_result_is_enabled(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _gate_on(monkeypatch)
    _make_workspace(tmp_path, memory="## Preferences\n- Dark mode", user="Name: Alice")
    result = project_memory_snapshot(workspace_root=tmp_path)
    assert result.enabled is True
    assert result.prompt_projection_allowed is True
    assert MEMORY_CONTEXT_OPEN in result.snapshot_block
    assert MEMORY_CONTEXT_CLOSE in result.snapshot_block


def test_gate_on_snapshot_appears_in_dynamic_section(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _gate_on(monkeypatch)
    _make_workspace(tmp_path, memory="## Long-term context\nProject alpha in progress.")
    result = project_memory_snapshot(workspace_root=tmp_path)

    prompt = build_system_prompt(
        session_key="s",
        turn_id="t",
        memory_snapshot_block=result.snapshot_block,
    )
    assert MEMORY_CONTEXT_OPEN in prompt
    assert MEMORY_CONTEXT_CLOSE in prompt
    # Must appear AFTER the dynamic boundary
    static_part, dynamic_part = prompt.split(PROMPT_DYNAMIC_BOUNDARY, 1)
    assert MEMORY_CONTEXT_OPEN not in static_part
    assert MEMORY_CONTEXT_OPEN in dynamic_part


def test_gate_on_evaluate_policy_with_gate_allows_projection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _gate_on(monkeypatch)
    policy = MemoryPolicy(memory_mode="normal", source_authority="long_term_allowed")
    decision = evaluate_memory_policy_with_gate(_recall_request(), policy)
    assert decision.prompt_projection_allowed is True
    assert "projection_gate_on" in decision.reason_codes


# ---------------------------------------------------------------------------
# 3. Incognito: no projection even with gate on
# ---------------------------------------------------------------------------


def test_incognito_blocks_projection_even_when_gate_on(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _gate_on(monkeypatch)
    _make_workspace(tmp_path, memory="Secret project notes.")
    result = project_memory_snapshot(workspace_root=tmp_path, memory_mode="incognito")
    assert result.enabled is False
    assert result.snapshot_block == ""
    assert result.prompt_projection_allowed is False


def test_incognito_evaluate_policy_with_gate_returns_false(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _gate_on(monkeypatch)
    policy = MemoryPolicy(memory_mode="incognito", source_authority="long_term_allowed")
    decision = evaluate_memory_policy_with_gate(_recall_request(), policy)
    assert decision.prompt_projection_allowed is False
    assert "incognito_blocks_projection" in decision.reason_codes


# ---------------------------------------------------------------------------
# 4. Snapshot reflects MEMORY.md/USER.md content (redacted, bounded)
# ---------------------------------------------------------------------------


def test_snapshot_contains_memory_md_content(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _gate_on(monkeypatch)
    content = "## Preferences\n- Prefers concise summaries"
    _make_workspace(tmp_path, memory=content)
    result = project_memory_snapshot(workspace_root=tmp_path)
    assert "Prefers concise summaries" in result.snapshot_block


def test_snapshot_contains_user_md_content_when_present(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _gate_on(monkeypatch)
    _make_workspace(tmp_path, user="Name: Bob\nRole: Developer")
    result = project_memory_snapshot(workspace_root=tmp_path)
    assert "Name: Bob" in result.snapshot_block


def test_snapshot_is_empty_when_no_files_exist(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _gate_on(monkeypatch)
    result = project_memory_snapshot(workspace_root=tmp_path)
    # No files → empty or minimal block; no injection needed
    assert result.snapshot_block == ""


def test_snapshot_redacts_bearer_tokens(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _gate_on(monkeypatch)
    _make_workspace(
        tmp_path,
        memory="Token: Bearer sk-test-super-secret-token-abc123\nother content",
    )
    result = project_memory_snapshot(workspace_root=tmp_path)
    assert "sk-test-super-secret-token-abc123" not in result.snapshot_block


def test_snapshot_redacts_private_paths(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _gate_on(monkeypatch)
    _make_workspace(
        tmp_path,
        memory="Config at /Users/kevin/secret/config.yaml\nsome visible content",
    )
    result = project_memory_snapshot(workspace_root=tmp_path)
    assert "/Users/kevin/secret" not in result.snapshot_block


def test_snapshot_is_bounded_by_max_bytes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _gate_on(monkeypatch)
    big_content = "x" * 200_000
    _make_workspace(tmp_path, memory=big_content)
    result = project_memory_snapshot(workspace_root=tmp_path, max_bytes=4096)
    assert len(result.snapshot_block.encode("utf-8")) <= 4096 + 200  # fence overhead


def test_snapshot_result_records_evidence_digests(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _gate_on(monkeypatch)
    _make_workspace(tmp_path, memory="## Notes\n- Remember to check logs")
    result = project_memory_snapshot(workspace_root=tmp_path)
    # Evidence: snapshot_digest must be a sha256 digest string
    assert result.snapshot_digest.startswith("sha256:")
    assert len(result.snapshot_digest) == len("sha256:") + 64


# ---------------------------------------------------------------------------
# 5. Cache-stability: static prefix unchanged by projection
# ---------------------------------------------------------------------------


def test_static_prefix_unchanged_regardless_of_gate(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Gate-off and gate-on must produce identical static prefixes."""
    _make_workspace(tmp_path, memory="## Notes\n- Memory content here")

    _gate_off(monkeypatch)
    prompt_off = build_system_prompt(
        session_key="s",
        turn_id="t",
        memory_snapshot_block="",
    )

    _gate_on(monkeypatch)
    result = project_memory_snapshot(workspace_root=tmp_path)
    prompt_on = build_system_prompt(
        session_key="s",
        turn_id="t",
        memory_snapshot_block=result.snapshot_block,
    )

    def _static(prompt: str) -> str:
        return prompt.split(PROMPT_DYNAMIC_BOUNDARY, 1)[0]

    assert _static(prompt_off) == _static(prompt_on)


def test_memory_context_never_in_static_prefix(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _gate_on(monkeypatch)
    _make_workspace(tmp_path, memory="## Long-term notes\n- Deploy infra first")
    result = project_memory_snapshot(workspace_root=tmp_path)
    prompt = build_system_prompt(
        session_key="s",
        turn_id="t",
        memory_snapshot_block=result.snapshot_block,
    )
    static_part = prompt.split(PROMPT_DYNAMIC_BOUNDARY, 1)[0]
    assert MEMORY_CONTEXT_OPEN not in static_part


# ---------------------------------------------------------------------------
# 6. Fencing: no user-visible confusion
# ---------------------------------------------------------------------------


def test_snapshot_block_is_clearly_fenced(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _gate_on(monkeypatch)
    _make_workspace(tmp_path, memory="Deploy infra notes.")
    result = project_memory_snapshot(workspace_root=tmp_path)
    assert result.snapshot_block.startswith(MEMORY_CONTEXT_OPEN)
    assert result.snapshot_block.rstrip().endswith(MEMORY_CONTEXT_CLOSE)


# ---------------------------------------------------------------------------
# 7. Projector via class interface
# ---------------------------------------------------------------------------


def test_projector_class_respects_gate(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _make_workspace(tmp_path, memory="## Notes\n- hello world")
    projector = MemoryPromptProjector(workspace_root=tmp_path)

    _gate_off(monkeypatch)
    off = projector.project()
    assert off.enabled is False

    _gate_on(monkeypatch)
    on = projector.project()
    assert on.enabled is True
    assert "hello world" in on.snapshot_block


def test_projector_class_with_explicit_enabled_true(tmp_path: Path) -> None:
    """Pass enabled=True directly (no env mutation needed — for unit tests)."""
    _make_workspace(tmp_path, memory="## Preferences\n- concise answers")
    projector = MemoryPromptProjector(workspace_root=tmp_path, enabled=True)
    result = projector.project()
    assert result.enabled is True
    assert "concise answers" in result.snapshot_block


def test_projector_explicit_enabled_incognito_still_blocked(tmp_path: Path) -> None:
    _make_workspace(tmp_path, memory="Private notes.")
    projector = MemoryPromptProjector(workspace_root=tmp_path, enabled=True)
    result = projector.project(memory_mode="incognito")
    assert result.enabled is False
    assert result.snapshot_block == ""


# ---------------------------------------------------------------------------
# 8. build_system_prompt_blocks dynamic tier also includes snapshot
# ---------------------------------------------------------------------------


def test_blocks_mode_snapshot_in_dynamic_block(tmp_path: Path) -> None:
    """build_system_prompt_blocks with cache_enabled=False must include snapshot in dynamic."""
    _make_workspace(tmp_path, memory="## Notes\n- project alpha in progress")
    projector = MemoryPromptProjector(workspace_root=tmp_path, enabled=True)
    result = projector.project()

    blocks = build_system_prompt_blocks(
        session_key="s",
        turn_id="t",
        memory_snapshot_block=result.snapshot_block,
        cache_enabled=False,
    )
    assert len(blocks) == 1
    combined_text = blocks[0]["text"]
    static_part, dynamic_part = combined_text.split(PROMPT_DYNAMIC_BOUNDARY, 1)
    assert MEMORY_CONTEXT_OPEN not in static_part
    assert MEMORY_CONTEXT_OPEN in dynamic_part
