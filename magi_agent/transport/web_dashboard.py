"""Serve the restored historical web dashboard (static Next.js export).

The rich OSS dashboard UI lives under ``apps/web`` and is built to a static
export (``output: "export"``) that is committed into the package at
``magi_agent/web_dashboard``. ``magi-agent serve`` mounts that bundle at
``/dashboard`` so the dashboard is usable from the local runtime with no
separate hosted backend and no Node runtime.

The bundle is entirely client-rendered and local-first: it talks to the same
origin via the runtime's own ``/v1/chat/*`` endpoints (see ``chat.py``) and
discovers its configuration from ``/app/bootstrap.json`` below. When the bundle
is absent (e.g. a source checkout that has not run the web build) the caller
falls back to the inline workbench shell in ``dashboard.py``.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

from magi_agent.runtime.openmagi_runtime import OpenMagiRuntime

# Token sentinel mirrored from the inline shell + main.py. The gateway token is
# only surfaced to the page when it is the well-known local-dev default, so a
# real ``GATEWAY_TOKEN`` secret is never embedded in a digest-safe surface.
_LOCAL_DEV_TOKEN = "local-dev-token"

BUNDLE_ROOT = Path(__file__).resolve().parent.parent / "web_dashboard"


def bundle_available() -> bool:
    """True when the committed static dashboard bundle is present."""
    return (BUNDLE_ROOT / "dashboard.html").is_file()


def _resolve_within_bundle(relative: str) -> Path | None:
    """Resolve ``relative`` against the bundle root, blocking path traversal."""
    candidate = (BUNDLE_ROOT / relative.lstrip("/")).resolve()
    try:
        candidate.relative_to(BUNDLE_ROOT.resolve())
    except ValueError:
        return None
    return candidate


def _serve_file(path: Path) -> Response:
    if not path.is_file():
        return Response(status_code=404)
    return FileResponse(path)


def local_dashboard_bootstrap(runtime: OpenMagiRuntime) -> dict[str, object]:
    """Local-first bootstrap consumed by the bundle's ``local-auth`` stub.

    ``agentUrl`` is empty so the UI uses same-origin relative requests. The
    gateway token is exposed only for the local-dev default; otherwise the UI
    treats the runtime as token-required and the operator supplies the token.
    """
    token = runtime.config.gateway_token
    expose = token == _LOCAL_DEV_TOKEN
    return {
        "ok": True,
        "agentUrl": "",
        "tokenRequired": bool(token) and not expose,
        "token": token if expose else None,
    }


def register_web_dashboard_routes(app: FastAPI, runtime: OpenMagiRuntime) -> None:
    """Mount the static dashboard bundle. Assumes ``bundle_available()``."""

    # Hashed JS/CSS/media chunks. Prefix mounts never shadow API routes.
    app.mount(
        "/_next",
        StaticFiles(directory=str(BUNDLE_ROOT / "_next")),
        name="dashboard-next-assets",
    )
    screenshots = BUNDLE_ROOT / "screenshots"
    if screenshots.is_dir():
        app.mount(
            "/screenshots",
            StaticFiles(directory=str(screenshots)),
            name="dashboard-screenshots",
        )

    @app.get("/app/bootstrap.json")
    def dashboard_bootstrap() -> JSONResponse:
        return JSONResponse(local_dashboard_bootstrap(runtime))

    @app.get("/dashboard")
    def dashboard() -> Response:
        return _serve_file(BUNDLE_ROOT / "dashboard.html")

    @app.get("/dashboard/{path:path}")
    def dashboard_deep_link(path: str) -> Response:
        # 1) exact static asset nested under /dashboard
        exact = _resolve_within_bundle(f"dashboard/{path}")
        if exact and exact.is_file():
            return _serve_file(exact)
        # 2) prerendered route html (e.g. local/chat/general -> general.html)
        html = _resolve_within_bundle(f"dashboard/{path}.html")
        if html and html.is_file():
            return _serve_file(html)
        # 3) SPA fallback — client routing resolves the rest (deep links,
        #    not-prerendered channels). Never blanks: serves the app shell.
        return _serve_file(BUNDLE_ROOT / "dashboard.html")

    # Root-level static assets (favicons, manifest, logos, sw.js, landing
    # pages). Registered explicitly per file so no greedy catch-all shadows
    # the /v1, /health, or /learnings routes.
    for entry in sorted(BUNDLE_ROOT.iterdir()):
        if not entry.is_file():
            continue
        name = entry.name
        if name == "index.html":
            continue  # "/" redirects to /dashboard (registered in dashboard.py)

        def _make_handler(target: Path):
            def _handler() -> Response:
                return _serve_file(target)

            return _handler

        app.add_api_route(
            f"/{name}",
            _make_handler(entry),
            methods=["GET"],
            include_in_schema=False,
        )


def register_root_redirect(app: FastAPI) -> None:
    @app.get("/", response_class=RedirectResponse)
    def root() -> RedirectResponse:
        return RedirectResponse("/dashboard", status_code=307)
