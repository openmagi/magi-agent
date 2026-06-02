from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from magi_agent.runtime.query_state import validate_safe_ref


_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    revalidate_instances="always",
    hide_input_in_errors=True,
)
_ALLOWED_KEYS = frozenset(
    {
        "temperature",
        "maxoutputtokens",
        "topp",
        "topk",
        "seed",
        "responsemimetype",
        "presencepenalty",
        "frequencypenalty",
    }
)
_CANONICAL_KEYS = {
    "temperature": "temperature",
    "maxoutputtokens": "maxOutputTokens",
    "topp": "topP",
    "topk": "topK",
    "seed": "seed",
    "responsemimetype": "responseMimeType",
    "presencepenalty": "presencePenalty",
    "frequencypenalty": "frequencyPenalty",
}
_EXACT_KEYS = frozenset(_CANONICAL_KEYS.values())
_NUMERIC_KEYS = frozenset(
    {
        "temperature",
        "topp",
        "presencepenalty",
        "frequencypenalty",
    }
)
_INTEGER_KEYS = frozenset({"maxoutputtokens", "topk", "seed"})
_STRING_ENUMS = {
    "responsemimetype": frozenset(
        {
            "application/json",
            "text/plain",
            "text/markdown",
            "application/x-ndjson",
        }
    )
}
class CacheSafeParams(BaseModel):
    model_config = _MODEL_CONFIG

    model_ref: str = Field(alias="modelRef")
    runtime_config_ref: str = Field(alias="runtimeConfigRef")
    cache_namespace_ref: str = Field(
        default="cache-namespace:default",
        alias="cacheNamespaceRef",
    )
    params: dict[str, int | float | bool | str | None] = Field(default_factory=dict)
    digest: str = ""

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
        update: Mapping[str, object] | None = None,
        deep: bool = False,
    ) -> Self:
        data = self.model_dump(by_alias=True, mode="python", warnings=False)
        if update:
            data.update(_alias_update(type(self), update))
        data.pop("digest", None)
        _ = deep
        return type(self).model_validate(data)

    @field_validator("model_ref", "runtime_config_ref", "cache_namespace_ref")
    @classmethod
    def _validate_ref(cls, value: str) -> str:
        return validate_safe_ref(value)

    @field_validator("params", mode="before")
    @classmethod
    def _validate_params(cls, value: object) -> dict[str, int | float | bool | str | None]:
        if value is None:
            return {}
        if not isinstance(value, Mapping):
            raise ValueError("cache-safe params must be a mapping")
        safe: dict[str, int | float | bool | str | None] = {}
        for key, item in value.items():
            normalized_key = re.sub(r"[^a-z0-9]", "", key.lower()) if isinstance(key, str) else ""
            if (
                not isinstance(key, str)
                or key not in _EXACT_KEYS
                or normalized_key not in _ALLOWED_KEYS
            ):
                raise ValueError("cache-safe params must use allowlisted runtime keys")
            if item is None:
                safe[_CANONICAL_KEYS[normalized_key]] = item
                continue
            if normalized_key in _NUMERIC_KEYS:
                if isinstance(item, bool) or not isinstance(item, int | float):
                    raise ValueError("cache-safe numeric params must be numeric")
                safe[_CANONICAL_KEYS[normalized_key]] = item
                continue
            if normalized_key in _INTEGER_KEYS:
                if isinstance(item, bool) or not isinstance(item, int):
                    raise ValueError("cache-safe integer params must be integers")
                safe[_CANONICAL_KEYS[normalized_key]] = item
                continue
            allowed_values = _STRING_ENUMS.get(normalized_key)
            if isinstance(item, str) and allowed_values is not None and item in allowed_values:
                safe[_CANONICAL_KEYS[normalized_key]] = item
                continue
            raise ValueError("cache-safe params must not include raw content or secrets")
        return safe

    @model_validator(mode="after")
    def _set_digest(self) -> Self:
        payload = {
            "modelRef": self.model_ref,
            "runtimeConfigRef": self.runtime_config_ref,
            "cacheNamespaceRef": self.cache_namespace_ref,
            "params": self.params,
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        object.__setattr__(self, "digest", "sha256:" + hashlib.sha256(encoded).hexdigest())
        return self

    def public_projection(self) -> dict[str, object]:
        return self.model_dump(by_alias=True, mode="python")


__all__ = ["CacheSafeParams"]


def _alias_update(
    cls: type[BaseModel],
    update: Mapping[str, object],
) -> dict[str, object]:
    alias_by_name = {
        name: field.alias
        for name, field in cls.model_fields.items()
        if field.alias is not None
    }
    return {
        str(alias_by_name.get(str(key), str(key))): value
        for key, value in update.items()
    }
