from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from magi_agent.transport.tool_preview import sanitize_tool_preview


ToolEvidenceKind = Literal["tool_call", "tool_result", "tool_error", "tool_timeout"]
ToolEvidenceStatus = Literal["ok", "error", "denied", "not_found", "not_exposed"]
PolicyFailureReason = Literal["denied", "not_found", "not_exposed", "missing_handler"]

_PRIVATE_PATH_RE = re.compile(
    r"(?:"
    r"/Users(?:/[^\s,;}\"']*)?"
    r"|/home(?:/[^\s,;}\"']*)?"
    r"|/private/var(?:/[^\s,;}\"']*)?"
    r"|/workspace(?:/[^\s,;}\"']*)?"
    r"|/data/bots(?:/[^\s,;}\"']*)?"
    r"|/var/lib/kubelet(?:/[^\s,;}\"']*)?"
    r"|/tmp/(?:opencode-inspect|openmagi-inspect|openmagi-workspace-[^/\s,;}\"']+|"
    r"[^/\s,;}\"']*(?:workspace|inspect)[^/\s,;}\"']*)(?:/[^\s,;}\"']*)?"
    r")"
)
_PRIVATE_SUMMARY_KEY_RE = re.compile(
    r"(?:"
    r"authorization\s*:|"
    r"\bbearer\s+[A-Za-z0-9._~+/=-]+|"
    r"\bcookie\b|"
    r"\bsid=[A-Za-z0-9._-]+|"
    r"\bsk-[A-Za-z0-9._-]+|"
    r"gh[opusr]_[A-Za-z0-9_]+|"
    r"github_pat_[A-Za-z0-9_]+|"
    r"xox[a-z]-[A-Za-z0-9._-]+|"
    r"AKIA[0-9A-Z]{8,}|"
    r"AIza[A-Za-z0-9_-]+|"
    r"/Users(?:/[^\s,;}\"']*)?|"
    r"/home(?:/[^\s,;}\"']*)?|"
    r"/workspace(?:/[^\s,;}\"']*)?|"
    r"/data/bots(?:/[^\s,;}\"']*)?|"
    r"/var/lib/kubelet(?:/[^\s,;}\"']*)?|"
    r"raw[_ -]?(?:tool|child|prompt|transcript|output|result|log|args)|"
    r"hidden[_ -]?reasoning|chain[_ -]?of[_ -]?thought"
    r")",
    re.IGNORECASE,
)
_PATCH_BODY_RE = re.compile(
    r"\*\*\* Begin Patch[\s\S]*?(?:\*\*\* End Patch|$)",
    re.IGNORECASE,
)
_MAX_SUMMARY_LENGTH = 240
_POLICY_ERROR_CODES: dict[PolicyFailureReason, str] = {
    "denied": "tool_denied",
    "not_found": "tool_not_found",
    "not_exposed": "tool_not_exposed",
    "missing_handler": "tool_missing_handler",
}
_POLICY_STATUSES: dict[PolicyFailureReason, ToolEvidenceStatus] = {
    "denied": "denied",
    "not_found": "not_found",
    "not_exposed": "not_exposed",
    "missing_handler": "not_found",
}


class ToolEvidenceRecord(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    kind: ToolEvidenceKind
    tool_call_id: str = Field(alias="toolCallId")
    tool_id: str = Field(alias="toolId")
    tool_name: str = Field(alias="toolName")
    observed_at: int | float = Field(alias="observedAt")
    terminal: bool
    executed: bool
    status: ToolEvidenceStatus
    arg_summary: Mapping[str, object] = Field(default_factory=dict, alias="argSummary")
    result_summary: Mapping[str, object] = Field(
        default_factory=dict,
        alias="resultSummary",
    )
    args_hash: str | None = Field(default=None, alias="argsHash")
    result_hash: str | None = Field(default=None, alias="resultHash")
    error_code: str | None = Field(default=None, alias="errorCode")
    error_message: str | None = Field(default=None, alias="errorMessage")
    duration_ms: int | None = Field(default=None, alias="durationMs")

    @field_validator("arg_summary", "result_summary", mode="before")
    @classmethod
    def _sanitize_summary(cls, value: object) -> Mapping[str, object]:
        if isinstance(value, Mapping):
            return _safe_summary(value)
        return {}

    @field_validator("error_message")
    @classmethod
    def _sanitize_error_message(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _sanitize_public_text(value)


class ToolEvidenceBoundary:
    def record_pair(
        self,
        *,
        tool_call_id: str,
        tool_id: str,
        tool_name: str,
        args: object,
        status: Literal["ok", "error"],
        result: object,
        duration_ms: int,
        observed_at: int | float,
    ) -> tuple[ToolEvidenceRecord, ToolEvidenceRecord]:
        return (
            build_tool_call_evidence(
                tool_call_id=tool_call_id,
                tool_id=tool_id,
                tool_name=tool_name,
                args=args,
                observed_at=observed_at,
            ),
            build_tool_result_evidence(
                tool_call_id=tool_call_id,
                tool_id=tool_id,
                tool_name=tool_name,
                status=status,
                result=result,
                duration_ms=duration_ms,
                observed_at=observed_at,
            ),
        )


def build_tool_call_evidence(
    *,
    tool_call_id: str,
    tool_id: str,
    tool_name: str,
    args: object,
    observed_at: int | float,
) -> ToolEvidenceRecord:
    return ToolEvidenceRecord(
        kind="tool_call",
        toolCallId=tool_call_id,
        toolId=tool_id,
        toolName=tool_name,
        observedAt=observed_at,
        terminal=False,
        executed=False,
        status="ok",
        argSummary=_safe_summary(args),
        argsHash=_hash_public_ref(args),
    )


def build_tool_result_evidence(
    *,
    tool_call_id: str,
    tool_id: str,
    tool_name: str,
    status: Literal["ok", "error"],
    result: object,
    duration_ms: int,
    observed_at: int | float,
) -> ToolEvidenceRecord:
    return ToolEvidenceRecord(
        kind="tool_result",
        toolCallId=tool_call_id,
        toolId=tool_id,
        toolName=tool_name,
        observedAt=observed_at,
        terminal=True,
        executed=True,
        status=status,
        resultSummary=_safe_result_summary(result, status=status),
        resultHash=_hash_public_ref(result),
        durationMs=duration_ms,
    )


def build_denied_tool_error_evidence(
    *,
    tool_call_id: str,
    tool_id: str,
    tool_name: str,
    reason: PolicyFailureReason,
    message: str,
    observed_at: int | float,
) -> ToolEvidenceRecord:
    return ToolEvidenceRecord(
        kind="tool_error",
        toolCallId=tool_call_id,
        toolId=tool_id,
        toolName=tool_name,
        observedAt=observed_at,
        terminal=True,
        executed=False,
        status=_POLICY_STATUSES[reason],
        errorCode=_POLICY_ERROR_CODES[reason],
        errorMessage=_sanitize_public_text(message),
    )


def build_tool_exception_evidence(
    *,
    tool_call_id: str,
    tool_id: str,
    tool_name: str,
    error: BaseException,
    duration_ms: int,
    observed_at: int | float,
) -> ToolEvidenceRecord:
    return ToolEvidenceRecord(
        kind="tool_error",
        toolCallId=tool_call_id,
        toolId=tool_id,
        toolName=tool_name,
        observedAt=observed_at,
        terminal=True,
        executed=True,
        status="error",
        errorCode="tool_threw",
        errorMessage="[redacted-error]",
        resultSummary=_exception_summary(error),
        durationMs=duration_ms,
    )


def build_tool_timeout_evidence(
    *,
    tool_call_id: str,
    tool_id: str,
    tool_name: str,
    timeout_ms: int,
    duration_ms: int,
    observed_at: int | float,
) -> ToolEvidenceRecord:
    return ToolEvidenceRecord(
        kind="tool_timeout",
        toolCallId=tool_call_id,
        toolId=tool_id,
        toolName=tool_name,
        observedAt=observed_at,
        terminal=True,
        executed=True,
        status="error",
        errorCode="tool_timeout",
        resultSummary={"timeoutMs": timeout_ms},
        durationMs=duration_ms,
    )


def _safe_summary(value: object) -> dict[str, object]:
    if isinstance(value, Mapping):
        summary: dict[str, object] = {}
        for key, nested in value.items():
            key_text = str(key)
            if _PRIVATE_SUMMARY_KEY_RE.search(key_text):
                summary[_redacted_summary_key(key_text)] = "[redacted]"
                continue
            normalized = key_text.replace("-", "_").lower()
            if _is_secret_key(normalized):
                summary[key_text] = "[redacted]"
            elif normalized in {"command", "cmd", "shell"}:
                summary["commandPreview"] = "[redacted-command]"
            elif normalized in {
                "patch",
                "patch_body",
                "diff",
                "diff_text",
                "difftext",
                "diff_ref",
                "diffref",
                "fixture_diff",
                "fixturediff",
                "fixture_diff_ref",
                "fixturediffref",
                "body",
            }:
                summary[f"{key_text}Preview"] = "[redacted-body]"
            elif normalized in {"prompt"}:
                summary[f"{key_text}Preview"] = "[redacted-prompt]"
            elif normalized in {
                "logs",
                "log",
                "stdout",
                "stderr",
                "output",
                "llmoutput",
                "llm_output",
                "transcriptoutput",
                "transcript_output",
            }:
                summary[f"{key_text}Preview"] = "[redacted-output]"
            elif normalized.endswith("path") or normalized in {"path", "file"}:
                summary[key_text] = _sanitize_public_text(str(nested))
            else:
                sanitized = _safe_summary_value(nested)
                if sanitized is not None:
                    summary[key_text] = sanitized
        return summary
    sanitized = _safe_summary_value(value)
    return {"preview": sanitized} if sanitized is not None else {}


def _safe_result_summary(
    value: object,
    *,
    status: Literal["ok", "error"],
) -> dict[str, object]:
    if isinstance(value, str):
        return {
            "type": "str",
            "size": len(value),
            "sha256": _hash_public_ref(value),
            "preview": "[redacted-error]" if status == "error" else "[redacted-output]",
        }
    return _safe_summary(value)


def _exception_summary(error: BaseException) -> dict[str, object]:
    message = str(error)
    return {
        "exceptionType": type(error).__name__,
        "messageSize": len(message),
        "messageHash": _hash_public_ref(message),
        "preview": "[redacted-error]",
    }


def _safe_summary_value(value: object) -> object:
    if isinstance(value, Mapping):
        return _safe_summary(value)
    if isinstance(value, list | tuple):
        items = [_safe_summary_value(item) for item in value[:5]]
        return [item for item in items if item is not None]
    if isinstance(value, str):
        return _sanitize_public_text(value)
    if isinstance(value, bool | int | float) or value is None:
        return value
    return _sanitize_public_text(str(value))


def _sanitize_public_text(value: str) -> str:
    redacted = sanitize_tool_preview(value)
    redacted = _PATCH_BODY_RE.sub("[redacted-body]", redacted)
    redacted = _PRIVATE_PATH_RE.sub("[redacted-path]", redacted)
    redacted = redacted.replace("child prompt", "[redacted-prompt]")
    if len(redacted) > _MAX_SUMMARY_LENGTH:
        return f"{redacted[: _MAX_SUMMARY_LENGTH - 3]}..."
    return redacted


def _hash_public_ref(value: object) -> str:
    material = json.dumps(value, sort_keys=True, default=repr, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(material.encode("utf-8")).hexdigest()


def _is_secret_key(normalized_key: str) -> bool:
    compact = normalized_key.replace("_", "")
    return any(
        fragment in normalized_key or fragment in compact
        for fragment in (
            "authorization",
            "cookie",
            "apikey",
            "api_key",
            "secret",
            "token",
            "password",
            "privatekey",
            "private_key",
            "servicekey",
            "service_key",
            "service_role_key",
            "credential",
            "credential_id",
            "credentials",
            "key",
        )
    )


def _redacted_summary_key(value: str) -> str:
    digest = hashlib.sha1(value.encode("utf-8")).hexdigest()[:12]
    return f"redactedKey:{digest}"


def build_coding_mutation_receipt_summary(
    *,
    tool_call_id: str,
    tool_name: str,
    receipt_status: str,
    action: str,
    input_digest: str,
    output_digest: str,
    workspace_digest: str,
    observed_at: int | float,
) -> ToolEvidenceRecord:
    """Build a tool-evidence record from a coding mutation receipt.

    This bridges the coding_tool_receipts boundary into the ToolEvidenceBoundary
    so mutation receipts appear in the evidence ledger as ``tool_result`` records
    with ``executed=True``.

    Default-off: callers must check receipt existence before calling.
    """
    status: ToolEvidenceStatus = "ok" if receipt_status == "success" else "error"
    return ToolEvidenceRecord(
        kind="tool_result",
        toolCallId=tool_call_id,
        toolId=tool_name,
        toolName=tool_name,
        observedAt=observed_at,
        terminal=True,
        executed=receipt_status != "blocked",
        status=status,
        resultSummary={
            "action": action,
            "receiptStatus": receipt_status,
            "inputDigest": input_digest,
            "outputDigest": output_digest,
            "workspaceDigest": workspace_digest,
            "productionWorkspaceMutationAllowed": False,
        },
        resultHash=_hash_public_ref(
            f"{receipt_status}|{input_digest}|{output_digest}|{workspace_digest}"
        ),
    )
