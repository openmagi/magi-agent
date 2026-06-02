from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import hashlib
import json
from json import JSONDecodeError

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from magi_agent.shadow.gate5b4c3_runner_input_adapter import (
    build_gate5b4c3_runner_input,
)
from magi_agent.shadow.gate5b4c3_shadow_generation_contract import (
    Gate5B4C3ShadowGenerationConfig,
    Gate5B4C3ShadowGenerationDiagnostic,
    Gate5B4C3ShadowGenerationRequest,
    build_gate5b4c3_shadow_generation_diagnostic,
)
from magi_agent.shadow.gate5b4c3_shadow_comparison import (
    build_gate5b4c3_shadow_comparison_artifact,
)
from magi_agent.shadow.gate5b4c3_shadow_counter_store import (
    Gate5B4C3ShadowCounterReservation,
    Gate5B4C3ShadowCounterState,
    Gate5B4C3ShadowCounterStore,
)
from magi_agent.shadow.gate5b4c3_shadow_generation_report import (
    Gate5B4C3ShadowGenerationRunnerReport,
    build_gate5b4c3_shadow_generation_report,
)


_INVALID_SHADOW_GENERATION_RESPONSE = {
    "error": "invalid_shadow_generation_contract",
    "responseAuthority": "typescript",
    "diagnosticOnly": True,
}


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


def register_shadow_generation_routes(app: FastAPI, runtime: object) -> None:
    @app.post("/v1/internal/gate5b/shadow-generations")
    async def gate5b_shadow_generations(request: Request) -> JSONResponse:
        try:
            payload = await request.json()
            _reject_true_authority_flags(payload)
            generation = Gate5B4C3ShadowGenerationRequest.model_validate(payload)
        except (JSONDecodeError, ValidationError, ValueError):
            return JSONResponse(
                status_code=422,
                content=_INVALID_SHADOW_GENERATION_RESPONSE,
            )

        route_config = _route_config(runtime)
        if route_config.mocked_runner_boundary_enabled:
            diagnostic_or_report = await _run_mocked_live_boundary(generation, route_config)
            if diagnostic_or_report is not None:
                return JSONResponse(
                    status_code=200,
                    content=diagnostic_or_report.model_dump(by_alias=True, mode="json"),
                )
        if route_config.live_runner_boundary_enabled:
            diagnostic_or_report = await _run_live_boundary(generation, route_config)
            if diagnostic_or_report is not None:
                return JSONResponse(
                    status_code=200,
                    content=diagnostic_or_report.model_dump(by_alias=True, mode="json"),
                )

        diagnostic = build_gate5b4c3_shadow_generation_diagnostic(
            generation,
            config=route_config.generation_config,
        )
        return JSONResponse(
            status_code=200,
            content=diagnostic.model_dump(by_alias=True, mode="json"),
        )


def _reject_true_authority_flags(payload: object) -> None:
    if not isinstance(payload, Mapping):
        raise ValueError("shadow generation payload must be an object")
    authority = payload.get("authority")
    if authority is None:
        return
    if not isinstance(authority, Mapping):
        raise ValueError("shadow generation authority flags must be an object")
    for value in authority.values():
        if value is not False:
            raise ValueError("shadow generation authority flags cannot be true")


async def run_gate5b4c3_live_runner_boundary_async(
    generation: Gate5B4C3ShadowGenerationRequest,
    *,
    config: Gate5B4C3ShadowGenerationConfig,
    adk_primitives_loader: Callable[[], object] | None = None,
) -> object:
    from magi_agent.shadow.gate5b4c3_live_runner_boundary import (
        run_gate5b4c3_live_runner_boundary_async as invoke_live_boundary,
    )

    return await invoke_live_boundary(
        generation,
        config=config,
        adk_primitives_loader=adk_primitives_loader,
    )


async def _run_live_boundary(
    generation: Gate5B4C3ShadowGenerationRequest,
    route_config: Gate5B4C3ShadowGenerationRouteConfig,
) -> Gate5B4C3ShadowGenerationDiagnostic | Gate5B4C3ShadowGenerationRunnerReport | None:
    diagnostic = build_gate5b4c3_shadow_generation_diagnostic(
        generation,
        config=route_config.generation_config,
    )
    if not diagnostic.accepted:
        return diagnostic
    reservation = _reserve_counter_or_report(generation, diagnostic, route_config, required=True)
    if reservation is None:
        return _counter_unavailable_report(generation, diagnostic)
    if isinstance(reservation, Gate5B4C3ShadowGenerationRunnerReport):
        return reservation

    try:
        boundary_result = await run_gate5b4c3_live_runner_boundary_async(
            generation,
            config=route_config.generation_config,
        )
        report = _report_from_boundary(generation, boundary_result)
    except Exception as exc:
        report = build_gate5b4c3_shadow_generation_report(
            diagnostic=diagnostic,
            status="error",
            reason="runner_error",
            error_class=type(exc).__name__,
            error_preview=str(exc),
            **_report_metadata(generation),
        )
    return _finalize_countered_report(
        generation=generation,
        report=report,
        reservation=reservation,
        counter_store=route_config.counter_store,
    )


async def _run_mocked_live_boundary(
    generation: Gate5B4C3ShadowGenerationRequest,
    route_config: Gate5B4C3ShadowGenerationRouteConfig,
) -> Gate5B4C3ShadowGenerationDiagnostic | Gate5B4C3ShadowGenerationRunnerReport | None:
    diagnostic = build_gate5b4c3_shadow_generation_diagnostic(
        generation,
        config=route_config.generation_config,
    )
    if not diagnostic.accepted:
        return diagnostic
    reservation = _reserve_counter_or_report(generation, diagnostic, route_config, required=False)
    if isinstance(reservation, Gate5B4C3ShadowGenerationRunnerReport):
        return reservation
    if route_config.mocked_adk_primitives_loader is None:
        report = build_gate5b4c3_shadow_generation_report(
            diagnostic=diagnostic,
            status="error",
            reason="runner_error",
            error_class="MissingMockedAdkPrimitivesLoader",
            error_preview="Mocked ADK primitives loader is required for Gate 5B-4c-3d-5.",
            **_report_metadata(generation),
        )
        if isinstance(reservation, Gate5B4C3ShadowCounterReservation):
            return _finalize_countered_report(
                generation=generation,
                report=report,
                reservation=reservation,
                counter_store=route_config.counter_store,
            )
        return report

    boundary_result = await run_gate5b4c3_live_runner_boundary_async(
        generation,
        config=route_config.generation_config,
        adk_primitives_loader=route_config.mocked_adk_primitives_loader.loader,
    )
    report = _report_from_boundary(generation, boundary_result)
    if isinstance(reservation, Gate5B4C3ShadowCounterReservation):
        return _finalize_countered_report(
            generation=generation,
            report=report,
            reservation=reservation,
            counter_store=route_config.counter_store,
        )
    return report


def _report_from_boundary(
    generation: Gate5B4C3ShadowGenerationRequest,
    boundary_result: object,
) -> Gate5B4C3ShadowGenerationRunnerReport:
    status, reason = _report_status_reason(boundary_result)
    return build_gate5b4c3_shadow_generation_report(
        diagnostic=boundary_result.diagnostic,
        status=status,
        reason=reason,
        adk_runner_invoked=boundary_result.adk_invoked,
        model_call_attempted=boundary_result.model_call_via_adk_runner_attempted,
        event_count=boundary_result.event_count,
        latency_ms=boundary_result.latency_ms,
        output_text=getattr(boundary_result, "output_text_internal", None),
        error_class=boundary_result.error_class,
        error_preview=boundary_result.error_preview,
        **_report_metadata(generation),
    )


def _reserve_counter_or_report(
    generation: Gate5B4C3ShadowGenerationRequest,
    diagnostic: Gate5B4C3ShadowGenerationDiagnostic,
    route_config: Gate5B4C3ShadowGenerationRouteConfig,
    *,
    required: bool,
) -> Gate5B4C3ShadowCounterReservation | Gate5B4C3ShadowGenerationRunnerReport | None:
    if route_config.counter_store is None:
        return None if required else None
    try:
        reservation = route_config.counter_store.reserve(
            request_digest=generation.request_id_digest,
            shadow_generation_id=generation.shadow_generation_id,
            selected_bot_digest=generation.selection.bot_id_digest,
            trusted_owner_user_id_digest=generation.selection.owner_user_id_digest,
            environment=generation.selection.environment,
            max_daily_generation_runs=generation.budgets.max_daily_generation_runs,
            max_daily_generation_cost_usd=generation.budgets.max_daily_generation_cost_usd,
            max_concurrent_generation_runs=generation.budgets.max_concurrent_generation_runs,
            max_pending_generation_runs=generation.budgets.max_pending_generation_runs,
            cost_cap_usd=generation.budgets.max_cost_usd,
        )
    except Exception as exc:
        return build_gate5b4c3_shadow_generation_report(
            diagnostic=diagnostic,
            status="error",
            reason="counter_store_error",
            counter_status="error",
            counter_reason="counter_store_error",
            idempotency_key_digest=generation.request_id_digest,
            error_class=type(exc).__name__,
            error_preview=str(exc),
            **_report_metadata(generation),
        )
    if reservation.status == "reserved":
        return reservation
    if reservation.status == "duplicate_replay":
        return build_gate5b4c3_shadow_generation_report(
            diagnostic=diagnostic,
            status="skipped",
            reason="idempotency_replay",
            counter_status=reservation.status,
            counter_reason=reservation.reason,
            counter_state=reservation.counter_state,
            idempotency_key_digest=generation.request_id_digest,
            comparison_artifact_digest=reservation.previous_comparison_artifact_digest,
            **_report_metadata(generation),
        )
    return build_gate5b4c3_shadow_generation_report(
        diagnostic=diagnostic,
        status="skipped",
        reason="counter_blocked",
        counter_status=reservation.status,
        counter_reason=reservation.reason,
        counter_state=reservation.counter_state,
        idempotency_key_digest=generation.request_id_digest,
        **_report_metadata(generation),
    )


def _counter_unavailable_report(
    generation: Gate5B4C3ShadowGenerationRequest,
    diagnostic: Gate5B4C3ShadowGenerationDiagnostic,
) -> Gate5B4C3ShadowGenerationRunnerReport:
    return build_gate5b4c3_shadow_generation_report(
        diagnostic=diagnostic,
        status="error",
        reason="counter_store_unavailable",
        counter_status="unavailable",
        counter_reason="counter_store_unavailable",
        idempotency_key_digest=generation.request_id_digest,
        error_class="Gate5B4C3ShadowCounterStoreUnavailable",
        error_preview="Durable shadow generation counters are required before live Runner entry.",
        **_report_metadata(generation),
    )


def _finalize_countered_report(
    *,
    generation: Gate5B4C3ShadowGenerationRequest,
    report: Gate5B4C3ShadowGenerationRunnerReport,
    reservation: Gate5B4C3ShadowCounterReservation,
    counter_store: Gate5B4C3ShadowCounterStore | None,
) -> Gate5B4C3ShadowGenerationRunnerReport:
    artifact = build_gate5b4c3_shadow_comparison_artifact(generation, report)
    finished_state_estimate = _counter_state_after_finish(reservation)
    final_report = report.model_copy(
        update={
            "counter_status": reservation.status,
            "counter_reason": reservation.reason,
            "counter_state": finished_state_estimate,
            "idempotency_key_digest": generation.request_id_digest,
            "comparison_status": artifact.comparison_status,
            "comparison_artifact_digest": artifact.artifact_digest,
        }
    )
    if counter_store is None:
        return final_report
    try:
        finished_state = counter_store.finish(
            reservation,
            status=final_report.status,
            reason=final_report.reason,
            report_digest=_report_digest(final_report),
            comparison_artifact_digest=artifact.artifact_digest,
        )
        return final_report.model_copy(update={"counter_state": finished_state})
    except Exception as exc:
        return final_report.model_copy(
            update={
                "status": "error",
                "reason": "counter_store_error",
                "counter_status": "error",
                "counter_reason": "counter_store_error",
                "error_class": type(exc).__name__,
                "error_preview": "Counter store finalization failed.",
            }
        )


def _counter_state_after_finish(
    reservation: Gate5B4C3ShadowCounterReservation,
) -> Gate5B4C3ShadowCounterState:
    return reservation.counter_state.model_copy(
        update={
            "in_flight_generation_runs": max(
                0,
                reservation.counter_state.in_flight_generation_runs - 1,
            ),
            "pending_generation_runs": max(
                0,
                reservation.counter_state.pending_generation_runs - 1,
            ),
        }
    )


def _report_digest(report: Gate5B4C3ShadowGenerationRunnerReport) -> str:
    encoded = json.dumps(
        report.model_dump(by_alias=True, mode="json", warnings=False),
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _report_status_reason(
    boundary_result: object,
) -> tuple[str, str]:
    if boundary_result.status == "completed":
        return "completed", "runner_completed"
    if boundary_result.reason == "runner_timeout":
        return "error", "runner_timeout"
    if boundary_result.reason in {"runner_error", "adk_primitives_error"}:
        return "error", "runner_error"
    if boundary_result.reason == "input_adapter_drop":
        return "dropped", "input_adapter_drop"
    return "skipped", "not_accepted"


def _report_metadata(generation: Gate5B4C3ShadowGenerationRequest) -> dict[str, object]:
    runner_input_result = build_gate5b4c3_runner_input(generation)
    runner_input = runner_input_result.runner_input
    return {
        "runner_timeout_ms": generation.budgets.python_runner_timeout_ms,
        "max_output_tokens": (
            runner_input.max_output_tokens if runner_input is not None else generation.budgets.max_output_tokens
        ),
        "max_estimated_input_tokens": generation.budgets.max_estimated_input_tokens,
        "max_total_estimated_tokens": generation.budgets.max_total_estimated_tokens,
        "routing_source": generation.model_routing.routing_source,
        "router_decision_digest": generation.model_routing.router_decision_digest,
        "routing_profile_digest": generation.model_routing.routing_profile_digest,
        "bot_config_model_digest": generation.model_routing.bot_config_model_digest,
        "fallback_approved": generation.model_routing.fallback_approved,
        "shadow_credential_ref": generation.model_routing.shadow_credential_ref,
        "retry_policy": generation.budgets.retry_policy,
        "cost_cap_usd": generation.budgets.max_cost_usd,
    }


def _route_config(runtime: object) -> Gate5B4C3ShadowGenerationRouteConfig:
    config = getattr(runtime, "gate5b4c3_shadow_generation_route_config", None)
    if isinstance(config, Gate5B4C3ShadowGenerationRouteConfig):
        return config
    return Gate5B4C3ShadowGenerationRouteConfig()


__all__ = [
    "Gate5B4C3MockedAdkPrimitivesLoader",
    "Gate5B4C3ShadowGenerationRouteConfig",
    "register_shadow_generation_routes",
]
