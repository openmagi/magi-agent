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

import pytest
from fastapi.testclient import TestClient

from magi_agent.app import create_app
from magi_agent.config.models import BuildInfo, RuntimeConfig
from magi_agent.runtime.openmagi_runtime import OpenMagiRuntime
from magi_agent.transport import web_dashboard

_TOKEN = "local-dev-token"
_APP_PATH_RE = re.compile(r"/v1/app/[A-Za-z0-9/_-]+")
_WORKSPACE_ENV_VARS = (
    "MAGI_AGENT_WORKSPACE",
    "CORE_AGENT_PYTHON_GATE5B_FULL_TOOLHOST_WORKSPACE_ROOT",
    "CORE_AGENT_WORKSPACE_ROOT",
)


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
    for name in _WORKSPACE_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    client = TestClient(create_app(_runtime()))
    client.headers.update({"x-gateway-token": _TOKEN})
    return client


def _client_with_workspace_env(tmp_path, monkeypatch, env_name: str, workspace) -> TestClient:
    app_cwd = tmp_path / "app"
    app_cwd.mkdir()
    monkeypatch.chdir(app_cwd)
    monkeypatch.setenv("MAGI_CONFIG", str(tmp_path / "config.toml"))
    for name in _WORKSPACE_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv(env_name, str(workspace))
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
        if len(template) == len(segments):
            if all(t.startswith("{") or t == s for t, s in zip(template, segments)):
                return True
        # JS template-literal truncation: bundle path is the static prefix of a
        # parametric route whose next (final) segment is an interpolated {param}.
        if len(template) == len(segments) + 1 and template[-1].startswith("{"):
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


@pytest.mark.parametrize(
    "llm_payload",
    [
        {"provider": "anthropic", "model": "claude-new"},
        {"provider": "anthropic", "model": "claude-new", "apiKey": ""},
    ],
)
def test_config_put_blank_api_key_preserves_existing_key(
    tmp_path, monkeypatch, llm_payload
) -> None:
    (tmp_path / "config.toml").write_text(
        (
            '[model]\nprovider = "anthropic"\nmodel = "claude-old"\n'
            'api_key = "sk-existing"\n'
        ),
        encoding="utf-8",
    )
    client = _client(tmp_path, monkeypatch)
    res = client.put("/v1/app/config", json={"llm": llm_payload})
    assert res.status_code == 200
    written = (tmp_path / "config.toml").read_text(encoding="utf-8")
    assert 'model = "claude-new"' in written
    assert 'api_key = "sk-existing"' in written
    assert "sk-existing" not in res.text


def test_config_put_maps_google_provider_to_cli_gemini(tmp_path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)
    res = client.put(
        "/v1/app/config",
        json={"llm": {"provider": "google", "model": "gemini-x", "apiKey": "g-key"}},
    )
    assert res.status_code == 200
    written = (tmp_path / "config.toml").read_text(encoding="utf-8")
    assert 'provider = "gemini"' in written
    assert 'api_key = "g-key"' in written


def test_config_put_rejects_unsupported_provider_without_overwriting(
    tmp_path, monkeypatch
) -> None:
    original = '[model]\nprovider = "openai"\nmodel = "gpt-x"\napi_key = "sk-existing"\n'
    (tmp_path / "config.toml").write_text(original, encoding="utf-8")
    client = _client(tmp_path, monkeypatch)
    res = client.put(
        "/v1/app/config",
        json={
            "llm": {
                "provider": "openai-compatible",
                "model": "local-model",
                "apiKey": "sk-new",
            }
        },
    )
    assert res.status_code == 400
    assert res.json()["error"] == "unsupported_provider"
    assert (tmp_path / "config.toml").read_text(encoding="utf-8") == original


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


def test_app_api_uses_hosted_workspace_env_for_skills_and_memory(
    tmp_path, monkeypatch
) -> None:
    workspace = tmp_path / "workspace"
    skill_dir = workspace / "skills" / "hosted-demo"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: hosted-demo-skill\ndescription: hosted demo\n---\n",
        encoding="utf-8",
    )
    (workspace / "MEMORY.md").write_text("hosted memory fact", encoding="utf-8")

    client = _client_with_workspace_env(
        tmp_path,
        monkeypatch,
        "CORE_AGENT_PYTHON_GATE5B_FULL_TOOLHOST_WORKSPACE_ROOT",
        workspace,
    )

    skills = client.get("/v1/app/skills").json()
    assert "hosted-demo-skill" in {s["name"] for s in skills["loaded"]}

    listing = client.get("/v1/app/memory").json()
    assert any(f["path"] == "MEMORY.md" for f in listing["files"])
    read = client.get("/v1/app/memory/file", params={"path": "MEMORY.md"})
    assert read.json()["content"] == "hosted memory fact"


def test_app_api_skills_scan_is_uncapped_and_includes_hosted_legacy_sibling(
    tmp_path, monkeypatch
) -> None:
    hosted_parent = tmp_path / "workspace"
    workspace = hosted_parent / "workspace"
    workspace.mkdir(parents=True)
    for index in range(120):
        skill_dir = workspace / "skills" / f"bulk-skill-{index:03d}"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            f"---\nname: bulk-skill-{index:03d}\ndescription: bulk\n---\n",
            encoding="utf-8",
        )
    qmd = workspace / "skills" / "qmd-search"
    qmd.mkdir(parents=True)
    (qmd / "SKILL.md").write_text(
        "---\nname: qmd-search\ndescription: qmd\n---\n",
        encoding="utf-8",
    )
    legacy = hosted_parent / "skills" / "moltbook"
    legacy.mkdir(parents=True)
    (legacy / "SKILL.md").write_text(
        "---\nname: moltbook\ndescription: legacy\n---\n",
        encoding="utf-8",
    )

    client = _client_with_workspace_env(
        tmp_path,
        monkeypatch,
        "MAGI_AGENT_WORKSPACE",
        workspace,
    )

    body = client.get("/v1/app/skills").json()
    dirs = {skill["dir"] for skill in body["loaded"]}
    assert "skills/qmd-search" in dirs
    assert "legacy-workspace/skills/moltbook" in dirs
    assert body["loadedCount"] == len(body["loaded"])


def test_app_api_discovers_hosted_nested_legacy_workspace_roots(
    tmp_path, monkeypatch
) -> None:
    pvc_root = tmp_path / "workspace"
    direct_workspace = pvc_root / "workspace"
    openclaw_workspace = pvc_root / "openclaw-home" / "workspace"
    agent_workspace = pvc_root / "agents" / "main" / "workspace"
    for workspace, name in (
        (direct_workspace, "direct-legacy"),
        (openclaw_workspace, "openclaw-legacy"),
        (agent_workspace, "agent-legacy"),
    ):
        (workspace / "memory" / "daily").mkdir(parents=True)
        (workspace / "memory" / "daily" / f"{name}.md").write_text(
            f"{name} memory fact",
            encoding="utf-8",
        )
        skill_dir = workspace / "skills" / name
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: hosted legacy\n---\n",
            encoding="utf-8",
        )

    client = _client_with_workspace_env(
        tmp_path,
        monkeypatch,
        "MAGI_AGENT_WORKSPACE",
        pvc_root,
    )

    listing = client.get("/v1/app/memory").json()
    paths = {file["path"] for file in listing["files"]}
    assert "memory/daily/direct-legacy.md" in paths
    assert "memory/daily/openclaw-legacy.md" in paths
    assert "memory/daily/agent-legacy.md" in paths

    read = client.get(
        "/v1/app/memory/file",
        params={"path": "memory/daily/openclaw-legacy.md"},
    )
    assert read.status_code == 200
    assert read.json()["content"] == "openclaw-legacy memory fact"

    search = client.get("/v1/app/memory/search", params={"q": "agent-legacy"})
    assert any(
        r["path"] == "memory/daily/agent-legacy.md" for r in search.json()["results"]
    )

    skills = client.get("/v1/app/skills").json()
    names = {skill["name"] for skill in skills["loaded"]}
    assert {"direct-legacy", "openclaw-legacy", "agent-legacy"} <= names


def test_local_cwd_does_not_discover_nested_workspace_without_env(
    tmp_path, monkeypatch
) -> None:
    nested = tmp_path / "workspace" / "memory"
    nested.mkdir(parents=True)
    (nested / "ROOT.md").write_text("nested local memory", encoding="utf-8")

    client = _client(tmp_path, monkeypatch)

    listing = client.get("/v1/app/memory").json()
    assert "memory/ROOT.md" not in {file["path"] for file in listing["files"]}
    read = client.get("/v1/app/memory/file", params={"path": "memory/ROOT.md"})
    assert read.status_code == 404


def test_workspace_write_uses_discovered_hosted_legacy_workspace_root(
    tmp_path, monkeypatch
) -> None:
    pvc_root = tmp_path / "workspace"
    (pvc_root / "memory").mkdir(parents=True)
    hosted_workspace = pvc_root / "agents" / "main" / "workspace"
    (hosted_workspace / "memory").mkdir(parents=True)
    (hosted_workspace / "memory" / "ROOT.md").write_text(
        "hosted state", encoding="utf-8"
    )
    client = _client_with_workspace_env(
        tmp_path,
        monkeypatch,
        "MAGI_AGENT_WORKSPACE",
        pvc_root,
    )

    res = client.put(
        "/v1/app/workspace/file",
        json={"path": "notes/from-dashboard.md", "content": "hosted note"},
    )

    assert res.status_code == 200
    assert (hosted_workspace / "notes" / "from-dashboard.md").read_text(
        encoding="utf-8"
    ) == "hosted note"
    assert not (pvc_root / "notes" / "from-dashboard.md").exists()


def test_workspace_env_precedence_and_traversal_protection(tmp_path, monkeypatch) -> None:
    primary = tmp_path / "primary-workspace"
    fallback = tmp_path / "fallback-workspace"
    primary.mkdir()
    fallback.mkdir()
    (primary / "MEMORY.md").write_text("primary memory", encoding="utf-8")
    (fallback / "MEMORY.md").write_text("fallback memory", encoding="utf-8")

    client = _client_with_workspace_env(
        tmp_path,
        monkeypatch,
        "CORE_AGENT_PYTHON_GATE5B_FULL_TOOLHOST_WORKSPACE_ROOT",
        fallback,
    )
    monkeypatch.setenv("MAGI_AGENT_WORKSPACE", str(primary))

    config = client.get("/v1/app/config").json()
    assert config["config"]["workspace"] == str(primary.resolve())
    read = client.get("/v1/app/memory/file", params={"path": "MEMORY.md"})
    assert read.json()["content"] == "primary memory"

    traversal_read = client.get("/v1/app/memory/file", params={"path": "../escape.md"})
    assert traversal_read.status_code == 403
    traversal_write = client.put(
        "/v1/app/workspace/file", json={"path": "../escape.md", "content": "x"}
    )
    assert traversal_write.status_code == 403
    assert not (tmp_path / "escape.md").exists()


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
    for name in _WORKSPACE_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    client = TestClient(create_app(_runtime()))  # no token header
    assert client.get("/v1/app/runtime").status_code == 401
