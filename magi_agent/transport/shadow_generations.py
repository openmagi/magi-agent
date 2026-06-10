"""Gate5B4C3 generation route configuration.

This module used to also host the ``/v1/internal/gate5b/shadow-generations``
comparison endpoint — a TS-era diagnostic surface for comparing Python shadow
generations against the TypeScript runtime. The hosted chat-proxy never called
it (consumer count: 0), so 08-PR2 deleted the endpoint, its
``_reject_true_authority_flags`` guard, and the report/counter plumbing it
carried. What remains is the route configuration consumed by the live
user-visible serving path (``transport.chat_routes`` /
``transport.gate2_sandbox_canary``) and built by
``config.env.parse_gate5b4c3_shadow_generation_route_env``.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from magi_agent.shadow.gate5b4c3_shadow_generation_contract import (
    Gate5B4C3ShadowGenerationConfig,
)
from magi_agent.shadow.gate5b4c3_shadow_counter_store import (
    Gate5B4C3ShadowCounterStore,
)


@dataclass(frozen=True, init=False)
class Gate5B4C3MockedAdkPrimitivesLoader:
    loader: Callable[[], object]

    def __init__(self, loader: Callable[[], object]) -> None:
        object.__setattr__(self, "loader", loader)


@dataclass(frozen=True, init=False)
class Gate5B4C3ShadowGenerationRouteConfig:
    mocked_runner_boundary_enabled: bool
    live_runner_boundary_enabled: bool
    generation_config: Gate5B4C3ShadowGenerationConfig
    mocked_adk_primitives_loader: Gate5B4C3MockedAdkPrimitivesLoader | None
    counter_store: Gate5B4C3ShadowCounterStore | None

    def __init__(
        self,
        mocked_runner_boundary_enabled: object = False,
        live_runner_boundary_enabled: object = False,
        generation_config: Gate5B4C3ShadowGenerationConfig | None = None,
        adk_primitives_loader: Callable[[], object] | None = None,
        *,
        mockedRunnerBoundaryEnabled: object | None = None,
        liveRunnerBoundaryEnabled: object | None = None,
        generationConfig: Gate5B4C3ShadowGenerationConfig | None = None,
        adkPrimitivesLoader: Callable[[], object] | None = None,
        mockedAdkPrimitivesLoader: Gate5B4C3MockedAdkPrimitivesLoader | None = None,
        counterStore: Gate5B4C3ShadowCounterStore | None = None,
    ) -> None:
        mocked_enabled_value = (
            mocked_runner_boundary_enabled
            if mockedRunnerBoundaryEnabled is None
            else mockedRunnerBoundaryEnabled
        )
        live_enabled_value = (
            live_runner_boundary_enabled
            if liveRunnerBoundaryEnabled is None
            else liveRunnerBoundaryEnabled
        )
        object.__setattr__(
            self,
            "mocked_runner_boundary_enabled",
            mocked_enabled_value is True,
        )
        object.__setattr__(
            self,
            "live_runner_boundary_enabled",
            live_enabled_value is True,
        )
        object.__setattr__(
            self,
            "generation_config",
            generation_config or generationConfig or Gate5B4C3ShadowGenerationConfig(),
        )
        object.__setattr__(
            self,
            "mocked_adk_primitives_loader",
            mockedAdkPrimitivesLoader
            if isinstance(mockedAdkPrimitivesLoader, Gate5B4C3MockedAdkPrimitivesLoader)
            else None,
        )
        object.__setattr__(self, "counter_store", counterStore)


__all__ = [
    "Gate5B4C3MockedAdkPrimitivesLoader",
    "Gate5B4C3ShadowGenerationRouteConfig",
]
