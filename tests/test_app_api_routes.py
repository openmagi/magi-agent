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
    for name in _WORKSPACE_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    client = TestClient(create_app(_runtime()))  # no token header
    assert client.get("/v1/app/providers").status_code == 401


def test_providers_put_requires_gateway_token(tmp_path, monkeypatch) -> None:
    """PUT /v1/app/providers without auth → 401."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MAGI_CONFIG", str(tmp_path / "config.toml"))
    for name in _WORKSPACE_ENV_VARS:
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
