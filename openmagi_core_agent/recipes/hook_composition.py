from __future__ import annotations

from collections.abc import Iterable, Mapping
import hashlib
import json
import re
from typing import Any, Literal, Self
import weakref

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PrivateAttr,
    StrictBool,
    StrictInt,
    StrictStr,
    field_serializer,
    model_serializer,
    model_validator,
    field_validator,
)


_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    hide_input_in_errors=True,
    revalidate_instances="always",
    validate_default=True,
)
_SAFE_RECIPE_REF_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}(?:\.[a-z0-9][a-z0-9_-]{0,63})+$")
_PUBLIC_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_SHA256_DIGEST_RE = re.compile(r"^sha256:[a-f0-9]{64}$")
_PRIVATE_PATH_RE = re.compile(
    r"(?:/Users|/home|/private|/var/lib/kubelet|/var/run/secrets|/workspace|/data/bots)"
)
_UNSAFE_TEXT_FRAGMENTS = frozenset((
    "accesskey",
    "apikey",
    "auth",
    "authheader",
    "secret",
    "sessionkey",
    "token",
    "credential",
    "password",
    "bearer",
    "authorization",
    "cookie",
    "privateconfig",
    "privatekey",
    "rawconfig",
    "rawprompt",
    "rawtool",
    "skproj",
    "toolargs",
    "toolarguments",
    "toolresult",
    "toolresults",
))
_UNSAFE_SECRET_SHAPED_RE = re.compile(
    r"(?:"
    r"sk[-_](?:proj[-_])?[a-z0-9_-]{8,}"
    r"|rk[-_][a-z0-9_-]{8,}"
    r"|glpat-[a-z0-9_-]{8,}"
    r"|github_pat_[a-z0-9_]{8,}"
    r"|gh[pousr]_[a-z0-9]{8,}"
    r"|xox[a-z0-9]*-[a-z0-9-]{8,}"
    r"|(?:A3T[A-Z0-9]|AKIA|ASIA|AGPA|AIDA|AROA|AIPA|ANPA|ANVA)[A-Z0-9]{16}"
    r"|AIza[0-9A-Za-z_-]{20,}"
    r"|eyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"
    r")",
    re.IGNORECASE,
)
_STAGE_ORDER = {
    "beforeTurnStart": 0,
    "beforeSystemPrompt": 4,
    "beforeMessageSend": 7,
    "beforeLLMCall": 10,
    "beforeToolUse": 20,
    "beforeCommit": 30,
    "beforeCompaction": 40,
    "onTaskCheckpoint": 50,
    "onRuleViolation": 60,
    "onArtifactCreated": 70,
    "afterToolUse": 80,
    "afterLLMCall": 90,
    "afterCommit": 100,
    "afterCompaction": 110,
    "afterTurnEnd": 120,
    "onAbort": 130,
    "onError": 140,
}


class _IdentityDigestRegistry:
    def __init__(self) -> None:
        self._entries: dict[int, tuple[weakref.ReferenceType[object], str]] = {}

    def mark(self, value: object, digest: object, field_name: str) -> None:
        object_id = id(value)

        def _remove_stale(ref: weakref.ReferenceType[object]) -> None:
            entry = self._entries.get(object_id)
            if entry is not None and entry[0] is ref:
                self._entries.pop(object_id, None)

        ref = weakref.ref(value, _remove_stale)
        self._entries[object_id] = (ref, _safe_digest(digest, field_name))

    def digest_for(self, value: object) -> str | None:
        object_id = id(value)
        entry = self._entries.get(object_id)
        if entry is None:
            return None
        ref, digest = entry
        if ref() is not value:
            self._entries.pop(object_id, None)
            return None
        return digest


_REGISTRY_ADMITTED_HOOK_DIGESTS = _IdentityDigestRegistry()
_HOOK_CONTRACT_DIGESTS = _IdentityDigestRegistry()


def _contains_unsafe_text(value: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]", "", value.strip().lower())
    return (
        _PRIVATE_PATH_RE.search(value) is not None
        or _UNSAFE_SECRET_SHAPED_RE.search(value) is not None
        or any(fragment in normalized for fragment in _UNSAFE_TEXT_FRAGMENTS)
    )


def _safe_digest(value: object, field_name: str = "digest") -> str:
    if not isinstance(value, str) or _SHA256_DIGEST_RE.fullmatch(value) is None:
        raise ValueError(f"{field_name} must be a sha256 digest")
    return value.lower()


def _public_id(value: object, field_name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a public identifier")
    public_value = value.strip()
    if not public_value:
        raise ValueError(f"{field_name} must be a public identifier")
    if _contains_unsafe_text(public_value) or _PUBLIC_ID_RE.fullmatch(public_value) is None:
        raise ValueError(f"{field_name} must be a public identifier")
    return public_value


def _public_optional_id(value: object, field_name: str) -> str | None:
    if value is None:
        return None
    return _public_id(value, field_name)


def _recipe_ref(value: object, field_name: str = "recipe_ref") -> str:
    ref = _public_id(value, field_name)
    if _SAFE_RECIPE_REF_RE.fullmatch(ref) is None:
        raise ValueError(f"{field_name} must be a recipe ref")
    return ref


def _tuple_values(value: object, field_name: str) -> tuple[object, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, Iterable) and not isinstance(value, (bytes, bytearray, Mapping)):
        return tuple(value)
    raise ValueError(f"{field_name} must be iterable")


def _public_id_tuple(value: object, field_name: str) -> tuple[str, ...]:
    refs: set[str] = set()
    for raw_value in _tuple_values(value, field_name):
        refs.add(_public_id(raw_value, field_name))
    return tuple(sorted(refs))


def _recipe_ref_tuple(value: object, field_name: str) -> tuple[str, ...]:
    refs: set[str] = set()
    for raw_value in _tuple_values(value, field_name):
        refs.add(_recipe_ref(raw_value, field_name))
    return tuple(sorted(refs))


def _canonical_digest(payload: Mapping[str, object]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _json_safe_private_config(value: object) -> dict[str, object]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValueError("private_config must be a mapping")
    try:
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":"))
        decoded = json.loads(encoded)
    except (TypeError, ValueError):
        raise ValueError("private_config must be JSON serializable") from None
    if not isinstance(decoded, dict):
        raise ValueError("private_config must be a JSON object")
    return decoded


def _private_config_digest(value: object) -> str:
    return _canonical_digest(_json_safe_private_config(value))


class HookCompositionConflict(BaseModel):
    model_config = _MODEL_CONFIG

    code: StrictStr
    subject_ref: StrictStr = Field(alias="subjectRef")
    recipe_refs: tuple[str, ...] = Field(default=(), alias="recipeRefs")
    hook_ids: tuple[str, ...] = Field(default=(), alias="hookIds")
    blocking: StrictBool

    @field_validator("code", "subject_ref")
    @classmethod
    def _sanitize_public_scalar(cls, value: object, info: Any) -> str:
        return _public_id(value, info.field_name)

    @field_validator("recipe_refs", mode="before")
    @classmethod
    def _sanitize_recipe_refs(cls, value: object) -> tuple[str, ...]:
        return _recipe_ref_tuple(value, "recipe_refs")

    @field_validator("hook_ids", mode="before")
    @classmethod
    def _sanitize_hook_ids(cls, value: object) -> tuple[str, ...]:
        return _public_id_tuple(value, "hook_ids")

    def public_projection(self) -> dict[str, object]:
        return {
            "code": _public_id(self.code, "code"),
            "blocking": bool(self.blocking),
            "subjectDigest": _canonical_digest(
                {"subjectRef": _public_id(self.subject_ref, "subject_ref")}
            ),
            "recipeRefCount": len(_recipe_ref_tuple(self.recipe_refs, "recipe_refs")),
            "hookIdCount": len(_public_id_tuple(self.hook_ids, "hook_ids")),
        }

    def model_dump(self, *args: Any, **kwargs: Any) -> dict[str, object]:
        return HookCompositionConflict.public_projection(self)

    def model_dump_json(self, *args: Any, **kwargs: Any) -> str:
        indent = kwargs.get("indent")
        separators = None if indent is not None else (",", ":")
        return json.dumps(
            HookCompositionConflict.public_projection(self),
            indent=indent,
            separators=separators,
            sort_keys=True,
        )

    @model_serializer(mode="plain")
    def _serialize_model(self) -> dict[str, object]:
        return HookCompositionConflict.public_projection(self)


class HookContribution(BaseModel):
    """Registry-admitted hook metadata contribution. Raw private config is never projected."""

    model_config = _MODEL_CONFIG

    recipe_ref: StrictStr = Field(alias="recipeRef")
    hook_id: StrictStr = Field(alias="hookId")
    stage: StrictStr
    priority: StrictInt
    scope: tuple[str, ...] = ("all",)
    idempotency_key: StrictStr | None = Field(default=None, alias="idempotencyKey")
    blocking: StrictBool
    failure_mode: Literal["fail_open", "fail_closed"] = Field(alias="failureMode")
    side_effectful: StrictBool = Field(default=False, alias="sideEffectful")
    security_critical: StrictBool = Field(default=False, alias="securityCritical")
    private_config: dict[str, object] = Field(default_factory=dict, alias="privateConfig")
    contribution_digest: StrictStr = Field(alias="contributionDigest")

    @classmethod
    def compute_contribution_digest(cls, data: Mapping[str, object]) -> str:
        return _canonical_digest(cls._canonical_digest_payload(data))

    @staticmethod
    def _from_registry_contribution(
        data: Mapping[str, object] | None = None,
        **values: Any,
    ) -> "HookContribution":
        if data is not None and values:
            raise ValueError("registry hook contribution accepts mapping or keyword values, not both")
        contribution_data = dict(data) if data is not None else values
        contribution = HookContribution(**contribution_data)
        _REGISTRY_ADMITTED_HOOK_DIGESTS.mark(
            contribution,
            contribution.contribution_digest,
            "contribution_digest",
        )
        return contribution

    @classmethod
    def _canonical_digest_payload(cls, data: Mapping[str, object]) -> dict[str, object]:
        normalized = _alias_updates(cls, data)
        return {
            "recipeRef": _recipe_ref(normalized.get("recipe_ref"), "recipe_ref"),
            "hookId": _public_id(normalized.get("hook_id"), "hook_id"),
            "stage": _public_id(normalized.get("stage"), "stage"),
            "priority": _strict_int(normalized.get("priority"), "priority"),
            "scope": _public_id_tuple(normalized.get("scope", ("all",)), "scope"),
            "idempotencyKey": _public_optional_id(
                normalized.get("idempotency_key"),
                "idempotency_key",
            ),
            "blocking": _strict_bool(normalized.get("blocking"), "blocking"),
            "failureMode": _failure_mode(normalized.get("failure_mode")),
            "sideEffectful": _strict_bool(
                normalized.get("side_effectful", False),
                "side_effectful",
            ),
            "securityCritical": _strict_bool(
                normalized.get("security_critical", False),
                "security_critical",
            ),
            "privateConfigDigest": _private_config_digest(
                normalized.get("private_config", {})
            ),
        }

    @field_validator("recipe_ref", mode="before")
    @classmethod
    def _sanitize_recipe_ref(cls, value: object) -> str:
        return _recipe_ref(value, "recipe_ref")

    @field_validator("hook_id", "stage")
    @classmethod
    def _sanitize_public_scalar(cls, value: object, info: Any) -> str:
        return _public_id(value, info.field_name)

    @field_validator("priority", mode="before")
    @classmethod
    def _sanitize_priority(cls, value: object) -> int:
        return _strict_int(value, "priority")

    @field_validator("scope", mode="before")
    @classmethod
    def _sanitize_scope(cls, value: object) -> tuple[str, ...]:
        return _public_id_tuple(value, "scope")

    @field_validator("idempotency_key", mode="before")
    @classmethod
    def _sanitize_idempotency_key(cls, value: object) -> str | None:
        return _public_optional_id(value, "idempotency_key")

    @field_validator("private_config", mode="before")
    @classmethod
    def _sanitize_private_config(cls, value: object) -> dict[str, object]:
        return _json_safe_private_config(value)

    @field_validator("contribution_digest", mode="before")
    @classmethod
    def _sanitize_contribution_digest(cls, value: object) -> str:
        return _safe_digest(value, "contribution_digest")

    @model_validator(mode="after")
    def _validate_contribution_digest(self) -> Self:
        if self.contribution_digest != _canonical_digest(HookContribution._digest_payload(self)):
            raise ValueError("hook contribution digest mismatch")
        if self.blocking and self.failure_mode != "fail_closed":
            raise ValueError("blocking hooks must fail closed")
        return self

    def _assert_registry_admitted(self) -> None:
        if self.contribution_digest != _canonical_digest(HookContribution._digest_payload(self)):
            raise ValueError("hook contribution digest mismatch")
        registered_digest = _REGISTRY_ADMITTED_HOOK_DIGESTS.digest_for(self)
        if registered_digest != _safe_digest(self.contribution_digest, "contribution_digest"):
            raise ValueError("hook contributions require registry-resolved instances")

    def _digest_payload(self) -> dict[str, object]:
        return {
            "recipeRef": _recipe_ref(self.recipe_ref, "recipe_ref"),
            "hookId": _public_id(self.hook_id, "hook_id"),
            "stage": _public_id(self.stage, "stage"),
            "priority": _strict_int(self.priority, "priority"),
            "scope": _public_id_tuple(self.scope, "scope"),
            "idempotencyKey": _public_optional_id(
                self.idempotency_key,
                "idempotency_key",
            ),
            "blocking": _strict_bool(self.blocking, "blocking"),
            "failureMode": _failure_mode(self.failure_mode),
            "sideEffectful": _strict_bool(self.side_effectful, "side_effectful"),
            "securityCritical": _strict_bool(self.security_critical, "security_critical"),
            "privateConfigDigest": _private_config_digest(self.private_config),
        }

    @property
    def private_config_digest(self) -> str:
        return _private_config_digest(self.private_config)

    def public_projection(self) -> dict[str, object]:
        payload = HookContribution._digest_payload(self)
        return {
            "recipeRef": payload["recipeRef"],
            "hookId": payload["hookId"],
            "stage": payload["stage"],
            "priority": payload["priority"],
            "scope": payload["scope"],
            "idempotencyKeyDigest": (
                None
                if self.idempotency_key is None
                else _canonical_digest({"idempotencyKey": self.idempotency_key})
            ),
            "blocking": payload["blocking"],
            "failureMode": payload["failureMode"],
            "sideEffectful": payload["sideEffectful"],
            "securityCritical": payload["securityCritical"],
            "privateConfigDigest": payload["privateConfigDigest"],
            "privateConfigRedacted": True,
            "contributionDigest": _safe_digest(
                self.contribution_digest,
                "contribution_digest",
            ),
        }

    def model_dump(self, *args: Any, **kwargs: Any) -> dict[str, object]:
        return HookContribution.public_projection(self)

    def model_dump_json(self, *args: Any, **kwargs: Any) -> str:
        indent = kwargs.get("indent")
        separators = None if indent is not None else (",", ":")
        return json.dumps(
            HookContribution.public_projection(self),
            indent=indent,
            separators=separators,
            sort_keys=True,
        )

    @model_serializer(mode="plain")
    def _serialize_model(self) -> dict[str, object]:
        return HookContribution.public_projection(self)


class ComposedHook(BaseModel):
    model_config = _MODEL_CONFIG

    hook_id: StrictStr = Field(alias="hookId")
    stage: StrictStr
    priority: StrictInt
    scope: tuple[str, ...] = ("all",)
    idempotency_key: StrictStr | None = Field(default=None, alias="idempotencyKey")
    blocking: StrictBool
    failure_mode: Literal["fail_open", "fail_closed"] = Field(alias="failureMode")
    side_effectful: StrictBool = Field(default=False, alias="sideEffectful")
    security_critical: StrictBool = Field(default=False, alias="securityCritical")
    recipe_refs: tuple[str, ...] = Field(alias="recipeRefs")
    contribution_digests: tuple[str, ...] = Field(alias="contributionDigests")
    private_config_digests: tuple[str, ...] = Field(alias="privateConfigDigests")

    @field_validator("hook_id", "stage")
    @classmethod
    def _sanitize_public_scalar(cls, value: object, info: Any) -> str:
        return _public_id(value, info.field_name)

    @field_validator("scope", mode="before")
    @classmethod
    def _sanitize_scope(cls, value: object) -> tuple[str, ...]:
        return _public_id_tuple(value, "scope")

    @field_validator("idempotency_key", mode="before")
    @classmethod
    def _sanitize_idempotency_key(cls, value: object) -> str | None:
        return _public_optional_id(value, "idempotency_key")

    @field_validator("recipe_refs", mode="before")
    @classmethod
    def _sanitize_recipe_refs(cls, value: object) -> tuple[str, ...]:
        return _recipe_ref_tuple(value, "recipe_refs")

    @field_validator("contribution_digests", "private_config_digests", mode="before")
    @classmethod
    def _sanitize_digests(cls, value: object, info: Any) -> tuple[str, ...]:
        return tuple(_safe_digest(item, info.field_name) for item in _tuple_values(value, info.field_name))

    def public_projection(self) -> dict[str, object]:
        return {
            "hookId": _public_id(self.hook_id, "hook_id"),
            "stage": _public_id(self.stage, "stage"),
            "priority": _strict_int(self.priority, "priority"),
            "scope": _public_id_tuple(self.scope, "scope"),
            "idempotencyKeyDigest": (
                None
                if self.idempotency_key is None
                else _canonical_digest({"idempotencyKey": self.idempotency_key})
            ),
            "blocking": _strict_bool(self.blocking, "blocking"),
            "failureMode": _failure_mode(self.failure_mode),
            "sideEffectful": _strict_bool(self.side_effectful, "side_effectful"),
            "securityCritical": _strict_bool(self.security_critical, "security_critical"),
            "recipeRefs": _recipe_ref_tuple(self.recipe_refs, "recipe_refs"),
            "contributionDigests": tuple(
                _safe_digest(digest, "contribution_digests")
                for digest in self.contribution_digests
            ),
            "privateConfigDigests": tuple(
                _safe_digest(digest, "private_config_digests")
                for digest in self.private_config_digests
            ),
            "privateConfigRedacted": True,
        }

    def model_dump(self, *args: Any, **kwargs: Any) -> dict[str, object]:
        return ComposedHook.public_projection(self)

    def model_dump_json(self, *args: Any, **kwargs: Any) -> str:
        indent = kwargs.get("indent")
        separators = None if indent is not None else (",", ":")
        return json.dumps(
            ComposedHook.public_projection(self),
            indent=indent,
            separators=separators,
            sort_keys=True,
        )

    @model_serializer(mode="plain")
    def _serialize_model(self) -> dict[str, object]:
        return ComposedHook.public_projection(self)


class EffectiveRecipeHookContract(BaseModel):
    model_config = _MODEL_CONFIG

    _validated_composition_digest: str = PrivateAttr(default="")

    schema_version: Literal["recipeHookComposition.v1"] = Field(
        default="recipeHookComposition.v1",
        alias="schemaVersion",
    )
    hooks: tuple[ComposedHook, ...]
    conflicts: tuple[HookCompositionConflict, ...] = ()
    blocked: StrictBool
    default_off: Literal[True] = Field(default=True, alias="defaultOff")
    traffic_attached: Literal[False] = Field(default=False, alias="trafficAttached")
    execution_attached: Literal[False] = Field(default=False, alias="executionAttached")
    live_activation: Literal[False] = Field(default=False, alias="liveActivation")
    composition_digest: StrictStr = Field(alias="compositionDigest")

    @classmethod
    def model_construct(
        cls,
        _fields_set: set[str] | None = None,
        **values: Any,
    ) -> Self:
        return cls.model_validate(cls._canonical_authority_payload(values))

    @staticmethod
    def _canonical_authority_payload(values: Mapping[str, Any]) -> dict[str, Any]:
        values = dict(values)
        for field_name, alias in (
            ("default_off", "defaultOff"),
            ("traffic_attached", "trafficAttached"),
            ("execution_attached", "executionAttached"),
            ("live_activation", "liveActivation"),
        ):
            values.pop(field_name, None)
            values.pop(alias, None)
        values.update({
            "defaultOff": True,
            "trafficAttached": False,
            "executionAttached": False,
            "liveActivation": False,
        })
        return values

    @field_validator("hooks", mode="before")
    @classmethod
    def _sanitize_hooks(cls, value: object) -> tuple[ComposedHook, ...]:
        hooks: list[ComposedHook] = []
        for hook in _tuple_values(value, "hooks"):
            if type(hook) is not ComposedHook:
                raise ValueError("hook composition requires structured hook instances")
            hooks.append(hook)
        return tuple(hooks)

    @field_validator("conflicts", mode="before")
    @classmethod
    def _sanitize_conflicts(cls, value: object) -> tuple[HookCompositionConflict, ...]:
        conflicts: list[HookCompositionConflict] = []
        for conflict in _tuple_values(value, "conflicts"):
            if type(conflict) is not HookCompositionConflict:
                raise ValueError("hook conflicts require structured conflict instances")
            conflicts.append(conflict)
        return tuple(conflicts)

    @field_validator("composition_digest", mode="before")
    @classmethod
    def _sanitize_composition_digest(cls, value: object) -> str:
        return _safe_digest(value, "composition_digest")

    @model_validator(mode="after")
    def _validate_composition_digest(self) -> Self:
        if self.composition_digest != EffectiveRecipeHookContract._compute_composition_digest(self):
            raise ValueError("hook composition digest mismatch")
        return self

    def model_post_init(self, __context: Any) -> None:
        self._validated_composition_digest = EffectiveRecipeHookContract._compute_composition_digest(self)
        _HOOK_CONTRACT_DIGESTS.mark(
            self,
            self._validated_composition_digest,
            "composition_digest",
        )

    def model_copy(
        self,
        *,
        update: Mapping[str, Any] | None = None,
        deep: bool = False,
    ) -> "EffectiveRecipeHookContract":
        EffectiveRecipeHookContract._assert_composition_digest_matches(self)
        if update:
            raise ValueError("hook composition contract authority fields are immutable")
        return EffectiveRecipeHookContract.model_validate(
            {
                "schemaVersion": self.schema_version,
                "hooks": self.hooks,
                "conflicts": self.conflicts,
                "blocked": self.blocked,
                "defaultOff": True,
                "trafficAttached": False,
                "executionAttached": False,
                "liveActivation": False,
                "compositionDigest": self.composition_digest,
            }
        )

    @staticmethod
    def _compute_composition_digest(contract: "EffectiveRecipeHookContract") -> str:
        return _canonical_digest(EffectiveRecipeHookContract._digest_payload(contract))

    @staticmethod
    def _digest_payload(contract: "EffectiveRecipeHookContract") -> dict[str, object]:
        return {
            "schemaVersion": _public_id(contract.schema_version, "schema_version"),
            "hooks": tuple(hook.public_projection() for hook in contract.hooks),
            "conflicts": tuple(
                {
                    "code": _public_id(conflict.code, "code"),
                    "subjectRef": _public_id(conflict.subject_ref, "subject_ref"),
                    "recipeRefs": _recipe_ref_tuple(conflict.recipe_refs, "recipe_refs"),
                    "hookIds": _public_id_tuple(conflict.hook_ids, "hook_ids"),
                    "blocking": bool(conflict.blocking),
                }
                for conflict in contract.conflicts
            ),
            "blocked": bool(contract.blocked),
            "defaultOff": True,
            "trafficAttached": False,
            "executionAttached": False,
            "liveActivation": False,
        }

    @staticmethod
    def _assert_composition_digest_matches(contract: "EffectiveRecipeHookContract") -> None:
        registered_digest = _HOOK_CONTRACT_DIGESTS.digest_for(contract)
        if registered_digest != EffectiveRecipeHookContract._compute_composition_digest(contract):
            raise ValueError("hook composition digest mismatch")

    def public_projection(self) -> dict[str, object]:
        EffectiveRecipeHookContract._assert_composition_digest_matches(self)
        return {
            "schemaVersion": self.schema_version,
            "compositionDigest": _safe_digest(
                self.composition_digest,
                "composition_digest",
            ),
            "hookCount": len(self.hooks),
            "hooks": tuple(hook.public_projection() for hook in self.hooks),
            "blocked": bool(self.blocked),
            "conflictCount": len(self.conflicts),
            "conflicts": tuple(conflict.public_projection() for conflict in self.conflicts),
            "defaultOff": True,
            "trafficAttached": False,
            "executionAttached": False,
            "liveActivation": False,
        }

    def model_dump(self, *args: Any, **kwargs: Any) -> dict[str, object]:
        return EffectiveRecipeHookContract.public_projection(self)

    def model_dump_json(self, *args: Any, **kwargs: Any) -> str:
        indent = kwargs.get("indent")
        separators = None if indent is not None else (",", ":")
        return json.dumps(
            EffectiveRecipeHookContract.public_projection(self),
            indent=indent,
            separators=separators,
            sort_keys=True,
        )

    @model_serializer(mode="plain")
    def _serialize_model(self) -> dict[str, object]:
        return EffectiveRecipeHookContract.public_projection(self)

    @field_serializer("composition_digest")
    def _serialize_composition_digest(self, value: object) -> str:
        EffectiveRecipeHookContract._assert_composition_digest_matches(self)
        return _safe_digest(value, "composition_digest")


def _alias_updates(model_class: type[BaseModel], update: Mapping[str, Any]) -> dict[str, Any]:
    alias_to_name = {
        field.alias: name
        for name, field in model_class.model_fields.items()
        if field.alias is not None
    }
    return {alias_to_name.get(key, key): value for key, value in update.items()}


def _strict_bool(value: object, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{field_name} must be a boolean")
    return value


def _strict_int(value: object, field_name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{field_name} must be an integer")
    return value


def _failure_mode(value: object) -> Literal["fail_open", "fail_closed"]:
    public_value = _public_id(value, "failure_mode")
    if public_value not in {"fail_open", "fail_closed"}:
        raise ValueError("failure_mode must be fail_open or fail_closed")
    return public_value  # type: ignore[return-value]


def _require_contribution(contribution: object) -> HookContribution:
    if type(contribution) is not HookContribution:
        raise ValueError("hook contributions require registry-resolved instances")
    contribution._assert_registry_admitted()
    return contribution


def _sort_key(
    contribution: HookContribution,
) -> tuple[tuple[int, str], int, tuple[str, ...], str, str, str, str]:
    return _contribution_order_key(contribution)


def _stage_rank(stage: str) -> tuple[int, str]:
    return (_STAGE_ORDER.get(stage, 1_000), stage)


def _contribution_order_key(
    contribution: HookContribution,
) -> tuple[tuple[int, str], int, tuple[str, ...], str, str, str, str]:
    return (
        _stage_rank(contribution.stage),
        contribution.priority,
        contribution.scope,
        contribution.idempotency_key or "",
        contribution.recipe_ref,
        contribution.hook_id,
        contribution.contribution_digest,
    )


def _hook_order_key(
    hook: "ComposedHook",
) -> tuple[tuple[int, str], int, tuple[str, ...], str, str, tuple[str, ...]]:
    return (
        _stage_rank(hook.stage),
        hook.priority,
        hook.scope,
        hook.idempotency_key or "",
        hook.hook_id,
        hook.recipe_refs,
    )


def _merge_contributions(contributions: tuple[HookContribution, ...]) -> ComposedHook:
    first = contributions[0]
    return ComposedHook(
        hookId=first.hook_id,
        stage=first.stage,
        priority=first.priority,
        scope=first.scope,
        idempotencyKey=first.idempotency_key,
        blocking=any(contribution.blocking for contribution in contributions),
        failureMode=(
            "fail_closed"
            if any(contribution.failure_mode == "fail_closed" for contribution in contributions)
            else "fail_open"
        ),
        sideEffectful=any(contribution.side_effectful for contribution in contributions),
        securityCritical=any(contribution.security_critical for contribution in contributions),
        recipeRefs=tuple(sorted({contribution.recipe_ref for contribution in contributions})),
        contributionDigests=tuple(
            sorted({contribution.contribution_digest for contribution in contributions})
        ),
        privateConfigDigests=tuple(
            sorted({contribution.private_config_digest for contribution in contributions})
        ),
    )


def _same_hook_key(contribution: HookContribution) -> tuple[str, tuple[str, ...], str]:
    return (
        contribution.stage,
        contribution.scope,
        contribution.hook_id,
    )


def _same_hook_priority_key(
    contribution: HookContribution,
) -> tuple[str, int, tuple[str, ...], str]:
    return (
        contribution.stage,
        contribution.priority,
        contribution.scope,
        contribution.hook_id,
    )


def _idempotency_key(contribution: HookContribution) -> tuple[str, tuple[str, ...], str]:
    if contribution.idempotency_key is None:
        raise ValueError("idempotency key is required")
    return (
        contribution.stage,
        contribution.scope,
        contribution.idempotency_key,
    )


def _idempotency_subject(group_key: tuple[str, tuple[str, ...], str]) -> str:
    stage, scope, idempotency_key = group_key
    scope_digest = _canonical_digest({"scope": scope})[7:23]
    return f"hook.idempotency:{stage}:{scope_digest}:{idempotency_key}"


def _non_idempotent_duplicate_conflict(
    group: Iterable[HookContribution],
) -> HookCompositionConflict:
    contributions = tuple(group)
    return HookCompositionConflict(
        code="non_idempotent_hook_duplicate",
        subjectRef=contributions[0].hook_id,
        recipeRefs=tuple(sorted({item.recipe_ref for item in contributions})),
        hookIds=tuple(sorted({item.hook_id for item in contributions})),
        blocking=True,
    )


def _build_contract(
    hooks: tuple[ComposedHook, ...],
    conflicts: tuple[HookCompositionConflict, ...],
) -> EffectiveRecipeHookContract:
    blocked = any(conflict.blocking for conflict in conflicts)
    payload = {
        "schemaVersion": "recipeHookComposition.v1",
        "hooks": hooks,
        "conflicts": conflicts,
        "blocked": blocked,
        "defaultOff": True,
        "trafficAttached": False,
        "executionAttached": False,
        "liveActivation": False,
    }
    payload["compositionDigest"] = _canonical_digest(
        {
            "schemaVersion": "recipeHookComposition.v1",
            "hooks": tuple(hook.public_projection() for hook in hooks),
            "conflicts": tuple(
                {
                    "code": _public_id(conflict.code, "code"),
                    "subjectRef": _public_id(conflict.subject_ref, "subject_ref"),
                    "recipeRefs": _recipe_ref_tuple(conflict.recipe_refs, "recipe_refs"),
                    "hookIds": _public_id_tuple(conflict.hook_ids, "hook_ids"),
                    "blocking": bool(conflict.blocking),
                }
                for conflict in conflicts
            ),
            "blocked": blocked,
            "defaultOff": True,
            "trafficAttached": False,
            "executionAttached": False,
            "liveActivation": False,
        }
    )
    return EffectiveRecipeHookContract.model_validate(payload)


def compose_hook_contributions(
    contributions: Iterable[HookContribution],
    *,
    disabled_hook_ids: Iterable[str] = (),
) -> EffectiveRecipeHookContract:
    admitted_contributions = tuple(
        sorted(
            (_require_contribution(contribution) for contribution in contributions),
            key=_sort_key,
        )
    )
    disabled_ids = _public_id_tuple(disabled_hook_ids, "disabled_hook_ids")
    disabled_id_set = set(disabled_ids)

    conflicts: list[HookCompositionConflict] = []
    enabled_contributions: list[HookContribution] = []
    for contribution in admitted_contributions:
        if contribution.hook_id not in disabled_id_set:
            enabled_contributions.append(contribution)
            continue
        if contribution.security_critical:
            conflicts.append(
                HookCompositionConflict(
                    code="security_critical_hook_opt_out_rejected",
                    subjectRef=contribution.hook_id,
                    recipeRefs=(contribution.recipe_ref,),
                    hookIds=(contribution.hook_id,),
                    blocking=False,
                )
            )
            enabled_contributions.append(contribution)

    effective_groups: list[tuple[HookContribution, ...]] = []
    by_idempotency_key: dict[tuple[str, tuple[str, ...], str], list[HookContribution]] = {}
    non_idempotent_by_hook: dict[
        tuple[str, tuple[str, ...], str],
        list[HookContribution],
    ] = {}
    same_hook_contributions: dict[
        tuple[str, tuple[str, ...], str],
        list[HookContribution],
    ] = {}
    for contribution in enabled_contributions:
        same_hook_contributions.setdefault(
            _same_hook_key(contribution),
            [],
        ).append(contribution)

    mixed_idempotency_duplicate_keys: set[tuple[str, tuple[str, ...], str]] = set()
    for group_key, group in sorted(same_hook_contributions.items()):
        has_idempotent = any(item.idempotency_key is not None for item in group)
        has_non_idempotent = any(item.idempotency_key is None for item in group)
        if len(group) > 1 and has_idempotent and has_non_idempotent:
            conflicts.append(_non_idempotent_duplicate_conflict(group))
            mixed_idempotency_duplicate_keys.add(group_key)

    for contribution in enabled_contributions:
        if _same_hook_key(contribution) in mixed_idempotency_duplicate_keys:
            continue
        if contribution.idempotency_key is not None:
            by_idempotency_key.setdefault(_idempotency_key(contribution), []).append(contribution)
        else:
            non_idempotent_by_hook.setdefault(_same_hook_key(contribution), []).append(contribution)

    for group_key, group in sorted(by_idempotency_key.items()):
        hook_ids = tuple(sorted({item.hook_id for item in group}))
        if len(hook_ids) > 1:
            conflicts.append(
                HookCompositionConflict(
                    code="idempotency_key_hook_collision",
                    subjectRef=_idempotency_subject(group_key),
                    recipeRefs=tuple(sorted({item.recipe_ref for item in group})),
                    hookIds=hook_ids,
                    blocking=True,
                )
            )
            continue
        effective_groups.append(tuple(group))

    for _key, group in sorted(non_idempotent_by_hook.items()):
        if len(group) > 1:
            conflicts.append(_non_idempotent_duplicate_conflict(group))
            continue
        grouped_by_priority: dict[
            tuple[str, int, tuple[str, ...], str],
            list[HookContribution],
        ] = {}
        for contribution in group:
            grouped_by_priority.setdefault(
                _same_hook_priority_key(contribution),
                [],
            ).append(contribution)
        effective_groups.extend(tuple(items) for _key, items in sorted(grouped_by_priority.items()))

    hooks = tuple(
        sorted(
            (_merge_contributions(group) for group in effective_groups),
            key=_hook_order_key,
        )
    )
    sorted_conflicts = tuple(
        sorted(
            conflicts,
            key=lambda conflict: (
                not conflict.blocking,
                conflict.code,
                conflict.subject_ref,
                conflict.recipe_refs,
                conflict.hook_ids,
            ),
        )
    )
    return _build_contract(hooks, sorted_conflicts)


__all__ = [
    "ComposedHook",
    "EffectiveRecipeHookContract",
    "HookCompositionConflict",
    "HookContribution",
    "compose_hook_contributions",
]
