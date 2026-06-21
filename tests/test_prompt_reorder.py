"""Track 12: Prompt cache-aware reordering tests.

Verifies that build_system_prompt() places stable identity/static blocks
before the __MAGI_PROMPT_DYNAMIC_BOUNDARY__ marker and dynamic per-turn
blocks after it, so the prompt prefix is byte-identical across turns.
"""

from __future__ import annotations

import importlib
from datetime import UTC, datetime
from types import ModuleType

import pytest


def _builder() -> ModuleType:
    try:
        return importlib.import_module("magi_agent.runtime.message_builder")
    except ModuleNotFoundError as exc:
        pytest.fail(f"message_builder module is missing: {exc}")


def _utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


_IDENTITY = {
    "bootstrap": "bootstrap body",
    "soul": "soul body",
    "learning": "learning body",
    "identity": "identity body",
    "user": "user body",
    "agents": "agents body",
}


class TestBoundaryMarkerPresence:
    def test_boundary_marker_present_in_output(self) -> None:
        builder = _builder()
        out = builder.build_system_prompt(
            session_key="s1",
            turn_id="t1",
            identity=_IDENTITY,
            now=_utc("2026-05-28T10:00:00.000Z"),
        )
        assert builder.PROMPT_DYNAMIC_BOUNDARY in out

    def test_boundary_marker_appears_exactly_once(self) -> None:
        builder = _builder()
        out = builder.build_system_prompt(
            session_key="s1",
            turn_id="t1",
            identity=_IDENTITY,
            now=_utc("2026-05-28T10:00:00.000Z"),
        )
        assert out.count(builder.PROMPT_DYNAMIC_BOUNDARY) == 1

    def test_boundary_marker_present_without_identity(self) -> None:
        builder = _builder()
        out = builder.build_system_prompt(
            session_key="s1",
            turn_id="t1",
            identity={},
            now=_utc("2026-05-28T10:00:00.000Z"),
        )
        assert builder.PROMPT_DYNAMIC_BOUNDARY in out


class TestSectionOrder:
    def test_identity_before_boundary(self) -> None:
        builder = _builder()
        out = builder.build_system_prompt(
            session_key="s1",
            turn_id="t1",
            identity=_IDENTITY,
            now=_utc("2026-05-28T10:00:00.000Z"),
        )
        boundary_pos = out.index(builder.PROMPT_DYNAMIC_BOUNDARY)
        for section in ("# BOOTSTRAP", "# SOUL", "# LEARNING", "# IDENTITY", "# USER", "# AGENTS"):
            assert out.index(section) < boundary_pos

    def test_static_blocks_before_boundary(self) -> None:
        builder = _builder()
        out = builder.build_system_prompt(
            session_key="s1",
            turn_id="t1",
            identity=_IDENTITY,
            now=_utc("2026-05-28T10:00:00.000Z"),
        )
        boundary_pos = out.index(builder.PROMPT_DYNAMIC_BOUNDARY)
        assert out.index("<deferral-prevention>") < boundary_pos
        assert out.index("<output-rules>") < boundary_pos

    def test_session_header_after_boundary(self) -> None:
        builder = _builder()
        out = builder.build_system_prompt(
            session_key="s1",
            turn_id="t1",
            identity=_IDENTITY,
            now=_utc("2026-05-28T10:00:00.000Z"),
        )
        boundary_pos = out.index(builder.PROMPT_DYNAMIC_BOUNDARY)
        assert out.index("[Session: s1]") > boundary_pos
        assert out.index("[Turn: t1]") > boundary_pos
        assert out.index("[Time:") > boundary_pos
        assert out.index("[Channel:") > boundary_pos

    def test_temporal_context_after_boundary(self) -> None:
        builder = _builder()
        out = builder.build_system_prompt(
            session_key="s1",
            turn_id="t1",
            identity=_IDENTITY,
            now=_utc("2026-05-28T10:00:00.000Z"),
        )
        boundary_pos = out.index(builder.PROMPT_DYNAMIC_BOUNDARY)
        assert out.index('<runtime_temporal_context hidden="true">') > boundary_pos

    def test_memory_mode_after_boundary(self) -> None:
        builder = _builder()
        out = builder.build_system_prompt(
            session_key="s1",
            turn_id="t1",
            identity=_IDENTITY,
            channel={"type": "web", "memoryMode": "incognito"},
            now=_utc("2026-05-28T10:00:00.000Z"),
        )
        boundary_pos = out.index(builder.PROMPT_DYNAMIC_BOUNDARY)
        assert out.index("memory_mode: incognito") > boundary_pos

    def test_addendum_after_boundary(self) -> None:
        builder = _builder()
        out = builder.build_system_prompt(
            session_key="s1",
            turn_id="t1",
            identity=_IDENTITY,
            user_message={"metadata": {"systemPromptAddendum": "<kb>data</kb>"}},
            now=_utc("2026-05-28T10:00:00.000Z"),
        )
        boundary_pos = out.index(builder.PROMPT_DYNAMIC_BOUNDARY)
        assert out.index("<kb>data</kb>") > boundary_pos

    def test_identity_section_order_preserved(self) -> None:
        builder = _builder()
        out = builder.build_system_prompt(
            session_key="s1",
            turn_id="t1",
            identity=_IDENTITY,
            now=_utc("2026-05-28T10:00:00.000Z"),
        )
        sections = ["# BOOTSTRAP", "# IDENTITY", "# USER", "# LEARNING", "# AGENTS", "# SOUL"]
        indexes = [out.index(s) for s in sections]
        assert indexes == sorted(indexes)


class TestCachePrefixStability:
    def test_prefix_byte_identical_across_different_turns(self) -> None:
        builder = _builder()
        prompts = []
        for i, (session, turn, ts) in enumerate([
            ("sess-A", "turn-1", "2026-05-28T10:00:00.000Z"),
            ("sess-B", "turn-2", "2026-05-28T11:30:00.000Z"),
            ("sess-C", "turn-3", "2026-05-28T23:59:59.999Z"),
            ("sess-D", "turn-4", "2026-06-01T00:00:00.000Z"),
            ("sess-E", "turn-5", "2027-01-15T08:45:00.000Z"),
        ]):
            prompts.append(
                builder.build_system_prompt(
                    session_key=session,
                    turn_id=turn,
                    identity=_IDENTITY,
                    now=_utc(ts),
                )
            )

        prefixes = []
        for prompt in prompts:
            boundary_idx = prompt.index(builder.PROMPT_DYNAMIC_BOUNDARY)
            prefixes.append(prompt[:boundary_idx])

        for i in range(1, len(prefixes)):
            assert prefixes[0] == prefixes[i], (
                f"Prefix differs between turn 0 and turn {i}"
            )

    def test_prefix_stable_with_varying_channel_and_addendum(self) -> None:
        builder = _builder()
        prompt_a = builder.build_system_prompt(
            session_key="s1",
            turn_id="t1",
            identity=_IDENTITY,
            channel={"type": "web"},
            now=_utc("2026-05-28T10:00:00.000Z"),
        )
        prompt_b = builder.build_system_prompt(
            session_key="s2",
            turn_id="t2",
            identity=_IDENTITY,
            channel={"type": "telegram", "memoryMode": "incognito"},
            user_message={"metadata": {"systemPromptAddendum": "extra context"}},
            now=_utc("2026-05-28T12:00:00.000Z"),
        )

        prefix_a = prompt_a[: prompt_a.index(builder.PROMPT_DYNAMIC_BOUNDARY)]
        prefix_b = prompt_b[: prompt_b.index(builder.PROMPT_DYNAMIC_BOUNDARY)]
        assert prefix_a == prefix_b

    def test_prefix_differs_when_identity_changes(self) -> None:
        builder = _builder()
        prompt_a = builder.build_system_prompt(
            session_key="s1",
            turn_id="t1",
            identity=_IDENTITY,
            now=_utc("2026-05-28T10:00:00.000Z"),
        )
        modified_identity = {**_IDENTITY, "soul": "updated soul body"}
        prompt_b = builder.build_system_prompt(
            session_key="s1",
            turn_id="t1",
            identity=modified_identity,
            now=_utc("2026-05-28T10:00:00.000Z"),
        )

        prefix_a = prompt_a[: prompt_a.index(builder.PROMPT_DYNAMIC_BOUNDARY)]
        prefix_b = prompt_b[: prompt_b.index(builder.PROMPT_DYNAMIC_BOUNDARY)]
        assert prefix_a != prefix_b


class TestContentCompleteness:
    def test_all_sections_present_after_reorder(self) -> None:
        builder = _builder()
        out = builder.build_system_prompt(
            session_key="s1",
            turn_id="t1",
            identity=_IDENTITY,
            channel={"type": "telegram", "memoryMode": "read_only"},
            user_message={"metadata": {"systemPromptAddendum": "<kb>data</kb>"}},
            now=_utc("2026-05-28T10:00:00.000Z"),
        )
        assert "[Session: s1]" in out
        assert "[Turn: t1]" in out
        assert "[Channel: telegram]" in out
        assert '<runtime_temporal_context hidden="true">' in out
        assert "# BOOTSTRAP" in out
        assert "# AGENTS" in out
        assert "<deferral-prevention>" in out
        assert "<output-rules>" in out
        assert "memory_mode: read_only" in out
        assert "<kb>data</kb>" in out

    def test_refresh_runtime_time_header_still_works(self) -> None:
        builder = _builder()
        out = builder.build_system_prompt(
            session_key="s1",
            turn_id="t1",
            identity=_IDENTITY,
            now=_utc("2026-05-28T10:00:00.000Z"),
        )
        refreshed = builder.refresh_runtime_time_header(
            out,
            now=_utc("2026-05-28T10:05:00.000Z"),
        )
        assert "[Time: 2026-05-28T10:05:00.000Z]" in refreshed
        assert "runtime_now_utc: 2026-05-28T10:05:00.000Z" in refreshed
        assert "[Time: 2026-05-28T10:00:00.000Z]" not in refreshed


class TestBuildSystemPromptBlocks:
    def test_blocks_cache_disabled_matches_flat_prompt(self) -> None:
        builder = _builder()
        flat = builder.build_system_prompt(
            session_key="s1",
            turn_id="t1",
            identity=_IDENTITY,
            now=_utc("2026-05-28T10:00:00.000Z"),
        )
        blocks = builder.build_system_prompt_blocks(
            session_key="s1",
            turn_id="t1",
            identity=_IDENTITY,
            now=_utc("2026-05-28T10:00:00.000Z"),
            cache_enabled=False,
        )
        assert len(blocks) == 1
        assert blocks[0]["text"] == flat

    def test_blocks_cache_enabled_static_before_dynamic(self) -> None:
        builder = _builder()
        blocks = builder.build_system_prompt_blocks(
            session_key="s1",
            turn_id="t1",
            identity=_IDENTITY,
            now=_utc("2026-05-28T10:00:00.000Z"),
            cache_enabled=True,
            model="claude-sonnet-4-6",
        )
        assert len(blocks) > 1
        boundary_idx = next(
            i for i, b in enumerate(blocks)
            if builder.PROMPT_DYNAMIC_BOUNDARY in b["text"]
        )
        for i in range(boundary_idx):
            assert "cache_control" in blocks[i] or blocks[i].get("cache_scope") is not None or "# BOOTSTRAP" in blocks[i]["text"] or "<deferral-prevention>" in blocks[i]["text"] or "<output-rules>" in blocks[i]["text"]

    def test_blocks_static_indices_are_contiguous_prefix(self) -> None:
        builder = _builder()
        blocks = builder.build_system_prompt_blocks(
            session_key="s1",
            turn_id="t1",
            identity=_IDENTITY,
            now=_utc("2026-05-28T10:00:00.000Z"),
            cache_enabled=True,
            model="claude-sonnet-4-6",
        )
        boundary_idx = next(
            i for i, b in enumerate(blocks)
            if builder.PROMPT_DYNAMIC_BOUNDARY in b["text"]
        )
        for i in range(boundary_idx):
            assert "cache_control" in blocks[i], f"Block {i} before boundary should have cache_control"
