from __future__ import annotations

import asyncio
import os
import time
import types
import uuid
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from magi_agent.customize.apply import apply_tool_overrides, apply_verification_overrides
from magi_agent.customize.catalog import build_catalog
from magi_agent.customize.custom_rules import validate_custom_rule
from magi_agent.customize.live_catalog import build_live_catalog
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
    set_control_plane_override,
    set_custom_rule,
    set_tool_override,
    set_user_rules,
    set_verification_budgets,
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


def _is_seam_spec_enabled() -> bool:
    """Read ``MAGI_CUSTOMIZE_SEAM_SPEC_ENABLED`` defensively (PR-C2 flag)."""
    try:
        from magi_agent.config.flags import flag_bool  # noqa: PLC0415

        return flag_bool("MAGI_CUSTOMIZE_SEAM_SPEC_ENABLED")
    except Exception:  # noqa: BLE001
        return False


def _resolve_seam_compile_factory(body: dict) -> Any:
    """Resolve the SeamSpec compiler model factory.

    Mirrors :func:`magi_agent.customize.shacl_compiler._resolve_shacl_compile_factory`:
    test injection via ``body["_seamModelFactory"]`` wins; otherwise the SHACL
    production factory resolver is reused so a single provider config covers
    both NL compilers (handoff §5 explicit reuse of PR-A's resolver).

    Module-level (NOT a closure inside ``register_customize_routes``) so tests
    can ``monkeypatch.setattr(customize_transport, "_resolve_seam_compile_factory", ...)``
    to inject a fake — the same pattern the SHACL endpoint tests use.
    """
    if isinstance(body, dict):
        factory = body.get("_seamModelFactory")
        if callable(factory):
            return factory
    from magi_agent.customize.shacl_compiler import (  # noqa: PLC0415
        _production_shacl_compiler_model_factory,
    )

    return _production_shacl_compiler_model_factory()


def _is_nl_rule_compiler_enabled() -> bool:
    """Read ``MAGI_CUSTOMIZE_NL_RULE_COMPILER_ENABLED`` defensively (PR-D1)."""
    try:
        from magi_agent.config.flags import flag_bool  # noqa: PLC0415

        return flag_bool("MAGI_CUSTOMIZE_NL_RULE_COMPILER_ENABLED")
    except Exception:  # noqa: BLE001
        return False


def _is_nl_interview_mode_enabled() -> bool:
    """Read ``MAGI_CUSTOMIZE_NL_INTERVIEW_MODE_ENABLED`` defensively (PR-F-UX6).

    Default-OFF gate: when this is OFF the route preserves the legacy
    one-shot ``compile_with_review`` path byte-identically. When ON the
    route may route through ``compile_interview_step`` which emits two new
    response shapes (``mode: "interview"`` and ``mode: "proposal"``);
    legacy callers continue to see the success / clarifyingQuestions /
    error shapes for well-formed inputs.
    """
    try:
        from magi_agent.config.flags import flag_bool  # noqa: PLC0415

        return flag_bool("MAGI_CUSTOMIZE_NL_INTERVIEW_MODE_ENABLED")
    except Exception:  # noqa: BLE001
        return False


def _is_runtime_fields_endpoint_enabled() -> bool:
    """Read ``MAGI_CUSTOMIZE_RUNTIME_FIELDS_ENDPOINT_ENABLED`` (PR-F-UX2)."""
    try:
        from magi_agent.config.flags import flag_bool  # noqa: PLC0415

        return flag_bool("MAGI_CUSTOMIZE_RUNTIME_FIELDS_ENDPOINT_ENABLED")
    except Exception:  # noqa: BLE001
        return False


def _resolve_nl_rule_compile_factory(body: dict) -> Any:
    """Resolve the unified NL→Rule compiler model factory.

    Same shape as :func:`_resolve_seam_compile_factory`: test injection via
    ``body["_ruleModelFactory"]`` wins, otherwise the SHACL production
    resolver is reused so a single provider config covers all three NL
    compilers. Module-level so tests can monkeypatch it.
    """
    if isinstance(body, dict):
        factory = body.get("_ruleModelFactory")
        if callable(factory):
            return factory
    from magi_agent.customize.shacl_compiler import (  # noqa: PLC0415
        _production_shacl_compiler_model_factory,
    )

    return _production_shacl_compiler_model_factory()


def _lift_field_constraint_for_save(body: dict) -> dict:
    """Compile a ``field_constraint`` save payload into a ``shacl_constraint``.

    PR-F3 (2026-06-23): both the NL surface and the guided wizard surface
    author rules with a ``field_constraint`` IR — the design doc says
    "field_constraint validated as a shacl_constraint (same backend gate);
    no new runtime path needed". This helper performs that lift before
    validation so :func:`validate_custom_rule` (which only knows the four
    legacy kinds) sees the synthesised SHACL TTL.

    Two input shapes are lifted:

    (a) NL surface (kind == "field_constraint"):
        ``what.payload`` IS the IR. Compile it to TTL, wrap as
        ``shacl_constraint`` with the IR carried in ``payload.authoredAs``
        so the wizard can re-open the rule with chips.

    (b) Wizard surface (kind == "shacl_constraint" + payload.shapeTtl == ""
        + payload.authoredAs.kind == "field_constraint"): compile the
        nested IR into TTL and slot it into ``payload.shapeTtl``.

    Other shapes pass through untouched. Raises :class:`ValueError` if the
    IR is structurally invalid (unknown evidence type / unknown field /
    unknown operator) so the route can surface the reason as a 400.
    """
    if not isinstance(body, dict):
        return body
    what = body.get("what")
    if not isinstance(what, dict):
        return body
    kind = what.get("kind")
    payload = what.get("payload")
    if not isinstance(payload, dict):
        return body

    from magi_agent.customize.field_constraint_compiler import (  # noqa: PLC0415
        compile_to_shacl_ttl,
    )

    # Branch (a): NL kind == "field_constraint" — the payload IS the IR.
    if kind == "field_constraint":
        # Strip a nested ``authoredAs`` (if the NL caller pre-wrapped one)
        # so the compiler sees just the IR keys; we re-add a clean
        # authoredAs below.
        ir = {k: v for k, v in payload.items() if k != "authoredAs"}
        shape_ttl = compile_to_shacl_ttl(ir)
        lifted = dict(body)
        lifted["what"] = {
            "kind": "shacl_constraint",
            "payload": {
                "shapeTtl": shape_ttl,
                "authoredAs": {"kind": "field_constraint", **ir},
            },
        }
        return lifted

    # Branch (b): Wizard kind == "shacl_constraint" with empty shapeTtl and
    # an authoredAs IR carrying kind == "field_constraint".
    if kind == "shacl_constraint":
        shape_ttl = payload.get("shapeTtl")
        authored_as = payload.get("authoredAs")
        if (
            isinstance(authored_as, dict)
            and authored_as.get("kind") == "field_constraint"
            and (not isinstance(shape_ttl, str) or not shape_ttl.strip())
        ):
            ir = {k: v for k, v in authored_as.items() if k != "kind"}
            synthesised = compile_to_shacl_ttl(ir)
            lifted = dict(body)
            lifted_payload = {**payload, "shapeTtl": synthesised}
            # Keep authoredAs verbatim for round-trip.
            lifted["what"] = {
                "kind": "shacl_constraint",
                "payload": lifted_payload,
            }
            return lifted

    return body


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

    @app.get("/v1/app/customize/evidence/live-catalog")
    async def get_live_catalog(request: Request) -> JSONResponse:
        """Per-evidence-type live view fused from hints + ledger + WHAT-menu.

        Read-only, fail-open. Reports for each built-in evidence type the
        registered fields, the fields populated over the last 100 turns, the
        sample-population count, the WHAT-menu refs that surface the type, and
        the user-authored custom rules that reference one of those refs.

        Query params:
          sessionId   str (required)   ledger is partitioned per session
        """
        unauthorized = _unauthorized_response(request, runtime)
        if unauthorized is not None:
            return unauthorized
        session_id = request.query_params.get("sessionId")
        if not session_id:
            return JSONResponse(
                status_code=400, content={"error": "sessionId_required"}
            )
        try:
            view = build_live_catalog(session_id=session_id)
        except Exception:  # noqa: BLE001 — fail-open per spec
            view = {
                "evidenceTypes": [],
                "samplingWindow": "last 100 turns",
                "asOf": "",
            }
        return JSONResponse(content=_make_json_safe(view))

    @app.get("/v1/app/customize/runtime-fields")
    async def get_runtime_fields(request: Request) -> JSONResponse:
        """PR-F-UX2 (F8 core): runtime variable chips for the wizard picker.

        Returns the set of runtime variables the operator can reference in
        a wizard text input (regex pattern, contentMatch, llm_criterion
        criterion, SHACL TTL) for the given (lifecycle, condition, tool?)
        tuple. The chip menu mirrors the runtime gate's actual signature so
        the operator never authors against a field the runtime cannot honor.

        Query params:
          lifecycle   str (required)  one of the wizard's Lifecycle values
                                      (before_tool_use, after_tool_use,
                                      pre_final, on_user_prompt_submit,
                                      on_subagent_stop, spawn).
          condition   str (required)  the wizard's conditionKind value
                                      (regex, contentMatch, path,
                                      path_allowlist, domain, ...).
          tool        str (optional)  tool name; when given, tool_input.*
                                      expands the tool's manifest
                                      input_schema properties.

        Read-only, fail-open: an unknown (lifecycle, condition) tuple
        returns ``{fields: [], context: ..., source: 'unknown'}`` rather
        than 4xx/5xx so the chip list silently degrades to "no chips".

        Gated by ``MAGI_CUSTOMIZE_RUNTIME_FIELDS_ENDPOINT_ENABLED`` so a
        fresh install / hosted serve does not expose the surface until the
        operator explicitly opts in. Lab profile enables it via
        :data:`LAB_EXPERIMENTAL_FLAGS`.
        """
        unauthorized = _unauthorized_response(request, runtime)
        if unauthorized is not None:
            return unauthorized
        if not _is_runtime_fields_endpoint_enabled():
            return JSONResponse(
                status_code=404,
                content={"error": "runtime_fields_endpoint_disabled"},
            )
        lifecycle = request.query_params.get("lifecycle") or ""
        condition = request.query_params.get("condition") or ""
        tool = request.query_params.get("tool")
        if not lifecycle or not condition:
            return JSONResponse(
                status_code=400,
                content={"error": "lifecycle_and_condition_required"},
            )
        try:
            from magi_agent.customize.runtime_fields import (  # noqa: PLC0415
                fields_for_context,
            )

            fields = fields_for_context(
                lifecycle,
                condition,
                tool=tool,
                tool_registry=runtime.tool_registry,
            )
        except Exception:  # noqa: BLE001 — fail-open per spec
            fields = []
        context = f"{lifecycle}/{condition}"
        if tool:
            context = f"{context}/{tool}"
        source = "fields_for_context" if fields else "unknown"
        return JSONResponse(
            content={
                "fields": fields,
                "context": context,
                "source": source,
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

    @app.patch("/v1/app/customize/control-plane/{behavior_id}")
    async def patch_control_plane(behavior_id: str, request: Request) -> JSONResponse:
        unauthorized = _unauthorized_response(request, runtime)
        if unauthorized is not None:
            return unauthorized
        try:
            body = await request.json()
        except Exception:
            return JSONResponse(status_code=400, content={"error": "invalid_json"})
        if not isinstance(body, dict) or not isinstance(body.get("enabled"), bool):
            return JSONResponse(status_code=400, content={"error": "enabled_bool_required"})
        from magi_agent.customize.control_plane_overrides import (  # noqa: PLC0415
            CONTROL_PLANE_BEHAVIORS,
            apply_control_plane_overrides_to_env,
        )

        if behavior_id not in {b.id for b in CONTROL_PLANE_BEHAVIORS}:
            return JSONResponse(
                status_code=404,
                content={"error": "not_found", "message": f'behavior "{behavior_id}" not found'},
            )
        enabled = body["enabled"]
        overrides = set_control_plane_override(behavior_id, enabled)
        # Project onto the live process env so the next turn's control-plane
        # build reflects the toggle without a restart (build_*_controls read
        # ``dict(os.environ)`` per call). Overwrite beats the profile seed.
        apply_control_plane_overrides_to_env(
            os.environ, {"control_plane": {behavior_id: enabled}}
        )
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

        # PR-F3 lift: validate_custom_rule only knows {deterministic_ref,
        # tool_perm, llm_criterion, shacl_constraint}. The two NL/wizard
        # authoring surfaces for field_constraint deliver:
        #   (a) NL  : what.kind == "field_constraint"
        #   (b) Wiz : what.kind == "shacl_constraint" + payload.authoredAs.kind
        #             == "field_constraint" + payload.shapeTtl == ""
        # In both cases compile the structured IR deterministically to a
        # SHACL shape *before* validation so the same backend gate (the
        # shacl_constraint validator) does the structural check. Persist as
        # shacl_constraint so the runtime gate fires unchanged; preserve the
        # authored IR in payload.authoredAs for round-trip.
        try:
            body = _lift_field_constraint_for_save(body)
        except ValueError as exc:
            return JSONResponse(
                status_code=400,
                content={
                    "error": "invalid_custom_rule",
                    "details": [str(exc)],
                },
            )

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

    # ------------------------------------------------------------------
    # PR-F7 — Customize budgets routes. Surface the per-bot cost vocabulary
    # (max tool calls per turn / max-steps brake / loop-guard hard) so the
    # dashboard can author MAGI_* overrides as Customize state. The applier
    # (governed_turn._maybe_apply_customize_budgets) projects the persisted
    # values onto the live env at turn entry via ``setdefault`` so an explicit
    # operator env always wins; the GET surface includes the resolved env
    # snapshot so the UI can flag "your dashboard save is dormant because
    # this env is pinned elsewhere".
    # ------------------------------------------------------------------

    @app.get("/v1/app/customize/budgets")
    async def get_customize_budgets(request: Request) -> JSONResponse:
        """Return the persisted budgets + effective env snapshot (PR-F7).

        Response shape: ``{"budgets": {...}, "effectiveEnv": {...}}``. The
        ``effectiveEnv`` value reflects what the runtime reads at turn entry
        for each budget's underlying MAGI_* env; ``null`` means the env is
        unset (so the dashboard save will take effect at the next turn).
        """
        unauthorized = _unauthorized_response(request, runtime)
        if unauthorized is not None:
            return unauthorized
        from magi_agent.customize.budgets_apply import (  # noqa: PLC0415
            BUDGET_ENV_MAP,
            effective_budget_envs,
        )

        overrides = load_overrides()
        budgets = (
            overrides.get("verification", {}).get("budgets", {})
            if isinstance(overrides, dict)
            else {}
        )
        return JSONResponse(
            content={
                "budgets": budgets,
                "effectiveEnv": effective_budget_envs(os.environ),
                "envMap": dict(BUDGET_ENV_MAP),
            }
        )

    @app.put("/v1/app/customize/budgets")
    async def put_customize_budgets(request: Request) -> JSONResponse:
        """Replace the persisted ``verification.budgets`` dict (PR-F7).

        Body: ``{"budgets": {budgetName: positiveInt, ...}}``. Unknown budget
        names are rejected so a typo cannot silently land in customize.json;
        non-positive / non-int / boolean values are rejected. On success the
        new overrides are persisted, applied to the live runtime policy via
        :func:`apply_verification_overrides`, and the response mirrors GET so
        the dashboard can refresh its read-only ``effectiveEnv`` snapshot.
        """
        unauthorized = _unauthorized_response(request, runtime)
        if unauthorized is not None:
            return unauthorized
        try:
            body = await request.json()
        except Exception:
            return JSONResponse(status_code=400, content={"error": "invalid_json"})
        if not isinstance(body, dict):
            return JSONResponse(
                status_code=400, content={"error": "object_required"}
            )
        budgets_raw = body.get("budgets")
        if not isinstance(budgets_raw, dict):
            return JSONResponse(
                status_code=400,
                content={"error": "budgets_object_required"},
            )

        from magi_agent.customize.budgets_apply import (  # noqa: PLC0415
            BUDGET_ENV_MAP,
            effective_budget_envs,
        )

        sanitized: dict[str, int] = {}
        errors: list[str] = []
        for key, value in budgets_raw.items():
            if not isinstance(key, str) or key not in BUDGET_ENV_MAP:
                errors.append(f"unknown budget: {key!r}")
                continue
            if isinstance(value, bool) or not isinstance(value, int):
                errors.append(f"{key}: must be a positive integer")
                continue
            if value <= 0:
                errors.append(f"{key}: must be > 0 (got {value})")
                continue
            sanitized[key] = value
        if errors:
            return JSONResponse(
                status_code=400,
                content={"error": "invalid_budgets", "details": errors},
            )

        overrides = set_verification_budgets(sanitized)
        apply_verification_overrides(runtime, overrides)
        return JSONResponse(
            content={
                "overrides": overrides,
                "budgets": overrides["verification"]["budgets"],
                "effectiveEnv": effective_budget_envs(os.environ),
                "envMap": dict(BUDGET_ENV_MAP),
            }
        )

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
        _MAX_PRIOR_TURNS = 10  # defensive O(n) cap before iteration (> round cap 3, leaves slack)
        raw_prior_turns = body.get("priorTurns")
        validated_prior_turns: list[dict] = []
        if isinstance(raw_prior_turns, list):
            # Upfront slice to bound iteration regardless of how many elements the client sends.
            raw_prior_turns = raw_prior_turns[:_MAX_PRIOR_TURNS]
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
                # Early-exit DoS guard: stop accumulating as soon as total bytes exceed limit.
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

        # Item 3 hardening — aggregate text cap (NL + prior turn content).
        # Maps PrecheckError to HTTP 422 so a pathological payload fails fast
        # and deterministically, before the LLM is invoked.
        from magi_agent.customize.shacl_compiler import (
            MAX_AGGREGATE_TEXT,
            PrecheckError,
            _precheck_aggregate,
        )

        try:
            _precheck_aggregate(nl_text, tuple(validated_prior_turns))
        except PrecheckError as exc:
            return JSONResponse(
                status_code=422,
                content={
                    "ok": False,
                    "error": str(exc),
                    "limit": MAX_AGGREGATE_TEXT,
                },
            )

        # Resolve the compiler model factory (test-injection → production → fail-open).
        # Distinct compiler vs reviewer callables: the orchestrator's reviewer-guard
        # rejects same-object self-review (handoff §2). Wrapping the underlying
        # resolver in two separate lambdas gives the guard the identity-distinct
        # callables it needs while keeping the upstream resolution path intact.
        resolved = _resolve_shacl_compile_factory(body)
        compiler_factory = (lambda: resolved()) if callable(resolved) else None
        reviewer_factory = (lambda: resolved()) if callable(resolved) else None

        try:
            # Step 1: Compile NL → SHACL TTL (with LLM timeout).
            fields = available_fields()
            compile_result = await asyncio.wait_for(
                compile_nl_to_shacl(
                    nl_text,
                    fields,
                    model_factory=compiler_factory,
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

            # Step 2: Review + explain (with LLM timeout each). Reviewer uses
            # the identity-distinct callable so the reviewer-guard sees compiler
            # ≠ reviewer (handoff §2). Explain reuses the compiler factory; it
            # is not a critic gate so identity does not matter there.
            review_result = await asyncio.wait_for(
                review_compilation(
                    nl_text, shape_ttl, fields, model_factory=reviewer_factory
                ),
                timeout=_LLM_CALL_TIMEOUT_S,
            )
            explanation = await asyncio.wait_for(
                explain_shape(shape_ttl, model_factory=compiler_factory),
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

            # Item 4 hardening: surface the deterministic structural check of
            # the compiled SHACL shape alongside the LLM critic's semantic
            # verdict. Empty list ⇒ shape parses, pySHACL loads it, and is
            # non-vacuous. The two signals are intentionally distinct so a
            # human reviewer is not relying on the LLM critic alone to catch
            # vacuously-permissive shapes.
            from magi_agent.customize.shacl_compiler import _shacl_validate

            response_payload: dict[str, Any] = {
                "ok": True,
                "shapeTtl": shape_ttl,
                "review": _make_json_safe(review_result),
                "explanation": explanation,
                "previewCases": preview,
                "shaclIssues": _shacl_validate(shape_ttl),
            }
            if preview_truncated:
                response_payload["previewTruncated"] = True
            return JSONResponse(content=response_payload)

        except Exception as exc:  # noqa: BLE001 — never raise from compile route
            return JSONResponse(
                content={"ok": False, "error": f"compile error: {exc}"}
            )

    # ------------------------------------------------------------------
    # PR-C2 — SeamSpec NL-spec routes (default-OFF, gated by
    # MAGI_CUSTOMIZE_SEAM_SPEC_ENABLED). Mirrors the SHACL compile route
    # in shape: auth FIRST, then flag check, then precheck, then orchestrator.
    # ------------------------------------------------------------------

    @app.post("/v1/app/customize/seams/compile")
    async def compile_seam_spec(request: Request) -> JSONResponse:
        """NL → SeamSpec compile preview (registration-time only).

        Returns the compiled spec + LLM critic verdict + deterministic
        ``schemaIssues``. NEVER persists — saving is done by PUT /seams
        after the user reviews the preview.

        Auth FIRST → flag check → body parse → length caps → aggregate
        precheck → orchestrator. Same shape as the SHACL compile route.
        """
        unauthorized = _unauthorized_response(request, runtime)
        if unauthorized is not None:
            return unauthorized

        if not _is_seam_spec_enabled():
            return JSONResponse(
                content={"ok": False, "error": "seam-spec compiler disabled"},
                status_code=200,
            )

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

        # priorTurns validation — same shape + caps as the SHACL route.
        _MAX_PRIOR_TURNS = 10
        raw_prior_turns = body.get("priorTurns")
        validated_prior_turns: list[dict] = []
        if isinstance(raw_prior_turns, list):
            raw_prior_turns = raw_prior_turns[:_MAX_PRIOR_TURNS]
            total_content_bytes = 0
            for element in raw_prior_turns:
                if not isinstance(element, dict):
                    continue
                role = element.get("role")
                content = element.get("content")
                if role not in ("user", "assistant"):
                    continue
                if not isinstance(content, str) or not content:
                    continue
                content_bytes = len(content.encode())
                if content_bytes > _MAX_NL_TEXT_BYTES:
                    continue
                total_content_bytes += content_bytes
                validated_prior_turns.append({"role": role, "content": content})
                if total_content_bytes > 5 * _MAX_NL_TEXT_BYTES:
                    return JSONResponse(
                        status_code=400,
                        content={
                            "ok": False,
                            "error": "priorTurns total content too large",
                        },
                    )

        validated_user_turn_count = sum(
            1 for t in validated_prior_turns if t["role"] == "user"
        )
        if validated_user_turn_count >= _MAX_COMPILE_ROUNDS:
            return JSONResponse(
                status_code=400,
                content={
                    "ok": False,
                    "error": "too many conversation rounds",
                },
            )

        # Aggregate text cap — reuses the SHACL precheck for cross-compiler parity.
        from magi_agent.customize.seam_compiler import (  # noqa: PLC0415
            MAX_AGGREGATE_TEXT,
            PrecheckError,
            compile_with_review,
        )
        from magi_agent.customize.shacl_compiler import (  # noqa: PLC0415
            _precheck_aggregate,
        )

        try:
            _precheck_aggregate(nl_text, tuple(validated_prior_turns))
        except PrecheckError as exc:
            return JSONResponse(
                status_code=422,
                content={
                    "ok": False,
                    "error": str(exc),
                    "limit": MAX_AGGREGATE_TEXT,
                },
            )

        # Distinct compiler / reviewer callables (handoff §2 self-review guard).
        resolved = _resolve_seam_compile_factory(body)
        compiler_factory = (lambda: resolved()) if callable(resolved) else None
        reviewer_factory = (lambda: resolved()) if callable(resolved) else None

        try:
            result = await asyncio.wait_for(
                compile_with_review(
                    nl_text,
                    compiler_model_factory=compiler_factory,
                    reviewer_model_factory=reviewer_factory,
                    prior_turns=tuple(validated_prior_turns),
                ),
                timeout=_LLM_CALL_TIMEOUT_S,
            )
        except Exception as exc:  # noqa: BLE001 — never raise from compile route
            return JSONResponse(
                content={"ok": False, "error": f"compile error: {exc}"}
            )

        # Serialize the SeamSpec dataclass back to its JSON shape for the
        # response. The compile_with_review payload mixes Python objects
        # (SeamSpec) with primitives; the wire shape must be pure JSON.
        spec_obj = result.get("spec")
        spec_payload = None
        if spec_obj is not None:
            from magi_agent.customize.seam_compiler import _serialize_spec  # noqa: PLC0415
            import json as _json  # noqa: PLC0415

            spec_payload = _json.loads(_serialize_spec(spec_obj))

        if result.get("clarifyingQuestions"):
            return JSONResponse(
                content={
                    "ok": False,
                    "clarifyingQuestions": list(result["clarifyingQuestions"]),
                    "spec": None,
                    "error": None,
                }
            )

        if not result.get("ok"):
            return JSONResponse(
                content={
                    "ok": False,
                    "error": result.get("error", "compilation failed"),
                }
            )

        return JSONResponse(
            content={
                "ok": True,
                "spec": spec_payload,
                "review": _make_json_safe(result["review"]),
                "schemaIssues": list(result.get("schemaIssues", [])),
            }
        )

    @app.put("/v1/app/customize/seams")
    async def put_seam_spec(request: Request) -> JSONResponse:
        """Persist (upsert) an approved SeamSpec JSON document.

        Body shape: ``{id?: str, spec_version: str, actions: [...]}``. The
        spec is structurally validated (deterministic) before save; a
        non-empty issues list is returned with 422 and nothing is persisted.

        Gated behind ``MAGI_CUSTOMIZE_SEAM_SPEC_ENABLED``.
        """
        unauthorized = _unauthorized_response(request, runtime)
        if unauthorized is not None:
            return unauthorized
        if not _is_seam_spec_enabled():
            return JSONResponse(
                content={"ok": False, "error": "seam-spec compiler disabled"},
                status_code=200,
            )
        try:
            body = await request.json()
        except Exception:
            return JSONResponse(
                status_code=400, content={"ok": False, "error": "invalid_json"}
            )
        if not isinstance(body, dict):
            return JSONResponse(
                status_code=400, content={"ok": False, "error": "object_required"}
            )
        from magi_agent.customize.seam_spec import (  # noqa: PLC0415
            parse_spec,
            validate_spec,
        )
        from magi_agent.customize.store import set_seam_spec  # noqa: PLC0415

        try:
            spec = parse_spec(body)
        except ValueError as exc:
            return JSONResponse(
                status_code=400,
                content={"ok": False, "error": str(exc)},
            )
        issues = validate_spec(spec)
        if issues:
            return JSONResponse(
                status_code=422,
                content={"ok": False, "error": "invalid spec", "schemaIssues": issues},
            )

        spec_doc = dict(body)
        if not isinstance(spec_doc.get("id"), str) or not spec_doc["id"]:
            spec_doc["id"] = f"seam_{uuid.uuid4().hex}"
        overrides = set_seam_spec(spec_doc)
        return JSONResponse(
            content={"ok": True, "id": spec_doc["id"], "overrides": overrides}
        )

    @app.delete("/v1/app/customize/seams/{spec_id}")
    async def delete_seam_spec_route(spec_id: str, request: Request) -> JSONResponse:
        """Remove a persisted SeamSpec by id. No-op when the id is absent."""
        unauthorized = _unauthorized_response(request, runtime)
        if unauthorized is not None:
            return unauthorized
        if not _is_seam_spec_enabled():
            return JSONResponse(
                content={"ok": False, "error": "seam-spec compiler disabled"},
                status_code=200,
            )
        from magi_agent.customize.store import delete_seam_spec  # noqa: PLC0415

        overrides = delete_seam_spec(spec_id)
        return JSONResponse(content={"ok": True, "overrides": overrides})

    # ------------------------------------------------------------------
    # PR-D1 — Unified NL → rule compiler (default-OFF, gated by
    # MAGI_CUSTOMIZE_NL_RULE_COMPILER_ENABLED). Single endpoint that
    # auto-routes the user's NL policy to one of six backing primitives
    # and returns a structured draft + LLM critic verdict + deterministic
    # schemaIssues. Persistence is delegated to the matching existing PUT
    # route (custom-rules / seams / dashboard-checks) — this endpoint
    # NEVER saves.
    # ------------------------------------------------------------------
    @app.post("/v1/app/customize/rules/compile")
    async def compile_rule_nl(request: Request) -> JSONResponse:
        """NL → rule draft compile preview (registration-time only)."""
        unauthorized = _unauthorized_response(request, runtime)
        if unauthorized is not None:
            return unauthorized

        if not _is_nl_rule_compiler_enabled():
            return JSONResponse(
                content={"ok": False, "error": "nl-rule compiler disabled"},
                status_code=200,
            )

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

        # priorTurns validation — identical caps to the SHACL / seam routes.
        _MAX_PRIOR_TURNS = 10
        raw_prior_turns = body.get("priorTurns")
        validated_prior_turns: list[dict] = []
        if isinstance(raw_prior_turns, list):
            raw_prior_turns = raw_prior_turns[:_MAX_PRIOR_TURNS]
            total_content_bytes = 0
            for element in raw_prior_turns:
                if not isinstance(element, dict):
                    continue
                role = element.get("role")
                content = element.get("content")
                if role not in ("user", "assistant"):
                    continue
                if not isinstance(content, str) or not content:
                    continue
                content_bytes = len(content.encode())
                if content_bytes > _MAX_NL_TEXT_BYTES:
                    continue
                total_content_bytes += content_bytes
                validated_prior_turns.append({"role": role, "content": content})
                if total_content_bytes > 5 * _MAX_NL_TEXT_BYTES:
                    return JSONResponse(
                        status_code=400,
                        content={
                            "ok": False,
                            "error": "priorTurns total content too large",
                        },
                    )

        validated_user_turn_count = sum(
            1 for t in validated_prior_turns if t["role"] == "user"
        )
        if validated_user_turn_count >= _MAX_COMPILE_ROUNDS:
            return JSONResponse(
                status_code=400,
                content={
                    "ok": False,
                    "error": "too many conversation rounds",
                },
            )

        from magi_agent.customize.rule_compiler import (  # noqa: PLC0415
            MAX_AGGREGATE_TEXT,
            PrecheckError,
            compile_interview_step,
            compile_with_review,
        )
        from magi_agent.customize.shacl_compiler import (  # noqa: PLC0415
            _precheck_aggregate,
        )

        try:
            _precheck_aggregate(nl_text, tuple(validated_prior_turns))
        except PrecheckError as exc:
            return JSONResponse(
                status_code=422,
                content={
                    "ok": False,
                    "error": str(exc),
                    "limit": MAX_AGGREGATE_TEXT,
                },
            )

        # Distinct callables for the self-review guard.
        resolved = _resolve_nl_rule_compile_factory(body)
        compiler_factory = (lambda: resolved()) if callable(resolved) else None
        reviewer_factory = (lambda: resolved()) if callable(resolved) else None

        # PR-F-UX6 interview-mode routing: when the flag is ON, route through
        # ``compile_interview_step`` which decides per-call between (a) the
        # legacy one-shot compile path (well-formed inputs, byte-identical
        # response shape), (b) an interview turn (underspecified inputs →
        # questions[]), and (c) a proposal turn (resolved intent → composed
        # primitives). The body can also force interview mode via
        # ``mode="interview"`` so the UI's "Refine" button works even on
        # well-formed inputs.
        interview_mode_enabled = _is_nl_interview_mode_enabled()
        body_mode = body.get("mode") if isinstance(body.get("mode"), str) else None
        force_interview = body_mode == "interview"

        if interview_mode_enabled or force_interview:
            try:
                step_result = await asyncio.wait_for(
                    compile_interview_step(
                        nl_text,
                        compiler_model_factory=compiler_factory,
                        reviewer_model_factory=reviewer_factory,
                        prior_turns=tuple(validated_prior_turns),
                        force_interview=force_interview,
                    ),
                    timeout=_LLM_CALL_TIMEOUT_S,
                )
            except Exception as exc:  # noqa: BLE001 — never raise from compile route
                return JSONResponse(
                    content={"ok": False, "error": f"compile error: {exc}"}
                )

            step_mode = step_result.get("mode")
            if step_mode == "interview":
                # Interview turn — questions[] for the frontend to render
                # with chip pickers per ``expects`` tag.
                return JSONResponse(
                    content={
                        "ok": bool(step_result.get("ok", True)),
                        "mode": "interview",
                        "questions": _make_json_safe(
                            step_result.get("questions", [])
                        ),
                        "intent": _make_json_safe(
                            step_result.get("intent", {})
                        ),
                        "error": step_result.get("error"),
                    }
                )
            if step_mode == "proposal":
                # Proposal turn — single primitive OR hybrid composition.
                return JSONResponse(
                    content={
                        "ok": True,
                        "mode": "proposal",
                        "proposal": _make_json_safe(step_result.get("proposal", {})),
                        "intent": _make_json_safe(step_result.get("intent", {})),
                    }
                )
            # step_mode == "compile" — fell through to the legacy path. Fall
            # through to the legacy response-formatting branches below.
            result = step_result
        else:
            try:
                result = await asyncio.wait_for(
                    compile_with_review(
                        nl_text,
                        compiler_model_factory=compiler_factory,
                        reviewer_model_factory=reviewer_factory,
                        prior_turns=tuple(validated_prior_turns),
                    ),
                    timeout=_LLM_CALL_TIMEOUT_S,
                )
            except Exception as exc:  # noqa: BLE001 — never raise from compile route
                return JSONResponse(
                    content={"ok": False, "error": f"compile error: {exc}"}
                )

        if result.get("clarifyingQuestions"):
            return JSONResponse(
                content={
                    "ok": False,
                    "clarifyingQuestions": list(result["clarifyingQuestions"]),
                    "routedKind": None,
                    "draft": None,
                    "error": None,
                }
            )

        if not result.get("ok"):
            # PR-F3 honest-degrade (2026-06-23): forward optional structured
            # keys the compiler populates for error == "field_not_in_catalog"
            # (and other honest-degrade branches) so the frontend banner can
            # render the per-(evidenceType, field) list + the redirect copy.
            # Strip None values so the response stays compact for the legacy
            # branches that do not populate them.
            error_payload: dict[str, Any] = {
                "ok": False,
                "error": result.get("error", "compilation failed"),
                "routedKind": result.get("routedKind"),
                "missingFields": result.get("missingFields"),
                "suggestion": result.get("suggestion"),
                "explanation": result.get("explanation"),
            }
            return JSONResponse(
                content={k: v for k, v in error_payload.items() if v is not None}
            )

        return JSONResponse(
            content={
                "ok": True,
                "routedKind": result["routedKind"],
                "draft": _make_json_safe(result["draft"]),
                "explanation": result.get("explanation", ""),
                "review": _make_json_safe(result["review"]),
                "schemaIssues": list(result.get("schemaIssues", [])),
            }
        )
