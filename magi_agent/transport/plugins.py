from __future__ import annotations

from secrets import compare_digest
from typing import TYPE_CHECKING, Any

from fastapi import Request
from fastapi.responses import JSONResponse

from magi_agent.plugins.audit import build_plugin_audit_snapshot
from magi_agent.plugins.manager import PluginStatus

if TYPE_CHECKING:
    from fastapi import FastAPI

    from magi_agent.runtime.openmagi_runtime import OpenMagiRuntime


def register_plugin_admin_routes(app: FastAPI, runtime: OpenMagiRuntime) -> None:
    @app.get("/v1/admin/plugins")
    async def list_plugins(request: Request) -> JSONResponse:
        unauthorized = _unauthorized_response(request, runtime)
        if unauthorized is not None:
            return unauthorized
        return JSONResponse(
            content={
                "plugins": [
                    _public_plugin_status(status) for status in runtime.plugin_state.plugins
                ],
                "trafficAttached": False,
                "executionAttached": False,
            }
        )

    @app.get("/v1/admin/plugins/audit")
    async def plugin_audit(request: Request) -> JSONResponse:
        unauthorized = _unauthorized_response(request, runtime)
        if unauthorized is not None:
            return unauthorized
        snapshot = build_plugin_audit_snapshot(runtime.plugin_state)
        return JSONResponse(content=snapshot.model_dump(by_alias=True, mode="json"))

    @app.get("/v1/admin/plugins/{plugin_id}")
    async def plugin_detail(plugin_id: str, request: Request) -> JSONResponse:
        unauthorized = _unauthorized_response(request, runtime)
        if unauthorized is not None:
            return unauthorized
        status = _plugin_by_id(runtime, plugin_id)
        if status is None:
            return JSONResponse(
                status_code=404,
                content={
                    "error": "not_found",
                    "message": f'plugin "{plugin_id}" not found',
                },
            )
        return JSONResponse(content={"plugin": _public_plugin_status(status)})


def _plugin_by_id(runtime: OpenMagiRuntime, plugin_id: str) -> PluginStatus | None:
    for status in runtime.plugin_state.plugins:
        if status.plugin_id == plugin_id:
            return status
    return None


def _public_plugin_status(status: PluginStatus) -> dict[str, Any]:
    return {
        "pluginId": status.plugin_id,
        "kind": status.kind.value,
        "version": status.version,
        "installed": status.installed,
        "enabled": status.enabled,
        "optedOut": status.opted_out,
        "defaultInstalled": status.default_installed,
        "defaultEnabled": status.default_enabled,
        "optOutAllowed": status.opt_out_allowed,
        "securityCritical": status.security_critical,
        "auditRequired": status.audit_required,
        "statusReason": status.status_reason,
        "tools": [tool.name for tool in status.tools],
        "hooks": [hook.name for hook in status.hooks],
        "harnessRules": list(status.harness_rules),
        "secrets": [
            {
                "name": secret.name,
                "source": secret.source,
            }
            for secret in status.secrets
        ],
        "permissions": list(status.permissions),
        "services": list(status.services),
        "trafficAttached": False,
        "executionAttached": False,
    }


def _unauthorized_response(
    request: Request,
    runtime: OpenMagiRuntime,
) -> JSONResponse | None:
    token = request.headers.get("x-gateway-token")
    if token is not None and compare_digest(token, runtime.config.gateway_token):
        return None
    return JSONResponse(status_code=401, content={"error": "unauthorized"})
