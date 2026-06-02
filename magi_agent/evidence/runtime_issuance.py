from __future__ import annotations

import json
import re
from collections.abc import Mapping
from hashlib import sha256
from typing import Any, Literal, Self
from weakref import finalize

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, field_validator


RuntimeIssueScope = str

_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    revalidate_instances="never",
    hide_input_in_errors=True,
)
_PUBLIC_REF_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{1,180}$")
_AUTHORITY_OBJECT_IDS: set[int] = set()
_AUTHORITY_FINGERPRINTS: dict[int, str] = {}
_AUTHORITY_FINALIZERS: dict[int, object] = {}


class RuntimeIssueAuthority(BaseModel):
    model_config = _MODEL_CONFIG

    _issued_by_runtime_boundary: bool = PrivateAttr(default=False)

    authority_id: str = Field(alias="authorityId")
    issuer: Literal["openmagi_runtime_boundary"] = "openmagi_runtime_boundary"
    scopes: tuple[RuntimeIssueScope, ...]
    local_only: Literal[True] = Field(default=True, alias="localOnly")

    @classmethod
    def model_construct(
        cls,
        _fields_set: set[str] | None = None,
        **values: Any,
    ) -> Self:
        _ = _fields_set, values
        raise TypeError("model_construct is disabled for runtime issue authority")

    def model_copy(
        self,
        *,
        update: Mapping[str, Any] | None = None,
        deep: bool = False,
    ) -> Self:
        _ = update, deep
        raise TypeError("runtime issue authority cannot be copied")

    @property
    def is_runtime_boundary_issued(self) -> bool:
        object_id = id(self)
        return (
            bool(self.__pydantic_private__.get("_issued_by_runtime_boundary"))
            and object_id in _AUTHORITY_OBJECT_IDS
            and _AUTHORITY_FINGERPRINTS.get(object_id) == _authority_fingerprint(self)
        )

    @field_validator("authority_id")
    @classmethod
    def _validate_authority_id(cls, value: str) -> str:
        return _public_ref(value, "authorityId")

    @field_validator("scopes")
    @classmethod
    def _validate_scopes(
        cls,
        value: tuple[RuntimeIssueScope, ...],
    ) -> tuple[RuntimeIssueScope, ...]:
        scopes = tuple(_public_ref(item, "scope") for item in value)
        if not scopes:
            raise ValueError("runtime issue authority requires at least one scope")
        if len(set(scopes)) != len(scopes):
            raise ValueError("runtime issue authority scopes must be unique")
        return scopes

    def public_projection(self) -> dict[str, object]:
        return {
            "authorityId": self.authority_id,
            "issuer": self.issuer,
            "scopes": self.scopes,
            "localOnly": self.local_only,
        }


def require_runtime_issue_authority(
    authority: RuntimeIssueAuthority | None,
    *,
    scope: RuntimeIssueScope,
) -> RuntimeIssueAuthority:
    requested_scope = _public_ref(scope, "scope")
    if (
        authority is None
        or not authority.is_runtime_boundary_issued
        or requested_scope not in authority.scopes
    ):
        raise RuntimeError(f"{requested_scope} requires runtime issue authority")
    return authority


def _discard_authority_object_id(object_id: int) -> None:
    _AUTHORITY_OBJECT_IDS.discard(object_id)
    _AUTHORITY_FINGERPRINTS.pop(object_id, None)
    _AUTHORITY_FINALIZERS.pop(object_id, None)


def _authority_fingerprint(authority: RuntimeIssueAuthority) -> str:
    material = json.dumps(
        authority.public_projection(),
        sort_keys=True,
        separators=(",", ":"),
    )
    return "sha256:" + sha256(material.encode("utf-8")).hexdigest()


def _public_ref(value: str, field_name: str) -> str:
    clean = value.strip()
    if not clean:
        raise ValueError(f"{field_name} must be non-empty")
    if not _PUBLIC_REF_RE.fullmatch(clean):
        raise ValueError(f"{field_name} must be a digest-safe public id")
    return clean


__all__ = [
    "RuntimeIssueAuthority",
    "RuntimeIssueScope",
    "require_runtime_issue_authority",
]
