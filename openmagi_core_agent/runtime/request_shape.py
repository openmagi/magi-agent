from __future__ import annotations

import hashlib
import json
import re
from typing import Mapping

from pydantic import BaseModel, ConfigDict, Field

from openmagi_core_agent.runtime.model_tiers import (
    ModelTier,
    ModelTierRegistry,
    ModelUsagePhase,
)


_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    revalidate_instances="always",
    hide_input_in_errors=True,
)
_PUBLIC_REF_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{1,180}$")
_DIGEST_RE = re.compile(r"^sha256:[a-f0-9]{64}$")
_PRIVATE_TEXT_RE = re.compile(
    r"(?:"
    r"/Users/[^\s,'\"]+|"
    r"/workspace/[^\s,'\"]+|"
    r"/data/bots/[^\s,'\"]+|"
    r"Bearer\s+[A-Za-z0-9._~+/=-]{4,}|"
    r"gh[opusr]_[A-Za-z0-9_]{4,}|"
    r"github_pat_[A-Za-z0-9_]{4,}|"
    r"xox[a-z]-[A-Za-z0-9._-]{4,}|"
    r"AKIA[0-9A-Z]{8,}|"
    r"AIza[A-Za-z0-9_-]{8,}|"
    r"sk-(?:live|test)?[-_A-Za-z0-9]{4,}|"
    r"authorization|cookie|credential|secret|token|password|private[_-]?key|api[_-]?key|"
    r"://|\\s"
    r")",
    re.IGNORECASE,
)


class RequestShapeRecord(BaseModel):
    model_config = _MODEL_CONFIG

    record_id: str = Field(alias="recordId")
    turn_id: str = Field(alias="turnId")
    phase: ModelUsagePhase
    provider: str
    model: str
    model_tier: ModelTier = Field(alias="modelTier")
    model_capabilities: tuple[str, ...] = Field(default=(), alias="modelCapabilities")
    recipe_snapshot_id: str | None = Field(default=None, alias="recipeSnapshotId")
    input_refs: tuple[str, ...] = Field(default=(), alias="inputRefs")
    evidence_refs: tuple[str, ...] = Field(default=(), alias="evidenceRefs")
    context_plan_digest: str | None = Field(default=None, alias="contextPlanDigest")
    input_digest: str = Field(alias="inputDigest")
    output_digest: str | None = Field(default=None, alias="outputDigest")
    validator_refs: tuple[str, ...] = Field(default=(), alias="validatorRefs")
    validator_statuses: Mapping[str, str] = Field(default_factory=dict, alias="validatorStatuses")
    cost_estimate_usd: float | None = Field(default=None, ge=0, alias="costEstimateUsd")
    escalation_reason: str | None = Field(default=None, alias="escalationReason")
    fallback_reason: str | None = Field(default=None, alias="fallbackReason")

    def public_projection(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "recordId": self.record_id,
            "turnId": self.turn_id,
            "phase": self.phase,
            "provider": self.provider,
            "model": self.model,
            "modelTier": self.model_tier,
            "modelCapabilities": list(self.model_capabilities),
            "inputRefs": list(self.input_refs),
            "evidenceRefs": list(self.evidence_refs),
            "inputDigest": self.input_digest,
            "validatorRefs": list(self.validator_refs),
            "validatorStatuses": dict(self.validator_statuses),
        }
        if self.recipe_snapshot_id is not None:
            payload["recipeSnapshotId"] = self.recipe_snapshot_id
        if self.context_plan_digest is not None:
            payload["contextPlanDigest"] = self.context_plan_digest
        if self.output_digest is not None:
            payload["outputDigest"] = self.output_digest
        if self.cost_estimate_usd is not None:
            payload["costEstimateUsd"] = self.cost_estimate_usd
        if self.escalation_reason is not None:
            payload["escalationReason"] = self.escalation_reason
        if self.fallback_reason is not None:
            payload["fallbackReason"] = self.fallback_reason
        return payload


class RequestShapeLedger:
    def __init__(self, model_registry: ModelTierRegistry | None = None) -> None:
        self._model_registry = model_registry or ModelTierRegistry.with_defaults()
        self._records: dict[str, RequestShapeRecord] = {}

    def record_model_phase(
        self,
        *,
        turnId: str,
        phase: ModelUsagePhase,
        provider: str,
        model: str,
        modelTier: ModelTier,
        modelCapabilities: tuple[str, ...] = (),
        recipeSnapshotId: str | None = None,
        inputRefs: tuple[str, ...] = (),
        evidenceRefs: tuple[str, ...] = (),
        contextPlanDigest: str | None = None,
        rawInput: object | None = None,
        outputText: object | None = None,
        validatorRefs: tuple[str, ...] = (),
        validatorStatuses: Mapping[str, str] | None = None,
        costEstimateUsd: float | None = None,
        escalationReason: str | None = None,
        fallbackReason: str | None = None,
    ) -> RequestShapeRecord:
        resolved = self._model_registry.resolve(provider=provider, model=model)
        if resolved.tier != modelTier:
            raise ValueError("modelTier does not match registry")
        safe_input_refs = _safe_refs(inputRefs)
        safe_evidence_refs = _safe_refs(evidenceRefs)
        safe_validator_refs = _safe_refs(validatorRefs)
        context_digest = (
            contextPlanDigest
            if contextPlanDigest is not None and _DIGEST_RE.fullmatch(contextPlanDigest)
            else None
        )
        input_digest = _digest(rawInput if rawInput is not None else safe_input_refs)
        output_digest = _digest(outputText) if outputText is not None else None
        safe_turn_id = _safe_turn_id(turnId)
        record_id = _digest(
            {
                "turn": safe_turn_id,
                "phase": phase,
                "provider": resolved.provider,
                "model": resolved.model,
                "inputRefs": safe_input_refs,
                "inputDigest": input_digest,
            },
            prefix="request-shape",
        )
        if record_id in self._records:
            return self._records[record_id]
        record = RequestShapeRecord(
            recordId=record_id,
            turnId=safe_turn_id,
            phase=phase,
            provider=resolved.provider,
            model=resolved.model,
            modelTier=resolved.tier,
            modelCapabilities=tuple(
                capability
                for capability in (modelCapabilities or resolved.capabilities)
                if capability in resolved.capabilities
            ),
            recipeSnapshotId=_safe_optional_identifier(recipeSnapshotId),
            inputRefs=safe_input_refs,
            evidenceRefs=safe_evidence_refs,
            contextPlanDigest=context_digest,
            inputDigest=input_digest,
            outputDigest=output_digest,
            validatorRefs=safe_validator_refs,
            validatorStatuses=_safe_validator_statuses(validatorStatuses or {}),
            costEstimateUsd=costEstimateUsd,
            escalationReason=_safe_reason(escalationReason),
            fallbackReason=_safe_reason(fallbackReason),
        )
        self._records[record_id] = record
        return record

    def records(self) -> tuple[RequestShapeRecord, ...]:
        return tuple(self._records[key] for key in sorted(self._records))


def _safe_refs(values: tuple[str, ...]) -> tuple[str, ...]:
    refs: list[str] = []
    for value in values:
        text = str(value).strip()
        if _PRIVATE_TEXT_RE.search(text) or _PUBLIC_REF_RE.fullmatch(text) is None:
            continue
        refs.append(text)
    return tuple(dict.fromkeys(refs))


def _safe_turn_id(value: str) -> str:
    text = str(value).strip()
    if _PRIVATE_TEXT_RE.search(text) or _PUBLIC_REF_RE.fullmatch(text) is None:
        return _digest(text or "missing-turn", prefix="turn")
    return text


def _safe_optional_identifier(value: str | None) -> str | None:
    if value is None:
        return None
    return _safe_turn_id(value)


def _safe_reason(value: str | None) -> str | None:
    if value is None:
        return None
    text = value.strip().replace("-", "_")
    if _PRIVATE_TEXT_RE.search(text) or not re.fullmatch(r"[a-z0-9_.:]{1,96}", text):
        return _digest(value, prefix="reason")
    return text


def _safe_validator_statuses(values: Mapping[str, str]) -> dict[str, str]:
    allowed = {"passed", "failed", "repair_required", "blocked", "skipped"}
    return {
        key: value
        for key, value in sorted(values.items())
        if key in _safe_refs((key,)) and value in allowed
    }


def _digest(value: object, *, prefix: str = "sha256") -> str:
    material = json.dumps(value, sort_keys=True, default=repr, separators=(",", ":"))
    hexdigest = hashlib.sha256(material.encode("utf-8")).hexdigest()
    return f"{prefix}:{hexdigest}" if prefix != "sha256" else f"sha256:{hexdigest}"


__all__ = [
    "RequestShapeLedger",
    "RequestShapeRecord",
]
