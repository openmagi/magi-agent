"""``_json_schema_to_genai_schema`` must carry string enum constraints.

Live failure observed on the τ-bench airline harness: tau-bench's
``book_reservation`` declares ``flight_type: {"enum": ["one_way",
"round_trip"]}``, but the converted genai ``Schema`` dropped ``enum``
entirely, so the model never saw the valid values and emitted
``"one way"`` — silently corrupting the write. Any tool whose schema
flows through this conversion is affected.

Non-string enums remain out of scope here: only the raw
``parameters_json_schema`` passthrough can carry them (see the
provider-repair block in ``tool_adapter.py``), and the typed ``Schema``
path rejects them at the wire.
"""
from __future__ import annotations

from magi_agent.adk_bridge.tool_adapter import _json_schema_to_genai_schema


def test_string_enum_is_carried_to_genai_schema():
    schema = {
        "type": "object",
        "properties": {
            "flight_type": {"type": "string", "enum": ["one_way", "round_trip"]},
        },
        "required": ["flight_type"],
    }
    converted = _json_schema_to_genai_schema(schema)
    assert converted.properties["flight_type"].enum == ["one_way", "round_trip"]


def test_enum_is_carried_inside_array_items():
    schema = {
        "type": "array",
        "items": {"type": "string", "enum": ["yes", "no"]},
    }
    converted = _json_schema_to_genai_schema(schema)
    assert converted.items.enum == ["yes", "no"]


def test_non_string_enum_is_not_forwarded_to_typed_schema():
    schema = {"type": "integer", "enum": [1, 2, 3]}
    converted = _json_schema_to_genai_schema(schema)
    assert converted.enum is None


def test_schema_without_enum_unchanged():
    schema = {"type": "string", "description": "d"}
    converted = _json_schema_to_genai_schema(schema)
    assert converted.enum is None
    assert converted.description == "d"
