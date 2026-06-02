from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from magi_agent.harness.presets import (
    BuiltinHarnessPreset,
    PresetCategory,
    PresetHookContribution,
    PresetSource,
    builtin_preset_catalog,
)
from magi_agent.harness.profiles import RuntimeProfile, build_default_profile


PolicySourceName = Literal[
    "platform hard safety policy",
    "org policy",
    "bot runtime policy",
    "security-critical Core Pack policy",
    "native plugin policy",
    "user agent.config.yaml",
    "user USER.md",
    "session-level temporary policy",
    "model-suggested plans",
]


class PolicySourcePrecedenceEntry(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    rank: int
    source: PolicySourceName
    authoritative: bool
    non_authoritative_reason: str | None = Field(
        default=None, alias="nonAuthoritativeReason"
    )


class HarnessHookContributionSummary(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    hook: str
    hook_points: tuple[str, ...] = Field(alias="hookPoints")
    blocking: bool
    fail_open: bool | None = Field(default=None, alias="failOpen")
    fail_closed: bool | None = Field(default=None, alias="failClosed")
    timeout_ms: int = Field(alias="timeoutMs")
    env_gates: tuple[str, ...] = Field(default=(), alias="envGates")
    config_gates: tuple[str, ...] = Field(default=(), alias="configGates")
    runtime_default_on: bool | None = Field(default=None, alias="runtimeDefaultOn")
    behavior_notes: tuple[str, ...] = Field(default=(), alias="behaviorNotes")


class HarnessPresetPolicySummary(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    key: str
    category: PresetCategory
    source: PresetSource = "builtin"
    default_on: bool = Field(alias="defaultOn")
    opt_out: bool = Field(alias="optOut")
    hard_safety: bool = Field(alias="hardSafety")
    security_critical: bool = Field(alias="securityCritical")
    dashboard_toggleable: bool = Field(alias="dashboardToggleable")
    hook_points: tuple[str, ...] = Field(default=(), alias="hookPoints")
    blocking: bool | None = None
    fail_open: bool | None = Field(default=None, alias="failOpen")
    fail_closed: bool | None = Field(default=None, alias="failClosed")
    timeout_ms: int | None = Field(default=None, alias="timeoutMs")
    env_gates: tuple[str, ...] = Field(default=(), alias="envGates")
    config_gates: tuple[str, ...] = Field(default=(), alias="configGates")
    scope_hints: tuple[str, ...] = Field(default=(), alias="scopeHints")
    contributed_hooks: tuple[str, ...] = Field(default=(), alias="contributedHooks")
    contributed_tools: tuple[str, ...] = Field(default=(), alias="contributedTools")
    contributed_ledgers: tuple[str, ...] = Field(default=(), alias="contributedLedgers")
    verifier_gates: tuple[str, ...] = Field(default=(), alias="verifierGates")
    hook_contributions: tuple[HarnessHookContributionSummary, ...] = Field(
        default=(), alias="hookContributions"
    )


class HarnessPolicyState(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    profile_name: str = Field(alias="profileName")
    traffic_attached: bool = Field(default=False, alias="trafficAttached")
    execution_attached: bool = Field(default=False, alias="executionAttached")
    source_precedence: tuple[PolicySourcePrecedenceEntry, ...] = Field(
        alias="sourcePrecedence"
    )
    presets: tuple[HarnessPresetPolicySummary, ...]


SOURCE_PRECEDENCE: tuple[PolicySourcePrecedenceEntry, ...] = (
    PolicySourcePrecedenceEntry(
        rank=1, source="platform hard safety policy", authoritative=True
    ),
    PolicySourcePrecedenceEntry(rank=2, source="org policy", authoritative=True),
    PolicySourcePrecedenceEntry(rank=3, source="bot runtime policy", authoritative=True),
    PolicySourcePrecedenceEntry(
        rank=4, source="security-critical Core Pack policy", authoritative=True
    ),
    PolicySourcePrecedenceEntry(rank=5, source="native plugin policy", authoritative=True),
    PolicySourcePrecedenceEntry(
        rank=6, source="user agent.config.yaml", authoritative=True
    ),
    PolicySourcePrecedenceEntry(rank=7, source="user USER.md", authoritative=True),
    PolicySourcePrecedenceEntry(
        rank=8, source="session-level temporary policy", authoritative=True
    ),
    PolicySourcePrecedenceEntry(
        rank=9,
        source="model-suggested plans",
        authoritative=False,
        non_authoritative_reason="never authoritative over policy",
    ),
)


def build_harness_policy_state(
    profile: RuntimeProfile | None = None,
    presets: tuple[BuiltinHarnessPreset, ...] | None = None,
) -> HarnessPolicyState:
    resolved_profile = profile or build_default_profile()
    catalog = builtin_preset_catalog() if presets is None else presets
    profile_keys = set(resolved_profile.builtin_preset_keys)
    selected_presets = tuple(
        preset.model_copy()
        for preset in catalog
        if not profile_keys or preset.key in profile_keys
    )

    return HarnessPolicyState(
        profile_name=resolved_profile.name,
        traffic_attached=False,
        execution_attached=False,
        source_precedence=tuple(entry.model_copy() for entry in SOURCE_PRECEDENCE),
        presets=tuple(_preset_summary(preset) for preset in selected_presets),
    )


def _preset_summary(preset: BuiltinHarnessPreset) -> HarnessPresetPolicySummary:
    fail_closed = _fail_closed_from_fail_open(preset.fail_open)

    return HarnessPresetPolicySummary(
        key=preset.key,
        category=preset.category,
        source=preset.source,
        default_on=preset.default_on,
        opt_out=preset.opt_out,
        hard_safety=preset.hard_safety,
        security_critical=preset.security_critical,
        dashboard_toggleable=_dashboard_toggleable(preset),
        hook_points=preset.hook_points,
        blocking=preset.blocking,
        fail_open=preset.fail_open,
        fail_closed=fail_closed,
        timeout_ms=preset.timeout_ms,
        env_gates=preset.env_gates,
        config_gates=preset.config_gates,
        scope_hints=preset.scope_hints,
        contributed_hooks=preset.contributed_hooks,
        contributed_tools=preset.contributed_tools,
        contributed_ledgers=preset.contributed_ledgers,
        verifier_gates=preset.verifier_gates,
        hook_contributions=tuple(
            _hook_contribution_summary(contribution)
            for contribution in preset.hook_contributions
        ),
    )


def _hook_contribution_summary(
    contribution: PresetHookContribution,
) -> HarnessHookContributionSummary:
    return HarnessHookContributionSummary(
        hook=contribution.hook,
        hook_points=contribution.hook_points,
        blocking=contribution.blocking,
        fail_open=contribution.fail_open,
        fail_closed=_fail_closed_from_fail_open(contribution.fail_open),
        timeout_ms=contribution.timeout_ms,
        env_gates=contribution.env_gates,
        config_gates=contribution.config_gates,
        runtime_default_on=contribution.runtime_default_on,
        behavior_notes=contribution.behavior_notes,
    )


def _dashboard_toggleable(preset: BuiltinHarnessPreset) -> bool:
    return preset.opt_out and not preset.hard_safety and not preset.security_critical


def _fail_closed_from_fail_open(fail_open: bool | None) -> bool | None:
    if fail_open is None:
        return None
    return not fail_open
