"""PR10: per-model semantic coding-hint blocks in the assembled system prompt.

When the model-aware flag is ON and the coding-agent path is active, the
assembled prompt carries a SMALL, family-keyed coding hint distilled from each
model family's known coding strengths/failure modes (OpenCode-style), e.g.

* GPT/o-series  -> verify stale-knowledge API/lib assumptions before trusting them
* Gemini        -> always use absolute file paths in tool calls
* Kimi/Moonshot -> code only takes effect when written via tools, not in reply text
* Claude        -> NO hint (already follows the structured body well)

These tests assert:
  * each family's prompt contains ONLY its own hint (no cross-family bleed),
  * the hint lives in the STATIC (cacheable) region before the dynamic
    boundary sentinel,
  * the hard-safety protected sections still lead the prompt,
  * flag OFF  -> no hint (single shared body, zero regression),
  * non-coding agent -> no coding hint even with the flag ON,
  * and — critically — the hint reaches the model on the LIVE request path the
    production runner uses (chat.py -> build_gate5b4c3_runner_input ->
    gate5b4c3 live runner -> ADK Agent.instruction), gated by
    MAGI_MODEL_AWARE_PROMPTS_ENABLED.
"""

from __future__ import annotations

from datetime import UTC, datetime
import importlib
from types import ModuleType

import pytest


def _builder() -> ModuleType:
    try:
        return importlib.import_module("magi_agent.runtime.message_builder")
    except ModuleNotFoundError as exc:  # pragma: no cover - import guard
        pytest.fail(f"message_builder module is missing: {exc}")


def _utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def _build(builder: ModuleType, *, model: str, flag: bool, coding: bool = True) -> str:
    return builder.build_system_prompt(
        session_key="s1",
        turn_id="t1",
        identity={},
        now=_utc("2026-05-28T00:00:00Z"),
        coding_agent=coding,
        model=model,
        model_aware_prompts_enabled=flag,
    )


# The hint text fragments that uniquely identify each family's block.
_GPT_MARKER = "your training may be stale"
_GEMINI_MARKER = "absolute file paths"
_KIMI_MARKER = "is not saved to disk"

_HINT_OPEN = "<coding-model-hint"
_ALL_MARKERS = (_GPT_MARKER, _GEMINI_MARKER, _KIMI_MARKER)


def _other_markers(own: str) -> tuple[str, ...]:
    return tuple(marker for marker in _ALL_MARKERS if marker != own)


@pytest.mark.parametrize(
    ("model", "own_marker"),
    [
        ("gpt-5", _GPT_MARKER),
        ("openai/gpt-5.5", _GPT_MARKER),
        ("gemini-3.5-flash", _GEMINI_MARKER),
        ("google/gemini-3.1-pro-preview", _GEMINI_MARKER),
        ("kimi-k2", _KIMI_MARKER),
        ("fireworks/kimi-k2p6", _KIMI_MARKER),
    ],
)
def test_family_hint_present_and_isolated(model: str, own_marker: str) -> None:
    builder = _builder()
    out = _build(builder, model=model, flag=True, coding=True)

    assert _HINT_OPEN in out
    assert own_marker in out
    for other in _other_markers(own_marker):
        assert other not in out


@pytest.mark.parametrize(
    "model",
    ["claude-opus-4-6", "anthropic/claude-sonnet-4-6"],
)
def test_claude_gets_no_hint(model: str) -> None:
    # The anthropic no-op hint was dropped: claude already follows the
    # structured blocks, so it must receive NO coding-model-hint block at all.
    builder = _builder()
    out = _build(builder, model=model, flag=True, coding=True)

    assert _HINT_OPEN not in out
    for marker in _ALL_MARKERS:
        assert marker not in out


def test_unknown_model_gets_generic_or_no_hint_but_no_family_bleed() -> None:
    builder = _builder()
    out = _build(builder, model="some-unknown-model", flag=True, coding=True)

    # The default family must not leak any other family's specific advice.
    for marker in _ALL_MARKERS:
        assert marker not in out


def test_flag_off_injects_no_hint_zero_regression() -> None:
    builder = _builder()
    on = _build(builder, model="gpt-5", flag=True, coding=True)
    off = _build(builder, model="gpt-5", flag=False, coding=True)

    assert _HINT_OPEN in on
    assert _HINT_OPEN not in off
    for marker in _ALL_MARKERS:
        assert marker not in off


def test_flag_off_prompt_is_model_independent() -> None:
    builder = _builder()
    gpt_off = _build(builder, model="gpt-5", flag=False, coding=True)
    gemini_off = _build(builder, model="gemini-3.5-flash", flag=False, coding=True)

    # Single shared body when the flag is off: identical regardless of model.
    assert gpt_off == gemini_off


def test_non_coding_agent_gets_no_hint_even_with_flag_on() -> None:
    builder = _builder()
    out = _build(builder, model="gpt-5", flag=True, coding=False)

    assert _HINT_OPEN not in out
    for marker in _ALL_MARKERS:
        assert marker not in out


def test_hint_lives_in_static_region_before_dynamic_boundary() -> None:
    builder = _builder()
    out = _build(builder, model="gemini-3.5-flash", flag=True, coding=True)

    boundary = builder.PROMPT_DYNAMIC_BOUNDARY
    assert boundary in out
    hint_at = out.index(_HINT_OPEN)
    boundary_at = out.index(boundary)
    assert hint_at < boundary_at


def test_protected_sections_lead_in_initial_assembly_with_hint_present() -> None:
    # NOTE: this exercises INITIAL assembly ordering (no hook), not the
    # promptTransform hook re-assertion path. See
    # test_protected_sections_reasserted_after_hook_reorder for the hook case.
    builder = _builder()
    out = _build(builder, model="gpt-5", flag=True, coding=True)

    deferral_at = out.index(builder.DEFERRAL_PREVENTION_BLOCK)
    output_rules_at = out.index(builder.OUTPUT_RULES_BLOCK)
    action_safety_at = out.index(builder.ACTION_SAFETY_BLOCK)
    hint_at = out.index(_HINT_OPEN)

    # Protected hard-safety blocks remain at the very front, ahead of the hint.
    assert deferral_at < output_rules_at < action_safety_at
    assert action_safety_at < hint_at


def test_protected_sections_reasserted_after_hook_reorder() -> None:
    # Directly drive the re-assertion helper that the promptTransform hook path
    # invokes: a hook that REORDERS protected blocks to the bottom must be
    # canonicalised back to the front (Track 16 rule 4).
    builder = _builder()
    protected = list(builder._PROTECTED_SECTIONS)
    # Hook output: protected blocks shoved to the end behind other content.
    reordered = ["<identity>...</identity>", *protected[::-1]]

    canonical = builder._reassert_protected_sections(reordered)

    assert canonical[: len(protected)] == protected
    assert canonical[len(protected) :] == ["<identity>...</identity>"]


def test_blocks_builder_injects_hint_when_model_aware_flag_on() -> None:
    builder = _builder()
    # The cache/blocks path is no longer hard-coded to model_aware=off: when the
    # flag is threaded ON it injects the per-family hint into the STATIC region.
    on = builder.build_system_prompt_blocks(
        session_key="s1",
        turn_id="t1",
        identity={},
        now=_utc("2026-05-28T00:00:00Z"),
        coding_agent=True,
        model="gpt-5",
        model_aware_prompts_enabled=True,
    )
    off = builder.build_system_prompt_blocks(
        session_key="s1",
        turn_id="t1",
        identity={},
        now=_utc("2026-05-28T00:00:00Z"),
        coding_agent=True,
        model="gpt-5",
        model_aware_prompts_enabled=False,
    )
    on_text = "".join(str(block.get("text", "")) for block in on)
    off_text = "".join(str(block.get("text", "")) for block in off)
    assert _HINT_OPEN in on_text
    assert _GPT_MARKER in on_text
    # Flag off keeps the blocks path model-independent (no cache segmentation).
    assert _HINT_OPEN not in off_text


# ---------------------------------------------------------------------------
# LIVE PATH integration test.
#
# The production runner does NOT call message_builder. The live system prompt
# reaches the model via:
#   transport/chat.py
#     -> run_gate5b4c3_live_runner_boundary_async
#       -> build_gate5b4c3_runner_input(request)   <-- live entry under test
#         -> _build_system_instruction(...)         <-- where the hint is added
#           -> Gate5B4C3RunnerInput.system_instruction
#             -> ADK Agent(instruction=...)         (boundary line ~512)
#
# This test drives that exact live entry (build_gate5b4c3_runner_input) with the
# coding-capable route (selected_full_toolhost) and asserts the family hint is
# present in the system_instruction the Agent receives when the env flag is ON,
# and absent when OFF / for a non-coding route. Removing the wiring fails it.
# ---------------------------------------------------------------------------

_SHA = "sha256:"
_DIGEST = _SHA + "a" * 64


def _runner_payload(model_label: str, tools_policy: str) -> dict[str, object]:
    # Both selected_full_toolhost (coding route) and shadow_readonly require
    # tools enabled in policy; only "disabled" turns tools off. The coding hint
    # gates on selected_full_toolhost specifically, so readonly still gets none.
    tools_enabled_route = tools_policy in {"selected_full_toolhost", "shadow_readonly"}
    return {
        "schemaVersion": "gate5b4c3.chatProxyShadowGeneration.v1",
        "mode": "shadow_generation_diagnostic",
        "responseAuthority": "typescript",
        "shadowGenerationId": "shadow_gen_live",
        "requestIdDigest": _DIGEST,
        "traceIdDigest": _DIGEST,
        "createdAt": 1779200000000,
        "selection": {
            "botIdDigest": _DIGEST,
            "ownerUserIdDigest": _DIGEST,
            "environment": "production",
            "selectedTarget": "gate5b_selected_bot",
            "sessionKeyDigest": _DIGEST,
        },
        "turn": {
            "turnId": "turn_live_001",
            "turnDigest": _DIGEST,
            "sanitizedCurrentTurnText": "Please refactor the helper function.",
            "sanitizedInputTextDigest": _DIGEST,
            "channelName": "app_channel",
            "tsResponseCorrelationId": "ts_corr_live",
        },
        "modelRouting": {
            "routingSource": "per_turn_injected",
            "providerLabel": "openai",
            "modelLabel": model_label,
            "routerDecisionDigest": _DIGEST,
            "routingProfileDigest": _DIGEST,
            "botConfigModelDigest": _DIGEST,
            "shadowCredentialRef": "server-shadow-ref",
            "credentialRefSource": "server_config",
            "temperature": 0.2,
            "maxOutputTokens": 512,
        },
        "recipeProfile": {
            "recipeId": "coding-assistant",
            "recipeVersion": "2026-05-19",
            "profileId": "selected-bot-shadow",
            "profileVersion": "v1",
            "runtimeEngine": "adk-python",
            "toolsPolicy": tools_policy,
            "memoryMode": "disabled",
            "sourceAuthority": "current_turn_only",
        },
        "policy": {
            "typeScriptResponseAuthority": True,
            "pythonDiagnosticOnly": True,
            "outputIsolation": "local_diagnostic_only",
            "toolsDisabled": not tools_enabled_route,
            "toolHostDispatchAllowed": tools_enabled_route,
            "memoryProviderCallsAllowed": False,
            "memoryWritesAllowed": False,
            "promptMemoryInjectionAllowed": False,
            "workspaceMutationAllowed": False,
            "childExecutionAllowed": False,
            "missionRuntimeAllowed": False,
            "evidenceBlockModeAllowed": False,
        },
        "budgets": {},
        "redaction": {
            "sanitizerId": "chat-proxy-sanitizer",
            "sanitizerVersion": "v1",
            "policyId": "gate5b4c3-redaction",
            "status": "passed",
            "redactedAt": 1779200000001,
            "redactedByteCount": 47,
            "forbiddenFieldScan": "passed",
            "sanitizedPayloadDigest": _DIGEST,
        },
        "authority": {},
    }


def _live_system_instruction(
    model_label: str,
    *,
    flag: bool,
    tools_policy: str = "selected_full_toolhost",
) -> str:
    from magi_agent.shadow.gate5b4c3_runner_input_adapter import (
        build_gate5b4c3_runner_input,
    )
    from magi_agent.shadow.gate5b4c3_shadow_generation_contract import (
        Gate5B4C3ShadowGenerationRequest,
    )

    request = Gate5B4C3ShadowGenerationRequest.model_validate(
        _runner_payload(model_label, tools_policy)
    )
    env = {"MAGI_MODEL_AWARE_PROMPTS_ENABLED": "1" if flag else "0"}
    result = build_gate5b4c3_runner_input(request, env=env)
    assert result.status == "accepted", result.reason
    assert result.runner_input is not None
    return result.runner_input.system_instruction


@pytest.mark.parametrize(
    ("model", "marker"),
    [
        # Bare model ids: the gate contract route component forbids "/", so the
        # live model_label is always provider-prefixless. Family detection still
        # resolves gpt->openai, gemini->google, kimi->fireworks.
        ("gpt-5.5", _GPT_MARKER),
        ("gemini-3.1-pro-preview", _GEMINI_MARKER),
        ("kimi-k2p6", _KIMI_MARKER),
    ],
)
def test_live_runner_input_carries_family_hint_with_flag_on(
    model: str, marker: str
) -> None:
    instruction = _live_system_instruction(model, flag=True)
    assert _HINT_OPEN in instruction
    assert marker in instruction
    for other in _other_markers(marker):
        assert other not in instruction


def test_live_runner_input_has_no_hint_with_flag_off() -> None:
    instruction = _live_system_instruction("gpt-5.5", flag=False)
    assert _HINT_OPEN not in instruction
    for marker in _ALL_MARKERS:
        assert marker not in instruction


def test_live_runner_input_flag_off_is_model_independent() -> None:
    gpt = _live_system_instruction("gpt-5.5", flag=False)
    gemini = _live_system_instruction("gemini-3.1-pro-preview", flag=False)
    assert gpt == gemini


def test_live_runner_input_no_hint_for_non_coding_readonly_route() -> None:
    # shadow_readonly is NOT the coding route, so even with the flag on the
    # per-model coding hint must not appear.
    instruction = _live_system_instruction(
        "gpt-5.5", flag=True, tools_policy="shadow_readonly"
    )
    assert _HINT_OPEN not in instruction
    for marker in _ALL_MARKERS:
        assert marker not in instruction
