from __future__ import annotations

import asyncio
import time
import types
import uuid
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from magi_agent.customize.apply import apply_tool_overrides, apply_verification_overrides
from magi_agent.customize.catalog import build_catalog
from magi_agent.customize.custom_rules import validate_custom_rule
from magi_agent.customize.shacl_compiler import (
    _resolve_shacl_compile_factory,
    available_fields,
    compile_nl_to_shacl,
    explain_shape,
    preview_cases,
    review_compilation,
)
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

# DoS caps for the compile route.
_MAX_PREVIEW_RECORDS = 50
_MAX_NL_TEXT_BYTES = 20_000

# Anti-loop cap: reject priorTurns if it already contains this many user turns.
# Rationale: each round = 1 user turn + 1 assistant clarification.  If the caller
# already has N≥3 user turns in priorTurns, the NEXT (current) turn would be
# round N+1, pushing well past the 3-round bound.  We cap BEFORE calling the
# compiler so the compiler never sees an unbounded context.
_MAX_COMPILE_ROUNDS = 3

# LLM call timeout (seconds) — fires through asyncio.wait_for; the existing
# except-Exception paths already degrade gracefully on timeout.
_LLM_CALL_TIMEOUT_S = 30


def _make_json_safe(obj: Any) -> Any:
    """Recursively convert non-JSON-serializable objects to plain Python primitives.

    Specifically handles ``types.MappingProxyType`` (from ``EvidenceRecord.fields``
    freezing) and Pydantic models with a ``model_dump`` method.  All other unknown
    types are coerced to their string representation.
    """
    if isinstance(obj, types.MappingProxyType):
        return {k: _make_json_safe(v) for k, v in obj.items()}
    if isinstance(obj, dict):
        return {k: _make_json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_make_json_safe(item) for item in obj]
    if hasattr(obj, "model_dump"):
        # Pydantic v2 model (e.g. EvidenceRecord).
        try:
            return _make_json_safe(obj.model_dump(by_alias=True, mode="python"))
        except Exception:  # noqa: BLE001
            pass
    if isinstance(obj, (str, int, float, bool)) or obj is None:
        return obj
    # Fallback: coerce unknown types to string.
    return str(obj)


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

    @app.post("/v1/app/customize/custom-rules/compile")
    async def compile_custom_rule(request: Request) -> JSONResponse:
        """Preview-only NL→SHACL compiler endpoint.

        NEVER saves the compiled shape — saving is done by the caller via
        PUT /custom-rules after the user reviews the preview.  This endpoint
        is gated behind ``MAGI_SHACL_COMPILER_ENABLED`` (default OFF).

        Request body (JSON):
          nlText        str   — Natural-language constraint description.
                                Must be non-empty and ≤ _MAX_NL_TEXT_BYTES bytes.
          sampleRecords list  — Optional EvidenceRecord dicts for preview_cases.
                                Capped at _MAX_PREVIEW_RECORDS entries; excess
                                entries are dropped and previewTruncated=True is
                                set in the response.
          _shaclModelFactory  — TEST-ONLY: inject a fake model factory; ignored
                                in production (not a real JSON-serializable key
                                in prod; tests inject via monkeypatch).

        Returns:
          200 {ok:True, shapeTtl, review, explanation, previewCases, previewTruncated?}
              on success.
          200 {ok:False, error} on compile failure, invalid input, or unavailable model.
          401 auth failure (always before the flag check).
          200 {ok:False, error:"compiler disabled"} when flag is OFF (auth passes first).
        """
        # Auth check FIRST — matches every other route in this file.
        # An unauthenticated caller must never be able to probe flag state.
        unauthorized = _unauthorized_response(request, runtime)
        if unauthorized is not None:
            return unauthorized

        # Guard: flag must be ON.
        try:
            from magi_agent.config.flags import flag_bool  # noqa: PLC0415

            compiler_enabled = flag_bool("MAGI_SHACL_COMPILER_ENABLED")
        except Exception:  # noqa: BLE001
            compiler_enabled = False

        if not compiler_enabled:
            return JSONResponse(
                content={"ok": False, "error": "compiler disabled"},
                status_code=200,
            )

        # Parse body.
        try:
            body = await request.json()
        except Exception:
            return JSONResponse(
                status_code=400, content={"ok": False, "error": "invalid_json"}
            )
        if not isinstance(body, dict) or not isinstance(body.get("nlText"), str):
            return JSONResponse(
                status_code=400, content={"ok": False, "error": "nlText_required"}
            )

        nl_text: str = body["nlText"]

        # I1: reject empty/whitespace-only nlText and enforce byte-length cap.
        if not nl_text.strip():
            return JSONResponse(
                status_code=400,
                content={"ok": False, "error": "nlText must not be empty"},
            )
        if len(nl_text.encode()) > _MAX_NL_TEXT_BYTES:
            return JSONResponse(
                status_code=400,
                content={
                    "ok": False,
                    "error": f"nlText exceeds {_MAX_NL_TEXT_BYTES}-byte limit",
                },
            )

        sample_records_raw: list = body.get("sampleRecords") or []

        # C1 part A: cap sampleRecords to _MAX_PREVIEW_RECORDS.
        preview_truncated = False
        if len(sample_records_raw) > _MAX_PREVIEW_RECORDS:
            sample_records_raw = sample_records_raw[:_MAX_PREVIEW_RECORDS]
            preview_truncated = True

        # --- priorTurns validation ---
        # If the body key is not a list, ignore it entirely (backward-compatible).
        raw_prior_turns = body.get("priorTurns")
        validated_prior_turns: list[dict] = []
        if isinstance(raw_prior_turns, list):
            total_content_bytes = 0
            for element in raw_prior_turns:
                # Each element must be a dict with a valid role and a non-empty str content.
                if not isinstance(element, dict):
                    continue
                role = element.get("role")
                content = element.get("content")
                if role not in ("user", "assistant"):
                    continue
                if not isinstance(content, str) or not content:
                    continue
                # Per-element content byte-length cap (same limit as nlText).
                content_bytes = len(content.encode())
                if content_bytes > _MAX_NL_TEXT_BYTES:
                    continue  # silently skip oversized individual elements
                total_content_bytes += content_bytes
                validated_prior_turns.append({"role": role, "content": content})

            # DoS guard: total bytes across all content fields.
            if total_content_bytes > 5 * _MAX_NL_TEXT_BYTES:
                return JSONResponse(
                    status_code=400,
                    content={"ok": False, "error": "priorTurns total content too large"},
                )

        # Anti-loop cap: count validated user turns; reject if already at/above limit.
        validated_user_turn_count = sum(
            1 for t in validated_prior_turns if t["role"] == "user"
        )
        if validated_user_turn_count >= _MAX_COMPILE_ROUNDS:
            return JSONResponse(
                status_code=400,
                content={
                    "ok": False,
                    "error": "too many conversation rounds — try raw mode",
                },
            )

        # Resolve the compiler model factory (test-injection → production → fail-open).
        factory = _resolve_shacl_compile_factory(body)

        try:
            # Step 1: Compile NL → SHACL TTL (with LLM timeout).
            fields = available_fields()
            compile_result = await asyncio.wait_for(
                compile_nl_to_shacl(
                    nl_text,
                    fields,
                    model_factory=factory,
                    prior_turns=tuple(validated_prior_turns),
                ),
                timeout=_LLM_CALL_TIMEOUT_S,
            )

            # --- clarifyingQuestions branch (Task 5.2) ---
            # If the compiler returned clarifying questions, forward them directly
            # to the caller without running reviewer / explain / preview.
            # The clarifyingQuestions value is a tuple in the compiler result; convert
            # to a list for JSON serialization (no MappingProxyType leakage).
            if compile_result.get("clarifyingQuestions"):
                questions = list(compile_result["clarifyingQuestions"])
                return JSONResponse(
                    content={
                        "ok": False,
                        "clarifyingQuestions": questions,
                        "shapeTtl": None,
                        "error": None,
                    }
                )

            if not compile_result.get("ok"):
                return JSONResponse(
                    content={
                        "ok": False,
                        "error": compile_result.get("error", "compilation failed"),
                    }
                )

            shape_ttl: str = compile_result["shapeTtl"]

            # Step 2: Review + explain (with LLM timeout each).
            review_result = await asyncio.wait_for(
                review_compilation(nl_text, shape_ttl, fields, model_factory=factory),
                timeout=_LLM_CALL_TIMEOUT_S,
            )
            explanation = await asyncio.wait_for(
                explain_shape(shape_ttl, model_factory=factory),
                timeout=_LLM_CALL_TIMEOUT_S,
            )

            # Step 3: preview_cases if sampleRecords provided.
            # C1 part B: offload the blocking SHACL validation to a thread so the
            # event loop is not blocked by run_shacl_rule's ThreadPoolExecutor calls.
            preview: list[dict] = []
            if sample_records_raw:
                # Convert each raw dict to an EvidenceRecord.  The HTTP body uses
                # a simplified format: {type, status, fields?, ...}.  Required
                # fields (observedAt, source) are filled in by the route from the
                # request time; extra/unknown keys are ignored.
                try:
                    from magi_agent.evidence.types import (  # noqa: PLC0415
                        EvidenceRecord,
                        EvidenceSource,
                    )

                    observed_at = int(time.time() * 1000)
                    _default_source = EvidenceSource(kind="verifier")
                    records = []
                    invalid_indices: list[int] = []
                    for idx, raw in enumerate(sample_records_raw):
                        if not isinstance(raw, dict):
                            invalid_indices.append(idx)
                            continue
                        try:
                            rec = EvidenceRecord(
                                type=str(raw.get("type", "")),
                                status=raw.get("status", "ok"),  # type: ignore[arg-type]
                                observedAt=raw.get("observedAt", observed_at),
                                source=_default_source,
                                fields=raw.get("fields") or {},
                            )
                            records.append((idx, rec))
                        except Exception as rec_exc:  # noqa: BLE001
                            # Include a per-case error entry so the caller knows
                            # the record was skipped, rather than silently dropping.
                            invalid_indices.append(idx)
                            preview.append({
                                "recordIndex": idx,
                                "conforms": None,
                                "status": "invalid_record",
                                "error": str(rec_exc),
                                "violations": [],
                            })
                except Exception:  # noqa: BLE001
                    records = []

                if records:
                    just_records = [rec for _idx, rec in records]
                    # Offload blocking SHACL validation (ThreadPoolExecutor inside
                    # run_shacl_rule) off the asyncio event loop.
                    raw_preview = await asyncio.to_thread(
                        preview_cases, shape_ttl, just_records, observed_at=observed_at
                    )
                    # Merge valid results with any per-case error entries from above.
                    for (rec_idx, _rec), case in zip(records, raw_preview):
                        safe_case = _make_json_safe(case)
                        safe_case["recordIndex"] = rec_idx
                        preview.append(safe_case)
                    # Sort by recordIndex so the response ordering is stable.
                    preview.sort(key=lambda c: c.get("recordIndex", 0))

            response_payload: dict[str, Any] = {
                "ok": True,
                "shapeTtl": shape_ttl,
                "review": _make_json_safe(review_result),
                "explanation": explanation,
                "previewCases": preview,
            }
            if preview_truncated:
                response_payload["previewTruncated"] = True
            return JSONResponse(content=response_payload)

        except Exception as exc:  # noqa: BLE001 — never raise from compile route
            return JSONResponse(
                content={"ok": False, "error": f"compile error: {exc}"}
            )
