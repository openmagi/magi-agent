from __future__ import annotations

from secrets import compare_digest
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from magi_agent.runtime.openmagi_runtime import OpenMagiRuntime
from magi_agent.tools.manifest import ToolManifest


ZERO_TOOL_STATS = {
    "calls": 0,
    "errors": 0,
    "avgDurationMs": 0,
    "lastCallAt": 0,
}


def register_tool_admin_routes(app: FastAPI, runtime: OpenMagiRuntime) -> None:
    @app.get("/v1/admin/tools")
    async def list_tools(request: Request) -> JSONResponse:
        unauthorized = _unauthorized_response(request, runtime)
        if unauthorized is not None:
            return unauthorized
        return JSONResponse(content={"tools": _public_tools(runtime)})

    @app.get("/v1/admin/tools/stats")
    async def tool_stats(request: Request) -> JSONResponse:
        unauthorized = _unauthorized_response(request, runtime)
        if unauthorized is not None:
            return unauthorized
        stats = {
            manifest.name: dict(ZERO_TOOL_STATS)
            for manifest in runtime.tool_registry.list_all()
        }
        return JSONResponse(content={"stats": stats})

    @app.get("/v1/admin/tools/{name}")
    async def tool_detail(name: str, request: Request) -> JSONResponse:
        unauthorized = _unauthorized_response(request, runtime)
        if unauthorized is not None:
            return unauthorized
        registration = runtime.tool_registry.resolve_registration(name)
        if registration is None:
            return JSONResponse(
                status_code=404,
                content={
                    "error": "not_found",
                    "message": f'tool "{name}" not found',
                },
            )
        return JSONResponse(
            content={
                "tool": _public_tool_metadata(
                    registration.manifest,
                    enabled=registration.enabled,
                )
            }
        )


def _public_tools(runtime: OpenMagiRuntime) -> list[dict[str, Any]]:
    tools: list[dict[str, Any]] = []
    for manifest in runtime.tool_registry.list_all():
        registration = runtime.tool_registry.resolve_registration(manifest.name)
        enabled = registration.enabled if registration is not None else False
        tools.append(_public_tool_metadata(manifest, enabled=enabled))
    return tools


def _public_tool_metadata(manifest: ToolManifest, *, enabled: bool) -> dict[str, Any]:
    return {
        "name": manifest.name,
        "description": manifest.description,
        "permission": manifest.permission,
        "kind": manifest.kind,
        "enabled": enabled,
        "source": manifest.source.kind,
        "isConcurrencySafe": manifest.is_concurrency_safe,
        "dangerous": manifest.dangerous,
        "tags": list(manifest.tags),
        "inputSchema": manifest.input_schema,
        "outputSchema": manifest.output_schema,
        "timeoutMs": manifest.timeout_ms,
        "mutatesWorkspace": manifest.mutates_workspace,
        "availableInModes": list(manifest.available_in_modes),
        "shouldDefer": manifest.should_defer,
        "pluginId": manifest.plugin_id,
        "optOut": manifest.opt_out,
    }


def _unauthorized_response(
    request: Request,
    runtime: OpenMagiRuntime,
) -> JSONResponse | None:
    token = request.headers.get("x-gateway-token")
    if token is not None and compare_digest(token, runtime.config.gateway_token):
        return None
    return JSONResponse(status_code=401, content={"error": "unauthorized"})
