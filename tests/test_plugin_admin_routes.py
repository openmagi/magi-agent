from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from typing import Any

from fastapi.testclient import TestClient

from magi_agent.app import create_app
from magi_agent.config.models import BuildInfo, RuntimeConfig
from magi_agent.plugins.manager import PluginOptOutRecord, resolve_plugin_state
from magi_agent.plugins.native_catalog import native_plugin_manifests
from magi_agent.runtime.openmagi_runtime import OpenMagiRuntime


EXPECTED_NATIVE_PLUGIN_IDS = (
    "openmagi.agentmemory",
    "openmagi.browser",
    "openmagi.documents",
    "openmagi.knowledge",
    "openmagi.missions",
    "openmagi.scheduled-work",
    "openmagi.security-posture",
    "openmagi.web",
    "openmagi.web-acquisition",
)

DEFAULT_DISABLED_PLUGIN_IDS = {
    "openmagi.agentmemory",
    "openmagi.browser",
    "openmagi.missions",
    "openmagi.scheduled-work",
    "openmagi.security-posture",
    "openmagi.web",
    "openmagi.web-acquisition",
}

EXPECTED_PUBLIC_PLUGIN_FIELDS = {
    "pluginId",
    "kind",
    "version",
    "installed",
    "enabled",
    "optedOut",
    "defaultInstalled",
    "defaultEnabled",
    "optOutAllowed",
    "securityCritical",
    "auditRequired",
    "statusReason",
    "tools",
    "hooks",
    "harnessRules",
    "secrets",
    "permissions",
    "services",
    "trafficAttached",
    "executionAttached",
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


def plugin_by_id(plugins: list[dict[str, Any]], plugin_id: str) -> dict[str, Any]:
    return next(plugin for plugin in plugins if plugin["pluginId"] == plugin_id)


def assert_no_executable_metadata(value: object) -> None:
    dumped = json.dumps(value, sort_keys=True)
    assert "entrypoint" not in dumped
    assert "configSchema" not in dumped
    assert "magi_agent.plugins.native" not in dumped
    assert "handler" not in dumped
    assert "super-secret" not in dumped


def opt_out(plugin_id: str) -> PluginOptOutRecord:
    return PluginOptOutRecord(
        pluginId=plugin_id,
        scope="bot",
        actor="user:test",
        reason="disabled for admin route test",
        ts="2026-05-15T00:00:00Z",
    )


def test_admin_plugin_routes_require_gateway_token() -> None:
    client = make_client()

    for path in (
        "/v1/admin/plugins",
        "/v1/admin/plugins/audit",
        "/v1/admin/plugins/openmagi.web",
    ):
        missing = client.get(path)
        assert missing.status_code == 401
        assert missing.json() == {"error": "unauthorized"}

        wrong = client.get(path, headers=admin_headers("wrong-token"))
        assert wrong.status_code == 401
        assert wrong.json() == {"error": "unauthorized"}


def test_default_runtime_exposes_exact_native_plugin_ids_enabled() -> None:
    client = make_client()

    response = client.get("/v1/admin/plugins", headers=admin_headers())

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"plugins", "trafficAttached", "executionAttached"}
    assert body["trafficAttached"] is False
    assert body["executionAttached"] is False
    plugins = body["plugins"]
    assert tuple(plugin["pluginId"] for plugin in plugins) == EXPECTED_NATIVE_PLUGIN_IDS
    agentmemory = plugin_by_id(plugins, "openmagi.agentmemory")
    assert agentmemory["installed"] is True
    assert agentmemory["enabled"] is False
    assert agentmemory["defaultEnabled"] is False
    assert agentmemory["statusReason"] == "default_disabled"
    scheduled_work = plugin_by_id(plugins, "openmagi.scheduled-work")
    assert scheduled_work["installed"] is True
    assert scheduled_work["enabled"] is False
    assert scheduled_work["defaultEnabled"] is False
    assert scheduled_work["statusReason"] == "default_disabled"
    assert scheduled_work["tools"] == [
        "CronCreate",
        "CronList",
        "CronUpdate",
        "CronDelete",
        "TaskWait",
        "TaskGet",
        "TaskList",
        "TaskOutput",
        "TaskStop",
    ]
    assert scheduled_work["harnessRules"] == ["scheduled_work_recipe_policy"]
    assert scheduled_work["permissions"] == []
    assert scheduled_work["services"] == []
    assert scheduled_work["secrets"] == []
    missions = plugin_by_id(plugins, "openmagi.missions")
    assert missions["enabled"] is False
    assert missions["defaultEnabled"] is False
    assert missions["statusReason"] == "default_disabled"
    assert missions["permissions"] == ["read", "meta"]
    assert missions["services"] == ["mission-ledger"]
    assert missions["trafficAttached"] is False
    assert missions["executionAttached"] is False
    web = plugin_by_id(plugins, "openmagi.web")
    assert web["enabled"] is False
    assert web["defaultEnabled"] is False
    assert web["statusReason"] == "default_disabled"
    browser = plugin_by_id(plugins, "openmagi.browser")
    assert browser["enabled"] is False
    assert browser["defaultEnabled"] is False
    assert browser["statusReason"] == "default_disabled"
    web_acquisition = plugin_by_id(plugins, "openmagi.web-acquisition")
    assert web_acquisition["enabled"] is False
    assert web_acquisition["defaultEnabled"] is False
    assert web_acquisition["statusReason"] == "default_disabled"
    assert web_acquisition["tools"] == []
    assert web_acquisition["harnessRules"] == [
        "web_acquisition_provider_boundary",
        "web_acquisition_source_ledger_boundary",
    ]
    security_posture = plugin_by_id(plugins, "openmagi.security-posture")
    assert security_posture["enabled"] is False
    assert security_posture["defaultEnabled"] is False
    assert security_posture["optOutAllowed"] is False
    assert security_posture["securityCritical"] is True
    assert security_posture["statusReason"] == "default_disabled"
    assert security_posture["tools"] == []
    assert security_posture["hooks"] == []
    assert security_posture["permissions"] == []
    assert security_posture["services"] == []
    assert security_posture["secrets"] == []
    assert security_posture["harnessRules"] == [
        "security_posture_matrix",
        "external_surface_fail_closed",
        "sandbox_preflight",
        "credential_pass_through_policy",
        "context_file_injection_guard",
        "supply_chain_advisory",
    ]
    assert all(
        plugin["enabled"] is True
        for plugin in plugins
        if plugin["pluginId"] not in DEFAULT_DISABLED_PLUGIN_IDS
    )
    assert all(plugin["installed"] is True for plugin in plugins)
    assert all(plugin["optedOut"] is False for plugin in plugins)
    assert all(
        plugin["statusReason"] == "enabled"
        for plugin in plugins
        if plugin["pluginId"] not in DEFAULT_DISABLED_PLUGIN_IDS
    )
    assert all(plugin["trafficAttached"] is False for plugin in plugins)
    assert all(plugin["executionAttached"] is False for plugin in plugins)
    assert EXPECTED_PUBLIC_PLUGIN_FIELDS.issubset(plugins[0])
    assert_no_executable_metadata(body)


def test_plugin_detail_returns_security_posture_metadata_only_default_disabled() -> None:
    client = make_client()

    response = client.get("/v1/admin/plugins/openmagi.security-posture", headers=admin_headers())

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"plugin"}
    plugin = body["plugin"]
    assert EXPECTED_PUBLIC_PLUGIN_FIELDS.issubset(plugin)
    assert plugin["pluginId"] == "openmagi.security-posture"
    assert plugin["kind"] == "native"
    assert plugin["installed"] is True
    assert plugin["enabled"] is False
    assert plugin["defaultEnabled"] is False
    assert plugin["optOutAllowed"] is False
    assert plugin["securityCritical"] is True
    assert plugin["statusReason"] == "default_disabled"
    assert plugin["tools"] == []
    assert plugin["hooks"] == []
    assert plugin["permissions"] == []
    assert plugin["services"] == []
    assert plugin["secrets"] == []
    assert plugin["trafficAttached"] is False
    assert plugin["executionAttached"] is False
    assert plugin["harnessRules"] == [
        "security_posture_matrix",
        "external_surface_fail_closed",
        "sandbox_preflight",
        "credential_pass_through_policy",
        "context_file_injection_guard",
        "supply_chain_advisory",
    ]
    assert_no_executable_metadata(body)


def test_plugin_detail_returns_web_metadata_aliases_and_redacted_secrets() -> None:
    client = make_client()

    response = client.get("/v1/admin/plugins/openmagi.web", headers=admin_headers())

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"plugin"}
    plugin = body["plugin"]
    assert EXPECTED_PUBLIC_PLUGIN_FIELDS.issubset(plugin)
    assert plugin["pluginId"] == "openmagi.web"
    assert plugin["kind"] == "native"
    assert plugin["enabled"] is False
    assert plugin["defaultEnabled"] is False
    assert plugin["statusReason"] == "default_disabled"
    assert plugin["tools"] == ["WebSearch", "web-search", "web_search", "WebFetch"]
    assert plugin["hooks"] == []
    assert plugin["harnessRules"] == ["web_source_citation"]
    assert plugin["permissions"] == ["read", "net"]
    assert plugin["services"] == ["api-proxy", "firecrawl"]
    assert plugin["secrets"] == [
        {"name": "GATEWAY_TOKEN", "source": "platform"},
        {"name": "FIRECRAWL_API_KEY", "source": "platform"},
    ]
    assert plugin["trafficAttached"] is False
    assert plugin["executionAttached"] is False
    assert_no_executable_metadata(body)


def test_plugin_detail_returns_scheduled_work_disabled_metadata_without_runtime_attachment() -> None:
    client = make_client()

    response = client.get("/v1/admin/plugins/openmagi.scheduled-work", headers=admin_headers())

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"plugin"}
    plugin = body["plugin"]
    assert EXPECTED_PUBLIC_PLUGIN_FIELDS.issubset(plugin)
    assert plugin["pluginId"] == "openmagi.scheduled-work"
    assert plugin["kind"] == "native"
    assert plugin["installed"] is True
    assert plugin["enabled"] is False
    assert plugin["defaultEnabled"] is False
    assert plugin["statusReason"] == "default_disabled"
    assert plugin["tools"] == [
        "CronCreate",
        "CronList",
        "CronUpdate",
        "CronDelete",
        "TaskWait",
        "TaskGet",
        "TaskList",
        "TaskOutput",
        "TaskStop",
    ]
    assert plugin["hooks"] == []
    assert plugin["harnessRules"] == ["scheduled_work_recipe_policy"]
    assert plugin["permissions"] == []
    assert plugin["services"] == []
    assert plugin["secrets"] == []
    assert plugin["trafficAttached"] is False
    assert plugin["executionAttached"] is False
    assert_no_executable_metadata(body)
    dumped = json.dumps(body, sort_keys=True)
    assert "CronScheduler" not in dumped
    assert "ScriptCronRunner" not in dumped
    assert "LongRunningFunctionTool" not in dumped


def test_audit_route_returns_snapshot_summary_with_forced_attachment_flags() -> None:
    client = make_client()

    response = client.get("/v1/admin/plugins/audit", headers=admin_headers())

    assert response.status_code == 200
    snapshot = response.json()
    assert snapshot["trafficAttached"] is False
    assert snapshot["executionAttached"] is False
    assert snapshot["summary"]["pluginCount"] == len(EXPECTED_NATIVE_PLUGIN_IDS)
    assert snapshot["summary"]["enabledPluginCount"] == (
        len(EXPECTED_NATIVE_PLUGIN_IDS) - len(DEFAULT_DISABLED_PLUGIN_IDS)
    )
    assert snapshot["summary"]["optedOutPluginCount"] == 0
    assert snapshot["summary"]["declaredSecretNames"] == [
        "FIRECRAWL_API_KEY",
        "GATEWAY_TOKEN",
    ]
    assert snapshot["summary"]["trafficAttached"] is False
    assert snapshot["summary"]["executionAttached"] is False
    web = plugin_by_id(snapshot["entries"], "openmagi.web")
    assert web["declaredSecrets"] == [
        {"name": "GATEWAY_TOKEN", "source": "platform"},
        {"name": "FIRECRAWL_API_KEY", "source": "platform"},
    ]
    assert web["enabled"] is False
    assert web["statusReason"] == "default_disabled"
    assert web["trafficAttached"] is False
    assert web["executionAttached"] is False
    web_acquisition = plugin_by_id(snapshot["entries"], "openmagi.web-acquisition")
    assert web_acquisition["enabled"] is False
    assert web_acquisition["statusReason"] == "default_disabled"
    assert web_acquisition["tools"] == []
    assert web_acquisition["declaredSecrets"] == []
    assert web_acquisition["trafficAttached"] is False
    assert web_acquisition["executionAttached"] is False
    scheduled_work = plugin_by_id(snapshot["entries"], "openmagi.scheduled-work")
    assert scheduled_work["enabled"] is False
    assert scheduled_work["statusReason"] == "default_disabled"
    assert scheduled_work["tools"] == [
        "CronCreate",
        "CronList",
        "CronUpdate",
        "CronDelete",
        "TaskWait",
        "TaskGet",
        "TaskList",
        "TaskOutput",
        "TaskStop",
    ]
    assert scheduled_work["declaredSecrets"] == []
    assert scheduled_work["trafficAttached"] is False
    assert scheduled_work["executionAttached"] is False
    assert_no_executable_metadata(snapshot)


def test_injected_opt_out_state_disables_plugin_routes_and_preserves_audit_metadata() -> None:
    plugin_state = resolve_plugin_state(native_plugin_manifests(), (opt_out("openmagi.web"),))
    runtime = OpenMagiRuntime(config=make_config(), plugin_state=plugin_state)
    client = make_client(runtime)

    assert "WebSearch" not in runtime.plugin_state.active_tools
    assert "web-search" not in runtime.plugin_state.active_tools
    assert "web_search" not in runtime.plugin_state.active_tools
    assert "WebFetch" not in runtime.plugin_state.active_tools

    list_response = client.get("/v1/admin/plugins", headers=admin_headers())
    detail_response = client.get("/v1/admin/plugins/openmagi.web", headers=admin_headers())
    audit_response = client.get("/v1/admin/plugins/audit", headers=admin_headers())

    assert list_response.status_code == 200
    listed_web = plugin_by_id(list_response.json()["plugins"], "openmagi.web")
    assert listed_web["enabled"] is False
    assert listed_web["optedOut"] is True
    assert listed_web["statusReason"] == "opted_out"
    assert listed_web["tools"] == ["WebSearch", "web-search", "web_search", "WebFetch"]

    assert detail_response.status_code == 200
    detail_web = detail_response.json()["plugin"]
    assert detail_web["enabled"] is False
    assert detail_web["optedOut"] is True
    assert detail_web["permissions"] == ["read", "net"]
    assert detail_web["secrets"] == [
        {"name": "GATEWAY_TOKEN", "source": "platform"},
        {"name": "FIRECRAWL_API_KEY", "source": "platform"},
    ]

    assert audit_response.status_code == 200
    snapshot = audit_response.json()
    audit_web = plugin_by_id(snapshot["entries"], "openmagi.web")
    assert audit_web["enabled"] is False
    assert audit_web["optedOut"] is True
    assert audit_web["permissions"] == ["read", "net"]
    assert audit_web["tools"] == ["WebSearch", "web-search", "web_search", "WebFetch"]
    assert audit_web["declaredSecrets"] == [
        {"name": "GATEWAY_TOKEN", "source": "platform"},
        {"name": "FIRECRAWL_API_KEY", "source": "platform"},
    ]
    assert snapshot["summary"]["enabledPluginCount"] == (
        len(EXPECTED_NATIVE_PLUGIN_IDS) - len(DEFAULT_DISABLED_PLUGIN_IDS)
    )
    assert snapshot["summary"]["optedOutPluginCount"] == 1


def test_unknown_plugin_returns_not_found() -> None:
    client = make_client()

    response = client.get("/v1/admin/plugins/openmagi.missing", headers=admin_headers())

    assert response.status_code == 404
    assert response.json() == {
        "error": "not_found",
        "message": 'plugin "openmagi.missing" not found',
    }


def test_admin_plugin_routes_do_not_scaffold_mutating_routes() -> None:
    client = make_client()

    install = client.post("/v1/admin/plugins/openmagi.web/install", headers=admin_headers())
    enable = client.put("/v1/admin/plugins/openmagi.web/enable", headers=admin_headers())
    disable = client.put("/v1/admin/plugins/openmagi.web/disable", headers=admin_headers())
    opt_out_response = client.put(
        "/v1/admin/plugins/openmagi.web/opt-out",
        headers=admin_headers(),
    )
    delete = client.delete("/v1/admin/plugins/openmagi.web", headers=admin_headers())

    assert install.status_code in {404, 405}
    assert enable.status_code in {404, 405}
    assert disable.status_code in {404, 405}
    assert opt_out_response.status_code in {404, 405}
    assert delete.status_code in {404, 405}


def test_plugin_admin_route_import_boundary_stays_metadata_only() -> None:
    script = """
import importlib
import importlib.abc

forbidden_prefixes = (
    "google.adk",
    "magi_agent.adk_bridge",
    "magi_agent.runtime",
    "magi_agent.tools.dispatcher",
    "magi_agent.tools.registry",
    "magi_agent.hooks.bus",
    "magi_agent.plugins.native",
    "magi_agent.app",
    "magi_agent.main",
)


class ForbiddenImportFinder(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if any(
            fullname == prefix or fullname.startswith(f"{prefix}.")
            for prefix in forbidden_prefixes
        ):
            raise AssertionError(f"forbidden import attempted: {fullname}")
        return None


import sys

sys.meta_path.insert(0, ForbiddenImportFinder())
importlib.import_module("magi_agent.transport.plugins")
"""
    completed = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(script)],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr


def test_plugin_admin_import_does_not_delete_preloaded_forbidden_modules() -> None:
    script = """
import importlib
import sys

preloaded = importlib.import_module("magi_agent.runtime.openmagi_runtime")
importlib.import_module("magi_agent.transport.plugins")
current = sys.modules.get("magi_agent.runtime.openmagi_runtime")
if current is not preloaded:
    raise AssertionError("plugin admin import deleted a preloaded runtime module")
"""
    completed = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(script)],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr


def test_transport_package_import_is_lazy_until_health_export_access() -> None:
    script = """
import importlib
import sys

transport = importlib.import_module("magi_agent.transport")
eagerly_loaded = [
    module_name
    for module_name in (
        "magi_agent.transport.health",
        "magi_agent.runtime",
        "magi_agent.runtime.openmagi_runtime",
    )
    if module_name in sys.modules
]
if eagerly_loaded:
    raise AssertionError(f"transport package import eagerly loaded: {eagerly_loaded}")

health_payload = transport.health_payload
from magi_agent.transport import health_payload as imported_health_payload
from magi_agent.transport import healthz_payload

if health_payload is not imported_health_payload:
    raise AssertionError("lazy health_payload export is not stable")
if not callable(health_payload) or not callable(healthz_payload):
    raise AssertionError("lazy health exports must be callable")
if "magi_agent.runtime.openmagi_runtime" not in sys.modules:
    raise AssertionError("accessing health exports should load the health implementation")
"""
    completed = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(script)],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
