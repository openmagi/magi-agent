"""LAB profile must auto-enable empty_response_recovery.

Kevin's 0.1.72+ dashboard shows the main agent running dozens of tool
calls then ending the turn with zero text — the frontend then renders
"작업은 진행됐지만 최종 답변 텍스트가 도착하지 않았습니다." (chat-store.ts:133).

The engine already has the corrective recovery (PR4 R2,
``MAGI_EMPTY_RESPONSE_RECOVERY_ENABLED``): one bounded re-invocation
saying "produce your final answer now". The flag was originally in
``LAB_EXPERIMENTAL_FLAGS`` so opting into the lab profile auto-enabled
it. It has since been promoted to ``profile_bool`` (profile-aware
default-ON), so the lab auto-enablement now flows through the profile
resolver (ON under any non-safe profile, OFF under the safe-family).
Other profiles (safe / eval / minimal / conservative) stay OFF.
"""
from __future__ import annotations

from magi_agent.config.flags import flag_profile_bool
from magi_agent.runtime.local_defaults import apply_lab_runtime_defaults


def test_lab_profile_includes_empty_response_recovery() -> None:
    # The flag is a profile-aware default-ON (_pb / flag_profile_bool):
    # it resolves True under the lab profile via the profile resolver, so
    # the main agent retries once instead of silently ending blank.
    env: dict[str, str] = {}
    apply_lab_runtime_defaults(env)
    assert flag_profile_bool("MAGI_EMPTY_RESPONSE_RECOVERY_ENABLED", env=env) is True, (
        "Lab profile must auto-enable empty_response_recovery so the main "
        "agent retries once instead of silently ending the turn with no text."
    )


def test_lab_profile_includes_empty_response_escalation() -> None:
    # WS5 PR5b: lab opts into the bounded second attempt + honest blocked
    # notice so the dogfood surface exercises the escalation path. The flag
    # is a profile-aware default-ON (_pb / flag_profile_bool): it resolves
    # True under the lab profile via the profile resolver.
    env: dict[str, str] = {}
    apply_lab_runtime_defaults(env)
    assert flag_profile_bool("MAGI_EMPTY_RESPONSE_ESCALATION_ENABLED", env=env) is True, (
        "Lab profile must auto-enable empty_response_escalation so the main "
        "agent does a bounded second attempt and ends with an honest blocked "
        "notice instead of completing blank."
    )
