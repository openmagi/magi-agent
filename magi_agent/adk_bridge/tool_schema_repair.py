"""E-12 — provider-specific tool-schema repair for the ADK tool bridge.

magi runs on Google ADK (no LiteLLM dependency). ADK 1.33.0's
``google.adk.tools._gemini_schema_util`` repairs most provider-specific
JSON-schema issues for the typed Gemini schema path:

* ``$ref`` dereferencing (covers OpenCode's Moonshot ``$ref``-sibling
  case),
* camelCase → snake_case field conversion,
* type-list / ``null`` normalization,
* ``format`` field filtering.

The raw ``parameters_json_schema`` passthrough used by ``FunctionDeclaration``
can still carry provider-incompatible fields to the wire. This module
covers the live-observed Gemini gaps: non-string enum values and
additional-properties keywords. All other provider families are an
identity passthrough.

This used to live in ``prompt/provider_adapter.py`` (a layering leak —
schema repair is a tool/adk-bridge concern, not a prompt-assembly
concern). It moves here per E-12 of the unified remediation plan
(2026-06-18). ``prompt/provider_adapter`` re-exports
``repair_tool_schema_for_provider`` for back-compat with external
importers.
"""

from __future__ import annotations

from magi_agent.shared.provider_family import ProviderFamily

# JSON-schema keys whose values are themselves a (sub)schema.
_SCHEMA_VALUE_KEYS: tuple[str, ...] = ("items",)
# JSON-schema keys whose values are a list of (sub)schemas.
_SCHEMA_LIST_KEYS: tuple[str, ...] = ("anyOf", "oneOf", "allOf", "prefixItems")
# JSON-schema keys whose values map names → (sub)schema.
_SCHEMA_DICT_KEYS: tuple[str, ...] = (
    "properties",
    "$defs",
    "definitions",
    "patternProperties",
)
_GEMINI_DROPPED_SCHEMA_KEYS: frozenset[str] = frozenset(
    ("additionalProperties", "additional_properties")
)


def _enum_value_to_string(value: object) -> str:
    """Coerce a single enum value to the string form Gemini accepts.

    ``None`` maps to JSON ``"null"`` (not Python's ``"None"``).
    Booleans map to JSON-style lowercase ``"true"``/``"false"`` rather
    than Python's ``"True"``/``"False"`` so the surfaced enum reads
    naturally.
    """

    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _repair_gemini_schema(node: object) -> object:
    """Recursively coerce integer/number/boolean enums to string enums."""

    if isinstance(node, list):
        return [_repair_gemini_schema(item) for item in node]
    if not isinstance(node, dict):
        return node

    repaired: dict[str, object] = {}
    for key, value in node.items():
        if key in _GEMINI_DROPPED_SCHEMA_KEYS:
            continue
        if key in _SCHEMA_VALUE_KEYS:
            repaired[key] = _repair_gemini_schema(value)
        elif key in _SCHEMA_LIST_KEYS and isinstance(value, list):
            repaired[key] = [_repair_gemini_schema(item) for item in value]
        elif key in _SCHEMA_DICT_KEYS and isinstance(value, dict):
            repaired[key] = {
                name: _repair_gemini_schema(sub) for name, sub in value.items()
            }
        else:
            repaired[key] = value

    enum = repaired.get("enum")
    if isinstance(enum, list) and enum:
        needs_repair = any(not isinstance(item, str) for item in enum)
        if needs_repair:
            repaired["enum"] = [_enum_value_to_string(item) for item in enum]
            repaired["type"] = "string"
    return repaired


def repair_tool_schema_for_provider(
    schema: dict[str, object],
    family: ProviderFamily,
) -> dict[str, object]:
    """Return a provider-repaired copy of a tool input JSON schema.

    The input is never mutated on the Gemini path
    (``_repair_gemini_schema`` builds fresh dicts). Runtime ToolHost
    argument validation still runs against the original manifest schema;
    provider repair only normalizes the schema sent to the model
    provider.

    Only ``ProviderFamily.GOOGLE`` triggers a repair today. Every other
    family returns the *input object as-is* (callers must not mutate the
    returned value); the ADK-native runtime / underlying provider
    already accepts those schemas without modification.
    """

    if family is ProviderFamily.GOOGLE:
        result = _repair_gemini_schema(schema)
        if isinstance(result, dict):
            return result
        return dict(schema)
    return schema


__all__ = ["repair_tool_schema_for_provider"]
