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

# The providers API reports `configured`/`apiKeySet` by resolving a key from the
# config file OR the provider's env var (app_api.py reads os.environ). These
# tests assert configured-state purely from the tmp config file, so any ambient
# provider key in the env (CI, dev shells) would make a provider read as
# configured even after its stored key is deleted. Clear them for hermetic runs.
_PROVIDER_KEY_ENV_VARS = (
    "MAGI_PROVIDER",
    "MAGI_MODEL",
    "MAGI_LLM_API_KEY",
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
    "FIREWORKS_API_KEY",
    "OPENROUTER_API_KEY",
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
    for name in (*_WORKSPACE_ENV_VARS, *_PROVIDER_KEY_ENV_VARS):
        monkeypatch.delenv(name, raising=False)
    client = TestClient(create_app(_runtime()))
    client.headers.update({"x-gateway-token": _TOKEN})
    return client


def _client_with_workspace_env(tmp_path, monkeypatch, env_name: str, workspace) -> TestClient:
    app_cwd = tmp_path / "app"
    app_cwd.mkdir()
    monkeypatch.chdir(app_cwd)
    monkeypatch.setenv("MAGI_CONFIG", str(tmp_path / "config.toml"))
    for name in (*_WORKSPACE_ENV_VARS, *_PROVIDER_KEY_ENV_VARS):
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
        if len(template) < len(segments):
            continue
        # The bundle's segments must agree with the template prefix (literal
        # segments equal; {param} segments match anything).
        if not all(t.startswith("{") or t == s for t, s in zip(template, segments)):
            continue
        # Exact-length match, OR JS template-literal truncation: the bundle path
        # is the static prefix of a parametric route and EVERY remaining template
        # segment is an interpolated {param} (handles routes with >1 trailing
        # param, e.g. /v1/app/customize/verification/{kind}/{item_id}).
        if all(seg.startswith("{") for seg in template[len(segments):]):
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


def test_app_tools_alias_returns_existing_tool_inventory(tmp_path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)

    app_res = client.get("/v1/app/tools")
    api_res = client.get("/api/tools")

    assert app_res.status_code == 200
    assert api_res.status_code == 200
    assert app_res.json() == api_res.json()
    assert set(app_res.json()) == {"tools"}
    assert app_res.json()["tools"]


def test_app_tools_alias_requires_gateway_token(tmp_path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)
    client.headers.pop("x-gateway-token", None)

    res = client.get("/v1/app/tools")

    assert res.status_code == 401
    assert res.json() == {"error": "unauthorized"}


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
    # Default (no vector param) uses the substring matcher.
    assert search.json()["mode"] == "substring"


def test_memory_search_vector_opt_in_uses_vector_backend(tmp_path, monkeypatch) -> None:
    from magi_agent.transport import app_api

    (tmp_path / "MEMORY.md").write_text("alpha bravo charlie", encoding="utf-8")
    client = _client(tmp_path, monkeypatch)

    captured: dict[str, object] = {}

    def fake_vector(query: str, limit: int):
        captured["query"] = query
        captured["limit"] = limit
        return [{"path": "memory/daily/x.md", "score": 0.91, "context": "semantic hit",
                 "contentPreview": "semantic hit"}]

    monkeypatch.setattr(app_api, "_vector_memory_search", fake_vector)

    resp = client.get("/v1/app/memory/search", params={"q": "meaning", "vector": 1})
    body = resp.json()
    assert body["mode"] == "vector"
    assert body["results"][0]["path"] == "memory/daily/x.md"
    assert captured["query"] == "meaning"


def test_memory_search_vector_falls_back_when_unavailable(tmp_path, monkeypatch) -> None:
    """vector=1 but the vector backend is unusable (returns None) -> substring."""
    from magi_agent.transport import app_api

    (tmp_path / "MEMORY.md").write_text("alpha bravo charlie", encoding="utf-8")
    client = _client(tmp_path, monkeypatch)
    monkeypatch.setattr(app_api, "_vector_memory_search", lambda q, limit: None)

    resp = client.get("/v1/app/memory/search", params={"q": "bravo", "vector": 1})
    body = resp.json()
    assert body["mode"] == "substring"
    assert any(r["path"] == "MEMORY.md" for r in body["results"])


def test_vector_memory_search_helper_maps_hits_when_opted_in(tmp_path, monkeypatch) -> None:
    from magi_agent.memory.search.base import SearchCapabilities, SearchHit
    from magi_agent.transport import app_api

    monkeypatch.chdir(tmp_path)
    for name in app_api._WORKSPACE_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("MAGI_MEMORY_VECTOR_SEARCH", "1")

    class _FakeVectorBackend:
        capabilities = SearchCapabilities(name="qmd", supports_vector=True)

        def reindex(self, root):
            return None

        def search(self, query, *, k):
            return [SearchHit(path="memory/daily/a.md", content="semantic body", score=0.8)]

    monkeypatch.setattr(
        "magi_agent.memory.search.select_search_backend",
        lambda config, *, vector=False: _FakeVectorBackend(),
    )

    out = app_api._vector_memory_search("anything", 5)
    assert out == [
        {"path": "memory/daily/a.md", "score": 0.8,
         "context": "semantic body", "contentPreview": "semantic body"}
    ]


def test_vector_memory_search_helper_returns_none_without_opt_in(tmp_path, monkeypatch) -> None:
    from magi_agent.transport import app_api

    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("MAGI_MEMORY_VECTOR_SEARCH", raising=False)
    # Opt-in OFF -> helper returns None so the caller uses substring search.
    assert app_api._vector_memory_search("anything", 5) is None


def test_memory_archive_is_listed_and_readable(tmp_path, monkeypatch) -> None:
    archive = tmp_path / "memory" / "archive"
    archive.mkdir(parents=True)
    (archive / "2026-06-20-daily.md").write_text("pre-compaction snapshot", encoding="utf-8")
    client = _client(tmp_path, monkeypatch)

    listing = client.get("/v1/app/memory").json()
    assert any(f["path"] == "memory/archive/2026-06-20-daily.md" for f in listing["files"])

    read = client.get(
        "/v1/app/memory/file", params={"path": "memory/archive/2026-06-20-daily.md"}
    )
    assert read.status_code == 200
    assert read.json()["content"] == "pre-compaction snapshot"


def test_memory_archive_is_read_only_write_rejected(tmp_path, monkeypatch) -> None:
    archive = tmp_path / "memory" / "archive"
    archive.mkdir(parents=True)
    target = archive / "2026-06-20-daily.md"
    target.write_text("original snapshot", encoding="utf-8")
    client = _client(tmp_path, monkeypatch)

    res = client.put(
        "/v1/app/workspace/file",
        json={"path": "memory/archive/2026-06-20-daily.md", "content": "tampered"},
    )
    assert res.status_code == 403
    assert res.json()["error"] == "forbidden_path"
    # On-disk content is untouched.
    assert target.read_text(encoding="utf-8") == "original snapshot"


def test_memory_archive_is_read_only_delete_skipped(tmp_path, monkeypatch) -> None:
    archive = tmp_path / "memory" / "archive"
    archive.mkdir(parents=True)
    target = archive / "2026-06-20-daily.md"
    target.write_text("keep me", encoding="utf-8")
    client = _client(tmp_path, monkeypatch)

    res = client.request(
        "DELETE",
        "/v1/app/memory/files",
        json={"paths": ["memory/archive/2026-06-20-daily.md"]},
    )
    assert res.status_code == 200
    assert res.json()["deleted"] == []
    assert target.exists()


def test_memory_lists_identity_files_project_and_global(tmp_path, monkeypatch) -> None:
    # Global self-identity lives in ~/.magi; project override in <workspace>/.magi.
    # The Memory dashboard must surface BOTH so what feeds the system prompt
    # (magi_agent.cli.identity.load_identity) is visible.
    home = tmp_path / "home"
    (home / ".magi").mkdir(parents=True)
    (home / ".magi" / "USER.md").write_text("kevin facts", encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))

    workspace = tmp_path / "ws"
    (workspace / ".magi").mkdir(parents=True)
    (workspace / ".magi" / "IDENTITY.md").write_text("who the agent is", encoding="utf-8")
    monkeypatch.chdir(workspace)
    monkeypatch.setenv("MAGI_CONFIG", str(tmp_path / "config.toml"))
    for name in (*_WORKSPACE_ENV_VARS, *_PROVIDER_KEY_ENV_VARS):
        monkeypatch.delenv(name, raising=False)
    client = TestClient(create_app(_runtime()))
    client.headers.update({"x-gateway-token": _TOKEN})

    paths = {f["path"] for f in client.get("/v1/app/memory").json()["files"]}
    assert ".magi/IDENTITY.md" in paths
    assert "~/.magi/USER.md" in paths

    proj = client.get("/v1/app/memory/file", params={"path": ".magi/IDENTITY.md"})
    assert proj.json()["content"] == "who the agent is"
    glob = client.get("/v1/app/memory/file", params={"path": "~/.magi/USER.md"})
    assert glob.json()["content"] == "kevin facts"


def test_memory_identity_sealed_agents_md_not_listed(tmp_path, monkeypatch) -> None:
    # AGENTS.md is a sealed basename (cross-tool convention file); it must NOT be
    # surfaced via the Memory listing even when present in the .magi namespace.
    workspace = tmp_path / "ws"
    (workspace / ".magi").mkdir(parents=True)
    (workspace / ".magi" / "AGENTS.md").write_text("roster", encoding="utf-8")
    monkeypatch.chdir(workspace)
    monkeypatch.setenv("MAGI_CONFIG", str(tmp_path / "config.toml"))
    for name in (*_WORKSPACE_ENV_VARS, *_PROVIDER_KEY_ENV_VARS):
        monkeypatch.delenv(name, raising=False)
    client = TestClient(create_app(_runtime()))
    client.headers.update({"x-gateway-token": _TOKEN})

    paths = {f["path"] for f in client.get("/v1/app/memory").json()["files"]}
    assert ".magi/AGENTS.md" not in paths


def test_memory_identity_file_not_deletable(tmp_path, monkeypatch) -> None:
    workspace = tmp_path / "ws"
    (workspace / ".magi").mkdir(parents=True)
    target = workspace / ".magi" / "IDENTITY.md"
    target.write_text("keep me", encoding="utf-8")
    monkeypatch.chdir(workspace)
    monkeypatch.setenv("MAGI_CONFIG", str(tmp_path / "config.toml"))
    for name in (*_WORKSPACE_ENV_VARS, *_PROVIDER_KEY_ENV_VARS):
        monkeypatch.delenv(name, raising=False)
    client = TestClient(create_app(_runtime()))
    client.headers.update({"x-gateway-token": _TOKEN})

    res = client.request(
        "DELETE", "/v1/app/memory/files", json={"paths": [".magi/IDENTITY.md"]}
    )
    assert res.status_code == 200
    assert res.json()["deleted"] == []
    assert target.exists()


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


def test_knowledge_upload_writes_binary_and_lists_in_index(tmp_path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)
    payload = b"%PDF-1.4 binary\x00\x01\x02 body"
    res = client.post(
        "/v1/app/knowledge/upload",
        content=payload,
        headers={"x-filename": "report.pdf", "content-type": "application/pdf"},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["status"] == "ready"
    assert body["filename"] == "report.pdf"
    assert body["collection"] == "Downloads"
    assert body["doc_id"] == "knowledge/Downloads/report.pdf"
    # Bytes land on disk verbatim.
    on_disk = tmp_path / "knowledge" / "Downloads" / "report.pdf"
    assert on_disk.read_bytes() == payload
    # And it shows up in the knowledge index the read panel consumes.
    index = client.get("/v1/app/knowledge").json()
    paths = {doc["path"] for doc in index["documents"]}
    assert "knowledge/Downloads/report.pdf" in paths


def test_knowledge_upload_sanitizes_traversal_filename(tmp_path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)
    res = client.post(
        "/v1/app/knowledge/upload",
        content=b"x",
        headers={"x-filename": "../../etc/escape.txt", "content-type": "text/plain"},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    # No path escape: the stored file stays inside knowledge/Downloads.
    assert body["doc_id"].startswith("knowledge/Downloads/")
    assert ".." not in body["doc_id"]
    assert not (tmp_path.parent / "etc" / "escape.txt").exists()


def test_knowledge_upload_rejects_missing_filename(tmp_path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)
    res = client.post(
        "/v1/app/knowledge/upload",
        content=b"x",
        headers={"content-type": "text/plain"},
    )
    assert res.status_code == 400


def test_knowledge_upload_disambiguates_collision(tmp_path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)
    headers = {"x-filename": "notes.txt", "content-type": "text/plain"}
    first = client.post("/v1/app/knowledge/upload", content=b"one", headers=headers)
    second = client.post("/v1/app/knowledge/upload", content=b"two", headers=headers)
    assert first.status_code == 200 and second.status_code == 200
    d1 = first.json()["doc_id"]
    d2 = second.json()["doc_id"]
    assert d1 != d2
    assert (tmp_path / "knowledge" / "Downloads" / "notes.txt").read_bytes() == b"one"
    # Second write did not clobber the first.
    assert (tmp_path / d2).read_bytes() == b"two"


def test_knowledge_upload_requires_gateway_token(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MAGI_CONFIG", str(tmp_path / "config.toml"))
    for name in (*_WORKSPACE_ENV_VARS, *_PROVIDER_KEY_ENV_VARS):
        monkeypatch.delenv(name, raising=False)
    client = TestClient(create_app(_runtime()))  # no token header
    res = client.post(
        "/v1/app/knowledge/upload",
        content=b"x",
        headers={"x-filename": "a.txt"},
    )
    assert res.status_code == 401


def test_requires_gateway_token(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MAGI_CONFIG", str(tmp_path / "config.toml"))
    for name in (*_WORKSPACE_ENV_VARS, *_PROVIDER_KEY_ENV_VARS):
        monkeypatch.delenv(name, raising=False)
    client = TestClient(create_app(_runtime()))  # no token header
    assert client.get("/v1/app/runtime").status_code == 401


# --------------------------------------------------------------------------- #
# D1a: /v1/app/providers GET + PUT + persist_provider_keys
# --------------------------------------------------------------------------- #

def test_providers_get_lists_all_supported_providers(tmp_path, monkeypatch) -> None:
    """GET /v1/app/providers lists every SUPPORTED_PROVIDER entry."""
    from magi_agent.cli.providers import SUPPORTED_PROVIDERS

    client = _client(tmp_path, monkeypatch)
    res = client.get("/v1/app/providers")
    assert res.status_code == 200
    body = res.json()
    assert "providers" in body
    names = [p["name"] for p in body["providers"]]
    assert names == list(SUPPORTED_PROVIDERS)


def test_providers_get_configured_flag_reflects_only_keyed_providers(
    tmp_path, monkeypatch
) -> None:
    """With FIREWORKS_API_KEY in config, exactly fireworks is configured:true."""
    (tmp_path / "config.toml").write_text(
        '[providers.fireworks]\napi_key = "fw-secret"\n',
        encoding="utf-8",
    )
    client = _client(tmp_path, monkeypatch)
    res = client.get("/v1/app/providers")
    assert res.status_code == 200
    body = res.json()
    by_name = {p["name"]: p for p in body["providers"]}
    assert by_name["fireworks"]["configured"] is True
    for name, info in by_name.items():
        if name != "fireworks":
            assert info["configured"] is False


def test_providers_get_does_not_leak_api_key(tmp_path, monkeypatch) -> None:
    """GET /v1/app/providers never serializes a raw API key value."""
    (tmp_path / "config.toml").write_text(
        '[providers.anthropic]\napi_key = "sk-ant-secret"\n',
        encoding="utf-8",
    )
    client = _client(tmp_path, monkeypatch)
    res = client.get("/v1/app/providers")
    assert res.status_code == 200
    assert "sk-ant-secret" not in res.text


def test_providers_put_writes_two_provider_keys(tmp_path, monkeypatch) -> None:
    """PUT with two providers' keys writes both to config.toml."""
    client = _client(tmp_path, monkeypatch)
    res = client.put(
        "/v1/app/providers",
        json={
            "providers": {
                "anthropic": {"apiKey": "sk-ant-new"},
                "openai": {"apiKey": "sk-oai-new"},
            }
        },
    )
    assert res.status_code == 200
    written = (tmp_path / "config.toml").read_text(encoding="utf-8")
    assert "sk-ant-new" in written
    assert "sk-oai-new" in written


def test_providers_put_then_get_shows_both_configured(tmp_path, monkeypatch) -> None:
    """After PUT sets two keys, GET shows both as configured:true."""
    client = _client(tmp_path, monkeypatch)
    client.put(
        "/v1/app/providers",
        json={
            "providers": {
                "anthropic": {"apiKey": "sk-ant-abc"},
                "fireworks": {"apiKey": "fw-key-abc"},
            }
        },
    )
    res = client.get("/v1/app/providers")
    body = res.json()
    by_name = {p["name"]: p for p in body["providers"]}
    assert by_name["anthropic"]["configured"] is True
    assert by_name["fireworks"]["configured"] is True


def test_providers_put_then_configured_providers_returns_both(tmp_path, monkeypatch) -> None:
    """After PUT, configured_providers() on the written config returns both names."""
    import tomllib
    from magi_agent.cli.providers import configured_providers

    client = _client(tmp_path, monkeypatch)
    client.put(
        "/v1/app/providers",
        json={
            "providers": {
                "anthropic": {"apiKey": "sk-ant-abc"},
                "openai": {"apiKey": "sk-oai-abc"},
            }
        },
    )
    config_path = tmp_path / "config.toml"
    with open(config_path, "rb") as fh:
        raw = tomllib.load(fh)
    result = configured_providers(env={}, config=raw)
    assert "anthropic" in result
    assert "openai" in result


def test_providers_put_active_sets_model_provider(tmp_path, monkeypatch) -> None:
    """PUT with active sets [model].provider in config.toml."""
    client = _client(tmp_path, monkeypatch)
    res = client.put(
        "/v1/app/providers",
        json={
            "providers": {"fireworks": {"apiKey": "fw-key"}},
            "active": "fireworks",
        },
    )
    assert res.status_code == 200
    written = (tmp_path / "config.toml").read_text(encoding="utf-8")
    assert 'provider = "fireworks"' in written


def test_providers_put_empty_api_key_deletes_stored_key(tmp_path, monkeypatch) -> None:
    """PUT with apiKey:'' removes a previously stored provider key."""
    (tmp_path / "config.toml").write_text(
        '[providers.anthropic]\napi_key = "sk-ant-existing"\n',
        encoding="utf-8",
    )
    client = _client(tmp_path, monkeypatch)
    res = client.put(
        "/v1/app/providers",
        json={"providers": {"anthropic": {"apiKey": ""}}},
    )
    assert res.status_code == 200
    body = res.json()
    by_name = {p["name"]: p for p in body["providers"]}
    assert by_name["anthropic"]["configured"] is False


def test_providers_put_unknown_provider_returns_400(tmp_path, monkeypatch) -> None:
    """PUT with an unknown provider name → 400 unsupported_provider."""
    client = _client(tmp_path, monkeypatch)
    res = client.put(
        "/v1/app/providers",
        json={"providers": {"nonexistent-llm": {"apiKey": "key"}}},
    )
    assert res.status_code == 400
    assert res.json()["error"] == "unsupported_provider"


def test_providers_put_coexists_with_config_put_model_selection(
    tmp_path, monkeypatch
) -> None:
    """PUT /v1/app/providers key, then PUT /v1/app/config selecting a model — key survives."""
    client = _client(tmp_path, monkeypatch)
    # Step 1: store a fireworks key via the providers endpoint.
    client.put(
        "/v1/app/providers",
        json={"providers": {"fireworks": {"apiKey": "fw-secret-coexist"}}},
    )
    # Step 2: update the model selection via /v1/app/config.
    client.put(
        "/v1/app/config",
        json={"llm": {"provider": "openai", "model": "gpt-5.5", "apiKey": "sk-oai"}},
    )
    # Verify the fireworks key was NOT clobbered.
    written = (tmp_path / "config.toml").read_text(encoding="utf-8")
    assert "fw-secret-coexist" in written
    assert 'provider = "openai"' in written


def test_providers_get_requires_gateway_token(tmp_path, monkeypatch) -> None:
    """GET /v1/app/providers without auth → 401."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MAGI_CONFIG", str(tmp_path / "config.toml"))
    for name in (*_WORKSPACE_ENV_VARS, *_PROVIDER_KEY_ENV_VARS):
        monkeypatch.delenv(name, raising=False)
    client = TestClient(create_app(_runtime()))  # no token header
    assert client.get("/v1/app/providers").status_code == 401


def test_providers_put_requires_gateway_token(tmp_path, monkeypatch) -> None:
    """PUT /v1/app/providers without auth → 401."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MAGI_CONFIG", str(tmp_path / "config.toml"))
    for name in (*_WORKSPACE_ENV_VARS, *_PROVIDER_KEY_ENV_VARS):
        monkeypatch.delenv(name, raising=False)
    client = TestClient(create_app(_runtime()))  # no token header
    res = client.put("/v1/app/providers", json={"providers": {}})
    assert res.status_code == 401


# --------------------------------------------------------------------------- #
# D1a: persist_provider_keys unit tests
# --------------------------------------------------------------------------- #

def test_persist_provider_keys_round_trip_preserves_unrelated_sections(
    tmp_path,
) -> None:
    """persist_provider_keys preserves [model] and other existing sections."""
    import tomllib
    from magi_agent.cli.providers import persist_provider_keys

    config_path = tmp_path / "config.toml"
    config_path.write_text(
        '[model]\nprovider = "anthropic"\nmodel = "claude-x"\n\n'
        '[custom_section]\nfoo = "bar"\n',
        encoding="utf-8",
    )
    persist_provider_keys({"openai": "sk-oai-test"}, path=config_path)

    with open(config_path, "rb") as fh:
        raw = tomllib.load(fh)

    # The new key was written.
    assert raw["providers"]["openai"]["api_key"] == "sk-oai-test"
    # Existing [model] section preserved.
    assert raw["model"]["provider"] == "anthropic"
    assert raw["model"]["model"] == "claude-x"
    # Unrelated [custom_section] preserved.
    assert raw["custom_section"]["foo"] == "bar"


def test_persist_provider_keys_delete_semantics(tmp_path) -> None:
    """Passing None or '' removes a provider key and drops the empty table."""
    import tomllib
    from magi_agent.cli.providers import persist_provider_keys

    config_path = tmp_path / "config.toml"
    config_path.write_text(
        '[providers.anthropic]\napi_key = "sk-ant-existing"\n',
        encoding="utf-8",
    )
    persist_provider_keys({"anthropic": None}, path=config_path)

    with open(config_path, "rb") as fh:
        raw = tomllib.load(fh)

    assert "providers" not in raw or "anthropic" not in raw.get("providers", {})


def test_persist_provider_keys_delete_empty_string(tmp_path) -> None:
    """Passing '' also removes a provider key."""
    import tomllib
    from magi_agent.cli.providers import persist_provider_keys

    config_path = tmp_path / "config.toml"
    config_path.write_text(
        '[providers.openai]\napi_key = "sk-oai-existing"\n',
        encoding="utf-8",
    )
    persist_provider_keys({"openai": ""}, path=config_path)

    with open(config_path, "rb") as fh:
        raw = tomllib.load(fh)

    assert "providers" not in raw or "openai" not in raw.get("providers", {})


def test_persist_provider_keys_unknown_provider_raises(tmp_path) -> None:
    """Unknown provider name raises UnknownProviderError."""
    from magi_agent.cli.providers import UnknownProviderError, persist_provider_keys

    config_path = tmp_path / "config.toml"
    with pytest.raises(UnknownProviderError):
        persist_provider_keys({"nonexistent": "key"}, path=config_path)


def test_persist_provider_keys_0600_permissions(tmp_path) -> None:
    """Written config.toml has 0600 permissions."""
    import stat as _stat
    from magi_agent.cli.providers import persist_provider_keys

    config_path = tmp_path / "config.toml"
    persist_provider_keys({"anthropic": "sk-ant-test"}, path=config_path)

    mode = config_path.stat().st_mode
    # Owner read+write (0o600), nothing else.
    assert mode & 0o777 == _stat.S_IRUSR | _stat.S_IWUSR


# C1 — PUT /v1/app/config with apiKey must produce a 0600 file
def test_config_put_with_api_key_produces_0600_file(tmp_path, monkeypatch) -> None:
    """PUT /v1/app/config containing an apiKey → config.toml is 0600, not world-readable."""
    import stat as _stat

    client = _client(tmp_path, monkeypatch)
    res = client.put(
        "/v1/app/config",
        json={"llm": {"provider": "openai", "model": "gpt-5.5", "apiKey": "sk-secret-c1"}},
    )
    assert res.status_code == 200
    config_path = tmp_path / "config.toml"
    assert config_path.exists(), "config.toml was not written"
    mode = config_path.stat().st_mode
    assert mode & 0o777 == _stat.S_IRUSR | _stat.S_IWUSR, (
        f"Expected 0o600 but got {oct(mode & 0o777)}"
    )


# C2 — PUT /v1/app/providers with BOTH apiKey AND model must produce 0600
#       AND persist both values correctly.
def test_providers_put_with_key_and_model_produces_0600_file(
    tmp_path, monkeypatch
) -> None:
    """PUT /v1/app/providers with apiKey+model → file is 0600, key and model both stored."""
    import stat as _stat
    import tomllib

    client = _client(tmp_path, monkeypatch)
    res = client.put(
        "/v1/app/providers",
        json={
            "providers": {
                "openai": {"apiKey": "sk-oai-c2", "model": "gpt-5.5"},
            }
        },
    )
    assert res.status_code == 200

    config_path = tmp_path / "config.toml"
    assert config_path.exists(), "config.toml was not written"

    # File-mode check (the main security invariant).
    mode = config_path.stat().st_mode
    assert mode & 0o777 == _stat.S_IRUSR | _stat.S_IWUSR, (
        f"Expected 0o600 but got {oct(mode & 0o777)}"
    )

    # Both values must be persisted.
    with open(config_path, "rb") as fh:
        raw = tomllib.load(fh)
    assert raw["providers"]["openai"]["api_key"] == "sk-oai-c2"
    assert raw["providers"]["openai"]["model"] == "gpt-5.5"


def test_providers_put_with_key_and_model_get_shows_configured_and_model(
    tmp_path, monkeypatch
) -> None:
    """After PUT with apiKey+model, GET /v1/app/providers shows configured:true and the model."""
    client = _client(tmp_path, monkeypatch)
    client.put(
        "/v1/app/providers",
        json={
            "providers": {
                "openai": {"apiKey": "sk-oai-c2b", "model": "gpt-5.5"},
            }
        },
    )
    res = client.get("/v1/app/providers")
    assert res.status_code == 200
    by_name = {p["name"]: p for p in res.json()["providers"]}
    assert by_name["openai"]["configured"] is True
    assert by_name["openai"]["model"] == "gpt-5.5"


# --------------------------------------------------------------------------- #
# Workspace file listing (Knowledge → Workspace tab, local self-host)
# --------------------------------------------------------------------------- #
def _seed(root, rel: str, text: str = "x") -> None:
    from pathlib import Path

    p = Path(root) / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def test_workspace_lists_root_and_nested_files(tmp_path, monkeypatch) -> None:
    _seed(tmp_path, "whitepaper.html", "<h1>hi</h1>")
    _seed(tmp_path, "notes.md", "# notes")
    _seed(tmp_path, "sub/report.txt", "body")
    client = _client(tmp_path, monkeypatch)

    res = client.get("/v1/app/workspace")
    assert res.status_code == 200
    paths = {row["path"] for row in res.json()["files"]}
    assert {"whitepaper.html", "notes.md", "sub/report.txt"} <= paths
    row = next(r for r in res.json()["files"] if r["path"] == "notes.md")
    assert row["size"] > 0 and isinstance(row["modifiedAt"], str)


def test_workspace_excludes_dedicated_tabs_and_noise(tmp_path, monkeypatch) -> None:
    _seed(tmp_path, "keep.md")
    _seed(tmp_path, "memory/daily/x.md")
    _seed(tmp_path, "knowledge/coll/doc.md")
    _seed(tmp_path, ".magi/identity.md")
    _seed(tmp_path, "node_modules/pkg/index.js")
    _seed(tmp_path, ".git/config")
    _seed(tmp_path, "__pycache__/x.pyc")
    client = _client(tmp_path, monkeypatch)

    paths = {row["path"] for row in client.get("/v1/app/workspace").json()["files"]}
    assert "keep.md" in paths
    assert not any(
        p.startswith(("memory/", "knowledge/", ".magi/", "node_modules/", ".git/", "__pycache__/"))
        for p in paths
    )


def test_workspace_file_returns_content(tmp_path, monkeypatch) -> None:
    _seed(tmp_path, "doc.md", "hello world")
    client = _client(tmp_path, monkeypatch)
    res = client.get("/v1/app/workspace/file", params={"path": "doc.md"})
    assert res.status_code == 200
    assert res.json()["content"] == "hello world"


def test_workspace_file_blocks_traversal(tmp_path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)
    res = client.get("/v1/app/workspace/file", params={"path": "../../etc/passwd"})
    assert res.status_code in (403, 404)


def test_workspace_requires_gateway_token(tmp_path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)
    client.headers.pop("x-gateway-token", None)
    assert client.get("/v1/app/workspace").status_code == 401
    assert client.get("/v1/app/workspace/file", params={"path": "x"}).status_code == 401
