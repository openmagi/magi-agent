"""U2: tier-aware final + persisted oracle (design 2026-07-12 §2.2, Fix 1/5).

For ``tier == "t3"`` ONLY the runner relaxes exactly two canned-utterance-coupled
checks — the ``intent_mismatch`` string leg of ``policy_intent_is_first_utterance``
and the ``max_turns_to_ready`` cap. Every structural check stays HARD, per-turn
invariants stay HARD, and ANY other/omitted tier value behaves STRICTLY (Fix 5).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from benchmarks.authoring.runner import run_scenario
from benchmarks.authoring.scenario import load_scenario
from benchmarks.authoring.usersim import Stop, UserTurn

_CORPUS_V1 = (
    Path(__file__).resolve().parents[2] / "benchmarks" / "authoring" / "corpus" / "v1"
)
_HANDWRITTEN = _CORPUS_V1 / "handwritten"


def _runtime():
    from magi_agent.config.models import BuildInfo, RuntimeConfig
    from magi_agent.runtime.openmagi_runtime import OpenMagiRuntime

    return OpenMagiRuntime(
        config=RuntimeConfig(
            bot_id="local-bot",
            user_id="local-user",
            gateway_token="test-gateway-token",
            api_proxy_url="http://api-proxy.local",
            chat_proxy_url="http://chat-proxy.local",
            redis_url="redis://redis.local:6379/0",
            model="gpt-5.2",
            build=BuildInfo(version="0.1.0", build_sha="sha-test"),
        )
    )


def _isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(tmp_path / "c.json"))
    monkeypatch.setenv("MAGI_CUSTOMIZE_NL_INTERACTIVE_ENABLED", "1")
    monkeypatch.setattr(
        "magi_agent.packs.discovery.default_search_bases", lambda: [tmp_path]
    )


def _happy():
    return load_scenario(_HANDWRITTEN / "rule_happy_toolperm_en_001.yaml")


class _SlotAnswerSim:
    """Drives the happy scenario to ready by supplying the canonical slot answers
    on each turn, but with a DIFFERENT free-text first utterance than the canned
    ``scenario.turns[0].say``. Mirrors what PersonaUserSim now does.

    ``pace`` inserts a filler turn before the answering turns so the scenario
    can reach ready one turn OVER the canned cap.
    """

    def __init__(self, first_say: str, *, pace: int = 0) -> None:
        self._first_say = first_say
        self._pace = pace
        # Turn 0 supplies q_what.kind alongside the first say; the remaining
        # answers arrive on subsequent (non-filler) turns.
        self._answer_scripts = [
            {"q_action": "block", "q_scope": "always"},
        ]

    def next_turn(self, scenario, transcript):
        idx = len(transcript)
        if idx == 0:
            return UserTurn(say=self._first_say, answers={"q_what.kind": "tool_perm"})
        # Optional filler turns to slow the pace (still making no progress).
        if self._pace and idx <= self._pace:
            return UserTurn(say="hmm, let me think", answers={})
        script_i = idx - 1 - self._pace
        if script_i < 0 or script_i >= len(self._answer_scripts):
            return Stop()
        return UserTurn(say=None, answers=self._answer_scripts[script_i])


def test_persona_first_utterance_passes_t3_fails_t2(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # t2: intent_mismatch is HARD -> different first say fails the persisted oracle.
    _isolated(tmp_path, monkeypatch)
    r_t2 = run_scenario(
        _happy(), _runtime(), token="test-gateway-token", tier="t2",
        user_sim=_SlotAnswerSim("please stop Bash from running"),
    )
    assert r_t2.passed is False
    assert r_t2.first_divergence is not None
    assert r_t2.first_divergence.get("oracle") == "intent_mismatch"


def test_persona_first_utterance_passes_t3(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolated(tmp_path, monkeypatch)
    r_t3 = run_scenario(
        _happy(), _runtime(), token="test-gateway-token", tier="t3",
        user_sim=_SlotAnswerSim("please stop Bash from running"),
    )
    assert r_t3.passed is True, r_t3.first_divergence


def test_one_turn_over_cap_passes_t3_fails_t2(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Reach ready one turn OVER the canned max_turns_to_ready=2 cap.
    _isolated(tmp_path, monkeypatch)
    r_t2 = run_scenario(
        _happy(), _runtime(), token="test-gateway-token", tier="t2",
        user_sim=_SlotAnswerSim("block the Bash tool", pace=1),
    )
    assert r_t2.passed is False
    assert r_t2.first_divergence.get("oracle") == "max_turns_to_ready"

    r_t3 = run_scenario(
        _happy(), _runtime(), token="test-gateway-token", tier="t3",
        user_sim=_SlotAnswerSim("block the Bash tool", pace=1),
    )
    assert r_t3.passed is True, r_t3.first_divergence


def test_structural_persisted_violation_still_fails_t3(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """intent_ref_count (an envelope save whose policy references the wrong rule)
    is a structural persisted check and STILL fails under t3."""
    from benchmarks.authoring.oracles import persisted as P

    _isolated(tmp_path, monkeypatch)

    # Force assert_policy_intent's ref-count leg to fail by poisoning the ref id.
    real = P.assert_policy_intent

    def _wrong_ref(snap, rule_id, expected_intent, expected_display=None, **kw):
        return real(snap, "no-such-rule-id", expected_intent, expected_display, **kw)

    monkeypatch.setattr(P, "assert_policy_intent", _wrong_ref)

    r_t3 = run_scenario(
        _happy(), _runtime(), token="test-gateway-token", tier="t3",
        user_sim=_SlotAnswerSim("please stop Bash from running"),
    )
    assert r_t3.passed is False
    assert r_t3.first_divergence.get("oracle") == "intent_ref_count"


def test_per_turn_i5_leak_still_fails_t3(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A per-turn invariant (I5 vocabulary leak) short-circuits before the final
    oracle and is HARD in every tier including t3."""
    _isolated(tmp_path, monkeypatch)

    from benchmarks.authoring import runner as runner_mod
    from benchmarks.authoring.invariants import InvariantViolation

    real_check = runner_mod.check_invariants

    def _leaky(result, *, flow, answers, turn_index):
        v = real_check(result, flow=flow, answers=answers, turn_index=turn_index)
        if v:
            return v
        # Inject a synthetic I5 leak on the first turn.
        if turn_index == 0:
            return [
                InvariantViolation(
                    invariant_id="I5",
                    turn_index=turn_index,
                    evidence="leaked internal vocab",
                )
            ]
        return []

    monkeypatch.setattr(runner_mod, "check_invariants", _leaky)

    r_t3 = run_scenario(
        _happy(), _runtime(), token="test-gateway-token", tier="t3",
        user_sim=_SlotAnswerSim("block the Bash tool"),
    )
    assert r_t3.passed is False
    assert r_t3.first_divergence.get("invariant") == "I5"


def test_tier_omitted_or_unknown_behaves_strictly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fix 5: only exact tier=='t3' relaxes. Omitted default (t1) and an unknown
    tier string keep intent_mismatch + max_turns_to_ready HARD."""
    _isolated(tmp_path, monkeypatch)

    # Omitted tier -> default 't1' -> strict intent_mismatch.
    r_default = run_scenario(
        _happy(), _runtime(), token="test-gateway-token",
        user_sim=_SlotAnswerSim("please stop Bash from running"),
    )
    assert r_default.passed is False
    assert r_default.first_divergence.get("oracle") == "intent_mismatch"

    # Unknown tier string -> strict (no relaxation).
    r_unknown = run_scenario(
        _happy(), _runtime(), token="test-gateway-token", tier="t9-bogus",
        user_sim=_SlotAnswerSim("please stop Bash from running"),
    )
    assert r_unknown.passed is False
    assert r_unknown.first_divergence.get("oracle") == "intent_mismatch"
