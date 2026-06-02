from __future__ import annotations

import subprocess
import sys

from magi_agent.runtime.structured_output_boundary import (
    StructuredOutputBoundary,
    StructuredOutputConfig,
    StructuredOutputRequest,
)


SCORE_SCHEMA = {
    "type": "object",
    "required": ["answer", "score"],
    "additionalProperties": False,
    "properties": {
        "answer": {"type": "string"},
        "score": {"type": "number"},
    },
}


def _request(
    raw_output: str,
    *,
    final: bool = True,
    schema: dict[str, object] | None = None,
) -> StructuredOutputRequest:
    return StructuredOutputRequest(
        requestId="req-structured-1",
        turnId="turn-1",
        schemaName="ScoreSchema",
        schema=schema or SCORE_SCHEMA,
        rawOutput=raw_output,
        isFinal=final,
    )


def test_structured_output_boundary_is_disabled_by_default() -> None:
    decision = StructuredOutputBoundary(StructuredOutputConfig()).validate(
        _request('{"answer": "ok", "score": 1}')
    )

    assert decision.status == "disabled"
    assert decision.reason_codes == ("structured_output_boundary_disabled",)
    assert set(decision.authority_flags.model_dump(by_alias=True).values()) == {False}


def test_structured_output_validates_final_json_schema_without_authority() -> None:
    decision = StructuredOutputBoundary(StructuredOutputConfig(enabled=True)).validate(
        _request('{"answer": "ok", "score": 0.9}')
    )
    projection = decision.public_projection()

    assert decision.status == "valid"
    assert decision.parsed_output == {"answer": "ok", "score": 0.9}
    assert projection["authorityFlags"]["modelCalled"] is False
    assert projection["authorityFlags"]["runnerInvoked"] is False
    assert projection["parsedOutput"] == {"answer": "ok", "score": 0.9}


def test_structured_output_blocks_malformed_partial_and_schema_mismatch() -> None:
    boundary = StructuredOutputBoundary(StructuredOutputConfig(enabled=True))

    malformed = boundary.validate(_request('{"answer": "ok"', final=True))
    partial = boundary.validate(_request('{"answer": "ok"', final=False))
    mismatch = boundary.validate(_request('{"answer": "ok", "score": "high"}'))
    extra = boundary.validate(_request('{"answer": "ok", "score": 1, "extra": true}'))

    assert malformed.status == "repair_required"
    assert malformed.reason_codes == ("malformed_structured_output",)
    assert partial.status == "partial"
    assert partial.reason_codes == ("partial_structured_output_pending",)
    assert mismatch.status == "blocked"
    assert mismatch.reason_codes == ("structured_output_schema_mismatch",)
    assert extra.status == "blocked"
    assert extra.reason_codes == ("structured_output_schema_mismatch",)


def test_structured_output_supports_enum_arrays_and_nested_objects() -> None:
    schema = {
        "type": "object",
        "required": ["status", "items", "meta"],
        "additionalProperties": False,
        "properties": {
            "status": {"type": "string", "enum": ["ok", "blocked"]},
            "items": {"type": "array", "items": {"type": "string"}},
            "meta": {
                "type": "object",
                "required": ["count"],
                "additionalProperties": False,
                "properties": {"count": {"type": "integer"}},
            },
        },
    }

    decision = StructuredOutputBoundary(StructuredOutputConfig(enabled=True)).validate(
        _request(
            '{"status": "ok", "items": ["a", "b"], "meta": {"count": 2}}',
            schema=schema,
        )
    )

    assert decision.status == "valid"


def test_structured_output_redacts_private_payloads_and_raw_output_from_projection() -> None:
    decision = StructuredOutputBoundary(StructuredOutputConfig(enabled=True)).validate(
        _request(
            '{"answer": "raw_tool_log /Users/kevin/private sk-structured-secret", "score": 1}'
        )
    )
    projection = decision.public_projection()
    encoded = str(projection)

    assert decision.status == "blocked"
    assert decision.reason_codes == ("private_structured_output_blocked",)
    assert "raw_tool_log" not in encoded
    assert "/Users/kevin" not in encoded
    assert "sk-structured-secret" not in encoded
    assert "rawOutput" not in projection


def test_structured_output_boundary_has_no_live_imports() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import importlib
import sys

importlib.import_module("magi_agent.runtime.structured_output_boundary")
forbidden = (
    "google.adk.runners",
    "requests",
    "httpx",
    "subprocess",
)
loaded = [name for name in forbidden if name in sys.modules]
if loaded:
    raise AssertionError(f"forbidden modules loaded: {loaded}")
""",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
