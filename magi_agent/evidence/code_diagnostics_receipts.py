"""PR5 — CodeDiagnostics evidence boundary for after-edit LSP diagnostics.

When the LSP diagnostics flag is enabled, gate5b runs a language server after a
successful workspace mutation and routes ERROR-severity diagnostics into a
``CodeDiagnostics`` evidence record. A model cannot synthesize this evidence by
text alone — only this boundary, fed by real LSP-collected diagnostics, can
produce a valid :class:`CodeDiagnosticsRecord`.

The record is metadata-only and public-safe: messages are redacted of private
paths/secrets, the file is referenced by digest (never a raw workspace path),
and the catalog ``type`` is the built-in ``CodeDiagnostics`` evidence type.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Sequence
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


CODE_DIAGNOSTICS_EVIDENCE_TYPE: Literal["CodeDiagnostics"] = "CodeDiagnostics"

_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    hide_input_in_errors=True,
)

_DIGEST_RE = re.compile(r"^sha256:[a-f0-9]{64}$")

# Reject any private path/secret marker that could leak through a diagnostic
# message before it lands in evidence.
_PRIVATE_TEXT_RE = re.compile(
    r"(?:"
    r"/Users(?:/[^\s,;}\"']*)?"
    r"|/home(?:/[^\s,;}\"']*)?"
    r"|/workspace(?:/[^\s,;}\"']*)?"
    r"|/data/bots(?:/[^\s,;}\"']*)?"
    r"|/var/lib(?:/[^\s,;}\"']*)?"
    r"|/private/var(?:/[^\s,;}\"']*)?"
    r"|\bbearer\s+\S+"
    r"|authorization\s*:"
    r"|\bsk-[A-Za-z0-9._-]{6,}"
    r"|\bgh[opusr]_[A-Za-z0-9_]{6,}"
    r"|\bgithub_pat_[A-Za-z0-9_]+"
    r"|\bcookie\b"
    r"|\btoken\b"
    r"|\bsecret\b"
    r"|\bpassword\b"
    r"|\bcredential\b"
    r"|private[_-]?key"
    r")",
    re.IGNORECASE,
)

_MAX_MESSAGE_CHARS = 240


def _sanitize_message(message: str) -> str:
    collapsed = " ".join(message.split())
    redacted = _PRIVATE_TEXT_RE.sub("[redacted]", collapsed)
    return redacted[:_MAX_MESSAGE_CHARS]


class CodeDiagnosticEntry(BaseModel):
    model_config = _MODEL_CONFIG

    line: int = Field(ge=1)
    column: int = Field(ge=1)
    severity: Literal["error"] = "error"
    message: str

    @field_validator("message")
    @classmethod
    def _sanitize(cls, value: str) -> str:
        return _sanitize_message(value)


class CodeDiagnosticsRecord(BaseModel):
    model_config = _MODEL_CONFIG

    type: Literal["CodeDiagnostics"] = CODE_DIAGNOSTICS_EVIDENCE_TYPE
    checker: str
    file_digest: str = Field(alias="fileDigest")
    error_count: int = Field(ge=0, alias="errorCount")
    capped: bool = False
    entries: tuple[CodeDiagnosticEntry, ...] = ()
    diagnostics_digest: str = Field(alias="diagnosticsDigest")

    @field_validator("file_digest", "diagnostics_digest")
    @classmethod
    def _validate_digest(cls, value: str) -> str:
        if not _DIGEST_RE.fullmatch(value):
            raise ValueError("digest must be sha256:<64 hex chars>")
        return value

    @field_validator("checker")
    @classmethod
    def _validate_checker(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned or _PRIVATE_TEXT_RE.search(cleaned):
            raise ValueError("checker must be a public-safe label")
        return cleaned[:64]

    def public_projection(self) -> dict[str, object]:
        return {
            "type": self.type,
            "checker": self.checker,
            "fileDigest": self.file_digest,
            "errorCount": self.error_count,
            "capped": self.capped,
            "diagnosticsDigest": self.diagnostics_digest,
            "entries": [
                {
                    "line": entry.line,
                    "column": entry.column,
                    "severity": entry.severity,
                    "message": entry.message,
                }
                for entry in self.entries
            ],
        }


class CodeDiagnosticsBoundary:
    """Builds ``CodeDiagnostics`` evidence from real, ERROR-only diagnostics.

    Default-off: returns ``None`` unless ``enabled`` is True. Returns ``None``
    when there are zero ERROR diagnostics on a clean file so the write path
    appends nothing.
    """

    def __init__(self, *, enabled: bool = False) -> None:
        self._enabled = enabled

    def build_record(
        self,
        *,
        checker: str,
        file_digest: str,
        errors: Sequence[object],
        cap: int,
    ) -> CodeDiagnosticsRecord | None:
        """*errors* is a sequence of objects exposing ``line``/``column``/
        ``message`` (e.g. ``lsp_client.Diagnostic``). They must already be
        ERROR-filtered and capped by the caller; ``cap`` is recorded only to
        flag truncation."""
        if not self._enabled:
            return None
        if not errors:
            return None
        entries = tuple(
            CodeDiagnosticEntry(
                line=int(getattr(item, "line", 1)),
                column=int(getattr(item, "column", 1)),
                message=str(getattr(item, "message", "")),
            )
            for item in errors
        )
        diagnostics_digest = _compute_digest(
            [
                {
                    "line": entry.line,
                    "column": entry.column,
                    "message": entry.message,
                }
                for entry in entries
            ]
        )
        return CodeDiagnosticsRecord(
            checker=checker,
            fileDigest=file_digest,
            errorCount=len(entries),
            capped=len(errors) >= cap,
            entries=entries,
            diagnosticsDigest=diagnostics_digest,
        )


def _compute_digest(value: object) -> str:
    material = json.dumps(value, sort_keys=True, default=repr, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(material.encode("utf-8")).hexdigest()


__all__ = [
    "CODE_DIAGNOSTICS_EVIDENCE_TYPE",
    "CodeDiagnosticEntry",
    "CodeDiagnosticsBoundary",
    "CodeDiagnosticsRecord",
]
