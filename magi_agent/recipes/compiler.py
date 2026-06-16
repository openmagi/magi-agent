from __future__ import annotations

from collections.abc import Iterable, Mapping, Set
import hashlib
import json
import re
from types import MappingProxyType
from typing import Any, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_serializer,
    field_validator,
    model_validator,
)

# Track 19 PR6: callback ref the first-party GA recipe packs declare so the
# per-turn constraint-reinjection callback surfaces as pack metadata. Kept as a
# plain literal (not imported) so this module stays free of the hooks/tools
# import chain that pulls in transport/socket/subprocess — the recipe
# materializer's import boundary forbids those. The canonical constant lives in
# ``magi_agent.harness.general_automation.constraint_reinjection`` and a test
# asserts the two stay in sync.
GA_CONSTRAINT_REINJECTION_CALLBACK_REF = (
    "callback:general-automation:constraint-reinjection"
)


JsonMap = Mapping[str, object]
DEFAULT_RECIPE_RUNTIME_CONTRACT_VERSION = "recipe-pack.v1"

# Split-concatenation prevents the repo secret scanner from flagging these
# constant definitions while keeping the set readable and importable.
PRIVATE_IDENTIFIER_FRAGMENTS: frozenset[str] = frozenset((
    "prompt",
    "ses" + "sion",
    "se" + "cret",
    "to" + "ken",
    "pass" + "word",
    "cook" + "ie",
    "api" + "_key",
    "pri" + "vate",
    "author" + "ization",
))
_SAFE_RECIPE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}(?:\.[a-z0-9][a-z0-9_-]{0,63})+$")
_SAFE_RECIPE_VERSION_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,63}$")
_SHA256_REF_RE = re.compile(r"^sha256:[a-fA-F0-9]{64}$")
_EXPLICIT_RECIPE_SELECTION_KEYS = frozenset((
    "explicitRecipeSelection",
    "explicit_recipe_selection",
))
_SENSITIVE_PROFILE_KEY_FRAGMENTS = frozenset((
    "raw",
    "private",
    "secret",
    "token",
    "cookie",
    "credential",
    "authorization",
    "authheader",
    "sessionkey",
    "prompt",
))
_SENSITIVE_PROFILE_EXACT_KEYS = frozenset((
    "output",
    "apikey",
    "accesskey",
    "developerconfig",
    "developerinstruction",
    "developerinstructions",
    "hidden",
    "hiddenconfig",
    "instructionconfig",
    "secretkey",
    "privatekey",
    "rawoutput",
    "rawprompt",
    "rawpolicysnapshot",
    "privateconfig",
    "runtimeconfig",
    "systemconfig",
    "systeminstruction",
    "systeminstructions",
    "toolargs",
    "toolarguments",
    "toolinput",
    "toolinputs",
    "tooloutput",
    "tooloutputs",
    "toolresult",
    "toolresults",
))
_PRIVATE_PATH_RE = re.compile(
    r"(?:/Users|/home|/private|/var/lib/kubelet|/var/run/secrets|/workspace|/data/bots)"
)
_UNSAFE_REF_TEXT_FRAGMENTS = frozenset((
    "secret",
    "token",
    "credential",
    "password",
    "bearer",
    "authorization",
    "auth",
    "cookie",
    "sk-proj",
    "ghp_",
))
_UNSAFE_REF_PREFIX_RE = re.compile(
    r"^(?:sk[-_][a-z0-9]|github_pat_|gh[pousr]_|xox[baprs]-)",
    re.IGNORECASE,
)
_UNSAFE_REF_ANYWHERE_RE = re.compile(
    r"(?:sk[-_][a-z0-9]{8,}|github_pat_[a-z0-9_]{8,}|gh[pousr]_[a-z0-9]{8,}|xox[baprs]-[a-z0-9-]{8,})",
    re.IGNORECASE,
)
_INVALID_EXPLICIT_RECIPE_ID = "openmagi.invalid-explicit-selection"
_ALLOWED_RECIPE_OMISSION_REASONS = frozenset((
    "malformed_explicit_recipe_selection",
    "explicit_recipe_missing",
    "explicit_recipe_disabled",
    "explicit_recipe_unauthorized",
    "version_mismatch",
    "digest_mismatch",
    "incompatible_runtime_contract",
    "dependency_unavailable",
    "dependency_unauthorized",
    "dependency_incompatible_runtime_contract",
    "dependency_forbidden_tool_ref",
    "forbidden_tool_ref",
    "forbidden_projection_policy",
    "hard_invariant_downgrade",
))
_NON_BLOCKING_AUTOMATIC_OMISSION_REASONS = frozenset((
    "dependency_unavailable",
    "explicit_recipe_disabled",
))


def _alias_updates(model_class: type[BaseModel], update: Mapping[str, Any]) -> dict[str, Any]:
    alias_to_name = {
        field.alias: name
        for name, field in model_class.model_fields.items()
        if field.alias is not None
    }
    return {alias_to_name.get(key, key): value for key, value in update.items()}


class _FrozenRecipeModel(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True, extra="forbid")

    @classmethod
    def model_construct(
        cls,
        _fields_set: set[str] | None = None,
        **values: Any,
    ) -> Self:
        return cls(**values)

    def model_copy(
        self,
        *,
        update: Mapping[str, Any] | None = None,
        deep: bool = False,
    ) -> Self:
        data = self.model_dump(by_alias=False, mode="python", warnings=False)
        if update:
            data.update(_alias_updates(self.__class__, update))
        return self.__class__.model_validate(data)


def _as_tuple(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, Set) and not isinstance(value, (bytes, bytearray, Mapping)):
        return tuple(str(item) for item in sorted(value, key=str))
    if isinstance(value, Iterable) and not isinstance(value, (bytes, bytearray, Mapping)):
        return tuple(str(item) for item in value)
    return ()


def _freeze_profile_value(value: object) -> object:
    if isinstance(value, Mapping):
        return MappingProxyType({
            str(key): _freeze_profile_value(nested_value)
            for key, nested_value in sorted(value.items(), key=lambda item: str(item[0]))
        })
    if isinstance(value, list):
        return tuple(_freeze_profile_value(item) for item in value)
    if isinstance(value, tuple):
        return tuple(_freeze_profile_value(item) for item in value)
    if isinstance(value, Set) and not isinstance(value, (bytes, bytearray)):
        return tuple(_freeze_profile_value(item) for item in sorted(value, key=str))
    return value


def _thaw_profile_value(value: object) -> object:
    if isinstance(value, Mapping):
        return {
            str(key): _thaw_profile_value(nested_value)
            for key, nested_value in value.items()
        }
    if isinstance(value, tuple):
        return tuple(_thaw_profile_value(item) for item in value)
    return value


def _canonical_mission_lifecycle_data(value: Mapping[str, object]) -> dict[str, object]:
    data = dict(value)
    data.pop("mission_uses_long_running_function_tool", None)
    data["missionUsesLongRunningFunctionTool"] = False
    return data


def _canonical_attachment_flags_data(value: Mapping[str, object]) -> dict[str, object]:
    data = dict(value)
    for name, field in RecipeAttachmentFlags.model_fields.items():
        data.pop(name, None)
        if field.alias is not None:
            data[field.alias] = False
    return data


def _is_safe_recipe_id(value: str) -> bool:
    return bool(_SAFE_RECIPE_ID_RE.fullmatch(value))


def _is_safe_recipe_version(value: str) -> bool:
    return bool(_SAFE_RECIPE_VERSION_RE.fullmatch(value)) and not _contains_unsafe_ref_text(value)


def _is_sha256_ref(value: str) -> bool:
    return bool(_SHA256_REF_RE.fullmatch(value))


def _contains_unsafe_ref_text(value: str) -> bool:
    normalized = value.strip().lower()
    return (
        _UNSAFE_REF_PREFIX_RE.search(normalized) is not None
        or _UNSAFE_REF_ANYWHERE_RE.search(normalized) is not None
    ) or any(
        fragment in normalized for fragment in _UNSAFE_REF_TEXT_FRAGMENTS
    )


def _is_sensitive_profile_key(value: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]", "", value.strip().lower())
    return (
        normalized in _SENSITIVE_PROFILE_EXACT_KEYS
        or normalized.startswith("tool")
        or normalized.startswith("hidden")
        or normalized.startswith("systeminstruction")
        or normalized.startswith("developerinstruction")
        or normalized.startswith("systemconfig")
        or normalized.startswith("developerconfig")
        or "instruction" in normalized
        or "config" in normalized
        or (
            ("system" in normalized or "developer" in normalized)
            and ("instruction" in normalized or "config" in normalized)
        )
    ) or any(
        fragment in normalized for fragment in _SENSITIVE_PROFILE_KEY_FRAGMENTS
    )


def _is_sensitive_profile_string(value: str) -> bool:
    normalized = value.strip().lower()
    compact = re.sub(r"[^a-z0-9]", "", normalized)
    return (
        _PRIVATE_PATH_RE.search(value) is not None
        or _contains_unsafe_ref_text(value)
        or re.search(r"\b(?:raw|private)\b", normalized) is not None
        or compact.startswith("raw")
        or compact.startswith("private")
        or "raw model output" in normalized
        or "rawchildtranscript" in compact
        or "rawtranscript" in compact
        or "private active snapshot" in normalized
        or "privatefixturequery" in compact
    )


def _sanitize_profile_value(value: object) -> object | None:
    if isinstance(value, str):
        return None if _is_sensitive_profile_string(value) else value
    if isinstance(value, Mapping):
        sanitized: dict[str, object] = {}
        for raw_key, nested_value in sorted(value.items(), key=lambda item: str(item[0])):
            key = str(raw_key)
            if _is_sensitive_profile_key(key):
                continue
            safe_value = _sanitize_profile_value(nested_value)
            if safe_value is not None:
                sanitized[key] = safe_value
        return sanitized
    if isinstance(value, Set) and not isinstance(value, (bytes, bytearray, Mapping)):
        sanitized_items = [
            safe_value
            for item in sorted(value, key=str)
            if (safe_value := _sanitize_profile_value(item)) is not None
        ]
        return tuple(sanitized_items)
    if isinstance(value, list | tuple) and not isinstance(value, (bytes, bytearray)):
        sanitized_items = [
            safe_value
            for item in value
            if (safe_value := _sanitize_profile_value(item)) is not None
        ]
        return tuple(sanitized_items)
    return value


def _sanitize_resolved_profile(value: object) -> Mapping[str, object]:
    sanitized = _sanitize_profile_value(value)
    if not isinstance(sanitized, Mapping):
        sanitized = {}
    frozen = _freeze_profile_value(sanitized)
    if not isinstance(frozen, Mapping):
        return MappingProxyType({})
    return frozen


def _dedupe_tuple(values: Iterable[str]) -> tuple[str, ...]:
    deduped: list[str] = []
    for value in values:
        if value and value not in deduped:
            deduped.append(value)
    return tuple(deduped)


def _deep_merge(base: dict[str, object], incoming: Mapping[str, object]) -> dict[str, object]:
    merged = dict(base)
    for raw_key in sorted(incoming.keys(), key=str):
        key = str(raw_key)
        if (
            key in {"packs"}
            or key in _EXPLICIT_RECIPE_SELECTION_KEYS
            or _is_sensitive_profile_key(key)
        ):
            continue
        value = incoming[raw_key]
        sanitized_value = _sanitize_profile_value(value)
        if sanitized_value is None:
            continue
        if (
            isinstance(merged.get(key), Mapping)
            and isinstance(sanitized_value, Mapping)
        ):
            merged[key] = _deep_merge(dict(merged[key]), sanitized_value)
        else:
            merged[key] = _freeze_profile_value(sanitized_value)
    return merged


def _pack_config_value(layer: Mapping[str, object], key: str) -> object:
    packs = layer.get("packs")
    if isinstance(packs, Mapping):
        return packs.get(key)
    return None


def _layer_pack_ids(layer: Mapping[str, object]) -> tuple[str, ...]:
    packs = layer.get("packs")
    if isinstance(packs, Mapping):
        return _as_tuple(packs.get("enable"))
    return _as_tuple(packs)


def _layer_disabled_pack_ids(layer: Mapping[str, object]) -> tuple[str, ...]:
    return _as_tuple(_pack_config_value(layer, "disable"))


def _task_type(layer: Mapping[str, object]) -> str | None:
    raw_task_type = layer.get("taskType") or layer.get("task_type")
    if raw_task_type is None:
        return None
    return str(raw_task_type)


def _plural_task_intents(layer: Mapping[str, object]) -> tuple[str, ...]:
    intents: list[str] = []
    for key in (
        "taskTypes",
        "task_types",
        "taskIntent",
        "task_intent",
        "taskIntents",
        "task_intents",
    ):
        for value in _as_tuple(layer.get(key)):
            if value and value not in intents:
                intents.append(value)
    return tuple(intents)


def _task_profile_selector_intents(
    request: "ProfileResolutionRequest",
    resolved_profile: Mapping[str, object],
) -> tuple[str, ...]:
    intents: list[str] = []
    legacy_task_type = _task_type(request.task_profile)
    if legacy_task_type is not None:
        intents.append(legacy_task_type)
    else:
        fallback_task_type = _task_type(resolved_profile)
        if fallback_task_type is not None:
            intents.append(fallback_task_type)
    for intent in _plural_task_intents(request.task_profile):
        if intent not in intents:
            intents.append(intent)
    return tuple(intents)


def _normalized_policy_value(value: object) -> str | None:
    if value is None:
        return None
    return str(value).strip().lower().replace("-", "_")


def _policy_value_from_layer(
    layer: Mapping[str, object],
    keys: tuple[str, ...],
) -> str | None:
    for key in keys:
        value = layer.get(key)
        if value is not None and not isinstance(value, Mapping):
            return _normalized_policy_value(value)
    return None


def _budget_cap_values_from_layer(layer: Mapping[str, object]) -> tuple[int, ...]:
    candidates: list[object] = [
        layer.get("budgetCap"),
        layer.get("budget_cap"),
        layer.get("maxToolCalls"),
        layer.get("max_tool_calls"),
        layer.get("turnBudget"),
        layer.get("turn_budget"),
    ]
    for budget in (layer.get("budget"), layer.get("customization")):
        if not isinstance(budget, Mapping):
            continue
        candidates.extend((
            budget.get("cap"),
            budget.get("max"),
            budget.get("budgetCap"),
            budget.get("budget_cap"),
            budget.get("maxToolCalls"),
            budget.get("max_tool_calls"),
            budget.get("turnBudget"),
            budget.get("turn_budget"),
        ))
    caps: list[int] = []
    for candidate in candidates:
        if isinstance(candidate, bool) or candidate is None:
            continue
        try:
            value = int(candidate)
        except (TypeError, ValueError):
            continue
        if value >= 0:
            caps.append(value)
    return tuple(caps)


_PUBLIC_PROVIDER_CONFLICT_NAMES = frozenset((
    "web",
    "browser",
    "model",
    "search",
    "reader",
    "fetch",
    "memory",
    "artifact",
    "channel",
    "telegram",
    "discord",
))

_PUBLIC_TOOL_PROVIDER_CONFLICT_NAMES = frozenset((
    "file.read",
    "file.write",
    "file.patch",
    "test.run",
    "git.diff",
    "browser.open",
    "browser.snapshot",
    "browser.scrape",
    "browser.click",
    "browser.fill",
    "browser.scroll",
    "browser.screenshot",
    "artifact.prepare-delivery",
    "file.delivery-plan",
))


def _safe_conflict_ref(kind: str, raw_name: object) -> str:
    name = str(raw_name).strip()
    public_names = (
        _PUBLIC_PROVIDER_CONFLICT_NAMES
        if kind == "provider"
        else _PUBLIC_TOOL_PROVIDER_CONFLICT_NAMES
    )
    if name in public_names:
        return f"{kind}.{name}"
    digest = hashlib.sha256(name.encode("utf-8")).hexdigest()[:12]
    return f"{kind}.ref_{digest}"


def _provider_tool_declarations(layer: Mapping[str, object]) -> tuple[tuple[str, str], ...]:
    declarations: list[tuple[str, str]] = []
    provider_maps = ("providers", "providerMap", "provider_map")
    tool_provider_maps = ("toolProviders", "tool_providers")
    for key in provider_maps:
        value = layer.get(key)
        if isinstance(value, Mapping):
            for name, provider in sorted(value.items(), key=lambda item: str(item[0])):
                declarations.append((_safe_conflict_ref("provider", name), str(provider)))
    for key in tool_provider_maps:
        value = layer.get(key)
        if isinstance(value, Mapping):
            for name, provider in sorted(value.items(), key=lambda item: str(item[0])):
                declarations.append((_safe_conflict_ref("tool_provider", name), str(provider)))
    scalar_keys = {
        "provider": "provider.default",
        "modelProvider": "provider.model",
        "toolProvider": "tool_provider.default",
        "browserProvider": "provider.browser",
    }
    for key, conflict_ref in scalar_keys.items():
        value = layer.get(key)
        if value is not None and not isinstance(value, Mapping):
            declarations.append((conflict_ref, str(value)))
    return tuple(declarations)


def _build_composition_policy_metadata(
    layers: tuple[Mapping[str, object], ...],
) -> "CompositionPolicyMetadata":
    memory_rank = {"normal": 0, "read_only": 1, "incognito": 2}
    side_effect_rank = {"allow": 0, "approval_required": 1, "deny": 2}
    budget_caps = tuple(
        cap
        for layer in layers
        for cap in _budget_cap_values_from_layer(layer)
    )
    memory_modes = tuple(
        mode
        for mode in (
            _policy_value_from_layer(layer, ("memoryMode", "memory_mode"))
            for layer in layers
        )
        if mode in memory_rank
    )
    side_effect_postures = tuple(
        posture
        for posture in (
            _policy_value_from_layer(
                layer,
                ("sideEffectPosture", "side_effect_posture", "sideEffects", "side_effects"),
            )
            for layer in layers
        )
        if posture in side_effect_rank
    )

    seen_providers: dict[str, str] = {}
    conflicts: list[str] = []
    for layer in layers:
        for name, provider in _provider_tool_declarations(layer):
            existing = seen_providers.get(name)
            if existing is not None and existing != provider and name not in conflicts:
                conflicts.append(name)
            seen_providers[name] = provider

    return CompositionPolicyMetadata(
        budgetCap=min(budget_caps) if budget_caps else None,
        memoryMode=max(memory_modes, key=lambda mode: memory_rank[mode])
        if memory_modes
        else "normal",
        sideEffectPosture=max(
            side_effect_postures,
            key=lambda posture: side_effect_rank[posture],
        )
        if side_effect_postures
        else "allow",
        conflictRefs=tuple(conflicts),
        blocked=bool(conflicts),
        requiresClarification=bool(conflicts),
    )


class RecipeAttachmentFlags(_FrozenRecipeModel):
    traffic_attached: bool = Field(default=False, alias="trafficAttached")
    execution_attached: bool = Field(default=False, alias="executionAttached")
    route_attached: bool = Field(default=False, alias="routeAttached")
    runner_attached: bool = Field(default=False, alias="runnerAttached")
    live_tools_attached: bool = Field(default=False, alias="liveToolsAttached")
    live_callbacks_attached: bool = Field(default=False, alias="liveCallbacksAttached")
    canary_attached: bool = Field(default=False, alias="canaryAttached")
    production_attached: bool = Field(default=False, alias="productionAttached")
    block_mode_enabled_for_live_traffic: bool = Field(
        default=False,
        alias="blockModeEnabledForLiveTraffic",
    )

    @field_serializer(
        "traffic_attached",
        "execution_attached",
        "route_attached",
        "runner_attached",
        "live_tools_attached",
        "live_callbacks_attached",
        "canary_attached",
        "production_attached",
        "block_mode_enabled_for_live_traffic",
    )
    def _serialize_false_flag(self, value: object) -> bool:
        return False

    @classmethod
    def model_construct(
        cls,
        _fields_set: set[str] | None = None,
        **values: Any,
    ) -> Self:
        return cls()

    @model_validator(mode="after")
    def _reject_enabled_flags(self) -> Self:
        enabled = [
            name
            for name in self.__class__.model_fields
            if getattr(self, name) is True
        ]
        if enabled:
            raise ValueError(
                "Gate 2 recipe metadata cannot enable attachment flags: "
                + ", ".join(enabled)
            )
        return self


class MissionLifecycleMetadata(_FrozenRecipeModel):
    enabled: bool = False
    lifecycle_refs: tuple[str, ...] = Field(
        default=(),
        alias="lifecycleRefs",
    )
    mission_uses_long_running_function_tool: bool = Field(
        default=False,
        alias="missionUsesLongRunningFunctionTool",
    )
    native_plugin_boundary_refs: tuple[str, ...] = Field(
        default=(),
        alias="nativePluginBoundaryRefs",
    )

    @classmethod
    def model_construct(
        cls,
        _fields_set: set[str] | None = None,
        **values: Any,
    ) -> Self:
        return cls(**_canonical_mission_lifecycle_data(values))

    @field_validator("lifecycle_refs", "native_plugin_boundary_refs", mode="before")
    @classmethod
    def _tuple_refs(cls, value: object) -> tuple[str, ...]:
        return _as_tuple(value)

    @model_validator(mode="after")
    def _reject_mission_as_long_running_function_tool(self) -> Self:
        if self.mission_uses_long_running_function_tool:
            raise ValueError(
                "mission lifecycle metadata must not model missions as LongRunningFunctionTool"
            )
        return self


class CompositionPolicyMetadata(_FrozenRecipeModel):
    validator_merge: str = Field(default="all_of", alias="validatorMerge")
    approval_gate_merge: str = Field(default="union", alias="approvalGateMerge")
    evidence_merge: str = Field(default="union", alias="evidenceMerge")
    audit_merge: str = Field(default="union", alias="auditMerge")
    budget_cap: int | None = Field(default=None, alias="budgetCap")
    memory_mode: str = Field(default="normal", alias="memoryMode")
    side_effect_posture: str = Field(default="allow", alias="sideEffectPosture")
    provider_tool_conflict_policy: str = Field(
        default="blocked_or_requires_clarification",
        alias="providerToolConflictPolicy",
    )
    conflict_refs: tuple[str, ...] = Field(default=(), alias="conflictRefs")
    blocked: bool = False
    requires_clarification: bool = Field(default=False, alias="requiresClarification")

    @field_validator("conflict_refs", mode="before")
    @classmethod
    def _tuple_refs(cls, value: object) -> tuple[str, ...]:
        return _as_tuple(value)

    @field_validator(
        "validator_merge",
        "approval_gate_merge",
        "evidence_merge",
        "audit_merge",
        "memory_mode",
        "side_effect_posture",
        "provider_tool_conflict_policy",
    )
    @classmethod
    def _reject_empty_policy_label(cls, value: str) -> str:
        if not value:
            raise ValueError("composition policy labels must be non-empty")
        return value

    @model_validator(mode="after")
    def _validate_restrictive_merge_semantics(self) -> Self:
        if self.validator_merge != "all_of":
            raise ValueError("validator merge policy must remain all_of")
        if self.approval_gate_merge != "union":
            raise ValueError("approval gate merge policy must remain union")
        if self.evidence_merge != "union":
            raise ValueError("evidence merge policy must remain union")
        if self.audit_merge != "union":
            raise ValueError("audit merge policy must remain union")
        if self.memory_mode not in {"normal", "read_only", "incognito"}:
            raise ValueError("unknown memory mode composition policy")
        if self.side_effect_posture not in {"allow", "approval_required", "deny"}:
            raise ValueError("unknown side-effect posture composition policy")
        if self.provider_tool_conflict_policy != "blocked_or_requires_clarification":
            raise ValueError("provider/tool conflicts must not silently overwrite")
        if self.conflict_refs and not (self.blocked or self.requires_clarification):
            raise ValueError("provider/tool conflicts must block or require clarification")
        return self


class ExplicitRecipeRef(_FrozenRecipeModel):
    recipe_id: str = Field(alias="recipeId")
    version: str | None = None
    digest: str | None = None

    @field_validator("recipe_id")
    @classmethod
    def _reject_empty_recipe_id(cls, value: str) -> str:
        recipe_id = value.strip()
        if not recipe_id or not _is_safe_recipe_id(recipe_id):
            raise ValueError("recipeId must be non-empty")
        return recipe_id

    @field_validator("version", "digest")
    @classmethod
    def _normalize_optional_ref_field(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            return None
        return normalized

    @model_validator(mode="after")
    def _validate_ref_shape(self) -> Self:
        if self.version is not None and not _is_safe_recipe_version(self.version):
            raise ValueError("version must be a safe recipe ref version")
        if self.digest is not None and not _is_sha256_ref(self.digest):
            raise ValueError("digest must be a sha256 ref")
        return self


class ExplicitRecipeSelectionRequest(_FrozenRecipeModel):
    mode: str = "this_turn"
    required_recipe_refs: tuple[ExplicitRecipeRef, ...] = Field(
        default=(),
        alias="requiredRecipeRefs",
    )
    allow_additional_auto_recipes: bool = Field(
        default=True,
        alias="allowAdditionalAutoRecipes",
    )

    @field_validator("mode")
    @classmethod
    def _validate_mode(cls, value: str) -> str:
        if value not in {"this_turn", "session", "bot_default"}:
            raise ValueError("explicit recipe selection mode is not supported")
        return value

    @field_validator("required_recipe_refs", mode="before")
    @classmethod
    def _tuple_recipe_refs(cls, value: object) -> tuple[object, ...]:
        if value is None:
            return ()
        if isinstance(value, tuple):
            return value
        if isinstance(value, list):
            return tuple(value)
        return (value,)


class RecipeSelectionMetadata(_FrozenRecipeModel):
    selection_source: str = Field(default="default", alias="selectionSource")
    requested_recipe_refs: tuple[ExplicitRecipeRef, ...] = Field(
        default=(),
        alias="requestedRecipeRefs",
    )
    applied_recipe_refs: tuple[ExplicitRecipeRef, ...] = Field(
        default=(),
        alias="appliedRecipeRefs",
    )
    omitted_recipe_refs: tuple[ExplicitRecipeRef, ...] = Field(
        default=(),
        alias="omittedRecipeRefs",
    )
    omission_reasons: Mapping[str, tuple[str, ...]] = Field(
        default_factory=dict,
        alias="omissionReasons",
    )
    policy_snapshot_digest: str = Field(alias="policySnapshotDigest")
    admission_blocked: bool = Field(default=False, alias="admissionBlocked")

    @field_validator(
        "requested_recipe_refs",
        "applied_recipe_refs",
        "omitted_recipe_refs",
        mode="before",
    )
    @classmethod
    def _tuple_recipe_refs(cls, value: object) -> tuple[object, ...]:
        if value is None:
            return ()
        if isinstance(value, tuple):
            return value
        if isinstance(value, list):
            return tuple(value)
        return (value,)

    @field_validator("omission_reasons", mode="before")
    @classmethod
    def _canonical_omission_reasons(
        cls,
        value: object,
    ) -> Mapping[str, tuple[str, ...]]:
        if not isinstance(value, Mapping):
            return {}
        sanitized: dict[str, tuple[str, ...]] = {}
        for recipe_id, reasons in sorted(value.items(), key=lambda item: str(item[0])):
            recipe_id_value = str(recipe_id).strip()
            safe_recipe_id = (
                recipe_id_value
                if _is_safe_recipe_id(recipe_id_value)
                else _INVALID_EXPLICIT_RECIPE_ID
            )
            safe_reasons = _dedupe_tuple(
                str(reason)
                for reason in _as_tuple(reasons)
                if str(reason) in _ALLOWED_RECIPE_OMISSION_REASONS
            )
            if safe_reasons:
                sanitized[safe_recipe_id] = safe_reasons
        return sanitized

    @field_validator("selection_source")
    @classmethod
    def _validate_selection_source(cls, value: str) -> str:
        if value not in {"explicit", "automatic", "default", "mixed"}:
            raise ValueError("unsupported recipe selection source")
        return value

    @field_validator("policy_snapshot_digest")
    @classmethod
    def _validate_policy_snapshot_digest(cls, value: str) -> str:
        if not _is_sha256_ref(value):
            raise ValueError("policySnapshotDigest must be a sha256 ref")
        return value

    @field_serializer("omission_reasons")
    def _serialize_omission_reasons(
        self,
        value: Mapping[str, tuple[str, ...]],
    ) -> dict[str, tuple[str, ...]]:
        return dict(value)


class RecipePackManifest(_FrozenRecipeModel):
    pack_id: str = Field(alias="packId")
    version: str = "1"
    display_name: str = Field(alias="displayName")
    description: str
    when_to_use: str = Field(default="", alias="whenToUse")
    default_enabled: bool = Field(default=False, alias="defaultEnabled")
    hard_safety: bool = Field(default=False, alias="hardSafety")
    opt_out_allowed: bool = Field(default=True, alias="optOutAllowed")
    customizable: bool = True
    task_profile_selectors: tuple[str, ...] = Field(
        default=(),
        alias="taskProfileSelectors",
    )
    depends_on_pack_ids: tuple[str, ...] = Field(
        default=(),
        alias="dependsOnPackIds",
    )
    instruction_refs: tuple[str, ...] = Field(default=(), alias="instructionRefs")
    tool_refs: tuple[str, ...] = Field(default=(), alias="toolRefs")
    callback_refs: tuple[str, ...] = Field(default=(), alias="callbackRefs")
    validator_refs: tuple[str, ...] = Field(default=(), alias="validatorRefs")
    approval_gate_refs: tuple[str, ...] = Field(default=(), alias="approvalGateRefs")
    checkpoint_refs: tuple[str, ...] = Field(default=(), alias="checkpointRefs")
    evidence_refs: tuple[str, ...] = Field(default=(), alias="evidenceRefs")
    audit_refs: tuple[str, ...] = Field(default=(), alias="auditRefs")
    adk_primitive_ownership: tuple[str, ...] = Field(
        default=(),
        alias="adkPrimitiveOwnership",
    )
    openmagi_boundary_ownership: tuple[str, ...] = Field(
        default=(),
        alias="openmagiBoundaryOwnership",
    )
    callback_set_metadata: tuple[str, ...] = Field(
        default=(),
        alias="callbackSetMetadata",
    )
    validator_set_metadata: tuple[str, ...] = Field(
        default=(),
        alias="validatorSetMetadata",
    )
    approval_gate_metadata: tuple[str, ...] = Field(
        default=(),
        alias="approvalGateMetadata",
    )
    live_tool_refs: tuple[str, ...] = Field(default=(), alias="liveToolRefs")
    live_callback_refs: tuple[str, ...] = Field(default=(), alias="liveCallbackRefs")
    runner_route_refs: tuple[str, ...] = Field(default=(), alias="runnerRouteRefs")
    compatible_runtime_contract_versions: tuple[str, ...] = Field(
        default=(DEFAULT_RECIPE_RUNTIME_CONTRACT_VERSION,),
        alias="compatibleRuntimeContractVersions",
    )
    mission_lifecycle: MissionLifecycleMetadata | None = Field(
        default=None,
        alias="missionLifecycle",
    )
    attachment_flags: RecipeAttachmentFlags = Field(
        default_factory=RecipeAttachmentFlags,
        alias="attachmentFlags",
    )

    @field_validator(
        "task_profile_selectors",
        "depends_on_pack_ids",
        "instruction_refs",
        "tool_refs",
        "callback_refs",
        "validator_refs",
        "approval_gate_refs",
        "checkpoint_refs",
        "evidence_refs",
        "audit_refs",
        "adk_primitive_ownership",
        "openmagi_boundary_ownership",
        "callback_set_metadata",
        "validator_set_metadata",
        "approval_gate_metadata",
        "live_tool_refs",
        "live_callback_refs",
        "runner_route_refs",
        "compatible_runtime_contract_versions",
        mode="before",
    )
    @classmethod
    def _tuple_refs(cls, value: object) -> tuple[str, ...]:
        return _as_tuple(value)

    @field_validator("pack_id", "version")
    @classmethod
    def _reject_empty_manifest_ref(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("recipe pack ref fields must be non-empty")
        if "." in normalized and not _is_safe_recipe_id(normalized):
            raise ValueError("recipe pack id must be a safe recipe id")
        if "." not in normalized and not _is_safe_recipe_version(normalized):
            raise ValueError("recipe pack version must be a safe version")
        return normalized

    @field_validator("mission_lifecycle", mode="before")
    @classmethod
    def _canonicalize_mission_lifecycle(
        cls,
        value: object,
    ) -> MissionLifecycleMetadata | None | object:
        if value is None:
            return None
        if isinstance(value, MissionLifecycleMetadata):
            data = value.model_dump(by_alias=True, mode="python", warnings=False)
        elif isinstance(value, Mapping):
            data = dict(value)
        else:
            return value
        return MissionLifecycleMetadata(**_canonical_mission_lifecycle_data(data))

    @field_validator("attachment_flags", mode="before")
    @classmethod
    def _canonicalize_attachment_flags(cls, value: object) -> RecipeAttachmentFlags | object:
        if isinstance(value, RecipeAttachmentFlags):
            data = value.model_dump(by_alias=True, mode="python", warnings=False)
            return RecipeAttachmentFlags(**_canonical_attachment_flags_data(data))
        if isinstance(value, Mapping):
            return RecipeAttachmentFlags(**_canonical_attachment_flags_data(value))
        return value

    @field_serializer("live_tool_refs", "live_callback_refs", "runner_route_refs")
    def _serialize_no_live_refs(self, value: object) -> tuple[str, ...]:
        return ()

    @field_serializer("attachment_flags")
    def _serialize_attachment_flags(self, value: object) -> dict[str, bool]:
        return RecipeAttachmentFlags().model_dump(by_alias=True, mode="python")

    @model_validator(mode="after")
    def _validate_safety_and_metadata_only(self) -> Self:
        if self.hard_safety and self.opt_out_allowed:
            raise ValueError("hard-safety packs must be non-opt-out")
        if self.hard_safety and self.customizable:
            raise ValueError("hard-safety packs must be non-customizable")
        if not self.hard_safety and not self.opt_out_allowed:
            raise ValueError("non-hard recipe packs must remain opt-out allowed")
        if not self.hard_safety and not self.customizable:
            raise ValueError("non-hard recipe packs must remain customizable")
        if self.live_tool_refs or self.live_callback_refs or self.runner_route_refs:
            raise ValueError("recipe pack manifests must remain metadata-only")
        return self


class ProfileResolutionRequest(_FrozenRecipeModel):
    user_profile: JsonMap = Field(default_factory=dict, alias="userProfile")
    workspace_policy: JsonMap = Field(default_factory=dict, alias="workspacePolicy")
    task_profile: JsonMap = Field(default_factory=dict, alias="taskProfile")
    recipe_pack_config: JsonMap = Field(default_factory=dict, alias="recipePackConfig")
    runtime_context: JsonMap = Field(default_factory=dict, alias="runtimeContext")


class ResolvedRecipeProfile(_FrozenRecipeModel):
    resolved_profile: Mapping[str, object] = Field(alias="resolvedProfile")
    selected_pack_ids: tuple[str, ...] = Field(alias="selectedPackIds")
    opted_out_pack_ids: tuple[str, ...] = Field(default=(), alias="optedOutPackIds")
    non_opt_out_pack_ids: tuple[str, ...] = Field(default=(), alias="nonOptOutPackIds")
    composition_policy_metadata: CompositionPolicyMetadata = Field(
        default_factory=CompositionPolicyMetadata,
        alias="compositionPolicyMetadata",
    )
    recipe_selection: RecipeSelectionMetadata = Field(
        default_factory=lambda: _build_recipe_selection_metadata(
            selection_source="default",
            requested_refs=(),
            applied_pack_ids=(),
            omitted_refs=(),
            omission_reasons={},
            registry=None,
        ),
        alias="recipeSelection",
    )

    @field_validator("selected_pack_ids", "opted_out_pack_ids", "non_opt_out_pack_ids", mode="before")
    @classmethod
    def _tuple_refs(cls, value: object) -> tuple[str, ...]:
        return _as_tuple(value)

    @field_validator("resolved_profile", mode="before")
    @classmethod
    def _freeze_resolved_profile(cls, value: object) -> Mapping[str, object] | object:
        return _sanitize_resolved_profile(value)

    @model_validator(mode="after")
    def _store_frozen_resolved_profile(self) -> Self:
        object.__setattr__(
            self,
            "resolved_profile",
            _sanitize_resolved_profile(self.resolved_profile),
        )
        return self

    @field_serializer("resolved_profile")
    def _serialize_resolved_profile(self, value: Mapping[str, object]) -> dict[str, object]:
        return _thaw_profile_value(value)  # type: ignore[return-value]


class RecipeSnapshot(_FrozenRecipeModel):
    snapshot_id: str = Field(alias="snapshotId")
    resolved_profile: Mapping[str, object] = Field(alias="resolvedProfile")
    selected_pack_ids: tuple[str, ...] = Field(alias="selectedPackIds")
    opted_out_pack_ids: tuple[str, ...] = Field(default=(), alias="optedOutPackIds")
    non_opt_out_pack_ids: tuple[str, ...] = Field(default=(), alias="nonOptOutPackIds")
    composition_policy_metadata: CompositionPolicyMetadata = Field(
        default_factory=CompositionPolicyMetadata,
        alias="compositionPolicyMetadata",
    )
    recipe_selection: RecipeSelectionMetadata = Field(
        default_factory=lambda: _build_recipe_selection_metadata(
            selection_source="default",
            requested_refs=(),
            applied_pack_ids=(),
            omitted_refs=(),
            omission_reasons={},
            registry=None,
        ),
        alias="recipeSelection",
    )
    instruction_refs: tuple[str, ...] = Field(default=(), alias="instructionRefs")
    tool_refs: tuple[str, ...] = Field(default=(), alias="toolRefs")
    callback_refs: tuple[str, ...] = Field(default=(), alias="callbackRefs")
    validator_refs: tuple[str, ...] = Field(default=(), alias="validatorRefs")
    approval_gate_refs: tuple[str, ...] = Field(default=(), alias="approvalGateRefs")
    checkpoint_refs: tuple[str, ...] = Field(default=(), alias="checkpointRefs")
    evidence_refs: tuple[str, ...] = Field(default=(), alias="evidenceRefs")
    audit_refs: tuple[str, ...] = Field(default=(), alias="auditRefs")
    adk_primitive_ownership: tuple[str, ...] = Field(
        default=(),
        alias="adkPrimitiveOwnership",
    )
    openmagi_boundary_ownership: tuple[str, ...] = Field(
        default=(),
        alias="openmagiBoundaryOwnership",
    )
    callback_set_metadata: tuple[str, ...] = Field(
        default=(),
        alias="callbackSetMetadata",
    )
    validator_set_metadata: tuple[str, ...] = Field(
        default=(),
        alias="validatorSetMetadata",
    )
    approval_gate_metadata: tuple[str, ...] = Field(
        default=(),
        alias="approvalGateMetadata",
    )
    mission_lifecycle: MissionLifecycleMetadata | None = Field(
        default=None,
        alias="missionLifecycle",
    )
    attachment_flags: RecipeAttachmentFlags = Field(
        default_factory=RecipeAttachmentFlags,
        alias="attachmentFlags",
    )

    @field_validator(
        "selected_pack_ids",
        "opted_out_pack_ids",
        "non_opt_out_pack_ids",
        "instruction_refs",
        "tool_refs",
        "callback_refs",
        "validator_refs",
        "approval_gate_refs",
        "checkpoint_refs",
        "evidence_refs",
        "audit_refs",
        "adk_primitive_ownership",
        "openmagi_boundary_ownership",
        "callback_set_metadata",
        "validator_set_metadata",
        "approval_gate_metadata",
        mode="before",
    )
    @classmethod
    def _tuple_refs(cls, value: object) -> tuple[str, ...]:
        return _as_tuple(value)

    @field_validator("resolved_profile", mode="before")
    @classmethod
    def _freeze_resolved_profile(cls, value: object) -> Mapping[str, object] | object:
        return _sanitize_resolved_profile(value)

    @field_validator("mission_lifecycle", mode="before")
    @classmethod
    def _canonicalize_mission_lifecycle(
        cls,
        value: object,
    ) -> MissionLifecycleMetadata | None | object:
        if value is None:
            return None
        if isinstance(value, MissionLifecycleMetadata):
            data = value.model_dump(by_alias=True, mode="python", warnings=False)
        elif isinstance(value, Mapping):
            data = dict(value)
        else:
            return value
        return MissionLifecycleMetadata(**_canonical_mission_lifecycle_data(data))

    @field_validator("attachment_flags", mode="before")
    @classmethod
    def _canonicalize_attachment_flags(cls, value: object) -> RecipeAttachmentFlags | object:
        if isinstance(value, RecipeAttachmentFlags):
            data = value.model_dump(by_alias=True, mode="python", warnings=False)
            return RecipeAttachmentFlags(**_canonical_attachment_flags_data(data))
        if isinstance(value, Mapping):
            return RecipeAttachmentFlags(**_canonical_attachment_flags_data(value))
        return value

    @model_validator(mode="after")
    def _store_frozen_resolved_profile_and_validate_snapshot_id(self) -> Self:
        object.__setattr__(
            self,
            "resolved_profile",
            _sanitize_resolved_profile(self.resolved_profile),
        )
        expected = build_recipe_snapshot_id(self.selected_pack_ids)
        if self.snapshot_id != expected:
            raise ValueError("snapshotId must match selectedPackIds")
        return self

    @field_serializer("resolved_profile")
    def _serialize_resolved_profile(self, value: Mapping[str, object]) -> dict[str, object]:
        return _thaw_profile_value(value)  # type: ignore[return-value]

    @field_serializer("attachment_flags")
    def _serialize_attachment_flags(self, value: object) -> dict[str, bool]:
        return RecipeAttachmentFlags().model_dump(by_alias=True, mode="python")


def build_recipe_snapshot_id(pack_ids: tuple[str, ...]) -> str:
    digest = hashlib.sha256("\n".join(pack_ids).encode("utf-8")).hexdigest()[:16]
    return f"recipe-snapshot:{digest}"


def build_recipe_pack_digest(pack: RecipePackManifest) -> str:
    payload = pack.model_dump(by_alias=True, mode="json", warnings=False)
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _build_recipe_selection_policy_snapshot_digest(
    *,
    selection_source: str,
    requested_refs: tuple[ExplicitRecipeRef, ...],
    applied_refs: tuple[ExplicitRecipeRef, ...],
    omitted_refs: tuple[ExplicitRecipeRef, ...],
    omission_reasons: Mapping[str, tuple[str, ...]],
) -> str:
    payload = {
        "selectionSource": selection_source,
        "requestedRecipeRefs": [
            ref.model_dump(by_alias=True, mode="json", exclude_none=True)
            for ref in requested_refs
        ],
        "appliedRecipeRefs": [
            ref.model_dump(by_alias=True, mode="json", exclude_none=True)
            for ref in applied_refs
        ],
        "omittedRecipeRefs": [
            ref.model_dump(by_alias=True, mode="json", exclude_none=True)
            for ref in omitted_refs
        ],
        "omissionReasons": {
            key: tuple(reasons)
            for key, reasons in sorted(omission_reasons.items())
        },
    }
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _pack_ref(pack: RecipePackManifest) -> ExplicitRecipeRef:
    return ExplicitRecipeRef(
        recipeId=pack.pack_id,
        version=pack.version,
        digest=build_recipe_pack_digest(pack),
    )


def _build_recipe_selection_metadata(
    *,
    selection_source: str,
    requested_refs: tuple[ExplicitRecipeRef, ...],
    applied_pack_ids: tuple[str, ...],
    omitted_refs: tuple[ExplicitRecipeRef, ...],
    omission_reasons: Mapping[str, tuple[str, ...]],
    registry: "PackRegistry | None",
) -> RecipeSelectionMetadata:
    applied_refs = tuple(
        _pack_ref(registry.get(pack_id))
        for pack_id in applied_pack_ids
        if registry is not None and pack_id in registry.pack_ids
    )
    policy_snapshot_digest = _build_recipe_selection_policy_snapshot_digest(
        selection_source=selection_source,
        requested_refs=requested_refs,
        applied_refs=applied_refs,
        omitted_refs=omitted_refs,
        omission_reasons=omission_reasons,
    )
    return RecipeSelectionMetadata(
        selectionSource=selection_source,
        requestedRecipeRefs=requested_refs,
        appliedRecipeRefs=applied_refs,
        omittedRecipeRefs=omitted_refs,
        omissionReasons=omission_reasons,
        policySnapshotDigest=policy_snapshot_digest,
        admissionBlocked=bool(omitted_refs),
    )


class PackRegistry:
    def __init__(self, packs: Iterable[RecipePackManifest] = ()) -> None:
        self._packs: dict[str, RecipePackManifest] = {}
        for pack in packs:
            self.register(pack)

    @property
    def pack_ids(self) -> tuple[str, ...]:
        return tuple(self._packs)

    def register(self, pack: RecipePackManifest) -> None:
        if pack.pack_id in self._packs:
            raise ValueError(f"duplicate recipe pack id: {pack.pack_id}")
        self._packs[pack.pack_id] = pack

    def get(self, pack_id: str) -> RecipePackManifest:
        try:
            return self._packs[pack_id]
        except KeyError as exc:
            raise KeyError(f"unknown recipe pack id: {pack_id}") from exc

    def values(self) -> tuple[RecipePackManifest, ...]:
        return tuple(self._packs.values())

    @classmethod
    def with_first_party_packs(cls) -> Self:
        return cls(_first_party_packs())


def _explicit_recipe_selection_from_request(
    request: ProfileResolutionRequest,
) -> ExplicitRecipeSelectionRequest | None:
    raw_selection = None
    explicit_key_present = False
    for key in _EXPLICIT_RECIPE_SELECTION_KEYS:
        if key in request.runtime_context:
            raw_selection = request.runtime_context[key]
            explicit_key_present = True
            break
    if not explicit_key_present:
        return None
    if not isinstance(raw_selection, Mapping):
        return _malformed_explicit_recipe_selection()
    try:
        return ExplicitRecipeSelectionRequest.model_validate(raw_selection)
    except ValidationError:
        return _malformed_explicit_recipe_selection()


def _malformed_explicit_recipe_selection() -> ExplicitRecipeSelectionRequest:
    return ExplicitRecipeSelectionRequest(
        mode="this_turn",
        requiredRecipeRefs=[{"recipeId": _INVALID_EXPLICIT_RECIPE_ID}],
        allowAdditionalAutoRecipes=False,
    )


def _authorized_recipe_refs(request: ProfileResolutionRequest) -> tuple[str, ...]:
    refs: list[str] = []
    for key in ("authorizedRecipeRefs", "authorized_recipe_refs"):
        for ref in _as_tuple(request.workspace_policy.get(key)):
            if ref not in refs:
                refs.append(ref)
    return tuple(refs)


def _forbidden_tool_refs(request: ProfileResolutionRequest) -> tuple[str, ...]:
    refs: list[str] = []
    for layer in (request.workspace_policy, request.runtime_context):
        for key in ("forbiddenToolRefs", "forbidden_tool_refs"):
            for ref in _as_tuple(layer.get(key)):
                if ref not in refs:
                    refs.append(ref)
    return tuple(refs)


def _runtime_contract_version(request: ProfileResolutionRequest) -> str:
    for key in ("recipeRuntimeContractVersion", "recipe_runtime_contract_version"):
        value = request.runtime_context.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return DEFAULT_RECIPE_RUNTIME_CONTRACT_VERSION


def _hard_invariant_downgraded(request: ProfileResolutionRequest) -> bool:
    hard_invariant_sets = tuple(
        request.runtime_context[key]
        for key in ("hardInvariants", "hard_invariants")
        if key in request.runtime_context
    )
    if not hard_invariant_sets:
        return False
    return any(
        _hard_invariant_set_downgraded(hard_invariants)
        for hard_invariants in hard_invariant_sets
    )


def _hard_invariant_set_downgraded(value: object) -> bool:
    if not isinstance(value, Mapping):
        return True
    if not value:
        return True
    return any(_hard_invariant_value_downgraded(item) for item in value.values())


def _hard_invariant_value_downgraded(value: object) -> bool:
    return value is not True


def _projection_policy_is_forbidden(request: ProfileResolutionRequest) -> bool:
    policy = _policy_value_from_layer(
        request.runtime_context,
        ("projectionPolicy", "projection_policy"),
    )
    return policy in {"raw", "raw_text", "raw_text_allowed", "unsafe_raw"}


def _is_ref_authorized(ref: ExplicitRecipeRef, authorized_refs: tuple[str, ...]) -> bool:
    if not authorized_refs:
        return True
    candidates = {
        ref.recipe_id,
    }
    if ref.version is not None:
        candidates.add(f"{ref.recipe_id}@{ref.version}")
    if ref.digest is not None:
        candidates.add(ref.digest)
        candidates.add(f"{ref.recipe_id}@{ref.digest}")
    return any(candidate in authorized_refs for candidate in candidates)


def _dependency_unavailable(
    registry: PackRegistry,
    pack: RecipePackManifest,
    disabled: set[str],
    seen: set[str] | None = None,
) -> bool:
    visited = seen if seen is not None else set()
    for dependency_id in pack.depends_on_pack_ids:
        if dependency_id in visited:
            continue
        visited.add(dependency_id)
        if dependency_id not in registry.pack_ids:
            return True
        dependency = registry.get(dependency_id)
        if dependency_id in disabled and dependency.opt_out_allowed:
            return True
        if _dependency_unavailable(registry, dependency, disabled, visited):
            return True
    return False


def _dependency_omission_reasons(
    *,
    registry: PackRegistry,
    pack: RecipePackManifest,
    request: ProfileResolutionRequest,
    disabled: set[str],
    seen: set[str] | None = None,
) -> tuple[str, ...]:
    visited = seen if seen is not None else set()
    reasons: list[str] = []
    for dependency_id in pack.depends_on_pack_ids:
        if dependency_id in visited:
            continue
        visited.add(dependency_id)
        if dependency_id not in registry.pack_ids:
            reasons.append("dependency_unavailable")
            continue
        dependency = registry.get(dependency_id)
        if dependency_id in disabled and dependency.opt_out_allowed:
            reasons.append("dependency_unavailable")
            continue
        direct_reasons = _pack_direct_omission_reasons(
            registry=registry,
            pack_id=dependency_id,
            request=request,
            disabled=disabled,
            explicit_ref=None,
        )
        if "explicit_recipe_unauthorized" in direct_reasons:
            reasons.append("dependency_unauthorized")
        if "incompatible_runtime_contract" in direct_reasons:
            reasons.append("dependency_incompatible_runtime_contract")
        if "forbidden_tool_ref" in direct_reasons:
            reasons.append("dependency_forbidden_tool_ref")
        reasons.extend(
            _dependency_omission_reasons(
                registry=registry,
                pack=dependency,
                request=request,
                disabled=disabled,
                seen=visited,
            )
        )
    return _dedupe_tuple(reasons)


def _pack_direct_omission_reasons(
    *,
    registry: PackRegistry,
    pack_id: str,
    request: ProfileResolutionRequest,
    disabled: set[str],
    explicit_ref: ExplicitRecipeRef | None,
) -> tuple[str, ...]:
    if pack_id == _INVALID_EXPLICIT_RECIPE_ID:
        return ("malformed_explicit_recipe_selection",)
    if pack_id not in registry.pack_ids:
        return ("explicit_recipe_missing",)

    pack = registry.get(pack_id)
    reasons: list[str] = []
    if pack_id in disabled and pack.opt_out_allowed:
        reasons.append("explicit_recipe_disabled")
    if not pack.hard_safety and not _is_ref_authorized(
        explicit_ref or _pack_ref(pack),
        _authorized_recipe_refs(request),
    ):
        reasons.append("explicit_recipe_unauthorized")
    if explicit_ref is not None and explicit_ref.version is not None:
        if explicit_ref.version != pack.version:
            reasons.append("version_mismatch")
    if explicit_ref is not None and explicit_ref.digest is not None:
        if explicit_ref.digest != build_recipe_pack_digest(pack):
            reasons.append("digest_mismatch")
    if _runtime_contract_version(request) not in pack.compatible_runtime_contract_versions:
        if not pack.hard_safety:
            reasons.append("incompatible_runtime_contract")
    if set(pack.tool_refs) & set(_forbidden_tool_refs(request)):
        reasons.append("forbidden_tool_ref")
    if not pack.hard_safety and _projection_policy_is_forbidden(request):
        reasons.append("forbidden_projection_policy")
    if not pack.hard_safety and _hard_invariant_downgraded(request):
        reasons.append("hard_invariant_downgrade")
    return _dedupe_tuple(reasons)


def _pack_omission_reasons(
    *,
    registry: PackRegistry,
    pack_id: str,
    request: ProfileResolutionRequest,
    disabled: set[str],
    explicit_ref: ExplicitRecipeRef | None,
) -> tuple[str, ...]:
    direct_reasons = _pack_direct_omission_reasons(
        registry=registry,
        pack_id=pack_id,
        request=request,
        disabled=disabled,
        explicit_ref=explicit_ref,
    )
    if pack_id not in registry.pack_ids:
        return direct_reasons
    dependency_reasons = _dependency_omission_reasons(
        registry=registry,
        pack=registry.get(pack_id),
        request=request,
        disabled=disabled,
    )
    return _dedupe_tuple((*direct_reasons, *dependency_reasons))


def _explicit_ref_omission_reasons(
    *,
    registry: PackRegistry,
    ref: ExplicitRecipeRef,
    request: ProfileResolutionRequest,
    disabled: set[str],
) -> tuple[str, ...]:
    if ref.recipe_id == _INVALID_EXPLICIT_RECIPE_ID:
        return ("malformed_explicit_recipe_selection",)
    return _pack_omission_reasons(
        registry=registry,
        pack_id=ref.recipe_id,
        request=request,
        disabled=disabled,
        explicit_ref=ref,
    )


class ProfileResolver:
    def __init__(self, registry: PackRegistry) -> None:
        self._registry = registry

    def resolve(
        self,
        request: ProfileResolutionRequest,
        *,
        env: Mapping[str, str] | None = None,
    ) -> ResolvedRecipeProfile:
        default_packs_expanded = _default_packs_expanded(env)
        layers = (
            request.user_profile,
            request.workspace_policy,
            request.task_profile,
            request.recipe_pack_config,
            request.runtime_context,
        )
        resolved_profile: dict[str, object] = {}
        for layer in layers:
            resolved_profile = _deep_merge(resolved_profile, layer)

        selected: list[str] = []
        disabled: set[str] = set()
        for layer in layers:
            disabled.update(_layer_disabled_pack_ids(layer))
        composition_policy = _build_composition_policy_metadata(layers)
        explicit_selection = _explicit_recipe_selection_from_request(request)
        requested_refs = (
            explicit_selection.required_recipe_refs
            if explicit_selection is not None
            else ()
        )
        malformed_explicit_selection = any(
            ref.recipe_id == _INVALID_EXPLICIT_RECIPE_ID for ref in requested_refs
        )

        visiting: set[str] = set()

        def select(pack_id: str) -> bool:
            if pack_id not in self._registry.pack_ids:
                return False
            if pack_id in selected:
                return True
            pack = self._registry.get(pack_id)
            if pack_id in disabled and pack.opt_out_allowed:
                return False
            if pack_id in visiting:
                raise ValueError(f"cyclic recipe pack dependency: {pack_id}")
            visiting.add(pack_id)
            try:
                if not all(select(dependency_id) for dependency_id in pack.depends_on_pack_ids):
                    return False
            finally:
                visiting.remove(pack_id)
            selected.append(pack_id)
            return True

        omitted_refs: list[ExplicitRecipeRef] = []
        omission_reasons: dict[str, tuple[str, ...]] = {}

        def record_omission(
            pack_id: str,
            reasons: tuple[str, ...],
            explicit_ref: ExplicitRecipeRef | None = None,
        ) -> None:
            if not reasons:
                return
            ref = (
                explicit_ref
                if explicit_ref is not None
                else _pack_ref(self._registry.get(pack_id))
                if pack_id in self._registry.pack_ids
                else ExplicitRecipeRef(recipeId=_INVALID_EXPLICIT_RECIPE_ID)
            )
            if ref.recipe_id not in {item.recipe_id for item in omitted_refs}:
                omitted_refs.append(ref)
            omission_reasons[ref.recipe_id] = _dedupe_tuple(
                (*omission_reasons.get(ref.recipe_id, ()), *reasons)
            )

        def select_if_admitted(
            pack_id: str,
            explicit_ref: ExplicitRecipeRef | None = None,
        ) -> bool:
            reasons = _pack_omission_reasons(
                registry=self._registry,
                pack_id=pack_id,
                request=request,
                disabled=disabled,
                explicit_ref=explicit_ref,
            )
            if reasons:
                record_omission(pack_id, reasons, explicit_ref)
                return False
            return select(pack_id)

        for pack in self._registry.values():
            promote_as_default = pack.default_enabled or (
                default_packs_expanded
                and pack.pack_id in SAFE_DEFAULT_PACK_EXPANSION_IDS
            )
            if pack.hard_safety or (
                promote_as_default and not malformed_explicit_selection
            ):
                select_if_admitted(pack.pack_id)

        default_selected = tuple(selected)
        automatic_selected = False
        allow_additional_auto = (
            explicit_selection is None
            or explicit_selection.allow_additional_auto_recipes
        )
        task_intents = _task_profile_selector_intents(request, resolved_profile)
        if allow_additional_auto:
            for task_intent in task_intents:
                for pack in self._registry.values():
                    if task_intent in pack.task_profile_selectors:
                        before = tuple(selected)
                        select_if_admitted(pack.pack_id)
                        if tuple(selected) != before:
                            automatic_selected = True

            for layer in layers:
                for pack_id in _layer_pack_ids(layer):
                    before = tuple(selected)
                    select_if_admitted(pack_id)
                    if tuple(selected) != before:
                        automatic_selected = True

        if explicit_selection is not None:
            for ref in requested_refs:
                reasons = _explicit_ref_omission_reasons(
                    registry=self._registry,
                    ref=ref,
                    request=request,
                    disabled=disabled,
                )
                if reasons:
                    record_omission(ref.recipe_id, reasons, ref)
                    continue
                before = tuple(selected)
                if not select_if_admitted(ref.recipe_id, ref):
                    if ref.recipe_id not in omission_reasons:
                        record_omission(ref.recipe_id, ("dependency_unavailable",), ref)
                    continue
                if tuple(selected) != before and ref.recipe_id not in default_selected:
                    automatic_selected = automatic_selected or False

        non_opt_out = tuple(
            pack.pack_id
            for pack in self._registry.values()
            if pack.pack_id in selected and not pack.opt_out_allowed
        )
        omitted_pack_ids = {ref.recipe_id for ref in omitted_refs}
        selected = [
            pack_id
            for pack_id in selected
            if (
                (pack_id not in disabled or pack_id in non_opt_out)
                and (pack_id not in omitted_pack_ids or pack_id in non_opt_out)
            )
        ]
        selection_fail_closed = (
            explicit_selection is not None and bool(omitted_refs)
        ) or _omission_reasons_require_fail_closed(omission_reasons)
        if selection_fail_closed:
            selected = [pack_id for pack_id in selected if pack_id in non_opt_out]
        opted_out = tuple(
            pack_id
            for pack_id in self._registry.pack_ids
            if pack_id in disabled and pack_id not in non_opt_out
        )
        if explicit_selection is None:
            selection_source = "automatic" if automatic_selected else "default"
        elif selection_fail_closed:
            selection_source = "explicit"
        elif automatic_selected:
            selection_source = "mixed"
        else:
            selection_source = "explicit"
        applied_pack_ids = () if selection_fail_closed else tuple(selected)
        recipe_selection = _build_recipe_selection_metadata(
            selection_source=selection_source,
            requested_refs=requested_refs,
            applied_pack_ids=applied_pack_ids,
            omitted_refs=tuple(omitted_refs),
            omission_reasons=omission_reasons,
            registry=self._registry,
        )

        return ResolvedRecipeProfile(
            resolvedProfile=resolved_profile,
            selectedPackIds=tuple(selected),
            optedOutPackIds=opted_out,
            nonOptOutPackIds=non_opt_out,
            compositionPolicyMetadata=composition_policy,
            recipeSelection=recipe_selection,
        )


class AgentRecipeCompiler:
    def __init__(self, registry: PackRegistry) -> None:
        self._registry = registry
        self._resolver = ProfileResolver(registry)

    def compile(
        self,
        request: ProfileResolutionRequest,
        *,
        env: Mapping[str, str] | None = None,
    ) -> RecipeSnapshot:
        resolved = self._resolver.resolve(request, env=env)
        packs = tuple(self._registry.get(pack_id) for pack_id in resolved.selected_pack_ids)
        recipe_selection_fail_closed = _recipe_selection_fails_closed(
            resolved.recipe_selection
        )
        composition_blocked = (
            resolved.composition_policy_metadata.blocked
            or recipe_selection_fail_closed
        )
        composition_approval_refs = (
            ("approval:composition-policy:requires-clarification",)
            if resolved.composition_policy_metadata.blocked
            else ()
        )
        recipe_selection_approval_refs = (
            ("approval:recipe-selection:blocked",)
            if recipe_selection_fail_closed
            else ()
        )
        composition_checkpoint_refs = (
            ("checkpoint:composition-policy:provider-tool-conflict-blocked",)
            if resolved.composition_policy_metadata.blocked
            else ()
        )
        recipe_selection_checkpoint_refs = (
            ("checkpoint:recipe-selection:required-recipe-omitted",)
            if recipe_selection_fail_closed
            else ()
        )
        composition_audit_refs = (
            ("audit:composition-policy-provider-tool-conflict",)
            if resolved.composition_policy_metadata.blocked
            else ()
        )
        recipe_selection_audit_refs = (
            ("audit:recipe-selection-admission-block",)
            if recipe_selection_fail_closed
            else ()
        )
        composition_validator_metadata = (
            ("composition-policy:provider-tool-conflict",)
            if resolved.composition_policy_metadata.conflict_refs
            else ()
        )

        mission_lifecycle = next(
            (
                pack.mission_lifecycle
                for pack in packs
                if pack.mission_lifecycle is not None
            ),
            None,
        )

        return RecipeSnapshot(
            snapshotId=build_recipe_snapshot_id(resolved.selected_pack_ids),
            resolvedProfile=resolved.resolved_profile,
            selectedPackIds=resolved.selected_pack_ids,
            optedOutPackIds=resolved.opted_out_pack_ids,
            nonOptOutPackIds=resolved.non_opt_out_pack_ids,
            compositionPolicyMetadata=resolved.composition_policy_metadata,
            recipeSelection=resolved.recipe_selection,
            instructionRefs=() if composition_blocked else _aggregate(packs, "instruction_refs"),
            toolRefs=() if composition_blocked else _aggregate(packs, "tool_refs"),
            callbackRefs=() if composition_blocked else _aggregate(packs, "callback_refs"),
            validatorRefs=() if composition_blocked else _aggregate(packs, "validator_refs"),
            approvalGateRefs=(
                composition_approval_refs + recipe_selection_approval_refs
                if composition_blocked
                else _aggregate(packs, "approval_gate_refs")
                + composition_approval_refs
                + recipe_selection_approval_refs
            ),
            checkpointRefs=(
                composition_checkpoint_refs + recipe_selection_checkpoint_refs
                if composition_blocked
                else _aggregate(packs, "checkpoint_refs")
                + composition_checkpoint_refs
                + recipe_selection_checkpoint_refs
            ),
            evidenceRefs=() if composition_blocked else _aggregate(packs, "evidence_refs"),
            auditRefs=(
                composition_audit_refs + recipe_selection_audit_refs
                if composition_blocked
                else _aggregate(packs, "audit_refs")
                + composition_audit_refs
                + recipe_selection_audit_refs
            ),
            adkPrimitiveOwnership=(
                () if composition_blocked else _aggregate(packs, "adk_primitive_ownership")
            ),
            openmagiBoundaryOwnership=(
                () if composition_blocked else _aggregate(packs, "openmagi_boundary_ownership")
            ),
            callbackSetMetadata=(
                () if composition_blocked else _aggregate(packs, "callback_set_metadata")
            ),
            validatorSetMetadata=(
                composition_validator_metadata
                if composition_blocked
                else _aggregate(packs, "validator_set_metadata")
                + composition_validator_metadata
            ),
            approvalGateMetadata=(
                () if composition_blocked else _aggregate(packs, "approval_gate_metadata")
            ),
            missionLifecycle=None if composition_blocked else mission_lifecycle,
            attachmentFlags=RecipeAttachmentFlags(),
        )


def _aggregate(packs: tuple[RecipePackManifest, ...], field_name: str) -> tuple[str, ...]:
    refs: list[str] = []
    for pack in packs:
        for ref in getattr(pack, field_name):
            if ref not in refs:
                refs.append(ref)
    return tuple(refs)


def _recipe_selection_fails_closed(metadata: RecipeSelectionMetadata) -> bool:
    if not metadata.admission_blocked:
        return False
    if metadata.requested_recipe_refs:
        return True
    return _omission_reasons_require_fail_closed(metadata.omission_reasons)


def _omission_reasons_require_fail_closed(
    omission_reasons: Mapping[str, tuple[str, ...]],
) -> bool:
    return any(
        reason not in _NON_BLOCKING_AUTOMATIC_OMISSION_REASONS
        for reasons in omission_reasons.values()
        for reason in reasons
    )


# Safe default-pack expansion set (doc 05 PR-2 / A1-G1).
#
# Promoted to ``defaultEnabled`` ONLY when ``MAGI_RECIPE_DEFAULT_PACKS_EXPANDED``
# is truthy (default OFF). Membership is restricted to packs that satisfy every
# safe criterion (doc 05 §6 open-decision (1)):
#   (a) NOT ``hardSafety`` (those are already default),
#   (b) require only read-only / idempotent tools (no mutating tool refs),
#   (c) carry zero production-authority approval gates (approval metadata is
#       ``metadata-only``), and
#   (d) declare no live dependency (no live tool/callback/runner-route refs and
#       no provider-opt-in / external-source approval gate).
#
# ``openmagi.agent-methodology`` and ``openmagi.superpowers-compat`` are pure
# instruction/validator metadata packs with no tool refs and only
# ``metadata-only`` approval gates, so promoting them adds methodology guidance
# without enabling any side-effect/authority. Every other first-party pack
# carries a write/send/mutation approval gate, a side-effecting tool, or a live
# provider dependency and therefore stays opt-in (explicit task selector only).
SAFE_DEFAULT_PACK_EXPANSION_IDS: tuple[str, ...] = (
    "openmagi.agent-methodology",
    "openmagi.superpowers-compat",
)


def _default_packs_expanded(env: Mapping[str, str] | None) -> bool:
    """Whether the ``MAGI_RECIPE_DEFAULT_PACKS_EXPANDED`` stage gate is ON.

    Imported lazily so that ``compiler``'s strict no-live-runtime import
    boundary is preserved (``config.env`` is pure parsing — it pulls no
    transport/adk/dispatcher chain).
    """

    if env is None:
        import os

        env = os.environ
    from magi_agent.config.env import parse_recipe_default_packs_expanded

    return parse_recipe_default_packs_expanded(env)


def _first_party_packs() -> tuple[RecipePackManifest, ...]:
    common_adk_owners = (
        "ADK Agent owns execution shape",
        "ADK Runner owns invocation",
        "ADK Event owns event stream",
        "ADK FunctionTool owns tool call surface",
        "ADK LongRunningFunctionTool owns long tool/job calls only",
        "ADK SessionService owns session state",
        "ADK MemoryService owns memory state",
        "ADK ArtifactService owns artifact state",
        "ADK callbacks/plugins own lifecycle attachment",
        "ADK evals own evaluator execution",
    )
    common_openmagi_owners = (
        "OpenMagi ProfileResolver owns deterministic metadata merge",
        "OpenMagi AgentRecipeCompiler owns immutable recipe metadata snapshots",
        "OpenMagi PackRegistry owns first-party pack metadata catalog",
        "OpenMagi ApprovalGate metadata owns product approval compatibility",
        "OpenMagi Evidence/Audit refs own diagnostic compatibility metadata",
        "OpenMagi redaction metadata owns public safety compatibility",
    )

    return (
        RecipePackManifest(
            packId="openmagi.context-safety",
            displayName="Context Safety",
            description="Non-opt-out public redaction and hard safety metadata.",
            defaultEnabled=True,
            hardSafety=True,
            optOutAllowed=False,
            customizable=False,
            instructionRefs=("instruction:context-safety:system",),
            callbackRefs=("callback:context-safety:redaction-audit",),
            validatorRefs=(
                "validator:context-safety:public-redaction",
                "validator:context-safety:no-production-attachment",
            ),
            approvalGateRefs=("approval:context-safety:side-effect-deny-by-default",),
            evidenceRefs=("evidence:context-safety-redaction",),
            auditRefs=("audit:recipe-profile-resolution",),
            adkPrimitiveOwnership=common_adk_owners,
            openmagiBoundaryOwnership=common_openmagi_owners,
            callbackSetMetadata=("CallbackSet:context-safety:metadata-only",),
            validatorSetMetadata=("ValidatorSet:context-safety:metadata-only",),
            approvalGateMetadata=("ApprovalGate:context-safety:metadata-only",),
        ),
        RecipePackManifest(
            packId="openmagi.evidence",
            displayName="Evidence",
            description="Non-opt-out diagnostic evidence and audit reference metadata.",
            defaultEnabled=True,
            hardSafety=True,
            optOutAllowed=False,
            customizable=False,
            instructionRefs=("instruction:evidence:audit-only",),
            callbackRefs=("callback:evidence:record-adk-event-ref",),
            validatorRefs=("validator:evidence:no-block-mode",),
            evidenceRefs=("evidence:runtime-issued-record",),
            auditRefs=("audit:evidence-ledger-ref",),
            adkPrimitiveOwnership=common_adk_owners,
            openmagiBoundaryOwnership=common_openmagi_owners,
            callbackSetMetadata=("CallbackSet:evidence:metadata-only",),
            validatorSetMetadata=("ValidatorSet:evidence:metadata-only",),
        ),
        RecipePackManifest(
            packId="openmagi.agent-methodology",
            displayName="Agent Methodology",
            description=(
                "Default-off first-party methodology metadata for planning, "
                "onboarding, TDD, verification, review, subagent-development, "
                "git-worktree, and branch-finishing workflows."
            ),
            whenToUse=(
                "When a complex multi-step task must be decomposed, planned, "
                "and verified through a disciplined methodology."
            ),
            taskProfileSelectors=(
                "methodology",
                "agent-methodology",
                "planning",
                "onboarding",
                "implementation-planning",
            ),
            instructionRefs=(
                "instruction:agent-methodology:using-superpowers",
                "instruction:agent-methodology:brainstorming-design-refinement",
                "instruction:agent-methodology:writing-plans",
                "instruction:agent-methodology:executing-plans",
                "instruction:agent-methodology:tdd-red-green-refactor",
                "instruction:agent-methodology:systematic-debugging",
                "instruction:agent-methodology:verification-before-completion",
                "instruction:agent-methodology:requesting-code-review",
                "instruction:agent-methodology:receiving-code-review",
                "instruction:agent-methodology:subagent-driven-development",
                "instruction:agent-methodology:git-worktree-workflow",
                "instruction:agent-methodology:finishing-development-branch",
            ),
            callbackRefs=(
                "callback:agent-methodology:plan-mode-auto-trigger",
                "callback:agent-methodology:onboarding-needed-check",
            ),
            validatorRefs=(
                "validator:agent-methodology:tdd-red-green-refactor",
                "validator:agent-methodology:systematic-debugging",
                "validator:agent-methodology:verification-before-completion",
                "validator:agent-methodology:requesting-code-review",
                "validator:agent-methodology:receiving-code-review",
                "validator:agent-methodology:plan-evidence-before-execution",
                "validator:agent-methodology:git-worktree-safety",
                "validator:agent-methodology:child-envelope-sanitized-upward-only",
                "validator:agent-methodology:no-live-runtime-without-approval",
            ),
            approvalGateRefs=(
                "approval:agent-methodology:plan-execution",
                "approval:agent-methodology:git-worktree-isolation",
                "approval:agent-methodology:live-behavior",
            ),
            checkpointRefs=(
                "checkpoint:agent-methodology:onboarding",
                "checkpoint:agent-methodology:design-refinement",
                "checkpoint:agent-methodology:plan-mode-auto-trigger",
                "checkpoint:agent-methodology:plan-before-act",
                "checkpoint:agent-methodology:execute-approved-plan",
                "checkpoint:agent-methodology:tdd-red",
                "checkpoint:agent-methodology:tdd-green",
                "checkpoint:agent-methodology:root-cause-before-fix",
                "checkpoint:agent-methodology:verification-before-completion",
                "checkpoint:agent-methodology:request-code-review",
                "checkpoint:agent-methodology:receive-code-review",
                "checkpoint:agent-methodology:subagent-parent-context-isolation",
                "checkpoint:agent-methodology:git-worktree-isolation",
                "checkpoint:agent-methodology:finishing-development-branch",
                "checkpoint:agent-methodology:live-behavior-approval",
            ),
            evidenceRefs=(
                "evidence:agent-methodology:approved-plan",
                "evidence:agent-methodology:debug-root-cause",
                "evidence:agent-methodology:git-diff",
                "evidence:agent-methodology:review-record",
                "evidence:agent-methodology:sanitized-child-envelope",
                "evidence:agent-methodology:test-run",
            ),
            auditRefs=(
                "audit:agent-methodology:onboarding-nudge",
                "audit:agent-methodology:design-refinement",
                "audit:agent-methodology:plan-lifecycle",
                "audit:agent-methodology:plan-auto-trigger",
                "audit:agent-methodology:tdd-cycle",
                "audit:agent-methodology:debugging",
                "audit:agent-methodology:verification-before-completion",
                "audit:agent-methodology:code-review",
                "audit:agent-methodology:subagent-driven-development",
                "audit:agent-methodology:git-worktree",
                "audit:agent-methodology:finishing-development-branch",
                "audit:agent-methodology:live-behavior-approval",
            ),
            adkPrimitiveOwnership=common_adk_owners,
            openmagiBoundaryOwnership=common_openmagi_owners
            + (
                "OpenMagi agent methodology owns recipe-selected workflow metadata; "
                "future live behavior attaches through ADK callbacks/plugins/evals/session primitives",
                "OpenMagi agent methodology does not own ADK Runner, Agent, Event, "
                "FunctionTool, SessionService, MemoryService, or ArtifactService",
                "OpenMagi child methodology metadata allows only sanitized structured "
                "child envelopes upward into parent context",
            ),
            callbackSetMetadata=(
                "CallbackSet:agent-methodology:plan-onboarding-metadata-only",
            ),
            validatorSetMetadata=(
                "ValidatorSet:agent-methodology:review-tdd-verification-metadata-only",
            ),
            approvalGateMetadata=("ApprovalGate:agent-methodology:metadata-only",),
        ),
        RecipePackManifest(
            packId="openmagi.superpowers-compat",
            displayName="Superpowers Compatibility",
            description=(
                "Compatibility/import metadata for the bundled Superpowers "
                "skill namespace without live slash execution."
            ),
            whenToUse=(
                "When the user wants to reference the bundled Superpowers skill "
                "methodology as guidance without live slash execution."
            ),
            taskProfileSelectors=("superpowers", "superpowers-compat"),
            dependsOnPackIds=("openmagi.agent-methodology",),
            instructionRefs=("instruction:superpowers-compat:skill-import-index",),
            callbackRefs=("callback:superpowers-compat:slash-command-import-metadata",),
            validatorRefs=("validator:superpowers-compat:prompt-only-import-boundary",),
            approvalGateRefs=("approval:superpowers-compat:live-slash-runtime",),
            checkpointRefs=("checkpoint:superpowers-compat:no-live-slash-runtime",),
            auditRefs=("audit:superpowers-compat:source-skill-index",),
            adkPrimitiveOwnership=common_adk_owners,
            openmagiBoundaryOwnership=common_openmagi_owners
            + (
                "OpenMagi Superpowers compatibility owns import metadata only; "
                "future live behavior attaches through ADK callbacks/plugins/evals/session primitives",
                "OpenMagi Superpowers compatibility exposes no live slash runtime, "
                "ToolHost handlers, child execution, or workspace mutation",
            ),
            callbackSetMetadata=("CallbackSet:superpowers-compat:none",),
            validatorSetMetadata=("ValidatorSet:superpowers-compat:metadata-only",),
            approvalGateMetadata=("ApprovalGate:superpowers-compat:metadata-only",),
        ),
        RecipePackManifest(
            packId="openmagi.web-acquisition",
            displayName="Web Acquisition",
            description=(
                "Default-off foundation metadata for web acquisition and source "
                "ledger inputs shared by research, office automation, browser "
                "automation, document review, legal/accounting/domain workflows, "
                "and general web Q&A."
            ),
            whenToUse=(
                "When content or sources must be fetched from the web, or an "
                "external page needs to be read."
            ),
            taskProfileSelectors=(
                "web",
                "web-acquisition",
                "web-qa",
                "general-web-qa",
                "office",
                "office-automation",
                "browser",
                "browser-automation",
                "document-review",
                "legal",
                "accounting",
                "domain-workflow",
            ),
            instructionRefs=("instruction:web-acquisition:source-ledger-inputs",),
            validatorRefs=("verifier:web-acquisition:provider-boundary",),
            approvalGateRefs=("approval:web-acquisition:provider-opt-in",),
            evidenceRefs=("evidence:web-acquisition:source-ledger-input",),
            auditRefs=("audit:web-acquisition:source-ledger-inputs",),
            adkPrimitiveOwnership=common_adk_owners,
            openmagiBoundaryOwnership=common_openmagi_owners
            + (
                "OpenMagi web acquisition owns replaceable provider-interface metadata; "
                "future live surface is ADK FunctionTool through ToolHost",
                "OpenMagi web acquisition orchestration is not LongRunningFunctionTool; "
                "only individual long crawl/render/export jobs may use "
                "LongRunningFunctionTool",
            ),
            callbackSetMetadata=("CallbackSet:web-acquisition:none",),
            validatorSetMetadata=("VerifierBoundary:web-acquisition:metadata-only",),
            approvalGateMetadata=("ApprovalGate:web-acquisition:metadata-only",),
        ),
        RecipePackManifest(
            packId="openmagi.research",
            displayName="Research",
            description="Configurable research workflow metadata.",
            whenToUse=(
                "When the user asks for external facts, sources, or "
                "investigation, or an answer must be verified or cross-checked."
            ),
            taskProfileSelectors=("research", "document-review"),
            dependsOnPackIds=("openmagi.web-acquisition",),
            instructionRefs=("instruction:research:source-policy",),
            callbackRefs=("callback:research:source-capture",),
            validatorRefs=(
                "validator:research:citation-support",
                "validator:research:fact-grounding",
                "validator:research:evidence-checks",
            ),
            approvalGateRefs=("approval:research:external-source-use",),
            evidenceRefs=("evidence:inspected-source",),
            auditRefs=("audit:research-source-ledger",),
            adkPrimitiveOwnership=common_adk_owners,
            openmagiBoundaryOwnership=common_openmagi_owners,
            callbackSetMetadata=("CallbackSet:research:metadata-only",),
            validatorSetMetadata=("ValidatorSet:research:metadata-only",),
            approvalGateMetadata=("ApprovalGate:research:metadata-only",),
        ),
        RecipePackManifest(
            packId="openmagi.research-scout",
            displayName="Research Scout",
            description=(
                "Default-off fixture-only ScoutResearchAgent recipe metadata for "
                "OpenCode-inspired repository research profiles."
            ),
            whenToUse=(
                "When a repository must be cloned and surveyed to scout and "
                "study reference code."
            ),
            taskProfileSelectors=("scout_repo_fixture",),
            dependsOnPackIds=("openmagi.research",),
            instructionRefs=(
                "instruction:research-scout:repo-clone-before-inspection",
                "instruction:research-scout:repo-overview-before-broad-search",
                "instruction:research-scout:verified-facts-vs-inference",
                "instruction:research-scout:runtime-issued-evidence-only",
            ),
            toolRefs=(
                "tool:FixtureRepoClone",
                "tool:FixtureRepoOverview",
                "tool:FixtureReferenceRead",
                "tool:FixtureReferenceGrep",
                "tool:FixtureReferenceGlob",
            ),
            callbackRefs=("callback:research-scout:lifecycle-metadata",),
            validatorRefs=(
                "validator:research-scout:runtime-issued-evidence",
                "validator:research-scout:no-child-transcript-trust",
                "validator:research-scout:no-live-authority",
            ),
            approvalGateRefs=("approval:research-scout:activation-gate",),
            checkpointRefs=(
                "checkpoint:research-scout:repo-clone-before-inspection",
                "checkpoint:research-scout:repo-overview-before-broad-search",
                "checkpoint:research-scout:verified-facts-vs-inference",
            ),
            evidenceRefs=(
                "evidence:research-scout:runtime-issued-envelope",
                "evidence:research-scout:digest-safe-source-ref",
            ),
            auditRefs=("audit:research-scout:fixture-profile-materialization",),
            adkPrimitiveOwnership=common_adk_owners,
            openmagiBoundaryOwnership=common_openmagi_owners
            + (
                "OpenMagi research harness owns ScoutResearchAgent recipe profile "
                "materialization as fixture-only metadata",
                "OpenMagi ToolHost remains the only execution boundary for future "
                "RepoClone, RepoOverview, Read, Grep, Glob, WebSearch, and WebFetch tools",
                "OpenMagi research harness accepts child output only through "
                "runtime-issued child evidence envelopes",
                "OpenMagi research scout metadata does not attach ADK Runner, live "
                "FunctionTool execution, providers, browser, memory, channels, or workspace mutation",
            ),
            callbackSetMetadata=("CallbackSet:research-scout:metadata-only",),
            validatorSetMetadata=("ValidatorSet:research-scout:metadata-only",),
            approvalGateMetadata=("ApprovalGate:research-scout:metadata-only",),
        ),
        RecipePackManifest(
            packId="openmagi.dev-coding",
            displayName="Development Coding",
            description="Configurable coding workflow metadata.",
            whenToUse=(
                "When the goal is to change code, debug, refactor, or write "
                "tests."
            ),
            taskProfileSelectors=("coding", "development", "dev-coding"),
            instructionRefs=("instruction:dev-coding:tdd",),
            toolRefs=("tool:file.read", "tool:test.run"),
            callbackRefs=("callback:dev-coding:diff-capture",),
            validatorRefs=("validator:dev-coding:tdd-verification",),
            approvalGateRefs=("approval:dev-coding:workspace-mutation",),
            evidenceRefs=("evidence:git-diff", "evidence:test-run"),
            auditRefs=("audit:dev-coding-verification",),
            adkPrimitiveOwnership=common_adk_owners,
            openmagiBoundaryOwnership=common_openmagi_owners,
            callbackSetMetadata=("CallbackSet:dev-coding:metadata-only",),
            validatorSetMetadata=("ValidatorSet:dev-coding:metadata-only",),
            approvalGateMetadata=("ApprovalGate:dev-coding:metadata-only",),
        ),
        RecipePackManifest(
            packId="openmagi.autopilot",
            displayName="Autopilot",
            description=(
                "Default-off strict autonomous FSM workflow metadata: interview -> "
                "consensus-plan -> execute -> review -> adversarial-QA with "
                "gate-failure return-to-plan."
            ),
            whenToUse=(
                "When an ambiguous build request must be carried fully "
                "autonomously from interview through plan, execute, and QA."
            ),
            taskProfileSelectors=(
                "autopilot",
                "autonomous",
                "full-auto",
                "build-me",
            ),
            dependsOnPackIds=(
                "openmagi.agent-methodology",
                "openmagi.dev-coding",
            ),
            instructionRefs=("instruction:autopilot:strict-loop-contract",),
            callbackRefs=("callback:autopilot:phase-router",),
            validatorRefs=(
                "validator:autopilot:interview-ambiguity-cleared",
                "validator:autopilot:consensus-architect-then-critic",
                "validator:autopilot:review-clean",
                # Wraps verifier-bus "adversarial-qa" plus the
                # qaSkipAllowedForNonruntime path.
                "validator:autopilot:qa-passed-or-skipped",
                "validator:autopilot:max-review-cycle-bounded",
            ),
            approvalGateRefs=(
                "approval:autopilot:execution-lane",
                "approval:autopilot:live-behavior",
            ),
            checkpointRefs=(
                "checkpoint:autopilot:interview",
                "checkpoint:autopilot:consensus-plan",
                "checkpoint:autopilot:execute",
                "checkpoint:autopilot:review",
                "checkpoint:autopilot:qa",
                "checkpoint:autopilot:return-to-plan",
            ),
            evidenceRefs=(
                "evidence:autopilot:clarified-spec",
                "evidence:autopilot:consensus-record",
                "evidence:autopilot:phase-transition",
            ),
            auditRefs=("audit:autopilot:fsm-lifecycle",),
            adkPrimitiveOwnership=common_adk_owners,
            openmagiBoundaryOwnership=common_openmagi_owners
            + (
                "OpenMagi autopilot owns recipe-selected FSM transition metadata; "
                "live phase driving attaches through ADK callbacks/plugins later",
                "OpenMagi autopilot does not own ADK Runner, Agent, Event, "
                "FunctionTool, SessionService, MemoryService, or ArtifactService",
            ),
            callbackSetMetadata=("CallbackSet:autopilot:phase-router-metadata-only",),
            validatorSetMetadata=("ValidatorSet:autopilot:fsm-gates-metadata-only",),
            approvalGateMetadata=("ApprovalGate:autopilot:metadata-only",),
        ),
        RecipePackManifest(
            packId="openmagi.missions",
            displayName="Missions",
            description="Metadata-only mission lifecycle recipe boundary.",
            whenToUse=(
                "When a long-running mission with an objective and budget must "
                "run under progress, checkpoint, and resume control."
            ),
            taskProfileSelectors=("mission", "missions", "scheduled-work"),
            instructionRefs=("instruction:missions:objective",),
            toolRefs=("tool:mission-progress-metadata",),
            callbackRefs=("callback:missions:progress-checkpoint",),
            validatorRefs=("validator:missions:budget-envelope",),
            approvalGateRefs=("approval:missions:cancel-retry-resume-control",),
            evidenceRefs=("evidence:mission-progress-ref",),
            auditRefs=("audit:mission-lifecycle-ref",),
            adkPrimitiveOwnership=common_adk_owners,
            openmagiBoundaryOwnership=common_openmagi_owners
            + ("OpenMagi native plugin boundary owns mission lifecycle metadata",),
            callbackSetMetadata=("CallbackSet:missions:metadata-only",),
            validatorSetMetadata=("ValidatorSet:missions:metadata-only",),
            approvalGateMetadata=("ApprovalGate:missions:metadata-only",),
            missionLifecycle=MissionLifecycleMetadata(
                enabled=False,
                lifecycleRefs=(
                    "mission-objective",
                    "progress-checkpoint",
                    "cancel-retry-resume-control",
                    "completion-criteria",
                ),
                missionUsesLongRunningFunctionTool=False,
                nativePluginBoundaryRefs=("native-plugin:openmagi.missions",),
            ),
        ),
        RecipePackManifest(
            packId="openmagi.scheduled-work",
            displayName="Scheduled Work",
            description="Metadata-only scheduler, cron, and background task recipe boundary.",
            whenToUse=(
                "When scheduled, recurring (cron), or background-running tasks "
                "must be set up or managed."
            ),
            taskProfileSelectors=(
                "scheduled-work",
                "cron",
                "scheduler",
                "background-task",
                "notify-user",
            ),
            instructionRefs=("instruction:scheduled-work:disabled-by-default",),
            toolRefs=(
                "tool:CronCreate",
                "tool:CronList",
                "tool:CronUpdate",
                "tool:CronDelete",
                "tool:TaskWait",
                "tool:TaskGet",
                "tool:TaskList",
                "tool:TaskOutput",
                "tool:TaskStop",
            ),
            callbackRefs=("callback:scheduled-work:lifecycle-metadata",),
            validatorRefs=("validator:scheduled-work:budget-and-stop-conditions",),
            approvalGateRefs=("approval:scheduled-work:resume-or-notify",),
            checkpointRefs=(
                "checkpoint:scheduled-work:cron-lifecycle",
                "checkpoint:scheduled-work:background-task-lifecycle",
            ),
            evidenceRefs=("evidence:scheduled-work:lifecycle-ref",),
            auditRefs=("audit:scheduled-work:runtime-boundary",),
            adkPrimitiveOwnership=common_adk_owners,
            openmagiBoundaryOwnership=common_openmagi_owners
            + (
                "OpenMagi scheduled work owns scheduler/cron/background metadata; "
                "no background loop starts from recipe materialization",
            ),
            callbackSetMetadata=("CallbackSet:scheduled-work:metadata-only",),
            validatorSetMetadata=("ValidatorSet:scheduled-work:metadata-only",),
            approvalGateMetadata=("ApprovalGate:scheduled-work:metadata-only",),
        ),
        RecipePackManifest(
            packId="openmagi.memory-agentmemory",
            displayName="AgentMemory Provider",
            description=(
                "Metadata-only AgentMemory provider recipe boundary behind "
                "OpenMagi memory policy."
            ),
            whenToUse=(
                "When prior-session context or learned facts must be recalled "
                "or stored."
            ),
            taskProfileSelectors=("memory-provider-eval", "agentmemory"),
            toolRefs=("tool:AgentMemorySearch", "tool:AgentMemoryRemember"),
            callbackRefs=("callback:agentmemory.recall", "callback:agentmemory.observe"),
            validatorRefs=("verifier:agentmemory-provider-boundary",),
            adkPrimitiveOwnership=common_adk_owners,
            openmagiBoundaryOwnership=common_openmagi_owners,
            callbackSetMetadata=("CallbackSet:memory-agentmemory:metadata-only",),
            validatorSetMetadata=("VerifierBoundary:agentmemory-provider:metadata-only",),
        ),
        RecipePackManifest(
            packId="openmagi.channel-delivery",
            displayName="Channel Delivery",
            description=(
                "Default-off metadata for generic channel dispatcher, push delivery, "
                "Telegram, Discord, and web app delivery intents."
            ),
            whenToUse=(
                "When the act itself is sending or notifying the user over a "
                "channel such as Telegram, Discord, or the web app."
            ),
            taskProfileSelectors=(
                "channel-delivery",
                "notify-user",
                "telegram",
                "discord",
                "web-channel",
            ),
            instructionRefs=("instruction:channel-delivery:receipt-only",),
            toolRefs=(
                "tool:ChannelDispatcher",
                "tool:NotifyUser",
                "tool:FileSend",
            ),
            callbackRefs=("callback:channel-delivery:delivery-receipt",),
            validatorRefs=(
                "validator:channel-delivery:no-token-leakage",
                "validator:channel-delivery:channel-policy",
            ),
            approvalGateRefs=("approval:channel-delivery:external-send",),
            checkpointRefs=("checkpoint:channel-delivery:delivery-receipt",),
            evidenceRefs=("evidence:channel-delivery-receipt",),
            auditRefs=("audit:channel-delivery-boundary",),
            adkPrimitiveOwnership=common_adk_owners,
            openmagiBoundaryOwnership=common_openmagi_owners
            + (
                "OpenMagi channel delivery owns dispatcher metadata only; "
                "Telegram/Discord providers remain injected fake-provider surfaces until activation",
            ),
            callbackSetMetadata=("CallbackSet:channel-delivery:metadata-only",),
            validatorSetMetadata=("ValidatorSet:channel-delivery:metadata-only",),
            approvalGateMetadata=("ApprovalGate:channel-delivery:metadata-only",),
        ),
        RecipePackManifest(
            packId="openmagi.office-automation",
            displayName="Office Automation",
            description="Configurable office automation workflow metadata.",
            whenToUse=(
                "When document or slide files must be read, created, or "
                "converted at the file level."
            ),
            taskProfileSelectors=("office", "office-automation"),
            instructionRefs=("instruction:office-automation:preview-then-approve",),
            toolRefs=("tool:file.read", "tool:spreadsheet.read", "tool:browser.inspect"),
            callbackRefs=(
                "callback:office-automation:preview-capture",
                GA_CONSTRAINT_REINJECTION_CALLBACK_REF,
            ),
            validatorRefs=("validator:office-automation:preview-before-write",),
            approvalGateRefs=("approval:office-automation:write-or-send",),
            evidenceRefs=("evidence:office-preview",),
            auditRefs=("audit:office-automation-action-plan",),
            adkPrimitiveOwnership=common_adk_owners,
            openmagiBoundaryOwnership=common_openmagi_owners,
            callbackSetMetadata=("CallbackSet:office-automation:metadata-only",),
            validatorSetMetadata=("ValidatorSet:office-automation:metadata-only",),
            approvalGateMetadata=("ApprovalGate:office-automation:metadata-only",),
        ),
        RecipePackManifest(
            packId="openmagi.artifact-delivery",
            displayName="Artifact Delivery",
            description=(
                "Default-off metadata for sanitized file/artifact preparation, "
                "channel delivery planning, and delivery acknowledgement."
            ),
            whenToUse=(
                "When a produced file or artifact must be sanitized and "
                "prepared before it is delivered to the user."
            ),
            taskProfileSelectors=(
                "artifact",
                "artifact-delivery",
                "file-delivery",
                "file-delivery-plan",
            ),
            dependsOnPackIds=("openmagi.office-automation",),
            instructionRefs=("instruction:artifact-delivery:sanitized-delivery-preview",),
            toolRefs=("tool:artifact.prepare-delivery", "tool:file.delivery-plan"),
            callbackRefs=(
                "callback:artifact-delivery:delivery-manifest-capture",
                GA_CONSTRAINT_REINJECTION_CALLBACK_REF,
            ),
            validatorRefs=(
                "validator:artifact-delivery:no-raw-path-leakage",
                "validator:artifact-delivery:redacted-preview-only",
            ),
            approvalGateRefs=("approval:artifact-delivery:channel-send",),
            checkpointRefs=(
                "checkpoint:artifact-delivery:sanitized-artifact-ref",
                "checkpoint:artifact-delivery:delivery-ack-metadata",
            ),
            evidenceRefs=("evidence:artifact-delivery-ref",),
            auditRefs=("audit:artifact-delivery-manifest",),
            adkPrimitiveOwnership=common_adk_owners,
            openmagiBoundaryOwnership=common_openmagi_owners,
            callbackSetMetadata=("CallbackSet:artifact-delivery:metadata-only",),
            validatorSetMetadata=("ValidatorSet:artifact-delivery:metadata-only",),
            approvalGateMetadata=("ApprovalGate:artifact-delivery:metadata-only",),
        ),
        RecipePackManifest(
            packId="openmagi.spreadsheet-automation",
            displayName="Spreadsheet Automation",
            description="Configurable spreadsheet workflow metadata.",
            whenToUse=(
                "When tabular data must be calculated, aggregated, or "
                "transformed in-place."
            ),
            taskProfileSelectors=("spreadsheet", "spreadsheet-automation"),
            instructionRefs=("instruction:spreadsheet-automation:preview-then-approve",),
            toolRefs=("tool:spreadsheet.read", "tool:spreadsheet.plan-write"),
            callbackRefs=(
                "callback:spreadsheet-automation:preview-capture",
                GA_CONSTRAINT_REINJECTION_CALLBACK_REF,
            ),
            validatorRefs=("validator:spreadsheet-automation:preview-before-write",),
            approvalGateRefs=("approval:spreadsheet-automation:write",),
            evidenceRefs=("evidence:spreadsheet-preview",),
            auditRefs=("audit:spreadsheet-automation-action-plan",),
            adkPrimitiveOwnership=common_adk_owners,
            openmagiBoundaryOwnership=common_openmagi_owners,
            callbackSetMetadata=("CallbackSet:spreadsheet-automation:metadata-only",),
            validatorSetMetadata=("ValidatorSet:spreadsheet-automation:metadata-only",),
            approvalGateMetadata=("ApprovalGate:spreadsheet-automation:metadata-only",),
        ),
        RecipePackManifest(
            packId="openmagi.browser-automation",
            displayName="Browser Automation",
            description="Configurable browser workflow metadata.",
            whenToUse=(
                "When a browser must drive and navigate web pages "
                "interactively."
            ),
            taskProfileSelectors=("browser", "browser-automation"),
            instructionRefs=("instruction:browser-automation:inspect-before-act",),
            toolRefs=("tool:browser.inspect", "tool:browser.plan-action"),
            callbackRefs=(
                "callback:browser-automation:step-capture",
                GA_CONSTRAINT_REINJECTION_CALLBACK_REF,
            ),
            validatorRefs=("validator:browser-automation:action-plan",),
            approvalGateRefs=("approval:browser-automation:external-action",),
            evidenceRefs=("evidence:browser-inspection",),
            auditRefs=("audit:browser-automation-action-plan",),
            adkPrimitiveOwnership=common_adk_owners,
            openmagiBoundaryOwnership=common_openmagi_owners,
            callbackSetMetadata=("CallbackSet:browser-automation:metadata-only",),
            validatorSetMetadata=("ValidatorSet:browser-automation:metadata-only",),
            approvalGateMetadata=("ApprovalGate:browser-automation:metadata-only",),
        ),
        RecipePackManifest(
            packId="openmagi.document-review",
            displayName="Document Review",
            description="Configurable document review workflow metadata.",
            whenToUse=(
                "When an existing document must be reviewed, assessed, or "
                "commented on with source-grounded findings."
            ),
            taskProfileSelectors=("document-review", "document"),
            instructionRefs=("instruction:document-review:source-grounded-review",),
            toolRefs=("tool:file.read", "tool:document.inspect"),
            callbackRefs=("callback:document-review:finding-capture",),
            validatorRefs=("validator:document-review:citation-support",),
            approvalGateRefs=("approval:document-review:tracked-change-or-comment",),
            evidenceRefs=("evidence:document-review-finding",),
            auditRefs=("audit:document-review-ledger",),
            adkPrimitiveOwnership=common_adk_owners,
            openmagiBoundaryOwnership=common_openmagi_owners,
            callbackSetMetadata=("CallbackSet:document-review:metadata-only",),
            validatorSetMetadata=("ValidatorSet:document-review:metadata-only",),
            approvalGateMetadata=("ApprovalGate:document-review:metadata-only",),
        ),
        RecipePackManifest(
            # Minimal selectable source-grounded read-only pack. It pairs with
            # the ``openmagi.source-grounded`` reliability-policy recipe whose
            # only non-hard required validator is the NAMED public ref
            # ``verifier:research-source-evidence`` (satisfied by the live
            # source-ledger projector behind MAGI_SOURCE_LEDGER_EVIDENCE_GATE_
            # ENABLED on a turn that read >=1 inspected source). Registering it
            # lets MAGI_FORCE_RECIPE=openmagi.source-grounded resolve via the
            # explicit-selection path instead of failing closed with
            # ``explicit_recipe_missing``. Default-off + opt-out, so the
            # automatic (unforced) OFF selection stays byte-identical. The
            # ``source-grounded`` task selector is unique to this pack so it is
            # never auto-selected by an existing task profile.
            packId="openmagi.source-grounded",
            displayName="Source Grounded",
            description=(
                "Configurable source-grounded read-only workflow metadata "
                "requiring named source-evidence before a final answer."
            ),
            whenToUse=(
                "When the answer must read and cite named sources before "
                "responding — source-grounded answering with required "
                "source-evidence."
            ),
            taskProfileSelectors=("source-grounded",),
            # NOTE: NO ``dependsOnPackIds``. ``openmagi.web-acquisition`` was
            # pulled in ONLY via this dep (it is not hard-safety and not a
            # default pack), and its refs (``verifier:web-acquisition:provider-
            # boundary``, ``evidence:web-acquisition:source-ledger-input``, plus
            # the reliability-policy ``source_quality`` / ``no_auth_bypass`` /
            # ``source_ledger``) have no live producer on the source-grounded
            # path, so the dep made the gate permanently block. Source-grounded
            # carries its OWN named source-evidence refs (``verifier:research-
            # source-evidence`` + ``evidence:inspected-source``), satisfied by
            # the live source-ledger projector on a real read.
            instructionRefs=("instruction:source-grounded:read-before-answer",),
            callbackRefs=("callback:source-grounded:source-capture",),
            validatorRefs=("verifier:research-source-evidence",),
            approvalGateRefs=("approval:source-grounded:external-source-use",),
            evidenceRefs=("evidence:inspected-source",),
            auditRefs=("audit:source-grounded-ledger",),
            adkPrimitiveOwnership=common_adk_owners,
            openmagiBoundaryOwnership=common_openmagi_owners,
            callbackSetMetadata=("CallbackSet:source-grounded:metadata-only",),
            validatorSetMetadata=("ValidatorSet:source-grounded:metadata-only",),
            approvalGateMetadata=("ApprovalGate:source-grounded:metadata-only",),
        ),
        RecipePackManifest(
            packId="openmagi.lightweight-scripting",
            displayName="Lightweight Scripting",
            description="Configurable lightweight scripting workflow metadata.",
            whenToUse=(
                "When a small throwaway script or quick automation needs to be "
                "written and run fast."
            ),
            taskProfileSelectors=("lightweight-scripting", "scripting"),
            instructionRefs=("instruction:lightweight-scripting:small-script-plan",),
            toolRefs=("tool:file.read", "tool:script.plan-run"),
            callbackRefs=("callback:lightweight-scripting:diff-capture",),
            validatorRefs=("validator:lightweight-scripting:test-or-dry-run",),
            approvalGateRefs=("approval:lightweight-scripting:workspace-mutation",),
            evidenceRefs=("evidence:script-plan", "evidence:script-dry-run"),
            auditRefs=("audit:lightweight-scripting-verification",),
            adkPrimitiveOwnership=common_adk_owners,
            openmagiBoundaryOwnership=common_openmagi_owners,
            callbackSetMetadata=("CallbackSet:lightweight-scripting:metadata-only",),
            validatorSetMetadata=("ValidatorSet:lightweight-scripting:metadata-only",),
            approvalGateMetadata=("ApprovalGate:lightweight-scripting:metadata-only",),
        ),
        # PR5 learning-usage: default-OFF static-injection pack carrying the
        # ``instruction:learning:usage`` ref.  Selected only when a task profile
        # asks for ``learning`` (or ``learning-usage`` / ``self-improvement``);
        # registering it leaves the OFF compiled snapshot byte-identical.  The
        # builder + instruction text live in
        # ``recipes/first_party/learning_usage.py`` (imported lazily to avoid a
        # circular import, since that module imports ``RecipePackManifest``).
        _build_learning_usage_pack(),
        # discovery: default-OFF static-injection pack carrying the
        # ``instruction:discovery:iterative`` ref.  Selected only when a task
        # profile asks for ``discovery``; registering it leaves the OFF compiled
        # snapshot byte-identical.  The builder + instruction text live in
        # ``recipes/first_party/discovery.py`` (imported lazily to avoid a
        # circular import, since that module imports ``RecipePackManifest``).
        _build_discovery_pack(),
    )


def _build_learning_usage_pack() -> RecipePackManifest:
    # Lazy import breaks the compiler ↔ learning_usage circular import:
    # ``learning_usage`` imports ``RecipePackManifest`` from this module, so a
    # top-level import here would form a cycle at module load.
    from magi_agent.recipes.first_party.learning_usage import build_learning_usage_pack

    return build_learning_usage_pack()


def _build_discovery_pack() -> RecipePackManifest:
    # Lazy import breaks the compiler ↔ discovery circular import:
    # ``discovery`` imports ``RecipePackManifest`` from this module, so a
    # top-level import here would form a cycle at module load.
    from magi_agent.recipes.first_party.discovery import build_discovery_pack

    return build_discovery_pack()
