from magi_agent.config.env import (
    OutputContinuationEnv,
    parse_output_continuation_env,
)
from magi_agent.runtime.output_continuation import (
    OutputContinuationConfig,
    build_continuation_message,
    should_continue,
    stop_reason_is_truncated,
)

import pytest


class TestStopReasonIsTruncated:
    @pytest.mark.parametrize(
        "value",
        ["max_tokens", "length", "MAX_TOKENS", " Length ", "max_output_tokens"],
    )
    def test_truncated_markers(self, value: str) -> None:
        assert stop_reason_is_truncated(value) is True

    @pytest.mark.parametrize(
        "value", ["end_turn", "stop", "tool_use", "", None, 123, "completed"]
    )
    def test_non_truncated(self, value: object) -> None:
        assert stop_reason_is_truncated(value) is False


class TestShouldContinue:
    cfg = OutputContinuationConfig(enabled=True, max_continuations=3)

    def test_continues_when_truncated_with_output_and_budget(self) -> None:
        assert should_continue(
            self.cfg, truncated=True, output_seen=True, continuations_used=0
        )

    def test_no_continue_when_disabled(self) -> None:
        off = OutputContinuationConfig(enabled=False, max_continuations=3)
        assert not should_continue(
            off, truncated=True, output_seen=True, continuations_used=0
        )

    def test_no_continue_when_config_none(self) -> None:
        assert not should_continue(
            None, truncated=True, output_seen=True, continuations_used=0
        )

    def test_no_continue_when_not_truncated(self) -> None:
        assert not should_continue(
            self.cfg, truncated=False, output_seen=True, continuations_used=0
        )

    def test_no_continue_when_no_output(self) -> None:
        # A truncation with no emitted output is not a resumable deliverable.
        assert not should_continue(
            self.cfg, truncated=True, output_seen=False, continuations_used=0
        )

    def test_budget_exhausted_stops(self) -> None:
        assert not should_continue(
            self.cfg, truncated=True, output_seen=True, continuations_used=3
        )
        assert should_continue(
            self.cfg, truncated=True, output_seen=True, continuations_used=2
        )


def test_continuation_message_resumes_not_restarts() -> None:
    msg = build_continuation_message()
    assert "Continue" in msg
    assert "do not repeat" in msg.lower()


class TestParseOutputContinuationEnv:
    def test_default_enabled_outside_safe_profile(self) -> None:
        parsed = parse_output_continuation_env({})
        assert parsed == OutputContinuationEnv(enabled=True, max_continuations=4)

    def test_explicit_disable(self) -> None:
        parsed = parse_output_continuation_env(
            {"MAGI_OUTPUT_CONTINUATION_ENABLED": "0"}
        )
        assert parsed.enabled is False

    def test_safe_profile_disables(self) -> None:
        parsed = parse_output_continuation_env({"MAGI_RUNTIME_PROFILE": "safe"})
        assert parsed.enabled is False

    def test_custom_max(self) -> None:
        parsed = parse_output_continuation_env(
            {"MAGI_MAX_OUTPUT_CONTINUATIONS": "8"}
        )
        assert parsed.max_continuations == 8

    def test_invalid_max_raises(self) -> None:
        from magi_agent.config.env import RuntimeEnvError

        with pytest.raises(RuntimeEnvError):
            parse_output_continuation_env({"MAGI_MAX_OUTPUT_CONTINUATIONS": "0"})
