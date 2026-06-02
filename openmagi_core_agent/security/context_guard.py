from __future__ import annotations

import hashlib
import json
import re

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr


_CONTEXT_FILE_RE = re.compile(
    r"^(AGENTS|CLAUDE|SOUL|TOOLS|HEARTBEAT|\.cursorrules)\.md$|^\.cursorrules$",
)
_IGNORE_RE = re.compile(
    r"\b(ignore|disregard|override)\b.{0,80}\b(previous|prior|system|developer)\b",
    re.IGNORECASE | re.DOTALL,
)
_HIDDEN_COMMENT_RE = re.compile(
    r"<!--.{0,240}(hidden|ignore|secret|credential|\.env|exfil).{0,240}-->",
    re.IGNORECASE | re.DOTALL,
)
_SECRET_READ_RE = re.compile(
    r"(\.env|credentials?\.json|\.netrc|id_rsa|private[_-]?key|api[_-]?key)",
    re.IGNORECASE,
)
_EXFIL_RE = re.compile(
    r"\b(curl|wget|nc|scp|rsync)\b.{0,160}"
    r"(\.env|credential|secret|token|--data|--upload-file|@)",
    re.IGNORECASE | re.DOTALL,
)
_INVISIBLE_RE = re.compile("[\u200b\u200c\u200d\u2060\u202a-\u202e]")

_PUBLIC_REASON_CODES = {
    "context_file_allowed",
    "context_filename_not_recognized",
    "credential_exfiltration_attempt",
    "hidden_comment_injection",
    "ignore_instruction_attack",
    "invisible_unicode_detected",
    "secret_read_attempt",
}
_POLICY_CLASS = "heuristic_projection_policy"
_BOUNDARY_CLASS = "heuristic"


class ContextGuardResult(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True, extra="forbid")

    _content_digest: str = PrivateAttr(default="")

    filename: str
    allowed: bool
    reason_codes: tuple[str, ...] = Field(alias="reasonCodes")
    scan_digest: str = Field(default="", alias="scanDigest")

    def public_projection(self) -> dict[str, object]:
        filename = _public_filename(getattr(self, "filename", "redacted"))
        reason_codes = _public_reason_codes(getattr(self, "reason_codes", ()))
        content_included = (
            self.allowed is True
            and filename != "redacted"
            and reason_codes == ["context_file_allowed"]
            and self.scan_digest
            == _scan_digest(
                filename=filename,
                allowed=True,
                content_digest=self._content_digest,
                reason_codes=("context_file_allowed",),
            )
            and self._content_digest.startswith("sha256:")
        )
        return {
            "filename": filename,
            "allowed": content_included,
            "reasonCodes": reason_codes,
            "contentIncluded": content_included,
            "policyClass": _POLICY_CLASS,
            "boundaryClass": _BOUNDARY_CLASS,
        }


def scan_context_file(filename: str, content: str) -> ContextGuardResult:
    normalized_filename = filename.strip()
    public_filename = _public_filename(normalized_filename)
    reasons: list[str] = []
    if public_filename == "redacted":
        reasons.append("context_filename_not_recognized")
    if _IGNORE_RE.search(content):
        reasons.append("ignore_instruction_attack")
    if _HIDDEN_COMMENT_RE.search(content):
        reasons.append("hidden_comment_injection")
    if _SECRET_READ_RE.search(content):
        reasons.append("secret_read_attempt")
    if _EXFIL_RE.search(content):
        reasons.append("credential_exfiltration_attempt")
    if _INVISIBLE_RE.search(content):
        reasons.append("invisible_unicode_detected")

    if reasons:
        return _make_result(
            filename=normalized_filename,
            allowed=False,
            content=content,
            reason_codes=tuple(dict.fromkeys(reasons)),
        )
    return _make_result(
        filename=normalized_filename,
        allowed=True,
        content=content,
        reason_codes=("context_file_allowed",),
    )


def _make_result(
    *,
    filename: str,
    allowed: bool,
    content: str,
    reason_codes: tuple[str, ...],
) -> ContextGuardResult:
    content_digest = _content_digest(content)
    result = ContextGuardResult(
        filename=filename,
        allowed=allowed,
        reasonCodes=reason_codes,
        scanDigest=_scan_digest(
            filename=_public_filename(filename),
            allowed=allowed,
            content_digest=content_digest,
            reason_codes=reason_codes,
        ),
    )
    object.__setattr__(result, "_content_digest", content_digest)
    return result


def _scan_digest(
    *,
    filename: str,
    allowed: bool,
    content_digest: str,
    reason_codes: tuple[str, ...],
) -> str:
    payload = {
        "allowed": allowed,
        "boundaryClass": _BOUNDARY_CLASS,
        "contentDigest": _public_digest(content_digest),
        "filename": _public_filename(filename),
        "policyClass": _POLICY_CLASS,
        "reasonCodes": _public_reason_codes(reason_codes),
        "schema": "openmagi.contextGuardResult.v1",
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _content_digest(content: str) -> str:
    return f"sha256:{hashlib.sha256(content.encode()).hexdigest()}"


def _public_digest(digest: object) -> str:
    value = str(digest)
    if re.fullmatch(r"sha256:[0-9a-f]{64}", value):
        return value
    return "redacted"


def _public_filename(filename: object) -> str:
    value = str(filename).strip()
    if _CONTEXT_FILE_RE.fullmatch(value):
        return value
    return "redacted"


def _public_reason_codes(reason_codes: object) -> list[str]:
    if not isinstance(reason_codes, tuple):
        return ["redacted"]
    public: list[str] = []
    for reason_code in reason_codes:
        value = str(reason_code)
        if value in _PUBLIC_REASON_CODES:
            public.append(value)
        else:
            public.append("redacted")
    return public or ["redacted"]
