from __future__ import annotations

import subprocess
import sys

import pytest
from pydantic import ValidationError

from magi_agent.plugins.manifest import parse_plugin_manifest
from magi_agent.plugins.manager import (
    PluginOptOutRecord,
    resolve_plugin_state,
)


def _manifest(**overrides: object):
    data: dict[str, object] = {
        "id": "openmagi.knowledge",
        "name": "Knowledge",
        "version": "0.1.0",
        "kind": "native",
        "defaultInstalled": True,
        "defaultEnabled": True,
        "optOutAllowed": True,
        "securityCritical": False,
        "audit_required": False,
        "permissions": ("read", "net"),
        "services": ("knowledge-worker",),
        "tools": (
            {
                "name": "KnowledgeSearch",
                "entrypoint": "openmagi_native.knowledge:search",
            },
        ),
        "hooks": (
            {
                "name": "knowledge_audit",
                "point": "afterToolUse",
                "entrypoint": "openmagi_native.knowledge:audit",
            },
        ),
        "harnessRules": ("cite_sources",),
        "secrets": (
            {
                "name": "KNOWLEDGE_WORKER_TOKEN",
                "source": "platform",
            },
        ),
    }
    data.update(overrides)
    return parse_plugin_manifest(data)


def _opt_out(plugin_id: str) -> PluginOptOutRecord:
    return PluginOptOutRecord(
        pluginId=plugin_id,
        scope="bot",
        actor="user:test",
        reason="disabled for test",
        ts="2026-05-15T00:00:00Z",
        effectiveRuntimeVersion="0.1.0-adk-scaffold",
    )


def test_default_install_enable_resolution_for_two_manifests() -> None:
    state = resolve_plugin_state(
        (
            _manifest(
                id="openmagi.zeta",
                tools=({"name": "ZetaTool", "entrypoint": "plugins.zeta:run"},),
                hooks=(),
                harnessRules=(),
            ),
            _manifest(
                id="openmagi.alpha",
                defaultEnabled=False,
                tools=({"name": "AlphaTool", "entrypoint": "plugins.alpha:run"},),
                hooks=({"name": "alpha_hook", "entrypoint": "plugins.alpha:hook"},),
                harnessRules=("alpha_rule",),
            ),
        )
    )

    assert tuple(status.plugin_id for status in state.plugins) == ("openmagi.alpha", "openmagi.zeta")
    alpha, zeta = state.plugins
    assert alpha.installed is True
    assert alpha.enabled is False
    assert alpha.status_reason == "default_disabled"
    assert zeta.installed is True
    assert zeta.enabled is True
    assert zeta.status_reason == "enabled"
    assert state.active_tools == ("ZetaTool",)
    assert state.active_hooks == ()
    assert state.active_harness_rules == ()
    assert state.traffic_attached is False
    assert state.execution_attached is False


def test_opted_out_plugin_disables_and_derives_affected_metadata() -> None:
    knowledge = _manifest()
    shell = _manifest(
        id="openmagi.shell",
        tools=({"name": "Bash", "entrypoint": "plugins.shell:run"},),
        hooks=({"name": "shell_audit", "entrypoint": "plugins.shell:audit"},),
        harnessRules=("path_guard",),
    )

    state = resolve_plugin_state((knowledge, shell), (_opt_out("openmagi.shell"),))

    assert tuple(status.plugin_id for status in state.plugins) == ("openmagi.knowledge", "openmagi.shell")
    opted_out = state.plugins[1]
    assert opted_out.installed is True
    assert opted_out.enabled is False
    assert opted_out.opted_out is True
    assert opted_out.status_reason == "opted_out"
    assert state.active_tools == ("KnowledgeSearch",)
    assert state.active_hooks == ("knowledge_audit",)
    assert state.active_harness_rules == ("cite_sources",)
    assert state.opt_outs[0].affected_tools == ("Bash",)
    assert state.opt_outs[0].affected_hooks == ("shell_audit",)
    assert state.opt_outs[0].affected_harness_rules == ("path_guard",)


def test_non_default_installed_manifest_remains_disabled_status_entry() -> None:
    state = resolve_plugin_state(
        (
            _manifest(),
            _manifest(
                id="openmagi.optional",
                defaultInstalled=False,
                defaultEnabled=False,
                tools=({"name": "OptionalTool", "entrypoint": "plugins.optional:run"},),
                hooks=({"name": "optional_hook", "entrypoint": "plugins.optional:hook"},),
                harnessRules=("optional_rule",),
            ),
        )
    )

    assert tuple(status.plugin_id for status in state.plugins) == (
        "openmagi.knowledge",
        "openmagi.optional",
    )
    optional = state.plugins[1]
    assert optional.installed is False
    assert optional.enabled is False
    assert optional.status_reason == "not_default_installed"
    assert state.active_tools == ("KnowledgeSearch",)
    assert state.active_hooks == ("knowledge_audit",)
    assert state.active_harness_rules == ("cite_sources",)


def test_derived_opt_out_record_dumps_typescript_aliases() -> None:
    state = resolve_plugin_state(
        (
            _manifest(
                tools=({"name": "KnowledgeSearch", "entrypoint": "plugins.knowledge:search"},),
                hooks=({"name": "knowledge_audit", "entrypoint": "plugins.knowledge:audit"},),
                harnessRules=("cite_sources",),
            ),
        ),
        (
            PluginOptOutRecord(
                pluginId="openmagi.knowledge",
                scope="bot",
                actor="user:test",
                reason=None,
                ts="2026-05-15T00:00:00Z",
            ),
        ),
        runtime_version="0.2.0-adk-scaffold",
    )

    dumped = state.opt_outs[0].model_dump(by_alias=True)

    assert dumped["pluginId"] == "openmagi.knowledge"
    assert dumped["effectiveRuntimeVersion"] == "0.2.0-adk-scaffold"
    assert dumped["affectedTools"] == ("KnowledgeSearch",)
    assert dumped["affectedHooks"] == ("knowledge_audit",)
    assert dumped["affectedHarnessRules"] == ("cite_sources",)


def test_derived_opt_out_affected_tools_union_manifest_tools_and_tool_capabilities() -> None:
    state = resolve_plugin_state(
        (
            _manifest(
                id="openmagi.partial",
                tools=(
                    {"name": "RealTool", "entrypoint": "plugins.partial:real"},
                    {"name": "SecondTool", "entrypoint": "plugins.partial:second"},
                ),
                capabilities=(
                    {"type": "tool", "name": "RealTool"},
                    {"type": "hook", "name": "partial_hook"},
                    {"type": "tool", "name": "CapabilityOnlyTool"},
                ),
                hooks=(),
                harnessRules=(),
            ),
        ),
        (
            PluginOptOutRecord(
                pluginId="openmagi.partial",
                scope="bot",
                actor="user:test",
                reason="partial capability regression",
                ts="2026-05-15T00:00:00Z",
            ),
        ),
    )

    assert state.opt_outs[0].affected_tools == (
        "RealTool",
        "SecondTool",
        "CapabilityOnlyTool",
    )


def test_prefilled_opt_out_affected_metadata_is_preserved() -> None:
    state = resolve_plugin_state(
        (
            _manifest(
                tools=({"name": "KnowledgeSearch", "entrypoint": "plugins.knowledge:search"},),
                hooks=({"name": "knowledge_audit", "entrypoint": "plugins.knowledge:audit"},),
                harnessRules=("cite_sources",),
            ),
        ),
        (
            PluginOptOutRecord(
                pluginId="openmagi.knowledge",
                scope="bot",
                actor="user:test",
                reason="custom affected metadata",
                ts="2026-05-15T00:00:00Z",
                effectiveRuntimeVersion="0.1.5",
                affectedTools=("CustomTool",),
                affectedHooks=("custom_hook",),
                affectedHarnessRules=("custom_rule",),
            ),
        ),
        runtime_version="0.2.0-adk-scaffold",
    )

    opt_out = state.opt_outs[0]
    assert opt_out.effective_runtime_version == "0.1.5"
    assert opt_out.affected_tools == ("CustomTool",)
    assert opt_out.affected_hooks == ("custom_hook",)
    assert opt_out.affected_harness_rules == ("custom_rule",)


def test_active_lists_are_sorted_and_deduped_across_overlapping_plugins() -> None:
    state = resolve_plugin_state(
        (
            _manifest(
                id="openmagi.zeta",
                tools=(
                    {"name": "SharedTool", "entrypoint": "plugins.zeta:shared"},
                    {"name": "ZetaTool", "entrypoint": "plugins.zeta:run"},
                ),
                hooks=(
                    {"name": "shared_hook", "entrypoint": "plugins.zeta:shared_hook"},
                    {"name": "zeta_hook", "entrypoint": "plugins.zeta:hook"},
                ),
                harnessRules=("shared_rule", "zeta_rule"),
            ),
            _manifest(
                id="openmagi.alpha",
                tools=(
                    {"name": "AlphaTool", "entrypoint": "plugins.alpha:run"},
                    {"name": "SharedTool", "entrypoint": "plugins.alpha:shared"},
                ),
                hooks=(
                    {"name": "alpha_hook", "entrypoint": "plugins.alpha:hook"},
                    {"name": "shared_hook", "entrypoint": "plugins.alpha:shared_hook"},
                ),
                harnessRules=("alpha_rule", "shared_rule"),
            ),
        )
    )

    assert state.active_tools == ("AlphaTool", "SharedTool", "ZetaTool")
    assert state.active_hooks == ("alpha_hook", "shared_hook", "zeta_hook")
    assert state.active_harness_rules == ("alpha_rule", "shared_rule", "zeta_rule")


@pytest.mark.parametrize(
    "overrides",
    (
        {"id": "openmagi.guard", "securityCritical": True, "optOutAllowed": False},
        {"id": "openmagi.required", "optOutAllowed": False},
    ),
)
def test_security_critical_and_non_opt_out_plugins_cannot_be_opted_out(
    overrides: dict[str, object],
) -> None:
    manifest = _manifest(**overrides)

    with pytest.raises(ValueError, match="cannot be opted out"):
        resolve_plugin_state((manifest,), (_opt_out(manifest.plugin_id),))


def test_unknown_opt_out_and_duplicate_plugin_ids_reject() -> None:
    with pytest.raises(ValueError, match="unknown plugin"):
        resolve_plugin_state((_manifest(),), (_opt_out("openmagi.missing"),))

    with pytest.raises(ValueError, match="duplicate plugin id"):
        resolve_plugin_state((_manifest(), _manifest()))


def test_duplicate_opt_outs_for_same_plugin_reject() -> None:
    with pytest.raises(ValueError, match="duplicate opt-out plugin id"):
        resolve_plugin_state(
            (_manifest(),),
            (
                _opt_out("openmagi.knowledge"),
                _opt_out("openmagi.knowledge"),
            ),
        )


@pytest.mark.parametrize("field_name", ("actor", "ts"))
@pytest.mark.parametrize("blank_value", ("", "   "))
def test_opt_out_actor_and_ts_reject_blank_values(field_name: str, blank_value: str) -> None:
    data = {
        "pluginId": "openmagi.knowledge",
        "scope": "bot",
        "actor": "user:test",
        "reason": "disabled for test",
        "ts": "2026-05-15T00:00:00Z",
    }
    data[field_name] = blank_value

    with pytest.raises(ValidationError, match=field_name):
        PluginOptOutRecord.model_validate(data)


def test_status_alias_serialization_includes_policy_and_attachment_fields() -> None:
    state = resolve_plugin_state((_manifest(audit_required=True),))

    dumped = state.plugins[0].model_dump(by_alias=True)

    assert dumped["pluginId"] == "openmagi.knowledge"
    assert dumped["defaultInstalled"] is True
    assert dumped["defaultEnabled"] is True
    assert dumped["optOutAllowed"] is True
    assert dumped["securityCritical"] is False
    assert dumped["auditRequired"] is True
    assert dumped["statusReason"] == "enabled"
    assert dumped["trafficAttached"] is False
    assert dumped["executionAttached"] is False


def test_repeated_fields_are_tuples_and_models_are_immutable() -> None:
    state = resolve_plugin_state((_manifest(),))
    status = state.plugins[0]
    opt_out = _opt_out("openmagi.knowledge")

    assert isinstance(state.plugins, tuple)
    assert isinstance(state.active_tools, tuple)
    assert isinstance(state.active_hooks, tuple)
    assert isinstance(state.active_harness_rules, tuple)
    assert isinstance(status.tools, tuple)
    assert isinstance(status.hooks, tuple)
    assert isinstance(status.harness_rules, tuple)
    assert isinstance(status.secrets, tuple)
    assert isinstance(status.permissions, tuple)
    assert isinstance(status.services, tuple)
    assert isinstance(opt_out.affected_tools, tuple)
    assert isinstance(opt_out.affected_hooks, tuple)
    assert isinstance(opt_out.affected_harness_rules, tuple)

    with pytest.raises(ValidationError):
        status.enabled = False  # type: ignore[misc]
    with pytest.raises(ValidationError):
        state.active_tools = ()  # type: ignore[misc]
    with pytest.raises(ValidationError):
        opt_out.actor = "user:other"  # type: ignore[misc]


def test_manager_import_boundary_does_not_load_adk_runtime_or_execution_modules() -> None:
    script = """
import importlib
import sys

importlib.import_module("magi_agent.plugins.manager")
forbidden_prefixes = (
    "google.adk",
    "magi_agent.adk_bridge",
    "magi_agent.runtime",
    "magi_agent.tools.dispatcher",
    "magi_agent.tools.registry",
    "magi_agent.hooks.bus",
    "magi_agent.transport",
)
loaded = [name for name in sys.modules if name == forbidden_prefixes[0] or name.startswith(forbidden_prefixes)]
if loaded:
    raise AssertionError(f"manager import loaded forbidden modules: {loaded}")
"""
    completed = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
