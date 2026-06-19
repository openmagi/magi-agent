"""Frozen-contract / authority model bases (C-4 / C-5 shared home).

A dependency-free leaf (stdlib + pydantic only). No imports from other
``magi_agent`` subpackages, so every consumer can safely import it.

``FrozenContractModel`` is the single frozen-contract base: the canonical
``ConfigDict`` trio (``frozen``, ``populate_by_name``, ``extra="forbid"``,
``validate_default``, ``hide_input_in_errors``) plus the escape-hatch disabling
(``model_construct`` rejected, ``model_copy(update=...)`` rejected) that was
hand-re-pasted across ~15 sites (``billing/quota``, ``billing/spend_guard``,
``tenancy/context``, ``ops/job_queue``, ``ops/metrics`` x4, ...).

The force-false authority base (``FalseOnlyAuthorityModel``, C-4) will live
beside this one; it is a separate consolidation.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Self

from pydantic import BaseModel, ConfigDict

FROZEN_CONTRACT_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    hide_input_in_errors=True,
)


class FrozenContractModel(BaseModel):
    """Immutable, alias-aware, extra-forbidding contract model.

    Disables the pydantic escape hatches that would let a caller bypass
    validation: ``model_construct`` always raises, and ``model_copy`` rejects an
    ``update`` (a frozen contract must be rebuilt through validation, not mutated
    in place). ``model_copy()`` without an update round-trips through
    ``model_validate`` so the copy is re-validated.
    """

    model_config = FROZEN_CONTRACT_CONFIG

    @classmethod
    def model_construct(cls, _fields_set: set[str] | None = None, **values: object) -> Self:
        _ = _fields_set, values
        raise ValueError(f"model_construct is disabled for {cls.__name__}")

    def model_copy(self, *, update: Mapping[str, object] | None = None, deep: bool = False) -> Self:
        if update:
            raise ValueError(f"model_copy update is disabled for {type(self).__name__}")
        _ = deep
        return type(self).model_validate(self.model_dump(by_alias=True, mode="json"))


__all__ = ["FROZEN_CONTRACT_CONFIG", "FrozenContractModel"]
