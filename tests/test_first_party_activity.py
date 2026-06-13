from __future__ import annotations

import json

from magi_agent.evidence.first_party_activity import (
    FIRST_PARTY_ACTIVITY_REFS,
    SKILL_LOAD_REF,
    SUBAGENT_SPAWN_REF,
    TOOL_CALL_REF,
    FirstPartyActivity,
    build_first_party_activities,
    to_evidence_record,
)
from magi_agent.tools.context import ToolContext
from magi_agent.tools.result import ToolResult


def _context(**overrides: object) -> ToolContext:
    payload: dict[str, object] = {
        "botId": "bot-1",
        "sessionId": "sess-1",
        "turnId": "turn-1",
        "toolUseId": "call-1",
    }
    payload.update(overrides)
    return ToolContext.model_validate(payload)


def _build(
    tool_name: str,
    result: ToolResult,
    *,
    arguments: dict[str, object] | None = None,
    refs: tuple[str, ...] = FIRST_PARTY_ACTIVITY_REFS,
    context: ToolContext | None = None,
):
    return build_first_party_activities(
        tool_name=tool_name,
        arguments=arguments or {"q": "hello"},
        context=context or _context(),
        result=result,
        enabled_refs=refs,
    )


def test_tool_call_activity_ok() -> None:
    result = ToolResult(status="ok", output={"answer": 42}, latencyMs=7)
    (activity,) = _build("web_search", result)
    assert activity.v == 1
    assert activity.evidence_type == "ToolCall"
    assert activity.public_ref == TOOL_CALL_REF
    assert activity.name == "web_search"
    assert activity.status == "ok"
    assert activity.actor == "main"
    assert activity.spawn_depth == 0
    assert activity.duration_ms == 7
    assert activity.record_id.startswith("evd_")
    assert activity.detail["argsSha256"]
    assert activity.detail["resultSha256"]
    assert int(str(activity.detail["resultBytes"])) > 0


def test_blocked_call_is_recorded_with_reason() -> None:
    result = ToolResult(status="blocked", metadata={"reason": "permission denied"})
    (activity,) = _build("Bash", result)
    assert activity.status == "blocked"
    assert activity.reason == "permission denied"
    assert activity.duration_ms == 0


def test_needs_approval_and_error_statuses_recorded() -> None:
    for status, error_code in (("needs_approval", None), ("error", "tool_not_found")):
        result = ToolResult(status=status, errorCode=error_code, metadata={"reason": "x"})
        (activity,) = _build("Bash", result)
        assert activity.status == status
        assert activity.error_code == error_code


def test_disabled_ref_yields_nothing() -> None:
    result = ToolResult(status="ok", output={})
    assert _build("web_search", result, refs=(SKILL_LOAD_REF,)) == ()


def test_skill_loader_promotes_to_skill_load_per_loaded_skill() -> None:
    result = ToolResult(
        status="ok",
        output={
            "skills": ["bundled/web-research", "skills/my-skill"],
            "skillCount": 2,
            "loadedSkills": [
                {
                    "path": "bundled/web-research",
                    "source": "bundled",
                    "body": "# A",
                    "bodyDigest": "d1",
                },
                {
                    "path": "skills/my-skill",
                    "source": "workspace",
                    "body": "# B",
                    "bodyDigest": "d2",
                },
            ],
            "loadedSkillCount": 2,
        },
    )
    activities = _build("SkillLoader", result)
    assert [a.evidence_type for a in activities] == ["SkillLoad", "SkillLoad"]
    assert activities[0].public_ref == SKILL_LOAD_REF
    assert activities[0].detail["skillPath"] == "bundled/web-research"
    assert activities[0].detail["skillSource"] == "bundled"
    assert activities[0].detail["bodyDigest"] == "d1"
    assert activities[0].name == "SkillLoader"


def test_skill_loader_error_falls_back_to_tool_call() -> None:
    result = ToolResult(status="error", errorCode="boom")
    (activity,) = _build("SkillLoader", result)
    assert activity.evidence_type == "ToolCall"


def test_spawn_agent_promotes_to_subagent_spawn() -> None:
    result = ToolResult(
        status="blocked",
        errorCode="live_child_runner_disabled",
        output={
            "status": "not_attached",
            "persona": "general",
            "promptDigest": "abc123",
            "spawnDepth": 0,
            "liveChildRunnerAttached": False,
        },
    )
    (activity,) = _build(
        "SpawnAgent",
        result,
        arguments={
            "prompt": "do x",
            "persona": "general",
            "provider": "anthropic",
            "model": "claude",
        },
    )
    assert activity.evidence_type == "SubagentSpawn"
    assert activity.public_ref == SUBAGENT_SPAWN_REF
    assert activity.detail["promptDigest"] == "abc123"
    assert activity.detail["provider"] == "anthropic"
    assert activity.detail["model"] == "claude"
    assert activity.detail["liveChildRunnerAttached"] is False


def test_child_actor_at_spawn_depth() -> None:
    result = ToolResult(status="ok", output={})
    (activity,) = _build("web_search", result, context=_context(spawnDepth=2))
    assert activity.actor == "child"
    assert activity.spawn_depth == 2


def test_summaries_are_redacted_and_capped() -> None:
    secret = "xoxb-" + "1234567890-" * 30  # assembled at runtime — GH push protection
    result = ToolResult(status="ok", output={"token": secret})
    (activity,) = _build("web_search", result, arguments={"auth": secret})
    detail_json = json.dumps(dict(activity.detail))
    assert secret not in detail_json
    # Truncation is not redaction: assert no actionable prefix leaks either
    assert secret[:24] not in detail_json
    assert len(str(activity.detail["argsSummary"])) <= 400
    assert len(str(activity.detail["resultSummary"])) <= 400


def test_long_bytes_and_bytearray_redacted_in_result_summary() -> None:
    # Assemble a secret at runtime so GH push protection cannot flag the literal.
    prefix = "xoxb-"
    padding = "a" * 60  # prefix(5) + padding(60) = 65 bytes >= threshold of 64
    secret_bytes = (prefix + padding).encode("utf-8")
    secret_bytearray = bytearray(secret_bytes)
    # bytes secret under a non-secret key
    result_bytes = ToolResult(status="ok", output={"blob": secret_bytes})
    (activity_bytes,) = _build("web_search", result_bytes)
    detail_json_bytes = json.dumps(dict(activity_bytes.detail))
    # The raw secret must not appear in resultSummary
    assert (prefix + padding) not in detail_json_bytes
    assert detail_json_bytes.count("[redacted:long-value]") >= 1
    # bytearray secret under a non-secret key
    result_ba = ToolResult(status="ok", output={"blob": secret_bytearray})
    (activity_ba,) = _build("web_search", result_ba)
    detail_json_ba = json.dumps(dict(activity_ba.detail))
    assert (prefix + padding) not in detail_json_ba
    assert detail_json_ba.count("[redacted:long-value]") >= 1


def test_summary_build_is_fast_on_long_delimiterless_output() -> None:
    """Regression: ReDoS / catastrophic backtracking on the default-ON hot path.

    The ledger ``_UNQUOTED_*_SECRET_RE`` patterns backtrack quadratically on long
    delimiter-free strings (minified JS, base64, hex dumps, single-line logs).
    Before the FIX, an 8KB unbroken output ran the regex sanitizers over the FULL
    value and took ~12.5s, freezing the asyncio event loop. The build must now
    redact long values structurally (cheap length check) BEFORE any regex sees
    them, so the build completes near-instantly. Generous 1.0s bound catches a
    regression without being flaky.
    """
    import time

    big = "A" * 8192  # 8KB unbroken, no delimiters — triggers backtracking pre-fix
    result = ToolResult(status="ok", output={"stdout": big})
    start = time.perf_counter()
    (activity,) = _build("Bash", result, arguments={"cmd": big})
    elapsed = time.perf_counter() - start
    assert elapsed < 1.0, f"build took {elapsed:.3f}s — ReDoS regression"
    # The long opaque value must not survive into the bounded preview.
    detail_json = json.dumps(dict(activity.detail))
    assert big not in detail_json
    assert "[redacted:long-value]" in detail_json


def test_hyphenated_credential_key_short_value_redacted_to_disk() -> None:
    """Regression: credential-key value leak to the default-ON JSONL disk sink.

    A short (8-63 char) value with no recognizable token format, nested under a
    hyphenated credential key such as ``set-cookie``, used to survive into
    ``argsSummary``/``resultSummary`` because ``_summary`` did not pass
    ``include_public_credential_keys=True``. It must now be redacted.
    The secret is assembled at runtime (never a literal) for GH push protection.
    """
    cred_key = "set-" + "cookie"
    secret = "session" + "id=abc123def456ghi"  # short, hyphenated key, no token shape
    result = ToolResult(status="ok", output={"headers": {cred_key: secret}})
    (activity,) = _build("web_search", result, arguments={"nested": {cred_key: secret}})
    detail_json = json.dumps(dict(activity.detail))
    assert "abc123def456ghi" not in detail_json
    assert "[redacted]" in detail_json


def test_partial_ref_only_tool_call_excludes_skill_loader_and_spawn_agent() -> None:
    # Case (a): only TOOL_CALL_REF enabled — SkillLoader and SpawnAgent yield ZERO activities.
    only_tool_call = (TOOL_CALL_REF,)
    skill_result = ToolResult(
        status="ok",
        output={
            "loadedSkills": [
                {"path": "bundled/web-research", "source": "bundled", "bodyDigest": "d1"}
            ]
        },
    )
    assert _build("SkillLoader", skill_result, refs=only_tool_call) == ()
    spawn_result = ToolResult(
        status="ok",
        output={
            "status": "ok",
            "persona": "general",
            "promptDigest": "abc",
            "liveChildRunnerAttached": True,
        },
    )
    assert _build("SpawnAgent", spawn_result, refs=only_tool_call) == ()


def test_partial_ref_only_skill_load_no_loaded_skills_yields_nothing() -> None:
    # Case (b): only SKILL_LOAD_REF enabled, SkillLoader call returned no loadedSkills
    # — ZERO activities because ToolCall fallback requires TOOL_CALL_REF.
    only_skill_load = (SKILL_LOAD_REF,)
    result = ToolResult(status="error", errorCode="skill_not_found")
    assert _build("SkillLoader", result, refs=only_skill_load) == ()


def test_to_evidence_record_projection() -> None:
    result = ToolResult(status="ok", output={}, latencyMs=3)
    (activity,) = _build("web_search", result)
    record = to_evidence_record(activity)
    assert record.type == "custom:FirstPartyToolCall"
    assert record.status == "ok"
    assert record.source.kind == "tool_trace"
    assert record.source.tool_name == "web_search"
    assert record.fields["recordId"] == activity.record_id
    # blocked maps into the closed EvidenceStatus vocabulary
    blocked = to_evidence_record(_build("Bash", ToolResult(status="blocked"))[0])
    assert blocked.status == "unknown"
    failed = to_evidence_record(_build("Bash", ToolResult(status="error"))[0])
    assert failed.status == "failed"


def test_detail_is_frozen_and_model_dump_still_works() -> None:
    """Item B: detail must be a MappingProxyType (immutable); model_dump must still work."""
    import pytest
    from types import MappingProxyType

    result = ToolResult(status="ok", output={"r": 1})
    (activity,) = _build("web_search", result, arguments={"q": "hello"})
    # detail is a frozen MappingProxyType — mutation raises TypeError
    with pytest.raises(TypeError):
        activity.detail["tampered"] = "INJECTED"  # type: ignore[index]
    assert isinstance(activity.detail, MappingProxyType)
    # model_dump(mode="json") must succeed and return a plain dict
    dumped = activity.model_dump(mode="json", by_alias=True)
    assert isinstance(dumped["detail"], dict)
    # The serialized detail carries the expected keys
    assert "argsSha256" in dumped["detail"]


def test_default_constructed_detail_is_frozen() -> None:
    """Pre-step A: a default-constructed detail (empty dict) must also be a frozen MappingProxyType.

    Without ``validate_default=True`` on ``model_config``, pydantic skips the
    ``_freeze_detail`` validator for the default value, leaving a mutable plain
    dict that bypasses the immutability guarantee for the span between
    construction and first use.
    """
    import pytest
    from types import MappingProxyType

    activity = FirstPartyActivity.model_validate(
        {
            "recordId": "evd_abc",
            "evidenceType": "ToolCall",
            "publicRef": TOOL_CALL_REF,
            "name": "web_search",
            "status": "ok",
            "actor": "main",
            # deliberately omit "detail" to trigger default
        }
    )
    assert isinstance(activity.detail, MappingProxyType), (
        f"Expected MappingProxyType, got {type(activity.detail)}"
    )
    with pytest.raises(TypeError):
        activity.detail["injected"] = "bad"  # type: ignore[index]
