from __future__ import annotations

from json import JSONDecodeError
from typing import TYPE_CHECKING

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from magi_agent.shadow.gate5b4c2_shadow_invocation_contract import (
    Gate5B4C2ShadowGateConfig,
    Gate5B4C2ShadowInvocationRequest,
    build_gate5b4c2_shadow_invocation_receipt,
)

if TYPE_CHECKING:
    from magi_agent.runtime.openmagi_runtime import OpenMagiRuntime


def register_shadow_invocation_routes(app: FastAPI, runtime: OpenMagiRuntime) -> None:
    @app.post("/v1/internal/gate5b/shadow-invocations")
    async def gate5b_shadow_invocations(request: Request) -> JSONResponse:
        try:
            payload = await request.json()
            invocation = Gate5B4C2ShadowInvocationRequest.model_validate(payload)
        except (JSONDecodeError, ValidationError, ValueError):
            return JSONResponse(
                status_code=422,
                content={
                    "error": "invalid_shadow_invocation_contract",
                    "responseAuthority": "typescript",
                    "diagnosticOnly": True,
                },
            )

        receipt = build_gate5b4c2_shadow_invocation_receipt(
            invocation,
            config=Gate5B4C2ShadowGateConfig(),
        )
        return JSONResponse(
            status_code=200,
            content=receipt.model_dump(by_alias=True, mode="json"),
        )
