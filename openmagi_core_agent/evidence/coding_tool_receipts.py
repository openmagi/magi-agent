"""PR3 — ToolHost Coding Mutation Receipt Boundary.

Emits structured receipts when ToolHost-dispatched coding mutations execute.
Receipts include action, exposed tool name, tool call digest, sandbox/workspace
digest, and public-safe metadata.

A model cannot synthesize mutation evidence by text alone — only the boundary
can produce a valid ``CodingToolReceiptRecord``.

All features are default-off unless explicitly enabled.
``productionWorkspaceMutationAllowed`` is hardcoded ``False``.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator

from openmagi_core_agent.tools.result import ToolResult


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CodingToolReceiptStatus = Literal["success", "error", "blocked"]

_CODING_MUTATION_TOOLS: frozenset[str] = frozenset({
    "FileEdit",
    "FileWrite",
    "PatchApply",
    "Bash",
})

_TOOL_ACTION_MAP: dict[str, str] = {
    "FileEdit": "edit",
    "FileWrite": "write",
    "PatchApply": "patch",
    "Bash": "execute",
}

_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    hide_input_in_errors=True,
)

_PRIVATE_DATA_RE = re.compile(
    r"(?:"
    r"/Users(?:/[^\s,;}\"']*)?"
    r"|/home(?:/[^\s,;}\"']*)?"
    r"|/workspace(?:/[^\s,;}\"']*)?"
    r"|/data/bots(?:/[^\s,;}\"']*)?"
    r"|/var/lib/kubelet(?:/[^\s,;}\"']*)?"
    r"|/private/var(?:/[^\s,;}\"']*)?"
    r"|\bsk-[A-Za-z0-9._-]{6,}"
    r"|\bgh[opusr]_[A-Za-z0-9_]{6,}"
    r"|\bgithub_pat_[A-Za-z0-9_]+"
    r"|\bxox[a-z]-[A-Za-z0-9._-]+"
    r"|\bAKIA[0-9A-Z]{8,}"
    r"|\bAIza[A-Za-z0-9_-]+"
    r"|\bbearer\s+[A-Za-z0-9._~+/=-]{6,}"
    r"|authorization\s*:"
    r"|secret[_\s]*[:=]"
    r"|token[_\s]*[:=]"
    r"|password[_\s]*[:=]"
    r"|api[_-]?key[_\s]*[:=]"
    r")",
    re.IGNORECASE,
)

_DIGEST_RE = re.compile(r"^sha256:[a-f0-9]{64}$")


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------


def is_coding_mutation_tool(tool_name: str) -> bool:
    """Return True if *tool_name* is a coding-mutation tool."""
    return tool_name in _CODING_MUTATION_TOOLS


def text_claim_is_not_receipt(value: object) -> bool:
    """Return True if *value* is a plain text claim rather than a real receipt.

    A ``CodingToolReceiptRecord`` instance is the *only* valid receipt shape.
    Anything else — strings, dicts, JSON blobs — is a text-only claim that
    cannot substitute for real tool execution evidence.
    """
    return not isinstance(value, CodingToolReceiptRecord)


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


class CodingToolReceiptConfig(BaseModel):
    model_config = _MODEL_CONFIG

    enabled: bool = False
    production_workspace_mutation_allowed: Literal[False] = Field(
        default=False,
        alias="productionWorkspaceMutationAllowed",
    )

    @classmethod
    def model_construct(
        cls,
        _fields_set: set[str] | None = None,
        **values: Any,
    ) -> Self:
        _ = _fields_set
        values["productionWorkspaceMutationAllowed"] = False
        return cls.model_validate(values)


# ---------------------------------------------------------------------------
# Receipt record
# ---------------------------------------------------------------------------


class CodingToolReceiptRecord(BaseModel):
    model_config = _MODEL_CONFIG

    tool_call_id: str = Field(alias="toolCallId")
    tool_name: str = Field(alias="toolName")
    action: str
    status: CodingToolReceiptStatus
    input_digest: str = Field(alias="inputDigest")
    output_digest: str = Field(alias="outputDigest")
    workspace_digest: str = Field(alias="workspaceDigest")
    production_workspace_mutation_allowed: Literal[False] = Field(
        default=False,
        alias="productionWorkspaceMutationAllowed",
    )

    @field_validator("input_digest", "output_digest", "workspace_digest")
    @classmethod
    def _validate_digest(cls, value: str) -> str:
        if not _DIGEST_RE.fullmatch(value):
            raise ValueError("digest must be sha256:<64 hex chars>")
        return value

    def public_projection(self) -> dict[str, object]:
        """Return a public-safe projection with no raw paths/secrets."""
        return {
            "toolCallId": _sanitize_public(self.tool_call_id),
            "toolName": _sanitize_public(self.tool_name),
            "action": self.action,
            "status": self.status,
            "inputDigest": self.input_digest,
            "outputDigest": self.output_digest,
            "workspaceDigest": self.workspace_digest,
            "productionWorkspaceMutationAllowed": False,
        }


# ---------------------------------------------------------------------------
# Boundary
# ---------------------------------------------------------------------------


class CodingToolReceiptBoundary:
    """Narrow boundary that extracts receipts from ToolHost dispatch results.

    Default-off: returns ``None`` unless config.enabled is True.
    """

    def __init__(
        self,
        config: CodingToolReceiptConfig | None = None,
    ) -> None:
        self.config = config or CodingToolReceiptConfig()

    def extract_receipt(
        self,
        *,
        tool_call_id: str,
        tool_name: str,
        arguments: dict[str, object],
        result: ToolResult,
    ) -> CodingToolReceiptRecord | None:
        """Extract a receipt from a completed tool dispatch.

        Returns ``None`` if:
        - The boundary is disabled.
        - The tool is not a coding mutation tool.
        """
        if not self.config.enabled:
            return None

        if not is_coding_mutation_tool(tool_name):
            return None

        status = _map_status(result.status)
        action = _TOOL_ACTION_MAP.get(tool_name, "unknown")
        input_digest = _compute_digest(arguments)
        output_digest = _compute_digest(
            result.output if result.output is not None else result.error_message or ""
        )
        workspace_digest = _compute_workspace_digest(tool_name, arguments, result)

        return CodingToolReceiptRecord(
            toolCallId=tool_call_id,
            toolName=tool_name,
            action=action,
            status=status,
            inputDigest=input_digest,
            outputDigest=output_digest,
            workspaceDigest=workspace_digest,
            productionWorkspaceMutationAllowed=False,
        )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _map_status(tool_status: str) -> CodingToolReceiptStatus:
    if tool_status == "ok":
        return "success"
    if tool_status == "blocked" or tool_status == "needs_approval":
        return "blocked"
    return "error"


def _compute_digest(value: object) -> str:
    """Compute sha256 digest of *value* serialized as canonical JSON."""
    material = json.dumps(value, sort_keys=True, default=repr, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(material.encode("utf-8")).hexdigest()


def _compute_workspace_digest(
    tool_name: str,
    arguments: dict[str, object],
    result: ToolResult,
) -> str:
    """Compute a workspace-scoped digest from tool name, path-like args, and result status."""
    path = arguments.get("path", arguments.get("file_path", ""))
    seed = f"{tool_name}|{path}|{result.status}"
    return "sha256:" + hashlib.sha256(seed.encode("utf-8")).hexdigest()


def _sanitize_public(value: str) -> str:
    """Strip private data from a string for public projection."""
    sanitized = _PRIVATE_DATA_RE.sub("[redacted]", value)
    return sanitized[:240]


__all__ = [
    "CodingToolReceiptBoundary",
    "CodingToolReceiptConfig",
    "CodingToolReceiptRecord",
    "CodingToolReceiptStatus",
    "is_coding_mutation_tool",
    "text_claim_is_not_receipt",
]
