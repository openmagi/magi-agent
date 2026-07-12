"""U1: PersonaUserSim now derives structured answers from slots.

Design 2026-07-12 §2.1 (follow-on #1) + Fix 2 (§2.3) + Fix 3 (DRY).

- PersonaUserSim.next_turn returns slot-derived ``answers`` for a flow-A
  scenario whose transcript's last entry carried ``questions[]``. The answers
  equal what ``DeterministicUserSim`` would produce for the same questions +
  slots (shared-mapping equivalence, by construction via ``_derive_answers``).
- The ``say`` still comes from the (scripted) persona LLM.
- ``answers={}`` when there are no pending questions (first turn).
- N2: a scripted LLM emitting a field value inside ``say`` does NOT change the
  structured answers (values come from slots).
- Fix 2: a scripted LLM returning empty/unparseable output yields a
  ``persona_llm_empty_say`` observation.
"""
from __future__ import annotations

from typing import Any

from benchmarks.authoring.fakes import ScriptedLlm
from benchmarks.authoring.usersim import (
    DeterministicUserSim,
    PersonaUserSim,
    UserTurn,
    _derive_answers,
)


def _scenario(slots: dict[str, Any], flow: str = "single_rule") -> Any:
    return type(
        "S",
        (),
        {
            "turns": [],
            "turn_budget": 8,
            "generated": {"slots": slots},
            "flow": flow,
            "language": "en",
        },
    )()


def _transcript_with_questions(question_ids: list[str]) -> list[dict[str, Any]]:
    return [
        {
            "turn": 0,
            "say": "hi",
            "answers": {},
            "response": {
                "assistant_message": "which tool?",
                "questions": [{"id": qid, "prompt": f"prompt {qid}"} for qid in question_ids],
            },
            "http_status": 200,
        }
    ]


_SLOTS_FLOW_A = {
    "kind": "tool_perm",
    "firesAt": "PreToolUse",
    "action": "deny",
    "scope": "always",
    "tool": "Bash",
}


# ---------------------------------------------------------------------------
# Change #1: persona derives slot answers, equivalent to DeterministicUserSim
# ---------------------------------------------------------------------------


def test_persona_derives_slot_answers_equivalent_to_deterministic() -> None:
    question_ids = ["q_what.kind", "q_firesAt", "q_action", "q_scope"]
    transcript = _transcript_with_questions(question_ids)

    persona_llm = ScriptedLlm(['{"say": "block the Bash tool"}'])
    sim = PersonaUserSim(persona="cooperative", scripted_llm=persona_llm.as_factory())
    scenario = _scenario(_SLOTS_FLOW_A)

    turn = sim.next_turn(scenario, transcript)
    assert isinstance(turn, UserTurn)
    # say comes from the scripted persona LLM.
    assert turn.say == "block the Bash tool"

    # answers equal the shared _derive_answers output for the same questions+slots.
    expected_answers, _ = _derive_answers(
        [{"id": qid} for qid in question_ids], "single_rule", _SLOTS_FLOW_A
    )
    assert turn.answers == expected_answers
    # non-empty and exactly the four slot values.
    assert turn.answers == {
        "q_what.kind": "tool_perm",
        "q_firesAt": "PreToolUse",
        "q_action": "deny",
        "q_scope": "always",
    }


def test_persona_first_turn_no_questions_yields_empty_answers() -> None:
    persona_llm = ScriptedLlm(['{"say": "I want to block Bash"}'])
    sim = PersonaUserSim(persona="cooperative", scripted_llm=persona_llm.as_factory())
    scenario = _scenario(_SLOTS_FLOW_A)

    turn = sim.next_turn(scenario, [])  # first turn: no transcript, no questions
    assert isinstance(turn, UserTurn)
    assert turn.say == "I want to block Bash"
    assert turn.answers == {}


def test_persona_llm_field_value_in_say_does_not_change_structured_answers() -> None:
    """N2: values come from slots, not from persona prose in ``say``."""
    question_ids = ["q_what.kind", "q_action"]
    transcript = _transcript_with_questions(question_ids)

    # The persona tries to emit a bogus field value inside say; answers stay slot-driven.
    persona_llm = ScriptedLlm(['{"say": "kind is shacl_constraint action allow"}'])
    sim = PersonaUserSim(persona="adversarial", scripted_llm=persona_llm.as_factory())
    scenario = _scenario(_SLOTS_FLOW_A)

    turn = sim.next_turn(scenario, transcript)
    assert isinstance(turn, UserTurn)
    assert turn.answers == {"q_what.kind": "tool_perm", "q_action": "deny"}


def test_persona_derives_flow_b_param_answers() -> None:
    question_ids = ["gatedTool", "onUnavailable"]
    transcript = _transcript_with_questions(question_ids)
    slots = {"gated_tool": "WebFetch", "on_unavailable": "block", "domain": "example.com"}

    persona_llm = ScriptedLlm(['{"say": "gate WebFetch on evidence"}'])
    sim = PersonaUserSim(persona="cooperative", scripted_llm=persona_llm.as_factory())
    scenario = _scenario(slots, flow="linked_policy")

    turn = sim.next_turn(scenario, transcript)
    expected, _ = _derive_answers(
        [{"id": qid} for qid in question_ids], "linked_policy", slots
    )
    assert turn.answers == expected
    assert turn.answers == {"gatedTool": "WebFetch", "onUnavailable": "block"}


# ---------------------------------------------------------------------------
# Fix 2 (P1): persona-LLM liveness observation
# ---------------------------------------------------------------------------


def test_persona_empty_say_emits_liveness_observation() -> None:
    persona_llm = ScriptedLlm(['{"say": ""}'])
    sim = PersonaUserSim(persona="cooperative", scripted_llm=persona_llm.as_factory())
    scenario = _scenario(_SLOTS_FLOW_A)

    turn = sim.next_turn(scenario, [])
    assert isinstance(turn, UserTurn)
    assert turn.say is None
    obs_types = [o.get("type") for o in turn.observations]
    assert "persona_llm_empty_say" in obs_types
    empty_obs = next(o for o in turn.observations if o.get("type") == "persona_llm_empty_say")
    assert empty_obs.get("persona") == "cooperative"


def test_persona_unparseable_output_emits_liveness_observation() -> None:
    persona_llm = ScriptedLlm(["this is not json at all"])
    sim = PersonaUserSim(persona="confused", scripted_llm=persona_llm.as_factory())
    scenario = _scenario(_SLOTS_FLOW_A)

    turn = sim.next_turn(scenario, [])
    assert turn.say is None
    assert any(o.get("type") == "persona_llm_empty_say" for o in turn.observations)


def test_persona_no_factory_emits_liveness_observation() -> None:
    """A dead persona LLM (no factory -> canned empty say) is flagged."""
    sim = PersonaUserSim(persona="cooperative", scripted_llm=None)
    scenario = _scenario(_SLOTS_FLOW_A)

    turn = sim.next_turn(scenario, [])
    assert turn.say is None
    assert any(o.get("type") == "persona_llm_empty_say" for o in turn.observations)


def test_persona_nonempty_say_has_no_liveness_observation() -> None:
    persona_llm = ScriptedLlm(['{"say": "block Bash"}'])
    sim = PersonaUserSim(persona="cooperative", scripted_llm=persona_llm.as_factory())
    scenario = _scenario(_SLOTS_FLOW_A)

    turn = sim.next_turn(scenario, [])
    assert turn.say == "block Bash"
    assert not any(o.get("type") == "persona_llm_empty_say" for o in turn.observations)


# ---------------------------------------------------------------------------
# Fix 3 (DRY): DeterministicUserSim behavior unchanged after extraction
# ---------------------------------------------------------------------------


def test_deterministic_sim_uses_shared_derive_answers() -> None:
    """DeterministicUserSim still produces the same slot answers (refactor is
    byte-identical to the shared helper)."""
    from benchmarks.authoring.scenario import Turn

    question_ids = ["q_what.kind", "q_firesAt", "q_action", "q_scope"]
    transcript = _transcript_with_questions(question_ids)
    scenario = type(
        "S",
        (),
        {
            "turns": [Turn(say="block Bash", answers_from_slots=True)],
            "turn_budget": 8,
            "generated": {"slots": _SLOTS_FLOW_A},
            "flow": "single_rule",
        },
    )()

    sim = DeterministicUserSim()
    turn = sim.next_turn(scenario, transcript)
    expected, _ = _derive_answers(
        [{"id": qid} for qid in question_ids], "single_rule", _SLOTS_FLOW_A
    )
    assert turn.answers == expected
