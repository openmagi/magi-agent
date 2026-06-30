"""Tests for runtime.empty_response_recovery — pure decision helpers (R2).

Hermes mechanism 3 port: never end a turn with nothing. Two behaviors share one
config/flag:

* empty-response recovery — tools ran this attempt but no text was emitted →
  one bounded corrective re-invocation;
* iteration-budget grace — the event budget was exhausted mid-turn → one grace
  re-invocation asking for the final answer.

These helpers are pure (no env, no model); ``config=None`` or
``enabled=False`` must make every decision ``False`` so the engine's control
flow stays byte-identical when the flag is OFF.
"""

from __future__ import annotations

from magi_agent.config.env import (
    EmptyResponseRecoveryEnv,
    parse_empty_response_recovery_env,
)
from magi_agent.runtime.empty_response_recovery import (
    EmptyResponseRecoveryConfig,
    build_blocked_notice,
    build_blocked_or_final_message,
    build_empty_response_message,
    build_grace_message,
    select_recovery_message,
    should_grace,
    should_recover_empty,
)


class TestConfigDefaults:
    def test_defaults_are_off_and_bounded(self) -> None:
        cfg = EmptyResponseRecoveryConfig()
        assert cfg.enabled is False
        assert cfg.max_recoveries == 1
        assert cfg.grace_event_allowance == 64


class TestShouldRecoverEmpty:
    cfg = EmptyResponseRecoveryConfig(enabled=True, max_recoveries=1)

    def test_fires_on_tools_ran_no_text(self) -> None:
        assert should_recover_empty(
            self.cfg, tool_ran=True, text_seen=False, recoveries_used=0
        )

    def test_disabled_config_is_false(self) -> None:
        off = EmptyResponseRecoveryConfig(enabled=False)
        assert not should_recover_empty(
            off, tool_ran=True, text_seen=False, recoveries_used=0
        )

    def test_none_config_is_false(self) -> None:
        assert not should_recover_empty(
            None, tool_ran=True, text_seen=False, recoveries_used=0
        )

    def test_text_seen_is_false(self) -> None:
        assert not should_recover_empty(
            self.cfg, tool_ran=True, text_seen=True, recoveries_used=0
        )

    def test_no_tools_ran_is_false(self) -> None:
        # A clean stop without tool activity is the model's normal "nothing to
        # add" — not the tools-ran-but-silent failure mode this targets.
        assert not should_recover_empty(
            self.cfg, tool_ran=False, text_seen=False, recoveries_used=0
        )

    def test_budget_exhausted_is_false(self) -> None:
        assert not should_recover_empty(
            self.cfg, tool_ran=True, text_seen=False, recoveries_used=1
        )

    def test_budget_over_is_false(self) -> None:
        assert not should_recover_empty(
            self.cfg, tool_ran=True, text_seen=False, recoveries_used=2
        )

    def test_higher_budget_allows_more(self) -> None:
        cfg = EmptyResponseRecoveryConfig(enabled=True, max_recoveries=2)
        assert should_recover_empty(
            cfg, tool_ran=True, text_seen=False, recoveries_used=1
        )
        assert not should_recover_empty(
            cfg, tool_ran=True, text_seen=False, recoveries_used=2
        )


class TestShouldGrace:
    cfg = EmptyResponseRecoveryConfig(enabled=True)

    def test_fires_on_budget_exhausted_no_text(self) -> None:
        assert should_grace(
            self.cfg, budget_exhausted=True, text_seen=False, graces_used=0
        )

    def test_disabled_config_is_false(self) -> None:
        off = EmptyResponseRecoveryConfig(enabled=False)
        assert not should_grace(
            off, budget_exhausted=True, text_seen=False, graces_used=0
        )

    def test_none_config_is_false(self) -> None:
        assert not should_grace(
            None, budget_exhausted=True, text_seen=False, graces_used=0
        )

    def test_requires_budget_exhausted(self) -> None:
        assert not should_grace(
            self.cfg, budget_exhausted=False, text_seen=False, graces_used=0
        )

    def test_text_seen_is_false(self) -> None:
        assert not should_grace(
            self.cfg, budget_exhausted=True, text_seen=True, graces_used=0
        )

    def test_only_one_grace_ever(self) -> None:
        assert not should_grace(
            self.cfg, budget_exhausted=True, text_seen=False, graces_used=1
        )


class TestMessages:
    def test_empty_response_message_exact(self) -> None:
        assert build_empty_response_message() == (
            "You just executed tool calls but your response contained no "
            "text. Process the tool results above and continue with your "
            "answer."
        )

    def test_grace_message_exact(self) -> None:
        assert build_grace_message() == (
            "You have reached the step budget for this turn. Produce your "
            "final answer now from what you already have; do not call more "
            "tools."
        )


class TestConfigEscalateDefault:
    def test_escalate_defaults_false(self) -> None:
        # The new PR5b ``escalate`` field defaults False so an OFF-escalation
        # config is exactly equal to the pre-PR5b dataclass.
        cfg = EmptyResponseRecoveryConfig(enabled=True, max_recoveries=1)
        assert cfg.escalate is False
        assert cfg == EmptyResponseRecoveryConfig(
            enabled=True,
            max_recoveries=1,
            grace_event_allowance=64,
            escalate=False,
        )


class TestEscalationMessages:
    def test_blocked_or_final_message_non_empty_and_distinct(self) -> None:
        msg = build_blocked_or_final_message()
        assert isinstance(msg, str) and msg
        assert msg != build_empty_response_message()
        assert msg != build_grace_message()

    def test_blocked_notice_non_empty_and_distinct(self) -> None:
        notice = build_blocked_notice()
        assert isinstance(notice, str) and notice
        assert notice != build_blocked_or_final_message()
        assert notice != build_empty_response_message()

    def test_blocked_notice_literal(self) -> None:
        # E14: freeze the exact non-answer wording so a reviewer diffs any
        # change and it cannot drift into looking like a fabricated answer.
        assert build_blocked_notice() == (
            "I was not able to produce a final answer for this turn after "
            "retrying. The tools ran but I could not synthesize a result. "
            "Please rephrase or narrow the request, or check the tool output "
            "above."
        )

    def test_blocked_or_final_message_literal(self) -> None:
        assert build_blocked_or_final_message() == (
            "Produce a final answer now from the tool results above. If you "
            "cannot, state explicitly and briefly what is blocking you. Do "
            "not call more tools."
        )


class TestSelectRecoveryMessage:
    def test_escalation_off_always_empty_response_message(self) -> None:
        assert (
            select_recovery_message(escalate=False, is_final=False)
            == build_empty_response_message()
        )
        assert (
            select_recovery_message(escalate=False, is_final=True)
            == build_empty_response_message()
        )

    def test_escalation_on_non_final_uses_empty_response_message(self) -> None:
        assert (
            select_recovery_message(escalate=True, is_final=False)
            == build_empty_response_message()
        )

    def test_escalation_on_final_uses_blocked_or_final_message(self) -> None:
        assert (
            select_recovery_message(escalate=True, is_final=True)
            == build_blocked_or_final_message()
        )


class TestEnvParsing:
    def test_default_off(self) -> None:
        parsed = parse_empty_response_recovery_env({})
        assert parsed == EmptyResponseRecoveryEnv(enabled=False, max_recoveries=1)

    def test_strict_truthy_on(self) -> None:
        for value in ("1", "true", "yes", "on", " TRUE "):
            parsed = parse_empty_response_recovery_env(
                {"MAGI_EMPTY_RESPONSE_RECOVERY_ENABLED": value}
            )
            assert parsed.enabled is True

    def test_non_truthy_stays_off(self) -> None:
        # Strict opt-in: junk values do NOT enable (unlike the runtime-profile
        # default-ON convention used by output-continuation).
        for value in ("0", "false", "off", "", "enable", "2"):
            parsed = parse_empty_response_recovery_env(
                {"MAGI_EMPTY_RESPONSE_RECOVERY_ENABLED": value}
            )
            assert parsed.enabled is False

    def test_runtime_profile_does_not_enable(self) -> None:
        # No profile-based default-ON: absent flag is OFF even in the full
        # local profile.
        parsed = parse_empty_response_recovery_env({"MAGI_RUNTIME_PROFILE": "full"})
        assert parsed.enabled is False

    def test_max_recoveries_knob(self) -> None:
        parsed = parse_empty_response_recovery_env(
            {
                "MAGI_EMPTY_RESPONSE_RECOVERY_ENABLED": "1",
                "MAGI_EMPTY_RESPONSE_MAX_RECOVERIES": "3",
            }
        )
        assert parsed.max_recoveries == 3

    def test_max_recoveries_must_be_positive(self) -> None:
        import pytest

        from magi_agent.config.env import RuntimeEnvError

        with pytest.raises(RuntimeEnvError):
            parse_empty_response_recovery_env(
                {"MAGI_EMPTY_RESPONSE_MAX_RECOVERIES": "0"}
            )


class TestEscalationEnvParsing:
    def test_escalation_default_off(self) -> None:
        parsed = parse_empty_response_recovery_env(
            {"MAGI_EMPTY_RESPONSE_RECOVERY_ENABLED": "1"}
        )
        assert parsed.escalate is False

    def test_escalation_off_max_one(self) -> None:
        # Regression: without escalation the default max stays 1.
        parsed = parse_empty_response_recovery_env(
            {"MAGI_EMPTY_RESPONSE_RECOVERY_ENABLED": "1"}
        )
        assert parsed.max_recoveries == 1

    def test_escalation_strict_truthy_on(self) -> None:
        for value in ("1", "true", "yes", "on", " TRUE "):
            parsed = parse_empty_response_recovery_env(
                {
                    "MAGI_EMPTY_RESPONSE_RECOVERY_ENABLED": "1",
                    "MAGI_EMPTY_RESPONSE_ESCALATION_ENABLED": value,
                }
            )
            assert parsed.escalate is True

    def test_escalation_default_max_two(self) -> None:
        # Escalation ON with no explicit max -> default becomes 2.
        parsed = parse_empty_response_recovery_env(
            {
                "MAGI_EMPTY_RESPONSE_RECOVERY_ENABLED": "1",
                "MAGI_EMPTY_RESPONSE_ESCALATION_ENABLED": "1",
            }
        )
        assert parsed.max_recoveries == 2

    def test_escalation_explicit_max_override_wins(self) -> None:
        # Operator override always wins (key present), including an explicit =1.
        for explicit, expected in (("1", 1), ("3", 3)):
            parsed = parse_empty_response_recovery_env(
                {
                    "MAGI_EMPTY_RESPONSE_RECOVERY_ENABLED": "1",
                    "MAGI_EMPTY_RESPONSE_ESCALATION_ENABLED": "1",
                    "MAGI_EMPTY_RESPONSE_MAX_RECOVERIES": explicit,
                }
            )
            assert parsed.max_recoveries == expected

    def test_escalation_non_truthy_stays_off(self) -> None:
        for value in ("0", "false", "off", "", "enable", "2"):
            parsed = parse_empty_response_recovery_env(
                {
                    "MAGI_EMPTY_RESPONSE_RECOVERY_ENABLED": "1",
                    "MAGI_EMPTY_RESPONSE_ESCALATION_ENABLED": value,
                }
            )
            assert parsed.escalate is False
            # And the max default falls back to 1 when escalation is OFF.
            assert parsed.max_recoveries == 1
