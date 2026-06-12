"""Kernel-owned catalog contract (D4) — re-homed from the deleted authoring plane.

``CompileRecipePackCatalog`` is the flat reference catalog the live runtime
validates primitive refs against. It was historically defined in
``magi_agent/authoring/compiler.py``; when main deleted the authoring recipe
builder (zero non-test importers), the catalog model moved HERE because the
neutral pack kernel is its real owner: the live catalog is built from loaded
pack manifests (:mod:`magi_agent.packs.catalog_build`), not from any authoring
flow. Public symbol name is preserved.

The model keeps the original contract hardening: frozen, ``extra="forbid"``,
camelCase aliases, ``model_construct`` disabled (no validation bypass) and an
alias-aware ``model_copy``.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

_MODEL_CONFIG = ConfigDict(frozen=True, populate_by_name=True, extra="forbid")


def _copy_update_alias(model_type: type[BaseModel], key: str) -> str:
    field = model_type.model_fields.get(key)
    if field is not None and field.alias is not None:
        return field.alias
    return key


class _CatalogModel(BaseModel):
    model_config = _MODEL_CONFIG

    @classmethod
    def model_construct(cls, *args: object, **kwargs: object) -> Self:
        raise TypeError("model_construct is disabled for catalog contracts")

    def model_copy(
        self,
        *,
        update: Mapping[str, Any] | None = None,
        deep: bool = False,
    ) -> Self:
        data = self.model_dump(by_alias=True)
        if update:
            for key, value in update.items():
                data[_copy_update_alias(type(self), key)] = value
        return type(self).model_validate(data)


class CompileRecipePackCatalog(_CatalogModel):
    connector_refs: tuple[str, ...] = Field(default=(), alias="connectorRefs")
    tool_refs: tuple[str, ...] = Field(default=(), alias="toolRefs")
    plugin_refs: tuple[str, ...] = Field(default=(), alias="pluginRefs")
    validator_refs: tuple[str, ...] = Field(default=(), alias="validatorRefs")
    harness_refs: tuple[str, ...] = Field(default=(), alias="harnessRefs")
    required_evidence_refs: tuple[str, ...] = Field(
        default=(), alias="requiredEvidenceRefs"
    )
    evidence_producer_refs: tuple[str, ...] = Field(
        default=(), alias="evidenceProducerRefs"
    )
    approval_authority_refs: tuple[str, ...] = Field(
        default=("authority:owner-human@1",), alias="approvalAuthorityRefs"
    )
    hard_invariant_refs: tuple[str, ...] = Field(default=(), alias="hardInvariantRefs")
    required_hard_invariant_refs: tuple[str, ...] = Field(
        default=("invariant.no-live-execution", "invariant.no-activation"),
        alias="requiredHardInvariantRefs",
    )

    @classmethod
    def default(cls) -> CompileRecipePackCatalog:
        return cls(
            connectorRefs=("connector.source.readonly",),
            toolRefs=(
                "BrowserLive",
                "CitationVerify",
                "FileWrite",
                "SourceOpen",
            ),
            pluginRefs=("plugin.source-review.readonly",),
            validatorRefs=("validator:sourceOpened@1", "validator:quoteExactMatch@1"),
            harnessRefs=("harness:authoring-static@1",),
            requiredEvidenceRefs=("openedSourceSnapshot", "quoteDigest"),
            evidenceProducerRefs=("evidence:source-opened@1", "evidence:quote-digest@1"),
            approvalAuthorityRefs=("authority:owner-human@1",),
            hardInvariantRefs=("invariant.no-live-execution", "invariant.no-activation"),
            requiredHardInvariantRefs=(
                "invariant.no-live-execution",
                "invariant.no-activation",
            ),
        )

    @field_validator(
        "connector_refs",
        "tool_refs",
        "plugin_refs",
        "validator_refs",
        "harness_refs",
        "required_evidence_refs",
        "evidence_producer_refs",
        "approval_authority_refs",
        "hard_invariant_refs",
        "required_hard_invariant_refs",
    )
    @classmethod
    def _validate_refs(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not isinstance(item, str) or not item.strip() for item in value):
            raise ValueError("catalog refs must be non-empty strings")
        return value

    @model_validator(mode="after")
    def _validate_required_hard_invariants(self) -> CompileRecipePackCatalog:
        missing = set(self.required_hard_invariant_refs).difference(self.hard_invariant_refs)
        if missing:
            raise ValueError("requiredHardInvariantRefs must be declared in hardInvariantRefs")
        return self
