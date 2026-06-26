"""Unit tests for the conversational policy compiler state machine.

These tests drive ``step_compile`` directly (no HTTP layer) with
``model_factory=None`` so the fallback path is exercised
deterministically. The LLM-driven branches are pinned by the HTTP
e2e tests with an opt-in real-LLM run.
"""

from __future__ import annotations

import asyncio

import pytest

from magi_agent.customize.nl_compiler_interactive import (
    InteractiveInputError,
    MAX_ANSWERS,
    MAX_ANSWER_KEY_CHARS,
    MAX_HISTORY_TURNS,
    PrecheckError,
    Question,
    QuestionOption,
    _apply_answers_to_draft,
    _auto_fill_singletons,
    _canonical_questions,
    _coerce_llm_questions,
    _merge_updates,
    _missing_fields_for_draft,
    _parse_llm_envelope,
    _to_plain_language,
    step_compile,
)


# ---------------------------------------------------------------------------
# Plain-language scrubber
# ---------------------------------------------------------------------------


def test_scrubber_rewrites_internal_tokens() -> None:
    text = "Use a regex matcher to check the lifecycle kind and llm_critic"
    out = _to_plain_language(text)
    assert "regex" not in out.lower()
    assert "matcher" not in out.lower()
    assert "lifecycle" not in out.lower()
    assert "llm_critic" not in out.lower()
    assert "a pattern" in out
    assert "AI judge" in out


def test_scrubber_leaves_paths_alone() -> None:
    text = "/etc/kind-config/regex.conf has the matcher"
    out = _to_plain_language(text)
    # ``kind-config`` and ``regex.conf`` are not whole-word matches
    # for our internal tokens (``\b``-bounded) so they must stay intact.
    assert "/etc/kind-config" in out
    assert "regex.conf" in out


def test_scrubber_handles_empty() -> None:
    assert _to_plain_language("") == ""


# ---------------------------------------------------------------------------
# Missing-field detection
# ---------------------------------------------------------------------------


def test_missing_for_empty_draft_starts_at_kind() -> None:
    assert _missing_fields_for_draft({}) == ["what.kind"]


def test_missing_for_kind_only_lists_slot_and_action_and_scope_and_payload() -> None:
    missing = _missing_fields_for_draft({"what": {"kind": "tool_perm"}})
    # firesAt + action + scope + payload all missing (kind alone isn't enough)
    assert "firesAt" in missing
    assert "action" in missing
    assert "scope" in missing
    assert "what.payload" in missing


def test_missing_for_complete_tool_perm_draft_is_empty() -> None:
    draft = {
        "scope": "always",
        "firesAt": "before_tool_use",
        "action": "block",
        "what": {
            "kind": "tool_perm",
            "payload": {
                "match": {"tool": "shell_exec"},
                "decision": "deny",
            },
        },
    }
    assert _missing_fields_for_draft(draft) == []


def test_missing_with_illegal_slot_for_kind() -> None:
    # tool_perm only legal at before_tool_use; after_tool_use should
    # trip the firesAt-not-in-legal-set check.
    draft = {
        "scope": "always",
        "firesAt": "after_tool_use",
        "action": "block",
        "what": {"kind": "tool_perm", "payload": {"match": {"tool": "x"}, "decision": "deny"}},
    }
    assert "firesAt" in _missing_fields_for_draft(draft)


# ---------------------------------------------------------------------------
# Answer → draft merge
# ---------------------------------------------------------------------------


def test_apply_kind_answer_sets_what_kind() -> None:
    out = _apply_answers_to_draft({}, {"q_what.kind": "tool_perm"})
    assert out["what"]["kind"] == "tool_perm"


def test_apply_unknown_kind_silently_dropped() -> None:
    # A bogus kind must NOT corrupt the draft; the next turn re-asks
    # the canonical question.
    out = _apply_answers_to_draft({}, {"q_what.kind": "totally_made_up"})
    assert "what" not in out or out["what"].get("kind") is None


def test_apply_payload_hint_stays_in_buffer() -> None:
    out = _apply_answers_to_draft(
        {"what": {"kind": "shacl_constraint"}},
        {"q_what.payload": "@prefix sh: <http://www.w3.org/ns/shacl#> ."},
    )
    # Free-text payload goes into _payload_hint buffer; the LLM
    # consumes it next turn (we never blindly write user free-text
    # into what.payload because validators are strict).
    assert out.get("_payload_hint", "").startswith("@prefix sh:")
    assert "payload" not in out["what"]  # NOT auto-written


def test_apply_unknown_q_id_silently_dropped() -> None:
    out = _apply_answers_to_draft({}, {"q_definitely_unknown": "nope"})
    assert out == {}


# ---------------------------------------------------------------------------
# Auto-fill singletons
# ---------------------------------------------------------------------------


def test_auto_fill_single_slot_for_shacl_constraint() -> None:
    # shacl_constraint has ONE legal slot (pre_final) and ONE legal
    # action (block) — they MUST auto-fill so we don't waste a turn.
    draft = {"what": {"kind": "shacl_constraint"}}
    out = _auto_fill_singletons(draft)
    assert out["firesAt"] == "pre_final"
    assert out["action"] == "block"


def test_auto_fill_skips_when_multiple_legal_slots() -> None:
    # llm_criterion has many legal slots — auto-fill MUST NOT pick one.
    draft = {"what": {"kind": "llm_criterion"}}
    out = _auto_fill_singletons(draft)
    assert "firesAt" not in out


def test_auto_fill_no_op_when_kind_unset() -> None:
    assert _auto_fill_singletons({}) == {}


# ---------------------------------------------------------------------------
# Canonical question generation
# ---------------------------------------------------------------------------


def test_canonical_starts_with_kind_picker() -> None:
    qs = _canonical_questions({})
    assert qs and qs[0].id == "q_what.kind"
    assert qs[0].kind == "single_select"
    # Every supported kind shows up as an option.
    values = {o.value for o in (qs[0].options or ())}
    assert "tool_perm" in values
    assert "llm_criterion" in values
    assert "shacl_constraint" in values
    assert "capability_scope" in values


def test_canonical_max_questions_per_turn() -> None:
    qs = _canonical_questions({"what": {"kind": "llm_criterion"}})
    assert len(qs) <= 2


def test_canonical_payload_question_per_kind() -> None:
    for kind in ("tool_perm", "shacl_constraint", "shell_command", "capability_scope"):
        draft = _auto_fill_singletons({"what": {"kind": kind}, "scope": "always"})
        # Either action remained or auto-fill collapsed it; either way
        # the LAST missing item should be the payload, and the canonical
        # picker should produce a free-text question for it.
        qs = _canonical_questions(draft)
        payload_qs = [q for q in qs if q.id == "q_what.payload"]
        if payload_qs:
            assert payload_qs[0].kind == "text"


# ---------------------------------------------------------------------------
# LLM envelope parsing
# ---------------------------------------------------------------------------


def test_parse_envelope_strips_markdown_fences() -> None:
    raw = '```json\n{"assistant_message": "hi", "draft_updates": null, "questions": []}\n```'
    out = _parse_llm_envelope(raw)
    assert out and out["assistant_message"] == "hi"


def test_parse_envelope_recovers_from_trailing_garbage() -> None:
    raw = '{"assistant_message": "hi", "draft_updates": null, "questions": []}\n\nsorry for the noise'
    out = _parse_llm_envelope(raw)
    assert out and out["assistant_message"] == "hi"


def test_parse_envelope_returns_none_on_garbage() -> None:
    assert _parse_llm_envelope("not json at all") is None
    assert _parse_llm_envelope("") is None
    assert _parse_llm_envelope("```\n```") is None


def test_coerce_llm_questions_drops_off_topic_targets() -> None:
    missing = ["what.payload"]
    raw = [
        {
            "id": "q_what.kind",  # NOT in missing — should drop
            "prompt": "Pick a kind",
            "kind": "single_select",
            "targets_field": "what.kind",
            "options": [{"value": "tool_perm", "label": "Tool perm"}],
        },
        {
            "id": "q_what.payload",
            "prompt": "Paste the payload",
            "kind": "text",
            "targets_field": "what.payload",
        },
    ]
    out = _coerce_llm_questions(raw, missing)
    ids = [q.id for q in out]
    assert "q_what.kind" not in ids
    assert "q_what.payload" in ids


def test_coerce_llm_questions_caps_at_max() -> None:
    missing = ["what.kind"]
    raw = [
        {
            "id": "q_what.kind",
            "prompt": f"Q {i}",
            "kind": "single_select",
            "targets_field": "what.kind",
        }
        for i in range(5)
    ]
    out = _coerce_llm_questions(raw, missing)
    assert len(out) <= 2


# ---------------------------------------------------------------------------
# Merge semantics
# ---------------------------------------------------------------------------


def test_merge_updates_does_not_overwrite_operator_scope() -> None:
    draft = {"scope": "coding"}
    updates = {"scope": "always"}
    out = _merge_updates(draft, updates)
    assert out["scope"] == "coding"


def test_merge_updates_fills_empty_field() -> None:
    out = _merge_updates({}, {"scope": "always"})
    assert out["scope"] == "always"


def test_merge_updates_deep_merges_what() -> None:
    draft = {"what": {"kind": "tool_perm"}}
    updates = {"what": {"payload": {"match": {"tool": "shell_exec"}, "decision": "deny"}}}
    out = _merge_updates(draft, updates)
    assert out["what"]["kind"] == "tool_perm"
    assert out["what"]["payload"]["decision"] == "deny"


# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------


def test_validate_history_rejects_too_long() -> None:
    with pytest.raises(InteractiveInputError):
        asyncio.run(
            step_compile(
                history=[{"role": "user", "content": "x"} for _ in range(MAX_HISTORY_TURNS + 1)],
                draft_so_far=None,
                answers=None,
                model_factory=None,
            )
        )


def test_validate_history_rejects_bad_role() -> None:
    with pytest.raises(InteractiveInputError):
        asyncio.run(
            step_compile(
                history=[{"role": "system", "content": "x"}],
                draft_so_far=None,
                answers=None,
                model_factory=None,
            )
        )


def test_validate_answers_rejects_too_many() -> None:
    with pytest.raises(InteractiveInputError):
        asyncio.run(
            step_compile(
                history=[],
                draft_so_far=None,
                answers={f"q_{i}": "v" for i in range(MAX_ANSWERS + 1)},
                model_factory=None,
            )
        )


def test_validate_answers_rejects_long_key() -> None:
    with pytest.raises(InteractiveInputError):
        asyncio.run(
            step_compile(
                history=[],
                draft_so_far=None,
                answers={"q_" + "x" * MAX_ANSWER_KEY_CHARS: "v"},
                model_factory=None,
            )
        )


def test_precheck_rejects_aggregate_overrun() -> None:
    huge = "x" * 10_000
    with pytest.raises(PrecheckError):
        asyncio.run(
            step_compile(
                history=[{"role": "user", "content": huge} for _ in range(10)],
                draft_so_far=None,
                answers=None,
                model_factory=None,
            )
        )


# ---------------------------------------------------------------------------
# End-to-end state machine (no LLM)
# ---------------------------------------------------------------------------


def test_full_walk_no_llm_reaches_payload_step() -> None:
    """Operator picks kind→scope through canonical questions, ends at payload ask."""

    async def go() -> None:
        # Turn 1 — empty draft, asks for kind
        r1 = await step_compile(
            history=[], draft_so_far=None, answers=None, model_factory=None
        )
        assert r1["missing_fields"][0] == "what.kind"
        assert [q["id"] for q in r1["questions"]] == ["q_what.kind"]
        assert not r1["ready_to_save"]

        # Turn 2 — shacl_constraint chosen + scope=always
        r2 = await step_compile(
            history=[{"role": "user", "content": "hi"}],
            draft_so_far=r1["draft"],
            answers={"q_what.kind": "shacl_constraint", "q_scope": "always"},
            model_factory=None,
        )
        assert r2["draft"]["firesAt"] == "pre_final"
        assert r2["draft"]["action"] == "block"
        assert r2["missing_fields"] == ["what.payload"]
        assert [q["id"] for q in r2["questions"]] == ["q_what.payload"]
        assert not r2["ready_to_save"]

    asyncio.run(go())


def test_full_walk_without_llm_never_marks_ready_to_save() -> None:
    """No-LLM path can only get to the payload-question turn — payload
    free-text alone doesn't satisfy validate_custom_rule."""

    async def go() -> None:
        draft = {"what": {"kind": "shacl_constraint"}, "scope": "always"}
        r = await step_compile(
            history=[],
            draft_so_far=draft,
            answers={"q_what.payload": "@prefix sh: <http://www.w3.org/ns/shacl#> ."},
            model_factory=None,
        )
        assert r["ready_to_save"] is False

    asyncio.run(go())


def test_ready_to_save_flips_when_validator_passes() -> None:
    """A complete tool_perm draft posted with no further answers
    should validate and ready_to_save should become True."""

    async def go() -> None:
        complete = {
            "scope": "always",
            "firesAt": "before_tool_use",
            "action": "block",
            "what": {
                "kind": "tool_perm",
                "payload": {"match": {"tool": "shell_exec"}, "decision": "deny"},
            },
        }
        r = await step_compile(
            history=[],
            draft_so_far=complete,
            answers=None,
            model_factory=None,
        )
        assert r["ready_to_save"] is True
        assert r["missing_fields"] == []

    asyncio.run(go())


def test_scrubbed_assistant_message_on_wire() -> None:
    """No-LLM fallback narration must not leak internal tokens."""

    async def go() -> None:
        r = await step_compile(
            history=[],
            draft_so_far=None,
            answers=None,
            model_factory=None,
        )
        lowered = r["assistant_message"].lower()
        for forbidden in ("regex", "matcher", "lifecycle", "llm_critic", "shacl"):
            assert forbidden not in lowered
        for q in r["questions"]:
            lowered = q["prompt"].lower()
            for forbidden in ("regex", "matcher", "lifecycle", "llm_critic", "shacl"):
                assert forbidden not in lowered

    asyncio.run(go())
