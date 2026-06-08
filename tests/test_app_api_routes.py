"""Contract + behaviour tests for the dashboard ``/v1/app/*`` API surface.

The static dashboard bundle (``magi_agent/web_dashboard``) is built and committed
independently of this Python backend. Regression history: a dashboard rebuild
shipped a bundle that called a ``/v1/app/*`` API family the runtime never
implemented, so every Overview/Usage/Skills/Memory/Knowledge/Settings page 404'd
with "Failed to load local runtime".

``test_every_bundle_app_path_has_a_backend_route`` is the drift gate: it extracts
every ``/v1/app/*`` path the committed bundle calls and asserts each resolves to a
registered FastAPI route. If a future bundle rebuild adds a new endpoint without
a backend route, this fails before release.
"""

from __future__ import annotations

import re

from fastapi.testclient import TestClient

from magi_agent.app import create_app
from magi_agent.config.models import BuildInfo, RuntimeConfig
from magi_agent.runtime.openmagi_runtime import OpenMagiRuntime
from magi_agent.transport import web_dashboard

_TOKEN = "local-dev-token"
_APP_PATH_RE = re.compile(r"/v1/app/[A-Za-z0-9/_-]+")


def _runtime() -> OpenMagiRuntime:
    return OpenMagiRuntime(
        config=RuntimeConfig(
            bot_id="local-bot",
            user_id="local-user",
            gateway_token=_TOKEN,
            api_proxy_url="http://api-proxy.local",
            chat_proxy_url="http://chat-proxy.local",
            redis_url="redis://redis.local:6379/0",
            model="gpt-5.2",
            build=BuildInfo(version="0.1.0", build_sha="sha-test"),
        )
    )


def _client(tmp_path, monkeypatch) -> TestClient:
    # Isolate the workspace (cwd) and the provider config file so tests never
    # read or write the developer's real ~/.magi/config.toml.
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MAGI_CONFIG", str(tmp_path / "config.toml"))
    client = TestClient(create_app(_runtime()))
    client.headers.update({"x-gateway-token": _TOKEN})
    return client


def _bundle_app_paths() -> set[str]:
    text = "\n".join(
        path.read_text(errors="ignore")
        for path in web_dashboard.BUNDLE_ROOT.glob("_next/static/chunks/*.js")
    )
    return {match.rstrip("/") for match in _APP_PATH_RE.findall(text)}


def _registered_app_templates() -> list[list[str]]:
    app = create_app(_runtime())
    templates: list[list[str]] = []
    for route in app.routes:
        path = getattr(route, "path", "")
        if path.startswith("/v1/app"):
            templates.append([seg for seg in path.split("/") if seg])
    return templates


def _is_covered(path: str, templates: list[list[str]]) -> bool:
    segments = [seg for seg in path.split("/") if seg]
    for template in templates:
        if len(template) != len(segments):
            continue
        if all(t.startswith("{") or t == s for t, s in zip(template, segments)):
            return True
    return False


# --------------------------------------------------------------------------- #
# Drift gate
# --------------------------------------------------------------------------- #
def test_bundle_actually_calls_v1_app_paths() -> None:
    # Sanity: the committed bundle really does depend on this surface, otherwise
    # the gate below would be vacuously true.
    paths = _bundle_app_paths()
    assert "/v1/app/runtime" in paths
    assert "/v1/app/config" in paths


def test_every_bundle_app_path_has_a_backend_route() -> None:
    templates = _registered_app_templates()
    missing = sorted(p for p in _bundle_app_paths() if not _is_covered(p, templates))
    assert not missing, (
        "dashboard bundle calls /v1/app paths with no backend route "
        f"(rebuild shipped ahead of the runtime): {missing}"
    )


# --------------------------------------------------------------------------- #
# Behaviour / shape
# --------------------------------------------------------------------------- #
def test_runtime_returns_expected_sections(tmp_path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)
    res = client.get("/v1/app/runtime")
    assert res.status_code == 200
    body = res.json()
    for section in ("sessions", "tasks", "crons", "artifacts"):
        assert "count" in body[section]
        assert isinstance(body[section]["items"], list)
    # Tools are real (the registry is populated even on the thin shell).
    assert body["tools"]["count"] > 0
    assert "loadedCount" in body["skills"]


def test_config_get_does_not_leak_secrets(tmp_path, monkeypatch) -> None:
    (tmp_path / "config.toml").write_text(
        '[model]\nprovider = "anthropic"\nmodel = "claude-x"\napi_key = "sk-secret"\n',
        encoding="utf-8",
    )
    client = _client(tmp_path, monkeypatch)
    res = client.get("/v1/app/config")
    assert res.status_code == 200
    body = res.json()
    assert body["config"]["llm"]["provider"] == "anthropic"
    assert body["config"]["llm"]["apiKeySet"] is True
    # The raw key must never be serialized into the dashboard surface.
    assert "sk-secret" not in res.text


def test_config_put_writes_toml(tmp_path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)
    res = client.put(
        "/v1/app/config",
        json={"llm": {"provider": "openai", "model": "gpt-x", "apiKey": "sk-new"}},
    )
    assert res.status_code == 200
    written = (tmp_path / "config.toml").read_text(encoding="utf-8")
    assert 'provider = "openai"' in written
    assert 'api_key = "sk-new"' in written


def test_skills_scan_finds_workspace_skill(tmp_path, monkeypatch) -> None:
    skill_dir = tmp_path / "skills" / "demo"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: demo-skill\ndescription: a demo\ntags: a, b\n---\nbody\n",
        encoding="utf-8",
    )
    client = _client(tmp_path, monkeypatch)
    res = client.get("/v1/app/skills")
    assert res.status_code == 200
    body = res.json()
    names = {s["name"] for s in body["loaded"]}
    assert "demo-skill" in names
    assert body["loadedCount"] >= 1
    # reload is a re-scan and returns the same contract.
    assert client.post("/v1/app/skills/reload").status_code == 200


def test_memory_list_read_search(tmp_path, monkeypatch) -> None:
    (tmp_path / "MEMORY.md").write_text("alpha bravo charlie", encoding="utf-8")
    client = _client(tmp_path, monkeypatch)

    listing = client.get("/v1/app/memory").json()
    assert any(f["path"] == "MEMORY.md" for f in listing["files"])

    read = client.get("/v1/app/memory/file", params={"path": "MEMORY.md"})
    assert read.json()["content"] == "alpha bravo charlie"

    search = client.get("/v1/app/memory/search", params={"q": "bravo"})
    assert any(r["path"] == "MEMORY.md" for r in search.json()["results"])


def test_workspace_write_then_read(tmp_path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)
    res = client.put("/v1/app/workspace/file", json={"path": "notes/x.md", "content": "hi"})
    assert res.status_code == 200
    assert (tmp_path / "notes" / "x.md").read_text(encoding="utf-8") == "hi"


def test_sealed_and_traversal_paths_are_rejected(tmp_path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)
    sealed = client.put("/v1/app/workspace/file", json={"path": "AGENTS.md", "content": "x"})
    assert sealed.status_code == 403
    traversal = client.put(
        "/v1/app/workspace/file", json={"path": "../escape.md", "content": "x"}
    )
    assert traversal.status_code == 403


def test_knowledge_index_empty_is_valid(tmp_path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)
    res = client.get("/v1/app/knowledge")
    assert res.status_code == 200
    body = res.json()
    assert body["collections"] == []
    assert body["documents"] == []


def test_requires_gateway_token(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MAGI_CONFIG", str(tmp_path / "config.toml"))
    client = TestClient(create_app(_runtime()))  # no token header
    assert client.get("/v1/app/runtime").status_code == 401
