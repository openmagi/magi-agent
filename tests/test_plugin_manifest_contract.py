from __future__ import annotations

import json
import subprocess
import sys

import pytest
from pydantic import ValidationError

from openmagi_core_agent.plugins import manifest as manifest_contract

PluginKind = manifest_contract.PluginKind
PluginManifest = manifest_contract.PluginManifest


def parse_plugin_manifest(data: object) -> PluginManifest:
    return manifest_contract.parse_plugin_manifest(data)  # type: ignore[attr-defined]


def _valid_manifest_data(**overrides: object) -> dict[str, object]:
    data: dict[str, object] = {
        "id": "openmagi.knowledge",
        "name": "Knowledge",
        "version": "0.1.0",
        "kind": "native",
        "description": "OpenMagi native knowledge plugin.",
        "defaultInstalled": True,
        "defaultEnabled": True,
        "optOutAllowed": True,
        "securityCritical": False,
        "publisher": "OpenMagi",
        "runtime": {
            "minCoreVersion": "0.1.0",
            "adkCompatibility": "google-adk>=1.33",
        },
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
        "configSchema": {"type": "object", "additionalProperties": False},
    }
    data.update(overrides)
    return data


def _minimal_manifest_data(**overrides: object) -> dict[str, object]:
    data: dict[str, object] = {
        "id": "openmagi.minimal",
        "version": "0.1.0",
        "kind": "native",
    }
    data.update(overrides)
    return data


def test_prd_shape_dict_parses_into_frozen_manifest_and_dumps_aliases() -> None:
    manifest = parse_plugin_manifest(_valid_manifest_data())

    assert manifest.plugin_id == "openmagi.knowledge"
    assert manifest.name == "Knowledge"
    assert manifest.kind is PluginKind.NATIVE
    assert manifest.default_installed is True
    assert manifest.default_enabled is True
    assert manifest.opt_out is True
    assert manifest.audit_required is False
    assert manifest.runtime.min_core_version == "0.1.0"
    assert manifest.runtime.adk_compatibility == "google-adk>=1.33"
    assert manifest.permissions == ("read", "net")
    assert manifest.services == ("knowledge-worker",)
    assert manifest.tools[0].entrypoint == "openmagi_native.knowledge:search"
    assert manifest.hooks[0].point == "afterToolUse"
    assert manifest.harness_rules == ("cite_sources",)
    assert manifest.secrets[0].source == "platform"
    assert manifest.config_schema == {"type": "object", "additionalProperties": False}

    dumped = manifest.model_dump(by_alias=True)
    assert dumped["id"] == "openmagi.knowledge"
    assert dumped["defaultInstalled"] is True
    assert dumped["defaultEnabled"] is True
    assert dumped["optOutAllowed"] is True
    assert dumped["securityCritical"] is False
    assert dumped["runtime"]["minCoreVersion"] == "0.1.0"
    assert dumped["runtime"]["adkCompatibility"] == "google-adk>=1.33"
    assert dumped["harnessRules"] == ("cite_sources",)
    assert dumped["configSchema"] == {"type": "object", "additionalProperties": False}

    with pytest.raises(ValidationError):
        manifest.name = "Changed"  # type: ignore[misc]


def test_legacy_constructor_fields_still_work() -> None:
    manifest = PluginManifest(
        plugin_id="openmagi.cloud",
        kind=PluginKind.NATIVE,
        version="0.1.0",
        default_installed=True,
        opt_out=True,
        audit_required=True,
        capabilities=[
            {"type": "tool", "name": "KnowledgeSearch"},
            {"type": "hook", "name": "cloud_audit"},
        ],
    )

    assert manifest.plugin_id == "openmagi.cloud"
    assert manifest.default_installed is True
    assert manifest.opt_out is True
    assert manifest.audit_required is True
    assert manifest.security_critical is False
    assert tuple(cap.type for cap in manifest.capabilities) == ("tool", "hook")
    assert manifest.model_dump(by_alias=True)["id"] == "openmagi.cloud"
    assert manifest.model_dump(by_alias=True)["securityCritical"] is False


def test_omitted_prd_security_defaults_dump_non_contradictory_aliases() -> None:
    data = _minimal_manifest_data()

    manifest = parse_plugin_manifest(data)

    assert manifest.opt_out is True
    assert manifest.security_critical is False
    dumped = manifest.model_dump(by_alias=True)
    assert dumped["optOutAllowed"] is True
    assert dumped["securityCritical"] is False


def test_legacy_audit_required_opt_out_does_not_dump_security_critical() -> None:
    manifest = PluginManifest(
        plugin_id="openmagi.audit",
        kind=PluginKind.NATIVE,
        version="0.1.0",
        opt_out=True,
        audit_required=True,
    )

    assert manifest.audit_required is True
    assert manifest.opt_out is True
    assert manifest.security_critical is False
    dumped = manifest.model_dump(by_alias=True)
    assert dumped["optOutAllowed"] is True
    assert dumped["securityCritical"] is False


@pytest.mark.parametrize(
    "field_overrides",
    (
        {"security_critical": True, "securityCritical": False},
        {"securityCritical": True, "security_critical": False},
        {"opt_out": False, "optOutAllowed": True},
        {"optOutAllowed": False, "opt_out": True},
    ),
)
def test_conflicting_duplicate_security_aliases_reject_before_normalization(
    field_overrides: dict[str, object],
) -> None:
    with pytest.raises(ValidationError, match="conflicting duplicate"):
        parse_plugin_manifest(_minimal_manifest_data(**field_overrides))


@pytest.mark.parametrize(
    "field_overrides",
    (
        {"security_critical": True, "securityCritical": True, "opt_out": False},
        {"optOutAllowed": False, "opt_out": False, "securityCritical": True},
    ),
)
def test_matching_duplicate_security_aliases_are_accepted(
    field_overrides: dict[str, object],
) -> None:
    manifest = parse_plugin_manifest(_minimal_manifest_data(**field_overrides))

    assert manifest.security_critical is True
    assert manifest.opt_out is False


@pytest.mark.parametrize(
    "field_overrides",
    (
        {"security_critical": True, "opt_out": True},
        {"security_critical": True, "optOutAllowed": True},
        {"securityCritical": True, "opt_out": True},
        {"securityCritical": True, "optOutAllowed": True},
    ),
)
def test_security_critical_rejects_opt_out_after_normalization(
    field_overrides: dict[str, object],
) -> None:
    with pytest.raises(ValidationError, match="securityCritical"):
        parse_plugin_manifest(_minimal_manifest_data(**field_overrides))


@pytest.mark.parametrize(
    "field_overrides",
    (
        {"security_critical": True, "opt_out": False},
        {"securityCritical": True, "optOutAllowed": False},
    ),
)
def test_security_critical_accepts_non_opt_out_field_and_alias_inputs(
    field_overrides: dict[str, object],
) -> None:
    manifest = parse_plugin_manifest(_minimal_manifest_data(**field_overrides))

    assert manifest.security_critical is True
    assert manifest.opt_out is False
    dumped = manifest.model_dump(by_alias=True)
    assert dumped["securityCritical"] is True
    assert dumped["optOutAllowed"] is False


def test_config_schema_is_defensively_copied_from_nested_input_mutation() -> None:
    config_schema = {
        "type": "object",
        "properties": {
            "enabled": {
                "type": "boolean",
            },
        },
    }
    manifest = parse_plugin_manifest(_minimal_manifest_data(configSchema=config_schema))

    config_schema["properties"]["enabled"]["type"] = "string"  # type: ignore[index]

    assert manifest.config_schema == {
        "type": "object",
        "properties": {
            "enabled": {
                "type": "boolean",
            },
        },
    }


def test_json_string_parse_is_object_only_and_yaml_is_rejected_clearly() -> None:
    manifest = parse_plugin_manifest(json.dumps(_valid_manifest_data(id="openmagi.web")))

    assert manifest.plugin_id == "openmagi.web"

    with pytest.raises(ValueError, match="JSON object"):
        parse_plugin_manifest('["openmagi.web"]')

    with pytest.raises(ValueError, match="YAML"):
        parse_plugin_manifest("id: openmagi.web\nkind: native\n")


@pytest.mark.parametrize(
    ("field_overrides", "error_match"),
    (
        ({"id": ""}, "plugin id"),
        ({"id": "openmagi/knowledge"}, "plugin id"),
        ({"id": "../openmagi.knowledge"}, "plugin id"),
        ({"id": "openmagi knowledge"}, "plugin id"),
        ({"id": "vendor.knowledge"}, "native plugin"),
        ({"defaultInstalled": False, "defaultEnabled": True}, "defaultEnabled"),
        ({"securityCritical": True, "optOutAllowed": True}, "securityCritical"),
        ({"secrets": ({"name": "TOKEN", "source": "vault"},)}, "source"),
        ({"permissions": ("read", "admin")}, "permissions"),
        ({"tools": ({"name": "BadTool", "entrypoint": "not-a-callable"},)}, "entrypoint"),
    ),
)
def test_manifest_validation_rejects_invalid_contracts(
    field_overrides: dict[str, object],
    error_match: str,
) -> None:
    with pytest.raises(ValidationError, match=error_match):
        parse_plugin_manifest(_valid_manifest_data(**field_overrides))


def test_repeated_fields_are_tuples_and_model_is_immutable() -> None:
    manifest = parse_plugin_manifest(_valid_manifest_data())

    assert isinstance(manifest.permissions, tuple)
    assert isinstance(manifest.services, tuple)
    assert isinstance(manifest.tools, tuple)
    assert isinstance(manifest.hooks, tuple)
    assert isinstance(manifest.harness_rules, tuple)
    assert isinstance(manifest.secrets, tuple)

    with pytest.raises(ValidationError):
        manifest.default_enabled = False  # type: ignore[misc]


def test_manifest_import_boundary_does_not_load_adk_or_runtime_modules() -> None:
    script = """
import importlib
import sys

importlib.import_module("openmagi_core_agent.plugins.manifest")
forbidden_prefixes = (
    "google.adk",
    "openmagi_core_agent.adk_bridge",
    "openmagi_core_agent.runtime.openmagi_runtime",
    "openmagi_core_agent.tools.dispatcher",
    "openmagi_core_agent.hooks.bus",
    "openmagi_core_agent.transport.chat",
    "openmagi_core_agent.transport.tools",
)
loaded = [name for name in sys.modules if name == forbidden_prefixes[0] or name.startswith(forbidden_prefixes)]
if loaded:
    raise AssertionError(f"manifest import loaded forbidden modules: {loaded}")
"""
    completed = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
