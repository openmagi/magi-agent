"""PR-V5 -- verify-before-replying skeptic member (advisory, default-OFF).

Six RED-first tests that gate the GREEN implementation:
1. evaluate_criterion_findings parses structured LLM output into a tuple of dicts.
2. The skeptic prompt contains the fixed first-party criterion and demands verbatim spans.
3. A2 post-filter (filter_skeptic_findings) drops non-verbatim and sub-minimum spans.
4. When MAGI_VERIFY_BEFORE_REPLYING_SKEPTIC_ENABLED is falsy, _build_critic_factory
   is never invoked (D3 binding).
5. When all skeptic findings are filtered out, audit_candidate receives skeptic_findings=().
6. Skeptic findings carry confidence="advisory" and never count toward high_count.

Style: no em-dashes (period/comma/colon/parens only).
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from typing import Any

import pytest

# These imports anchor the RED failures: none of the names below exist in
# criterion_engine until GREEN is committed.
from magi_agent.customize.criterion_engine import (
    _SKEPTIC_CRITERION,
    _SKEPTIC_SYSTEM_INSTRUCTION,
    CriterionFindings,
    evaluate_criterion_findings,
    project_evidence_for_criterion,
)
from magi_agent.evidence.verify_audit import (
    VerifyFinding,
    audit_candidate,
    filter_skeptic_findings,
    fingerprint_finding,
)


# ---------------------------------------------------------------------------
# Fake ADK helpers (mirroring test_criterion_engine_structured.py)
# ---------------------------------------------------------------------------


class _FakePart:
    def __init__(self, text: str) -> None:
        self.text = text


class _FakeContent:
    def __init__(self, text: str) -> None:
        self.parts = [_FakePart(text)]


class _FakeLlmResponse:
    def __init__(self, text: str) -> None:
        self.content = _FakeContent(text)


def _scripted_llm(json_text: str, captured: list[Any]):
    """Fake LLM that yields a single scripted JSON response."""

    class _FakeLlm:
        model = "fake-skeptic-model"

        async def generate_content_async(
            self, llm_request: Any, stream: bool = False
        ) -> AsyncGenerator:
            captured.append(llm_request)
            yield _FakeLlmResponse(json_text)

    return _FakeLlm()


def _factory(llm: Any):
    """Wrap an LLM object in a zero-argument factory callable."""
    return lambda: llm


# ---------------------------------------------------------------------------
# Test 1: evaluate_criterion_findings parses structured LLM output
# ---------------------------------------------------------------------------


def test_evaluate_criterion_findings_parses_structured_output() -> None:
    """A well-formed LLM findings response is parsed into a tuple of dicts."""
    captured: list[Any] = []
    llm_json = (
        '{"findings": [{"span": "This is definitely the best possible answer",'
        ' "concern": "unsupported certainty", "note": "no evidence given"}]}'
    )
    model = _scripted_llm(llm_json, captured)

    async def _fake_invoke(m: Any, p: str) -> str:
        # Simulate _default_invoke_findings returning the raw JSON text.
        captured.append(("invoke", p))
        return llm_json

    result = asyncio.run(
        evaluate_criterion_findings(
            criterion=_SKEPTIC_CRITERION,
            draft_text="This is definitely the best possible answer.",
            model_factory=_factory(model),
            invoke=_fake_invoke,
        )
    )
    assert isinstance(result, tuple), f"expected tuple, got {type(result)}"
    assert len(result) == 1
    item = result[0]
    assert isinstance(item, dict), f"expected dict item, got {type(item)}"
    assert "span" in item
    assert "concern" in item
    assert item["concern"] == "unsupported certainty"


# ---------------------------------------------------------------------------
# Test 2: skeptic prompt demands verbatim spans
# ---------------------------------------------------------------------------


def test_skeptic_prompt_demands_verbatim_spans() -> None:
    """The rendered prompt contains the fixed first-party criterion text and
    demands verbatim spans from the model."""
    captured: list[Any] = []
    fallback_json = '{"findings": []}'

    async def _capture_invoke(m: Any, prompt: str) -> str:
        captured.append(prompt)
        return fallback_json

    asyncio.run(
        evaluate_criterion_findings(
            criterion=_SKEPTIC_CRITERION,
            draft_text="The model is perfect and will solve all your problems.",
            model_factory=_factory(object()),
            invoke=_capture_invoke,
        )
    )
    assert captured, "invoke was never called"
    prompt = captured[0]
    assert isinstance(prompt, str), f"prompt must be str, got {type(prompt)}"
    # The fixed criterion keywords must appear in the rendered prompt.
    lower = prompt.lower()
    assert "overconfidence" in lower or "flattery" in lower or "certainty" in lower, (
        f"criterion keywords missing from prompt: {prompt[:300]}"
    )
    # The prompt or the system instruction must reference verbatim spans.
    # We check both: the criterion text itself and the system instruction.
    verbatim_in_prompt = "verbatim" in lower
    verbatim_in_instruction = "verbatim" in _SKEPTIC_SYSTEM_INSTRUCTION.lower()
    assert verbatim_in_prompt or verbatim_in_instruction, (
        "Neither the prompt nor _SKEPTIC_SYSTEM_INSTRUCTION demands verbatim spans"
    )


# ---------------------------------------------------------------------------
# Test 3: A2 post-filter drops non-verbatim and sub-minimum spans
# ---------------------------------------------------------------------------


def test_post_filter_drops_nonverbatim_and_short_spans() -> None:
    """filter_skeptic_findings drops findings whose claim_text is not verbatim
    in the candidate text OR is shorter than the A2 minimum (15 chars / 3 words).
    Findings that pass both checks are kept.
    """
    candidate = "This is definitely the best possible approach for your use case."

    def _make_finding(claim_text: str, idx: int = 0) -> VerifyFinding:
        return VerifyFinding(
            finding_id=fingerprint_finding(
                "verify_before_replying.skeptic_review",
                "skeptic_overconfidence",
                canonical_value=claim_text,
            ),
            rule_id="verify_before_replying.skeptic_review",
            confidence="advisory",
            claim_class="skeptic_overconfidence",
            claim_text=claim_text,
            span=(0, len(claim_text)),
            evidence_refs=(),
            expected=None,
            observed=None,
            detail="overconfidence detected",
            suggested_action="consider",
        )

    # Should be KEPT: verbatim substring, meets minimum.
    good = _make_finding("definitely the best possible approach")
    # Should be DROPPED: not verbatim in candidate.
    nonverbatim = _make_finding("absolutely unequivocally the finest solution")
    # Should be DROPPED: verbatim but too short (under 15 chars or fewer than 3 words).
    too_short = _make_finding("best")

    # evaluate_criterion_findings is called first (in a real turn) to produce
    # the raw dicts; here we test the post-filter step directly.
    kept, dropped = filter_skeptic_findings([good, nonverbatim, too_short], candidate)
    assert len(kept) == 1, f"expected 1 kept, got {len(kept)}: {kept}"
    assert kept[0].claim_text == good.claim_text
    assert dropped == 2


# ---------------------------------------------------------------------------
# Test 4: flag OFF never builds critic
# ---------------------------------------------------------------------------


def test_skeptic_flag_off_never_builds_critic(monkeypatch) -> None:
    """When MAGI_VERIFY_BEFORE_REPLYING_SKEPTIC_ENABLED is falsy,
    _build_critic_factory is NEVER invoked (D3 binding).

    We test the criterion_engine boundary: evaluate_criterion_findings with
    model_factory=None (the value the driver passes when the flag is OFF)
    returns () immediately without ever constructing a model.
    """
    sentinel_calls: list[str] = []

    def _sentinel_factory() -> Any:
        sentinel_calls.append("called")
        raise AssertionError("_build_critic_factory must not be called when flag is OFF")

    monkeypatch.setenv("MAGI_VERIFY_BEFORE_REPLYING_SKEPTIC_ENABLED", "0")
    # Patch the factory function that the driver would call when flag is ON.
    monkeypatch.setattr(
        "magi_agent.adk_bridge.lifecycle_llm_call_control._build_critic_factory",
        _sentinel_factory,
    )

    # When the flag is OFF, the driver passes model_factory=None to
    # evaluate_criterion_findings. The function must return () without
    # touching the factory at all.
    result = asyncio.run(
        evaluate_criterion_findings(
            criterion=_SKEPTIC_CRITERION,
            draft_text="This will definitely work perfectly.",
            model_factory=None,
        )
    )
    assert result == (), f"expected empty tuple, got {result}"
    assert not sentinel_calls, f"_build_critic_factory was called: {sentinel_calls}"

    # Also verify the env parse returns falsy for "0".
    from magi_agent.config.env import parse_verify_before_replying_skeptic_enabled
    import os

    assert not parse_verify_before_replying_skeptic_enabled(os.environ)


# ---------------------------------------------------------------------------
# Test 5: zero surviving findings injects nothing into audit_candidate
# ---------------------------------------------------------------------------


def test_skeptic_zero_surviving_findings_injects_nothing() -> None:
    """When evaluate_criterion_findings + filter_skeptic_findings together produce
    zero surviving findings, audit_candidate receives skeptic_findings=() and
    the result has no skeptic-sourced entries in findings.
    """
    # Simulate: LLM returned a finding but its span is not verbatim.
    candidate = "Paris is the capital of France."
    nonverbatim_claim = "absolutely unequivocally the finest city on Earth"

    def _make_finding(claim_text: str) -> VerifyFinding:
        return VerifyFinding(
            finding_id=fingerprint_finding(
                "verify_before_replying.skeptic_review",
                "skeptic_overconfidence",
                canonical_value=claim_text,
            ),
            rule_id="verify_before_replying.skeptic_review",
            confidence="advisory",
            claim_class="skeptic_overconfidence",
            claim_text=claim_text,
            span=(0, len(claim_text)),
            evidence_refs=(),
            expected=None,
            observed=None,
            detail="claim not supported",
            suggested_action="consider",
        )

    raw = (_make_finding(nonverbatim_claim),)
    kept, dropped = filter_skeptic_findings(raw, candidate)
    assert len(kept) == 0, "expected no surviving findings"

    # audit_candidate is called with the empty tuple that survived post-filter.
    result = audit_candidate(
        final_text=candidate,
        prompt="What is the capital of France?",
        turn_records=[],
        session_records=[],
        gate_result=None,
        collector_present=False,
        surfaced_fingerprints=set(),
        skeptic_findings=kept,
        skeptic_ran=True,
        skeptic_dropped=dropped,
    )
    skeptic_rule = "verify_before_replying.skeptic_review"
    skeptic_sourced = [f for f in result.findings if f.rule_id == skeptic_rule]
    assert skeptic_sourced == [], (
        f"expected no skeptic findings in result, got {skeptic_sourced}"
    )


# ---------------------------------------------------------------------------
# Test 6: skeptic never upgrades confidence to "high"
# ---------------------------------------------------------------------------


def test_skeptic_never_upgrades_confidence() -> None:
    """VerifyFinding rows emitted by the skeptic carry confidence="advisory".
    audit_candidate must never count them in high_count.
    """
    claim = "This is definitely the absolute best solution"
    candidate = f"{claim} for your requirements."

    skeptic_finding = VerifyFinding(
        finding_id=fingerprint_finding(
            "verify_before_replying.skeptic_review",
            "skeptic_overconfidence",
            canonical_value=claim,
        ),
        rule_id="verify_before_replying.skeptic_review",
        confidence="advisory",
        claim_class="skeptic_overconfidence",
        claim_text=claim,
        span=(0, len(claim)),
        evidence_refs=(),
        expected=None,
        observed=None,
        detail="overconfidence detected",
        suggested_action="consider",
    )

    result = audit_candidate(
        final_text=candidate,
        prompt="What is the best solution?",
        turn_records=[],
        session_records=[],
        gate_result=None,
        collector_present=False,
        surfaced_fingerprints=set(),
        skeptic_findings=(skeptic_finding,),
        skeptic_ran=True,
        skeptic_dropped=0,
    )
    # The finding must appear in findings...
    assert any(
        f.rule_id == "verify_before_replying.skeptic_review" for f in result.findings
    ), "skeptic finding missing from result.findings"
    # ...but must NEVER count toward high_count.
    assert result.high_count == 0, (
        f"skeptic advisory finding must not increment high_count; "
        f"got high_count={result.high_count}"
    )
    # It must count toward advisory_count instead.
    assert result.advisory_count >= 1, (
        f"expected advisory_count >= 1, got {result.advisory_count}"
    )
