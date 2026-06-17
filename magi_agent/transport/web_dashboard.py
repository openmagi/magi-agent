"""Serve the web dashboard (static Next.js export) — the single dashboard path.

The OSS dashboard UI lives under ``apps/web`` and is built to a static export
(``output: "export"``) that is committed into the package at
``magi_agent/web_dashboard`` (wheels always ship it via package-data).
``magi-agent serve`` mounts that bundle at ``/dashboard`` so the dashboard is
usable from the local runtime with no separate hosted backend and no Node
runtime.

The bundle is entirely client-rendered and local-first: it talks to the same
origin via the runtime's own ``/v1/chat/*`` endpoints (see ``chat.py``) and
discovers its configuration from ``/app/bootstrap.json`` below. When the bundle
is absent (only possible in a source checkout that has not run the web build),
``/dashboard`` serves a static build-instruction placeholder instead — there is
no second inline web frontend.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    RedirectResponse,
    Response,
)
from fastapi.staticfiles import StaticFiles

from magi_agent.runtime.openmagi_runtime import OpenMagiRuntime

# Token sentinel mirrored from main.py. The gateway token is only surfaced to
# the page when it is the well-known local-dev default, so a real
# ``GATEWAY_TOKEN`` secret is never embedded in a digest-safe surface.
_LOCAL_DEV_TOKEN = "local-dev-token"

BUNDLE_ROOT = Path(__file__).resolve().parent.parent / "web_dashboard"

# Static placeholder served when the bundle is absent (source checkout without
# a web build). Honest build instructions only — no app logic, no runtime data.
_BUNDLE_MISSING_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Magi Agent — dashboard bundle not built</title>
  <style>
    body { margin: 0; min-height: 100vh; display: grid; place-items: center;
           background: #f7f8fb; color: #222736;
           font-family: ui-sans-serif, system-ui, -apple-system, sans-serif; }
    main { max-width: 34rem; padding: 2rem; background: #fff;
           border: 1px solid #dde2eb; border-radius: 8px; }
    code { background: #eef1f7; padding: 0.15em 0.4em; border-radius: 4px; }
    a { color: #7047d8; }
  </style>
</head>
<body>
  <main>
    <h1>dashboard bundle not built</h1>
    <p>This is a source checkout without the built web dashboard. Run
    <code>scripts/build-web-dashboard.sh</code> (requires Node) to build it,
    or install a packaged release via Homebrew or the wheel — packaged
    installs always include the dashboard.</p>
    <p>The API on this port is fully functional without the dashboard.</p>
    <p><a href="https://github.com/openmagi/magi-agent#local-web-dashboard">
    Local web dashboard docs (README)</a></p>
  </main>
</body>
</html>"""


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
        # 3) Chat channel deep link with no prerendered html (a user-created
        #    channel). Serve the chat shell — which resolves the channel from
        #    the live URL client-side — instead of the dashboard index, which
        #    would redirect back to /chat/general (bouncing the user out).
        segments = path.strip("/").split("/")
        if len(segments) >= 3 and segments[1] == "chat":
            shell = _resolve_within_bundle(
                f"dashboard/{segments[0]}/chat/general.html"
            )
            if shell and shell.is_file():
                return _serve_file(shell)
        # 4) SPA fallback — client routing resolves the rest. Never blanks:
        #    serves the app shell.
        return _serve_file(BUNDLE_ROOT / "dashboard.html")

    # Root-level static assets (favicons, manifest, logos, sw.js, landing
    # pages). Registered explicitly per file so no greedy catch-all shadows
    # the /v1, /health, or /learnings routes.
    for entry in sorted(BUNDLE_ROOT.iterdir()):
        if not entry.is_file():
            continue
        name = entry.name
        if name == "index.html":
            continue  # "/" redirects to /dashboard (register_root_redirect)

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


def _register_bundle_missing_placeholder(app: FastAPI) -> None:
    """Mount the static build-instruction placeholder at ``/dashboard``."""

    @app.get("/dashboard", response_class=HTMLResponse)
    def dashboard_placeholder() -> HTMLResponse:
        return HTMLResponse(_BUNDLE_MISSING_HTML)

    @app.get("/dashboard/{path:path}", response_class=HTMLResponse)
    def dashboard_placeholder_deep_link(path: str) -> HTMLResponse:
        return HTMLResponse(_BUNDLE_MISSING_HTML)


def register_dashboard_routes(app: FastAPI, runtime: OpenMagiRuntime) -> None:
    """Single dashboard entry point: static bundle, else build instructions."""
    register_root_redirect(app)
    if bundle_available():
        register_web_dashboard_routes(app, runtime)
        return
    _register_bundle_missing_placeholder(app)
