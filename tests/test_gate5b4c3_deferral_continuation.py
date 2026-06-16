"""Deferral detection drives the run-until-done continuation.

When the model emits text with no tool call but announces a next action
("Next Concrete Action: I will fetch…") instead of performing it, the serve
loop must recognize the deferral and re-invoke (bounded) so multi-step tasks
run to completion. Genuine final answers and casual chat must NOT match.
"""

from magi_agent.shadow.gate5b4c3_live_runner_boundary import (
    _MAX_DEFERRAL_CONTINUATIONS,
    _looks_like_deferred_plan,
)


def test_max_deferral_continuations_is_bounded() -> None:
    assert 1 <= _MAX_DEFERRAL_CONTINUATIONS <= 12


def test_detects_english_next_action_deferral() -> None:
    assert _looks_like_deferred_plan(
        "## Next Concrete Action\nI will fetch the actual Tesla 10-K filing list "
        "to identify the most recent filing and then retrieve its content."
    )


def test_detects_remaining_work_and_refreshed_plan() -> None:
    assert _looks_like_deferred_plan("Refreshed Short Plan (Remaining Work Only)\n1. Fetch the actual 10-K")
    assert _looks_like_deferred_plan("Next step: search credible sources for analyst views.")


def test_detects_korean_deferral() -> None:
    assert _looks_like_deferred_plan("이제 SEC에서 10-K 데이터를 가져오겠습니다.")
    assert _looks_like_deferred_plan("다음 단계로 서브에이전트를 스폰합니다.")


def test_genuine_final_answer_does_not_match() -> None:
    assert not _looks_like_deferred_plan("248 × 17 = 4216.")
    assert not _looks_like_deferred_plan(
        "Here is the completed analysis. Let me know if you need anything else."
    )
    assert not _looks_like_deferred_plan("I'll be happy to help with that.")
    assert not _looks_like_deferred_plan("")
