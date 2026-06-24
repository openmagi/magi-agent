"""F6.5 firing test: ``llm_criterion + contentMatch`` after-tool combo.

End-to-end-ish slice through the after-tool ingestion gate that proves the
deterministic ``contentMatch`` pre-filter sits *in front of* the (cost-bearing)
LLM critic call:

1. Persisted ``llm_criterion`` rule (firesAt=after_tool_use, action=override,
   enabled) carries both a non-empty ``criterion`` and a deterministic
   ``contentMatch`` payload (``pattern`` = ``AKIA[0-9A-Z]{16}``, ``isRegex``).
2. When the tool result text DOES match the pre-filter pattern, the critic
   is invoked exactly once.
3. When the tool result text does NOT match the pre-filter pattern, the
   critic is NOT invoked at all (zero calls) — the deterministic pre-filter
   short-circuits before the model spend.
4. Same persisted rule, identical wiring, only the tool result differs.

This is the user-side deterministic input-definition slot the F6.5 spec
exposes in the wizard: the regex gate is byte-stable / model-free, the
critic is the advisory verdict, and the combo composes them honestly.

Note on env flags. The after-tool gate cost-gates on
``MAGI_CUSTOMIZE_VERIFICATION_ENABLED`` + ``MAGI_CUSTOMIZE_CUSTOM_RULES_ENABLED``
(``flag_profile_bool`` in ``after_tool_gate._decide``). Both are set so the
firing path is not silently inert. The ``model_factory`` is set to a sentinel
so the criterion sub-mode is reachable (it is gated on a non-``None`` factory).
"""

from __future__ import annotations

import asyncio

import pytest

from magi_agent.customize.after_tool_gate import (
    CUSTOMIZE_AFTER_TOOL_BLOCK_TYPE,
    CustomizeAfterToolControl,
)
from magi_agent.customize.verification_policy import CustomizeVerificationPolicy

_RULE_ID = "cr_f6_5_llm_criterion_contentmatch_aws_key"
_CRITERION_TEXT = "the AWS key is a real (not example) production credential"
_PATTERN = r"AKIA[0-9A-Z]{16}"
_TOOL_NAME = "fetch_url"


class _Tool:
    def __init__(self, name: str) -> None:
        self.name = name


def _combo_rule() -> dict:
    """An after-tool ``llm_criterion`` rule with both a criterion and a
    deterministic ``contentMatch`` pre-filter — the exact wizard payload
    PR-F6.5 lets the operator author from the Guided step."""
    return {
        "id": _RULE_ID,
        "scope": "research",
        "enabled": True,
        "firesAt": "after_tool_use",
        "action": "override",
        "what": {
            "kind": "llm_criterion",
            "payload": {
                "toolMatch": [_TOOL_NAME],
                "criterion": _CRITERION_TEXT,
                "contentMatch": {
                    "pattern": _PATTERN,
                    "isRegex": True,
                },
            },
        },
    }


def _policy() -> CustomizeVerificationPolicy:
    return CustomizeVerificationPolicy(custom_rules=(_combo_rule(),))


def _run(ctrl: CustomizeAfterToolControl, *, result: str):
    return asyncio.run(
        ctrl.on_after_tool(
            tool=_Tool(_TOOL_NAME), args={}, tool_context=None, result=result
        )
    )


@pytest.fixture(autouse=True)
def _flags_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAGI_CUSTOMIZE_VERIFICATION_ENABLED", "1")
    monkeypatch.setenv("MAGI_CUSTOMIZE_CUSTOM_RULES_ENABLED", "1")


def test_critic_invoked_when_content_match_pattern_hits() -> None:
    """Pre-filter matches ⇒ critic invoked once + override emitted on fail.

    Locks the "gate passes → critic runs" half of the combo contract. The
    fake critic returns ``pass=false`` so the rule fires an override and we
    can also assert the runtime surfaced the rule id and reason verbatim.
    """
    calls: list[str] = []

    async def fake_invoke(_model, prompt: str) -> str:
        calls.append(prompt)
        return '{"pass": false, "reason": "real-looking AWS key"}'

    ctrl = CustomizeAfterToolControl(
        model_factory=lambda: object(),
        invoke=fake_invoke,
        policy_loader=_policy,
    )

    out = _run(ctrl, result="leaked credential AKIAIOSFODNN7EXAMPLE in body")

    assert out is not None
    assert out["response_type"] == CUSTOMIZE_AFTER_TOOL_BLOCK_TYPE
    assert out["rule_id"] == _RULE_ID
    assert out["reason"] == "real-looking AWS key"
    # Critic invoked exactly once — the pre-filter gated through, then the
    # advisory verdict produced the override.
    assert len(calls) == 1


def test_critic_not_invoked_when_content_match_pattern_misses() -> None:
    """Pre-filter does NOT match ⇒ critic is never called + no override.

    This is the deterministic short-circuit the combo is designed for: the
    operator pays zero model cost on results that cannot possibly be
    interesting. ``fake_invoke`` counts invocations and the assertion locks
    "zero" — a regression that bypassed the pre-filter (e.g. always-on
    critic) would flip this count to ``1``.
    """
    calls: list[str] = []

    async def fake_invoke(_model, prompt: str) -> str:
        # If the regression path runs the critic anyway, return a deliberate
        # pass so the test still surfaces "critic was invoked" via the count
        # (rather than via a stale block reason).
        calls.append(prompt)
        return '{"pass": true, "reason": "ok"}'

    ctrl = CustomizeAfterToolControl(
        model_factory=lambda: object(),
        invoke=fake_invoke,
        policy_loader=_policy,
    )

    # "harmless" carries no AKIA-shaped token, so the deterministic
    # pre-filter rejects the result before the critic is ever consulted.
    out = _run(ctrl, result="harmless plain text with no credentials")

    assert out is None
    assert calls == []


def test_pre_filter_only_runs_critic_on_matching_results() -> None:
    """Same persisted rule + same wiring; result content alone flips the
    critic-call count from 0 to 1.

    Belt-and-suspenders coverage: the two assertions above split the
    pre-filter contract across two tests; this one runs both sides through
    a single controller instance to lock the per-call dispatch (cached
    policy / loader state cannot silently leak between calls).
    """
    calls: list[str] = []

    async def fake_invoke(_model, prompt: str) -> str:
        calls.append(prompt)
        return '{"pass": true, "reason": "ok"}'

    ctrl = CustomizeAfterToolControl(
        model_factory=lambda: object(),
        invoke=fake_invoke,
        policy_loader=_policy,
    )

    # 1) Miss → 0 calls, no override.
    assert _run(ctrl, result="nothing to see here") is None
    assert len(calls) == 0

    # 2) Hit → exactly 1 call. (Critic returns pass=true so no override is
    #    emitted, but the per-call count proves dispatch happened.)
    assert _run(ctrl, result="AKIAIOSFODNN7EXAMPLE inside") is None
    assert len(calls) == 1
