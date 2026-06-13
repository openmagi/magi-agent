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
