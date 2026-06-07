from __future__ import annotations

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from magi_agent.observability.page import build_page_router


def test_observability_page_served():
    app = FastAPI()
    app.include_router(build_page_router(SimpleNamespace()))
    client = TestClient(app)
    r = client.get("/observability")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    body = r.text
    assert "Magi Observability" in body
    assert "/api/observability/v1" in body
    # tab labels present
    for label in ("Live", "Sessions", "Health", "Board"):
        assert label in body


def test_page_uses_textcontent_for_event_data():
    """Guard against XSS regressions: dynamic event data must use textContent.

    The page may use innerHTML for static skeleton markup, but all
    event-derived field rendering (kind, tool_name, status, summary, session
    IDs) must go through textContent so raw HTML/script in tool output cannot
    execute.
    """
    from magi_agent.observability.page import _PAGE_HTML

    # textContent must be present (used for all dynamic event field rendering)
    assert "textContent" in _PAGE_HTML

    # The page must NOT assign innerHTML from fetched event fields.
    # We allow innerHTML only for static skeleton construction — verify that
    # the dynamic row builder (buildEventRow) uses textContent exclusively.
    # Count textContent usages vs innerHTML usages in JS; textContent must win.
    tc_count = _PAGE_HTML.count("textContent")
    ih_count = _PAGE_HTML.count(".innerHTML =")
    assert tc_count > 0, "textContent must be used for safe event rendering"
    assert ih_count == 0, (
        "innerHTML assignment found in page — event data must use textContent "
        "to prevent XSS; static HTML should be in the template literal, not JS"
    )


def test_page_token_input_present():
    """Gateway token input must be present so the JS can authenticate API calls."""
    from magi_agent.observability.page import _PAGE_HTML

    assert 'id="obs-token"' in _PAGE_HTML
    assert "local-dev-token" in _PAGE_HTML


def test_page_api_helper_uses_authorization_header():
    """The api() helper must send Authorization: Bearer <token> on every call."""
    from magi_agent.observability.page import _PAGE_HTML

    assert "Authorization" in _PAGE_HTML
    assert "Bearer" in _PAGE_HTML


def test_page_polls_activity_endpoint():
    """Live tab must reference the /activity endpoint with since_id tracking."""
    from magi_agent.observability.page import _PAGE_HTML

    assert "/activity" in _PAGE_HTML
    assert "since_id" in _PAGE_HTML


def test_page_sessions_endpoint_referenced():
    """Sessions tab must reference /sessions and /sessions/{id}/events."""
    from magi_agent.observability.page import _PAGE_HTML

    assert "/sessions" in _PAGE_HTML
    assert "/events" in _PAGE_HTML


def test_page_health_and_board_endpoints_referenced():
    from magi_agent.observability.page import _PAGE_HTML

    assert "/health/live" in _PAGE_HTML
    assert "/board" in _PAGE_HTML
