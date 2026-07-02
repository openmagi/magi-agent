from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import hashlib
import inspect
import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from magi_agent.memory.contracts import (
    MemoryRecord,
    RecallRequest,
    RecallResult,
    UnsupportedMemoryOperationError,
)
from magi_agent.memory.policy import MemoryPolicy, evaluate_memory_policy
from magi_agent.ops.safety import (
    AUTHORIZATION_HEADER_RE as _AUTHORIZATION_HEADER_RE,
    BEARER_TOKEN_RE as _BEARER_TOKEN_RE,
    GITHUB_TOKEN_RE as _GITHUB_TOKEN_RE,
    OPENAI_TOKEN_RE as _OPENAI_TOKEN_RE,
    STRIPE_TOKEN_RE as _STRIPE_TOKEN_RE,
    redact_secret_tokens as _kernel_redact_secret_tokens,
)


_MODEL_CONFIG = ConfigDict(frozen=True, populate_by_name=True, extra="forbid")
_PRIVATE_PATH_RE = re.compile(
    r"(?:/Users(?:/[^,\s\"']*)?|/home(?:/[^,\s\"']*)?|"
    r"/workspace(?:/[^,\s\"']*)?|/data/bots(?:/[^,\s\"']*)?|"
    r"/var/lib/kubelet(?:/[^,\s\"']*)?|pvc-[A-Za-z0-9-]+)",
    re.IGNORECASE,
)
_PRIVATE_LINE_RE = re.compile(
    r"raw[ _-]?(?:child|subagent|tool|prompt|transcript|output|result|log|args)|"
    r"raw[_-]?prompt|raw[_-]?tool[_-]?log|raw[_-]?child[_-]?output|"
    r"(?:child|subagent)[ _-]?(?:prompt|output|transcript)|"
    r"tool[ _-]?(?:log|args|result)|"
    r"private[ _-]?path|hidden|chain[ _-]?of[ _-]?thought|"
    r"private[ _-]?reasoning|private[ _-]?memory[A-Za-z0-9_-]*|"
    r"reasoning[_-]?trace|model[_-]?internal|"
    r"<child[_-]?prompt\b|</child[_-]?prompt>|<tool[_-]?log\b|</tool[_-]?log>|"
    r"<hidden[_-]?reasoning\b|</hidden[_-]?reasoning>|cookie|set-cookie",
    re.IGNORECASE,
)
_PRIVATE_METADATA_KEY_RE = re.compile(
    r"raw|hidden|private|prompt|tool|child|path|secret|token|authorization|cookie|password",
    re.IGNORECASE,
)
# The five token patterns and the generic redactor now come from the single
# home magi_agent/ops/safety.py (imported above). adk_bridge keeps its own
# site-specific label patterns (telegram/object/sensitive URL, whole-line
# cookie) and applies the kernel redactor LAST in _redact_secret_text.
_TELEGRAM_BOT_URL_RE = re.compile(
    r"https?://api\.telegram\.org/bot[0-9]+:[^/\s\"'<>]+[^\s\"'<>]*",
    re.IGNORECASE,
)
_SENSITIVE_OBJECT_URL_RE = re.compile(
    r"https?://(?:"
    r"(?:storage\.googleapis\.com|storage\.cloud\.google\.com|[^/\s\"'<>]*\.storage\.googleapis\.com)|"
    r"(?:[^/\s\"'<>]*s3[^/\s\"'<>]*\.amazonaws\.com|s3[.-][^/\s\"'<>]*\.amazonaws\.com)|"
    r"(?:[^/\s\"'<>]*\.supabase\.co/storage/)|"
    r"(?:[^/\s\"'<>]*\.r2\.cloudflarestorage\.com)|"
    r"(?:[^/\s\"'<>]*blob\.core\.windows\.net)"
    r")[^\s\"'<>]*",
    re.IGNORECASE,
)
_PRIVATE_BLOCK_RE = re.compile(
    r"<(?:child[_-]?prompt|tool[_-]?log|hidden[_-]?reasoning)\b[^>]*>.*?"
    r"</(?:child[_-]?prompt|tool[_-]?log|hidden[_-]?reasoning)>",
    re.IGNORECASE | re.DOTALL,
)
_PRIVATE_LINE_START_RE = re.compile(
    r"^\s*(?:raw[ _-]?(?:child|subagent|tool|prompt|transcript|output|result|log|args)"
    r"(?:[ _-]?(?:child|subagent|tool|prompt|transcript|output|result|log|args))*|"
    r"(?:child|subagent)[ _-]?(?:prompt|output|transcript)|"
    r"tool[ _-]?(?:log|args|result)|"
    r"hidden[ _-]?reasoning|chain[ _-]?of[ _-]?thought|"
    r"private[ _-]?reasoning|private[ _-]?memory[A-Za-z0-9_-]*|"
    r"reasoning[_-]?trace|model[_-]?internal|authorization|cookie|set-cookie|"
    r"[A-Za-z0-9_-]*(?:secret|token|password|private[_-]?key|api[_-]?key|"
    r"access[_-]?key|aws[_-]?access[_-]?key[_-]?id|aws[_-]?secret[_-]?access[_-]?key)"
    r"[A-Za-z0-9_-]*)\s*[:=].*$",
    re.IGNORECASE,
)
_SECRET_KEY_VALUE_RE = re.compile(
    r"(?i)"
    r"([A-Za-z0-9_-]*(?:secret|token|password|private[_-]?key|api[_-]?key|"
    r"access[_-]?key|aws[_-]?access[_-]?key[_-]?id|aws[_-]?secret[_-]?access[_-]?key)"
    r"[A-Za-z0-9_-]*\s*[:=]\s*)"
    r"([^\s,}\n]+)"
)
_SENSITIVE_SOURCE_SCHEME_RE = re.compile(
    r"^(?:s3|gs|gcs|supabase|postgres|postgresql|mysql|redis|mongodb|file|vault|"
    r"secret|secrets|ssh|scp|ftp)://",
    re.IGNORECASE,
)
_SENSITIVE_URL_RE = re.compile(
    r"(?:s3|gs|gcs|supabase|postgres|postgresql|mysql|redis|mongodb|file|vault|"
    r"secret|secrets)://[^\s\"'<>]+|"
    r"https?://[^\s\"'<>]*[?&](?:X-Amz-Signature|access[_-]?token|api[_-]?key|auth|"
    r"authorization|cookie|credential|key|password|private[_-]?key|secret|session|"
    r"sig|signature|token)=[^\s\"'<>]+",
    re.IGNORECASE,
)
_SENSITIVE_QUERY_RE = re.compile(
    r"[?&](?:access[_-]?token|auth|authorization|cookie|credential|key|password|"
    r"private[_-]?key|secret|session|sig|signature|token)=",
    re.IGNORECASE,
)
_URL_USERINFO_RE = re.compile(r"^[a-z][a-z0-9+.-]*://[^/\s?#@]+@", re.IGNORECASE)
_PRIVATE_SOURCE_TEXT_RE = re.compile(
    r"authorization\s*:|cookie\s*:|set-cookie\s*:|bearer\s+|"
    r"https?://api\.telegram\.org/bot[0-9]+:[^/\s?#]+|"
    r"(?:localhost|127\.0\.0\.1|0\.0\.0\.0|169\.254\.169\.254|metadata\.google\.internal|"
    r"\.cluster\.local)(?:[:/?#]|$)",
    re.IGNORECASE,
)
_COOKIE_HEADER_RE = re.compile(r"\b(?:Cookie|Set-Cookie)\s*:\s*[^\n\r]+", re.IGNORECASE)
_SAFE_SCOPES: set[str] = {"user", "bot", "org", "project", "session", "task"}
_SAFE_KINDS: set[str] = {
    "event",
    "note",
    "fact",
    "decision",
    "preference",
    "reasoning",
    "artifact",
    "relation",
}
_SAFE_CONFIDENCE: set[str] = {
    "observed",
    "inferred",
    "user_asserted",
    "system_asserted",
    "verified",
}
_SAFE_VISIBILITY: set[str] = {"private", "shared", "public-safe"}
_SAFE_REASON_CODE_RE = re.compile(r"^[a-z][a-z0-9_.:-]{0,127}$")


class ADKMemoryBridgeConfig(BaseModel):
    model_config = _MODEL_CONFIG

    enabled: bool = False
    local_fake_provider_enabled: bool = Field(default=False, alias="localFakeProviderEnabled")
    local_fake_adk_service_enabled: bool = Field(
        default=False,
        alias="localFakeAdkServiceEnabled",
    )
    provider_id: str = Field(default="adk-memory-bridge", alias="providerId")
    adk_service_provider_id: str = Field(
        default="adk-memory-service",
        alias="adkServiceProviderId",
    )
    app_name: str = Field(default="magi-agent", alias="appName")
    user_id: str = Field(default="local-fake-user", alias="userId")
    max_records: int = Field(default=5, alias="maxRecords", ge=1, le=20)


@dataclass(frozen=True)
class ADKMemoryBridgeRecallOutcome:
    result: RecallResult
    diagnostic_metadata: Mapping[str, object]

    def public_projection(self) -> dict[str, object]:
        return {
            "providerId": _safe_provider_id(self.result.provider_id),
            "recallAllowed": self.result.recall_allowed,
            "writeAllowed": self.result.write_allowed,
            "promptProjectionAllowed": self.result.prompt_projection_allowed,
            "publicProjectionAllowed": self.result.public_projection_allowed,
            "reasonCodes": [_safe_reason_code(reason_code) for reason_code in self.result.reason_codes],
            "records": [
                _record_public_projection(record)
                for record in self.result.records
                if self.result.public_projection_allowed
            ],
            "diagnosticMetadata": _sanitize_diagnostic_metadata(self.diagnostic_metadata),
        }


class ADKMemoryServiceBridge:
    """OpenMagi policy/redaction boundary around ADK MemoryService recall.

    The bridge does not create an ADK runtime, attach runners, write memory, or
    project memory into prompts. Live-looking paths require explicit local fake
    flags and caller-provided fakes.
    """

    def __init__(
        self,
        config: ADKMemoryBridgeConfig,
        *,
        provider: object | None = None,
        adk_memory_service: object | None = None,
    ) -> None:
        self.config = config
        self.provider = provider
        self.adk_memory_service = adk_memory_service

    async def recall(
        self,
        request: RecallRequest,
        *,
        policy: MemoryPolicy,
    ) -> ADKMemoryBridgeRecallOutcome:
        decision = evaluate_memory_policy(request, policy)
        diagnostics = _diagnostic_metadata()

        if not self.config.enabled:
            return self._outcome(
                decision,
                diagnostics,
                reason_codes=(*decision.reason_codes, "adk_memory_bridge_disabled"),
            )
        if not decision.recall_allowed:
            return self._outcome(decision, diagnostics)

        records: list[MemoryRecord] = []
        if self.config.local_fake_provider_enabled and self.provider is not None:
            if getattr(self.provider, "openmagi_local_fake_provider", False) is not True:
                return self._outcome(
                    decision,
                    diagnostics,
                    reason_codes=(*decision.reason_codes, "local_fake_memory_provider_untrusted"),
                )
            try:
                provider_result = await _maybe_await(
                    self.provider.recall(request, policy=policy),  # type: ignore[attr-defined]
                )
            except Exception as exc:
                diagnostics["provider_error"] = _sanitize_body(
                    str(exc),
                    max_bytes=240,
                ) or "[redacted-provider-error]"
                return self._outcome(
                    decision,
                    diagnostics,
                    reason_codes=(*decision.reason_codes, "local_fake_memory_provider_error"),
                )
            diagnostics["provider_called"] = True
            if isinstance(provider_result, RecallResult):
                records.extend(_sanitize_records(provider_result.records, max_bytes=request.max_bytes))

        if self.config.local_fake_adk_service_enabled and self.adk_memory_service is not None:
            if getattr(self.adk_memory_service, "openmagi_local_fake_provider", False) is not True:
                return self._outcome(
                    decision,
                    diagnostics,
                    reason_codes=(*decision.reason_codes, "local_fake_adk_memory_service_untrusted"),
                )
            try:
                response = await _maybe_await(
                    self.adk_memory_service.search_memory(
                        app_name=self.config.app_name,
                        user_id=self.config.user_id,
                        query=request.query,
                    )
                )
            except Exception as exc:
                diagnostics["adk_service_error"] = _sanitize_body(
                    str(exc),
                    max_bytes=240,
                ) or "[redacted-provider-error]"
                return self._outcome(
                    decision,
                    diagnostics,
                    reason_codes=(*decision.reason_codes, "local_fake_adk_memory_service_error"),
                )
            diagnostics["adk_service_called"] = True
            if hasattr(response, "memories"):
                records.extend(self._records_from_adk_response(response, request))

        limited = tuple(records[: min(request.limit, self.config.max_records)])
        return self._outcome(decision, diagnostics, records=limited)

    async def remember(self, _payload: object) -> None:
        raise UnsupportedMemoryOperationError("remember", provider_id=self.config.provider_id)

    async def delete(self, _record_id: str) -> None:
        raise UnsupportedMemoryOperationError("delete", provider_id=self.config.provider_id)

    async def redact(self, _record_id: str) -> None:
        raise UnsupportedMemoryOperationError("redact", provider_id=self.config.provider_id)

    def _records_from_adk_response(
        self,
        response: object,
        request: RecallRequest,
    ) -> list[MemoryRecord]:
        records: list[MemoryRecord] = []
        memories = getattr(response, "memories", ())
        if not isinstance(memories, list | tuple):
            return records
        for entry in memories:
            text = _text_from_memory_entry(entry)
            if not text:
                continue
            raw_metadata = getattr(entry, "custom_metadata", None)
            metadata = raw_metadata if isinstance(raw_metadata, dict) else {}
            source_ref = _safe_source_ref(
                _metadata_string(metadata, "sourceRef")
                or _metadata_string(metadata, "source_ref")
                or getattr(entry, "id", None)
                or "adk-memory-entry"
            )
            records.append(
                MemoryRecord(
                    id=_safe_source_ref(getattr(entry, "id", "")) if getattr(entry, "id", None) else _record_id(source_ref, text),
                    scope=_safe_literal(metadata.get("scope"), _SAFE_SCOPES, "bot"),
                    kind=_safe_literal(metadata.get("kind"), _SAFE_KINDS, "note"),
                    body=_sanitize_body(text, max_bytes=request.max_bytes),
                    source_ref=source_ref,
                    provider_id=self.config.adk_service_provider_id,
                    confidence=_safe_literal(
                        metadata.get("confidence"),
                        _SAFE_CONFIDENCE,
                        "observed",
                    ),
                    visibility=_safe_literal(
                        metadata.get("visibility"),
                        _SAFE_VISIBILITY,
                        "private",
                    ),
                    score=_safe_score(metadata.get("score")),
                    custom_metadata=_sanitize_custom_metadata(metadata),
                )
            )
        return records

    def _outcome(
        self,
        decision: object,
        diagnostics: Mapping[str, object],
        *,
        records: tuple[MemoryRecord, ...] = (),
        reason_codes: tuple[str, ...] | None = None,
    ) -> ADKMemoryBridgeRecallOutcome:
        return ADKMemoryBridgeRecallOutcome(
            result=RecallResult(
                provider_id=self.config.provider_id,
                records=records,
                recall_allowed=getattr(decision, "recall_allowed"),
                write_allowed=False,
                prompt_projection_allowed=False,
                public_projection_allowed=getattr(decision, "public_projection_allowed"),
                reason_codes=reason_codes or getattr(decision, "reason_codes"),
            ),
            diagnostic_metadata=dict(diagnostics),
        )


def _diagnostic_metadata() -> dict[str, object]:
    return {
        "provider_called": False,
        "adk_service_called": False,
        "prompt_projection_allowed": False,
        "memory_writes_enabled": False,
        "production_storage_enabled": False,
    }


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


def _record_public_projection(record: MemoryRecord) -> dict[str, object]:
    projection: dict[str, object] = {
        "id": _safe_source_ref(record.id),
        "scope": record.scope,
        "kind": record.kind,
        "providerId": _safe_provider_id(record.provider_id),
        "sourceRef": _safe_source_ref(record.source_ref),
        "confidence": record.confidence,
        "visibility": record.visibility,
        "score": record.score,
    }
    if record.visibility == "public-safe":
        projection["snippet"] = _sanitize_body(record.body, max_bytes=512)
    return projection


def _sanitize_records(records: tuple[MemoryRecord, ...], *, max_bytes: int = 16_384) -> list[MemoryRecord]:
    return [
        MemoryRecord(
            id=_safe_source_ref(record.id),
            scope=record.scope,
            kind=record.kind,
            body=_sanitize_body(record.body, max_bytes=max_bytes),
            source_ref=_safe_source_ref(record.source_ref),
            provider_id=record.provider_id,
            subject=record.subject,
            confidence=record.confidence,
            visibility=record.visibility,
            score=record.score,
            time_bounds=record.time_bounds,
            custom_metadata=_sanitize_custom_metadata(record.custom_metadata),
        )
        for record in records
    ]


def _text_from_memory_entry(entry: object) -> str:
    parts = getattr(getattr(entry, "content", None), "parts", None)
    if not isinstance(parts, list):
        return ""
    texts: list[str] = []
    for part in parts:
        if bool(getattr(part, "thought", False)):
            continue
        text = getattr(part, "text", None)
        if isinstance(text, str):
            texts.append(text)
    return "\n".join(texts)


def _sanitize_body(text: str, *, max_bytes: int = 16_384) -> str:
    text = _PRIVATE_BLOCK_RE.sub("[redacted private block]", text)
    text = _redact_secret_text(text)
    public_lines = _drop_private_marker_lines(text.splitlines())
    sanitized = "\n".join(public_lines)
    sanitized = _PRIVATE_PATH_RE.sub("[redacted-path]", sanitized)
    encoded = sanitized.encode("utf-8")
    if len(encoded) <= max_bytes:
        return sanitized
    return encoded[: max(0, max_bytes - 3)].decode("utf-8", errors="ignore") + "..."


def _drop_private_marker_lines(lines: list[str]) -> list[str]:
    public_lines: list[str] = []
    for line in lines:
        line_has_marker = bool(
            _PRIVATE_LINE_RE.search(line) or _PRIVATE_LINE_START_RE.search(line)
            or "[redacted-telegram-url]" in line
            or "[redacted-object-url]" in line
            or "[redacted-url]" in line
            or "[redacted-source-url]" in line
        )
        if line_has_marker:
            break
        public_lines.append(line)
    return public_lines


def _redact_secret_text(text: str) -> str:
    redacted = _BEARER_TOKEN_RE.sub(r"\1[redacted]", text)
    redacted = _AUTHORIZATION_HEADER_RE.sub(r"\1[redacted]", redacted)
    redacted = _COOKIE_HEADER_RE.sub("[redacted-cookie]", redacted)
    redacted = _TELEGRAM_BOT_URL_RE.sub("[redacted-telegram-url]", redacted)
    redacted = _SENSITIVE_URL_RE.sub("[redacted-url]", redacted)
    redacted = _SENSITIVE_OBJECT_URL_RE.sub("[redacted-object-url]", redacted)
    redacted = _GITHUB_TOKEN_RE.sub("[redacted]", redacted)
    redacted = _OPENAI_TOKEN_RE.sub("[redacted]", redacted)
    redacted = _STRIPE_TOKEN_RE.sub("[redacted]", redacted)
    redacted = _SECRET_KEY_VALUE_RE.sub(r"\1[redacted]", redacted)
    # Kernel LAST: site labels above are preserved, and the kernel adds quoted
    # key/value, public-credential KV, and session-assignment coverage.
    return _kernel_redact_secret_tokens(redacted)


def _safe_source_ref(source_ref: str) -> str:
    if (
        _PRIVATE_PATH_RE.search(source_ref)
        or _PRIVATE_LINE_RE.search(source_ref)
        or _PRIVATE_LINE_START_RE.search(source_ref)
        or source_ref.startswith("/")
        or _SENSITIVE_SOURCE_SCHEME_RE.search(source_ref)
        or _SENSITIVE_URL_RE.search(source_ref)
        or _SENSITIVE_QUERY_RE.search(source_ref)
        or _URL_USERINFO_RE.search(source_ref)
        or _PRIVATE_SOURCE_TEXT_RE.search(source_ref)
        or _SENSITIVE_OBJECT_URL_RE.search(source_ref)
        or _GITHUB_TOKEN_RE.search(source_ref)
        or _OPENAI_TOKEN_RE.search(source_ref)
        or _STRIPE_TOKEN_RE.search(source_ref)
        or _SECRET_KEY_VALUE_RE.search(source_ref)
    ):
        return f"memory:{hashlib.sha1(source_ref.encode('utf-8')).hexdigest()[:16]}"
    if ".." in source_ref.split("/"):
        return f"memory:{hashlib.sha1(source_ref.encode('utf-8')).hexdigest()[:16]}"
    return _PRIVATE_PATH_RE.sub("[redacted-path]", source_ref)


def _safe_provider_id(provider_id: str) -> str:
    safe = _safe_source_ref(provider_id)
    if safe != provider_id:
        return f"provider:{safe.removeprefix('memory:')}"
    return provider_id


def _safe_reason_code(reason_code: str) -> str:
    if (
        _SAFE_REASON_CODE_RE.fullmatch(reason_code)
        and _PRIVATE_LINE_RE.search(reason_code) is None
        and _PRIVATE_LINE_START_RE.search(reason_code) is None
        and _PRIVATE_PATH_RE.search(reason_code) is None
        and _SENSITIVE_URL_RE.search(reason_code) is None
        and _SECRET_KEY_VALUE_RE.search(reason_code) is None
        and _PRIVATE_SOURCE_TEXT_RE.search(reason_code) is None
    ):
        return reason_code
    return f"reason:{hashlib.sha1(reason_code.encode('utf-8')).hexdigest()[:16]}"


def _sanitize_custom_metadata(metadata: Mapping[str, object]) -> dict[str, object]:
    sanitized: dict[str, object] = {}
    for key, value in metadata.items():
        if _PRIVATE_METADATA_KEY_RE.search(key):
            continue
        safe_value = _sanitize_metadata_value(value)
        if safe_value is not None:
            sanitized[key] = safe_value
    return sanitized


def _sanitize_diagnostic_metadata(metadata: Mapping[str, object]) -> dict[str, object]:
    sanitized: dict[str, object] = {}
    for key, value in metadata.items():
        if _PRIVATE_METADATA_KEY_RE.search(str(key)):
            continue
        safe_value = _sanitize_diagnostic_value(value)
        if safe_value is not None:
            sanitized[str(key)] = safe_value
    return sanitized


def _sanitize_diagnostic_value(value: object) -> object | None:
    if isinstance(value, str):
        return _sanitize_body(value, max_bytes=512)
    if isinstance(value, bool | int | float) or value is None:
        return value
    if isinstance(value, Mapping):
        return _sanitize_diagnostic_metadata(value)
    if isinstance(value, list | tuple):
        return [
            item
            for item in (_sanitize_diagnostic_value(nested) for nested in value)
            if item is not None
        ]
    return None


def _sanitize_metadata_value(value: object) -> object | None:
    if isinstance(value, str):
        if _PRIVATE_LINE_RE.search(value):
            return None
        return _sanitize_body(value)
    if isinstance(value, bool | int | float) or value is None:
        return value
    return None


def _metadata_string(metadata: Mapping[str, object], key: str) -> str | None:
    value = metadata.get(key)
    return value if isinstance(value, str) else None


def _safe_literal(value: object, allowed: set[str], default: str) -> Any:
    return value if isinstance(value, str) and value in allowed else default


def _safe_score(value: object) -> float | None:
    if isinstance(value, int | float):
        return float(value)
    return None


def _record_id(source_ref: str, text: str) -> str:
    digest = hashlib.sha1(f"{source_ref}\n{text}".encode("utf-8")).hexdigest()[:16]
    return f"memory:{digest}"
