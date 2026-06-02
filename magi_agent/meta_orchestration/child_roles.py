from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Mapping, Sequence
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from magi_agent.meta_orchestration.task_plan import (
    _copy_update_alias,
    _validate_public_ref,
    _validate_public_text,
    _validate_ref_tuple,
)

_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    revalidate_instances="always",
    hide_input_in_errors=True,
)
_DOMAIN_VALUES = frozenset({"generic", "research", "coding", "backoffice", "custom"})
_FORBIDDEN_SINGLE_TOOL_TERMS = frozenset(
    {
        "bash",
        "shell",
        "k8s",
        "deploy",
        "secret",
        "secrets",
        "env",
        "provisioning",
        "supabase",
        "frontend",
    }
)
_FORBIDDEN_COMPOUND_TOOL_TERMS = frozenset(
    {
        ("live", "web"),
        ("workspace", "write"),
        ("memory", "write"),
        ("channel", "send"),
        ("chat", "proxy"),
        ("prod", "route"),
    }
)


class _MetaChildRoleModel(BaseModel):
    model_config = _MODEL_CONFIG

    @classmethod
    def model_construct(
        cls,
        _fields_set: set[str] | None = None,
        **values: Any,
    ) -> Self:
        _ = _fields_set, values
        raise TypeError("model_construct is disabled for meta child role contracts")

    def model_copy(
        self,
        *,
        update: Mapping[str, Any] | None = None,
        deep: bool = False,
    ) -> Self:
        _ = deep
        data = self.model_dump(by_alias=True, mode="python", warnings=False)
        if update:
            for key, value in update.items():
                data[_copy_update_alias(type(self), key)] = value
        return type(self).model_validate(data)


class MetaChildRoleDefinition(_MetaChildRoleModel):
    role_ref: str = Field(alias="roleRef")
    display_name: str = Field(alias="displayName", max_length=160)
    domain: str
    allowed_tool_refs: tuple[str, ...] = Field(alias="allowedToolRefs")
    denied_tool_refs: tuple[str, ...] = Field(default=(), alias="deniedToolRefs")
    context_policy_ref: str = Field(alias="contextPolicyRef")
    completion_contract_ref: str = Field(alias="completionContractRef")
    max_spawn_depth: int = Field(alias="maxSpawnDepth", ge=0, le=8, strict=True)
    default_off: Literal[True] = Field(default=True, alias="defaultOff")

    @field_validator("role_ref", "context_policy_ref", "completion_contract_ref")
    @classmethod
    def _validate_refs(cls, value: str, info: Any) -> str:
        return _validate_public_ref(value, info.field_name)

    @field_validator("display_name")
    @classmethod
    def _validate_display_name(cls, value: str) -> str:
        return _validate_public_text(value, "displayName")

    @field_validator("domain")
    @classmethod
    def _validate_domain(cls, value: str) -> str:
        clean = _validate_public_ref(value, "domain")
        if clean not in _DOMAIN_VALUES:
            raise ValueError("domain must be generic, research, coding, backoffice, or custom")
        return clean

    @field_validator("allowed_tool_refs")
    @classmethod
    def _validate_allowed_tool_refs(cls, value: Sequence[str]) -> tuple[str, ...]:
        for ref in tuple(value):
            if _is_forbidden_global_tool_ref(ref):
                raise ValueError(f"allowedToolRefs contains forbidden global tool ref {ref!r}")
        refs = _sorted_ref_tuple(value, "allowedToolRefs")
        if not refs:
            raise ValueError("allowedToolRefs must include at least one explicit grant")
        return refs

    @field_validator("denied_tool_refs")
    @classmethod
    def _validate_denied_tool_refs(cls, value: Sequence[str]) -> tuple[str, ...]:
        return _sorted_ref_tuple(value, "deniedToolRefs")

    @field_validator("default_off", mode="before")
    @classmethod
    def _validate_default_off(cls, value: object) -> object:
        if value is not True:
            raise ValueError("defaultOff must remain true")
        return value

    @model_validator(mode="after")
    def _validate_role_contract(self) -> Self:
        overlap = set(self.allowed_tool_refs) & set(self.denied_tool_refs)
        if overlap:
            raise ValueError("allowedToolRefs and deniedToolRefs must not overlap")
        return self

    def public_projection(self) -> dict[str, object]:
        return {
            "roleRef": self.role_ref,
            "displayName": self.display_name,
            "domain": self.domain,
            "contextPolicyRef": self.context_policy_ref,
            "completionContractRef": self.completion_contract_ref,
            "maxSpawnDepth": self.max_spawn_depth,
            "maxSpawnDepthDescriptiveOnly": True,
            "defaultOff": self.default_off,
            "allowedToolCount": len(self.allowed_tool_refs),
            "deniedToolCount": len(self.denied_tool_refs),
            "toolGrantDigest": _digest_tool_grants(self.allowed_tool_refs, self.denied_tool_refs),
        }


class MetaChildRoleRegistry:
    def __init__(self, roles: Iterable[MetaChildRoleDefinition | Mapping[str, object]] = ()) -> None:
        by_ref: dict[str, MetaChildRoleDefinition] = {}
        for item in roles:
            role = (
                item
                if isinstance(item, MetaChildRoleDefinition)
                else MetaChildRoleDefinition.model_validate(item)
            )
            if role.role_ref in by_ref:
                raise ValueError(f"duplicate roleRef {role.role_ref!r}")
            by_ref[role.role_ref] = role
        self._roles_by_ref = dict(sorted(by_ref.items()))

    def role_refs(self) -> tuple[str, ...]:
        return tuple(self._roles_by_ref)

    def require(self, role_ref: str) -> MetaChildRoleDefinition:
        clean_ref = _validate_public_ref(role_ref, "roleRef")
        try:
            return self._roles_by_ref[clean_ref]
        except KeyError as exc:
            raise KeyError(f"unknown roleRef {clean_ref!r}") from exc

    def allowed_tool_refs_for(self, role_ref: str) -> tuple[str, ...]:
        return self.require(role_ref).allowed_tool_refs

    def public_projection(self) -> tuple[dict[str, object], ...]:
        return tuple(role.public_projection() for role in self._roles_by_ref.values())


def _sorted_ref_tuple(value: Sequence[str], field_name: str) -> tuple[str, ...]:
    return tuple(sorted(_validate_ref_tuple(value, field_name)))


def _is_forbidden_global_tool_ref(ref: str) -> bool:
    terms = _tool_ref_terms(ref)
    if any(term in _FORBIDDEN_SINGLE_TOOL_TERMS for term in terms):
        return True
    return any(_contains_adjacent_terms(terms, compound) for compound in _FORBIDDEN_COMPOUND_TOOL_TERMS)


def _tool_ref_terms(ref: str) -> tuple[str, ...]:
    split_camel = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", ref)
    return tuple(
        term.lower()
        for term in re.split(r"[^A-Za-z0-9]+", split_camel)
        if term
    )


def _contains_adjacent_terms(terms: tuple[str, ...], compound: tuple[str, str]) -> bool:
    return any(pair == compound for pair in zip(terms, terms[1:]))


def _digest_tool_grants(allowed: tuple[str, ...], denied: tuple[str, ...]) -> str:
    payload = json.dumps(
        {"allowedToolRefs": allowed, "deniedToolRefs": denied},
        sort_keys=True,
        separators=(",", ":"),
    )
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()
