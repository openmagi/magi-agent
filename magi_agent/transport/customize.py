from __future__ import annotations

import uuid

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from magi_agent.customize.apply import apply_tool_overrides, apply_verification_overrides
from magi_agent.customize.catalog import build_catalog
from magi_agent.customize.custom_rules import validate_custom_rule
from magi_agent.customize.store import (
    delete_custom_rule,
    load_overrides,
    set_custom_rule,
    set_tool_override,
    set_user_rules,
    set_verification_override,
)
from magi_agent.runtime.openmagi_runtime import OpenMagiRuntime
from magi_agent.transport.tools import _unauthorized_response

_VERIFICATION_KINDS = {"recipes", "harness_presets", "hooks"}


def register_customize_routes(app: FastAPI, runtime: OpenMagiRuntime) -> None:
    @app.get("/v1/app/customize")
    async def get_customize(request: Request) -> JSONResponse:
        unauthorized = _unauthorized_response(request, runtime)
        if unauthorized is not None:
            return unauthorized
        return JSONResponse(
            content={
                "catalog": build_catalog(runtime),
                "overrides": load_overrides(),
            }
        )

    @app.patch("/v1/app/customize/tools/{name}")
    async def patch_tool(name: str, request: Request) -> JSONResponse:
        unauthorized = _unauthorized_response(request, runtime)
        if unauthorized is not None:
            return unauthorized
        try:
            body = await request.json()
        except Exception:
            return JSONResponse(status_code=400, content={"error": "invalid_json"})
        if not isinstance(body, dict) or not isinstance(body.get("enabled"), bool):
            return JSONResponse(status_code=400, content={"error": "enabled_bool_required"})
        enabled = body["enabled"]
        if runtime.tool_registry.resolve_registration(name) is None:
            return JSONResponse(
                status_code=404,
                content={"error": "not_found", "message": f'tool "{name}" not found'},
            )
        overrides = set_tool_override(name, enabled)
        apply_tool_overrides(runtime, {"tools": {name: enabled}})
        return JSONResponse(content={"overrides": overrides})

    @app.patch("/v1/app/customize/verification/{kind}/{item_id}")
    async def patch_verification(kind: str, item_id: str, request: Request) -> JSONResponse:
        unauthorized = _unauthorized_response(request, runtime)
        if unauthorized is not None:
            return unauthorized
        if kind not in _VERIFICATION_KINDS:
            return JSONResponse(status_code=400, content={"error": "unknown_kind"})
        try:
            body = await request.json()
        except Exception:
            return JSONResponse(status_code=400, content={"error": "invalid_json"})
        if not isinstance(body, dict) or not isinstance(body.get("enabled"), bool):
            return JSONResponse(status_code=400, content={"error": "enabled_bool_required"})
        mode = body["mode"] if isinstance(body.get("mode"), str) else None
        overrides = set_verification_override(kind, item_id, body["enabled"], mode=mode)
        apply_verification_overrides(runtime, overrides)
        return JSONResponse(content={"overrides": overrides})

    @app.put("/v1/app/customize/rules")
    async def put_rules(request: Request) -> JSONResponse:
        unauthorized = _unauthorized_response(request, runtime)
        if unauthorized is not None:
            return unauthorized
        try:
            body = await request.json()
        except Exception:
            return JSONResponse(status_code=400, content={"error": "invalid_json"})
        if not isinstance(body, dict) or not isinstance(body.get("text"), str):
            return JSONResponse(status_code=400, content={"error": "text_required"})
        overrides = set_user_rules(body["text"])
        apply_verification_overrides(runtime, overrides)
        return JSONResponse(content={"overrides": overrides})

    @app.put("/v1/app/customize/custom-rules")
    async def put_custom_rule(request: Request) -> JSONResponse:
        unauthorized = _unauthorized_response(request, runtime)
        if unauthorized is not None:
            return unauthorized
        try:
            body = await request.json()
        except Exception:
            return JSONResponse(status_code=400, content={"error": "invalid_json"})
        if not isinstance(body, dict):
            return JSONResponse(status_code=400, content={"error": "object_required"})
        errors = validate_custom_rule(body)
        if errors:
            return JSONResponse(
                status_code=400, content={"error": "invalid_custom_rule", "details": errors}
            )
        rule = dict(body)
        if not isinstance(rule.get("id"), str) or not rule["id"]:
            rule["id"] = f"cr_{uuid.uuid4().hex}"
        overrides = set_custom_rule(rule)
        apply_verification_overrides(runtime, overrides)
        return JSONResponse(content={"overrides": overrides, "id": rule["id"]})

    @app.delete("/v1/app/customize/custom-rules/{rule_id}")
    async def delete_custom_rule_route(rule_id: str, request: Request) -> JSONResponse:
        unauthorized = _unauthorized_response(request, runtime)
        if unauthorized is not None:
            return unauthorized
        overrides = delete_custom_rule(rule_id)
        apply_verification_overrides(runtime, overrides)
        return JSONResponse(content={"overrides": overrides})
