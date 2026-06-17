from __future__ import annotations

from collections.abc import Mapping
import re
from typing import Literal, NamedTuple, Self, TypeAlias

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


class ChildRoute(NamedTuple):
    """A validated child-spawn route (canonical ``provider``/``model``)."""

    provider: str
    model: str


# Registry provider labels for the gemini/google dual-alias pair.  The CLI
# provider is "gemini"; the registry stores records under BOTH "gemini" and
# "google".  This map expands a cli-provider name to all registry labels it covers.
_PROVIDER_REGISTRY_ALIASES: dict[str, frozenset[str]] = {
    "gemini": frozenset({"gemini", "google"}),
}


def _keyed_registry_providers(
    env: Mapping[str, str],
) -> set[str] | None:
    """Registry-provider labels whose API key is configured, or ``None`` to mean
    'do not filter' (fail-open: gate OFF, no keys at all, or any error).

    Returns a ``set[str]`` of registry provider labels when gate ON + at least
    one key is found, else ``None``.  Callers treat ``None`` as "skip filtering".
    """
    try:
        from magi_agent.cli.providers import (  # noqa: PLC0415
            configured_providers,
            resolve_provider_config,
        )
        from magi_agent.config.env import (  # noqa: PLC0415
            is_key_aware_model_routes_enabled,
        )

        if not is_key_aware_model_routes_enabled(env):
            return None

        keyed = configured_providers(env=env)
        if not keyed:
            # No keys at all — fail-open to legacy behavior.
            return None

        # Map cli-provider names → registry provider labels.
        registry_set: set[str] = set()
        for p in keyed:
            registry_set |= _PROVIDER_REGISTRY_ALIASES.get(p, frozenset({p}))

        # Also include the *selected* provider's registry label so the configured
        # bot can always route on its own model even if it only has cheap-tier records.
        try:
            sel = resolve_provider_config(env=env)
            if sel:
                registry_set |= _PROVIDER_REGISTRY_ALIASES.get(
                    sel.provider, frozenset({sel.provider})
                )
        except Exception:  # noqa: BLE001 — fail-soft: selected provider is best-effort
            pass

        return registry_set
    except Exception:  # noqa: BLE001 — any error → fail-open (never filter)
        return None


def resolve_child_route(
    provider: str, model: str, env: Mapping[str, str]
) -> ChildRoute | None:
    """Canonical ACCEPTANCE authority for a child-spawn ``(provider, model)``.

    Returns the route a child may run on, else ``None`` (caller blocks). A route
    is accepted iff it (a) resolves in the built-in :class:`ModelTierRegistry`
    without an ``unknown_model_*`` reason code — returned canonical/normalised —
    OR (b) is in the operator's deployment route allowlist — returned as given.

    When ``MAGI_KEY_AWARE_MODEL_ROUTES_ENABLED`` is ON and at least one provider
    key is configured, registry-resolved routes whose provider is NOT in the keyed
    set are treated as not accepted (fall through to allowlist), unless the route
    matches the currently selected provider/model (fail-open for the configured
    bot's own route).

    This is the SINGLE function ``child_runner_live._validate_route`` delegates to
    and that :func:`available_child_model_routes` enumerates against, so the
    routes the model is TOLD about (prompt/tool guidance) can never drift from the
    routes the runner ACCEPTS. Never raises.
    """
    # Compute keyed providers once; None means "do not filter".
    keyed = _keyed_registry_providers(env)

    # Determine the selected provider/model for the fail-open "own route" check.
    sel_provider: str | None = None
    sel_model: str | None = None
    if keyed is not None:
        try:
            from magi_agent.cli.providers import resolve_provider_config  # noqa: PLC0415

            sel = resolve_provider_config(env=env)
            if sel:
                sel_provider = sel.provider.strip().casefold()
                sel_model = sel.model.strip().casefold()
        except Exception:  # noqa: BLE001 — fail-soft
            pass

    try:
        resolved = ModelTierRegistry.with_defaults().resolve(
            provider=provider, model=model
        )
    except Exception:  # noqa: BLE001 — label-validation failure → not a registry route.
        resolved = None
    if resolved is not None:
        reason_codes = tuple(getattr(resolved, "reason_codes", ()) or ())
        if not any("unknown_model" in code for code in reason_codes):
            resolved_provider = str(getattr(resolved, "provider", provider))
            resolved_model = str(getattr(resolved, "model", model))
            # Key-aware filter: if keyed is not None, only accept when the
            # resolved provider is in the keyed set OR it is the selected route.
            if keyed is None or resolved_provider in keyed:
                return ChildRoute(resolved_provider, resolved_model)
            # Not in keyed set — check if it is the selected (configured) route.
            if (
                sel_provider is not None
                and sel_model is not None
                and resolved_provider == sel_provider
                and resolved_model == sel_model
            ):
                return ChildRoute(resolved_provider, resolved_model)
            # Fall through to allowlist / selected check below.

    # Always accept if the (provider, model) matches the selected route
    # (handles custom model ids not in the static registry).
    if keyed is not None and sel_provider is not None and sel_model is not None:
        if (
            provider.strip().casefold() == sel_provider
            and model.strip().casefold() == sel_model
        ):
            return ChildRoute(provider, model)

    try:
        from magi_agent.config.env import (  # noqa: PLC0415
            operator_allowed_model_routes,
        )

        allowlist = operator_allowed_model_routes(env)
        if (provider.strip().casefold(), model.strip().casefold()) in allowlist:
            return ChildRoute(provider, model)
    except Exception:  # noqa: BLE001 — allowlist read must never block validation.
        pass
    return None


def available_child_model_routes(env: Mapping[str, str]) -> list[str]:
    """Sorted ``provider:model (tier)`` routes a child spawn may target.

    The union of the two sources :func:`resolve_child_route` accepts: the
    built-in :class:`ModelTierRegistry` AND the operator's deployment route
    allowlist. Single source of truth for both the SpawnAgent tool guidance and
    the system-prompt capability block, so the model is told exactly the routes
    that pass validation. A consistency test asserts every listed route resolves.

    When ``MAGI_KEY_AWARE_MODEL_ROUTES_ENABLED`` is ON and at least one provider
    key is configured, only routes whose provider is in the keyed set are included
    from the built-in registry (the operator allowlist is always included in full).
    The selected provider's own model is always included even if it is a custom id
    not present in the static registry.  When OFF or no keys are found, output is
    byte-identical to today (fail-open).  Fail-soft: any error contributes nothing.
    """
    # Compute keyed providers; None means "do not filter".
    keyed = _keyed_registry_providers(env)

    tiers: dict[str, str] = {}
    try:
        for (provider, model), record in ModelTierRegistry.with_defaults()._records.items():
            if keyed is not None and provider not in keyed:
                continue
            tiers[f"{provider}:{model}"] = str(getattr(record, "tier", "") or "")
    except Exception:  # noqa: BLE001 — registry read must never raise here.
        pass

    # When gate is ON, also ensure the selected provider's own model is present
    # (handles custom model ids not in the static registry).
    if keyed is not None:
        try:
            from magi_agent.cli.providers import resolve_provider_config  # noqa: PLC0415

            sel = resolve_provider_config(env=env)
            if sel:
                tiers.setdefault(f"{sel.provider}:{sel.model}", "")
        except Exception:  # noqa: BLE001 — fail-soft: selected provider is best-effort
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
    "ChildRoute",
    "available_child_model_routes",
    "resolve_child_route",
]
