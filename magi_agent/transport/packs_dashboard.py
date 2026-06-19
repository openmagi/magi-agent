"""Dashboard pack-builder REST endpoints (self-host only, default-OFF, 410 gate).

Surfaces the dashboard-authored custom-check builder over four routes under
``/v1/app/packs/dashboard``. Reads/writes the single user pack at
``<writable base>/dashboard-authored/`` via :mod:`magi_agent.packs.dashboard_authored`
(``read_sidecar`` / ``write_pack``).

Two independent gates, both checked at the TOP of every handler (``_gate``):

* ``MAGI_DASHBOARD_PACK_AUTHORING_ENABLED`` OFF  → 410 Gone.
* hosted deployment (``is_hosted_deployment()`` true) → 410 Gone.

Hosted multi-tenant must NEVER expose this surface (a tenant could author a
regex producer that runs against another tenant's tool output) — same model as
the user HookBus. When OFF or hosted the routes are still registered but every
handler returns 410, so the default app surface is byte-identical.

The registrar takes ``(app, runtime)`` to match the transport convention
(mirrors ``register_customize_routes``); ``runtime`` is unused because these
routes are purely FS-based.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from pydantic import ValidationError

from magi_agent.config.env import (
    is_dashboard_pack_authoring_enabled,
    is_hosted_deployment,
)


def _pack_root() -> Path:
    """Resolve the writable dashboard pack directory.

    ``default_search_bases()`` returns
    ``[bundled_firstparty, ~/.magi/packs, cwd/.magi/packs]``. The bundled base
    lives *inside* the installed package and is read-only by contract — we MUST
    never write there. We therefore pick the FIRST base that is not the bundled
    first-party base (normally ``~/.magi/packs``). Tests monkeypatch
    ``default_search_bases`` to ``[tmp_path]``; since ``tmp_path`` is not the
    bundled base it is selected as the write target.
    """
    from magi_agent.packs.dashboard_authored import DASHBOARD_PACK_DIR_NAME
    from magi_agent.packs.discovery import (
        _bundled_firstparty_base,
        default_search_bases,
    )

    bundled = _bundled_firstparty_base()
    try:
        bundled_resolved = bundled.resolve()
    except OSError:
        bundled_resolved = bundled

    chosen: Path | None = None
    for base in default_search_bases():
        base = Path(base)
        try:
            base_resolved = base.resolve()
        except OSError:
            base_resolved = base
        if base_resolved != bundled_resolved:
            chosen = base
            break
    if chosen is None:
        # Degenerate config (only the bundled base). Fall back to the user home
        # pack base rather than writing into the package.
        chosen = Path.home() / ".magi" / "packs"
    return chosen / DASHBOARD_PACK_DIR_NAME


def register_dashboard_pack_routes(app: FastAPI, runtime) -> None:  # noqa: ANN001
    _ = runtime  # FS-based routes; accepted to match the (app, runtime) convention.

    def _gate() -> None:
        if not is_dashboard_pack_authoring_enabled():
            raise HTTPException(
                status_code=410, detail="dashboard pack authoring disabled"
            )
        if is_hosted_deployment():
            raise HTTPException(
                status_code=410,
                detail="dashboard pack authoring is self-host only",
            )

    def _checks_payload(root: Path, checks) -> dict:  # noqa: ANN001
        return {
            "enabled": True,
            "packs_root": str(root),
            "checks": [c.model_dump(by_alias=True) for c in checks],
        }

    @app.get("/v1/app/packs/dashboard/checks")
    def list_checks() -> dict:
        _gate()
        from magi_agent.packs.dashboard_authored import read_sidecar

        root = _pack_root()
        return _checks_payload(root, read_sidecar(root))

    @app.get("/v1/app/packs/dashboard/menu")
    def menu() -> dict:
        _gate()
        # Best-effort tool catalog; never raise (→ empty list on any failure).
        try:
            from magi_agent.tools.catalog import core_tool_manifests

            tools = sorted({m.name for m in core_tool_manifests()})
        except Exception:  # noqa: BLE001
            tools = []
        return {"tools": tools}

    @app.put("/v1/app/packs/dashboard/checks/{check_id}")
    def upsert(check_id: str, payload: dict) -> dict:
        _gate()
        from magi_agent.packs.dashboard_authored import (
            DashboardCheck,
            read_sidecar,
            validate_dashboard_check,
            write_pack,
        )

        if not isinstance(payload, dict) or payload.get("id") != check_id:
            raise HTTPException(
                status_code=400, detail="path id and body id must match"
            )
        errors = validate_dashboard_check(payload)
        if errors:
            raise HTTPException(status_code=400, detail={"errors": errors})
        try:
            check = DashboardCheck.model_validate(payload)
        except ValidationError as exc:
            raise HTTPException(status_code=400, detail=exc.errors()) from exc

        root = _pack_root()
        checks = [c for c in read_sidecar(root) if c.id != check_id]
        checks.append(check)
        write_pack(root, checks)
        return _checks_payload(root, checks)

    @app.delete("/v1/app/packs/dashboard/checks/{check_id}")
    def delete(check_id: str) -> dict:
        _gate()
        from magi_agent.packs.dashboard_authored import read_sidecar, write_pack

        root = _pack_root()
        existing = read_sidecar(root)
        if not any(c.id == check_id for c in existing):
            raise HTTPException(
                status_code=404, detail=f"check {check_id!r} not found"
            )
        kept = [c for c in existing if c.id != check_id]
        write_pack(root, kept)
        return _checks_payload(root, kept)
