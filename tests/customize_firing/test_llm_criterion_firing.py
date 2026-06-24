"""F1 firing test: prove an ``llm_criterion`` rule invokes the critic and applies its verdict.

End-to-end-ish slice through the pre-final gate the CLI engine consults
(``MagiEngineDriver._maybe_llm_criterion_block``) that proves three things
together:

1. Persisted ``llm_criterion`` rule (firesAt=pre_final, action=block, enabled)
   is loaded from ``customize.json`` and selected for evaluation.
2. The criterion judge is invoked exactly once and with the user-authored
   criterion text (the rule's ``payload.criterion``).
3. The judge's verdict is applied: ``pass=false`` yields a block reason
   string; ``pass=true`` yields ``None`` (rule does not fire) under the
   identical wiring.

Implementation note. The runtime gate calls
``magi_agent.customize.criterion_engine.evaluate_criterion`` per matching
rule; this test substitutes that callable via ``monkeypatch.setattr`` so no
live LLM call is made. The criterion text passed in is captured for the
"called once with criterion text" assertion.

Note on env flags. The F1 spec calls out ``MAGI_CUSTOMIZE_VERIFICATION_ENABLED``
+ ``MAGI_CUSTOMIZE_CUSTOM_RULES_ENABLED`` as the authoring/runtime gates; the
pre-final engine seam additionally cost-gates on ``MAGI_EGRESS_GATE_ENABLED``
(it owns the critic model factory). All three are set so the firing path is
not silently inert.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from magi_agent.cli.engine import MagiEngineDriver
from magi_agent.customize.store import set_custom_rule

_RULE_ID = "cr_f1_llm_criterion_cites_source"
_CRITERION_TEXT = "the answer cites at least one source"
_DRAFT_TEXT = "The market grew 40% last year according to internal estimates."


def _llm_criterion_rule() -> dict:
    """An ``llm_criterion`` rule mirroring the F1 spec shape."""
    return {
        "id": _RULE_ID,
        "scope": "coding",
        "enabled": True,
        "what": {
            "kind": "llm_criterion",
            "payload": {"criterion": _CRITERION_TEXT},
        },
        "firesAt": "pre_final",
        "action": "block",
    }


@pytest.fixture
def cfg(monkeypatch, tmp_path) -> Path:
    """Tmp ``customize.json`` + flags ON. Persists the F1 ``llm_criterion`` rule."""
    monkeypatch.setenv("MAGI_CUSTOMIZE_VERIFICATION_ENABLED", "1")
    monkeypatch.setenv("MAGI_CUSTOMIZE_CUSTOM_RULES_ENABLED", "1")
    # Pre-final engine seam additionally cost-gates on the egress gate flag
    # (owns the critic model factory); without it the gate short-circuits to
    # ``None`` regardless of rule presence.
    monkeypatch.setenv("MAGI_EGRESS_GATE_ENABLED", "1")
    cfile = tmp_path / "customize.json"
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(cfile))
    set_custom_rule(_llm_criterion_rule(), path=cfile)
    return cfile


def _drive(driver: MagiEngineDriver) -> str | None:
    return asyncio.run(driver._maybe_llm_criterion_block(final_text=_DRAFT_TEXT))


def test_llm_criterion_block_action_fires_when_critic_fails(
    cfg: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Critic returning ``pass=false`` ⇒ rule fires and engine surfaces block reason.

    Also asserts the critic was invoked exactly once and received the
    user-authored criterion text verbatim (and the draft text under judgment).
    """
    calls: list[dict] = []

    async def fake_eval(*, criterion, draft_text, model_factory):
        calls.append(
            {
                "criterion": criterion,
                "draft_text": draft_text,
                "model_factory": model_factory,
            }
        )
        return (False, "no sources cited")

    monkeypatch.setattr(
        "magi_agent.customize.criterion_engine.evaluate_criterion", fake_eval
    )

    driver = MagiEngineDriver(criterion_model_factory=lambda: object())
    reason = _drive(driver)

    # 1. Outcome: rule action=block + critic fail ⇒ block reason surfaced.
    assert reason == "no sources cited"
    # 2. Critic invoked exactly once.
    assert len(calls) == 1
    # 3. Critic called with the user-authored criterion text from the rule
    #    payload (proves the rule's payload reached the judge).
    assert calls[0]["criterion"] == _CRITERION_TEXT
    # 4. Draft under judgment is the engine's ``final_text``.
    assert calls[0]["draft_text"] == _DRAFT_TEXT


def test_llm_criterion_rule_does_not_fire_when_critic_passes(
    cfg: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Critic returning ``pass=true`` ⇒ identical wiring, rule does NOT fire.

    Same persisted rule, same flags, same driver — the only thing that
    differs from the failure test is the judge's verdict. This isolates the
    "apply the verdict" half of the firing contract: a positive verdict must
    yield ``None`` (no block) so a passing answer is never spuriously gated.
    """
    calls: list[dict] = []

    async def fake_eval(*, criterion, draft_text, model_factory):
        calls.append({"criterion": criterion, "draft_text": draft_text})
        return (True, "ok")

    monkeypatch.setattr(
        "magi_agent.customize.criterion_engine.evaluate_criterion", fake_eval
    )

    driver = MagiEngineDriver(criterion_model_factory=lambda: object())
    reason = _drive(driver)

    # Verdict pass ⇒ no block (no reason returned).
    assert reason is None
    # The judge was still consulted exactly once (rule was selected and
    # evaluated; it just didn't produce a block).
    assert len(calls) == 1
    assert calls[0]["criterion"] == _CRITERION_TEXT


def test_llm_criterion_inert_when_flags_off(monkeypatch, tmp_path) -> None:
    """Master flags OFF ⇒ rule does not fire AND the judge is NOT invoked.

    Locks the default-OFF byte-identical invariant: a future regression that
    bypasses MAGI_CUSTOMIZE_VERIFICATION_ENABLED or MAGI_EGRESS_GATE_ENABLED
    would spuriously call the critic (cost + behavior change). The fake judge
    raises if called so flag-blind regressions surface loudly.
    """
    monkeypatch.setenv("MAGI_CUSTOMIZE_VERIFICATION_ENABLED", "0")
    monkeypatch.setenv("MAGI_CUSTOMIZE_CUSTOM_RULES_ENABLED", "0")
    monkeypatch.setenv("MAGI_EGRESS_GATE_ENABLED", "0")
    cfile = tmp_path / "customize.json"
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(cfile))
    set_custom_rule(_llm_criterion_rule(), path=cfile)

    async def fail_eval(*, criterion, draft_text, model_factory):
        raise AssertionError(
            "judge must not be invoked when master flags are off"
        )

    monkeypatch.setattr(
        "magi_agent.customize.criterion_engine.evaluate_criterion", fail_eval
    )

    driver = MagiEngineDriver(criterion_model_factory=lambda: object())
    reason = _drive(driver)
    assert reason is None
