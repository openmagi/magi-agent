from __future__ import annotations

import copy
import json
import re
from collections.abc import Mapping
from enum import Enum
from json import JSONDecodeError
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from openmagi_core_agent.plugins.sandbox_policy import PluginSandboxPolicy, PluginTrustLevel


class PluginKind(str, Enum):
    CORE = "core"
    NATIVE = "native"
    CUSTOM = "custom"


PluginCapabilityType = Literal["tool", "hook", "harness", "classifier", "service-endpoint", "verifier"]
PermissionClass = Literal["read", "write", "execute", "net", "meta"]
SecretSource = Literal["platform", "user", "plugin", "environment"]

_PLUGIN_ID_RE = re.compile(r"^[a-z][a-z0-9-]*(?:\.[a-z][a-z0-9-]*)+$")
_ENTRYPOINT_RE = re.compile(r"^[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*:[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*$")
_PERMISSION_VALUES = {"read", "write", "execute", "net", "meta"}


class PluginCapability(BaseModel):
    model_config = ConfigDict(frozen=True)

    type: PluginCapabilityType
    name: str


class PluginRuntime(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    min_core_version: str | None = Field(default=None, alias="minCoreVersion")
    adk_compatibility: str | None = Field(default=None, alias="adkCompatibility")


class PluginToolRef(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    entrypoint: str

    @field_validator("name", "entrypoint")
    @classmethod
    def _reject_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("tool name and entrypoint must be non-empty")
        return value

    @field_validator("entrypoint")
    @classmethod
    def _validate_entrypoint(cls, value: str) -> str:
        if not _ENTRYPOINT_RE.fullmatch(value):
            raise ValueError("tool entrypoint must use module:callable style")
        return value


class PluginHookRef(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    point: str | None = None
    entrypoint: str | None = None

    @field_validator("name")
    @classmethod
    def _reject_empty_name(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("hook name must be non-empty")
        return value

    @field_validator("entrypoint")
    @classmethod
    def _validate_optional_entrypoint(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not value.strip():
            raise ValueError("hook entrypoint must be non-empty")
        if not _ENTRYPOINT_RE.fullmatch(value):
            raise ValueError("hook entrypoint must use module:callable style")
        return value


class PluginSecretRef(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    source: SecretSource

    @field_validator("name")
    @classmethod
    def _reject_empty_name(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("secret name must be non-empty")
        return value


class PluginManifest(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    plugin_id: str = Field(alias="id")
    name: str = ""
    kind: PluginKind
    version: str
    description: str = ""
    default_installed: bool = Field(default=False, alias="defaultInstalled")
    default_enabled: bool = Field(default=False, alias="defaultEnabled")
    opt_out: bool = Field(default=True, alias="optOutAllowed")
    audit_required: bool = False
    security_critical: bool = Field(default=False, alias="securityCritical")
    publisher: str | None = None
    runtime: PluginRuntime = Field(default_factory=PluginRuntime)
    permissions: tuple[PermissionClass, ...] = ()
    services: tuple[str, ...] = ()
    tools: tuple[PluginToolRef, ...] = ()
    hooks: tuple[PluginHookRef, ...] = ()
    harness_rules: tuple[str, ...] = Field(default=(), alias="harnessRules")
    secrets: tuple[PluginSecretRef, ...] = ()
    config_schema: dict[str, object] = Field(default_factory=dict, alias="configSchema")
    capabilities: tuple[PluginCapability, ...] = ()
    trust_level: PluginTrustLevel = Field(default="untrusted", alias="trustLevel")
    supply_chain_digest: str | None = Field(default=None, alias="supplyChainDigest")
    manifest_digest: str | None = Field(default=None, alias="manifestDigest")
    sandbox: PluginSandboxPolicy | None = None

    @property
    def opt_out_allowed(self) -> bool:
        return self.opt_out

    @model_validator(mode="before")
    @classmethod
    def _reject_conflicting_duplicate_aliases(cls, data: object) -> object:
        if not isinstance(data, Mapping):
            return data

        duplicate_fields = (
            ("securityCritical", "security_critical"),
            ("optOutAllowed", "opt_out"),
        )
        for alias, field_name in duplicate_fields:
            if alias in data and field_name in data and data[alias] != data[field_name]:
                raise ValueError(f"conflicting duplicate inputs for {alias}/{field_name}")
        return data

    @field_validator("plugin_id")
    @classmethod
    def _validate_plugin_id(cls, value: str) -> str:
        if not value or not _PLUGIN_ID_RE.fullmatch(value):
            raise ValueError(
                "plugin id must be a non-empty dotted namespace without spaces, slashes, or path traversal"
            )
        return value

    @field_validator("permissions", mode="before")
    @classmethod
    def _validate_permissions(cls, value: object) -> object:
        if value is None:
            return ()
        if isinstance(value, str):
            values = (value,)
        else:
            values = tuple(value)  # type: ignore[arg-type]
        invalid = [item for item in values if item not in _PERMISSION_VALUES]
        if invalid:
            raise ValueError("permissions must use read/write/execute/net/meta classes")
        return values

    @field_validator("config_schema")
    @classmethod
    def _copy_config_schema(cls, value: dict[str, object]) -> dict[str, object]:
        return copy.deepcopy(value)

    @field_validator("supply_chain_digest", "manifest_digest")
    @classmethod
    def _validate_optional_digest(cls, value: str | None) -> str | None:
        if value is None:
            return None
        suffix = value.removeprefix("sha256:")
        if not value.startswith("sha256:") or len(suffix) != 64 or any(
            char not in "0123456789abcdef" for char in suffix
        ):
            raise ValueError("plugin digests must be sha256 digests")
        return value

    @model_validator(mode="after")
    def _validate_contract(self) -> PluginManifest:
        if self.kind is PluginKind.NATIVE and not self.plugin_id.startswith("openmagi."):
            raise ValueError("native plugin manifests must use an openmagi. plugin id namespace")
        if self.default_enabled and not self.default_installed:
            raise ValueError("defaultEnabled cannot be true when defaultInstalled is false")
        if self.security_critical and self.opt_out:
            raise ValueError("securityCritical plugins cannot be opt-out allowed")
        return self


def parse_plugin_manifest(data: Mapping[str, object] | str) -> PluginManifest:
    if isinstance(data, str):
        try:
            parsed = json.loads(data)
        except JSONDecodeError as exc:
            raise ValueError(
                "Only JSON object strings are supported; YAML manifest text requires a YAML parser and is not supported"
            ) from exc
        if not isinstance(parsed, Mapping):
            raise ValueError("Plugin manifest JSON string must contain a JSON object")
        return PluginManifest.model_validate(parsed)

    if not isinstance(data, Mapping):
        raise ValueError("Plugin manifest input must be a mapping object or JSON object string")

    return PluginManifest.model_validate(data)
