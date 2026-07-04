from fastapi.testclient import TestClient

from magi_agent.app import create_app
from magi_agent.config.models import BuildInfo, RuntimeConfig
from magi_agent.runtime.openmagi_runtime import OpenMagiRuntime
from magi_agent.tools import ToolRegistry, ToolSource
from magi_agent.tools.manifest import ToolManifest


EXPECTED_CORE_TOOL_NAMES = {
    "TodoWrite",
    "FileRead",
    "FileWrite",
    "FileEdit",
    "PatchApply",
    "Glob",
    "Grep",
    "Bash",
    "TestRun",
    "GitDiff",
    "AskUserQuestion",
    "EnterPlanMode",
    "ExitPlanMode",
    "Clock",
    "Calculation",
    "TaskList",
    "TaskGet",
    "TaskOutput",
    "CronList",
    "InspectSelfEvidence",
}

EXPECTED_NATIVE_TOOL_NAMES = {
    "AgentMemorySearch",
    "AgentMemoryRemember",
    "apify_search_actors",
    "apify_run_actor",
    "ArtifactDelete",
    "ArtifactUpdate",
    "BatchRead",
    "Browser",
    "CodeDiagnostics",
    "CodeIntelligence",
    "CodeSymbolSearch",
    "CodeWorkspace",
    "CodingBenchmark",
    "CommitCheckpoint",
    "DateRange",
    "SocialBrowser",
    "DocumentWrite",
    "SpreadsheetWrite",
    "ExternalSourceCache",
    "ExternalSourceRead",
    "ExternalToolLoader",
    "FileDeliver",
    "FileSend",
    "KnowledgeSearch",
    "knowledge-search",
    "KnowledgeWrite",
    "knowledge-write",
    "OkfLookup",
    "okf-lookup",
    "MemoryRedact",
    "MemoryWrite",
    "MissionLedger",
    "NotifyUser",
    "PackageDependencyResolve",
    "PersistentPython",
    "ProjectVerificationPlanner",
    "RepoMap",
    "RepoTaskState",
    "RepositoryMap",
    "SafeCommand",
    "SkillLoader",
    "SkillRuntimeHooks",
    "CronCreate",
    "CronUpdate",
    "CronDelete",
    "SpawnAgent",
    "SpawnWorktreeApply",
    "SwitchToActMode",
    "TaskBoard",
    "TaskWait",
    "TaskStop",
    "RunInBackground",
    "WebSearch",
    "web-search",
    "web_search",
    "WebFetch",
}

EXPECTED_DEFAULT_TOOL_NAMES = EXPECTED_CORE_TOOL_NAMES | EXPECTED_NATIVE_TOOL_NAMES

EXPECTED_PUBLIC_TOOL_FIELDS = {
    "name",
    "description",
    "permission",
    "kind",
    "enabled",
    "source",
    "isConcurrencySafe",
    "dangerous",
    "tags",
    "inputSchema",
    "outputSchema",
    "timeoutMs",
    "mutatesWorkspace",
    "availableInModes",
    "shouldDefer",
    "pluginId",
    "optOut",
}


def make_config() -> RuntimeConfig:
    return RuntimeConfig(
        bot_id="bot-test",
        user_id="user-test",
        gateway_token="gateway-token",
        api_proxy_url="http://api-proxy.local",
        chat_proxy_url="http://chat-proxy.local",
        redis_url="redis://redis.local:6379/0",
        model="gpt-5.2",
        build=BuildInfo(version="0.1.0-adk-scaffold", build_sha="sha-test"),
    )


def make_client(runtime: OpenMagiRuntime | None = None) -> TestClient:
    return TestClient(create_app(runtime or OpenMagiRuntime(config=make_config())))


def admin_headers(token: str = "gateway-token") -> dict[str, str]:
    return {"x-gateway-token": token}


def tool_by_name(tools: list[dict[str, object]], name: str) -> dict[str, object]:
    return next(tool for tool in tools if tool["name"] == name)


def make_custom_manifest(name: str) -> ToolManifest:
    return ToolManifest(
        name=name,
        description=f"{name} test tool",
        kind="custom",
        source=ToolSource(kind="custom-plugin", package="tests.tools"),
        permission="read",
        inputSchema={"type": "object"},
        timeoutMs=1_000,
    )


def test_admin_tool_routes_require_gateway_token() -> None:
    client = make_client()

    for path in (
        "/v1/admin/tools",
        "/v1/admin/tools/stats",
        "/v1/admin/tools/FileRead",
        "/api/tools",
        "/api/tools/stats",
        "/api/tools/FileRead",
    ):
        missing = client.get(path)
        assert missing.status_code == 401
        assert missing.json() == {"error": "unauthorized"}

        wrong = client.get(path, headers=admin_headers("wrong-token"))
        assert wrong.status_code == 401
        assert wrong.json() == {"error": "unauthorized"}


def test_list_tools_returns_enabled_first_party_catalog_metadata() -> None:
    client = make_client()

    response = client.get("/v1/admin/tools", headers=admin_headers())

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"tools"}
    tools = body["tools"]
    assert {tool["name"] for tool in tools} == EXPECTED_DEFAULT_TOOL_NAMES

    file_read = tool_by_name(tools, "FileRead")
    assert EXPECTED_PUBLIC_TOOL_FIELDS.issubset(file_read)
    assert file_read["enabled"] is True
    assert file_read["kind"] == "core"
    assert file_read["source"] == "builtin"
    assert file_read["permission"] == "read"
    assert file_read["isConcurrencySafe"] is True
    assert file_read["dangerous"] is False
    assert file_read["mutatesWorkspace"] is False
    assert file_read["availableInModes"] == ["plan", "act"]
    file_read_schema = file_read["inputSchema"]
    assert isinstance(file_read_schema, dict)
    assert file_read_schema["type"] == "object"
    assert file_read_schema["required"] == ["path"]
    assert file_read_schema["properties"]["path"]["type"] == "string"
    assert file_read["outputSchema"] is None
    assert file_read["pluginId"] is None
    assert file_read["optOut"] is True
    assert "handler" not in file_read

    bash = tool_by_name(tools, "Bash")
    assert bash["enabled"] is True
    assert bash["dangerous"] is True
    assert bash["mutatesWorkspace"] is True
    assert bash["permission"] == "execute"

    patch_apply = tool_by_name(tools, "PatchApply")
    assert patch_apply["enabled"] is True
    assert patch_apply["dangerous"] is False
    assert patch_apply["mutatesWorkspace"] is True
    assert patch_apply["permission"] == "write"
    assert patch_apply["availableInModes"] == ["act"]

    for readonly_name in (
        "TaskList",
        "TaskGet",
        "TaskOutput",
        "CronList",
    ):
        manifest = tool_by_name(tools, readonly_name)
        assert manifest["enabled"] is True
        assert manifest["dangerous"] is False
        assert manifest["mutatesWorkspace"] is False
        assert manifest["permission"] == "meta"
        assert "gate1a" not in manifest["tags"]


def test_runtime_uses_explicit_registry_without_adding_core_catalog() -> None:
    registry = ToolRegistry()
    registry.register(make_custom_manifest("CustomRead"))
    runtime = OpenMagiRuntime(config=make_config(), tool_registry=registry)
    client = make_client(runtime)

    response = client.get("/v1/admin/tools", headers=admin_headers())

    assert response.status_code == 200
    tools = response.json()["tools"]
    assert [tool["name"] for tool in tools] == ["CustomRead"]
    assert tools[0]["source"] == "custom-plugin"


def test_tool_detail_returns_known_tool_metadata() -> None:
    client = make_client()

    response = client.get("/v1/admin/tools/FileRead", headers=admin_headers())

    assert response.status_code == 200
    tool = response.json()["tool"]
    assert EXPECTED_PUBLIC_TOOL_FIELDS.issubset(tool)
    assert tool["name"] == "FileRead"
    assert tool["enabled"] is True
    assert "handler" not in tool


def test_tool_detail_returns_not_found_for_unknown_tool() -> None:
    client = make_client()

    response = client.get("/v1/admin/tools/MissingTool", headers=admin_headers())

    assert response.status_code == 404
    assert response.json() == {
        "error": "not_found",
        "message": 'tool "MissingTool" not found',
    }


def test_tool_stats_returns_zero_stub_stats_for_registered_tools() -> None:
    client = make_client()

    response = client.get("/v1/admin/tools/stats", headers=admin_headers())

    assert response.status_code == 200
    stats = response.json()["stats"]
    assert set(stats) == EXPECTED_DEFAULT_TOOL_NAMES
    assert stats["FileRead"] == {
        "calls": 0,
        "errors": 0,
        "avgDurationMs": 0,
        "lastCallAt": 0,
    }
    assert stats["Bash"] == {
        "calls": 0,
        "errors": 0,
        "avgDurationMs": 0,
        "lastCallAt": 0,
    }


def test_local_dashboard_api_tools_alias_matches_admin_catalog() -> None:
    client = make_client()

    admin_response = client.get("/v1/admin/tools", headers=admin_headers())
    dashboard_response = client.get("/api/tools", headers=admin_headers())

    assert admin_response.status_code == 200
    assert dashboard_response.status_code == 200
    assert dashboard_response.json() == admin_response.json()


def test_local_dashboard_api_tools_alias_supports_detail_and_stats() -> None:
    client = make_client()

    detail = client.get("/api/tools/FileRead", headers=admin_headers())
    stats = client.get("/api/tools/stats", headers=admin_headers())

    assert detail.status_code == 200
    assert detail.json()["tool"]["name"] == "FileRead"
    assert stats.status_code == 200
    assert set(stats.json()["stats"]) == EXPECTED_DEFAULT_TOOL_NAMES


def test_local_dashboard_api_tools_can_toggle_registry_state() -> None:
    client = make_client()

    disabled = client.post("/api/tools/FileRead/disable", headers=admin_headers())
    detail_disabled = client.get("/api/tools/FileRead", headers=admin_headers())
    enabled = client.post("/api/tools/FileRead/enable", headers=admin_headers())
    detail_enabled = client.get("/api/tools/FileRead", headers=admin_headers())

    assert disabled.status_code == 200
    assert disabled.json() == {"tool": "FileRead", "enabled": False}
    assert detail_disabled.status_code == 200
    assert detail_disabled.json()["tool"]["enabled"] is False
    assert enabled.status_code == 200
    assert enabled.json() == {"tool": "FileRead", "enabled": True}
    assert detail_enabled.status_code == 200
    assert detail_enabled.json()["tool"]["enabled"] is True


def test_local_dashboard_api_tools_toggle_requires_gateway_token() -> None:
    client = make_client()

    missing = client.post("/api/tools/FileRead/disable")
    wrong = client.post("/api/tools/FileRead/disable", headers=admin_headers("wrong-token"))

    assert missing.status_code == 401
    assert missing.json() == {"error": "unauthorized"}
    assert wrong.status_code == 401
    assert wrong.json() == {"error": "unauthorized"}


def test_admin_tool_routes_do_not_scaffold_mutating_routes() -> None:
    client = make_client()

    enable = client.put("/v1/admin/tools/FileRead/enable", headers=admin_headers())
    disable = client.put("/v1/admin/tools/FileRead/disable", headers=admin_headers())
    delete = client.delete("/v1/admin/tools/FileRead", headers=admin_headers())

    assert enable.status_code in {404, 405}
    assert disable.status_code in {404, 405}
    assert delete.status_code in {404, 405}
