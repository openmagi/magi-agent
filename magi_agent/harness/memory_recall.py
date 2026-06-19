from __future__ import annotations

from collections.abc import Mapping
from typing import Literal

from pydantic import Field

from magi_agent.memory.contracts import RecallRequest
from magi_agent.memory.namespaces import MemoryNamespacePolicy
from magi_agent.ops.authority import FalseOnlyAuthorityModel
from magi_agent.recipes.first_party.memory_recall import (
    MemoryRecallProjectionPolicy,
    MemoryRecallRecipeResult,
    execute_readonly_memory_recall,
)


class MemoryRecallHarnessConfig(FalseOnlyAuthorityModel):
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


class MemoryRecallHarness:
    """Default-off read-only memory recall harness over an injected local fake adapter."""

    def __init__(
        self,
        config: MemoryRecallHarnessConfig | Mapping[str, object] | None = None,
        *,
        adapter: object | None = None,
        default_namespace_policy: object | None = None,
        default_projection_policy: object | None = None,
    ) -> None:
        self.config = (
            config
            if isinstance(config, MemoryRecallHarnessConfig)
            else MemoryRecallHarnessConfig.model_validate(config or {})
        )
        self.adapter = adapter
        # C4 dual-load seams: used ONLY when a recall() caller passes None for
        # the corresponding policy. The shipped defaults stay None, so existing
        # per-call policy construction (and the missing-policy denials) are
        # byte-identical.
        self._default_namespace_policy = default_namespace_policy
        self._default_projection_policy = default_projection_policy

    async def recall(
        self,
        *,
        request: RecallRequest | Mapping[str, object],
        namespace_policy: MemoryNamespacePolicy | Mapping[str, object] | None,
        projection_policy: MemoryRecallProjectionPolicy | Mapping[str, object] | None,
    ) -> MemoryRecallRecipeResult:
        return await execute_readonly_memory_recall(
            request=request,
            namespace_policy=(
                namespace_policy
                if namespace_policy is not None
                else self._default_namespace_policy
            ),
            projection_policy=(
                projection_policy
                if projection_policy is not None
                else self._default_projection_policy
            ),
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


def build_gated_live_learning_recall_harness(
    *,
    store: object | None = None,
    readiness: object,
    bot_id: str,
    user_id: str,
    tenant_id: str = "local",
    namespace_ref: str | None = None,
    k: int = 8,
) -> MemoryRecallHarness | None:
    """PR7 — gated REAL learning-recall binding.

    Promotes the PR5 ``build_learning_recall_harness`` local-fake to a real
    (live) recall binding ONLY when the learning-live readiness gate
    (``gates/learning_live_readiness``) resolves to ``live`` for the given
    scope.  Otherwise returns ``None`` — the caller keeps the local-fake /
    disabled path, byte-identical to PR1–PR6.

    Authority flags on the underlying ``MemoryRecallHarnessConfig`` stay
    frozen-``Literal[False]``; live behaviour is gate-derived and recorded in the
    ``learning/live`` audit, NOT by flipping a flag.  The import of the readiness
    gate + live binding is lazy so this module's default import path is unchanged.
    """
    from magi_agent.gates.learning_live_readiness import (  # lazy: keep seam thin
        LearningLiveReadinessConfig,
        resolve_learning_live_execution_mode,
    )

    if not isinstance(readiness, LearningLiveReadinessConfig):
        return None
    mode = resolve_learning_live_execution_mode(
        readiness, bot_id=bot_id, user_id=user_id
    )
    if mode != "live":
        # disabled / shadow → no live recall binding (shadow is observe-only).
        return None
    return build_learning_recall_harness(
        store=store,
        tenant_id=tenant_id,
        enabled=True,
        local_fake_adapter_enabled=True,
        namespace_ref=namespace_ref,
        k=k,
    )


__all__ = [
    "MemoryRecallHarness",
    "MemoryRecallHarnessConfig",
    "build_gated_live_learning_recall_harness",
    "build_learning_recall_harness",
]
