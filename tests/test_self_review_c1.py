"""C1 — Post-turn self-review fork: TDD test suite.

Tests
-----
1. gate-off → no fork, None returned, zero background work.
2. shadow mode (enabled + shadow=True) → candidates emitted, acted=False.
3. live mode (enabled + shadow=False) → candidate_sink called, acted=False.
4. parent-cache-untouched invariant: pre/post fingerprints equal after fork.
5. restricted tool surface: REVIEW_DISABLED_TOOLSETS covers shell/net/FS/msg/sched.
6. evidence redaction: EvidenceRecord fields contain only digests/lengths, no raw text.
7. fail-open: fork runner exception → None returned, no re-raise.
8. fork runner disabled (MAGI_FORK_CACHE_ENABLED=0) → no candidates, hook still runs.
9. cache-fingerprint mismatch → candidates=0, cacheUntouched=False in result.
10. ReviewCandidate model: frozen, acted always False from C1.
11. SelfReviewConfig.from_env: reads MAGI_SELF_REVIEW_ENABLED / MAGI_SELF_REVIEW_SHADOW.
12. _parse_fork_output: happy path, bad JSON, missing fields, out-of-range confidence.
"""
from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from typing import Any

import pytest

from magi_agent.harness.self_review import (
    REVIEW_DISABLED_TOOLSETS,
    CandidateSink,
    ForkReviewInput,
    ForkReviewResult,
    ReviewCandidate,
    SelfReviewConfig,
    _parse_fork_output,
    _turn_provenance_digest,
    run_self_review_hook,
)
from magi_agent.runtime.fork_runner import ChildResult, ForkCacheShareEvidence
from magi_agent.runtime.prompt_snapshot import FrozenPromptSnapshot


# ---------------------------------------------------------------------------
# Fakes / helpers
# ---------------------------------------------------------------------------


class FakeCandidateSink:
    """Collects all received ReviewCandidate objects for assertion."""

    def __init__(self) -> None:
        self.received: list[ReviewCandidate] = []

    def receive(self, candidate: ReviewCandidate) -> None:
        self.received.append(candidate)


class FakeForkRunner:
    """Fake ForkRunner: returns pre-configured ChildResult list."""

    def __init__(
        self,
        *,
        child_output: str = "",
        status: str = "ok",
        raise_on_fork: bool = False,
    ) -> None:
        self._output = child_output
        self._status = status
        self._raise_on_fork = raise_on_fork
        self.fork_calls: list[dict[str, Any]] = []

    async def fork(
        self,
        *,
        parent_turn_id: str,
        system_prompt_blocks: list[dict[str, Any]],
        parent_assistant_message: dict[str, Any],
        child_directives: list[str],
    ) -> tuple[list[ChildResult], ForkCacheShareEvidence]:
        self.fork_calls.append(
            {
                "parent_turn_id": parent_turn_id,
                "system_prompt_blocks": system_prompt_blocks,
                "parent_assistant_message": parent_assistant_message,
                "child_directives": child_directives,
            }
        )
        if self._raise_on_fork:
            raise RuntimeError("injected fork failure")

        result = ChildResult(
            directive=child_directives[0] if child_directives else "",
            status=self._status,  # type: ignore[arg-type]
            output=self._output,
        )
        evidence = ForkCacheShareEvidence(
            parentTurnId=parent_turn_id,
            childCount=len(child_directives),
            sharedPrefixFingerprint="fake-fp",
            status="ok",
            elapsedMs=0.1,
        )
        return [result], evidence


def _make_system_blocks() -> list[dict[str, Any]]:
    return [
        {"type": "text", "text": "You are a helpful assistant."},
        {"type": "text", "text": "Follow instructions."},
    ]


def _make_assistant_message() -> dict[str, Any]:
    return {
        "role": "assistant",
        "content": [{"type": "text", "text": "I completed the task."}],
    }


_NOW = datetime(2026, 6, 7, 12, 0, 0, tzinfo=UTC)


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# 1. Gate OFF → no-op
# ---------------------------------------------------------------------------


class TestGateOff:
    def test_gate_off_returns_none(self) -> None:
        config = SelfReviewConfig(enabled=False, shadow=True)
        sink = FakeCandidateSink()
        runner = FakeForkRunner()
        result = _run(
            run_self_review_hook(
                session_id="sess-1",
                turn_id="turn-1",
                system_prompt_blocks=_make_system_blocks(),
                parent_assistant_message=_make_assistant_message(),
                fork_runner=runner,
                candidate_sink=sink,
                config=config,
                now=_NOW,
            )
        )
        assert result is None

    def test_gate_off_fork_never_called(self) -> None:
        config = SelfReviewConfig(enabled=False, shadow=True)
        sink = FakeCandidateSink()
        runner = FakeForkRunner()
        _run(
            run_self_review_hook(
                session_id="sess-1",
                turn_id="turn-1",
                system_prompt_blocks=_make_system_blocks(),
                parent_assistant_message=_make_assistant_message(),
                fork_runner=runner,
                candidate_sink=sink,
                config=config,
                now=_NOW,
            )
        )
        assert runner.fork_calls == []

    def test_gate_off_no_candidates_received(self) -> None:
        config = SelfReviewConfig(enabled=False, shadow=True)
        sink = FakeCandidateSink()
        runner = FakeForkRunner()
        _run(
            run_self_review_hook(
                session_id="sess-1",
                turn_id="turn-1",
                system_prompt_blocks=_make_system_blocks(),
                parent_assistant_message=_make_assistant_message(),
                fork_runner=runner,
                candidate_sink=sink,
                config=config,
                now=_NOW,
            )
        )
        assert sink.received == []

    def test_from_env_default_disabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("MAGI_SELF_REVIEW_ENABLED", raising=False)
        cfg = SelfReviewConfig.from_env()
        assert cfg.enabled is False

    def test_from_env_explicit_off(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MAGI_SELF_REVIEW_ENABLED", "false")
        cfg = SelfReviewConfig.from_env()
        assert cfg.enabled is False


# ---------------------------------------------------------------------------
# 2. Shadow mode → candidates emitted, acted=False
# ---------------------------------------------------------------------------


class TestShadowMode:
    def _fork_output_with_candidates(self) -> str:
        objs = [
            {"kind": "memory", "proposal": "User prefers concise answers.", "confidence": 0.8},
            {"kind": "skill", "proposal": "Always summarize at the end.", "confidence": 0.7},
        ]
        return " ".join(json.dumps(o) for o in objs)

    def test_shadow_candidates_emitted(self) -> None:
        config = SelfReviewConfig(enabled=True, shadow=True)
        sink = FakeCandidateSink()
        runner = FakeForkRunner(child_output=self._fork_output_with_candidates())
        result = _run(
            run_self_review_hook(
                session_id="sess-shadow",
                turn_id="turn-shadow",
                system_prompt_blocks=_make_system_blocks(),
                parent_assistant_message=_make_assistant_message(),
                fork_runner=runner,
                candidate_sink=sink,
                config=config,
                now=_NOW,
            )
        )
        assert result is not None
        assert result.candidates_emitted == 2
        assert len(sink.received) == 2

    def test_shadow_acted_always_false(self) -> None:
        config = SelfReviewConfig(enabled=True, shadow=True)
        sink = FakeCandidateSink()
        runner = FakeForkRunner(child_output=self._fork_output_with_candidates())
        _run(
            run_self_review_hook(
                session_id="sess-shadow",
                turn_id="turn-shadow",
                system_prompt_blocks=_make_system_blocks(),
                parent_assistant_message=_make_assistant_message(),
                fork_runner=runner,
                candidate_sink=sink,
                config=config,
                now=_NOW,
            )
        )
        for candidate in sink.received:
            assert candidate.acted is False

    def test_shadow_mode_field_on_candidates(self) -> None:
        config = SelfReviewConfig(enabled=True, shadow=True)
        sink = FakeCandidateSink()
        runner = FakeForkRunner(child_output=self._fork_output_with_candidates())
        _run(
            run_self_review_hook(
                session_id="sess-shadow",
                turn_id="turn-shadow",
                system_prompt_blocks=_make_system_blocks(),
                parent_assistant_message=_make_assistant_message(),
                fork_runner=runner,
                candidate_sink=sink,
                config=config,
                now=_NOW,
            )
        )
        for candidate in sink.received:
            assert candidate.mode == "shadow"

    def test_shadow_result_mode(self) -> None:
        config = SelfReviewConfig(enabled=True, shadow=True)
        sink = FakeCandidateSink()
        runner = FakeForkRunner(child_output=self._fork_output_with_candidates())
        result = _run(
            run_self_review_hook(
                session_id="sess-shadow",
                turn_id="turn-shadow",
                system_prompt_blocks=_make_system_blocks(),
                parent_assistant_message=_make_assistant_message(),
                fork_runner=runner,
                candidate_sink=sink,
                config=config,
                now=_NOW,
            )
        )
        assert result is not None
        assert result.mode == "shadow"


# ---------------------------------------------------------------------------
# 3. Live mode → candidate_sink called, acted=False (C1 never sets acted=True)
# ---------------------------------------------------------------------------


class TestLiveMode:
    def _output(self) -> str:
        return json.dumps(
            {"kind": "memory", "proposal": "Live candidate.", "confidence": 0.9}
        )

    def test_live_sink_called(self) -> None:
        config = SelfReviewConfig(enabled=True, shadow=False)
        sink = FakeCandidateSink()
        runner = FakeForkRunner(child_output=self._output())
        _run(
            run_self_review_hook(
                session_id="sess-live",
                turn_id="turn-live",
                system_prompt_blocks=_make_system_blocks(),
                parent_assistant_message=_make_assistant_message(),
                fork_runner=runner,
                candidate_sink=sink,
                config=config,
                now=_NOW,
            )
        )
        assert len(sink.received) == 1

    def test_live_acted_still_false(self) -> None:
        """C1 NEVER produces acted=True — that is C2's concern."""
        config = SelfReviewConfig(enabled=True, shadow=False)
        sink = FakeCandidateSink()
        runner = FakeForkRunner(child_output=self._output())
        _run(
            run_self_review_hook(
                session_id="sess-live",
                turn_id="turn-live",
                system_prompt_blocks=_make_system_blocks(),
                parent_assistant_message=_make_assistant_message(),
                fork_runner=runner,
                candidate_sink=sink,
                config=config,
                now=_NOW,
            )
        )
        for c in sink.received:
            assert c.acted is False

    def test_live_mode_field(self) -> None:
        config = SelfReviewConfig(enabled=True, shadow=False)
        sink = FakeCandidateSink()
        runner = FakeForkRunner(child_output=self._output())
        _run(
            run_self_review_hook(
                session_id="sess-live",
                turn_id="turn-live",
                system_prompt_blocks=_make_system_blocks(),
                parent_assistant_message=_make_assistant_message(),
                fork_runner=runner,
                candidate_sink=sink,
                config=config,
                now=_NOW,
            )
        )
        for c in sink.received:
            assert c.mode == "live"


# ---------------------------------------------------------------------------
# 4. Parent-cache-untouched invariant
# ---------------------------------------------------------------------------


class TestParentCacheUntouched:
    def test_pre_post_fingerprints_equal(self) -> None:
        blocks = _make_system_blocks()
        snapshot_before = FrozenPromptSnapshot.capture(blocks)

        config = SelfReviewConfig(enabled=True, shadow=True)
        sink = FakeCandidateSink()
        runner = FakeForkRunner(child_output="")
        result = _run(
            run_self_review_hook(
                session_id="sess-cache",
                turn_id="turn-cache",
                system_prompt_blocks=blocks,
                parent_assistant_message=_make_assistant_message(),
                fork_runner=runner,
                candidate_sink=sink,
                config=config,
                now=_NOW,
            )
        )
        assert result is not None
        assert result.cache_untouched is True
        # The parent's blocks fingerprint must be the same after the hook.
        snapshot_after = FrozenPromptSnapshot.capture(blocks)
        assert snapshot_before.fingerprint == snapshot_after.fingerprint

    def test_parent_blocks_not_mutated(self) -> None:
        blocks = _make_system_blocks()
        original_text = blocks[0]["text"]

        config = SelfReviewConfig(enabled=True, shadow=True)
        sink = FakeCandidateSink()
        runner = FakeForkRunner(child_output="")
        _run(
            run_self_review_hook(
                session_id="sess-immut",
                turn_id="turn-immut",
                system_prompt_blocks=blocks,
                parent_assistant_message=_make_assistant_message(),
                fork_runner=runner,
                candidate_sink=sink,
                config=config,
                now=_NOW,
            )
        )
        assert blocks[0]["text"] == original_text


# ---------------------------------------------------------------------------
# 5. Restricted tool surface
# ---------------------------------------------------------------------------


class TestRestrictedToolSurface:
    def test_disabled_toolsets_non_empty(self) -> None:
        assert len(REVIEW_DISABLED_TOOLSETS) > 0

    def test_shell_tools_restricted(self) -> None:
        shell_tools = {"BashTool", "RunCommand", "ExecuteCode"}
        assert shell_tools.issubset(set(REVIEW_DISABLED_TOOLSETS))

    def test_network_tools_restricted(self) -> None:
        net_tools = {"WebSearch", "WebFetch"}
        assert net_tools.issubset(set(REVIEW_DISABLED_TOOLSETS))

    def test_file_write_tools_restricted(self) -> None:
        file_tools = {"WriteFile", "EditFile"}
        assert file_tools.issubset(set(REVIEW_DISABLED_TOOLSETS))

    def test_messaging_tools_restricted(self) -> None:
        msg_tools = {"TelegramSend", "DiscordSend", "FileDeliver"}
        assert msg_tools.issubset(set(REVIEW_DISABLED_TOOLSETS))

    def test_scheduling_tools_restricted(self) -> None:
        sched_tools = {"CronCreate", "TaskCreate"}
        assert sched_tools.issubset(set(REVIEW_DISABLED_TOOLSETS))

    def test_evidence_records_disabled_toolsets(self) -> None:
        """The evidence record must include disabledToolsets for audit."""
        config = SelfReviewConfig(enabled=True, shadow=True)
        sink = FakeCandidateSink()
        runner = FakeForkRunner(child_output="")
        result = _run(
            run_self_review_hook(
                session_id="sess-tools",
                turn_id="turn-tools",
                system_prompt_blocks=_make_system_blocks(),
                parent_assistant_message=_make_assistant_message(),
                fork_runner=runner,
                candidate_sink=sink,
                config=config,
                now=_NOW,
            )
        )
        assert result is not None
        fields = dict(result.evidence.fields)
        assert "disabledToolsets" in fields
        disabled = fields["disabledToolsets"]
        assert "BashTool" in disabled


# ---------------------------------------------------------------------------
# 6. Evidence redaction
# ---------------------------------------------------------------------------


class TestEvidenceRedaction:
    def test_evidence_type_is_custom(self) -> None:
        config = SelfReviewConfig(enabled=True, shadow=True)
        sink = FakeCandidateSink()
        runner = FakeForkRunner(child_output="")
        result = _run(
            run_self_review_hook(
                session_id="sess-ev",
                turn_id="turn-ev",
                system_prompt_blocks=_make_system_blocks(),
                parent_assistant_message=_make_assistant_message(),
                fork_runner=runner,
                candidate_sink=sink,
                config=config,
                now=_NOW,
            )
        )
        assert result is not None
        assert result.evidence.type == "custom:SelfReviewForkExecution"

    def test_evidence_contains_no_raw_transcript(self) -> None:
        """No raw text from the conversation may appear in the evidence fields."""
        secret_text = "TOP SECRET PLAN: deploy at midnight"
        blocks = [{"type": "text", "text": secret_text}]
        assistant_msg = {
            "role": "assistant",
            "content": [{"type": "text", "text": secret_text}],
        }
        config = SelfReviewConfig(enabled=True, shadow=True)
        sink = FakeCandidateSink()
        runner = FakeForkRunner(child_output="")
        result = _run(
            run_self_review_hook(
                session_id="sess-redact",
                turn_id="turn-redact",
                system_prompt_blocks=blocks,
                parent_assistant_message=assistant_msg,
                fork_runner=runner,
                candidate_sink=sink,
                config=config,
                now=_NOW,
            )
        )
        assert result is not None
        # Serialize all evidence fields and check no raw secret text appears.
        evidence_json = result.evidence.model_dump_json()
        assert secret_text not in evidence_json

    def test_evidence_fingerprints_truncated(self) -> None:
        config = SelfReviewConfig(enabled=True, shadow=True)
        sink = FakeCandidateSink()
        runner = FakeForkRunner(child_output="")
        result = _run(
            run_self_review_hook(
                session_id="sess-fp",
                turn_id="turn-fp",
                system_prompt_blocks=_make_system_blocks(),
                parent_assistant_message=_make_assistant_message(),
                fork_runner=runner,
                candidate_sink=sink,
                config=config,
                now=_NOW,
            )
        )
        assert result is not None
        fields = dict(result.evidence.fields)
        # Fingerprints are truncated to 16 chars (not full 64-char SHA-256).
        assert len(str(fields["preForkFingerprint"])) == 16
        assert len(str(fields["postForkFingerprint"])) == 16

    def test_evidence_status_ok_on_success(self) -> None:
        config = SelfReviewConfig(enabled=True, shadow=True)
        sink = FakeCandidateSink()
        runner = FakeForkRunner(child_output="")
        result = _run(
            run_self_review_hook(
                session_id="sess-ok",
                turn_id="turn-ok",
                system_prompt_blocks=_make_system_blocks(),
                parent_assistant_message=_make_assistant_message(),
                fork_runner=runner,
                candidate_sink=sink,
                config=config,
                now=_NOW,
            )
        )
        assert result is not None
        assert result.evidence.status == "ok"


# ---------------------------------------------------------------------------
# 7. Fail-open: fork runner exception → None returned, no re-raise
# ---------------------------------------------------------------------------


class TestFailOpen:
    def test_fork_exception_returns_none(self) -> None:
        config = SelfReviewConfig(enabled=True, shadow=True)
        sink = FakeCandidateSink()
        runner = FakeForkRunner(raise_on_fork=True)
        result = _run(
            run_self_review_hook(
                session_id="sess-fail",
                turn_id="turn-fail",
                system_prompt_blocks=_make_system_blocks(),
                parent_assistant_message=_make_assistant_message(),
                fork_runner=runner,
                candidate_sink=sink,
                config=config,
                now=_NOW,
            )
        )
        assert result is None

    def test_fork_exception_no_candidates(self) -> None:
        config = SelfReviewConfig(enabled=True, shadow=True)
        sink = FakeCandidateSink()
        runner = FakeForkRunner(raise_on_fork=True)
        _run(
            run_self_review_hook(
                session_id="sess-fail",
                turn_id="turn-fail",
                system_prompt_blocks=_make_system_blocks(),
                parent_assistant_message=_make_assistant_message(),
                fork_runner=runner,
                candidate_sink=sink,
                config=config,
                now=_NOW,
            )
        )
        assert sink.received == []


# ---------------------------------------------------------------------------
# 8. Fork runner disabled → no candidates, hook still completes
# ---------------------------------------------------------------------------


class TestForkRunnerDisabled:
    def test_disabled_fork_runner_returns_result(self) -> None:
        """When ForkRunner.fork returns empty results (disabled), hook returns normally."""
        config = SelfReviewConfig(enabled=True, shadow=True)
        sink = FakeCandidateSink()

        # Simulate a fork runner that returns [] (ForkRunner disabled path).
        class EmptyForkRunner:
            async def fork(self, **_: Any) -> tuple[list[ChildResult], ForkCacheShareEvidence]:
                evidence = ForkCacheShareEvidence(
                    parentTurnId="t1",
                    childCount=1,
                    sharedPrefixFingerprint="",
                    status="disabled",
                    elapsedMs=0.0,
                )
                return [], evidence

        result = _run(
            run_self_review_hook(
                session_id="sess-disabled-fork",
                turn_id="turn-disabled-fork",
                system_prompt_blocks=_make_system_blocks(),
                parent_assistant_message=_make_assistant_message(),
                fork_runner=EmptyForkRunner(),
                candidate_sink=sink,
                config=config,
                now=_NOW,
            )
        )
        assert result is not None
        assert result.candidates_emitted == 0
        assert sink.received == []


# ---------------------------------------------------------------------------
# 9. Cache-fingerprint mismatch (injected via ForkReviewInput manipulation)
# ---------------------------------------------------------------------------


class TestCacheFingerprintMismatch:
    def test_mismatch_candidates_zero(self) -> None:
        """If pre/post fingerprints differ, candidates_emitted must be 0."""
        from magi_agent.harness.self_review import _run_review_fork

        # Build an input with a bogus pre_fork_fingerprint so it won't match.
        blocks = _make_system_blocks()
        import copy
        blocks_copy = copy.deepcopy(blocks)

        from magi_agent.runtime.prompt_snapshot import FrozenPromptSnapshot
        real_fp = FrozenPromptSnapshot.capture(blocks_copy).fingerprint

        fork_input = ForkReviewInput(
            sessionId="sess-mismatch",
            turnId="turn-mismatch",
            systemPromptBlocks=tuple(blocks_copy),
            parentAssistantMessage=_make_assistant_message(),
            # Deliberately wrong fingerprint.
            preForkFingerprint="0" * 64,
        )

        sink = FakeCandidateSink()
        runner = FakeForkRunner(
            child_output=json.dumps(
                {"kind": "memory", "proposal": "Proposal.", "confidence": 0.5}
            )
        )
        result = _run(
            _run_review_fork(
                fork_input=fork_input,
                fork_runner=runner,
                candidate_sink=sink,
                mode="shadow",
                now=_NOW,
            )
        )
        assert result.candidates_emitted == 0
        assert result.cache_untouched is False
        assert sink.received == []

    def test_mismatch_evidence_status_failed(self) -> None:
        from magi_agent.harness.self_review import _run_review_fork
        import copy

        blocks_copy = copy.deepcopy(_make_system_blocks())
        fork_input = ForkReviewInput(
            sessionId="sess-mismatch2",
            turnId="turn-mismatch2",
            systemPromptBlocks=tuple(blocks_copy),
            parentAssistantMessage=_make_assistant_message(),
            preForkFingerprint="0" * 64,  # bad fingerprint
        )

        sink = FakeCandidateSink()
        runner = FakeForkRunner(child_output="")
        result = _run(
            _run_review_fork(
                fork_input=fork_input,
                fork_runner=runner,
                candidate_sink=sink,
                mode="shadow",
                now=_NOW,
            )
        )
        assert result.evidence.status == "failed"


# ---------------------------------------------------------------------------
# 10. ReviewCandidate model: frozen, acted always False
# ---------------------------------------------------------------------------


class TestReviewCandidateModel:
    def test_model_frozen(self) -> None:
        candidate = ReviewCandidate(
            kind="memory",
            proposal="Test proposal.",
            provenanceDigest="abc123",
            confidence=0.75,
            sessionId="s1",
            turnId="t1",
            acted=False,
            mode="shadow",
        )
        with pytest.raises(Exception):
            candidate.acted = True  # type: ignore[misc]

    def test_acted_default_false(self) -> None:
        candidate = ReviewCandidate(
            kind="skill",
            proposal="Test skill.",
            provenanceDigest="abc123",
            confidence=0.6,
            sessionId="s1",
            turnId="t1",
            mode="live",
        )
        assert candidate.acted is False

    def test_candidate_kind_memory(self) -> None:
        candidate = ReviewCandidate(
            kind="memory",
            proposal="A memory fact.",
            provenanceDigest="digest",
            confidence=0.8,
            sessionId="s1",
            turnId="t1",
            mode="shadow",
        )
        assert candidate.kind == "memory"

    def test_candidate_kind_skill(self) -> None:
        candidate = ReviewCandidate(
            kind="skill",
            proposal="A skill pattern.",
            provenanceDigest="digest",
            confidence=0.6,
            sessionId="s1",
            turnId="t1",
            mode="live",
        )
        assert candidate.kind == "skill"


# ---------------------------------------------------------------------------
# 11. SelfReviewConfig.from_env
# ---------------------------------------------------------------------------


class TestSelfReviewConfigFromEnv:
    def test_enabled_via_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MAGI_SELF_REVIEW_ENABLED", "1")
        cfg = SelfReviewConfig.from_env()
        assert cfg.enabled is True

    def test_shadow_default_true(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MAGI_SELF_REVIEW_ENABLED", "1")
        monkeypatch.delenv("MAGI_SELF_REVIEW_SHADOW", raising=False)
        cfg = SelfReviewConfig.from_env()
        assert cfg.shadow is True

    def test_shadow_explicit_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MAGI_SELF_REVIEW_ENABLED", "1")
        monkeypatch.setenv("MAGI_SELF_REVIEW_SHADOW", "false")
        cfg = SelfReviewConfig.from_env()
        assert cfg.shadow is False

    def test_shadow_explicit_zero(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MAGI_SELF_REVIEW_ENABLED", "1")
        monkeypatch.setenv("MAGI_SELF_REVIEW_SHADOW", "0")
        cfg = SelfReviewConfig.from_env()
        assert cfg.shadow is False


# ---------------------------------------------------------------------------
# 12. _parse_fork_output: unit tests
# ---------------------------------------------------------------------------


class TestParseForkOutput:
    def _run_parse(self, output: str) -> list[ReviewCandidate]:
        return _parse_fork_output(
            output=output,
            session_id="s",
            turn_id="t",
            provenance_digest="digest",
            mode="shadow",
        )

    def test_happy_path_memory(self) -> None:
        output = json.dumps(
            {"kind": "memory", "proposal": "A fact.", "confidence": 0.8}
        )
        result = self._run_parse(output)
        assert len(result) == 1
        assert result[0].kind == "memory"
        assert result[0].confidence == 0.8

    def test_happy_path_skill(self) -> None:
        output = json.dumps(
            {"kind": "skill", "proposal": "A skill.", "confidence": 0.6}
        )
        result = self._run_parse(output)
        assert len(result) == 1
        assert result[0].kind == "skill"

    def test_multiple_candidates(self) -> None:
        objs = [
            {"kind": "memory", "proposal": "Fact 1.", "confidence": 0.9},
            {"kind": "skill", "proposal": "Skill 1.", "confidence": 0.7},
        ]
        output = " ".join(json.dumps(o) for o in objs)
        result = self._run_parse(output)
        assert len(result) == 2

    def test_invalid_json_skipped(self) -> None:
        output = "{not valid json} " + json.dumps(
            {"kind": "memory", "proposal": "Good.", "confidence": 0.5}
        )
        result = self._run_parse(output)
        assert len(result) == 1

    def test_invalid_kind_skipped(self) -> None:
        output = json.dumps({"kind": "invalid", "proposal": "Bad.", "confidence": 0.5})
        result = self._run_parse(output)
        assert result == []

    def test_missing_proposal_skipped(self) -> None:
        output = json.dumps({"kind": "memory", "confidence": 0.5})
        result = self._run_parse(output)
        assert result == []

    def test_empty_proposal_skipped(self) -> None:
        output = json.dumps({"kind": "memory", "proposal": "  ", "confidence": 0.5})
        result = self._run_parse(output)
        assert result == []

    def test_confidence_clamped_high(self) -> None:
        output = json.dumps({"kind": "memory", "proposal": "X.", "confidence": 99.0})
        result = self._run_parse(output)
        assert len(result) == 1
        assert result[0].confidence == 1.0

    def test_confidence_clamped_low(self) -> None:
        output = json.dumps({"kind": "memory", "proposal": "X.", "confidence": -5.0})
        result = self._run_parse(output)
        assert len(result) == 1
        assert result[0].confidence == 0.0

    def test_confidence_default_when_missing(self) -> None:
        output = json.dumps({"kind": "memory", "proposal": "X."})
        result = self._run_parse(output)
        assert len(result) == 1
        assert result[0].confidence == 0.5

    def test_empty_output_returns_empty(self) -> None:
        result = self._run_parse("")
        assert result == []

    def test_acted_always_false(self) -> None:
        output = json.dumps({"kind": "memory", "proposal": "X.", "confidence": 0.8})
        result = self._run_parse(output)
        assert result[0].acted is False


# ---------------------------------------------------------------------------
# 13. Provenance digest helper
# ---------------------------------------------------------------------------


class TestTurnProvenanceDigest:
    def test_deterministic(self) -> None:
        d1 = _turn_provenance_digest("sess-1", "turn-1")
        d2 = _turn_provenance_digest("sess-1", "turn-1")
        assert d1 == d2

    def test_different_inputs_differ(self) -> None:
        d1 = _turn_provenance_digest("sess-A", "turn-1")
        d2 = _turn_provenance_digest("sess-B", "turn-1")
        assert d1 != d2

    def test_is_sha256_hex(self) -> None:
        d = _turn_provenance_digest("sess", "turn")
        assert len(d) == 64
        int(d, 16)  # must be valid hex


# ---------------------------------------------------------------------------
# 14. CandidateSink Protocol satisfied by FakeCandidateSink
# ---------------------------------------------------------------------------


class TestCandidateSinkProtocol:
    def test_fake_sink_satisfies_protocol(self) -> None:
        sink = FakeCandidateSink()
        assert isinstance(sink, CandidateSink)

    def test_candidate_session_and_turn_id_propagated(self) -> None:
        config = SelfReviewConfig(enabled=True, shadow=True)
        sink = FakeCandidateSink()
        runner = FakeForkRunner(
            child_output=json.dumps(
                {"kind": "memory", "proposal": "A fact.", "confidence": 0.8}
            )
        )
        _run(
            run_self_review_hook(
                session_id="sess-propagate",
                turn_id="turn-propagate",
                system_prompt_blocks=_make_system_blocks(),
                parent_assistant_message=_make_assistant_message(),
                fork_runner=runner,
                candidate_sink=sink,
                config=config,
                now=_NOW,
            )
        )
        assert len(sink.received) == 1
        assert sink.received[0].session_id == "sess-propagate"
        assert sink.received[0].turn_id == "turn-propagate"
