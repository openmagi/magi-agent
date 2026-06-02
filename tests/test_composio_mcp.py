from __future__ import annotations


class FakeMcp:
    url = "https://mcp.composio.dev/session/test"
    headers = {"Authorization": "Bearer session-token", "x-composio-session": "sess_123"}


class FakeSession:
    mcp = FakeMcp()


class FakeComposioClient:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def create(self, **kwargs: object) -> FakeSession:
        self.calls.append(dict(kwargs))
        return FakeSession()


class FakeToolset:
    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs
        self.name = "fake-composio-toolset"


class FakeConnectionParams:
    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs


class FakeAgent:
    def __init__(self, tools: object | None = None) -> None:
        self.tools = tools


class FakeRunner:
    def __init__(self, tools: object | None = None) -> None:
        self.agent = FakeAgent(tools=tools)


def _erroring_toolset(message: str) -> type:
    class ErroringToolset:
        def __init__(self, **_kwargs: object) -> None:
            raise RuntimeError(message)

    return ErroringToolset


def test_inactive_config_builds_no_toolsets() -> None:
    from openmagi_core_agent.composio.config import resolve_composio_config
    from openmagi_core_agent.composio.mcp import build_composio_toolset_bundle

    cfg = resolve_composio_config({})
    bundle = build_composio_toolset_bundle(
        cfg,
        composio_client_factory=lambda _api_key: FakeComposioClient(),
        toolset_cls=FakeToolset,
        connection_params_cls=FakeConnectionParams,
    )

    assert bundle.active is False
    assert bundle.toolsets == ()
    assert bundle.status == "inactive"
    assert bundle.reason == "disabled_by_config"


def test_active_config_creates_session_and_toolset_without_real_network() -> None:
    from openmagi_core_agent.composio.config import resolve_composio_config
    from openmagi_core_agent.composio.mcp import build_composio_toolset_bundle

    client = FakeComposioClient()
    cfg = resolve_composio_config(
        {
            "COMPOSIO_API_KEY": "cp_test_secret",
            "MAGI_COMPOSIO_ENABLED": "on",
            "MAGI_COMPOSIO_ENTITY_ID": "agent-1",
            "MAGI_COMPOSIO_TOOLKITS": "gmail,googledrive",
        }
    )
    bundle = build_composio_toolset_bundle(
        cfg,
        composio_client_factory=lambda _api_key: client,
        toolset_cls=FakeToolset,
        connection_params_cls=FakeConnectionParams,
    )

    assert bundle.active is True
    assert bundle.status == "ready"
    assert len(bundle.toolsets) == 1
    assert client.calls == [{"user_id": "agent-1", "toolkits": ["gmail", "googledrive"]}]
    toolset = bundle.toolsets[0]
    params = toolset.kwargs["connection_params"]
    assert params.kwargs["url"] == "https://mcp.composio.dev/session/test"
    assert params.kwargs["headers"]["Authorization"] == "Bearer session-token"
    assert toolset.kwargs["tool_name_prefix"] == "composio"


def test_default_active_config_creates_unrestricted_session() -> None:
    from openmagi_core_agent.composio.config import resolve_composio_config
    from openmagi_core_agent.composio.mcp import build_composio_toolset_bundle

    client = FakeComposioClient()
    cfg = resolve_composio_config(
        {"COMPOSIO_API_KEY": "cp_test_secret", "MAGI_COMPOSIO_ENABLED": "on"}
    )
    bundle = build_composio_toolset_bundle(
        cfg,
        composio_client_factory=lambda _api_key: client,
        toolset_cls=FakeToolset,
        connection_params_cls=FakeConnectionParams,
    )

    assert bundle.active is True
    assert bundle.status == "ready"
    assert client.calls == [{"user_id": "default"}]


def test_ready_bundle_json_dump_excludes_runtime_toolsets_and_session_secrets() -> None:
    from openmagi_core_agent.composio.config import resolve_composio_config
    from openmagi_core_agent.composio.mcp import build_composio_toolset_bundle

    cfg = resolve_composio_config(
        {
            "COMPOSIO_API_KEY": "cp_test_secret",
            "MAGI_COMPOSIO_ENABLED": "on",
            "MAGI_COMPOSIO_ENTITY_ID": "agent-1",
        }
    )
    bundle = build_composio_toolset_bundle(
        cfg,
        composio_client_factory=lambda _api_key: FakeComposioClient(),
        toolset_cls=FakeToolset,
        connection_params_cls=FakeConnectionParams,
    )

    dump = bundle.model_dump(by_alias=True, mode="json")
    rendered = str(dump)

    assert bundle.toolsets[0].name == "fake-composio-toolset"
    assert dump["active"] is True
    assert dump["status"] == "ready"
    assert "toolsets" not in dump
    assert "cp_test_secret" not in rendered
    assert "session-token" not in rendered
    assert "https://mcp.composio.dev/session/test" not in rendered
    assert "x-composio-session" not in rendered
    assert "sess_123" not in rendered


def test_endpoint_override_skips_composio_session_creation() -> None:
    from openmagi_core_agent.composio.config import resolve_composio_config
    from openmagi_core_agent.composio.mcp import build_composio_toolset_bundle

    client = FakeComposioClient()
    cfg = resolve_composio_config(
        {
            "COMPOSIO_API_KEY": "cp_test_secret",
            "MAGI_COMPOSIO_ENABLED": "on",
            "MAGI_COMPOSIO_MCP_URL": "https://mcp.composio.dev/mcp",
        }
    )
    bundle = build_composio_toolset_bundle(
        cfg,
        composio_client_factory=lambda _api_key: client,
        toolset_cls=FakeToolset,
        connection_params_cls=FakeConnectionParams,
    )

    assert bundle.status == "ready"
    assert client.calls == []
    params = bundle.toolsets[0].kwargs["connection_params"]
    assert params.kwargs["url"] == "https://mcp.composio.dev/mcp"
    assert params.kwargs["headers"]["Authorization"] == "Bearer cp_test_secret"


def test_ready_override_bundle_json_dump_excludes_endpoint_and_auth() -> None:
    from openmagi_core_agent.composio.config import resolve_composio_config
    from openmagi_core_agent.composio.mcp import build_composio_toolset_bundle

    cfg = resolve_composio_config(
        {
            "COMPOSIO_API_KEY": "cp_test_secret",
            "MAGI_COMPOSIO_ENABLED": "on",
            "MAGI_COMPOSIO_MCP_URL": "https://mcp.composio.dev/mcp?workspace=tok_secret",
        }
    )
    bundle = build_composio_toolset_bundle(
        cfg,
        composio_client_factory=lambda _api_key: FakeComposioClient(),
        toolset_cls=FakeToolset,
        connection_params_cls=FakeConnectionParams,
    )

    dump = bundle.model_dump(by_alias=True, mode="json")
    rendered = str(dump)

    assert bundle.toolsets
    assert "toolsets" not in dump
    assert "cp_test_secret" not in rendered
    assert "tok_secret" not in rendered
    assert "https://mcp.composio.dev/mcp" not in rendered
    assert "Authorization" not in rendered


def test_builder_error_is_sanitized() -> None:
    from openmagi_core_agent.composio.config import resolve_composio_config
    from openmagi_core_agent.composio.mcp import build_composio_toolset_bundle

    def boom(_api_key: str) -> FakeComposioClient:
        raise RuntimeError("Authorization: Bearer unsafe-token cp_test_secret")

    cfg = resolve_composio_config(
        {"COMPOSIO_API_KEY": "cp_test_secret", "MAGI_COMPOSIO_ENABLED": "on"}
    )
    bundle = build_composio_toolset_bundle(
        cfg,
        composio_client_factory=boom,
        toolset_cls=FakeToolset,
        connection_params_cls=FakeConnectionParams,
    )

    rendered = str(bundle.model_dump(by_alias=True))
    assert bundle.status == "error"
    assert bundle.active is False
    assert "unsafe-token" not in rendered
    assert "cp_test_secret" not in rendered


def test_toolset_error_redacts_mcp_session_url_and_query_credentials() -> None:
    from openmagi_core_agent.composio.config import resolve_composio_config
    from openmagi_core_agent.composio.mcp import build_composio_toolset_bundle

    cfg = resolve_composio_config(
        {"COMPOSIO_API_KEY": "cp_secret", "MAGI_COMPOSIO_ENABLED": "on"}
    )
    bundle = build_composio_toolset_bundle(
        cfg,
        composio_client_factory=lambda _api_key: FakeComposioClient(),
        toolset_cls=_erroring_toolset(
            "failed https://mcp.composio.dev/session/secret"
            "?api_key=cp_secret&token=tok_secret"
        ),
        connection_params_cls=FakeConnectionParams,
    )

    preview = bundle.last_error_preview or ""
    assert bundle.status == "error"
    assert "https://mcp.composio.dev/session/secret" not in preview
    assert "session/secret" not in preview
    assert "cp_secret" not in preview
    assert "tok_secret" not in preview


def test_toolset_error_redacts_override_url_query_values() -> None:
    from openmagi_core_agent.composio.config import resolve_composio_config
    from openmagi_core_agent.composio.mcp import build_composio_toolset_bundle

    class ErroringToolset:
        def __init__(self, **kwargs: object) -> None:
            params = kwargs["connection_params"]
            raise RuntimeError(f"failed override {params.kwargs['url']}")

    cfg = resolve_composio_config(
        {
            "COMPOSIO_API_KEY": "cp_runtime_secret",
            "MAGI_COMPOSIO_ENABLED": "on",
            "MAGI_COMPOSIO_MCP_URL": (
                "https://mcp.composio.dev/mcp?workspace=tok_secret&cursor=cp_secret"
            ),
        }
    )
    bundle = build_composio_toolset_bundle(
        cfg,
        composio_client_factory=lambda _api_key: FakeComposioClient(),
        toolset_cls=ErroringToolset,
        connection_params_cls=FakeConnectionParams,
    )

    preview = bundle.last_error_preview or ""
    assert bundle.status == "error"
    assert "cp_runtime_secret" not in preview
    assert "cp_secret" not in preview
    assert "tok_secret" not in preview


def test_toolset_error_redacts_composio_session_header_values() -> None:
    from openmagi_core_agent.composio.config import resolve_composio_config
    from openmagi_core_agent.composio.mcp import build_composio_toolset_bundle

    class ErroringToolset:
        def __init__(self, **kwargs: object) -> None:
            params = kwargs["connection_params"]
            raise RuntimeError(
                f"failed headers {params.kwargs['headers']} "
                "x-composio-session: sess_123"
            )

    cfg = resolve_composio_config(
        {"COMPOSIO_API_KEY": "cp_test_secret", "MAGI_COMPOSIO_ENABLED": "on"}
    )
    bundle = build_composio_toolset_bundle(
        cfg,
        composio_client_factory=lambda _api_key: FakeComposioClient(),
        toolset_cls=ErroringToolset,
        connection_params_cls=FakeConnectionParams,
    )

    rendered = str(bundle.model_dump(by_alias=True, mode="json"))
    assert bundle.status == "error"
    assert "sess_123" not in (bundle.last_error_preview or "")
    assert "sess_123" not in rendered


def test_missing_optional_composio_package_is_nonfatal() -> None:
    from openmagi_core_agent.composio.config import resolve_composio_config
    from openmagi_core_agent.composio.mcp import build_composio_toolset_bundle

    def missing_package(_api_key: str) -> FakeComposioClient:
        raise ImportError("No module named 'composio'")

    cfg = resolve_composio_config(
        {"COMPOSIO_API_KEY": "cp_test_secret", "MAGI_COMPOSIO_ENABLED": "on"}
    )
    bundle = build_composio_toolset_bundle(
        cfg,
        composio_client_factory=missing_package,
        toolset_cls=FakeToolset,
        connection_params_cls=FakeConnectionParams,
    )

    assert bundle.active is False
    assert bundle.status == "missing_package"
    assert bundle.reason == "missing_python_package"
    assert bundle.toolsets == ()


def test_attach_composio_toolsets_to_runner_is_idempotent() -> None:
    from openmagi_core_agent.composio.config import resolve_composio_config
    from openmagi_core_agent.composio.mcp import (
        attach_composio_toolsets_to_runner,
        build_composio_toolset_bundle,
    )

    cfg = resolve_composio_config(
        {"COMPOSIO_API_KEY": "cp_test_secret", "MAGI_COMPOSIO_ENABLED": "on"}
    )
    bundle = build_composio_toolset_bundle(
        cfg,
        composio_client_factory=lambda _api_key: FakeComposioClient(),
        toolset_cls=FakeToolset,
        connection_params_cls=FakeConnectionParams,
    )
    runner = FakeRunner()

    assert attach_composio_toolsets_to_runner(runner, bundle) is True
    assert attach_composio_toolsets_to_runner(runner, bundle) is True

    assert runner.agent.tools == [bundle.toolsets[0]]


class FakeAgentWithList:
    def __init__(self) -> None:
        self.tools: list[object] = []


class FakeRunnerWithList:
    def __init__(self) -> None:
        self.agent = FakeAgentWithList()


def test_attach_composio_toolsets_to_runner_extends_agent_tools() -> None:
    from openmagi_core_agent.composio.mcp import (
        ComposioToolsetBundle,
        attach_composio_toolsets_to_runner,
    )

    runner = FakeRunnerWithList()
    attached = attach_composio_toolsets_to_runner(
        runner,
        ComposioToolsetBundle(active=True, status="ready", toolsets=("toolset",)),
    )

    assert attached is True
    assert runner.agent.tools == ["toolset"]
