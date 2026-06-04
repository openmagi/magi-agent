"""Tests for per-provider tool-schema repair (PR9).

Scope (measure-first): magi runs on Google ADK (no LiteLLM dependency). ADK
1.33.0's ``_gemini_schema_util`` already repairs most provider-specific schema
issues for the Gemini path ($ref dereferencing, ``additionalProperties``
removal, snake_case conversion, type/null normalization, format filtering).

The one demonstrable remaining gap: Gemini's ``Schema.enum`` field is typed
``list[str]`` and the Gemini API rejects integer/number-valued enums. ADK does
not coerce enum values, so an integer-valued enum in a tool input schema reaches
the wire unrepaired (pydantic emits a serialization warning and the API rejects
the schema). These tests pin the Gemini integer-enum -> string-enum repair while
asserting every other provider family is an identity passthrough (handled by
ADK / not applicable on the ADK-native runtime).
"""

from __future__ import annotations

import copy

import pytest
from google.adk.tools import FunctionTool
from google.genai import types as genai_types

from magi_agent.adk_bridge import tool_adapter
from magi_agent.prompt.provider_adapter import (
    ProviderFamily,
    repair_tool_schema_for_provider,
)


# ---------------------------------------------------------------------------
# Gemini: integer/number enum -> string enum (values preserved)
# ---------------------------------------------------------------------------


class TestGeminiIntegerEnumRepair:
    def test_integer_enum_becomes_string_enum_values_preserved(self) -> None:
        schema = {
            "type": "object",
            "properties": {
                "priority": {"type": "integer", "enum": [1, 2, 3]},
            },
        }
        repaired = repair_tool_schema_for_provider(schema, ProviderFamily.GOOGLE)
        prop = repaired["properties"]["priority"]
        assert prop["type"] == "string"
        assert prop["enum"] == ["1", "2", "3"]

    def test_number_enum_becomes_string_enum_values_preserved(self) -> None:
        schema = {
            "type": "object",
            "properties": {
                "ratio": {"type": "number", "enum": [0.5, 1.0, 2.5]},
            },
        }
        repaired = repair_tool_schema_for_provider(schema, ProviderFamily.GOOGLE)
        prop = repaired["properties"]["ratio"]
        assert prop["type"] == "string"
        assert prop["enum"] == ["0.5", "1.0", "2.5"]

    def test_boolean_enum_becomes_string_enum_values_preserved(self) -> None:
        schema = {
            "type": "object",
            "properties": {
                "flag": {"type": "boolean", "enum": [True, False]},
            },
        }
        repaired = repair_tool_schema_for_provider(schema, ProviderFamily.GOOGLE)
        prop = repaired["properties"]["flag"]
        assert prop["type"] == "string"
        assert prop["enum"] == ["true", "false"]

    def test_string_enum_unchanged(self) -> None:
        schema = {
            "type": "object",
            "properties": {
                "mode": {"type": "string", "enum": ["fast", "slow"]},
            },
        }
        repaired = repair_tool_schema_for_provider(schema, ProviderFamily.GOOGLE)
        assert repaired["properties"]["mode"] == {
            "type": "string",
            "enum": ["fast", "slow"],
        }

    def test_mixed_enum_coerced_to_strings(self) -> None:
        schema = {
            "type": "object",
            "properties": {
                "weird": {"enum": [1, "two", 3.0]},
            },
        }
        repaired = repair_tool_schema_for_provider(schema, ProviderFamily.GOOGLE)
        prop = repaired["properties"]["weird"]
        assert prop["type"] == "string"
        assert prop["enum"] == ["1", "two", "3.0"]

    def test_nested_array_items_enum_repaired(self) -> None:
        schema = {
            "type": "object",
            "properties": {
                "levels": {
                    "type": "array",
                    "items": {"type": "integer", "enum": [10, 20]},
                },
            },
        }
        repaired = repair_tool_schema_for_provider(schema, ProviderFamily.GOOGLE)
        items = repaired["properties"]["levels"]["items"]
        assert items["type"] == "string"
        assert items["enum"] == ["10", "20"]

    def test_any_of_branch_enum_repaired(self) -> None:
        schema = {
            "anyOf": [
                {"type": "integer", "enum": [1, 2]},
                {"type": "null"},
            ],
        }
        repaired = repair_tool_schema_for_provider(schema, ProviderFamily.GOOGLE)
        branch = repaired["anyOf"][0]
        assert branch["type"] == "string"
        assert branch["enum"] == ["1", "2"]

    def test_other_fields_and_semantics_preserved(self) -> None:
        schema = {
            "type": "object",
            "description": "a tool",
            "properties": {
                "priority": {
                    "type": "integer",
                    "enum": [1, 2, 3],
                    "description": "priority level",
                },
                "name": {"type": "string"},
            },
            "required": ["priority"],
            "additionalProperties": False,
        }
        repaired = repair_tool_schema_for_provider(schema, ProviderFamily.GOOGLE)
        assert repaired["description"] == "a tool"
        assert repaired["required"] == ["priority"]
        assert repaired["additionalProperties"] is False
        assert repaired["properties"]["name"] == {"type": "string"}
        assert repaired["properties"]["priority"]["description"] == "priority level"

    def test_input_not_mutated(self) -> None:
        schema = {
            "type": "object",
            "properties": {"priority": {"type": "integer", "enum": [1, 2, 3]}},
        }
        snapshot = copy.deepcopy(schema)
        repair_tool_schema_for_provider(schema, ProviderFamily.GOOGLE)
        assert schema == snapshot

    def test_empty_enum_is_left_alone(self) -> None:
        schema = {"type": "integer", "enum": []}
        repaired = repair_tool_schema_for_provider(schema, ProviderFamily.GOOGLE)
        # Nothing to repair; no spurious type rewrite.
        assert repaired["type"] == "integer"
        assert repaired["enum"] == []


# ---------------------------------------------------------------------------
# Non-Gemini families: identity passthrough (handled by ADK / not applicable)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "family",
    [
        ProviderFamily.ANTHROPIC,
        ProviderFamily.OPENAI,
        ProviderFamily.FIREWORKS,
        ProviderFamily.DEFAULT,
    ],
)
class TestNonGeminiIdentity:
    def test_integer_enum_left_unchanged(self, family: ProviderFamily) -> None:
        schema = {
            "type": "object",
            "properties": {"priority": {"type": "integer", "enum": [1, 2, 3]}},
        }
        snapshot = copy.deepcopy(schema)
        repaired = repair_tool_schema_for_provider(schema, family)
        assert repaired == snapshot

    def test_returns_equal_copy(self, family: ProviderFamily) -> None:
        schema = {"type": "string", "enum": ["a", "b"]}
        repaired = repair_tool_schema_for_provider(schema, family)
        assert repaired == schema


# ---------------------------------------------------------------------------
# Flag + active-provider resolution
# ---------------------------------------------------------------------------


class TestProviderRepairConfig:
    def test_default_off(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("MAGI_PROVIDER_REPAIR_ENABLED", raising=False)
        assert tool_adapter.provider_repair_enabled() is False

    def test_flag_on(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MAGI_PROVIDER_REPAIR_ENABLED", "1")
        assert tool_adapter.provider_repair_enabled() is True

    def test_flag_off_explicit(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MAGI_PROVIDER_REPAIR_ENABLED", "0")
        assert tool_adapter.provider_repair_enabled() is False

    def test_active_provider_family_defaults_to_core_agent_model(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("CORE_AGENT_MODEL", "gemini-3.5-flash")
        assert tool_adapter.active_provider_family() == ProviderFamily.GOOGLE

    def test_active_provider_family_anthropic(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("CORE_AGENT_MODEL", "claude-sonnet-4-6")
        assert tool_adapter.active_provider_family() == ProviderFamily.ANTHROPIC

    def test_active_provider_family_unset_defaults_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("CORE_AGENT_MODEL", raising=False)
        assert tool_adapter.active_provider_family() == ProviderFamily.DEFAULT


# ---------------------------------------------------------------------------
# Anti-dormant wiring: FunctionTool declaration repair on the Gemini path
# ---------------------------------------------------------------------------


def _int_enum_tool() -> FunctionTool:
    async def my_tool(priority: int) -> dict[str, object]:
        return {"ok": priority}

    tool = FunctionTool(my_tool)
    return tool


def _force_int_enum_declaration(tool: FunctionTool) -> None:
    """Patch the tool to advertise an integer-valued enum parameter.

    ADK derives schemas from the Python signature (which cannot express an
    integer enum). The wire path that *can* carry an integer enum unrepaired is
    ``FunctionDeclaration.parameters_json_schema`` — a raw-dict passthrough that
    bypasses the string-typed ``Schema.enum`` validation. We install such a
    declaration to model the external / MCP tool case.
    """

    declaration = genai_types.FunctionDeclaration(
        name="my_tool",
        parameters_json_schema={
            "type": "object",
            "properties": {
                "priority": {"type": "integer", "enum": [1, 2, 3]},
            },
        },
    )
    object.__setattr__(tool, "_forced_declaration", declaration)
    tool._get_declaration = lambda: tool._forced_declaration  # type: ignore[method-assign]


class TestFunctionToolDeclarationRepairWiring:
    def test_off_path_is_identity(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MAGI_PROVIDER_REPAIR_ENABLED", "0")
        monkeypatch.setenv("CORE_AGENT_MODEL", "gemini-3.5-flash")
        tool = _int_enum_tool()
        _force_int_enum_declaration(tool)
        wrapped = tool_adapter.apply_provider_repair(tool)
        # OFF -> same object, unchanged declaration.
        assert wrapped is tool
        prop = wrapped._get_declaration().parameters_json_schema["properties"]["priority"]
        assert prop["type"] == "integer"
        assert prop["enum"] == [1, 2, 3]

    def test_on_gemini_repairs_integer_enum(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MAGI_PROVIDER_REPAIR_ENABLED", "1")
        monkeypatch.setenv("CORE_AGENT_MODEL", "gemini-3.5-flash")
        tool = _int_enum_tool()
        _force_int_enum_declaration(tool)
        wrapped = tool_adapter.apply_provider_repair(tool)
        prop = wrapped._get_declaration().parameters_json_schema["properties"]["priority"]
        assert prop["type"] == "string"
        assert prop["enum"] == ["1", "2", "3"]

    def test_on_non_gemini_is_identity(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MAGI_PROVIDER_REPAIR_ENABLED", "1")
        monkeypatch.setenv("CORE_AGENT_MODEL", "claude-sonnet-4-6")
        tool = _int_enum_tool()
        _force_int_enum_declaration(tool)
        wrapped = tool_adapter.apply_provider_repair(tool)
        prop = wrapped._get_declaration().parameters_json_schema["properties"]["priority"]
        assert prop["type"] == "integer"
        assert prop["enum"] == [1, 2, 3]

    def test_double_apply_does_not_stack_closures(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Applying apply_provider_repair twice must not double-wrap _get_declaration."""
        monkeypatch.setenv("MAGI_PROVIDER_REPAIR_ENABLED", "1")
        monkeypatch.setenv("CORE_AGENT_MODEL", "gemini-3.5-flash")
        tool = _int_enum_tool()
        _force_int_enum_declaration(tool)

        wrapped_once = tool_adapter.apply_provider_repair(tool)
        wrapped_twice = tool_adapter.apply_provider_repair(wrapped_once)

        # Both calls must return the same object.
        assert wrapped_once is tool
        assert wrapped_twice is tool

        # _get_declaration must be the same callable after both applications —
        # closures must not be stacked.
        assert wrapped_once._get_declaration is wrapped_twice._get_declaration

        # Behaviour: enum still correctly repaired (not double-wrapped or broken).
        prop = wrapped_twice._get_declaration().parameters_json_schema["properties"]["priority"]
        assert prop["type"] == "string"
        assert prop["enum"] == ["1", "2", "3"]


# ---------------------------------------------------------------------------
# None enum value -> JSON "null" (not Python "None")
# ---------------------------------------------------------------------------


class TestNoneEnumValue:
    def test_none_in_enum_becomes_json_null_string(self) -> None:
        schema = {
            "type": "object",
            "properties": {
                "count": {"enum": [1, None, 3]},
            },
        }
        repaired = repair_tool_schema_for_provider(schema, ProviderFamily.GOOGLE)
        prop = repaired["properties"]["count"]
        assert prop["enum"] == ["1", "null", "3"]
        assert prop["type"] == "string"

    def test_none_only_enum(self) -> None:
        schema = {"enum": [None]}
        repaired = repair_tool_schema_for_provider(schema, ProviderFamily.GOOGLE)
        assert repaired["enum"] == ["null"]
        assert repaired["type"] == "string"

    def test_none_enum_non_gemini_left_unchanged(self) -> None:
        """Non-Gemini families must leave None-valued enums untouched."""
        schema = {"enum": [1, None, 3]}
        for family in (
            ProviderFamily.ANTHROPIC,
            ProviderFamily.OPENAI,
            ProviderFamily.FIREWORKS,
            ProviderFamily.DEFAULT,
        ):
            repaired = repair_tool_schema_for_provider(schema, family)
            assert repaired is schema, f"expected same object for {family}"
