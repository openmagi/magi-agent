from __future__ import annotations

from collections.abc import Iterable, Mapping, Set
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
    SkipValidation,
    StrictBool,
    StrictStr,
    field_serializer,
    field_validator,
    model_serializer,
    model_validator,
)

from magi_agent.ops.authority import FalseOnlyAuthorityModel


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


_STACK_INPUT_DIGESTS = _IdentityDigestRegistry()
_REGISTRY_ADMITTED_SNAPSHOT_DIGESTS = _IdentityDigestRegistry()
_ADMISSION_RESULT_DIGESTS = _IdentityDigestRegistry()


def _alias_updates(model_class: type[BaseModel], update: Mapping[str, Any]) -> dict[str, Any]:
    alias_to_name = {
        field.alias: name
        for name, field in model_class.model_fields.items()
        if field.alias is not None
    }
    return {alias_to_name.get(key, key): value for key, value in update.items()}


def _as_ref_tuple(value: object) -> tuple[object, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, Set) and not isinstance(value, (bytes, bytearray, Mapping)):
        try:
            refs = tuple(value)
        except Exception:
            raise ValueError("unsafe recipe ref container") from None
        if all(isinstance(ref, str) for ref in refs):
            try:
                return tuple(sorted(refs))
            except Exception:
                raise ValueError("unsafe recipe ref container") from None
        return refs
    if isinstance(value, Iterable) and not isinstance(value, (bytes, bytearray, Mapping)):
        try:
            return tuple(value)
        except Exception:
            raise ValueError("unsafe recipe ref container") from None
    raise ValueError("recipe ref sections must be strings or iterables of strings")


def _contains_unsafe_text(value: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]", "", value.strip().lower())
    return (
        _PRIVATE_PATH_RE.search(value) is not None
        or _UNSAFE_SECRET_SHAPED_RE.search(value) is not None
        or any(fragment in normalized for fragment in _UNSAFE_TEXT_FRAGMENTS)
    )


def _sanitize_recipe_refs(value: object) -> tuple[str, ...]:
    refs: list[str] = []
    seen: set[str] = set()
    for raw_ref in _as_ref_tuple(value):
        if not isinstance(raw_ref, str):
            raise ValueError("recipe refs must be strings")
        try:
            ref = raw_ref.strip()
            if ref in seen:
                continue
            unsafe_ref = _contains_unsafe_text(ref) or _SAFE_RECIPE_REF_RE.fullmatch(ref) is None
        except Exception:
            raise ValueError("unsafe recipe ref") from None
        if unsafe_ref:
            raise ValueError("unsafe recipe ref")
        seen.add(ref)
        refs.append(ref)
    return tuple(refs)


def _is_false_like(value: object) -> bool:
    if value is False:
        return True
    if isinstance(value, int | float) and not isinstance(value, bool):
        return value == 0
    if isinstance(value, bytes):
        try:
            value = value.decode("ascii")
        except UnicodeDecodeError:
            return False
    if isinstance(value, str):
        return value.strip().lower() in {"false", "f", "0", "no", "n", "off"}
    return False


def _public_context_value(value: object, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a public identifier")
    normalized = value.strip()
    if not normalized:
        return None
    if _contains_unsafe_text(normalized) or _PUBLIC_ID_RE.fullmatch(normalized) is None:
        raise ValueError(f"{field_name} must be a public identifier")
    return normalized


def _public_auto_flag(value: object, field_name: str) -> bool:
    if isinstance(value, bool):
        return value
    if _is_false_like(value):
        return False
    raise ValueError(f"{field_name} must be a boolean")


def _strict_public_bool(value: object, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{field_name} must be a boolean")
    return value


def _public_required_value(value: object, field_name: str) -> str:
    normalized = _public_context_value(value, field_name)
    if normalized is None:
        raise ValueError(f"{field_name} must be a public identifier")
    return normalized


def _sanitize_single_recipe_ref(value: object) -> str:
    refs = _sanitize_recipe_refs(value)
    if len(refs) != 1:
        raise ValueError("exactly one recipe ref is required")
    return refs[0]


def _sanitize_public_ref_tuple(value: object, field_name: str) -> tuple[str, ...]:
    refs: list[str] = []
    seen: set[str] = set()
    try:
        raw_values = _as_ref_tuple(value)
    except Exception:
        raise ValueError(f"{field_name} must be public identifiers") from None
    for raw_value in raw_values:
        if not isinstance(raw_value, str):
            raise ValueError(f"{field_name} must be public identifiers")
        public_value = _public_required_value(raw_value, field_name)
        if public_value not in seen:
            seen.add(public_value)
            refs.append(public_value)
    return tuple(refs)


def _safe_digest(value: object, field_name: str = "digest") -> str:
    if not isinstance(value, str) or _SHA256_DIGEST_RE.fullmatch(value) is None:
        raise ValueError(f"{field_name} must be a sha256 digest")
    return value.lower()


def _dedupe_preserving_order(values: Iterable[str]) -> tuple[str, ...]:
    deduped: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value not in seen:
            seen.add(value)
            deduped.append(value)
    return tuple(deduped)


def _mark_registry_admitted(snapshot: object, snapshot_digest: object) -> None:
    _REGISTRY_ADMITTED_SNAPSHOT_DIGESTS.mark(
        snapshot,
        snapshot_digest,
        "snapshot_digest",
    )


class RecipeStackInput(FalseOnlyAuthorityModel):
    """Sanitized, local-only recipe stack input; refs remain untrusted until admission.

    C-4 PR-G3: re-parented onto ``FalseOnlyAuthorityModel``. The kernel's
    introspection-based ``_force_false`` validator + ``_ser`` serializer +
    ``model_construct`` route-through-validate cover the ``trusted`` /
    ``admitted`` ``Literal[False]`` fields uniformly. The previous custom
    ``model_construct`` / ``model_copy`` were ``cls(**values)`` /
    dump-and-revalidate -- functionally equivalent to the kernel's defaults
    given populate_by_name + extra=forbid + frozen=True. The 2-field
    ``@field_serializer("trusted", "admitted")`` is dropped (kernel covers it).
    PRESERVED: the ``default_off`` ``@field_serializer`` (Literal[True], not in
    scope for the kernel), the auto-refs serializer, the public-context
    serializers, ``_normalize_disabled_auto_refs`` ``@model_validator(before)``
    (semantic guard), ``revalidate_instances="always"`` (the only kernel
    config drift -- preserved so ``model_validate(existing_instance)`` still
    re-runs validation after a frozen-bypass ``__dict__`` mutation, per
    ``tests/test_recipe_composition_stack.py::
    test_model_validate_revalidates_existing_instances``).
    """

    model_config = ConfigDict(
        frozen=True,
        populate_by_name=True,
        extra="forbid",
        validate_default=True,
        revalidate_instances="always",
        hide_input_in_errors=True,
    )

    explicit_recipe_refs: tuple[str, ...] = Field(default=(), alias="explicitRecipeRefs")
    auto_recipe_refs: tuple[str, ...] = Field(default=(), alias="autoRecipeRefs")
    default_recipe_refs: tuple[str, ...] = Field(default=(), alias="defaultRecipeRefs")
    plugin_recipe_refs: tuple[str, ...] = Field(default=(), alias="pluginRecipeRefs")
    hard_safety_refs: tuple[str, ...] = Field(default=(), alias="hardSafetyRefs")
    allow_additional_auto_recipes: StrictBool = Field(default=False, alias="allowAdditionalAutoRecipes")
    selection_source: StrictStr | None = Field(default=None, alias="selectionSource")
    turn_id: StrictStr | None = Field(default=None, alias="turnId")
    session_id: StrictStr | None = Field(default=None, alias="sessionId")
    default_off: Literal[True] = Field(default=True, alias="defaultOff")
    trusted: Literal[False] = Field(default=False, alias="trusted")
    admitted: Literal[False] = Field(default=False, alias="admitted")

    @field_serializer("default_off")
    def _serialize_default_off(self, value: object) -> bool:
        return True

    @field_serializer("allow_additional_auto_recipes")
    def _serialize_allow_additional_auto_recipes(self, value: object) -> bool:
        return _public_auto_flag(value, "allow_additional_auto_recipes")

    @field_serializer(
        "explicit_recipe_refs",
        "default_recipe_refs",
        "plugin_recipe_refs",
        "hard_safety_refs",
    )
    def _serialize_ref_section(self, value: object) -> tuple[str, ...]:
        return _sanitize_recipe_refs(value)

    @field_serializer("auto_recipe_refs")
    def _serialize_auto_ref_section(self, value: object) -> tuple[str, ...]:
        return RecipeStackInput._safe_auto_refs(self, value)

    @field_serializer("selection_source", "turn_id", "session_id")
    def _serialize_public_context(self, value: object, info: Any) -> str | None:
        return _public_context_value(value, info.field_name)

    def model_post_init(self, __context: Any) -> None:
        _STACK_INPUT_DIGESTS.mark(
            self,
            RecipeStackInput.stack_digest(self),
            "stack_digest",
        )

    @model_validator(mode="before")
    @classmethod
    def _normalize_disabled_auto_refs(cls, value: object) -> object:
        if not isinstance(value, Mapping):
            return value
        data = dict(value)
        allow_auto = data.get("allowAdditionalAutoRecipes", data.get("allow_additional_auto_recipes", False))
        if _is_false_like(allow_auto):
            if "allowAdditionalAutoRecipes" in data:
                data["allowAdditionalAutoRecipes"] = False
            if "allow_additional_auto_recipes" in data:
                data["allow_additional_auto_recipes"] = False
            if "autoRecipeRefs" in data:
                _sanitize_recipe_refs(data["autoRecipeRefs"])
                data["autoRecipeRefs"] = ()
            if "auto_recipe_refs" in data:
                _sanitize_recipe_refs(data["auto_recipe_refs"])
                data["auto_recipe_refs"] = ()
        return data

    @field_validator(
        "explicit_recipe_refs",
        "auto_recipe_refs",
        "default_recipe_refs",
        "plugin_recipe_refs",
        "hard_safety_refs",
        mode="before",
    )
    @classmethod
    def _sanitize_ref_section(cls, value: object) -> tuple[str, ...]:
        return _sanitize_recipe_refs(value)

    @field_validator("selection_source", "turn_id", "session_id")
    @classmethod
    def _sanitize_public_context(cls, value: str | None, info: Any) -> str | None:
        return _public_context_value(value, info.field_name)

    def _safe_ref_sections(self) -> dict[str, tuple[str, ...]]:
        return {
            "explicitRecipeRefs": _sanitize_recipe_refs(self.explicit_recipe_refs),
            "autoRecipeRefs": RecipeStackInput._safe_auto_refs(
                self,
                self.auto_recipe_refs,
            ),
            "defaultRecipeRefs": _sanitize_recipe_refs(self.default_recipe_refs),
            "pluginRecipeRefs": _sanitize_recipe_refs(self.plugin_recipe_refs),
            "hardSafetyRefs": _sanitize_recipe_refs(self.hard_safety_refs),
        }

    def _safe_context_fields(self) -> dict[str, str | None]:
        return {
            "selectionSource": _public_context_value(self.selection_source, "selection_source"),
            "turnId": _public_context_value(self.turn_id, "turn_id"),
            "sessionId": _public_context_value(self.session_id, "session_id"),
        }

    def _safe_auto_flag(self) -> bool:
        return _public_auto_flag(
            self.allow_additional_auto_recipes,
            "allow_additional_auto_recipes",
        )

    def _safe_auto_refs(self, value: object) -> tuple[str, ...]:
        refs = _sanitize_recipe_refs(value)
        if not RecipeStackInput._safe_auto_flag(self):
            return ()
        return refs

    def all_recipe_refs(self) -> tuple[str, ...]:
        sections = RecipeStackInput._safe_ref_sections(self)
        return (
            *sections["explicitRecipeRefs"],
            *sections["autoRecipeRefs"],
            *sections["defaultRecipeRefs"],
            *sections["pluginRecipeRefs"],
            *sections["hardSafetyRefs"],
        )

    def stack_digest(self) -> str:
        sections = RecipeStackInput._safe_ref_sections(self)
        context = RecipeStackInput._safe_context_fields(self)
        payload = {
            **sections,
            "allowAdditionalAutoRecipes": RecipeStackInput._safe_auto_flag(self),
            **context,
            "defaultOff": True,
            "trusted": False,
            "admitted": False,
            "refsTrustState": "untrusted_until_admission",
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        return f"sha256:{hashlib.sha256(encoded).hexdigest()}"

    def _assert_stack_input_digest_matches(self) -> None:
        if _STACK_INPUT_DIGESTS.digest_for(self) != RecipeStackInput.stack_digest(self):
            raise ValueError("recipe stack input digest mismatch")

    def public_projection(self) -> dict[str, object]:
        sections = RecipeStackInput._safe_ref_sections(self)
        context = RecipeStackInput._safe_context_fields(self)
        return {
            **sections,
            "allowAdditionalAutoRecipes": RecipeStackInput._safe_auto_flag(self),
            **context,
            "defaultOff": True,
            "trusted": False,
            "admitted": False,
            "refsTrustState": "untrusted_until_admission",
            "stackDigest": RecipeStackInput.stack_digest(self),
        }


class AdmittedRecipeSnapshot(BaseModel):
    """Immutable admitted recipe declaration snapshot from a trusted registry boundary."""

    model_config = _MODEL_CONFIG

    recipe_ref: StrictStr = Field(alias="recipeRef")
    snapshot_digest: StrictStr = Field(alias="snapshotDigest")
    version: StrictStr
    source: StrictStr
    governed: StrictBool
    hard_safety: StrictBool = Field(alias="hardSafety")
    tool_grants: tuple[str, ...] = Field(default=(), alias="toolGrants")
    tool_denials: tuple[str, ...] = Field(default=(), alias="toolDenials")
    evidence_requirements: tuple[str, ...] = Field(default=(), alias="evidenceRequirements")
    approval_requirements: tuple[str, ...] = Field(default=(), alias="approvalRequirements")
    context_requirements: tuple[str, ...] = Field(default=(), alias="contextRequirements")
    hook_contributions: tuple[str, ...] = Field(default=(), alias="hookContributions")
    retry_policy: StrictStr = Field(default="none", alias="retryPolicy")
    projection_rules: tuple[str, ...] = Field(default=(), alias="projectionRules")

    @classmethod
    def compute_snapshot_digest(cls, data: Mapping[str, object]) -> str:
        payload = cls._canonical_digest_payload(data)
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        return f"sha256:{hashlib.sha256(encoded).hexdigest()}"

    @staticmethod
    def _from_registry_snapshot(
        data: Mapping[str, object] | None = None,
        **values: Any,
    ) -> "AdmittedRecipeSnapshot":
        """Build a locally registry-admitted snapshot from sanitized registry data."""

        if data is not None and values:
            raise ValueError("registry snapshot accepts mapping or keyword values, not both")
        snapshot_data = dict(data) if data is not None else values
        snapshot = AdmittedRecipeSnapshot(**snapshot_data)
        _mark_registry_admitted(snapshot, snapshot.snapshot_digest)
        return snapshot

    @classmethod
    def _canonical_digest_payload(cls, data: Mapping[str, object]) -> dict[str, object]:
        normalized = _alias_updates(cls, data)
        return {
            "recipeRef": _sanitize_single_recipe_ref(normalized.get("recipe_ref")),
            "version": _public_required_value(normalized.get("version"), "version"),
            "source": _public_required_value(normalized.get("source"), "source"),
            "governed": _strict_public_bool(normalized.get("governed"), "governed"),
            "hardSafety": _strict_public_bool(normalized.get("hard_safety"), "hard_safety"),
            "toolGrants": _sanitize_public_ref_tuple(
                normalized.get("tool_grants", ()),
                "tool_grants",
            ),
            "toolDenials": _sanitize_public_ref_tuple(
                normalized.get("tool_denials", ()),
                "tool_denials",
            ),
            "evidenceRequirements": _sanitize_public_ref_tuple(
                normalized.get("evidence_requirements", ()),
                "evidence_requirements",
            ),
            "approvalRequirements": _sanitize_public_ref_tuple(
                normalized.get("approval_requirements", ()),
                "approval_requirements",
            ),
            "contextRequirements": _sanitize_public_ref_tuple(
                normalized.get("context_requirements", ()),
                "context_requirements",
            ),
            "hookContributions": _sanitize_public_ref_tuple(
                normalized.get("hook_contributions", ()),
                "hook_contributions",
            ),
            "retryPolicy": _public_required_value(
                normalized.get("retry_policy", "none"),
                "retry_policy",
            ),
            "projectionRules": _sanitize_public_ref_tuple(
                normalized.get("projection_rules", ()),
                "projection_rules",
            ),
        }

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
    ) -> "AdmittedRecipeSnapshot":
        preserve_registry_admission = (
            not update
            and _REGISTRY_ADMITTED_SNAPSHOT_DIGESTS.digest_for(self)
            == _safe_digest(self.snapshot_digest, "snapshot_digest")
        )
        data: dict[str, object] = {
            "recipe_ref": self.recipe_ref,
            "snapshot_digest": self.snapshot_digest,
            "version": self.version,
            "source": self.source,
            "governed": self.governed,
            "hard_safety": self.hard_safety,
            "tool_grants": self.tool_grants,
            "tool_denials": self.tool_denials,
            "evidence_requirements": self.evidence_requirements,
            "approval_requirements": self.approval_requirements,
            "context_requirements": self.context_requirements,
            "hook_contributions": self.hook_contributions,
            "retry_policy": self.retry_policy,
            "projection_rules": self.projection_rules,
        }
        if update:
            data.update(_alias_updates(AdmittedRecipeSnapshot, update))
        snapshot = AdmittedRecipeSnapshot.model_validate(data)
        if preserve_registry_admission:
            _mark_registry_admitted(snapshot, snapshot.snapshot_digest)
        return snapshot

    @field_validator("recipe_ref", mode="before")
    @classmethod
    def _sanitize_snapshot_recipe_ref(cls, value: object) -> str:
        return _sanitize_single_recipe_ref(value)

    @field_validator("snapshot_digest", mode="before")
    @classmethod
    def _sanitize_snapshot_digest(cls, value: object) -> str:
        return _safe_digest(value, "snapshot_digest")

    @field_validator("version", "source", "retry_policy")
    @classmethod
    def _sanitize_public_scalar(cls, value: str, info: Any) -> str:
        public_value = _public_required_value(value, info.field_name)
        if info.field_name == "source" and public_value == "client":
            raise ValueError("admitted recipe source cannot be client")
        return public_value

    @field_validator(
        "tool_grants",
        "tool_denials",
        "evidence_requirements",
        "approval_requirements",
        "context_requirements",
        "hook_contributions",
        "projection_rules",
        mode="before",
    )
    @classmethod
    def _sanitize_public_tuple_section(cls, value: object, info: Any) -> tuple[str, ...]:
        return _sanitize_public_ref_tuple(value, info.field_name)

    def model_dump(self, *args: Any, **kwargs: Any) -> dict[str, object]:
        return AdmittedRecipeSnapshot.public_projection(self)

    def model_dump_json(self, *args: Any, **kwargs: Any) -> str:
        indent = kwargs.get("indent")
        separators = None if indent is not None else (",", ":")
        return json.dumps(
            AdmittedRecipeSnapshot.public_projection(self),
            indent=indent,
            separators=separators,
            sort_keys=True,
        )

    @model_serializer(mode="plain")
    def _serialize_snapshot_model(self) -> dict[str, object]:
        return AdmittedRecipeSnapshot.public_projection(self)

    @field_serializer("recipe_ref")
    def _serialize_snapshot_recipe_ref(self, value: object) -> str:
        return _sanitize_single_recipe_ref(value)

    @field_serializer("snapshot_digest")
    def _serialize_snapshot_digest(self, value: object) -> str:
        AdmittedRecipeSnapshot._assert_digest_matches(self)
        return _safe_digest(value, "snapshot_digest")

    @field_serializer("version", "source", "retry_policy")
    def _serialize_public_scalar(self, value: object, info: Any) -> str:
        return _public_required_value(value, info.field_name)

    @field_serializer("governed", "hard_safety")
    def _serialize_public_bool(self, value: object, info: Any) -> bool:
        return _strict_public_bool(value, info.field_name)

    @field_serializer(
        "tool_grants",
        "tool_denials",
        "evidence_requirements",
        "approval_requirements",
        "context_requirements",
        "hook_contributions",
        "projection_rules",
    )
    def _serialize_public_tuple_section(self, value: object, info: Any) -> tuple[str, ...]:
        return _sanitize_public_ref_tuple(value, info.field_name)

    @model_validator(mode="after")
    def _validate_snapshot_digest(self) -> Self:
        AdmittedRecipeSnapshot._assert_digest_matches(self)
        return self

    def _assert_digest_matches(self) -> None:
        expected = AdmittedRecipeSnapshot.compute_snapshot_digest(
            AdmittedRecipeSnapshot._digest_payload(self)
        )
        if _safe_digest(self.snapshot_digest, "snapshot_digest") != expected:
            raise ValueError("snapshot digest mismatch")

    def _assert_registry_admitted(self) -> None:
        AdmittedRecipeSnapshot._assert_digest_matches(self)
        registered_digest = _REGISTRY_ADMITTED_SNAPSHOT_DIGESTS.digest_for(self)
        if registered_digest != _safe_digest(self.snapshot_digest, "snapshot_digest"):
            raise ValueError("admitted snapshots require registry-resolved instances")

    def _digest_payload(self) -> dict[str, object]:
        return {
            "recipeRef": _sanitize_single_recipe_ref(self.recipe_ref),
            "version": _public_required_value(self.version, "version"),
            "source": _public_required_value(self.source, "source"),
            "governed": _strict_public_bool(self.governed, "governed"),
            "hardSafety": _strict_public_bool(self.hard_safety, "hard_safety"),
            "toolGrants": _sanitize_public_ref_tuple(self.tool_grants, "tool_grants"),
            "toolDenials": _sanitize_public_ref_tuple(self.tool_denials, "tool_denials"),
            "evidenceRequirements": _sanitize_public_ref_tuple(
                self.evidence_requirements,
                "evidence_requirements",
            ),
            "approvalRequirements": _sanitize_public_ref_tuple(
                self.approval_requirements,
                "approval_requirements",
            ),
            "contextRequirements": _sanitize_public_ref_tuple(
                self.context_requirements,
                "context_requirements",
            ),
            "hookContributions": _sanitize_public_ref_tuple(
                self.hook_contributions,
                "hook_contributions",
            ),
            "retryPolicy": _public_required_value(self.retry_policy, "retry_policy"),
            "projectionRules": _sanitize_public_ref_tuple(
                self.projection_rules,
                "projection_rules",
            ),
        }

    def public_projection(self) -> dict[str, object]:
        AdmittedRecipeSnapshot._assert_digest_matches(self)
        payload = AdmittedRecipeSnapshot._digest_payload(self)
        return {
            "recipeRef": payload["recipeRef"],
            "snapshotDigest": _safe_digest(self.snapshot_digest, "snapshot_digest"),
            "version": payload["version"],
            "source": payload["source"],
            "governed": payload["governed"],
            "hardSafety": payload["hardSafety"],
            "toolGrantCount": len(payload["toolGrants"]),
            "toolDenialCount": len(payload["toolDenials"]),
            "evidenceRequirementCount": len(payload["evidenceRequirements"]),
            "approvalRequirementCount": len(payload["approvalRequirements"]),
            "contextRequirementCount": len(payload["contextRequirements"]),
            "hookContributionCount": len(payload["hookContributions"]),
            "retryPolicy": payload["retryPolicy"],
            "projectionRuleCount": len(payload["projectionRules"]),
        }


class RecipeAdmissionConflict(BaseModel):
    model_config = _MODEL_CONFIG

    code: StrictStr
    recipe_ref: StrictStr = Field(alias="recipeRef")

    @field_validator("code")
    @classmethod
    def _sanitize_conflict_code(cls, value: str) -> str:
        return _public_required_value(value, "code")

    @field_validator("recipe_ref", mode="before")
    @classmethod
    def _sanitize_conflict_recipe_ref(cls, value: object) -> str:
        return _sanitize_single_recipe_ref(value)

    def public_projection(self) -> dict[str, str]:
        return {
            "code": _public_required_value(self.code, "code"),
            "recipeRef": _sanitize_single_recipe_ref(self.recipe_ref),
        }


class RecipeAdmissionRequest(BaseModel):
    model_config = _MODEL_CONFIG

    stack: RecipeStackInput
    admitted_snapshots: tuple[SkipValidation[AdmittedRecipeSnapshot], ...] = Field(
        default=(),
        alias="admittedSnapshots",
    )
    required_governed_recipe_refs: tuple[str, ...] = Field(
        default=(),
        alias="requiredGovernedRecipeRefs",
    )

    def model_dump(self, *args: Any, **kwargs: Any) -> dict[str, object]:
        return RecipeAdmissionRequest.public_projection(self)

    def model_dump_json(self, *args: Any, **kwargs: Any) -> str:
        indent = kwargs.get("indent")
        separators = None if indent is not None else (",", ":")
        return json.dumps(
            RecipeAdmissionRequest.public_projection(self),
            indent=indent,
            separators=separators,
            sort_keys=True,
        )

    @model_serializer(mode="plain")
    def _serialize_request_model(self) -> dict[str, object]:
        return RecipeAdmissionRequest.public_projection(self)

    @field_validator("stack", mode="before")
    @classmethod
    def _revalidate_stack(cls, value: object) -> RecipeStackInput:
        if type(value) is RecipeStackInput:
            RecipeStackInput._assert_stack_input_digest_matches(value)
        elif isinstance(value, RecipeStackInput):
            raise ValueError("admission request stack requires RecipeStackInput")
        return RecipeStackInput.model_validate(value)

    @field_validator("admitted_snapshots", mode="before")
    @classmethod
    def _revalidate_snapshots(cls, value: object) -> tuple[AdmittedRecipeSnapshot, ...]:
        if value is None:
            return ()
        if type(value) is AdmittedRecipeSnapshot:
            AdmittedRecipeSnapshot._assert_registry_admitted(value)
            return (value,)
        if isinstance(value, Iterable) and not isinstance(value, (str, bytes, bytearray, Mapping)):
            snapshots: list[AdmittedRecipeSnapshot] = []
            for snapshot in value:
                if type(snapshot) is not AdmittedRecipeSnapshot:
                    raise ValueError("admitted snapshots require registry-resolved instances")
                AdmittedRecipeSnapshot._assert_registry_admitted(snapshot)
                snapshots.append(snapshot)
            return tuple(snapshots)
        raise ValueError("admitted snapshots must be iterable")

    @field_validator("required_governed_recipe_refs", mode="before")
    @classmethod
    def _sanitize_required_governed_refs(cls, value: object) -> tuple[str, ...]:
        return _sanitize_recipe_refs(value)

    @field_serializer("stack")
    def _serialize_request_stack(self, value: object) -> dict[str, object]:
        return RecipeAdmissionRequest._safe_stack_projection(value)

    @field_serializer("admitted_snapshots")
    def _serialize_request_snapshots(self, value: object) -> tuple[dict[str, object], ...]:
        return RecipeAdmissionRequest._safe_snapshot_projections(value)

    @field_serializer("required_governed_recipe_refs")
    def _serialize_required_governed_refs(self, value: object) -> tuple[str, ...]:
        return _sanitize_recipe_refs(value)

    @staticmethod
    def _safe_stack_projection(value: object) -> dict[str, object]:
        if type(value) is not RecipeStackInput:
            raise ValueError("admission request stack requires RecipeStackInput")
        RecipeStackInput._assert_stack_input_digest_matches(value)
        return RecipeStackInput.public_projection(value)

    @staticmethod
    def _safe_snapshot_projections(value: object) -> tuple[dict[str, object], ...]:
        if not isinstance(value, Iterable) or isinstance(value, (str, bytes, bytearray, Mapping)):
            raise ValueError("admitted snapshots must be iterable")
        projections: list[dict[str, object]] = []
        for snapshot in value:
            if type(snapshot) is not AdmittedRecipeSnapshot:
                raise ValueError("admitted snapshots require registry-resolved instances")
            AdmittedRecipeSnapshot._assert_registry_admitted(snapshot)
            projections.append(AdmittedRecipeSnapshot.public_projection(snapshot))
        return tuple(projections)

    def public_projection(self) -> dict[str, object]:
        return {
            "stack": RecipeAdmissionRequest._safe_stack_projection(self.stack),
            "admittedSnapshots": RecipeAdmissionRequest._safe_snapshot_projections(
                self.admitted_snapshots
            ),
            "requiredGovernedRecipeRefs": _sanitize_recipe_refs(
                self.required_governed_recipe_refs
            ),
        }


def _revalidate_admission_request(request: object) -> RecipeAdmissionRequest:
    if not isinstance(request, RecipeAdmissionRequest):
        return RecipeAdmissionRequest.model_validate(request)
    if type(request.stack) is not RecipeStackInput:
        raise ValueError("admission request stack requires RecipeStackInput")
    RecipeStackInput._assert_stack_input_digest_matches(request.stack)
    return RecipeAdmissionRequest(
        stack=RecipeStackInput.model_copy(request.stack),
        admittedSnapshots=tuple(request.admitted_snapshots),
        requiredGovernedRecipeRefs=request.required_governed_recipe_refs,
    )


class RecipeAdmissionResult(BaseModel):
    model_config = _MODEL_CONFIG

    _validated_stack_digest: str = PrivateAttr(default="")
    _validated_result_digest: str = PrivateAttr(default="")

    stack_digest: StrictStr = Field(alias="stackDigest")
    admitted_snapshots: tuple[SkipValidation[AdmittedRecipeSnapshot], ...] = Field(alias="admittedSnapshots")
    admitted_recipe_refs: tuple[str, ...] = Field(alias="admittedRecipeRefs")
    missing_explicit_refs: tuple[str, ...] = Field(default=(), alias="missingExplicitRefs")
    missing_required_refs: tuple[str, ...] = Field(default=(), alias="missingRequiredRefs")
    rejected_auto_refs: tuple[str, ...] = Field(default=(), alias="rejectedAutoRefs")
    required_governed_recipe_refs: tuple[str, ...] = Field(
        default=(),
        alias="requiredGovernedRecipeRefs",
    )
    conflicts: tuple[RecipeAdmissionConflict, ...] = Field(default=())
    blocked: StrictBool

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
    ) -> "RecipeAdmissionResult":
        RecipeAdmissionResult._assert_result_digest_matches(self)
        if update:
            raise ValueError("admission result authority fields are immutable")
        data: dict[str, object] = {
            "stack_digest": self.stack_digest,
            "admitted_snapshots": self.admitted_snapshots,
            "admitted_recipe_refs": self.admitted_recipe_refs,
            "missing_explicit_refs": self.missing_explicit_refs,
            "missing_required_refs": self.missing_required_refs,
            "rejected_auto_refs": self.rejected_auto_refs,
            "required_governed_recipe_refs": self.required_governed_recipe_refs,
            "conflicts": self.conflicts,
            "blocked": self.blocked,
        }
        return RecipeAdmissionResult.model_validate(data)

    def model_post_init(self, __context: Any) -> None:
        self._validated_stack_digest = _safe_digest(self.stack_digest, "stack_digest")
        self._validated_result_digest = RecipeAdmissionResult._result_digest(self)
        _ADMISSION_RESULT_DIGESTS.mark(
            self,
            self._validated_result_digest,
            "admission_result_digest",
        )

    @field_validator("stack_digest", mode="before")
    @classmethod
    def _sanitize_result_stack_digest(cls, value: object) -> str:
        return _safe_digest(value, "stack_digest")

    @field_validator(
        "admitted_recipe_refs",
        "missing_explicit_refs",
        "missing_required_refs",
        "rejected_auto_refs",
        "required_governed_recipe_refs",
        mode="before",
    )
    @classmethod
    def _sanitize_result_ref_section(cls, value: object) -> tuple[str, ...]:
        return _sanitize_recipe_refs(value)

    @field_validator("admitted_snapshots", mode="before")
    @classmethod
    def _revalidate_result_snapshots(cls, value: object) -> tuple[AdmittedRecipeSnapshot, ...]:
        if value is None:
            return ()
        if type(value) is AdmittedRecipeSnapshot:
            AdmittedRecipeSnapshot._assert_registry_admitted(value)
            return (value,)
        if isinstance(value, Iterable) and not isinstance(value, (str, bytes, bytearray, Mapping)):
            snapshots: list[AdmittedRecipeSnapshot] = []
            for snapshot in value:
                if type(snapshot) is not AdmittedRecipeSnapshot:
                    raise ValueError("admitted snapshots require registry-resolved instances")
                AdmittedRecipeSnapshot._assert_registry_admitted(snapshot)
                snapshots.append(snapshot)
            return tuple(snapshots)
        raise ValueError("admitted snapshots must be iterable")

    @field_validator("conflicts", mode="before")
    @classmethod
    def _revalidate_result_conflicts(cls, value: object) -> tuple[RecipeAdmissionConflict, ...]:
        if value is None:
            return ()
        if isinstance(value, RecipeAdmissionConflict):
            return (RecipeAdmissionConflict.model_validate(value),)
        if isinstance(value, Iterable) and not isinstance(value, (str, bytes, bytearray, Mapping)):
            conflicts: list[RecipeAdmissionConflict] = []
            for conflict in value:
                if not isinstance(conflict, RecipeAdmissionConflict):
                    raise ValueError("conflicts require structured conflict instances")
                conflicts.append(RecipeAdmissionConflict.model_validate(conflict))
            return tuple(conflicts)
        raise ValueError("conflicts must be iterable")

    @model_validator(mode="after")
    def _validate_fail_closed_state(self) -> Self:
        snapshot_refs = tuple(
            _sanitize_single_recipe_ref(
                RecipeAdmissionResult._require_result_snapshot(snapshot).recipe_ref
            )
            for snapshot in self.admitted_snapshots
        )
        if self.blocked and (self.admitted_recipe_refs or self.admitted_snapshots):
            raise ValueError("blocked admission results cannot carry activation material")
        if self.admitted_recipe_refs != snapshot_refs:
            raise ValueError("admitted recipe refs must match admitted snapshots")
        if (
            self.missing_explicit_refs
            or self.missing_required_refs
            or self.conflicts
        ) and not self.blocked:
            raise ValueError("admission conflicts require blocked=true")
        return self

    @field_serializer("stack_digest")
    def _serialize_result_stack_digest(self, value: object) -> str:
        RecipeAdmissionResult._assert_result_digest_matches(self)
        return _safe_digest(value, "stack_digest")

    @field_serializer(
        "admitted_recipe_refs",
        "missing_explicit_refs",
        "missing_required_refs",
        "rejected_auto_refs",
        "required_governed_recipe_refs",
    )
    def _serialize_result_ref_section(self, value: object, info: Any) -> tuple[str, ...]:
        RecipeAdmissionResult._assert_result_digest_matches(self)
        if info.field_name == "admitted_recipe_refs" and self.blocked:
            return ()
        return _sanitize_recipe_refs(value)

    @field_serializer("admitted_snapshots")
    def _serialize_result_snapshots(
        self,
        value: object,
    ) -> tuple[dict[str, object], ...]:
        RecipeAdmissionResult._assert_result_digest_matches(self)
        if self.blocked:
            return ()
        if not isinstance(value, Iterable) or isinstance(value, (str, bytes, bytearray, Mapping)):
            raise ValueError("admitted snapshots must be iterable")
        projections: list[dict[str, object]] = []
        for snapshot in value:
            if type(snapshot) is not AdmittedRecipeSnapshot:
                raise ValueError("admitted snapshots require registry-resolved instances")
            projections.append(AdmittedRecipeSnapshot.public_projection(snapshot))
        return tuple(projections)

    @field_serializer("conflicts")
    def _serialize_result_conflicts(
        self,
        value: object,
    ) -> tuple[dict[str, str], ...]:
        RecipeAdmissionResult._assert_result_digest_matches(self)
        if not isinstance(value, Iterable) or isinstance(value, (str, bytes, bytearray, Mapping)):
            raise ValueError("conflicts must be iterable")
        projections: list[dict[str, str]] = []
        for conflict in value:
            if type(conflict) is not RecipeAdmissionConflict:
                raise ValueError("conflicts require structured conflict instances")
            projections.append(RecipeAdmissionConflict.public_projection(conflict))
        return tuple(projections)

    @field_serializer("blocked")
    def _serialize_result_blocked(self, value: object) -> bool:
        RecipeAdmissionResult._assert_result_digest_matches(self)
        return _strict_public_bool(value, "blocked")

    def _assert_stack_digest_matches(self) -> None:
        if _safe_digest(self.stack_digest, "stack_digest") != self._validated_stack_digest:
            raise ValueError("stack digest mismatch")

    @staticmethod
    def _require_result_snapshot(snapshot: object) -> AdmittedRecipeSnapshot:
        if type(snapshot) is not AdmittedRecipeSnapshot:
            raise ValueError("admitted snapshots require registry-resolved instances")
        AdmittedRecipeSnapshot._assert_registry_admitted(snapshot)
        return snapshot

    @staticmethod
    def _require_result_conflict(conflict: object) -> RecipeAdmissionConflict:
        if type(conflict) is not RecipeAdmissionConflict:
            raise ValueError("conflicts require structured conflict instances")
        return conflict

    def _result_digest_payload(self) -> dict[str, object]:
        return {
            "stackDigest": _safe_digest(self.stack_digest, "stack_digest"),
            "admittedSnapshotDigests": tuple(
                _safe_digest(
                    RecipeAdmissionResult._require_result_snapshot(snapshot).snapshot_digest,
                    "snapshot_digest",
                )
                for snapshot in self.admitted_snapshots
            ),
            "admittedRecipeRefs": _sanitize_recipe_refs(self.admitted_recipe_refs),
            "missingExplicitRefs": _sanitize_recipe_refs(self.missing_explicit_refs),
            "missingRequiredRefs": _sanitize_recipe_refs(self.missing_required_refs),
            "rejectedAutoRefs": _sanitize_recipe_refs(self.rejected_auto_refs),
            "requiredGovernedRecipeRefs": _sanitize_recipe_refs(
                self.required_governed_recipe_refs
            ),
            "conflicts": tuple(
                RecipeAdmissionConflict.public_projection(
                    RecipeAdmissionResult._require_result_conflict(conflict)
                )
                for conflict in self.conflicts
            ),
            "blocked": _strict_public_bool(self.blocked, "blocked"),
        }

    def _result_digest(self) -> str:
        payload = RecipeAdmissionResult._result_digest_payload(self)
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        return f"sha256:{hashlib.sha256(encoded).hexdigest()}"

    def _assert_result_digest_matches(self) -> None:
        RecipeAdmissionResult._assert_stack_digest_matches(self)
        registered_digest = _ADMISSION_RESULT_DIGESTS.digest_for(self)
        if RecipeAdmissionResult._result_digest(self) != registered_digest:
            raise ValueError("admission result digest mismatch")

    def public_projection(self) -> dict[str, object]:
        RecipeAdmissionResult._assert_result_digest_matches(self)
        admitted_recipe_refs: tuple[str, ...] = ()
        admitted_snapshots: tuple[dict[str, object], ...] = ()
        if not self.blocked:
            admitted_recipe_refs = _sanitize_recipe_refs(self.admitted_recipe_refs)
            admitted_snapshots = tuple(
                AdmittedRecipeSnapshot.public_projection(
                    RecipeAdmissionResult._require_result_snapshot(snapshot)
                )
                for snapshot in self.admitted_snapshots
            )
        return {
            "stackDigest": _safe_digest(self.stack_digest, "stack_digest"),
            "admittedRecipeRefs": admitted_recipe_refs,
            "missingExplicitRefs": _sanitize_recipe_refs(self.missing_explicit_refs),
            "missingRequiredRefs": _sanitize_recipe_refs(self.missing_required_refs),
            "rejectedAutoRefs": _sanitize_recipe_refs(self.rejected_auto_refs),
            "requiredGovernedRecipeRefs": _sanitize_recipe_refs(
                self.required_governed_recipe_refs
            ),
            "blocked": _strict_public_bool(self.blocked, "blocked"),
            "conflicts": tuple(
                RecipeAdmissionConflict.public_projection(
                    RecipeAdmissionResult._require_result_conflict(conflict)
                )
                for conflict in self.conflicts
            ),
            "admittedSnapshots": admitted_snapshots,
        }


def admit_recipe_stack(request: RecipeAdmissionRequest) -> RecipeAdmissionResult:
    admission_request = _revalidate_admission_request(request)
    stack = admission_request.stack
    stack_refs = _dedupe_preserving_order(stack.all_recipe_refs())
    snapshot_by_ref: dict[str, AdmittedRecipeSnapshot] = {}
    conflicts: list[RecipeAdmissionConflict] = []

    for snapshot in admission_request.admitted_snapshots:
        recipe_ref = _sanitize_single_recipe_ref(snapshot.recipe_ref)
        if recipe_ref in snapshot_by_ref:
            conflicts.append(
                RecipeAdmissionConflict(
                    code="duplicate_admitted_recipe_snapshot",
                    recipeRef=recipe_ref,
                )
            )
            continue
        snapshot_by_ref[recipe_ref] = snapshot

    admitted_snapshots: list[AdmittedRecipeSnapshot] = []
    admitted_recipe_refs: list[str] = []
    missing_explicit_refs: list[str] = []
    missing_required_refs: list[str] = []
    rejected_auto_refs: list[str] = []

    for recipe_ref in stack_refs:
        snapshot = snapshot_by_ref.get(recipe_ref)
        if snapshot is None:
            if recipe_ref in stack.explicit_recipe_refs:
                missing_explicit_refs.append(recipe_ref)
                conflicts.append(
                    RecipeAdmissionConflict(
                        code="explicit_recipe_missing",
                        recipeRef=recipe_ref,
                    )
                )
            if recipe_ref in stack.hard_safety_refs:
                missing_required_refs.append(recipe_ref)
                conflicts.append(
                    RecipeAdmissionConflict(
                        code="hard_safety_recipe_missing",
                        recipeRef=recipe_ref,
                    )
                )
            elif recipe_ref in stack.auto_recipe_refs:
                rejected_auto_refs.append(recipe_ref)
            continue
        if recipe_ref in stack.hard_safety_refs and not snapshot.hard_safety:
            missing_required_refs.append(recipe_ref)
            conflicts.append(
                RecipeAdmissionConflict(
                    code="hard_safety_recipe_not_hard",
                    recipeRef=recipe_ref,
                )
            )
            continue
        admitted_snapshots.append(snapshot)
        admitted_recipe_refs.append(recipe_ref)

    for recipe_ref in admission_request.required_governed_recipe_refs:
        snapshot = snapshot_by_ref.get(recipe_ref)
        if recipe_ref not in stack_refs or snapshot is None:
            conflicts.append(
                RecipeAdmissionConflict(
                    code="required_governed_recipe_missing",
                    recipeRef=recipe_ref,
                )
            )
        elif not snapshot.governed:
            conflicts.append(
                RecipeAdmissionConflict(
                    code="required_governed_recipe_resolved_ungoverned",
                    recipeRef=recipe_ref,
                )
            )

    blocked = bool(conflicts)
    effective_admitted_snapshots: tuple[AdmittedRecipeSnapshot, ...] = ()
    effective_admitted_recipe_refs: tuple[str, ...] = ()
    if not blocked:
        effective_admitted_snapshots = tuple(admitted_snapshots)
        effective_admitted_recipe_refs = tuple(admitted_recipe_refs)

    return RecipeAdmissionResult(
        stackDigest=RecipeStackInput.stack_digest(stack),
        admittedSnapshots=effective_admitted_snapshots,
        admittedRecipeRefs=effective_admitted_recipe_refs,
        missingExplicitRefs=tuple(missing_explicit_refs),
        missingRequiredRefs=tuple(missing_required_refs),
        rejectedAutoRefs=tuple(rejected_auto_refs),
        requiredGovernedRecipeRefs=admission_request.required_governed_recipe_refs,
        conflicts=tuple(conflicts),
        blocked=blocked,
    )


__all__ = (
    "AdmittedRecipeSnapshot",
    "RecipeAdmissionConflict",
    "RecipeAdmissionRequest",
    "RecipeAdmissionResult",
    "RecipeStackInput",
    "admit_recipe_stack",
)
