"""LAB profile must auto-enable empty_response_recovery.

Kevin's 0.1.72+ dashboard shows the main agent running dozens of tool
calls then ending the turn with zero text — the frontend then renders
"작업은 진행됐지만 최종 답변 텍스트가 도착하지 않았습니다." (chat-store.ts:133).

The engine already has the corrective recovery (PR4 R2,
``MAGI_EMPTY_RESPONSE_RECOVERY_ENABLED``): one bounded re-invocation
saying "produce your final answer now". It is default-OFF by docstring
intent because the corrective message persists in session history —
fine for production, wrong default for lab/dogfood where the
alternative is the frontend fallback banner and a stuck user.

Add the flag to ``LAB_EXPERIMENTAL_FLAGS`` so opting into the lab
profile auto-enables it. Other profiles (safe / eval / minimal /
conservative / full) stay byte-identical.
"""
from __future__ import annotations

from magi_agent.runtime.local_defaults import LAB_EXPERIMENTAL_FLAGS


def test_lab_profile_includes_empty_response_recovery() -> None:
    assert "MAGI_EMPTY_RESPONSE_RECOVERY_ENABLED" in LAB_EXPERIMENTAL_FLAGS, (
        "Lab profile must auto-enable empty_response_recovery so the main "
        "agent retries once instead of silently ending the turn with no text."
    )


def test_lab_profile_includes_empty_response_escalation() -> None:
    # WS5 PR5b: lab opts into the bounded second attempt + honest blocked
    # notice so the dogfood surface exercises the escalation path.
    assert "MAGI_EMPTY_RESPONSE_ESCALATION_ENABLED" in LAB_EXPERIMENTAL_FLAGS, (
        "Lab profile must auto-enable empty_response_escalation so the main "
        "agent does a bounded second attempt and ends with an honest blocked "
        "notice instead of completing blank."
    )
