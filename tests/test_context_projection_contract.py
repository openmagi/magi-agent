from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from openmagi_core_agent.runtime.context_projection import (
    ContextProjection,
    ContextProjectionMode,
    build_context_projection,
)


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "programmable_determinism"


def test_governed_context_projection_is_explicit_and_digest_recorded() -> None:
    projection = build_context_projection(
        projectionId="ctx-proj-001",
        mode="explicit",
        includedContextRefs=("source:snapshot-1", "artifact:calc-1"),
        excludedContextClasses=("raw_transcript", "child_raw_tool_log", "hidden_reasoning", "private_memory"),
        sourceDigests=("sha256:" + "1" * 64, "sha256:" + "2" * 64),
        tokenBudget=2048,
        byteBudget=8192,
        redactionStatus="redacted",
    )

    assert projection.model_visible_digest.startswith("sha256:")
    assert projection.mode == "explicit"
    assert "raw_transcript" in projection.excluded_context_classes


def test_governed_context_rejects_general_chat_history() -> None:
    with pytest.raises(ValidationError, match="general_chat_history"):
        build_context_projection(
            projectionId="ctx-proj-002",
            mode="general_chat_history",
            includedContextRefs=("transcript:all",),
            excludedContextClasses=(),
            sourceDigests=("sha256:" + "3" * 64,),
            tokenBudget=4096,
            byteBudget=16384,
            redactionStatus="redacted",
            governed=True,
        )


def test_child_agent_parent_visible_context_allows_only_sanitized_envelope_refs() -> None:
    projection = build_context_projection(
        projectionId="ctx-proj-child",
        mode="accumulate_verified",
        includedContextRefs=("child-envelope:abc", "evidence:review-1"),
        excludedContextClasses=("raw_child_transcript", "private_tool_trace", "hidden_reasoning"),
        sourceDigests=("sha256:" + "4" * 64,),
        tokenBudget=1024,
        byteBudget=4096,
        redactionStatus="redacted",
    )

    assert projection.parent_visible is True
    assert "raw_child_transcript" in projection.excluded_context_classes


def test_projection_mode_values_are_closed() -> None:
    assert set(ContextProjectionMode.__args__) == {
        "explicit",
        "last_step_only",
        "accumulate_verified",
        "general_chat_history",
    }


def test_context_projection_rejects_raw_context_refs_and_protected_fragments() -> None:
    with pytest.raises(ValidationError, match="includedContextRefs"):
        build_context_projection(
            projectionId="ctx-proj-raw",
            mode="explicit",
            includedContextRefs=("raw_transcript:full",),
            excludedContextClasses=("raw_transcript",),
            sourceDigests=("sha256:" + "5" * 64,),
            tokenBudget=1024,
            byteBudget=4096,
            redactionStatus="redacted",
        )
    with pytest.raises(ValidationError, match="protected"):
        build_context_projection(
            projectionId="ctx-proj-protected",
            mode="explicit",
            includedContextRefs=("source:" + "sess" + "ion-" + "to" + "ken",),
            excludedContextClasses=("raw_transcript",),
            sourceDigests=("sha256:" + "6" * 64,),
            tokenBudget=1024,
            byteBudget=4096,
            redactionStatus="redacted",
        )


def test_context_projection_rejects_generic_raw_ref_markers() -> None:
    for raw_ref in ("raw:full", "rawRef:full", "rawToolLog:full", "rawChildTranscript:full"):
        with pytest.raises(ValidationError, match="includedContextRefs"):
            build_context_projection(
                projectionId=f"ctx-proj-{raw_ref.split(':', maxsplit=1)[0].lower()}",
                mode="explicit",
                includedContextRefs=(raw_ref,),
                excludedContextClasses=("raw_transcript",),
                sourceDigests=("sha256:" + "6" * 64,),
                tokenBudget=1024,
                byteBudget=4096,
                redactionStatus="redacted",
            )


def test_context_projection_rejects_protected_excluded_context_classes() -> None:
    with pytest.raises(ValidationError, match="excludedContextClasses"):
        build_context_projection(
            projectionId="ctx-proj-excluded-protected",
            mode="explicit",
            includedContextRefs=("source:snapshot-1",),
            excludedContextClasses=("author" + "ization: bearer " + "se" + "cret",),
            sourceDigests=("sha256:" + "6" * 64,),
            tokenBudget=1024,
            byteBudget=4096,
            redactionStatus="redacted",
        )


def test_context_projection_model_copy_update_is_disabled() -> None:
    projection = build_context_projection(
        projectionId="ctx-proj-copy",
        mode="explicit",
        includedContextRefs=("source:snapshot-1",),
        excludedContextClasses=("raw_transcript",),
        sourceDigests=("sha256:" + "7" * 64,),
        tokenBudget=1024,
        byteBudget=4096,
        redactionStatus="redacted",
    )

    with pytest.raises(ValueError, match="model_copy update"):
        projection.model_copy(update={"mode": "general_chat_history"})


def test_context_projection_rejects_coerced_boolean_fields() -> None:
    valid = build_context_projection(
        projectionId="ctx-proj-bool",
        mode="explicit",
        includedContextRefs=("source:snapshot-1",),
        excludedContextClasses=("raw_transcript",),
        sourceDigests=("sha256:" + "8" * 64,),
        tokenBudget=1024,
        byteBudget=4096,
        redactionStatus="redacted",
    ).model_dump(by_alias=True, mode="json")

    with pytest.raises(ValidationError, match="governed"):
        ContextProjection.model_validate({**valid, "governed": "true"})
    with pytest.raises(ValidationError, match="parentVisible"):
        ContextProjection.model_validate({**valid, "parentVisible": "true"})


def test_context_projection_fixture_validates_and_is_digest_only() -> None:
    payload = json.loads((FIXTURE_DIR / "context_projection_explicit.json").read_text())
    projection = build_context_projection(**payload["builderInput"])

    assert projection.model_visible_digest == payload["expectedModelVisibleDigest"]
    encoded = json.dumps(_string_values(payload), sort_keys=True).lower()
    forbidden_fragments = (
        "pro" + "mpt",
        "author" + "ization",
        "coo" + "kie",
        "to" + "ken",
        "sess" + "ion",
        "priv" + "ate",
    )
    assert all(fragment not in encoded for fragment in forbidden_fragments)


def _string_values(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        values: list[str] = []
        for item in value.values():
            values.extend(_string_values(item))
        return values
    if isinstance(value, list):
        values = []
        for item in value:
            values.extend(_string_values(item))
        return values
    return []
