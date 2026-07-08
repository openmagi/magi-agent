from __future__ import annotations

import json
import subprocess
import sys
from types import ModuleType
from typing import Any

from magi_agent.plugins.manager import (
    PluginOptOutRecord,
    PluginStatus,
    ResolvedPluginState,
    resolve_plugin_state,
)
from magi_agent.plugins.manifest import PluginKind, parse_plugin_manifest
from magi_agent.plugins.native_catalog import native_plugin_manifests


EXPECTED_NATIVE_PLUGIN_IDS = (
    "openmagi.agentmemory",
    "openmagi.apify",
    "openmagi.artifacts",
    "openmagi.browser",
    "openmagi.coding",
    "openmagi.deep-solve",
    "openmagi.documents",
    "openmagi.knowledge",
    "openmagi.knowledge-okf",
    "openmagi.missions",
    "openmagi.scheduled-work",
    "openmagi.security-posture",
    "openmagi.skills",
    "openmagi.source-ledger",
    "openmagi.subagents",
    "openmagi.taskboard",
    "openmagi.web",
    "openmagi.web-acquisition",
)

DEFAULT_DISABLED_PLUGIN_IDS: set[str] = set()


def _audit_module() -> ModuleType:
    import magi_agent.plugins.audit as audit

    return audit


def _opt_out(plugin_id: str) -> PluginOptOutRecord:
    return PluginOptOutRecord(
        pluginId=plugin_id,
        scope="bot",
        actor="user:test",
        reason="disabled for audit contract test",
        ts="2026-05-15T00:00:00Z",
    )


def _entry_by_id(snapshot: Any, plugin_id: str) -> Any:
    return next(entry for entry in snapshot.entries if entry.plugin_id == plugin_id)


def test_native_catalog_audit_snapshot_exposes_permissions_and_declared_secret_metadata() -> None:
    audit = _audit_module()
    state = resolve_plugin_state(native_plugin_manifests())

    snapshot = audit.build_plugin_audit_snapshot(state)

    assert tuple(entry.plugin_id for entry in snapshot.entries) == EXPECTED_NATIVE_PLUGIN_IDS
    assert tuple(entry.plugin_id for entry in snapshot.entries) == tuple(
        plugin.plugin_id for plugin in state.plugins
    )
    assert snapshot.summary.plugin_count == len(EXPECTED_NATIVE_PLUGIN_IDS)
    assert snapshot.summary.enabled_plugin_count == (
        len(EXPECTED_NATIVE_PLUGIN_IDS) - len(DEFAULT_DISABLED_PLUGIN_IDS)
    )
    assert snapshot.summary.opted_out_plugin_count == 0
    assert snapshot.summary.permission_classes == ("execute", "meta", "net", "read", "write")
    assert snapshot.summary.declared_secret_names == ("APIFY_TOKEN", "FIRECRAWL_API_KEY", "GATEWAY_TOKEN")
    assert snapshot.summary.declared_secret_sources == ("platform", "user")
    assert snapshot.traffic_attached is False
    assert snapshot.execution_attached is False

    web = _entry_by_id(snapshot, "openmagi.web")
    assert web.enabled is True
    assert web.status_reason == "enabled"
    assert web.permissions == ("read", "net")
    assert web.services == ("api-proxy", "firecrawl")
    assert tuple(secret.name for secret in web.declared_secrets) == (
        "GATEWAY_TOKEN",
        "FIRECRAWL_API_KEY",
    )
    assert tuple(secret.source for secret in web.declared_secrets) == ("platform", "platform")

    security_posture = _entry_by_id(snapshot, "openmagi.security-posture")
    assert security_posture.enabled is True
    assert security_posture.status_reason == "enabled"
    assert security_posture.permissions == ()
    assert security_posture.services == ()
    assert security_posture.tools == ()
    assert security_posture.hooks == ()
    assert security_posture.declared_secrets == ()
    assert security_posture.security_critical is True
    assert security_posture.opt_out_allowed is False
    assert security_posture.harness_rules == (
        "security_posture_matrix",
        "external_surface_fail_closed",
        "sandbox_preflight",
        "credential_pass_through_policy",
        "context_file_injection_guard",
        "supply_chain_advisory",
    )


def test_audit_entries_include_status_security_audit_and_opt_out_metadata() -> None:
    audit = _audit_module()
    snapshot = audit.build_plugin_audit_snapshot(resolve_plugin_state(native_plugin_manifests()))

    agentmemory = _entry_by_id(snapshot, "openmagi.agentmemory")
    assert agentmemory.enabled is True
    assert agentmemory.status_reason == "enabled"
    assert agentmemory.audit_required is True
    assert agentmemory.security_critical is False
    assert agentmemory.opt_out_allowed is True
    assert agentmemory.opted_out is False
    assert agentmemory.tools == ("AgentMemorySearch", "AgentMemoryRemember")
    assert agentmemory.hooks == ("agentmemory.recall", "agentmemory.observe")
    assert agentmemory.harness_rules == ("memory_agentmemory_provider_boundary",)
    assert agentmemory.traffic_attached is False
    assert agentmemory.execution_attached is False

    scheduled_work = _entry_by_id(snapshot, "openmagi.scheduled-work")
    assert scheduled_work.enabled is True
    assert scheduled_work.status_reason == "enabled"
    assert scheduled_work.audit_required is True
    assert scheduled_work.security_critical is False
    assert scheduled_work.opt_out_allowed is True
    assert scheduled_work.opted_out is False
    assert scheduled_work.permissions == ()
    assert scheduled_work.services == ()
    assert scheduled_work.tools == (
        "CronCreate",
        "CronList",
        "CronUpdate",
        "CronDelete",
        "TaskWait",
        "TaskGet",
        "TaskList",
        "TaskOutput",
        "TaskStop",
        "RunInBackground",
    )
    assert scheduled_work.hooks == ()
    assert scheduled_work.harness_rules == ("scheduled_work_recipe_policy",)
    assert scheduled_work.declared_secrets == ()
    assert scheduled_work.traffic_attached is False
    assert scheduled_work.execution_attached is False

    browser = _entry_by_id(snapshot, "openmagi.browser")
    assert browser.enabled is True
    assert browser.status_reason == "enabled"
    assert browser.tools == ("Browser", "SocialBrowser")

    web_acquisition = _entry_by_id(snapshot, "openmagi.web-acquisition")
    assert web_acquisition.enabled is True
    assert web_acquisition.status_reason == "enabled"
    assert web_acquisition.permissions == ()
    assert web_acquisition.services == ()
    assert web_acquisition.tools == ()
    assert web_acquisition.harness_rules == (
        "web_acquisition_provider_boundary",
        "web_acquisition_source_ledger_boundary",
    )

    security_posture = _entry_by_id(snapshot, "openmagi.security-posture")
    assert security_posture.enabled is True
    assert security_posture.status_reason == "enabled"
    assert security_posture.audit_required is True
    assert security_posture.security_critical is True
    assert security_posture.opt_out_allowed is False
    assert security_posture.opted_out is False
    assert security_posture.permissions == ()
    assert security_posture.services == ()
    assert security_posture.tools == ()
    assert security_posture.hooks == ()
    assert security_posture.declared_secrets == ()
    assert security_posture.traffic_attached is False
    assert security_posture.execution_attached is False

    knowledge = _entry_by_id(snapshot, "openmagi.knowledge")

    assert knowledge.enabled is True
    assert knowledge.status_reason == "enabled"
    assert knowledge.audit_required is True
    assert knowledge.security_critical is False
    assert knowledge.opt_out_allowed is True
    assert knowledge.opted_out is False
    assert knowledge.tools == (
        "KnowledgeSearch",
        "knowledge-search",
        "KnowledgeWrite",
        "knowledge-write",
    )
    assert knowledge.hooks == ()
    assert knowledge.harness_rules == ("knowledge_write_scope",)
    assert knowledge.traffic_attached is False
    assert knowledge.execution_attached is False


def test_audit_snapshot_forces_attachment_flags_false_for_metadata_only_contract() -> None:
    audit = _audit_module()
    status = PluginStatus(
        pluginId="openmagi.attached-test",
        kind=PluginKind.NATIVE,
        version="0.1.0-adk-scaffold",
        installed=True,
        enabled=True,
        optedOut=False,
        defaultInstalled=True,
        defaultEnabled=True,
        optOutAllowed=True,
        securityCritical=False,
        auditRequired=True,
        statusReason="enabled",
        permissions=("read",),
        services=("runtime-looking-service",),
        trafficAttached=True,
        executionAttached=True,
    )
    state = ResolvedPluginState(
        plugins=(status,),
        activeTools=(),
        activeHooks=(),
        activeHarnessRules=(),
        trafficAttached=True,
        executionAttached=True,
    )

    snapshot = audit.build_plugin_audit_snapshot(state)
    entry = _entry_by_id(snapshot, "openmagi.attached-test")
    dumped = snapshot.model_dump(by_alias=True)

    assert entry.traffic_attached is False
    assert entry.execution_attached is False
    assert snapshot.summary.traffic_attached is False
    assert snapshot.summary.execution_attached is False
    assert snapshot.traffic_attached is False
    assert snapshot.execution_attached is False
    assert dumped["entries"][0]["trafficAttached"] is False
    assert dumped["entries"][0]["executionAttached"] is False
    assert dumped["summary"]["trafficAttached"] is False
    assert dumped["summary"]["executionAttached"] is False
    assert dumped["trafficAttached"] is False
    assert dumped["executionAttached"] is False


def test_opted_out_plugin_retains_declared_audit_metadata_while_manager_removes_active_tools() -> None:
    audit = _audit_module()
    manifests = native_plugin_manifests()
    state = resolve_plugin_state(manifests, (_opt_out("openmagi.web"),))

    assert "WebSearch" not in state.active_tools
    assert "web-search" not in state.active_tools
    assert "web_search" not in state.active_tools
    assert "WebFetch" not in state.active_tools

    snapshot = audit.build_plugin_audit_snapshot(state)
    web = _entry_by_id(snapshot, "openmagi.web")

    assert web.enabled is False
    assert web.opted_out is True
    assert web.status_reason == "opted_out"
    assert web.permissions == ("read", "net")
    assert web.tools == ("WebSearch", "web-search", "web_search", "WebFetch")
    assert tuple(secret.name for secret in web.declared_secrets) == (
        "GATEWAY_TOKEN",
        "FIRECRAWL_API_KEY",
    )
    assert snapshot.summary.enabled_plugin_count == len(EXPECTED_NATIVE_PLUGIN_IDS) - 1
    assert snapshot.summary.opted_out_plugin_count == 1
    assert snapshot.summary.permission_classes == ("execute", "meta", "net", "read", "write")
    assert snapshot.summary.declared_secret_names == ("APIFY_TOKEN", "FIRECRAWL_API_KEY", "GATEWAY_TOKEN")
    assert snapshot.summary.declared_secret_sources == ("platform", "user")


def test_audit_model_dumps_use_dashboard_compatible_aliases() -> None:
    audit = _audit_module()
    snapshot = audit.build_plugin_audit_snapshot(resolve_plugin_state(native_plugin_manifests()))

    entry_dump = _entry_by_id(snapshot, "openmagi.browser").model_dump(by_alias=True)
    snapshot_dump = snapshot.model_dump(by_alias=True)

    for key in (
        "pluginId",
        "statusReason",
        "auditRequired",
        "securityCritical",
        "optOutAllowed",
        "declaredSecrets",
        "trafficAttached",
        "executionAttached",
    ):
        assert key in entry_dump

    assert "plugin_id" not in entry_dump
    assert "status_reason" not in entry_dump
    assert "audit_required" not in entry_dump
    assert "security_critical" not in entry_dump
    assert "opt_out_allowed" not in entry_dump
    assert "declared_secrets" not in entry_dump
    assert snapshot_dump["entries"][0]["pluginId"] == "openmagi.agentmemory"
    assert snapshot_dump["summary"]["pluginCount"] == len(EXPECTED_NATIVE_PLUGIN_IDS)
    assert snapshot_dump["trafficAttached"] is False
    assert snapshot_dump["executionAttached"] is False


def test_audit_dump_excludes_secret_values_config_schema_and_executable_references() -> None:
    audit = _audit_module()
    manifest = parse_plugin_manifest(
        {
            "id": "openmagi.leakcheck",
            "name": "Leak Check",
            "kind": "native",
            "version": "0.1.0-adk-scaffold",
            "defaultInstalled": True,
            "defaultEnabled": True,
            "optOutAllowed": True,
            "audit_required": True,
            "permissions": ("read", "net"),
            "services": ("leak-check-worker",),
            "tools": (
                {
                    "name": "LeakCheck",
                    "entrypoint": "plugins.leakcheck:run",
                },
            ),
            "hooks": (
                {
                    "name": "leak_check_hook",
                    "entrypoint": "plugins.leakcheck:hook",
                },
            ),
            "harnessRules": ("leak_check_rule",),
            "secrets": (
                {
                    "name": "LEAKY_TOKEN",
                    "source": "platform",
                    "value": "super-secret-token-value",
                    "configSchema": {"type": "string", "secretValue": "nested-secret"},
                },
            ),
            "configSchema": {
                "type": "object",
                "secretValue": "manifest-secret",
            },
        }
    )
    snapshot = audit.build_plugin_audit_snapshot(resolve_plugin_state((manifest,)))

    dumped = snapshot.model_dump(by_alias=True)
    dumped_json = json.dumps(dumped, sort_keys=True)

    assert dumped["entries"][0]["tools"] == ("LeakCheck",)
    assert dumped["entries"][0]["hooks"] == ("leak_check_hook",)
    assert dumped["entries"][0]["declaredSecrets"] == (
        {"name": "LEAKY_TOKEN", "source": "platform"},
    )
    assert "super-secret-token-value" not in dumped_json
    assert "nested-secret" not in dumped_json
    assert "manifest-secret" not in dumped_json
    assert "configSchema" not in dumped_json
    assert "entrypoint" not in dumped_json
    assert "plugins.leakcheck" not in dumped_json


def test_audit_import_boundary_does_not_load_adk_runtime_execution_routes_or_native_modules() -> None:
    script = """
import importlib
import sys

importlib.import_module("magi_agent.plugins.audit")
forbidden_prefixes = (
    "google.adk",
    "magi_agent.adk_bridge",
    "magi_agent.runtime",
    "magi_agent.transport",
    "magi_agent.tools.dispatcher",
    "magi_agent.tools.registry",
    "magi_agent.hooks.bus",
    "magi_agent.plugins.native",
    "magi_agent.app",
    "magi_agent.main",
)
loaded = [
    name
    for name in sys.modules
    if any(name == prefix or name.startswith(f"{prefix}.") for prefix in forbidden_prefixes)
]
if loaded:
    raise AssertionError(f"audit import loaded forbidden modules: {loaded}")
"""
    completed = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr


def test_plugins_package_import_keeps_audit_exports_lazy_and_does_not_load_runtime_modules() -> None:
    script = """
import importlib
import sys

package = importlib.import_module("magi_agent.plugins")
assert "build_plugin_audit_snapshot" in package.__all__
forbidden_exact = (
    "magi_agent.plugins.audit",
    "magi_agent.plugins.manager",
    "magi_agent.plugins.native_catalog",
)
forbidden_prefixes = (
    "google.adk",
    "magi_agent.adk_bridge",
    "magi_agent.runtime",
    "magi_agent.transport",
    "magi_agent.tools.dispatcher",
    "magi_agent.tools.registry",
    "magi_agent.hooks.bus",
    "magi_agent.plugins.native",
    "magi_agent.app",
    "magi_agent.main",
)
loaded = [
    name
    for name in sys.modules
    if name in forbidden_exact
    or any(name == prefix or name.startswith(f"{prefix}.") for prefix in forbidden_prefixes)
]
if loaded:
    raise AssertionError(f"plugins package import loaded forbidden modules: {loaded}")
"""
    completed = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
