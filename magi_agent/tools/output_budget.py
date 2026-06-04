from __future__ import annotations

from collections.abc import Mapping
import hashlib
import json
import re
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, field_serializer

from .manifest import Budget
from .result import ToolResult


_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    hide_input_in_errors=True,
)
_DEFAULT_LLM_PREVIEW_CHARS = 4000
_DEFAULT_TRANSCRIPT_PREVIEW_CHARS = 1200
_PRIVATE_TEXT_RE = re.compile(
    r"(?:"
    r"authorization\s*:\s*bearer\s+[A-Za-z0-9._~+/=-]+|"
    r"\bbearer\s+[A-Za-z0-9._~+/=-]+|"
    r"\bcookie\s*:\s*[^\n\r]+|"
    r"\bsid=[A-Za-z0-9._-]+|"
    r"\bsk-[A-Za-z0-9._-]+|"
    r"gh[opusr]_[A-Za-z0-9_]+|"
    r"github_pat_[A-Za-z0-9_]+|"
    r"xox[a-z]-[A-Za-z0-9._-]+|"
    r"AKIA[0-9A-Z]{8,}|"
    r"AIza[A-Za-z0-9_-]+|"
    r"/workspace(?:/[^\s,;}\"']*)?|"
    r"/data/bots(?:/[^\s,;}\"']*)?|"
    r"/Users(?:/[^\s,;}\"']*)?|"
    r"/home(?:/[^\s,;}\"']*)?|"
    r"/var/lib/kubelet(?:/[^\s,;}\"']*)?|"
    r"raw[_ -]?(?:tool|child|prompt|transcript|output|result|log|args)|"
    r"hidden[_ -]?reasoning|chain[_ -]?of[_ -]?thought"
    r")",
    re.IGNORECASE,
)
_SENSITIVE_KEY_MARKERS = (
    "authorization",
    "cookie",
    "credential",
    "secret",
    "token",
    "password",
    "privatekey",
    "apikey",
    "servicekey",
    "key",
    "path",
    "raw",
)
_PUBLIC_REF_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{1,180}$")
_FALSE_AUTHORITY_FLAGS = {
    "adkArtifactServiceAttached": False,
    "productionStorageWritten": False,
    "liveAttachmentEnabled": False,
    "userVisibleOutputAllowed": False,
}


class OutputBudgetCounts(BaseModel):
    model_config = _MODEL_CONFIG

    raw_chars: int = Field(alias="rawChars")
    raw_bytes: int = Field(alias="rawBytes")
    llm_preview_chars: int = Field(alias="llmPreviewChars")
    llm_preview_bytes: int = Field(alias="llmPreviewBytes")
    transcript_preview_chars: int = Field(alias="transcriptPreviewChars")
    transcript_preview_bytes: int = Field(alias="transcriptPreviewBytes")


class OutputBudgetTruncation(BaseModel):
    model_config = _MODEL_CONFIG

    llm_preview_truncated: bool = Field(alias="llmPreviewTruncated")
    transcript_preview_truncated: bool = Field(alias="transcriptPreviewTruncated")


class BudgetedToolResult(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        populate_by_name=True,
        extra="forbid",
        arbitrary_types_allowed=True,
        hide_input_in_errors=True,
    )

    status: str
    raw_result: ToolResult = Field(alias="rawResult", repr=False)
    raw_blob: bytes = Field(alias="rawBlob", repr=False)
    digest: str
    result_ref: str = Field(alias="resultRef")
    llm_preview: object | None = Field(default=None, alias="llmPreview")
    transcript_preview: object | None = Field(default=None, alias="transcriptPreview")
    counts: OutputBudgetCounts
    truncation: OutputBudgetTruncation
    metadata: Mapping[str, object] = Field(default_factory=dict)

    @classmethod
    def model_construct(
        cls,
        _fields_set: set[str] | None = None,
        **values: Any,
    ) -> Self:
        return cls(**values)

    @field_serializer("raw_blob")
    def _serialize_raw_blob(self, value: bytes) -> str:
        return "sha256:" + hashlib.sha256(value).hexdigest()

    @field_serializer("raw_result")
    def _serialize_raw_result(self, value: ToolResult) -> dict[str, object]:
        blob = _stable_json_bytes(value.model_dump(by_alias=True, mode="python", warnings=False))
        return {
            "status": value.status,
            "digest": "sha256:" + hashlib.sha256(blob).hexdigest(),
            "storedOutOfBand": True,
        }

    def public_projection(
        self,
        *,
        store_receipt: object | None = None,
        validation_decision: object | None = None,
        delegation_available: bool = False,
    ) -> dict[str, object]:
        receipt_projection = (
            store_receipt.public_projection()
            if store_receipt is not None and hasattr(store_receipt, "public_projection")
            else None
        )
        store_ref = None
        if isinstance(receipt_projection, Mapping):
            raw_ref = receipt_projection.get("ref")
            if isinstance(raw_ref, str):
                store_ref = _safe_ref(raw_ref, prefix="result")
        projection = {
            "status": self.status,
            "resultRef": self.result_ref,
            "digest": self.digest,
            "llmPreview": self.llm_preview,
            "transcriptPreview": self.transcript_preview,
            "counts": self.counts.model_dump(by_alias=True),
            "truncation": self.truncation.model_dump(by_alias=True),
            "metadata": _safe_metadata(self.metadata),
            "storeRef": store_ref,
            "authorityFlags": dict(_FALSE_AUTHORITY_FLAGS),
        }
        if validation_decision is not None and hasattr(validation_decision, "public_projection"):
            projection["validation"] = validation_decision.public_projection()
        if delegation_available and (
            self.truncation.llm_preview_truncated or self.truncation.transcript_preview_truncated
        ):
            projection["delegationHint"] = (
                f"Full output stored out of band at {self.result_ref}. "
                "Do not inline the full payload — delegate to a read-only research child "
                "(Grep/Read with offset/limit) to inspect it and conserve context."
            )
        return projection


def budget_tool_result(
    result: ToolResult,
    *,
    budget: Budget | None = None,
    llm_preview_chars: int | None = None,
    transcript_preview_chars: int | None = None,
) -> BudgetedToolResult:
    raw_result = ToolResult.model_validate(result)
    raw_blob = _stable_json_bytes(raw_result.model_dump(by_alias=True, mode="python", warnings=False))
    digest = "sha256:" + hashlib.sha256(raw_blob).hexdigest()

    llm_limit = _positive_int(
        llm_preview_chars,
        fallback=budget.output_chars if budget is not None else None,
        default=_DEFAULT_LLM_PREVIEW_CHARS,
    )
    transcript_limit = _positive_int(
        transcript_preview_chars,
        fallback=budget.transcript_chars if budget is not None else None,
        default=_DEFAULT_TRANSCRIPT_PREVIEW_CHARS,
    )

    llm_source = raw_result.llm_output if raw_result.llm_output is not None else raw_result.output
    transcript_source = (
        raw_result.transcript_output
        if raw_result.transcript_output is not None
        else raw_result.llm_output
        if raw_result.llm_output is not None
        else raw_result.output
    )
    llm_preview, llm_truncated = _preview_value(llm_source, llm_limit)
    transcript_preview, transcript_truncated = _preview_value(transcript_source, transcript_limit)

    return BudgetedToolResult(
        status=raw_result.status,
        rawResult=raw_result,
        rawBlob=raw_blob,
        digest=digest,
        resultRef=f"result:{digest}",
        llmPreview=llm_preview,
        transcriptPreview=transcript_preview,
        counts=OutputBudgetCounts(
            rawChars=len(raw_blob.decode("utf-8")),
            rawBytes=len(raw_blob),
            llmPreviewChars=_char_count(llm_preview),
            llmPreviewBytes=_byte_count(llm_preview),
            transcriptPreviewChars=_char_count(transcript_preview),
            transcriptPreviewBytes=_byte_count(transcript_preview),
        ),
        truncation=OutputBudgetTruncation(
            llmPreviewTruncated=llm_truncated,
            transcriptPreviewTruncated=transcript_truncated,
        ),
        metadata=_safe_metadata(raw_result.metadata),
    )


def _positive_int(
    value: int | None = None,
    *,
    fallback: int | None,
    default: int,
) -> int:
    candidate = value if value is not None else fallback
    if isinstance(candidate, int) and candidate > 0:
        return candidate
    return default


def _preview_value(value: object, limit: int) -> tuple[object | None, bool]:
    if value is None:
        return None, False
    text = value if isinstance(value, str) else _stable_json_text(value)
    safe = _safe_text(text)
    if len(safe) <= limit:
        return safe, False
    return safe[:limit], True


def _stable_json_bytes(value: object) -> bytes:
    return _stable_json_text(value).encode("utf-8")


def _stable_json_text(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _char_count(value: object | None) -> int:
    return 0 if value is None else len(str(value))


def _byte_count(value: object | None) -> int:
    return 0 if value is None else len(str(value).encode("utf-8"))


def _safe_metadata(metadata: Mapping[str, object]) -> dict[str, object]:
    safe: dict[str, object] = {}
    for key, value in metadata.items():
        key_text = str(key)
        if _is_sensitive_key(key_text) or _PRIVATE_TEXT_RE.search(key_text):
            continue
        if isinstance(value, str):
            clean = _safe_text(value)
            if clean:
                safe[key_text] = clean[:240]
        elif isinstance(value, bool | int | float) or value is None:
            safe[key_text] = value
    return safe


def _safe_text(value: str) -> str:
    return _PRIVATE_TEXT_RE.sub("[redacted-private]", value).strip()


def _safe_ref(value: str, *, prefix: str) -> str:
    clean = _safe_text(value)
    if clean == value and _PUBLIC_REF_RE.fullmatch(clean):
        return clean
    return f"{prefix}:{hashlib.sha1(value.encode('utf-8')).hexdigest()[:16]}"


def _is_sensitive_key(value: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]", "", value.casefold())
    return any(marker in normalized for marker in _SENSITIVE_KEY_MARKERS)


__all__ = [
    "BudgetedToolResult",
    "OutputBudgetCounts",
    "OutputBudgetTruncation",
    "budget_tool_result",
]
