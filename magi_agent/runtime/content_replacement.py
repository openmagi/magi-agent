from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator

from magi_agent.runtime.query_state import validate_digest, validate_safe_ref


_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    revalidate_instances="always",
    hide_input_in_errors=True,
)
_UNSAFE_CONTENT_RE = re.compile(
    r"(?:"
    r"/Users/|"
    r"/workspace(?:/|\b)|"
    r"/data/bots(?:/|\b)|"
    r"\bbearer\b|"
    r"authorization|"
    r"cookie|"
    r"session[_-]?key|"
    r"api[_-]?key|"
    r"secret|"
    r"password|"
    r"private|"
    r"raw\s+prompt|"
    r"tool\s+log|"
    r"hidden\s+reasoning|"
    r"stdout|"
    r"stderr|"
    r"^sk-|"
    r"gh[opusr]_|"
    r"github_pat_|"
    r"xox[a-z]-|"
    r"AIza"
    r")",
    re.IGNORECASE,
)
_SAFE_PREVIEW_RE = re.compile(
    r"^(?:\[redacted unsafe content\]|\[content preview sha256:[a-f0-9]{16} bytes:[0-9]{1,12}\])$"
)


class ContentReplacement(BaseModel):
    model_config = _MODEL_CONFIG

    content_kind: str = Field(alias="contentKind")
    content_ref: str = Field(alias="contentRef")
    digest: str
    preview: str
    original_bytes: int = Field(alias="originalBytes")
    truncated: bool
    redacted: bool

    @classmethod
    def model_construct(
        cls,
        _fields_set: set[str] | None = None,
        **values: Any,
    ) -> Self:
        _ = _fields_set
        return cls.model_validate(values)

    def model_copy(
        self,
        *,
        update: Mapping[str, object] | None = None,
        deep: bool = False,
    ) -> Self:
        data = self.model_dump(by_alias=True, mode="python", warnings=False)
        if update:
            data.update(_alias_update(type(self), update))
        _ = deep
        return type(self).model_validate(data)

    @field_validator("content_kind", "content_ref")
    @classmethod
    def _validate_refish(cls, value: str) -> str:
        return validate_safe_ref(value)

    @field_validator("digest")
    @classmethod
    def _validate_digest(cls, value: str) -> str:
        return validate_digest(value)

    @field_validator("preview")
    @classmethod
    def _validate_preview(cls, value: str) -> str:
        if not _SAFE_PREVIEW_RE.fullmatch(value):
            raise ValueError("preview must be a synthetic digest preview or redaction marker")
        return value

    def public_projection(self) -> dict[str, object]:
        return self.model_dump(by_alias=True, mode="python")


def replace_content_with_ref(
    *,
    content_kind: str,
    raw_content: str | bytes,
    ref_namespace: str,
    preview_chars: int = 96,
) -> ContentReplacement:
    raw_bytes = raw_content if isinstance(raw_content, bytes) else raw_content.encode()
    text = raw_content.decode("utf-8", errors="replace") if isinstance(raw_content, bytes) else raw_content
    digest = "sha256:" + hashlib.sha256(raw_bytes).hexdigest()
    content_ref = f"{validate_safe_ref(ref_namespace)}:{digest.removeprefix('sha256:')[:16]}"
    redacted = _UNSAFE_CONTENT_RE.search(text) is not None
    if redacted:
        preview = "[redacted unsafe content]"
        truncated = len(raw_bytes) > 0
    else:
        truncated = len(text) > preview_chars
        preview = f"[content preview sha256:{digest.removeprefix('sha256:')[:16]} bytes:{len(raw_bytes)}]"
    return ContentReplacement(
        contentKind=content_kind,
        contentRef=content_ref,
        digest=digest,
        preview=preview,
        originalBytes=len(raw_bytes),
        truncated=truncated,
        redacted=redacted,
    )


def _alias_update(
    cls: type[BaseModel],
    update: Mapping[str, object],
) -> dict[str, object]:
    alias_by_name = {
        name: field.alias
        for name, field in cls.model_fields.items()
        if field.alias is not None
    }
    return {
        str(alias_by_name.get(str(key), str(key))): value
        for key, value in update.items()
    }


__all__ = ["ContentReplacement", "replace_content_with_ref"]
