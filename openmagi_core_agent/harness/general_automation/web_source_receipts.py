from __future__ import annotations

from collections.abc import Mapping
from hashlib import sha256
import json
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from openmagi_core_agent.web_acquisition.policy import (
    normalize_public_url,
    redact_public_text,
    url_policy_error,
)


WebSourceProofType = Literal["opened", "observed"]

_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    hide_input_in_errors=True,
)
_DIGEST_RE = re.compile(r"^sha256:[a-f0-9]{64}$")
_REF_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{1,180}$")
_TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


class WebSourceReceipt(BaseModel):
    model_config = _MODEL_CONFIG

    source_ref: str = Field(alias="sourceRef")
    evidence_ref: str = Field(alias="evidenceRef")
    normalized_url_digest: str = Field(alias="normalizedUrlDigest")
    content_digest: str = Field(alias="contentDigest")
    provider: str
    proof_type: WebSourceProofType = Field(alias="proofType")
    observed_at: str = Field(alias="observedAt")

    @field_validator("source_ref", "evidence_ref")
    @classmethod
    def _validate_ref(cls, value: str) -> str:
        if not _REF_RE.fullmatch(value):
            raise ValueError("ref must be a safe public reference")
        return value

    @field_validator("normalized_url_digest", "content_digest")
    @classmethod
    def _validate_digest(cls, value: str) -> str:
        if not _DIGEST_RE.fullmatch(value):
            raise ValueError("digest must be sha256")
        return value

    @field_validator("provider")
    @classmethod
    def _sanitize_provider(cls, value: str) -> str:
        clean = redact_public_text(value, max_chars=120).strip()
        if not clean:
            raise ValueError("provider must be public metadata")
        return clean

    @field_validator("observed_at")
    @classmethod
    def _validate_timestamp(cls, value: str) -> str:
        if not _TIMESTAMP_RE.fullmatch(value):
            raise ValueError("observedAt must be UTC second timestamp")
        return value

    def public_projection(self) -> dict[str, object]:
        return {
            "sourceRef": self.source_ref,
            "evidenceRef": self.evidence_ref,
            "normalizedUrlDigest": self.normalized_url_digest,
            "contentDigest": self.content_digest,
            "provider": self.provider,
            "proofType": self.proof_type,
            "observedAt": self.observed_at,
            "adkBoundary": {
                "functionTool": "WebFetch",
                "providerCallAttached": False,
            },
            "authorityFlags": {
                "providerCalled": False,
                "networkAccessed": False,
                "browserStarted": False,
                "routeAttached": False,
            },
        }


def build_web_source_receipt(
    *,
    url: str,
    contentDigest: str,
    provider: str,
    proofType: WebSourceProofType,
    observedAt: str,
) -> WebSourceReceipt:
    policy_error = url_policy_error(url)
    if policy_error is not None:
        raise ValueError(policy_error)
    normalized = normalize_public_url(url)
    normalized_digest = _digest(normalized)
    material: Mapping[str, object] = {
        "normalizedUrlDigest": normalized_digest,
        "contentDigest": contentDigest,
        "provider": provider,
        "proofType": proofType,
        "observedAt": observedAt,
    }
    return WebSourceReceipt(
        sourceRef=_ref("source:web", material),
        evidenceRef=_ref("evidence:web", material),
        normalizedUrlDigest=normalized_digest,
        contentDigest=contentDigest,
        provider=provider,
        proofType=proofType,
        observedAt=observedAt,
    )


def _ref(prefix: str, material: object) -> str:
    return f"{prefix}:{_digest(material)}"


def _digest(value: object) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        default=repr,
    ).encode("utf-8")
    return f"sha256:{sha256(encoded).hexdigest()}"


__all__ = [
    "WebSourceReceipt",
    "build_web_source_receipt",
]
