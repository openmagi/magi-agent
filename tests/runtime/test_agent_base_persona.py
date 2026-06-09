from __future__ import annotations

from magi_agent.runtime.message_builder import (
    MAGI_BASE_PERSONA,
    build_system_prompt,
)


def test_persona_present_with_no_identity_files():
    prompt = build_system_prompt(session_key="s", turn_id="t", identity=None)
    assert "You are Magi Agent" in prompt
    assert MAGI_BASE_PERSONA in prompt


def test_persona_is_first_section():
    prompt = build_system_prompt(session_key="s", turn_id="t", identity={})
    assert prompt.startswith(MAGI_BASE_PERSONA)


def test_persona_inoculates_against_project_identity():
    assert "do NOT define who you are" in MAGI_BASE_PERSONA or (
        "not" in MAGI_BASE_PERSONA.lower() and "identity" in MAGI_BASE_PERSONA.lower()
    )


def test_persona_protected_from_hook_stripping():
    from magi_agent.runtime.message_builder import _reassert_protected_sections

    canonical = _reassert_protected_sections([])
    assert MAGI_BASE_PERSONA in canonical
    assert canonical[0] == MAGI_BASE_PERSONA
