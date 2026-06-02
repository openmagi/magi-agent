from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class PresetCategory(StrEnum):
    ANSWER = "answer"
    FACT = "fact"
    CODING = "coding"
    TASK = "task"
    OUTPUT = "output"
    RESEARCH = "research"
    MEMORY = "memory"
    SECURITY = "security"


PresetSource = Literal["builtin", "native-plugin", "custom-plugin", "config"]


class HookTimeout(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    hook: str
    timeout_ms: int = Field(alias="timeoutMs")


class PresetHookContribution(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    hook: str
    hook_points: tuple[str, ...] = Field(alias="hookPoints")
    blocking: bool
    fail_open: bool | None = Field(default=None, alias="failOpen")
    timeout_ms: int = Field(alias="timeoutMs")
    env_gates: tuple[str, ...] = Field(default=(), alias="envGates")
    config_gates: tuple[str, ...] = Field(default=(), alias="configGates")
    runtime_default_on: bool | None = Field(default=None, alias="runtimeDefaultOn")
    behavior_notes: tuple[str, ...] = Field(default=(), alias="behaviorNotes")


class BuiltinHarnessPreset(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    key: str
    category: PresetCategory
    source: PresetSource = "builtin"
    default_on: bool = Field(alias="defaultOn")
    opt_out: bool = Field(alias="optOut")
    hard_safety: bool = Field(default=False, alias="hardSafety")
    security_critical: bool = Field(default=False, alias="securityCritical")
    hook_points: tuple[str, ...] = Field(default=(), alias="hookPoints")
    blocking: bool | None = None
    fail_open: bool | None = Field(default=None, alias="failOpen")
    timeout_ms: int | None = Field(default=None, alias="timeoutMs")
    hook_timeouts_ms: tuple[HookTimeout, ...] = Field(default=(), alias="hookTimeoutsMs")
    hook_contributions: tuple[PresetHookContribution, ...] = Field(
        default=(), alias="hookContributions"
    )
    env_gates: tuple[str, ...] = Field(default=(), alias="envGates")
    config_gates: tuple[str, ...] = Field(default=(), alias="configGates")
    scope_hints: tuple[str, ...] = Field(default=(), alias="scopeHints")
    contributed_hooks: tuple[str, ...] = Field(default=(), alias="contributedHooks")
    contributed_tools: tuple[str, ...] = Field(default=(), alias="contributedTools")
    contributed_ledgers: tuple[str, ...] = Field(default=(), alias="contributedLedgers")
    verifier_gates: tuple[str, ...] = Field(default=(), alias="verifierGates")

    @model_validator(mode="after")
    def hard_safety_presets_cannot_be_opt_out(self) -> Self:
        if self.hard_safety and self.opt_out:
            raise ValueError("hard-safety presets cannot be opt-out")
        if self.hard_safety and not self.default_on:
            raise ValueError("hard-safety presets must be default-on")
        if self.hard_safety and not self.security_critical:
            raise ValueError("hard-safety presets must be security-critical")
        return self

    def model_copy(self, *, update: dict[str, Any] | None = None, deep: bool = False) -> Self:
        data = self.model_dump()
        if update:
            data.update(update)
        return self.__class__.model_validate(data)


def _preset(
    key: str,
    category: PresetCategory,
    *,
    default_on: bool = True,
    opt_out: bool = True,
    hard_safety: bool = False,
    security_critical: bool = False,
    hook_points: tuple[str, ...] = (),
    blocking: bool | None = None,
    fail_open: bool | None = None,
    timeout_ms: int | None = None,
    hook_timeouts_ms: dict[str, int] | None = None,
    hook_contributions: tuple[PresetHookContribution, ...] = (),
    env_gates: tuple[str, ...] = (),
    config_gates: tuple[str, ...] = (),
    scope_hints: tuple[str, ...] = (),
    contributed_hooks: tuple[str, ...] = (),
    contributed_tools: tuple[str, ...] = (),
    contributed_ledgers: tuple[str, ...] = (),
    verifier_gates: tuple[str, ...] = (),
) -> BuiltinHarnessPreset:
    return BuiltinHarnessPreset(
        key=key,
        category=category,
        default_on=default_on,
        opt_out=opt_out,
        hard_safety=hard_safety,
        security_critical=security_critical,
        hook_points=hook_points,
        blocking=blocking,
        fail_open=fail_open,
        timeout_ms=timeout_ms,
        hook_timeouts_ms=tuple(
            HookTimeout(hook=hook, timeout_ms=timeout_ms)
            for hook, timeout_ms in sorted((hook_timeouts_ms or {}).items())
        ),
        hook_contributions=hook_contributions,
        env_gates=env_gates,
        config_gates=config_gates,
        scope_hints=scope_hints,
        contributed_hooks=contributed_hooks,
        contributed_tools=contributed_tools,
        contributed_ledgers=contributed_ledgers,
        verifier_gates=verifier_gates,
    )


def _hook(
    hook: str,
    hook_points: tuple[str, ...],
    *,
    blocking: bool,
    timeout_ms: int,
    fail_open: bool | None = None,
    env_gates: tuple[str, ...] = (),
    config_gates: tuple[str, ...] = (),
    runtime_default_on: bool | None = None,
    behavior_notes: tuple[str, ...] = (),
) -> PresetHookContribution:
    return PresetHookContribution(
        hook=hook,
        hook_points=hook_points,
        blocking=blocking,
        fail_open=fail_open,
        timeout_ms=timeout_ms,
        env_gates=env_gates,
        config_gates=config_gates,
        runtime_default_on=runtime_default_on,
        behavior_notes=behavior_notes,
    )


def _security_preset(
    key: str,
    *,
    hook_points: tuple[str, ...],
    hook_contributions: tuple[PresetHookContribution, ...],
    blocking: bool | None = None,
    fail_open: bool | None = None,
    timeout_ms: int | None = None,
    env_gates: tuple[str, ...] = (),
    config_gates: tuple[str, ...] = (),
) -> BuiltinHarnessPreset:
    return _preset(
        key,
        PresetCategory.SECURITY,
        opt_out=False,
        hard_safety=True,
        security_critical=True,
        hook_points=hook_points,
        blocking=blocking,
        fail_open=fail_open,
        timeout_ms=timeout_ms,
        env_gates=env_gates,
        config_gates=config_gates,
        scope_hints=("all-runs", "all-agents"),
        hook_contributions=hook_contributions,
        contributed_hooks=tuple(contribution.hook for contribution in hook_contributions),
    )


# Metadata only: this is an OpenMagi product policy catalog intended to map to
# future ADK callbacks/plugins/evals. It does not import or attach ADK runtime
# primitives.
_BUILTIN_PRESETS: tuple[BuiltinHarnessPreset, ...] = tuple(
    sorted(
        (
            _preset("answer-quality", PresetCategory.ANSWER, hook_points=("afterLLMCall",), verifier_gates=("answer-quality",)),
            _preset("completion-evidence", PresetCategory.ANSWER, hook_points=("afterLLMCall",), verifier_gates=("completion-evidence",)),
            _preset("pre-refusal", PresetCategory.ANSWER, hook_points=("beforeLLMCall",), blocking=True, fail_open=True),
            _preset("output-purity", PresetCategory.ANSWER, hook_points=("afterLLMCall",), verifier_gates=("output-purity",)),
            _preset("deferral-blocker", PresetCategory.ANSWER, hook_points=("afterLLMCall",), verifier_gates=("deferral-blocker",)),
            _preset("fact-grounding", PresetCategory.FACT, hook_points=("afterLLMCall",), verifier_gates=("grounding-required",)),
            _preset("self-claim", PresetCategory.FACT, hook_points=("afterLLMCall",), verifier_gates=("self-claim",)),
            _preset("resource-existence", PresetCategory.FACT, hook_points=("beforeToolUse", "afterToolUse"), verifier_gates=("resource-existence",)),
            _preset("claim-citation", PresetCategory.FACT, hook_points=("afterLLMCall",), contributed_ledgers=("source-ledger",), verifier_gates=("claim-citation",)),
            _preset("deterministic-evidence", PresetCategory.FACT, hook_points=("afterToolUse", "afterLLMCall"), verifier_gates=("deterministic-evidence",)),
            _preset("coding-verification", PresetCategory.CODING, hook_points=("beforeCommit",), blocking=True, fail_open=True, verifier_gates=("coding-verification",)),
            _preset(
                "coding-context",
                PresetCategory.CODING,
                hook_points=("beforeLLMCall",),
                blocking=True,
                fail_open=True,
                timeout_ms=10_000,
                hook_timeouts_ms={"repo-map": 12_000, "coding-context": 10_000, "focus-chain": 2_000},
                config_gates=("repo-map", "coding-context", "focus-chain"),
                hook_contributions=(
                    _hook(
                        "repo-map",
                        ("beforeLLMCall",),
                        blocking=True,
                        fail_open=True,
                        timeout_ms=12_000,
                        env_gates=("CORE_AGENT_REPO_MAP",),
                        config_gates=("repo-map",),
                        runtime_default_on=True,
                        behavior_notes=("default", "existing runtime gate; source noted by ledger #816"),
                    ),
                    _hook(
                        "coding-context",
                        ("beforeLLMCall",),
                        blocking=True,
                        fail_open=True,
                        timeout_ms=10_000,
                        env_gates=("CORE_AGENT_CODING_CONTEXT",),
                        config_gates=("coding-context",),
                        runtime_default_on=True,
                    ),
                    _hook(
                        "focus-chain",
                        ("beforeLLMCall",),
                        blocking=True,
                        fail_open=True,
                        timeout_ms=2_000,
                        env_gates=("MAGI_FOCUS_CHAIN",),
                        config_gates=("focus-chain",),
                        runtime_default_on=False,
                    ),
                ),
                contributed_hooks=("coding-context", "focus-chain", "repo-map"),
            ),
            _preset("coding-workspace-lock", PresetCategory.CODING, hook_points=("beforeToolUse",), blocking=True, fail_open=False),
            _preset("coding-child-review", PresetCategory.CODING, hook_points=("afterCommit",), verifier_gates=("coding-child-review",)),
            _preset(
                "benchmark-verifier",
                PresetCategory.CODING,
                hook_points=("beforeCommit",),
                blocking=True,
                fail_open=True,
                timeout_ms=65_000,
                env_gates=("MAGI_PRESET_VERIFIERS",),
                contributed_hooks=("before-commit-verifier",),
                verifier_gates=("benchmark-verifier", "report-only-evidence", "grounding-required"),
            ),
            _preset("task-contract", PresetCategory.TASK, hook_points=("beforeTurnStart", "onTaskCheckpoint"), verifier_gates=("task-contract",)),
            _preset("goal-progress", PresetCategory.TASK, hook_points=("onTaskCheckpoint",), verifier_gates=("goal-progress",)),
            _preset("task-board-completion", PresetCategory.TASK, hook_points=("onTaskCheckpoint", "afterTurnEnd"), verifier_gates=("task-board-completion",)),
            _preset("output-delivery", PresetCategory.OUTPUT, hook_points=("afterLLMCall",), verifier_gates=("output-delivery",)),
            _preset("artifact-delivery", PresetCategory.OUTPUT, hook_points=("onArtifactCreated", "afterTurnEnd"), verifier_gates=("artifact-delivery",)),
            _preset("response-language", PresetCategory.OUTPUT, hook_points=("afterLLMCall",), config_gates=("response-language-policy",), scope_hints=("configured-policy",)),
            _preset("parallel-research", PresetCategory.RESEARCH, default_on=False, hook_points=("beforeTurnStart",), scope_hints=("research-agent",)),
            _preset("source-authority", PresetCategory.RESEARCH, hook_points=("afterToolUse", "afterLLMCall"), contributed_ledgers=("source-ledger",), verifier_gates=("source-authority",)),
            _preset("memory-continuity", PresetCategory.MEMORY, hook_points=("beforeCompaction", "afterCompaction"), contributed_ledgers=("memory-ledger",)),
            _security_preset(
                "dangerous-patterns",
                hook_points=("beforeToolUse",),
                blocking=True,
                timeout_ms=3_000,
                env_gates=("CORE_AGENT_DANGEROUS_PATTERNS",),
                config_gates=("dangerous_patterns", "disable_builtin_hooks"),
                hook_contributions=(
                    _hook(
                        "builtin:dangerous-patterns",
                        ("beforeToolUse",),
                        blocking=True,
                        timeout_ms=3_000,
                        env_gates=("CORE_AGENT_DANGEROUS_PATTERNS",),
                        config_gates=("dangerous_patterns", "disable_builtin_hooks"),
                        runtime_default_on=True,
                        behavior_notes=("permission_decision ask/deny",),
                    ),
                ),
            ),
            _security_preset(
                "path-escape",
                hook_points=("beforeToolUse", "beforeCommit"),
                blocking=True,
                env_gates=("CORE_AGENT_RESOURCE_BOUNDARY",),
                hook_contributions=(
                    _hook(
                        "builtin:resource-boundary",
                        ("beforeToolUse",),
                        blocking=True,
                        fail_open=False,
                        timeout_ms=500,
                        env_gates=("CORE_AGENT_RESOURCE_BOUNDARY",),
                        runtime_default_on=True,
                    ),
                    _hook(
                        "builtin:resource-boundary-before-commit",
                        ("beforeCommit",),
                        blocking=True,
                        fail_open=True,
                        timeout_ms=2_000,
                        env_gates=("CORE_AGENT_RESOURCE_BOUNDARY",),
                        runtime_default_on=True,
                        behavior_notes=("beforeCommit may fail open after retry exhaustion",),
                    ),
                ),
            ),
            _security_preset(
                "secret-exposure",
                hook_points=("beforeCommit",),
                blocking=True,
                fail_open=True,
                timeout_ms=500,
                env_gates=("CORE_AGENT_SECRET_EXPOSURE",),
                hook_contributions=(
                    _hook(
                        "builtin:secret-exposure-gate",
                        ("beforeCommit",),
                        blocking=True,
                        fail_open=True,
                        timeout_ms=500,
                        env_gates=("CORE_AGENT_SECRET_EXPOSURE",),
                        runtime_default_on=True,
                        behavior_notes=("violation blocks", "internal errors continue"),
                    ),
                ),
            ),
            _security_preset(
                "git-safety",
                hook_points=("beforeToolUse",),
                blocking=True,
                fail_open=False,
                timeout_ms=1_000,
                hook_contributions=(
                    _hook(
                        "builtin:git-safety-gate",
                        ("beforeToolUse",),
                        blocking=True,
                        fail_open=False,
                        timeout_ms=1_000,
                        runtime_default_on=True,
                        behavior_notes=("blocks destructive git commands",),
                    ),
                ),
            ),
            _security_preset(
                "sealed-files",
                hook_points=("beforeTurnStart", "beforeCommit", "afterCommit"),
                env_gates=("CORE_AGENT_SEALED_FILES",),
                config_gates=("sealed_files", "sealed_files_allowlist_turns", "disable_builtin_hooks"),
                hook_contributions=(
                    _hook(
                        "builtin:sealed-files:beforeTurnStart",
                        ("beforeTurnStart",),
                        blocking=False,
                        fail_open=True,
                        timeout_ms=5_000,
                        env_gates=("CORE_AGENT_SEALED_FILES",),
                        config_gates=("sealed_files", "sealed_files_allowlist_turns", "disable_builtin_hooks"),
                        runtime_default_on=True,
                    ),
                    _hook(
                        "builtin:sealed-files",
                        ("beforeCommit",),
                        blocking=True,
                        fail_open=False,
                        timeout_ms=5_000,
                        env_gates=("CORE_AGENT_SEALED_FILES",),
                        config_gates=("sealed_files", "sealed_files_allowlist_turns", "disable_builtin_hooks"),
                        runtime_default_on=True,
                    ),
                    _hook(
                        "builtin:sealed-files:afterCommit",
                        ("afterCommit",),
                        blocking=False,
                        fail_open=True,
                        timeout_ms=5_000,
                        env_gates=("CORE_AGENT_SEALED_FILES",),
                        config_gates=("sealed_files", "sealed_files_allowlist_turns", "disable_builtin_hooks"),
                        runtime_default_on=True,
                    ),
                ),
            ),
            _security_preset(
                "arity-permission",
                hook_points=("beforeToolUse",),
                blocking=True,
                timeout_ms=3_000,
                env_gates=("CORE_AGENT_ARITY_PERMISSION",),
                config_gates=("arity_rules",),
                hook_contributions=(
                    _hook(
                        "builtin:arity-permission-gate",
                        ("beforeToolUse",),
                        blocking=True,
                        timeout_ms=3_000,
                        env_gates=("CORE_AGENT_ARITY_PERMISSION",),
                        config_gates=("arity_rules",),
                        runtime_default_on=False,
                        behavior_notes=("permission_decision ask/deny",),
                    ),
                ),
            ),
        ),
        key=lambda preset: preset.key,
    )
)

_PRESET_BY_KEY: dict[str, BuiltinHarnessPreset] = {preset.key: preset for preset in _BUILTIN_PRESETS}


def builtin_preset_catalog() -> tuple[BuiltinHarnessPreset, ...]:
    return tuple(preset.model_copy() for preset in _BUILTIN_PRESETS)


def builtin_preset_keys() -> tuple[str, ...]:
    return tuple(preset.key for preset in _BUILTIN_PRESETS)


def builtin_preset_by_key(key: str) -> BuiltinHarnessPreset:
    return _PRESET_BY_KEY[key].model_copy()
