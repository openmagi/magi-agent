from __future__ import annotations

from collections.abc import Mapping
import re
from typing import Literal, Self, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


ModelTier: TypeAlias = Literal[
    "cheap",
    "standard",
    "sota",
    "reasoning",
    "long_context",
    "vision",
    "local",
]
ModelCapability: TypeAlias = Literal[
    "tool_use",
    "function_calling",
    "json_schema",
    "streaming",
    "long_context",
    "coding",
    "reasoning",
    "citation_grounding",
    "vision",
    "low_latency",
]
ModelUsagePhase: TypeAlias = Literal[
    "intent_classification",
    "planning",
    "source_acquisition",
    "source_extraction",
    "code_search",
    "patch_planning",
    "patch_generation",
    "test_interpretation",
    "final_answer_drafting",
    "final_verification",
    "high_risk_review",
]


_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    revalidate_instances="always",
    hide_input_in_errors=True,
)
_PROVIDER_RE = re.compile(r"^[a-z][a-z0-9-]{0,31}$")
_MODEL_RE = re.compile(
    r"^(?=.{1,128}$)[a-z0-9][a-z0-9._-]*(?:/[a-z0-9][a-z0-9._-]*){0,5}$"
)
_UNSAFE_LABEL_RE = re.compile(
    r"(?:"
    r"^\s*$|"
    r"\s|"
    r"[\\/'\"`$=;|&<>]|"
    r"\.\.|"
    r"~|"
    r"://|"
    r"^sk-|"
    r"^xox[a-z]-|"
    r"^gh[opusr]_|"
    r"^github_pat_|"
    r"^AIza|"
    r"\bbearer\b|"
    r"api[_-]?key|"
    r"secret|"
    r"token|"
    r"password|"
    r"private[_-]?key"
    r")",
    re.IGNORECASE,
)
_UNSAFE_MODEL_LABEL_RE = re.compile(
    r"(?:"
    r"^\s*$|"
    r"\s|"
    r"[\\'\"`$=;|&<>]|"
    r"\.\.|"
    r"~|"
    r"://|"
    r"^sk-|"
    r"^xox[a-z]-|"
    r"^gh[opusr]_|"
    r"^github_pat_|"
    r"^AIza|"
    r"\bbearer\b|"
    r"api[_-]?key|"
    r"secret|"
    r"token|"
    r"password|"
    r"private[_-]?key"
    r")",
    re.IGNORECASE,
)


class _StrictModel(BaseModel):
    model_config = _MODEL_CONFIG

    @classmethod
    def model_construct(cls, _fields_set: set[str] | None = None, **values: object) -> Self:
        return cls(**values)


class ResolvedModelTier(_StrictModel):
    provider: str
    model: str
    tier: ModelTier
    capabilities: tuple[ModelCapability, ...] = ()
    dropped_requested_capabilities: tuple[str, ...] = Field(
        default=(),
        alias="droppedRequestedCapabilities",
    )
    reason_codes: tuple[str, ...] = Field(default=(), alias="reasonCodes")


class ModelTierPolicy(_StrictModel):
    recipe_id: str = Field(alias="recipeId")
    phase: ModelUsagePhase
    minimum_tier: ModelTier = Field(default="standard", alias="minimumTier")
    preferred_tier: ModelTier = Field(default="standard", alias="preferredTier")
    sota_reason: str | None = Field(default=None, alias="sotaReason")

    @model_validator(mode="after")
    def _validate_sota_reason(self) -> Self:
        if self.minimum_tier == "sota" and not (self.sota_reason or "").strip():
            raise ValueError("sotaReason is required when minimumTier is sota")
        return self


class _ModelTierRecord(_StrictModel):
    provider: str
    model: str
    tier: ModelTier
    capabilities: tuple[ModelCapability, ...] = ()

    @field_validator("provider")
    @classmethod
    def _validate_provider(cls, value: str) -> str:
        return _validate_provider(value)

    @field_validator("model")
    @classmethod
    def _validate_model(cls, value: str) -> str:
        return _validate_model(value)


class _ResolveRequest(_StrictModel):
    provider: str
    model: str
    requested_capabilities: tuple[str, ...] = Field(
        default=(),
        alias="requestedCapabilities",
    )

    @field_validator("provider")
    @classmethod
    def _validate_provider(cls, value: str) -> str:
        return _validate_provider(value)

    @field_validator("model")
    @classmethod
    def _validate_model(cls, value: str) -> str:
        return _validate_model(value)

    @field_validator("requested_capabilities")
    @classmethod
    def _validate_requested_capabilities(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        clean: list[str] = []
        for item in value:
            text = str(item).strip()
            if not text or _UNSAFE_LABEL_RE.search(text):
                continue
            clean.append(text)
        return tuple(dict.fromkeys(clean))


class ModelTierRegistry:
    def __init__(self, records: tuple[_ModelTierRecord, ...]) -> None:
        self._records = {
            (record.provider, record.model): record
            for record in sorted(records, key=lambda item: (item.provider, item.model))
        }

    @classmethod
    def with_defaults(cls) -> Self:
        return cls(
            records=(
                _ModelTierRecord(
                    provider="google",
                    model="gemini-3.5-flash",
                    tier="cheap",
                    capabilities=(
                        "streaming",
                        "json_schema",
                        "function_calling",
                        "low_latency",
                    ),
                ),
                _ModelTierRecord(
                    provider="gemini",
                    model="gemini-3.5-flash",
                    tier="cheap",
                    capabilities=(
                        "streaming",
                        "json_schema",
                        "function_calling",
                        "low_latency",
                    ),
                ),
                _ModelTierRecord(
                    provider="anthropic",
                    model="claude-sonnet-4-6",
                    tier="sota",
                    capabilities=(
                        "streaming",
                        "tool_use",
                        "long_context",
                        "coding",
                        "reasoning",
                    ),
                ),
                _ModelTierRecord(
                    provider="anthropic",
                    model="haiku",
                    tier="cheap",
                    capabilities=("streaming", "tool_use", "low_latency"),
                ),
                _ModelTierRecord(
                    provider="moonshot",
                    model="kimi-k2.6",
                    tier="cheap",
                    capabilities=("streaming", "coding", "long_context"),
                ),
                _ModelTierRecord(
                    provider="fireworks",
                    model="kimi-k2p6",
                    tier="cheap",
                    capabilities=("streaming", "coding", "long_context"),
                ),
                _ModelTierRecord(
                    provider="fireworks",
                    model="accounts/fireworks/models/kimi-k2-instruct",
                    tier="cheap",
                    capabilities=("streaming", "coding", "long_context"),
                ),
                _ModelTierRecord(
                    provider="openai",
                    model="gpt-5.5",
                    tier="sota",
                    capabilities=("reasoning", "tool_use", "json_schema", "coding"),
                ),
            )
        )

    @classmethod
    def from_records(cls, records: tuple[Mapping[str, object], ...]) -> Self:
        return cls(tuple(_ModelTierRecord.model_validate(record) for record in records))

    def resolve(
        self,
        *,
        provider: str,
        model: str,
        requestedCapabilities: tuple[str, ...] = (),
        requested_capabilities: tuple[str, ...] = (),
    ) -> ResolvedModelTier:
        request = _ResolveRequest(
            provider=provider,
            model=model,
            requestedCapabilities=requestedCapabilities or requested_capabilities,
        )
        record = self._records.get((request.provider, request.model))
        if record is None:
            return ResolvedModelTier(
                provider=request.provider,
                model=request.model,
                tier="standard",
                capabilities=(),
                droppedRequestedCapabilities=request.requested_capabilities,
                reasonCodes=("unknown_model_standard_no_elevated_capabilities",),
            )

        capabilities = record.capabilities
        dropped = tuple(
            capability
            for capability in request.requested_capabilities
            if capability not in capabilities
        )
        return ResolvedModelTier(
            provider=record.provider,
            model=record.model,
            tier=record.tier,
            capabilities=capabilities,
            droppedRequestedCapabilities=dropped,
        )


def _validate_provider(value: str) -> str:
    clean = value.strip().casefold()
    if _UNSAFE_LABEL_RE.search(clean) or not _PROVIDER_RE.fullmatch(clean):
        raise ValueError("provider label must be a safe server-side provider label")
    return clean


def _validate_model(value: str) -> str:
    clean = value.strip().casefold()
    if _UNSAFE_MODEL_LABEL_RE.search(clean) or not _MODEL_RE.fullmatch(clean):
        raise ValueError("model label must be a safe server-side model label")
    return clean


def available_child_model_routes(env: Mapping[str, str]) -> list[str]:
    """Sorted ``provider:model (tier)`` routes a child spawn may target.

    The union of the two sources ``child_runner_live._validate_route`` accepts:
    the built-in :class:`ModelTierRegistry` AND the operator's deployment route
    allowlist. Single source of truth for both the SpawnAgent tool guidance and
    the system-prompt capability block, so the model is told exactly the routes
    that pass validation. Fail-soft: any error contributes nothing.
    """
    tiers: dict[str, str] = {}
    try:
        for (provider, model), record in ModelTierRegistry.with_defaults()._records.items():
            tiers[f"{provider}:{model}"] = str(getattr(record, "tier", "") or "")
    except Exception:  # noqa: BLE001 — registry read must never raise here.
        pass
    try:
        from magi_agent.config.env import (  # noqa: PLC0415
            operator_allowed_model_routes,
        )

        for provider, model in operator_allowed_model_routes(env):
            tiers.setdefault(f"{provider}:{model}", "")
    except Exception:  # noqa: BLE001 — allowlist read must never raise here.
        pass
    return [
        f"{route} ({tier})" if tier else route for route, tier in sorted(tiers.items())
    ]


__all__ = [
    "ModelCapability",
    "ModelTier",
    "ModelTierPolicy",
    "ModelTierRegistry",
    "ModelUsagePhase",
    "ResolvedModelTier",
    "available_child_model_routes",
]
