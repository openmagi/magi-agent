"""Track 19 PR8 — progressive-disclosure GA recipes + compaction-protected bodies.

Tests (TDD, RED first):

(a) the cheap up-front listing contains existing GA presets' title + whenToUse
    but NOT the full bodies (so it is cheap to inject);
(b) ``load_recipe`` returns the FULL playbook body for a valid recipe and an
    error for an unknown recipe;
(c) microcompact / auto_compact PRESERVE a ``load_recipe`` tool-result (the
    loaded body is compaction-protected) while still compacting other results;
(d) flag-OFF / non-general → the listing is absent and the tool is inert;
(e) the compaction protection is a no-op for non-GA tool results.
"""
from __future__ import annotations

import pytest

from magi_agent.config.env import general_automation_live_enabled
from magi_agent.context.auto_compact import AutoCompactionEngine
from magi_agent.context.microcompact import (
    MIN_RESULT_TOKENS_FOR_COMPACT,
    MicrocompactEngine,
)
from magi_agent.context.types import WarningLevel
from magi_agent.harness.general_automation.recipe_disclosure import (
    LOAD_GA_RECIPE_TOOL_NAME,
    build_ga_recipe_listing_section,
    ga_recipe_listing_section,
    load_ga_recipe,
    load_ga_recipe_handler,
    load_ga_recipe_manifest,
)
from magi_agent.recipes.first_party.general_automation.presets import (
    GENERAL_AUTOMATION_PRESET_IDS,
    general_automation_preset_catalog,
)
from magi_agent.tools.context import ToolContext


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

_ON = {"MAGI_GA_LIVE_ENABLED": "1"}
_OFF = {"MAGI_GA_LIVE_ENABLED": "0"}


def _ctx(*, agent_role: str = "general") -> ToolContext:
    return ToolContext(
        botId="bot-1",
        sessionKey="sess-1",
        turnId="turn-1",
        executionContract={"agentRole": agent_role},
    )


async def _summarize(prompt: str) -> str:
    return "SUMMARY"


def _big_tool_result(
    *,
    name: str | None = None,
    tool_use_id: str | None = None,
    chars: int = 60_000,
) -> dict:
    msg: dict = {"role": "tool", "content": "y" * chars}
    if name is not None:
        msg["name"] = name
    if tool_use_id is not None:
        msg["tool_use_id"] = tool_use_id
    return msg


# ---------------------------------------------------------------------------
# (a) cheap up-front listing — title + whenToUse, NOT full bodies
# ---------------------------------------------------------------------------

def test_listing_contains_titles_and_when_to_use_only() -> None:
    section = build_ga_recipe_listing_section()
    assert section is not None
    # Every existing preset's title appears.
    for preset in general_automation_preset_catalog():
        assert preset.title in section
    # whenToUse marker present, full-body markers absent (cheap listing).
    assert "When to use" in section
    # The load tool is advertised so the model knows how to fetch a body.
    assert LOAD_GA_RECIPE_TOOL_NAME in section
    # The listing must NOT inline a full body: the per-recipe full body has a
    # distinctive heading that only the loaded body carries.
    full_body = load_ga_recipe("automation.office")
    assert "Allowed permissions:" in full_body  # body-only detail
    assert "Allowed permissions:" not in section


def test_listing_built_from_existing_presets_not_a_parallel_list() -> None:
    section = build_ga_recipe_listing_section()
    # Section is derived from the real preset catalog: count of listed recipe
    # ids equals the canonical preset id count.
    listed = sum(1 for rid in GENERAL_AUTOMATION_PRESET_IDS if rid in section)
    assert listed == len(GENERAL_AUTOMATION_PRESET_IDS)


# ---------------------------------------------------------------------------
# (b) load tool returns full body / errors on unknown
# ---------------------------------------------------------------------------

def test_load_recipe_returns_full_body_for_valid_recipe() -> None:
    body = load_ga_recipe("automation.research")
    assert "Research" in body  # title
    assert "Allowed permissions:" in body  # full-body detail
    assert "Tool categories:" in body
    # A representative tool category for research is present.
    assert "web_search" in body


def test_load_recipe_unknown_raises() -> None:
    with pytest.raises(KeyError):
        load_ga_recipe("automation.does-not-exist")


def test_load_recipe_handler_ok_when_active() -> None:
    result = load_ga_recipe_handler(
        {"recipe": "automation.files"},
        _ctx(agent_role="general"),
        env=_ON,
    )
    assert result.status == "ok"
    body = result.output
    assert isinstance(body, str)
    assert "Files" in body
    assert "Allowed permissions:" in body
    # The result is marked load-recipe so compaction protection recognizes it.
    assert result.metadata.get("toolName") == LOAD_GA_RECIPE_TOOL_NAME
    assert result.metadata.get("compactionProtected") is True


def test_load_recipe_handler_unknown_returns_error() -> None:
    result = load_ga_recipe_handler(
        {"recipe": "automation.nope"},
        _ctx(agent_role="general"),
        env=_ON,
    )
    assert result.status == "error"
    assert result.error_code == "general_automation_recipe_unknown"


# ---------------------------------------------------------------------------
# (d) flag-OFF / non-general → listing absent + tool inert
# ---------------------------------------------------------------------------

def test_listing_absent_when_flag_off() -> None:
    assert ga_recipe_listing_section(agent_role="general", env=_OFF) is None


def test_listing_absent_when_non_general() -> None:
    assert ga_recipe_listing_section(agent_role="coding", env=_ON) is None


def test_listing_present_when_on_and_general() -> None:
    section = ga_recipe_listing_section(agent_role="general", env=_ON)
    assert section is not None
    assert "Research" in section


def test_load_recipe_handler_inert_when_flag_off() -> None:
    result = load_ga_recipe_handler(
        {"recipe": "automation.files"},
        _ctx(agent_role="general"),
        env=_OFF,
    )
    assert result.status == "blocked"
    assert result.metadata.get("reason") == "general_automation_recipe_inert"


def test_load_recipe_handler_inert_when_non_general() -> None:
    result = load_ga_recipe_handler(
        {"recipe": "automation.files"},
        _ctx(agent_role="coding"),
        env=_ON,
    )
    assert result.status == "blocked"
    assert result.metadata.get("reason") == "general_automation_recipe_inert"


def test_manifest_disabled_by_default() -> None:
    manifest = load_ga_recipe_manifest()
    assert manifest.name == LOAD_GA_RECIPE_TOOL_NAME
    assert manifest.enabled_by_default is False
    assert manifest.permission == "meta"


# ---------------------------------------------------------------------------
# (c) compaction protection — load_recipe result survives, others compacted
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_microcompact_protects_load_recipe_result() -> None:
    protected = _big_tool_result(name=LOAD_GA_RECIPE_TOOL_NAME, tool_use_id="t-protected")
    other = _big_tool_result(name="GeneralAutomationShellRequest", tool_use_id="t-other")
    messages = [protected, other]

    engine = MicrocompactEngine(classifier=_summarize)
    out, result = await engine.apply(messages, WarningLevel.HIGH)

    # The protected load_recipe body is unchanged (still the big content).
    assert out[0]["content"] == protected["content"]
    # The other big tool result is compacted to the summary.
    assert out[1]["content"] == "SUMMARY"
    assert result.messages_compacted == 1


@pytest.mark.asyncio
async def test_microcompact_protection_noop_for_non_ga_results() -> None:
    # No load_recipe results → protection is a no-op; large results compact.
    other = _big_tool_result(name="GeneralAutomationShellRequest", tool_use_id="t-1")
    engine = MicrocompactEngine(classifier=_summarize)
    out, result = await engine.apply([other], WarningLevel.HIGH)
    assert out[0]["content"] == "SUMMARY"
    assert result.messages_compacted == 1


@pytest.mark.asyncio
async def test_auto_compact_protects_load_recipe_result() -> None:
    # Build a long conversation: several old turns + a protected load_recipe
    # result inside the OLD region, then recent turns kept verbatim.
    protected_body = "z" * 5_000
    messages: list[dict] = []
    for i in range(6):
        messages.append({"role": "user", "content": f"user message {i}"})
        if i == 1:
            messages.append(
                {
                    "role": "tool",
                    "name": LOAD_GA_RECIPE_TOOL_NAME,
                    "tool_use_id": "t-protected",
                    "content": protected_body,
                }
            )
        messages.append({"role": "assistant", "content": f"assistant reply {i}"})

    engine = AutoCompactionEngine(classifier=_summarize, keep_recent_turns=2)
    out, result = await engine.apply(messages, WarningLevel.CRITICAL)

    assert result.activated is True
    # The protected load_recipe body survives compaction verbatim somewhere in
    # the output even though its turn was in the OLD (summarized) region.
    serialized = "".join(
        m.get("content", "") if isinstance(m.get("content"), str) else "" for m in out
    )
    assert protected_body in serialized


@pytest.mark.asyncio
async def test_auto_compact_protection_noop_for_non_ga() -> None:
    # Without a protected result, the old region is summarized away.
    old_body = "q" * 5_000
    messages: list[dict] = []
    for i in range(6):
        messages.append({"role": "user", "content": f"user message {i}"})
        if i == 1:
            messages.append(
                {
                    "role": "tool",
                    "name": "GeneralAutomationShellRequest",
                    "tool_use_id": "t-1",
                    "content": old_body,
                }
            )
        messages.append({"role": "assistant", "content": f"assistant reply {i}"})

    engine = AutoCompactionEngine(classifier=_summarize, keep_recent_turns=2)
    out, result = await engine.apply(messages, WarningLevel.CRITICAL)
    assert result.activated is True
    serialized = "".join(
        m.get("content", "") if isinstance(m.get("content"), str) else "" for m in out
    )
    assert old_body not in serialized


# ---------------------------------------------------------------------------
# sanity: flag helper unchanged
# ---------------------------------------------------------------------------

def test_flag_helper_full_profile_default_on_and_explicit_off() -> None:
    assert general_automation_live_enabled({}) is True
    assert general_automation_live_enabled(_OFF) is False
    assert general_automation_live_enabled(_ON) is True
