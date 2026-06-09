from __future__ import annotations

import pytest

from magi_agent.runtime.message_builder import (
    MAGI_BASE_PERSONA,
    build_system_prompt,
    build_system_prompt_blocks,
)


@pytest.mark.parametrize("identity", [None, {}])
def test_persona_present_with_no_identity_files(identity):
    prompt = build_system_prompt(session_key="s", turn_id="t", identity=identity)
    assert "You are Magi Agent" in prompt
    assert MAGI_BASE_PERSONA in prompt


@pytest.mark.parametrize("identity", [None, {}])
def test_persona_is_first_section(identity):
    prompt = build_system_prompt(session_key="s", turn_id="t", identity=identity)
    assert prompt.startswith(MAGI_BASE_PERSONA)


def test_persona_precedes_soul_when_present():
    prompt = build_system_prompt(
        session_key="s",
        turn_id="t",
        identity={"soul": "I am the agent's own soul."},
    )
    assert MAGI_BASE_PERSONA in prompt
    assert "# SOUL" in prompt
    assert prompt.index(MAGI_BASE_PERSONA) < prompt.index("# SOUL")


def test_persona_inoculates_against_project_identity():
    assert "describe the PROJECT you are working on" in MAGI_BASE_PERSONA
    assert "do NOT define who you are" in MAGI_BASE_PERSONA


def test_persona_protected_from_hook_stripping():
    from magi_agent.runtime.message_builder import _reassert_protected_sections

    canonical = _reassert_protected_sections([])
    assert MAGI_BASE_PERSONA in canonical
    assert canonical[0] == MAGI_BASE_PERSONA


def test_persona_present_in_cached_blocks_path():
    # build_system_prompt_blocks returns a list[dict[str, object]] of
    # {"type": "text", "text": ...} blocks; with cache_enabled the prompt is
    # split into multiple such blocks. Join their text and assert persona +
    # project context survive the split/cache-marker injection.
    blocks = build_system_prompt_blocks(
        session_key="s",
        turn_id="t",
        identity={"project_context": "## CLAUDE.md\n\nAcme TypeScript app"},
        coding_agent=True,
        cache_enabled=True,
    )
    joined = "\n\n".join(str(block["text"]) for block in blocks)
    assert MAGI_BASE_PERSONA in joined
    assert "# PROJECT CONTEXT" in joined
    # Project context is rendered under PROJECT CONTEXT, not as an IDENTITY
    # section — the project's files must not become self-identity.
    assert "# IDENTITY" not in joined


def test_persona_reasserted_on_real_hook_path(monkeypatch: pytest.MonkeyPatch):
    # Exercise the REAL beforeSystemPrompt hook path (flag on + a bus whose hook
    # replaces the section list with one that DROPS the persona) and assert the
    # persona is restored at the FRONT by _reassert_protected_sections.
    monkeypatch.setenv("MAGI_PROMPT_TRANSFORM_HOOKS_ENABLED", "1")

    from magi_agent.harness.resolved import build_default_resolved_harness_state
    from magi_agent.hooks.bus import HookBus, RegisteredHook
    from magi_agent.hooks.context import HookContext
    from magi_agent.hooks.manifest import HookManifest, HookPoint
    from magi_agent.hooks.result import HookResult
    from magi_agent.tools.manifest import ToolSource

    def handler(_: HookContext) -> HookResult:
        # Hostile/buggy hook: returns a section list that OMITS the persona
        # entirely (only a filler section remains).
        return HookResult(action="replace", value=["SOME-UNRELATED-SECTION"])

    manifest = HookManifest(
        name="drop-persona",
        point=HookPoint.BEFORE_SYSTEM_PROMPT,
        description="drops persona hook",
        source=ToolSource(kind="builtin", package="test"),
        priority=0,
    )
    bus = HookBus(hooks=(RegisteredHook(manifest=manifest, handler=handler),))

    prompt = build_system_prompt(
        session_key="s",
        turn_id="t",
        identity={},
        hook_bus=bus,
        harness_state=build_default_resolved_harness_state(),
    )

    # Persona restored despite the hook dropping it, and re-asserted at the front.
    assert MAGI_BASE_PERSONA in prompt
    assert prompt.startswith(MAGI_BASE_PERSONA)
