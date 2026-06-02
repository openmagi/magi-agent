from __future__ import annotations

import asyncio
import copy
import json


def _sample_system_prompt_blocks() -> list[dict[str, object]]:
    return [
        {"type": "text", "text": "You are a helpful assistant."},
        {"type": "text", "text": "Follow instructions carefully.", "cache_control": {"type": "ephemeral"}},
    ]


def _sample_assistant_message() -> dict[str, object]:
    return {
        "role": "assistant",
        "content": [
            {"type": "text", "text": "I'll help with that."},
            {"type": "tool_use", "id": "tool_1", "name": "read_file", "input": {"path": "foo.py"}},
            {"type": "tool_use", "id": "tool_2", "name": "grep", "input": {"pattern": "bar"}},
        ],
    }


class TestFrozenPromptSnapshot:
    def test_capture_restore_byte_identical(self) -> None:
        from openmagi_core_agent.runtime.prompt_snapshot import FrozenPromptSnapshot

        blocks = _sample_system_prompt_blocks()
        snapshot = FrozenPromptSnapshot.capture(blocks)
        restored_a = snapshot.restore()
        restored_b = snapshot.restore()

        assert json.dumps(restored_a, sort_keys=True) == json.dumps(restored_b, sort_keys=True)
        assert json.dumps(restored_a, sort_keys=True) == json.dumps(blocks, sort_keys=True)

    def test_fingerprint_deterministic(self) -> None:
        from openmagi_core_agent.runtime.prompt_snapshot import FrozenPromptSnapshot

        blocks = _sample_system_prompt_blocks()
        snap_a = FrozenPromptSnapshot.capture(blocks)
        snap_b = FrozenPromptSnapshot.capture(blocks)
        assert snap_a.fingerprint == snap_b.fingerprint

    def test_fingerprint_changes_on_different_input(self) -> None:
        from openmagi_core_agent.runtime.prompt_snapshot import FrozenPromptSnapshot

        blocks_a = [{"type": "text", "text": "A"}]
        blocks_b = [{"type": "text", "text": "B"}]
        snap_a = FrozenPromptSnapshot.capture(blocks_a)
        snap_b = FrozenPromptSnapshot.capture(blocks_b)
        assert snap_a.fingerprint != snap_b.fingerprint

    def test_mutation_does_not_affect_snapshot(self) -> None:
        from openmagi_core_agent.runtime.prompt_snapshot import FrozenPromptSnapshot

        blocks = _sample_system_prompt_blocks()
        snapshot = FrozenPromptSnapshot.capture(blocks)
        blocks[0]["text"] = "MUTATED"
        restored = snapshot.restore()
        assert restored[0]["text"] == "You are a helpful assistant."

    def test_cache_control_preserved(self) -> None:
        from openmagi_core_agent.runtime.prompt_snapshot import FrozenPromptSnapshot

        blocks = _sample_system_prompt_blocks()
        snapshot = FrozenPromptSnapshot.capture(blocks)
        restored = snapshot.restore()
        assert restored[1]["cache_control"] == {"type": "ephemeral"}


class TestBuildForkedMessages:
    def test_shared_prefix_identical_across_children(self) -> None:
        from openmagi_core_agent.runtime.fork_messages import build_forked_messages

        assistant = _sample_assistant_message()
        msgs_a = build_forked_messages(
            parent_assistant_message=assistant,
            directive="child A: implement feature X",
        )
        msgs_b = build_forked_messages(
            parent_assistant_message=assistant,
            directive="child B: review feature X",
        )

        assert json.dumps(msgs_a[0], sort_keys=True) == json.dumps(msgs_b[0], sort_keys=True)
        assert json.dumps(msgs_a[1], sort_keys=True) == json.dumps(msgs_b[1], sort_keys=True)

    def test_only_directive_differs(self) -> None:
        from openmagi_core_agent.runtime.fork_messages import build_forked_messages

        msgs = build_forked_messages(
            parent_assistant_message=_sample_assistant_message(),
            directive="do something specific",
        )
        assert msgs[2]["role"] == "user"
        assert msgs[2]["content"] == "do something specific"

    def test_tool_results_use_placeholder(self) -> None:
        from openmagi_core_agent.runtime.fork_messages import (
            FORK_PLACEHOLDER_RESULT,
            build_forked_messages,
        )

        msgs = build_forked_messages(
            parent_assistant_message=_sample_assistant_message(),
            directive="test",
        )
        tool_results = msgs[1]["content"]
        assert len(tool_results) == 2
        for result in tool_results:
            assert result["type"] == "tool_result"
            assert result["content"] == FORK_PLACEHOLDER_RESULT

    def test_tool_use_ids_match(self) -> None:
        from openmagi_core_agent.runtime.fork_messages import build_forked_messages

        msgs = build_forked_messages(
            parent_assistant_message=_sample_assistant_message(),
            directive="test",
        )
        tool_results = msgs[1]["content"]
        assert tool_results[0]["tool_use_id"] == "tool_1"
        assert tool_results[1]["tool_use_id"] == "tool_2"

    def test_rejects_non_assistant_message(self) -> None:
        from openmagi_core_agent.runtime.fork_messages import build_forked_messages
        import pytest

        with pytest.raises(ValueError, match="role 'assistant'"):
            build_forked_messages(
                parent_assistant_message={"role": "user", "content": "hi"},
                directive="test",
            )

    def test_rejects_non_list_content(self) -> None:
        from openmagi_core_agent.runtime.fork_messages import build_forked_messages
        import pytest

        with pytest.raises(ValueError, match="list content"):
            build_forked_messages(
                parent_assistant_message={"role": "assistant", "content": "text only"},
                directive="test",
            )


class TestForkRunner:
    def test_disabled_by_default(self) -> None:
        import os
        os.environ.pop("MAGI_FORK_CACHE_ENABLED", None)
        from openmagi_core_agent.runtime.fork_runner import ForkRunner

        runner = ForkRunner()
        assert not runner.enabled

    def test_disabled_returns_empty(self) -> None:
        import os
        os.environ.pop("MAGI_FORK_CACHE_ENABLED", None)
        from openmagi_core_agent.runtime.fork_runner import ForkRunner

        runner = ForkRunner()
        results, evidence = asyncio.run(runner.fork(
            parent_turn_id="turn-1",
            system_prompt_blocks=_sample_system_prompt_blocks(),
            parent_assistant_message=_sample_assistant_message(),
            child_directives=["a", "b"],
        ))
        assert results == []
        assert evidence.status == "disabled"
        assert evidence.child_count == 2

    def test_fork_three_children_concurrent(self) -> None:
        import os
        os.environ["MAGI_FORK_CACHE_ENABLED"] = "true"
        try:
            from openmagi_core_agent.runtime.fork_runner import ForkRunner

            call_log: list[str] = []

            async def fake_executor(*, system_prompt_blocks, messages, directive):
                call_log.append(directive)
                return f"result for {directive}"

            runner = ForkRunner(child_executor=fake_executor)
            results, evidence = asyncio.run(runner.fork(
                parent_turn_id="turn-1",
                system_prompt_blocks=_sample_system_prompt_blocks(),
                parent_assistant_message=_sample_assistant_message(),
                child_directives=["impl", "review", "test"],
            ))

            assert len(results) == 3
            assert all(r.status == "ok" for r in results)
            assert evidence.status == "ok"
            assert evidence.child_count == 3
            assert evidence.shared_prefix_fingerprint != ""
            assert set(call_log) == {"impl", "review", "test"}
        finally:
            os.environ.pop("MAGI_FORK_CACHE_ENABLED", None)

    def test_fork_shared_cache_key(self) -> None:
        import os
        os.environ["MAGI_FORK_CACHE_ENABLED"] = "true"
        try:
            from openmagi_core_agent.runtime.fork_runner import ForkRunner

            captured_blocks: list[list[dict]] = []

            async def capture_executor(*, system_prompt_blocks, messages, directive):
                captured_blocks.append(system_prompt_blocks)
                return "ok"

            runner = ForkRunner(child_executor=capture_executor)
            asyncio.run(runner.fork(
                parent_turn_id="turn-1",
                system_prompt_blocks=_sample_system_prompt_blocks(),
                parent_assistant_message=_sample_assistant_message(),
                child_directives=["a", "b", "c"],
            ))

            assert len(captured_blocks) == 3
            canonical_a = json.dumps(captured_blocks[0], sort_keys=True)
            canonical_b = json.dumps(captured_blocks[1], sort_keys=True)
            canonical_c = json.dumps(captured_blocks[2], sort_keys=True)
            assert canonical_a == canonical_b == canonical_c
        finally:
            os.environ.pop("MAGI_FORK_CACHE_ENABLED", None)

    def test_fork_error_handling(self) -> None:
        import os
        os.environ["MAGI_FORK_CACHE_ENABLED"] = "true"
        try:
            from openmagi_core_agent.runtime.fork_runner import ForkRunner

            async def failing_executor(*, system_prompt_blocks, messages, directive):
                if directive == "fail":
                    raise RuntimeError("child failed")
                return "ok"

            runner = ForkRunner(child_executor=failing_executor)
            results, evidence = asyncio.run(runner.fork(
                parent_turn_id="turn-1",
                system_prompt_blocks=_sample_system_prompt_blocks(),
                parent_assistant_message=_sample_assistant_message(),
                child_directives=["ok1", "fail", "ok2"],
            ))

            assert len(results) == 3
            assert results[0].status == "ok"
            assert results[1].status == "error"
            assert "child failed" in (results[1].error_message or "")
            assert results[2].status == "ok"
            assert evidence.status == "partial"
        finally:
            os.environ.pop("MAGI_FORK_CACHE_ENABLED", None)

    def test_evidence_records_parent_turn_id(self) -> None:
        import os
        os.environ["MAGI_FORK_CACHE_ENABLED"] = "true"
        try:
            from openmagi_core_agent.runtime.fork_runner import ForkRunner

            async def noop_executor(*, system_prompt_blocks, messages, directive):
                return "done"

            runner = ForkRunner(child_executor=noop_executor)
            _, evidence = asyncio.run(runner.fork(
                parent_turn_id="turn-abc-123",
                system_prompt_blocks=_sample_system_prompt_blocks(),
                parent_assistant_message=_sample_assistant_message(),
                child_directives=["x"],
            ))

            assert evidence.parent_turn_id == "turn-abc-123"
            assert evidence.elapsed_ms >= 0
        finally:
            os.environ.pop("MAGI_FORK_CACHE_ENABLED", None)

    def test_no_executor_returns_errors(self) -> None:
        import os
        os.environ["MAGI_FORK_CACHE_ENABLED"] = "true"
        try:
            from openmagi_core_agent.runtime.fork_runner import ForkRunner

            runner = ForkRunner()
            results, evidence = asyncio.run(runner.fork(
                parent_turn_id="turn-1",
                system_prompt_blocks=_sample_system_prompt_blocks(),
                parent_assistant_message=_sample_assistant_message(),
                child_directives=["a", "b"],
            ))

            assert len(results) == 2
            assert all(r.status == "error" for r in results)
            assert evidence.status == "error"
        finally:
            os.environ.pop("MAGI_FORK_CACHE_ENABLED", None)
