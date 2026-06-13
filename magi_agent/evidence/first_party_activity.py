"""First-party activity evidence — versioned payloads + dispatch-seam builders.

Kernel capture mechanics (D6) for the dispatcher seam. WHAT counts as evidence
is declared by ``evidence_producer`` packs (the bundled
``openmagi.evidence-firstparty-activity`` pack registers the refs below); the
builders return nothing for refs that are not enabled, so removing/disabling
the pack genuinely disables capture. Fail-open by construction: builders never
raise on malformed outputs (defensive ``.get`` everywhere).
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Mapping, Sequence

from pydantic import BaseModel, ConfigDict, Field

from magi_agent.evidence.ledger import (
    _redact_public_summary_text,
    _sanitize_public_summary_value,
)
from magi_agent.evidence.types import EvidenceRecord
from magi_agent.tools.context import ToolContext
from magi_agent.tools.result import ToolResult

TOOL_CALL_REF = "evidence:toolCall@1"
SKILL_LOAD_REF = "evidence:skillLoad@1"
SUBAGENT_SPAWN_REF = "evidence:subagentSpawn@1"
FIRST_PARTY_ACTIVITY_REFS: tuple[str, ...] = (
    TOOL_CALL_REF,
    SKILL_LOAD_REF,
    SUBAGENT_SPAWN_REF,
)

_SKILL_LOADER_TOOL = "SkillLoader"
_SPAWN_AGENT_TOOL = "SpawnAgent"
_SUMMARY_MAX_CHARS = 400
_STATUS_TO_EVIDENCE_STATUS: Mapping[str, str] = {"ok": "ok", "error": "failed"}


class FirstPartyActivity(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    v: int = 1
    record_id: str = Field(alias="recordId")
    evidence_type: str = Field(alias="evidenceType")
    public_ref: str = Field(alias="publicRef")
    name: str
    status: str
    actor: str
    spawn_depth: int = Field(default=0, alias="spawnDepth")
    duration_ms: int = Field(default=0, alias="durationMs")
    error_code: str | None = Field(default=None, alias="errorCode")
    reason: str | None = None
    detail: Mapping[str, object] = Field(default_factory=dict)


def _sha256(value: object) -> str:
    try:
        payload = json.dumps(value, sort_keys=True, default=str)
    except (TypeError, ValueError):
        payload = str(value)
    return hashlib.sha256(payload.encode("utf-8", errors="replace")).hexdigest()


_LONG_VALUE_THRESHOLD = 64
_LONG_VALUE_REDACTED = "[redacted:long-value]"


def _redact_long_values(value: object) -> object:
    """Replace any string value whose length >= _LONG_VALUE_THRESHOLD with a
    redaction marker.  Long opaque strings in arg/result summaries are never
    needed — the full content is already represented by the sha256 digest
    fields.  Walk mappings and sequences recursively so nested secrets are also
    covered.
    """
    if isinstance(value, Mapping):
        return {k: _redact_long_values(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, Sequence)) and not isinstance(
        value, (str, bytes, bytearray)
    ):
        return [_redact_long_values(item) for item in value]
    if isinstance(value, (str, bytes, bytearray)) and len(value) >= _LONG_VALUE_THRESHOLD:
        return _LONG_VALUE_REDACTED
    return value


def _summary(value: object) -> str:
    # Use _sanitize_public_summary_value for Mapping inputs so that key-based
    # secret detection and string truncation run before the JSON encoding step,
    # preventing long secrets from surviving the raw-JSON cap.
    sanitized = _sanitize_public_summary_value(value)
    # Redact any remaining long string values (e.g. tokens under keys not in
    # _SECRET_FIELD_FRAGMENTS such as "auth").  Truncation is not redaction.
    sanitized = _redact_long_values(sanitized)
    try:
        text = json.dumps(sanitized, sort_keys=True, default=str)
    except (TypeError, ValueError):
        text = str(sanitized)
    return _redact_public_summary_text(text)[:_SUMMARY_MAX_CHARS]


def _record_id(context: ToolContext, evidence_type: str, name: str, index: int) -> str:
    key = ":".join(
        (
            context.session_id or "local",
            context.turn_id or "turn",
            context.tool_use_id or "call",
            evidence_type,
            name,
            str(index),
            str(time.monotonic_ns()),
        )
    )
    return "evd_" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:12]


def _base_kwargs(
    *,
    context: ToolContext,
    name: str,
    result: ToolResult,
) -> dict[str, object]:
    metadata = result.metadata if isinstance(result.metadata, Mapping) else {}
    reason = metadata.get("reason")
    return {
        "name": name,
        "status": str(result.status),
        "actor": "child" if context.spawn_depth > 0 else "main",
        "spawnDepth": context.spawn_depth,
        "durationMs": int(result.latency_ms or 0),
        "errorCode": result.error_code,
        "reason": str(reason) if isinstance(reason, str) else None,
    }


def _tool_call_activity(
    *,
    tool_name: str,
    arguments: Mapping[str, object],
    context: ToolContext,
    result: ToolResult,
) -> FirstPartyActivity:
    detail: dict[str, object] = {
        "argsSha256": _sha256(arguments),
        "argsSummary": _summary(arguments),
    }
    if result.output is not None:
        detail["resultSha256"] = _sha256(result.output)
        detail["resultSummary"] = _summary(result.output)
        try:
            detail["resultBytes"] = len(
                json.dumps(result.output, sort_keys=True, default=str).encode("utf-8")
            )
        except (TypeError, ValueError):
            detail["resultBytes"] = 0
    return FirstPartyActivity.model_validate(
        {
            **_base_kwargs(context=context, name=tool_name, result=result),
            "recordId": _record_id(context, "ToolCall", tool_name, 0),
            "evidenceType": "ToolCall",
            "publicRef": TOOL_CALL_REF,
            "detail": detail,
        }
    )


def _skill_load_activities(
    *,
    context: ToolContext,
    result: ToolResult,
) -> tuple[FirstPartyActivity, ...]:
    output = result.output if isinstance(result.output, Mapping) else {}
    loaded = output.get("loadedSkills")
    if not isinstance(loaded, (list, tuple)) or not loaded:
        return ()
    activities: list[FirstPartyActivity] = []
    for index, entry in enumerate(loaded):
        if not isinstance(entry, Mapping):
            continue
        activities.append(
            FirstPartyActivity.model_validate(
                {
                    **_base_kwargs(context=context, name=_SKILL_LOADER_TOOL, result=result),
                    "recordId": _record_id(context, "SkillLoad", _SKILL_LOADER_TOOL, index),
                    "evidenceType": "SkillLoad",
                    "publicRef": SKILL_LOAD_REF,
                    "detail": {
                        "skillPath": str(entry.get("path") or ""),
                        "skillSource": str(entry.get("source") or ""),
                        "bodyDigest": str(entry.get("bodyDigest") or ""),
                    },
                }
            )
        )
    return tuple(activities)


def _subagent_spawn_activity(
    *,
    arguments: Mapping[str, object],
    context: ToolContext,
    result: ToolResult,
) -> FirstPartyActivity:
    output = result.output if isinstance(result.output, Mapping) else {}
    detail: dict[str, object] = {
        "spawnStatus": str(output.get("status") or ""),
        "persona": str(output.get("persona") or arguments.get("persona") or ""),
        "promptDigest": str(output.get("promptDigest") or ""),
        "requestedDepth": context.spawn_depth,
        "liveChildRunnerAttached": bool(output.get("liveChildRunnerAttached", False)),
    }
    provider = str(arguments.get("provider") or "") or None
    model = str(arguments.get("model") or "") or None
    if provider is not None:
        detail["provider"] = provider
    if model is not None:
        detail["model"] = model
    return FirstPartyActivity.model_validate(
        {
            **_base_kwargs(context=context, name=_SPAWN_AGENT_TOOL, result=result),
            "recordId": _record_id(context, "SubagentSpawn", _SPAWN_AGENT_TOOL, 0),
            "evidenceType": "SubagentSpawn",
            "publicRef": SUBAGENT_SPAWN_REF,
            "detail": detail,
        }
    )


def build_first_party_activities(
    *,
    tool_name: str,
    arguments: Mapping[str, object],
    context: ToolContext,
    result: ToolResult,
    enabled_refs: tuple[str, ...],
) -> tuple[FirstPartyActivity, ...]:
    """Build 0..N activities for ONE dispatch outcome.

    Promotion: ``SkillLoader`` → N SkillLoad (one per loaded skill);
    ``SpawnAgent`` → 1 SubagentSpawn; everything else → 1 ToolCall. A promoted
    tool whose call failed (no usable output) falls back to a ToolCall record
    so blocked/failed attempts remain evidenced. Refs not enabled ⇒ ().

    Partial-ref semantics (pinned): (a) if only ``TOOL_CALL_REF`` is enabled,
    ``SkillLoader`` and ``SpawnAgent`` dispatches produce ZERO activities because
    their promotion paths require ``SKILL_LOAD_REF``/``SUBAGENT_SPAWN_REF``
    respectively, and the generic ToolCall branch explicitly excludes those two
    tool names; (b) if only ``SKILL_LOAD_REF`` is enabled and the SkillLoader
    call produced no ``loadedSkills``, ZERO activities are returned because the
    ToolCall fallback requires ``TOOL_CALL_REF``.
    """
    refs = frozenset(enabled_refs)
    if tool_name == _SKILL_LOADER_TOOL and SKILL_LOAD_REF in refs:
        skill_activities = _skill_load_activities(context=context, result=result)
        if skill_activities:
            return skill_activities
        if TOOL_CALL_REF in refs:
            return (
                _tool_call_activity(
                    tool_name=tool_name,
                    arguments=arguments,
                    context=context,
                    result=result,
                ),
            )
        return ()
    if tool_name == _SPAWN_AGENT_TOOL and SUBAGENT_SPAWN_REF in refs:
        return (
            _subagent_spawn_activity(
                arguments=arguments,
                context=context,
                result=result,
            ),
        )
    if TOOL_CALL_REF in refs and tool_name not in (_SKILL_LOADER_TOOL, _SPAWN_AGENT_TOOL):
        return (
            _tool_call_activity(
                tool_name=tool_name,
                arguments=arguments,
                context=context,
                result=result,
            ),
        )
    return ()


def to_evidence_record(activity: FirstPartyActivity) -> EvidenceRecord:
    return EvidenceRecord.model_validate(
        {
            "type": f"custom:FirstParty{activity.evidence_type}",
            "status": _STATUS_TO_EVIDENCE_STATUS.get(activity.status, "unknown"),
            "observedAt": time.time(),
            "source": {
                "kind": "tool_trace",
                "toolName": activity.name,
                "toolCallId": activity.record_id,
            },
            "fields": activity.model_dump(by_alias=True, mode="json"),
        }
    )
