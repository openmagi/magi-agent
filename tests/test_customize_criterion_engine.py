from __future__ import annotations

import asyncio

from magi_agent.customize.criterion_engine import (
    _CRITERION_PROMPT,
    EvidenceCriterionRecord,
    EvidenceCriterionView,
    evaluate_criterion,
    parse_verdict,
)


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


# --- evidence-grounded judge (evidence-grounded-critic seam) ----------------


def _capture_invoke(seen: list[str]):
    async def invoke(_model, prompt):
        seen.append(prompt)
        return '{"pass": true, "reason": "ok"}'

    return invoke


def test_evaluate_evidence_context_none_is_byte_identical():
    # When no evidence view is supplied the prompt is byte-for-byte the
    # evidence-blind prompt (every existing criterion is unaffected).
    seen: list[str] = []
    asyncio.run(
        evaluate_criterion(
            criterion="all claims cited",
            draft_text="The market grew.",
            model_factory=lambda: object(),
            invoke=_capture_invoke(seen),
            evidence_context=None,
        )
    )
    assert len(seen) == 1
    assert seen[0] == _CRITERION_PROMPT.format(
        criterion="all claims cited", draft="The market grew."
    )
    assert "UNTRUSTED_EVIDENCE" not in seen[0]


def test_evaluate_with_evidence_renders_evidence_block():
    view = EvidenceCriterionView(
        records=(
            EvidenceCriterionRecord(
                type="TestRun", ref="evidence:test", fields={"exit_code": 0}
            ),
        ),
        absent_types=("GitDiff",),
    )
    seen: list[str] = []
    passed, _ = asyncio.run(
        evaluate_criterion(
            criterion="the change is covered by a passing test run",
            draft_text="done",
            model_factory=lambda: object(),
            invoke=_capture_invoke(seen),
            evidence_context=view,
        )
    )
    assert passed is True
    assert len(seen) == 1
    prompt = seen[0]
    assert "UNTRUSTED_EVIDENCE" in prompt
    assert "TestRun" in prompt
    assert "exit_code" in prompt
    # absent evidence the criterion asked for is surfaced so the judge can
    # reason about absence.
    assert "GitDiff" in prompt
    assert "absentTypes" in prompt


def test_evaluate_with_evidence_still_fail_open_on_invoke_error():
    async def boom(_model, _prompt):
        raise RuntimeError("model down")

    view = EvidenceCriterionView(
        records=(EvidenceCriterionRecord(type="GitDiff", ref="evidence:diff"),)
    )
    passed, _ = asyncio.run(
        evaluate_criterion(
            criterion="c",
            draft_text="d",
            model_factory=lambda: object(),
            invoke=boom,
            evidence_context=view,
        )
    )
    assert passed is True


def test_evaluate_evidence_projection_error_falls_back_blind():
    # A view whose render() raises must degrade to the evidence-blind prompt,
    # never wedge the turn.
    class Boom(EvidenceCriterionView):
        def render(self) -> str:  # type: ignore[override]
            raise RuntimeError("projection blew up")

    seen: list[str] = []
    passed, _ = asyncio.run(
        evaluate_criterion(
            criterion="x",
            draft_text="y",
            model_factory=lambda: object(),
            invoke=_capture_invoke(seen),
            evidence_context=Boom(),
        )
    )
    assert passed is True
    assert len(seen) == 1
    assert "UNTRUSTED_EVIDENCE" not in seen[0]
    assert seen[0] == _CRITERION_PROMPT.format(criterion="x", draft="y")


def test_evidence_view_render_is_bounded_and_deterministic():
    # More records than the cap → only the cap is rendered; output is stable
    # JSON (sorted keys) so the prompt is deterministic.
    records = tuple(
        EvidenceCriterionRecord(type=f"T{i}", ref=f"evidence:{i}")
        for i in range(50)
    )
    view = EvidenceCriterionView(records=records, absent_types=("Missing",))
    rendered = view.render()
    import json

    payload = json.loads(rendered)
    assert len(payload["records"]) == 20  # _MAX_EVIDENCE_RECORDS
    assert payload["absentTypes"] == ["Missing"]
    assert view.render() == rendered  # deterministic
