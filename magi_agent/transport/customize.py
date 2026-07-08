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
    set_builtin_policy_override,
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


def _is_nl_mode_compiler_enabled() -> bool:
    """Read ``MAGI_CUSTOMIZE_NL_MODE_COMPILER_ENABLED`` defensively (PR-U3.4).

    Gates ``POST /v1/app/modes/compile``: the NL → agent-mode compiler. This is
    a fail-open capability (not a safety floor), so it is profile-aware
    default-ON: ON in the normal/full profile, OFF only under the quiet
    safe/eval/minimal profiles, and opt-out anywhere with ``0``.
    Registration-time only; fail-open when no model is available.
    """
    try:
        from magi_agent.config.flags import flag_profile_bool  # noqa: PLC0415

        return flag_profile_bool("MAGI_CUSTOMIZE_NL_MODE_COMPILER_ENABLED")
    except Exception:  # noqa: BLE001
        return False


def _resolve_nl_mode_compile_factory(body: dict) -> Any:
    """Resolve the NL→mode compiler model factory.

    Test injection via ``body["_modeModelFactory"]``; otherwise the shared
    production SHACL-compiler model factory (same registration-time model the
    NL-rule compiler uses).
    """
    if isinstance(body, dict):
        factory = body.get("_modeModelFactory")
        if callable(factory):
            return factory
    from magi_agent.customize.shacl_compiler import (  # noqa: PLC0415
        _production_shacl_compiler_model_factory,
    )

    return _production_shacl_compiler_model_factory()


def _resolve_policy_compile_factory(body: dict) -> Any:
    """Resolve the NL->policy compiler model factory.

    Test injection via ``body["_policyModelFactory"]``; otherwise the shared
    production model factory (same registration-time model the other NL
    compilers use)."""
    if isinstance(body, dict):
        factory = body.get("_policyModelFactory")
        if callable(factory):
            return factory
    from magi_agent.customize.shacl_compiler import (  # noqa: PLC0415
        _production_shacl_compiler_model_factory,
    )

    return _production_shacl_compiler_model_factory()


def _load_existing_producers() -> list[dict]:
    """Already-authored dashboard-check producers (as dicts) from the writable
    sidecar, so the policy compiler can reuse one instead of duplicating it.

    Fail-soft: any read/parse error yields ``[]`` (reuse simply does not fire;
    the compiler mints a fresh producer). Scoped to the writable pack root (the
    producers the operator actually authored and could duplicate), matching the
    root :func:`persist_policy_plan` reuses in place."""
    try:
        from magi_agent.customize.policy_persist import (  # noqa: PLC0415
            _writable_dashboard_pack_root,
        )
        from magi_agent.packs.dashboard_authored import read_sidecar  # noqa: PLC0415

        # mode="json" so tuple fields (trigger.domainAllowlist) serialize to
        # lists (the plan + validate_dashboard_check expect JSON-native lists,
        # matching what the producer looked like when it came over the wire).
        return [
            c.model_dump(by_alias=True, mode="json")
            for c in read_sidecar(_writable_dashboard_pack_root())
        ]
    except Exception:  # noqa: BLE001
        return []


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
        # U2 (policies-first surface unification): one-shot, idempotent backfill
        # of 1-rule policies for any pre-existing unreferenced rule the moment
        # the Customize surface is opened, so the Policies list is complete
        # without a separate migration step. Write-on-first-read only — a
        # fully-migrated store re-runs to a no-op with no write. Fail-soft: a
        # backfill error must never break the read.
        try:
            from magi_agent.customize.policies import (  # noqa: PLC0415
                ensure_policies_for_unreferenced_rules,
            )

            ensure_policies_for_unreferenced_rules()
        except Exception:  # noqa: BLE001
            pass
        return JSONResponse(
            content={
                "catalog": build_catalog(runtime),
                "overrides": load_overrides(),
            }
        )

    # Agent MODES (postures) CRUD. Operator-authored, session-sticky postures
    # (system prompt + tool delta + scoped policy ids). Backed by customize.modes
    # over customize.json (``agent_modes`` / ``active_agent_mode``). Auth-gated
    # like the other customize routes.
    @app.get("/v1/app/modes")
    async def list_agent_modes(request: Request) -> JSONResponse:
        unauthorized = _unauthorized_response(request, runtime)
        if unauthorized is not None:
            return unauthorized
        from magi_agent.customize.modes import active_mode_id, list_modes

        return JSONResponse(
            content={
                "modes": [mode.to_payload() for mode in list_modes()],
                "activeMode": active_mode_id(),
            }
        )

    @app.put("/v1/app/modes/{mode_id}")
    async def upsert_agent_mode(mode_id: str, request: Request) -> JSONResponse:
        unauthorized = _unauthorized_response(request, runtime)
        if unauthorized is not None:
            return unauthorized
        try:
            body = await request.json()
        except Exception:
            return JSONResponse(status_code=400, content={"error": "invalid_json"})
        if not isinstance(body, dict):
            return JSONResponse(status_code=400, content={"error": "object_required"})
        from pydantic import ValidationError

        from magi_agent.customize.modes import (
            AgentMode,
            active_mode_id,
            list_modes,
            upsert_mode,
        )

        # The path id is authoritative: drop any body-supplied id (alias) or
        # ``mode_id`` (field-name form, since populate_by_name is on) so neither
        # can conflict, then set the path id.
        payload = {key: value for key, value in body.items() if key not in ("id", "mode_id")}
        payload["id"] = mode_id
        try:
            mode = AgentMode.model_validate(payload)
        except ValidationError:
            return JSONResponse(status_code=400, content={"error": "invalid_mode"})
        try:
            upsert_mode(mode)
        except ValueError as exc:
            return JSONResponse(
                status_code=400, content={"error": "upsert_rejected", "message": str(exc)}
            )
        # Return the full refreshed view so the UI does not need a follow-up GET.
        return JSONResponse(
            content={
                "mode": mode.to_payload(),
                "modes": [item.to_payload() for item in list_modes()],
                "activeMode": active_mode_id(),
            }
        )

    @app.delete("/v1/app/modes/{mode_id}")
    async def delete_agent_mode(mode_id: str, request: Request) -> JSONResponse:
        unauthorized = _unauthorized_response(request, runtime)
        if unauthorized is not None:
            return unauthorized
        from magi_agent.customize.modes import active_mode_id, delete_mode, list_modes

        try:
            delete_mode(mode_id)
        except ValueError as exc:
            # Built-in posture modes are read-only.
            return JSONResponse(
                status_code=400, content={"error": "delete_rejected", "message": str(exc)}
            )
        return JSONResponse(
            content={
                "modes": [mode.to_payload() for mode in list_modes()],
                "activeMode": active_mode_id(),
            }
        )

    # --- POLICIES (named compositions of 1..N rules) ---------------------
    # A policy groups custom rules into a user-intent unit (see
    # customize.policies). Storage-only CRUD, mirroring the modes routes;
    # auth-gated identically.
    @app.get("/v1/app/policies")
    async def list_policies_route(request: Request) -> JSONResponse:
        unauthorized = _unauthorized_response(request, runtime)
        if unauthorized is not None:
            return unauthorized
        from magi_agent.customize.policies import list_policies

        return JSONResponse(
            content={"policies": [p.to_payload() for p in list_policies()]}
        )

    @app.put("/v1/app/policies/{policy_id}")
    async def upsert_policy_route(policy_id: str, request: Request) -> JSONResponse:
        unauthorized = _unauthorized_response(request, runtime)
        if unauthorized is not None:
            return unauthorized
        try:
            body = await request.json()
        except Exception:
            return JSONResponse(status_code=400, content={"error": "invalid_json"})
        if not isinstance(body, dict):
            return JSONResponse(status_code=400, content={"error": "object_required"})
        from pydantic import ValidationError

        from magi_agent.customize.policies import (
            BUILTIN_POLICIES,
            Policy,
            list_policies,
            upsert_policy,
        )

        # PR-4 hardening (review): first-party policy ids are reserved. A user
        # record with a builtin id would shadow the builtin's DISPLAY card
        # (list_policies lets the stored clone win) - display spoofing only
        # (the runtime floor keys on env/catalog, not this record), but there
        # is no legitimate reason to allow it.
        if policy_id in {b.policy_id for b in BUILTIN_POLICIES}:
            return JSONResponse(
                status_code=409,
                content={"error": "builtin_id_reserved", "policyId": policy_id},
            )

        # The path id is authoritative: drop any body-supplied id / policy_id so
        # neither can conflict, then set the path id.
        payload = {k: v for k, v in body.items() if k not in ("id", "policy_id")}
        payload["id"] = policy_id
        try:
            policy = Policy.model_validate(payload)
        except ValidationError:
            return JSONResponse(status_code=400, content={"error": "invalid_policy"})
        try:
            upsert_policy(policy)
        except ValueError as exc:
            return JSONResponse(
                status_code=400,
                content={"error": "upsert_rejected", "message": str(exc)},
            )
        return JSONResponse(
            content={
                "policy": policy.to_payload(),
                "policies": [p.to_payload() for p in list_policies()],
            }
        )

    @app.delete("/v1/app/policies/{policy_id}")
    async def delete_policy_route(policy_id: str, request: Request) -> JSONResponse:
        unauthorized = _unauthorized_response(request, runtime)
        if unauthorized is not None:
            return unauthorized
        from magi_agent.customize.policies import delete_policy, list_policies

        delete_policy(policy_id)
        return JSONResponse(
            content={"policies": [p.to_payload() for p in list_policies()]}
        )

    @app.patch("/v1/app/policies/{policy_id}")
    async def patch_policy_enabled(policy_id: str, request: Request) -> JSONResponse:
        """Policy-level enabled toggle with member-rule cascade (PR-1 U4).

        Body ``{"enabled": bool}``: atomically sets ``enabled`` on every member
        custom rule of a USER policy. 404 for an unknown policy id; 409 for a
        first-party (builtin) policy — those keep their own preset /
        control-plane / builtin-policies PATCH routes and are never toggled
        through this cascade. Returns the refreshed policy list."""
        unauthorized = _unauthorized_response(request, runtime)
        if unauthorized is not None:
            return unauthorized
        try:
            body = await request.json()
        except Exception:
            return JSONResponse(status_code=400, content={"error": "invalid_json"})
        if not isinstance(body, dict) or not isinstance(body.get("enabled"), bool):
            return JSONResponse(status_code=400, content={"error": "enabled_bool_required"})

        from magi_agent.customize.policies import (  # noqa: PLC0415
            list_policies,
            set_policy_enabled,
        )

        try:
            set_policy_enabled(policy_id, body["enabled"])
        except KeyError:
            return JSONResponse(
                status_code=404,
                content={
                    "error": "not_found",
                    "message": f'policy "{policy_id}" not found',
                },
            )
        except ValueError:
            # First-party (builtin) policy — not togglable via this route.
            return JSONResponse(
                status_code=409,
                content={
                    "error": "builtin_policy",
                    "message": (
                        f'built-in policy "{policy_id}" is toggled via its own '
                        "route, not the policy cascade"
                    ),
                },
            )
        # Re-project the runtime verification overrides so the cascade takes
        # effect without a restart (mirrors the custom-rule routes).
        apply_verification_overrides(runtime, load_overrides())
        return JSONResponse(
            content={"policies": [p.to_payload() for p in list_policies()]}
        )

    @app.post("/v1/app/policies/migrate")
    async def migrate_policies_route(request: Request) -> JSONResponse:
        unauthorized = _unauthorized_response(request, runtime)
        if unauthorized is not None:
            return unauthorized
        from magi_agent.customize.policies import (
            ensure_policies_for_unreferenced_rules,
            list_policies,
        )

        # U2: migrate groups AND synthesize 1-rule policies for every remaining
        # unreferenced rule (idempotent). ``ensure_...`` runs the group migration
        # itself first, then backfills the singles.
        created = ensure_policies_for_unreferenced_rules()
        return JSONResponse(
            content={
                "created": created,
                "policies": [p.to_payload() for p in list_policies()],
            }
        )

    @app.post("/v1/app/modes/active")
    async def set_active_agent_mode(request: Request) -> JSONResponse:
        unauthorized = _unauthorized_response(request, runtime)
        if unauthorized is not None:
            return unauthorized
        try:
            body = await request.json()
        except Exception:
            return JSONResponse(status_code=400, content={"error": "invalid_json"})
        # Require the key to be PRESENT so a malformed/empty body cannot silently
        # clear the active mode; explicit ``null`` still clears.
        if not isinstance(body, dict) or "modeId" not in body:
            return JSONResponse(status_code=400, content={"error": "modeId_required"})
        mode_id = body["modeId"]
        if mode_id is not None and not isinstance(mode_id, str):
            return JSONResponse(status_code=400, content={"error": "modeId_str_or_null"})
        from magi_agent.customize.modes import active_mode_id, set_active_mode

        try:
            set_active_mode(mode_id)
        except ValueError:
            # Do not echo the caller-supplied id back in the response.
            return JSONResponse(status_code=404, content={"error": "unknown_mode"})
        return JSONResponse(content={"activeMode": active_mode_id()})

    @app.get("/v1/app/prebuilt-components")
    async def list_prebuilt_components(request: Request) -> JSONResponse:
        """PR-P4: read-only list of always-on kernel components (read-before-write,
        path safety, receipts, ...) that gate every turn but were invisible in the
        dashboard. Descriptive only; never mutates.
        """
        unauthorized = _unauthorized_response(request, runtime)
        if unauthorized is not None:
            return unauthorized
        from magi_agent.customize.prebuilt_components import (  # noqa: PLC0415
            prebuilt_components_view,
        )

        try:
            components = prebuilt_components_view()
        except Exception:  # noqa: BLE001
            components = []
        return JSONResponse(content={"components": components})

    @app.get("/v1/app/packs")
    async def list_installed_packs(request: Request) -> JSONResponse:
        """PR-P3: read-only inventory of installed packs + what each provides.

        Powers the Packs tab's contents view so the operator can see the rules /
        behaviors / tools a pack contributes, not just its id. Never mutates.
        """
        unauthorized = _unauthorized_response(request, runtime)
        if unauthorized is not None:
            return unauthorized
        from magi_agent.packs.inventory import installed_packs_view  # noqa: PLC0415

        try:
            packs = installed_packs_view()
        except Exception:  # noqa: BLE001
            packs = []
        return JSONResponse(content={"packs": packs})

    @app.post("/v1/app/packs/{pack_id}/state")
    async def set_pack_state(pack_id: str, request: Request) -> JSONResponse:
        """Install (``enabled=true``) or remove (``enabled=false``) a pack.

        Persists a dashboard override to ``packs-state.json`` (never touches the
        operator's config.toml). "Remove" is reversible: installing again just
        clears the removal, so first-party packs are always recoverable. Returns
        the updated inventory. Self-host only, auth-gated."""
        unauthorized = _unauthorized_response(request, runtime)
        if unauthorized is not None:
            return unauthorized
        try:
            body = await request.json()
        except Exception:
            return JSONResponse(status_code=400, content={"error": "invalid_json"})
        if not isinstance(body, dict) or not isinstance(body.get("enabled"), bool):
            return JSONResponse(status_code=400, content={"error": "enabled_bool_required"})

        from magi_agent.packs.discovery import set_pack_runtime_state  # noqa: PLC0415
        from magi_agent.packs.inventory import installed_packs_view  # noqa: PLC0415

        try:
            packs = installed_packs_view()
        except Exception:  # noqa: BLE001
            packs = []
        if not any(p.get("packId") == pack_id for p in packs):
            return JSONResponse(status_code=404, content={"error": "unknown_pack"})

        set_pack_runtime_state(pack_id, bool(body["enabled"]))
        try:
            packs = installed_packs_view()
        except Exception:  # noqa: BLE001
            pass
        return JSONResponse(content={"packs": packs})

    @app.post("/v1/app/modes/compile")
    async def compile_agent_mode(request: Request) -> JSONResponse:
        """PR-U3.4: NL → agent-mode draft compile preview.

        Registration-time only. Drafts a full mode (system prompt + tool delta
        + scoped rules + permission mode) from a plain-language stance
        description for the operator to review in the Mode editor. Grounds the
        draft on the live tool catalog + the caller-supplied scopable rule ids,
        and honest-degrades (drops unknowns, caps permission mode) so the draft
        is always a structurally valid mode. Never activates anything: the
        caller saves via ``PUT /v1/app/modes/{id}``.
        """
        unauthorized = _unauthorized_response(request, runtime)
        if unauthorized is not None:
            return unauthorized

        if not _is_nl_mode_compiler_enabled():
            return JSONResponse(
                content={"ok": False, "error": "nl-mode compiler disabled"},
                status_code=200,
            )

        try:
            body = await request.json()
        except Exception:
            return JSONResponse(status_code=400, content={"error": "invalid_json"})
        if not isinstance(body, dict):
            return JSONResponse(status_code=400, content={"error": "object_required"})

        nl_text = body.get("nlText")
        if not isinstance(nl_text, str) or not nl_text.strip():
            return JSONResponse(status_code=400, content={"error": "nlText_required"})
        # Byte cap for parity with the sibling compile routes (multibyte-safe).
        if len(nl_text.encode()) > _MAX_NL_TEXT_BYTES:
            return JSONResponse(status_code=400, content={"error": "nlText_too_large"})

        # Available tools: authoritative from the live catalog. Scopable rule
        # ids: the caller's unified custom_rule/dashboard_check ids (grounding
        # only; save-time validation is authoritative). This route is one-shot
        # (no priorTurns): the compose surface never sends prior turns, so we do
        # not accept them (avoids an unbounded, unfenced LLM input).
        try:
            catalog = build_catalog(runtime)
            available_tools = [
                str(t.get("name"))
                for t in catalog.get("tools", [])
                if isinstance(t, dict) and t.get("name")
            ]
        except Exception:  # noqa: BLE001
            available_tools = []
        scopable = body.get("scopablePolicyIds")
        scopable_ids = (
            [str(p) for p in scopable if isinstance(p, str)]
            if isinstance(scopable, list)
            else []
        )

        from magi_agent.customize.mode_compiler import compile_nl_to_mode  # noqa: PLC0415
        from magi_agent.customize.modes import active_permission_mode  # noqa: PLC0415

        baseline = active_permission_mode() or "default"

        try:
            result = await asyncio.wait_for(
                compile_nl_to_mode(
                    nl_text,
                    model_factory=_resolve_nl_mode_compile_factory(body),
                    available_tools=available_tools,
                    scopable_policy_ids=scopable_ids,
                    baseline_permission_mode=baseline,
                ),
                timeout=_LLM_CALL_TIMEOUT_S,
            )
        except TimeoutError:
            return JSONResponse(
                content={"ok": False, "error": "compile timed out", "draft": None},
                status_code=200,
            )
        except Exception:  # noqa: BLE001
            # Do not echo raw exception text to the client (parity with the
            # sibling routes' structured errors).
            return JSONResponse(
                content={"ok": False, "error": "compile failed", "draft": None},
                status_code=200,
            )
        return JSONResponse(content=result, status_code=200)

    # --- POLICY compile (NL -> producer+gate+binding plan) ------------------
    # One-shot + multi-turn conversational compile of a multi-rule policy. The
    # LLM only extracts params; the plan is templated + validated server-side
    # (see customize.policy_compiler / nl_policy_interactive). Registration-time
    # preview only: the caller persists the assembled plan separately. Auth-gated;
    # fail-open (structured error / notApplicable, never a raw 500).
    @app.post("/v1/app/policies/compile")
    async def compile_policy(request: Request) -> JSONResponse:
        unauthorized = _unauthorized_response(request, runtime)
        if unauthorized is not None:
            return unauthorized
        try:
            body = await request.json()
        except Exception:
            return JSONResponse(status_code=400, content={"error": "invalid_json"})
        if not isinstance(body, dict):
            return JSONResponse(status_code=400, content={"error": "object_required"})
        nl_text = body.get("nlText")
        if not isinstance(nl_text, str) or not nl_text.strip():
            return JSONResponse(status_code=400, content={"error": "nlText_required"})
        if len(nl_text.encode()) > _MAX_NL_TEXT_BYTES:
            return JSONResponse(status_code=400, content={"error": "nlText_too_large"})

        from magi_agent.customize.policy_compiler import compile_nl_to_policy  # noqa: PLC0415

        try:
            result = await asyncio.wait_for(
                compile_nl_to_policy(
                    nl_text,
                    model_factory=_resolve_policy_compile_factory(body),
                    existing_producers=_load_existing_producers(),
                ),
                timeout=_LLM_CALL_TIMEOUT_S,
            )
        except TimeoutError:
            return JSONResponse(
                content={"ok": False, "error": "compile timed out", "plan": None},
                status_code=200,
            )
        except Exception:  # noqa: BLE001
            return JSONResponse(
                content={"ok": False, "error": "compile failed", "plan": None},
                status_code=200,
            )
        return JSONResponse(content=result, status_code=200)

    @app.post("/v1/app/policies/compile/interactive")
    async def compile_policy_interactive(request: Request) -> JSONResponse:
        unauthorized = _unauthorized_response(request, runtime)
        if unauthorized is not None:
            return unauthorized
        try:
            body = await request.json()
        except Exception:
            return JSONResponse(status_code=400, content={"error": "invalid_json"})
        if not isinstance(body, dict):
            return JSONResponse(status_code=400, content={"error": "object_required"})

        from magi_agent.customize.nl_policy_interactive import (  # noqa: PLC0415
            step_policy_compile,
        )
        from magi_agent.customize.nl_compiler_interactive import (  # noqa: PLC0415
            InteractiveInputError,
            PrecheckError,
        )

        try:
            result = await asyncio.wait_for(
                step_policy_compile(
                    history=body.get("history"),
                    params_so_far=body.get("paramsSoFar"),
                    answers=body.get("answers"),
                    model_factory=_resolve_policy_compile_factory(body),
                    existing_producers=_load_existing_producers(),
                ),
                timeout=_LLM_CALL_TIMEOUT_S,
            )
        except (InteractiveInputError, PrecheckError) as exc:
            # Structural body violation / prompt-budget overflow -> 422.
            return JSONResponse(status_code=422, content={"error": str(exc)})
        except TimeoutError:
            return JSONResponse(
                content={"ready_to_save": False, "error": "compile timed out"},
                status_code=200,
            )
        except Exception:  # noqa: BLE001
            return JSONResponse(
                content={"ready_to_save": False, "error": "compile failed"},
                status_code=200,
            )
        return JSONResponse(content=result, status_code=200)

    @app.post("/v1/app/policies/from-plan")
    async def save_policy_from_plan(request: Request) -> JSONResponse:
        """Persist an assembled policy plan: the producer (dashboard-check
        sidecar) + the gate (custom_rule) + the Policy record (with binding).
        Idempotent upsert-by-id; a structurally-unsound plan is rejected (400)
        before any store is written."""
        unauthorized = _unauthorized_response(request, runtime)
        if unauthorized is not None:
            return unauthorized
        try:
            body = await request.json()
        except Exception:
            return JSONResponse(status_code=400, content={"error": "invalid_json"})
        if not isinstance(body, dict):
            return JSONResponse(status_code=400, content={"error": "object_required"})
        plan = body.get("plan") if isinstance(body.get("plan"), dict) else body

        from magi_agent.customize.policy_persist import (  # noqa: PLC0415
            PolicyPersistError,
            persist_policy_plan,
        )

        try:
            saved = persist_policy_plan(plan)
        except PolicyPersistError as exc:
            return JSONResponse(
                status_code=400, content={"error": "invalid_plan", "message": str(exc)}
            )
        except Exception:  # noqa: BLE001
            return JSONResponse(status_code=400, content={"error": "save_failed"})
        return JSONResponse(content={"ok": True, **saved}, status_code=200)

    @app.post("/v1/app/policies/review")
    async def review_policy(request: Request) -> JSONResponse:
        """Review an assembled policy plan: deterministic integrity findings +
        an ADVISORY LLM intent-coverage verdict. Advisory only, never blocks a
        save. Fail-open: LLM trouble degrades the verdict to ``unknown`` while
        the deterministic ``structural`` findings are always returned."""
        unauthorized = _unauthorized_response(request, runtime)
        if unauthorized is not None:
            return unauthorized
        try:
            body = await request.json()
        except Exception:
            return JSONResponse(status_code=400, content={"error": "invalid_json"})
        if not isinstance(body, dict):
            return JSONResponse(status_code=400, content={"error": "object_required"})
        plan = body.get("plan") if isinstance(body.get("plan"), dict) else body

        from magi_agent.customize.policy_review import review_policy_plan  # noqa: PLC0415

        try:
            result = await asyncio.wait_for(
                review_policy_plan(
                    plan, model_factory=_resolve_policy_compile_factory(body)
                ),
                timeout=_LLM_CALL_TIMEOUT_S,
            )
        except TimeoutError:
            # Timeout on the advisory LLM: still return the deterministic layer.
            from magi_agent.customize.policy_plan import (  # noqa: PLC0415
                validate_policy_plan,
            )

            structural = validate_policy_plan(plan)
            return JSONResponse(
                content={
                    "structural": structural,
                    "structurallySound": not structural,
                    "review": {"verdict": "unknown", "issues": [], "confidence": 0.0, "coverage": ""},
                },
                status_code=200,
            )
        return JSONResponse(content=result, status_code=200)

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
        # F-UX10 (2026-06-24): when targeting a recipe, validate the id against
        # the curated ``RECIPES`` catalog so a typo cannot silently land in
        # ``verification.recipes[]`` and confuse the allowlist filter. Unknown
        # recipe ids return 404 and nothing is persisted. ``harness_presets``
        # and ``hooks`` retain their permissive write contract because their
        # ids are validated elsewhere (preset_map / file-authored hooks).
        if kind == "recipes":
            from magi_agent.customize.catalog import RECIPES  # noqa: PLC0415

            known_recipe_ids = {r["id"] for r in RECIPES if isinstance(r.get("id"), str)}
            if item_id not in known_recipe_ids:
                return JSONResponse(
                    status_code=404,
                    content={
                        "error": "not_found",
                        "message": f'recipe "{item_id}" not found',
                    },
                )
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

    @app.patch("/v1/app/customize/builtin-policies/{policy_id}")
    async def patch_builtin_policy(policy_id: str, request: Request) -> JSONResponse:
        unauthorized = _unauthorized_response(request, runtime)
        if unauthorized is not None:
            return unauthorized
        try:
            body = await request.json()
        except Exception:
            return JSONResponse(status_code=400, content={"error": "invalid_json"})
        if not isinstance(body, dict) or not isinstance(body.get("enabled"), bool):
            return JSONResponse(status_code=400, content={"error": "enabled_bool_required"})
        from magi_agent.customize.builtin_policy_overrides import (  # noqa: PLC0415
            BUILTIN_POLICY_TOGGLES,
            apply_builtin_policy_overrides_to_env,
        )

        # Only curated, user-disableable builtins are accepted. A floor policy
        # (source_citation) or an unknown id 404s -- it can never be disabled here.
        if policy_id not in {t.id for t in BUILTIN_POLICY_TOGGLES}:
            return JSONResponse(
                status_code=404,
                content={
                    "error": "not_found",
                    "message": f'built-in policy "{policy_id}" is not user-disableable',
                },
            )
        enabled = body["enabled"]
        overrides = set_builtin_policy_override(policy_id, enabled)
        # Project onto the live process env so the next turn's gate reads the
        # toggle without a restart (the driver reads ``os.environ`` per turn).
        # Overwrite beats the profile seed and re-enables cleanly.
        apply_builtin_policy_overrides_to_env(
            os.environ, {"builtin_policies": {policy_id: enabled}}
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
        # Policy-envelope fields (PR-4 authoring consolidation): the NL
        # authoring client threads the user's ORIGINAL sentence (``intent``)
        # plus a compiler-suggested/derived ``displayName`` through the save so
        # the auto-promoted 1-rule Policy carries them. They are Policy fields,
        # NOT rule fields — strip them from the persisted rule shape.
        display_name = (
            body.get("displayName")
            if isinstance(body.get("displayName"), str)
            else None
        )
        intent = body.get("intent") if isinstance(body.get("intent"), str) else None
        rule = {k: v for k, v in body.items() if k not in ("displayName", "intent")}
        supplied_id = isinstance(rule.get("id"), str) and bool(rule["id"])
        if not supplied_id:
            rule["id"] = f"cr_{uuid.uuid4().hex}"
        # U1 (policies-first surface unification): a genuinely NEW custom rule is
        # auto-promoted to a 1-rule Policy so it is never an orphan rule in the
        # Policies surface. Detect create-vs-update BEFORE the save: a
        # client-supplied id that already exists in the store is an UPDATE (skip
        # promotion); a backfilled id, or a supplied id not yet present, is a
        # CREATE. ``promote_rule_to_policy`` is itself idempotent (it no-ops when
        # the rule id is already referenced by a policy — e.g. a plan-persisted
        # rule), so this is a belt-and-suspenders guard, not the only one.
        existing_ids = {
            r.get("id")
            for r in load_overrides().get("verification", {}).get("custom_rules", [])
            if isinstance(r, dict)
        }
        is_create = rule["id"] not in existing_ids
        overrides = set_custom_rule(rule)
        # Grouped rules (a hybrid NL proposal saving N rules under one
        # ``groupId``) compose ONE logical policy: the client upserts that
        # Policy itself via PUT /v1/app/policies/{groupId}, and the read-time
        # group migration backfills legacy grouped saves. Per-rule promotion
        # here would shatter the group into N singles PLUS the group policy
        # (double-representation), so it is skipped for grouped saves.
        grouped = isinstance(rule.get("groupId"), str) and bool(rule["groupId"].strip())
        if is_create and not grouped:
            from magi_agent.customize.policies import (  # noqa: PLC0415
                promote_rule_to_policy,
            )

            # ``display_name`` / ``intent`` arrive from the NL authoring client
            # (the compile flow threads the operator's own sentence through the
            # save). Both optional: absent → displayName falls back to the rule
            # id inside ``promote_rule_to_policy`` and intent stays empty (the
            # Guided/Raw editors have no NL sentence — honest, not fabricated).
            try:
                promote_rule_to_policy(rule, display_name=display_name, intent=intent)
            except Exception:  # noqa: BLE001 - promotion must never fail the save
                pass
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

    @app.post("/v1/app/customize/custom-rules/compile-interactive")
    async def compile_custom_rule_interactive(request: Request) -> JSONResponse:
        """Conversational multi-turn variant of /custom-rules/compile.

        Mirror of magi-cp's ``/policies/compile-interactive`` adapted to
        magi-agent's surface (see
        ``magi_agent/customize/nl_compiler_interactive.py``). Where the
        one-shot route compiles a whole policy in a single LLM call, this
        endpoint runs a state machine: the client posts ``(history,
        draft_so_far, answers)``, the server applies the operator's
        answers first (immutable for this turn), calls the LLM with the
        running history, merges the LLM's partial patch on top, then
        decides the next batch of questions.

        Gated behind ``MAGI_CUSTOMIZE_NL_INTERACTIVE_ENABLED`` (profile-aware default: ON in full / lab,
        OFF). When the flag is OFF, returns the same disabled-feature
        envelope as the one-shot compiler so the dashboard can hide
        the chat UI without branching on HTTP status.

        Returns:
          200 {assistant_message, draft, missing_fields, questions,
               needs_more, ready_to_save, schema_issues}
              on every successful turn.
          200 {ok:False, error:"compiler disabled"} when flag OFF.
          400 invalid JSON / structural cap violation.
          422 InteractiveInputError / PrecheckError (body shape OK but
              one cap exceeded — history too long, answer key too long,
              aggregate text too large).
          401 auth failure (always before flag check).
        """
        unauthorized = _unauthorized_response(request, runtime)
        if unauthorized is not None:
            return unauthorized

        try:
            from magi_agent.config.flags import flag_profile_bool  # noqa: PLC0415

            # Profile-aware default: ON under the full / lab profile so a
            # local-serve operator sees the conversational chat surface
            # without exporting an extra env var; OFF under safe / eval
            # so a key-less benchmark host stays quiet.
            interactive_enabled = flag_profile_bool(
                "MAGI_CUSTOMIZE_NL_INTERACTIVE_ENABLED"
            )
        except Exception:  # noqa: BLE001
            interactive_enabled = False
        if not interactive_enabled:
            return JSONResponse(
                content={"ok": False, "error": "compiler disabled"},
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
                status_code=400,
                content={"ok": False, "error": "object_required"},
            )

        history = body.get("history")
        draft_so_far = body.get("draft_so_far")
        answers = body.get("answers")

        # Resolve the model factory the same way the one-shot route does,
        # so the same provider key + env knobs drive both surfaces.
        from magi_agent.customize.nl_compiler_interactive import (  # noqa: PLC0415
            InteractiveInputError,
            PrecheckError,
            step_compile,
        )

        # Honor a test-only injection key so backend tests can stub the
        # LLM without piggy-backing on the production egress critic
        # factory — same pattern the one-shot route uses for SHACL.
        injected = body.get("_modelFactory")
        if callable(injected):
            model_factory = injected
        else:
            from magi_agent.cli.wiring import (  # noqa: PLC0415
                _build_criterion_model_factory,
            )

            model_factory = _build_criterion_model_factory

        try:
            result = await step_compile(
                history=history,
                draft_so_far=draft_so_far,
                answers=answers,
                model_factory=model_factory,
            )
        except InteractiveInputError as exc:
            return JSONResponse(
                status_code=422,
                content={"ok": False, "error": str(exc)},
            )
        except PrecheckError as exc:
            return JSONResponse(
                status_code=422,
                content={"ok": False, "error": str(exc)},
            )
        except Exception as exc:  # noqa: BLE001
            # Fail-soft: an LLM-stack failure surfaces as a 200 envelope
            # with the canonical-fallback questions, so the dashboard
            # chat stays usable on a transient provider outage.
            return JSONResponse(
                status_code=200,
                content={
                    "ok": False,
                    "error": f"compiler_failed: {exc}",
                },
            )

        return JSONResponse(content=result, status_code=200)

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
