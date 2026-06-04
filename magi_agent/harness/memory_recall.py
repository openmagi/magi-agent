from __future__ import annotations

from collections.abc import Mapping
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_serializer, model_validator

from magi_agent.memory.contracts import RecallRequest
from magi_agent.memory.namespaces import MemoryNamespacePolicy
from magi_agent.recipes.first_party.memory_recall import (
    MemoryRecallProjectionPolicy,
    MemoryRecallRecipeResult,
    execute_readonly_memory_recall,
)


_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    hide_input_in_errors=True,
)


class MemoryRecallHarnessConfig(BaseModel):
    model_config = _MODEL_CONFIG

    enabled: bool = False
    local_fake_adapter_enabled: bool = Field(default=False, alias="localFakeAdapterEnabled")
    live_provider_enabled: Literal[False] = Field(default=False, alias="liveProviderEnabled")
    traffic_attached: Literal[False] = Field(default=False, alias="trafficAttached")
    user_visible_output_allowed: Literal[False] = Field(
        default=False,
        alias="userVisibleOutputAllowed",
    )
    memory_write_allowed: Literal[False] = Field(default=False, alias="memoryWriteAllowed")
    production_write_allowed: Literal[False] = Field(
        default=False,
        alias="productionWriteAllowed",
    )

    @model_validator(mode="before")
    @classmethod
    def _force_default_off_authority(cls, value: object) -> dict[str, object]:
        payload = dict(value) if isinstance(value, Mapping) else {}
        payload["liveProviderEnabled"] = False
        payload["trafficAttached"] = False
        payload["userVisibleOutputAllowed"] = False
        payload["memoryWriteAllowed"] = False
        payload["productionWriteAllowed"] = False
        return payload

    @field_serializer(
        "live_provider_enabled",
        "traffic_attached",
        "user_visible_output_allowed",
        "memory_write_allowed",
        "production_write_allowed",
    )
    def _serialize_false(self, _value: object) -> bool:
        return False


class MemoryRecallHarness:
    """Default-off read-only memory recall harness over an injected local fake adapter."""

    def __init__(
        self,
        config: MemoryRecallHarnessConfig | Mapping[str, object] | None = None,
        *,
        adapter: object | None = None,
    ) -> None:
        self.config = (
            config
            if isinstance(config, MemoryRecallHarnessConfig)
            else MemoryRecallHarnessConfig.model_validate(config or {})
        )
        self.adapter = adapter

    async def recall(
        self,
        *,
        request: RecallRequest | Mapping[str, object],
        namespace_policy: MemoryNamespacePolicy | Mapping[str, object] | None,
        projection_policy: MemoryRecallProjectionPolicy | Mapping[str, object] | None,
    ) -> MemoryRecallRecipeResult:
        return await execute_readonly_memory_recall(
            request=request,
            namespace_policy=namespace_policy,
            projection_policy=projection_policy,
            adapter=self.adapter,
            enabled=self.config.enabled,
            local_fake_adapter_enabled=self.config.local_fake_adapter_enabled,
        )


def build_learning_recall_harness(
    *,
    store: object | None = None,
    tenant_id: str = "local",
    enabled: bool = False,
    local_fake_adapter_enabled: bool = False,
    namespace_ref: str | None = None,
    k: int = 8,
) -> MemoryRecallHarness:
    """Bind a learning-recall adapter into the memory_recall DI seam.

    Returns a ``MemoryRecallHarness`` whose adapter is a
    ``magi_agent.learning.injection.LearningRecallAdapter`` — a local-fake
    provider that maps the request scope to ``store.retrieve(active-only)``.

    Default-OFF: ``enabled`` / ``local_fake_adapter_enabled`` default to
    ``False`` and ``store`` defaults to ``None``, so the harness performs no
    recall unless it is gated ON *and* a store is injected.  Authority flags on
    the underlying config stay frozen-False.  Real (live) recall binding is
    deferred to PR7 — this factory only ever attaches the local fake.

    The import of ``magi_agent.learning.injection`` is lazy so importing this
    module does not pull the learning store onto the memory_recall import path
    by default.
    """
    from magi_agent.learning.injection import (  # local import: keep seam thin
        DEFAULT_LEARNING_NAMESPACE_REF,
        LearningRecallAdapter,
    )

    adapter = LearningRecallAdapter(
        store=store,
        tenant_id=tenant_id,
        namespace_ref=namespace_ref or DEFAULT_LEARNING_NAMESPACE_REF,
        k=k,
    )
    config = MemoryRecallHarnessConfig(
        enabled=enabled,
        localFakeAdapterEnabled=local_fake_adapter_enabled,
    )
    return MemoryRecallHarness(config, adapter=adapter)


__all__ = [
    "MemoryRecallHarness",
    "MemoryRecallHarnessConfig",
    "build_learning_recall_harness",
]
