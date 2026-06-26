"""Tests for the pure-function runtime-fields derivation (F-UX2 / F8 core).

The module is hand-built from the discovery's per_tuple_variables map. The
tests pin the per-(lifecycle, condition) tuple field list (so an accidental
removal at the runtime gate signature drifts an authoring-time test, NOT a
silent dashboard regression), plus the tool-input expansion contract and the
evidence-fields fan-out.

All tests are pure (no env, no fixtures) and the derivation never raises.
"""

from __future__ import annotations

from typing import Any

import pytest

from magi_agent.customize.runtime_fields import fields_for_context


# ---------------------------------------------------------------------------
# fake tool registry for tool_input.* expansion tests
# ---------------------------------------------------------------------------


class _FakeManifest:
    def __init__(self, schema: dict[str, Any]):
        self.input_schema = schema


class _FakeRegistry:
    def __init__(self, manifests: dict[str, _FakeManifest] | None = None) -> None:
        self._manifests = manifests or {}

    def resolve(self, name: str) -> _FakeManifest | None:  # noqa: D401
        return self._manifests.get(name)


# ---------------------------------------------------------------------------
# context-free fields per lifecycle
# ---------------------------------------------------------------------------


def _names(fields: list[dict[str, str]]) -> list[str]:
    return [f["name"] for f in fields]


def test_before_tool_use_regex_lists_session_turn_tool_chips() -> None:
    out = fields_for_context("before_tool_use", "regex")
    names = _names(out)
    for required in ("session_id", "turn_id", "tool_name", "tool_use_id"):
        assert required in names, names
    # No tool given -> the generic tool_input.* marker chip is present.
    assert "tool_input.*" in names


def test_before_tool_use_domain_adds_url_alias_chips() -> None:
    out = fields_for_context("before_tool_use", "domain")
    names = _names(out)
    # URL alias key set per tool_perm._URL_ARG_KEYS.
    for key in ("url", "uri", "href", "link", "address", "endpoint"):
        assert f"tool_input.{key}" in names, key


def test_before_tool_use_path_adds_path_alias_chips() -> None:
    out = fields_for_context("before_tool_use", "path")
    names = _names(out)
    for key in ("path", "file", "filename", "filepath", "filePath", "pathRef"):
        assert f"tool_input.{key}" in names, key


def test_before_tool_use_path_allowlist_alias_form_matches_path() -> None:
    out_camel = fields_for_context("before_tool_use", "pathAllowlist")
    out_snake = fields_for_context("before_tool_use", "path_allowlist")
    # Both spellings are accepted and produce the same chip set.
    assert _names(out_camel) == _names(out_snake)


def test_before_tool_use_domain_allowlist_alias_form_matches_domain() -> None:
    out_camel = fields_for_context("before_tool_use", "domainAllowlist")
    out_snake = fields_for_context("before_tool_use", "domain_allowlist")
    assert _names(out_camel) == _names(out_snake)


# ---------------------------------------------------------------------------
# after_tool_use — tool result + truncation chips appear, alias chips do NOT
# ---------------------------------------------------------------------------


def test_after_tool_use_regex_includes_tool_result_text_and_truncated() -> None:
    out = fields_for_context("after_tool_use", "regex")
    names = _names(out)
    for required in (
        "session_id",
        "turn_id",
        "tool_name",
        "tool_use_id",
        "tool_result_text",
        "tool_result_truncated",
    ):
        assert required in names, names


def test_after_tool_use_llm_criterion_returns_same_base_set() -> None:
    out_regex = fields_for_context("after_tool_use", "regex")
    out_crit = fields_for_context("after_tool_use", "llm_criterion")
    # Both surfaces feed the same gate signature; the chip menu mirrors that.
    assert _names(out_regex) == _names(out_crit)


def test_after_tool_use_truncated_chip_is_bool_typed() -> None:
    out = fields_for_context("after_tool_use", "regex")
    by_name = {f["name"]: f for f in out}
    assert by_name["tool_result_truncated"]["type"] == "bool"


# ---------------------------------------------------------------------------
# pre_final — evidence_ref + shacl_constraint fan out to evidence:* chips
# ---------------------------------------------------------------------------


def test_pre_final_evidence_ref_lists_evidence_field_chips() -> None:
    out = fields_for_context("pre_final", "evidence_ref")
    names = _names(out)
    # Context-free base
    assert "session_id" in names
    assert "turn_id" in names
    # Real producer fields from _BUILTIN_FIELD_HINTS — TestRun is verified.
    assert "evidence:TestRun.fields.command" in names
    assert "evidence:TestRun.fields.exitCode" in names


def test_pre_final_shacl_constraint_alias_lists_same_evidence_chips() -> None:
    out_shacl = fields_for_context("pre_final", "shacl_constraint")
    out_field = fields_for_context("pre_final", "field_constraint")
    out_raw = fields_for_context("pre_final", "shacl")
    # All three SHACL-shaped surfaces share the evidence-fields menu.
    assert _names(out_shacl) == _names(out_field) == _names(out_raw)


def test_pre_final_llm_criterion_returns_final_text_and_turn_summary() -> None:
    out = fields_for_context("pre_final", "llm_criterion")
    names = _names(out)
    assert "final_text" in names
    assert "turn_summary" in names
    # Per the design: at pre_final the LLM judge does NOT receive tool_result_text.
    assert "tool_result_text" not in names


def test_pre_final_evidence_chips_include_unverified_type_markers() -> None:
    """Types with an honest empty hint still surface a ``.fields.*`` marker.

    Asserts the inert-producer-hide invariant: types whose producer is not
    confidently located in the codebase are NOT silently dropped from the
    picker (the operator must see they exist), but they're labeled so the
    operator knows authoring a per-field rule against them may silently
    never fire.
    """
    out = fields_for_context("pre_final", "evidence_ref")
    names = _names(out)
    # FileDeliver is listed in _BUILTIN_FIELD_HINTS with [] (empty by design).
    assert "evidence:FileDeliver.fields.*" in names
    # GitDiff now has a confident producer, so it surfaces concrete field chips
    # (not the unverified ``.fields.*`` marker).
    assert "evidence:GitDiff.fields.changedFiles" in names
    assert "evidence:GitDiff.fields.*" not in names


# ---------------------------------------------------------------------------
# tool_input.* expansion via the tool registry
# ---------------------------------------------------------------------------


def test_tool_input_expansion_uses_manifest_input_schema_when_tool_given() -> None:
    registry = _FakeRegistry(
        {
            "fetch_url": _FakeManifest(
                {
                    "type": "object",
                    "properties": {
                        "url": {"type": "string", "description": "URL to fetch"},
                        "method": {"type": "string"},
                    },
                }
            )
        }
    )
    out = fields_for_context(
        "before_tool_use",
        "regex",
        tool="fetch_url",
        tool_registry=registry,
    )
    names = _names(out)
    assert "tool_input.url" in names
    assert "tool_input.method" in names
    # The generic marker chip is NOT in the list when a specific tool resolved.
    assert "tool_input.*" not in names


def test_tool_input_expansion_returns_marker_for_unknown_tool() -> None:
    registry = _FakeRegistry({})
    out = fields_for_context(
        "before_tool_use",
        "regex",
        tool="not_a_real_tool",
        tool_registry=registry,
    )
    names = _names(out)
    # Unknown tool: no manifest -> no tool_input.* chips (degrade quietly).
    assert "tool_input.url" not in names
    # Context-free chips still present.
    assert "session_id" in names


def test_tool_input_expansion_returns_marker_when_no_registry() -> None:
    out = fields_for_context("before_tool_use", "regex", tool="fetch_url")
    names = _names(out)
    # No registry passed -> degrade to a single generic marker.
    assert "tool_input.*" in names


def test_tool_input_expansion_carries_jsonschema_description_into_chip() -> None:
    registry = _FakeRegistry(
        {
            "fetch_url": _FakeManifest(
                {
                    "type": "object",
                    "properties": {
                        "url": {
                            "type": "string",
                            "description": "Target URL to fetch (must be http or https).",
                        }
                    },
                }
            )
        }
    )
    out = fields_for_context(
        "before_tool_use",
        "regex",
        tool="fetch_url",
        tool_registry=registry,
    )
    by_name = {f["name"]: f for f in out}
    chip = by_name["tool_input.url"]
    assert chip["type"] == "string"
    assert chip["description"] == "Target URL to fetch (must be http or https)."


# ---------------------------------------------------------------------------
# Tier 2 (PR-F-UX1) + spawn (F4) lifecycles
# ---------------------------------------------------------------------------


def test_on_user_prompt_submit_lists_user_prompt_text() -> None:
    out = fields_for_context("on_user_prompt_submit", "llm_criterion")
    names = _names(out)
    assert "user_prompt_text" in names
    assert "session_id" in names
    assert "turn_id" in names


def test_on_subagent_stop_lists_child_final_text_and_session() -> None:
    out = fields_for_context("on_subagent_stop", "llm_criterion")
    names = _names(out)
    assert "child_final_text" in names
    assert "child_session_id" in names


def test_spawn_capability_scope_lists_request_role_and_metadata() -> None:
    out = fields_for_context("spawn", "capability_scope")
    names = _names(out)
    assert "request.role" in names
    assert "request.metadata" in names


# ---------------------------------------------------------------------------
# fail-open contract
# ---------------------------------------------------------------------------


def test_unknown_lifecycle_returns_empty_list() -> None:
    assert fields_for_context("not_a_lifecycle", "regex") == []


def test_unknown_condition_returns_empty_list() -> None:
    assert fields_for_context("before_tool_use", "not_a_condition") == []


def test_non_string_args_return_empty_list() -> None:
    assert fields_for_context(123, "regex") == []  # type: ignore[arg-type]
    assert fields_for_context("before_tool_use", None) == []  # type: ignore[arg-type]


def test_known_tuple_returns_nonempty_list() -> None:
    # Smoke: every (lifecycle, condition) tuple the wizard exposes returns
    # a non-empty chip menu.
    cases = [
        ("before_tool_use", "regex"),
        ("before_tool_use", "domain"),
        ("before_tool_use", "domain_allowlist"),
        ("before_tool_use", "path"),
        ("before_tool_use", "path_allowlist"),
        ("after_tool_use", "regex"),
        ("after_tool_use", "llm_criterion"),
        ("pre_final", "evidence_ref"),
        ("pre_final", "verifier_passed"),
        ("pre_final", "field_constraint"),
        ("pre_final", "shacl_constraint"),
        ("pre_final", "llm_criterion"),
        ("on_user_prompt_submit", "llm_criterion"),
        ("on_subagent_stop", "llm_criterion"),
        ("spawn", "capability_scope"),
    ]
    for lifecycle, condition in cases:
        out = fields_for_context(lifecycle, condition)
        assert len(out) > 0, f"{lifecycle}/{condition} produced no chips"


def test_derivation_is_deterministic() -> None:
    a = fields_for_context("before_tool_use", "regex")
    b = fields_for_context("before_tool_use", "regex")
    assert a == b


def test_every_chip_has_name_type_description_keys() -> None:
    out = fields_for_context("after_tool_use", "llm_criterion")
    for chip in out:
        assert set(chip.keys()) >= {"name", "type", "description"}, chip
        assert isinstance(chip["name"], str) and chip["name"]
        assert isinstance(chip["type"], str) and chip["type"]
        assert isinstance(chip["description"], str)
