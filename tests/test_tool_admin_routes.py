from fastapi.testclient import TestClient

from magi_agent.app import create_app
from magi_agent.config.models import BuildInfo, RuntimeConfig
from magi_agent.runtime.openmagi_runtime import OpenMagiRuntime
from magi_agent.tools import ToolRegistry, ToolSource
from magi_agent.tools.manifest import ToolManifest


EXPECTED_CORE_TOOL_NAMES = {
    "ToolSearch",
    "FileRead",
    "FileWrite",
    "FileEdit",
    "Glob",
    "Grep",
    "Bash",
    "TestRun",
    "GitDiff",
    "AskUserQuestion",
    "EnterPlanMode",
    "ExitPlanMode",
    "ArtifactCreate",
    "ArtifactRead",
    "ArtifactList",
    "Clock",
    "Calculation",
    "HealthStatus",
    "TaskList",
    "TaskGet",
    "TaskOutput",
    "CronList",
}

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

    for path in ("/v1/admin/tools", "/v1/admin/tools/stats", "/v1/admin/tools/FileRead"):
        missing = client.get(path)
        assert missing.status_code == 401
        assert missing.json() == {"error": "unauthorized"}

        wrong = client.get(path, headers=admin_headers("wrong-token"))
        assert wrong.status_code == 401
        assert wrong.json() == {"error": "unauthorized"}


def test_list_tools_returns_disabled_core_catalog_metadata() -> None:
    client = make_client()

    response = client.get("/v1/admin/tools", headers=admin_headers())

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"tools"}
    tools = body["tools"]
    assert {tool["name"] for tool in tools} == EXPECTED_CORE_TOOL_NAMES

    file_read = tool_by_name(tools, "FileRead")
    assert EXPECTED_PUBLIC_TOOL_FIELDS.issubset(file_read)
    assert file_read["enabled"] is False
    assert file_read["kind"] == "core"
    assert file_read["source"] == "builtin"
    assert file_read["permission"] == "read"
    assert file_read["isConcurrencySafe"] is True
    assert file_read["dangerous"] is False
    assert file_read["mutatesWorkspace"] is False
    assert file_read["availableInModes"] == ["plan", "act"]
    assert file_read["inputSchema"] == {"type": "object", "additionalProperties": True}
    assert file_read["outputSchema"] is None
    assert file_read["pluginId"] is None
    assert file_read["optOut"] is True
    assert "handler" not in file_read

    bash = tool_by_name(tools, "Bash")
    assert bash["enabled"] is False
    assert bash["dangerous"] is True
    assert bash["mutatesWorkspace"] is True
    assert bash["permission"] == "execute"

    for readonly_name in (
        "HealthStatus",
        "TaskList",
        "TaskGet",
        "TaskOutput",
        "CronList",
    ):
        manifest = tool_by_name(tools, readonly_name)
        assert manifest["enabled"] is False
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
    assert tool["enabled"] is False
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
    assert set(stats) == EXPECTED_CORE_TOOL_NAMES
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


def test_admin_tool_routes_do_not_scaffold_mutating_routes() -> None:
    client = make_client()

    enable = client.put("/v1/admin/tools/FileRead/enable", headers=admin_headers())
    disable = client.put("/v1/admin/tools/FileRead/disable", headers=admin_headers())
    delete = client.delete("/v1/admin/tools/FileRead", headers=admin_headers())

    assert enable.status_code in {404, 405}
    assert disable.status_code in {404, 405}
    assert delete.status_code in {404, 405}
