from __future__ import annotations

import asyncio

from magi_agent.customize.criterion_engine import evaluate_criterion, parse_verdict


def test_parse_verdict_pass_and_fail():
    assert parse_verdict('{"pass": true, "reason": "ok"}') == (True, "ok")
    assert parse_verdict('{"pass": false, "reason": "missing citations"}') == (False, "missing citations")


def test_parse_verdict_tolerates_fences_and_extra():
    assert parse_verdict('```json\n{"pass": false, "reason": "x"}\n```')[0] is False


def test_parse_verdict_invalid_returns_none():
    assert parse_verdict("not json") is None
    assert parse_verdict('{"reason": "no pass key"}') is None


def test_evaluate_fail_open_when_no_model():
    # No model factory → cannot judge → fail-open (passed=True), never blocks.
    passed, _ = asyncio.run(
        evaluate_criterion(criterion="all claims cited", draft_text="x", model_factory=None)
    )
    assert passed is True


def test_evaluate_uses_injected_invoke_for_block():
    async def fake_invoke(_model, _prompt):
        return '{"pass": false, "reason": "uncited claim"}'

    passed, reason = asyncio.run(
        evaluate_criterion(
            criterion="all claims cited",
            draft_text="The market grew 40%.",
            model_factory=lambda: object(),
            invoke=fake_invoke,
        )
    )
    assert passed is False
    assert reason == "uncited claim"


def test_evaluate_fail_open_on_invoke_error():
    async def boom(_model, _prompt):
        raise RuntimeError("model down")

    passed, _ = asyncio.run(
        evaluate_criterion(
            criterion="c", draft_text="d", model_factory=lambda: object(), invoke=boom
        )
    )
    assert passed is True  # error → never block
