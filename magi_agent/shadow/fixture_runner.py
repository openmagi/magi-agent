from __future__ import annotations

import asyncio
import json
import os
import re
import tempfile
import weakref
from collections.abc import Iterator, Mapping
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Literal, Self

from google.adk.events import Event
from google.genai import types
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    FieldSerializationInfo,
    PrivateAttr,
    field_serializer,
    field_validator,
    model_validator,
)

from magi_agent.adk_bridge.runner_adapter import (
    OpenMagiRunnerAdapter,
    RunnerTurnInput,
)


AllowedShadowFixtureSource = Literal[
    "golden_fixture",
    "redacted_ts_bundle",
    "synthetic_local",
]

_SHADOW_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
)
_LOCAL_ADK_RUNNER_FLAG = "CORE_AGENT_PYTHON_LOCAL_ADK_RUNNER"
_REPORT_COMPARISON_METADATA_SNAPSHOTS: dict[int, tuple[Mapping[str, object], bool]] = {}
_REPORT_TRUSTED_BUNDLE_KIND_SNAPSHOTS: dict[int, bool] = {}
_REPORT_OUTPUT_SNAPSHOTS: dict[int, Mapping[str, object]] = {}
_SECRET_SHAPED_RE = re.compile(
    r"(?:"
    r"\bBearer\b|"
    r"\bCookie\b|"
    r"\bAuthorization(?:\s*[:=]\s*|\s+)Basic\b|"
    r"\bBasic\s+[a-z0-9+/=]{8,}|"
    r"github_pat_|"
    r"gh[opusr]_|"
    r"AIza[0-9A-Za-z_-]{10,}|"
    r"eyJ[A-Za-z0-9_-]{5,}\.eyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}|"
    r"(?:^|[\s:=,;'\"`({\[])sk-[a-z0-9][a-z0-9_-]{7,}(?![a-z0-9_-])|"
    r"(?:^|[\s:=,;'\"`({\[])(?:sk_live_|rk_live_|whsec_)[a-z0-9]{8,}(?![a-z0-9])|"
    r"xox[a-z]-|"
    r"\bapi[_-]?key\s*(?::|=|\s)\s*|"
    r"\baccess[\s_-]?key\s*(?::|=|\s)\s*|"
    r"\baws[\s_-]?access[\s_-]?key[\s_-]?id\s*(?::|=|\s)\s*|"
    r"\bclient[\s_-]?secret\s*(?::|=|\s)\s*|"
    r"\brefresh[_-]?token\s*[:=]\s*|"
    r"\bauthorization[_-]?basic\s*[:=]\s*|"
    r"\bbasic[\s_-]?auth\s*(?::|=|\s)\s*|"
    r"\bprovider[\s_-]?key\s*(?::|=|\s)\s*|"
    r"\bcredentials?[\s_-]?key\s*[:=]\s*|"
    r"\bservice[\s_-]?key\s*(?::|=|\s)\s*|"
    r"\bsession[\s_-]?key\s*(?::|=|\s)\s*|"
    r"\bsession[\s_-]?key\s*[:=]\s*|"
    r"\bcredentials?\s*[:=]\s*|"
    r"\btoken\s*(?::|=|\s)\s*|"
    r"\bpassword\s*(?::|=|\s)\s*|"
    r"\bsecret\s*(?::|=|\s)\s*|"
    r"service[_-]?role|"
    r"private[_ -]?key|"
    r"begin [a-z ]*private key"
    r")",
    re.IGNORECASE,
)
_BENIGN_LOCAL_FIXTURE_PHRASES = frozenset(
    {
        "token budget",
        "secret plan",
        "api compatibility fixture",
        "database schema fixture",
    }
)
_LIVE_SURFACE_TERMS = (
    "api",
    "proxy",
    "dashboard",
    "telegram",
    "k8s",
    "deploy",
    "provisioning",
    "db",
    "database",
    "supabase",
    "production",
    "runtime_selector",
    "live_capture",
    "production_route",
    "route_attached",
    "traffic_attached",
    "canary_attached",
    "production_attached",
    "evidence_block_mode",
    "block_final_answer",
    "custom_extractor",
    "signed_external_ack",
    "signed_external_acknowledgement",
    "signed_external_ack_ingestion",
    "typescript_runtime",
    "type_script_runtime",
    "live_mission_creation",
    "child_execution",
    "workspace_mutation",
    "workspace_adoption",
    "background_resume",
    "background_run",
    "background_task_resume",
    "backgroundresume",
    "backgroundrun",
    "backgroundtaskresume",
    "scheduler_resume",
    "scheduler_run",
)
_LIVE_SURFACE_RE = re.compile(
    r"(?:^|[^a-z0-9])(?:"
    + "|".join(re.escape(term) for term in _LIVE_SURFACE_TERMS)
    + r")(?:[^a-z0-9]|$)",
    re.IGNORECASE,
)
_COMPACT_LIVE_SURFACE_TERMS = frozenset(
    term.replace("_", "") for term in _LIVE_SURFACE_TERMS
)
_COMPACT_LIVE_SURFACE_LONG_SUBTOKENS = frozenset(
    {
        "signedexternalack",
        "signedexternalacknowledgement",
        "signedexternalackingestion",
        "typescriptruntime",
        "livemissioncreation",
        "childexecution",
        "workspacemutation",
        "workspaceadoption",
        "customextractor",
        "evidenceblockmode",
        "blockfinalanswer",
        "backgroundresume",
        "backgroundrun",
        "backgroundtaskresume",
        "schedulerresume",
        "schedulerrun",
    }
)
_PRIVATE_HOST_RE = re.compile(r"\bmagi\.pro\b", re.IGNORECASE)
_URL_RE = re.compile(r"\b[a-z][a-z0-9+.-]*://", re.IGNORECASE)
_UNIX_ABSOLUTE_PATH_RE = re.compile(r"(?:^|[\s('\"`=:;,])/(?!/)\S+")
_WINDOWS_ABSOLUTE_PATH_RE = re.compile(r"(?:^|[\s('\"`=:;,])[a-zA-Z]:[\\/]\S*")
_SAFE_COMPARISON_METADATA_DEFAULTS = {
    "status": "diagnostic_only",
    "source_runtime": "TypeScript",
    "shadow_runtime": "Python ADK",
}
_TRUSTED_BUNDLE_KIND_VALUES = frozenset({"redacted_ts_capture", "redacted_ts_bundle"})
_REPORT_OWNED_COMPARISON_METADATA_KEYS = {
    "local_runner_status",
    "runner_adapter_collect_events_called",
    "projected_adk_event_ids",
    "transcript_comparisons",
    "sse_comparisons",
}
_OUTPUT_ATTACHMENT_COMPARISON_METADATA_KEYS = {
    "output_flags",
    "runtime_output",
    "user_visible",
    "production_transcript_append",
    "network",
    "network_sse",
    "route",
    "route_attached",
    "traffic",
    "traffic_attached",
    "canary",
    "canary_attached",
    "production",
    "production_attached",
    "useroutput",
    "publicoutput",
    "networkoutput",
    "routeoutput",
    "trafficoutput",
    "canaryoutput",
    "productionattachment",
}
_OUTPUT_ATTACHMENT_COMPARISON_METADATA_SUBJECT_PARTS = {
    "user",
    "public",
    "visible",
    "network",
    "route",
    "traffic",
    "canary",
    "production",
}
_OUTPUT_ATTACHMENT_COMPARISON_METADATA_CLAIM_PARTS = {
    "output",
    "visible",
    "sse",
    "transcript",
    "attached",
    "attachment",
}
_OUTPUT_ATTACHMENT_DIRECT_OUTPUT_PARTS = {"output", "outputs"}
_OUTPUT_ATTACHMENT_DIRECT_ATTACHMENT_PARTS = {"attached", "attachment"}
_COMPACT_OUTPUT_ATTACHMENT_COMPARISON_METADATA_KEYS = frozenset(
    key.replace("_", "") for key in _OUTPUT_ATTACHMENT_COMPARISON_METADATA_KEYS
) | frozenset(
    first + second
    for first in _OUTPUT_ATTACHMENT_COMPARISON_METADATA_SUBJECT_PARTS
    | _OUTPUT_ATTACHMENT_COMPARISON_METADATA_CLAIM_PARTS
    for second in _OUTPUT_ATTACHMENT_COMPARISON_METADATA_SUBJECT_PARTS
    | _OUTPUT_ATTACHMENT_COMPARISON_METADATA_CLAIM_PARTS
    if (
        first in _OUTPUT_ATTACHMENT_COMPARISON_METADATA_SUBJECT_PARTS
        and second in _OUTPUT_ATTACHMENT_COMPARISON_METADATA_CLAIM_PARTS
    )
    or (
        first in _OUTPUT_ATTACHMENT_COMPARISON_METADATA_CLAIM_PARTS
        and second in _OUTPUT_ATTACHMENT_COMPARISON_METADATA_SUBJECT_PARTS
    )
) | frozenset(
    first + second
    for first in _OUTPUT_ATTACHMENT_DIRECT_OUTPUT_PARTS
    | _OUTPUT_ATTACHMENT_DIRECT_ATTACHMENT_PARTS
    for second in _OUTPUT_ATTACHMENT_DIRECT_OUTPUT_PARTS
    | _OUTPUT_ATTACHMENT_DIRECT_ATTACHMENT_PARTS
    if (
        first in _OUTPUT_ATTACHMENT_DIRECT_OUTPUT_PARTS
        and second in _OUTPUT_ATTACHMENT_DIRECT_ATTACHMENT_PARTS
    )
    or (
        first in _OUTPUT_ATTACHMENT_DIRECT_ATTACHMENT_PARTS
        and second in _OUTPUT_ATTACHMENT_DIRECT_OUTPUT_PARTS
    )
)
_COMPACT_OUTPUT_ATTACHMENT_LONG_SUBTOKENS = frozenset(
    term
    for term in _COMPACT_OUTPUT_ATTACHMENT_COMPARISON_METADATA_KEYS
    if len(term) >= 8
) | frozenset(
    {
        "productiontranscriptappend",
        "outputflags",
        "runtimeoutput",
    }
)
_CREDENTIAL_COMPARISON_METADATA_KEYS = {
    "accesstoken",
    "token",
    "accesskey",
    "access_token",
    "access_key",
    "awsaccesskey",
    "awsaccesskeyid",
    "aws_access_key",
    "aws_access_key_id",
    "apikey",
    "authtoken",
    "slacktoken",
    "refreshtoken",
    "providertoken",
    "credential",
    "credentials",
    "credentialkey",
    "credentialskey",
    "clientsecret",
    "refresh_token",
    "client_secret",
    "providerkey",
    "provider_key",
    "provider_token",
    "auth_token",
    "sessiontoken",
    "secret",
    "secretkey",
    "password",
    "authorization",
    "authorizationbasic",
    "basicauth",
}
_KEY_CREDENTIAL_QUALIFIERS = {
    "access",
    "api",
    "auth",
    "authorization",
    "aws",
    "client",
    "credential",
    "credentials",
    "private",
    "provider",
    "secret",
    "service",
    "session",
    "slack",
}
_STANDALONE_CREDENTIAL_KEY_PARTS = {
    "authorization",
    "credential",
    "credentials",
    "password",
    "secret",
    "token",
}


class _FrozenJsonMapping(tuple[tuple[str, object], ...], Mapping[str, object]):
    __slots__ = ()

    def __new__(cls, data: Mapping[str, object]) -> Self:
        return tuple.__new__(cls, tuple(data.items()))

    def __getitem__(self, key: str) -> object:
        for stored_key, stored_value in tuple.__iter__(self):
            if stored_key == key:
                return stored_value
        raise KeyError(key)

    def __iter__(self) -> Iterator[str]:
        return (key for key, _ in tuple.__iter__(self))

    def __len__(self) -> int:
        return tuple.__len__(self)

    def __eq__(self, other: object) -> bool:
        if isinstance(other, Mapping):
            return dict(tuple.__iter__(self)) == dict(other.items())
        return False

    def __repr__(self) -> str:
        return repr(dict(tuple.__iter__(self)))


class _FrozenJsonList(tuple[object, ...]):
    __slots__ = ()

    def __eq__(self, other: object) -> bool:
        if isinstance(other, list):
            return list(self) == other
        return super().__eq__(other)


def _deep_freeze_json_like(value: object) -> object:
    if isinstance(value, Mapping):
        return _FrozenJsonMapping(
            {
                key: _deep_freeze_json_like(nested_value)
                for key, nested_value in value.items()
            }
        )
    if isinstance(value, list):
        return _FrozenJsonList(_deep_freeze_json_like(item) for item in value)
    return value


def _deep_thaw_json_like(value: object) -> object:
    if isinstance(value, Mapping):
        return {
            key: _deep_thaw_json_like(nested_value)
            for key, nested_value in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_deep_thaw_json_like(item) for item in value]
    return value


def _is_credential_comparison_metadata_key(normalized_key: str) -> bool:
    if normalized_key in _CREDENTIAL_COMPARISON_METADATA_KEYS:
        return True
    parts = frozenset(part for part in normalized_key.split("_") if part)
    if parts & _STANDALONE_CREDENTIAL_KEY_PARTS:
        return True
    if "key" in parts and parts & _KEY_CREDENTIAL_QUALIFIERS:
        return True
    if "basic" in parts and parts & {"auth", "authorization"}:
        return True
    return False


def _is_output_attachment_comparison_metadata_key(normalized_key: str) -> bool:
    if normalized_key in _OUTPUT_ATTACHMENT_COMPARISON_METADATA_KEYS:
        return True
    if _has_compact_output_attachment_claim(normalized_key):
        return True
    parts = tuple(part for part in normalized_key.split("_") if part)
    if len(parts) < 2:
        return False
    part_set = frozenset(parts)
    return bool(
        (
            part_set & _OUTPUT_ATTACHMENT_DIRECT_OUTPUT_PARTS
            and part_set & _OUTPUT_ATTACHMENT_DIRECT_ATTACHMENT_PARTS
        )
        or (
            part_set & _OUTPUT_ATTACHMENT_COMPARISON_METADATA_SUBJECT_PARTS
            and part_set & _OUTPUT_ATTACHMENT_COMPARISON_METADATA_CLAIM_PARTS
        )
    )


def _is_bundle_kind_comparison_metadata_key(normalized_key: str) -> bool:
    return normalized_key.replace("_", "") == "bundlekind"


def _has_compact_output_attachment_claim(normalized_surface: str) -> bool:
    compact_surface = normalized_surface.replace("_", "")
    if compact_surface in _COMPACT_OUTPUT_ATTACHMENT_COMPARISON_METADATA_KEYS:
        return True
    parts = tuple(part for part in normalized_surface.split("_") if part)
    compact_parts = parts or (compact_surface,)
    return any(
        subtoken in part
        for part in compact_parts
        for subtoken in _COMPACT_OUTPUT_ATTACHMENT_LONG_SUBTOKENS
    )


def _reject_non_json_like_comparison_metadata(value: object) -> None:
    if value is None or isinstance(value, (str, bool)):
        return
    if isinstance(value, int) and not isinstance(value, bool):
        return
    if isinstance(value, float):
        if not (value == value and value not in (float("inf"), float("-inf"))):
            raise ValueError("comparisonMetadata must contain only JSON-compatible values")
        return
    if isinstance(value, list):
        for item in value:
            _reject_non_json_like_comparison_metadata(item)
        return
    if isinstance(value, Mapping):
        for nested_key, nested_value in value.items():
            if not isinstance(nested_key, str):
                raise ValueError("comparisonMetadata must contain only JSON-compatible keys")
            _reject_non_json_like_comparison_metadata(nested_value)
        return
    raise ValueError("comparisonMetadata must contain only JSON-compatible values")


def _reject_production_like_value(value: object) -> None:
    if isinstance(value, str):
        _reject_production_like_string(value)
        return
    if isinstance(value, Mapping):
        for nested_key, nested_value in value.items():
            _reject_production_like_value(nested_key)
            _reject_production_like_value(nested_value)
        return
    if isinstance(value, (list, tuple)):
        for item in value:
            _reject_production_like_value(item)


def _reject_reserved_comparison_metadata_claims(value: object) -> None:
    if isinstance(value, Mapping):
        for raw_key, nested_value in value.items():
            if isinstance(raw_key, str):
                normalized_key = _normalize_live_surface_string(raw_key)
                if normalized_key in _SAFE_COMPARISON_METADATA_DEFAULTS:
                    raise ValueError("comparisonMetadata must not override report-owned metadata")
                elif normalized_key in _REPORT_OWNED_COMPARISON_METADATA_KEYS:
                    raise ValueError("comparisonMetadata must not override report-owned metadata")
                elif _is_output_attachment_comparison_metadata_key(normalized_key):
                    raise ValueError("comparisonMetadata must not claim output or traffic attachment")
                elif _is_bundle_kind_comparison_metadata_key(normalized_key):
                    raise ValueError("comparisonMetadata must not declare bundleKind")
                elif _is_credential_comparison_metadata_key(normalized_key):
                    raise ValueError("comparisonMetadata must not contain credential keys")
            _reject_reserved_comparison_metadata_claims(nested_value)
        return
    if isinstance(value, (list, tuple)):
        for item in value:
            _reject_reserved_comparison_metadata_claims(item)


def _reject_report_boundary_comparison_metadata_claims(
    value: object,
    *,
    allow_report_owned_metadata: bool = False,
    allow_trusted_bundle_kind: bool = False,
    _depth: int = 0,
) -> None:
    if isinstance(value, Mapping):
        for raw_key, nested_value in value.items():
            if isinstance(raw_key, str):
                normalized_key = _normalize_live_surface_string(raw_key)
                if normalized_key in _SAFE_COMPARISON_METADATA_DEFAULTS:
                    expected_value = _SAFE_COMPARISON_METADATA_DEFAULTS[normalized_key]
                    if (
                        not allow_report_owned_metadata
                        or _depth != 0
                        or nested_value != expected_value
                    ):
                        raise ValueError(
                            "comparisonMetadata must not override report-owned metadata"
                        )
                elif _is_output_attachment_comparison_metadata_key(normalized_key):
                    raise ValueError(
                        "comparisonMetadata must not claim output or traffic attachment"
                    )
                elif _is_bundle_kind_comparison_metadata_key(normalized_key):
                    if not (
                        allow_report_owned_metadata
                        and allow_trusted_bundle_kind
                        and _depth == 0
                        and raw_key == "bundleKind"
                        and nested_value in _TRUSTED_BUNDLE_KIND_VALUES
                    ):
                        raise ValueError("comparisonMetadata must not declare bundleKind")
                elif (
                    not allow_report_owned_metadata
                    or _depth != 0
                ) and normalized_key in _REPORT_OWNED_COMPARISON_METADATA_KEYS:
                    raise ValueError("comparisonMetadata must not override report-owned metadata")
                elif _is_credential_comparison_metadata_key(normalized_key):
                    raise ValueError("comparisonMetadata must not contain credential keys")
            _reject_report_boundary_comparison_metadata_claims(
                nested_value,
                allow_report_owned_metadata=allow_report_owned_metadata,
                allow_trusted_bundle_kind=allow_trusted_bundle_kind,
                _depth=_depth + 1,
            )
        return
    if isinstance(value, (list, tuple)):
        for item in value:
            _reject_report_boundary_comparison_metadata_claims(
                item,
                allow_report_owned_metadata=allow_report_owned_metadata,
                allow_trusted_bundle_kind=allow_trusted_bundle_kind,
                _depth=_depth + 1,
            )


def _validate_trusted_report_comparison_metadata(
    value: Mapping[str, object] | None,
) -> dict[str, object]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValueError("trusted comparisonMetadata must serialize to an object")

    trusted_metadata = dict(value)
    _reject_non_json_like_comparison_metadata(trusted_metadata)
    if set(trusted_metadata) != {"bundleKind"}:
        raise ValueError("trusted comparisonMetadata may only declare bundleKind")
    if trusted_metadata["bundleKind"] not in _TRUSTED_BUNDLE_KIND_VALUES:
        raise ValueError("trusted comparisonMetadata bundleKind is not canonical")
    _reject_production_like_value(trusted_metadata)
    return trusted_metadata


def _reject_production_like_string(value: str) -> None:
    normalized = value.strip()
    lowered = normalized.lower()
    normalized_surface = _normalize_live_surface_string(normalized)
    path_parts = tuple(part for part in re.split(r"[\\/]+", normalized) if part)

    if not normalized:
        return
    if lowered in _BENIGN_LOCAL_FIXTURE_PHRASES:
        return
    if _UNIX_ABSOLUTE_PATH_RE.search(normalized) or _WINDOWS_ABSOLUTE_PATH_RE.search(normalized):
        raise ValueError("fixture content must not contain absolute paths")
    if ".." in path_parts or "/.." in normalized or "\\.." in normalized:
        raise ValueError("fixture content must not contain parent path traversal")
    if _URL_RE.search(normalized):
        raise ValueError("fixture content must not contain URLs")
    if _PRIVATE_HOST_RE.search(normalized):
        raise ValueError("fixture content must not contain production hostnames")
    if "/data" in lowered or "/workspace" in lowered:
        raise ValueError("fixture content must not contain data/workspace paths")
    if "pvc" in lowered:
        raise ValueError("fixture content must not contain PVC references")
    if "bot-" in lowered:
        raise ValueError("fixture content must not contain bot IDs")
    if _has_compact_output_attachment_claim(normalized_surface):
        raise ValueError("fixture content must not claim output or traffic attachment")
    if (
        _LIVE_SURFACE_RE.search(lowered)
        or _LIVE_SURFACE_RE.search(normalized_surface)
        or _has_compact_live_surface_term(normalized_surface)
    ):
        raise ValueError("fixture content must not contain live production surfaces")
    if _SECRET_SHAPED_RE.search(normalized):
        raise ValueError("fixture content must not contain credential-shaped strings")


def _normalize_live_surface_string(value: str) -> str:
    acronym_spaced = re.sub(r"(?<=[A-Z])(?=[A-Z][a-z])", "_", value)
    camel_spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", acronym_spaced)
    return re.sub(r"[^a-z0-9]+", "_", camel_spaced.lower()).strip("_")


def _has_compact_live_surface_term(normalized_surface: str) -> bool:
    return any(
        part in _COMPACT_LIVE_SURFACE_TERMS
        or any(subtoken in part for subtoken in _COMPACT_LIVE_SURFACE_LONG_SUBTOKENS)
        for part in normalized_surface.split("_")
        if part
    )


def _store_report_comparison_metadata_snapshot(
    report: object,
    snapshot: Mapping[str, object],
    *,
    allow_report_owned_metadata: bool,
    allow_trusted_bundle_kind: bool = False,
) -> None:
    report_id = id(report)
    _REPORT_COMPARISON_METADATA_SNAPSHOTS[report_id] = (
        snapshot,
        allow_report_owned_metadata,
    )
    if allow_trusted_bundle_kind:
        _REPORT_TRUSTED_BUNDLE_KIND_SNAPSHOTS[report_id] = True
    else:
        _REPORT_TRUSTED_BUNDLE_KIND_SNAPSHOTS.pop(report_id, None)
    weakref.finalize(
        report,
        _REPORT_COMPARISON_METADATA_SNAPSHOTS.pop,
        report_id,
        None,
    )
    weakref.finalize(
        report,
        _REPORT_TRUSTED_BUNDLE_KIND_SNAPSHOTS.pop,
        report_id,
        None,
    )


def _store_report_output_snapshot(report: "Gate2ShadowFixtureReport") -> None:
    report_id = id(report)
    _REPORT_OUTPUT_SNAPSHOTS[report_id] = {
        "source_runtime": report.source_runtime,
        "shadow_runtime": report.shadow_runtime,
        "input_source": report.input_source,
        "turn_id": report.turn_id,
        "mode": report.mode,
        "adk_primitives": tuple(report.adk_primitives),
        "custom_runtime_loop": report.custom_runtime_loop,
        "projected_adk_event_ids": tuple(report.projected_adk_event_ids),
        "transcript_refs": tuple(report.transcript_refs),
        "sse_refs": tuple(report.sse_refs),
    }
    weakref.finalize(
        report,
        _REPORT_OUTPUT_SNAPSHOTS.pop,
        report_id,
        None,
    )


def _validated_report_output_snapshot_value(field_name: str, value: object) -> object:
    if field_name == "source_runtime":
        if value != "TypeScript":
            raise ValueError("report sourceRuntime snapshot is not canonical")
        return value
    if field_name == "shadow_runtime":
        if value != "Python ADK":
            raise ValueError("report shadowRuntime snapshot is not canonical")
        return value
    if field_name == "input_source":
        if value not in {"golden_fixture", "redacted_ts_bundle", "synthetic_local"}:
            raise ValueError("report inputSource snapshot is not fixture-local")
        return value
    if field_name == "turn_id":
        if not isinstance(value, str) or not value.strip():
            raise ValueError("report turnId snapshot must be a non-empty string")
        _reject_production_like_string(value)
        return value
    if field_name == "mode":
        if value != "fixture_shadow_audit":
            raise ValueError("report mode snapshot is not canonical")
        return value
    if field_name == "adk_primitives":
        if isinstance(value, str) or not isinstance(value, (list, tuple)):
            raise ValueError("report adkPrimitives snapshot must be a sequence")
        if tuple(value) != ("Agent", "Runner", "Event"):
            raise ValueError("report adkPrimitives snapshot is not canonical")
        return tuple(value)
    if field_name == "custom_runtime_loop":
        if value is not False:
            raise ValueError("report customRuntimeLoop snapshot is not canonical")
        return value
    if field_name in {"projected_adk_event_ids", "transcript_refs", "sse_refs"}:
        if isinstance(value, str) or not isinstance(value, (list, tuple)):
            raise ValueError("report refs snapshot must be a sequence")
        items = tuple(value)
        if any(not isinstance(item, str) or not item.strip() for item in items):
            raise ValueError("report refs snapshot must contain non-empty strings")
        _reject_production_like_value(items)
        return items
    raise TypeError("report canonical output snapshot contains an unknown field")


def _canonical_report_output_value(
    report: "Gate2ShadowFixtureReport",
    field_name: str,
    *,
    mode: str,
) -> object:
    snapshot = _REPORT_OUTPUT_SNAPSHOTS.get(id(report))
    if snapshot is None:
        raise TypeError("report canonical output snapshot is unavailable")
    if field_name not in snapshot:
        raise TypeError("report canonical output snapshot is incomplete")
    canonical_value = _validated_report_output_snapshot_value(
        field_name,
        snapshot[field_name],
    )
    if mode == "json":
        return _deep_thaw_json_like(canonical_value)
    return canonical_value


class _ShadowFixtureModel(BaseModel):
    model_config = _SHADOW_MODEL_CONFIG

    def model_copy(
        self,
        *,
        update: Mapping[str, Any] | None = None,
        deep: bool = False,
    ) -> Self:
        data = self.model_dump(by_alias=False, mode="python", warnings=False)
        if update:
            alias_to_name = {
                field.alias: name
                for name, field in self.__class__.model_fields.items()
                if field.alias is not None
            }
            data.update({alias_to_name.get(key, key): value for key, value in update.items()})
        return self.__class__.model_validate(data)


class Gate2ShadowOutputFlags(_ShadowFixtureModel):
    user_visible: Literal[False] = Field(default=False, alias="userVisible")
    production_transcript_append: Literal[False] = Field(
        default=False,
        alias="productionTranscriptAppend",
    )
    network_sse: Literal[False] = Field(default=False, alias="networkSse")
    route_attached: Literal[False] = Field(default=False, alias="routeAttached")
    traffic_attached: Literal[False] = Field(default=False, alias="trafficAttached")
    canary_attached: Literal[False] = Field(default=False, alias="canaryAttached")
    production_attached: Literal[False] = Field(default=False, alias="productionAttached")

    @model_validator(mode="before")
    @classmethod
    def _reject_non_strict_false_values(cls, value: object) -> object:
        if not isinstance(value, Mapping):
            return value
        for flag_value in value.values():
            if flag_value is not False:
                raise ValueError("Gate 2 output flags must be JSON false")
        return value

    @field_serializer(
        "user_visible",
        "production_transcript_append",
        "network_sse",
        "route_attached",
        "traffic_attached",
        "canary_attached",
        "production_attached",
    )
    def _serialize_canonical_false_flags(self, value: object) -> bool:
        return False


def _reject_raw_output_flag_claims(value: object) -> None:
    if isinstance(value, Gate2ShadowOutputFlags):
        raw_model_state = getattr(value, "__dict__", {})
        if not isinstance(raw_model_state, Mapping):
            raise ValueError("Gate 2 output flags raw state must be a mapping")
        field_names = set(Gate2ShadowOutputFlags.model_fields)
        for raw_key, raw_value in raw_model_state.items():
            if raw_key not in field_names:
                raise ValueError("Gate 2 output flags must not contain raw extra state")
            if raw_value is not False:
                raise ValueError("Gate 2 output flags must be JSON false")
        raw_extra = getattr(value, "__pydantic_extra__", None)
        if raw_extra is not None:
            if not isinstance(raw_extra, Mapping):
                raise ValueError("Gate 2 output flags raw extra state must be a mapping")
            raw_extra_items = tuple(raw_extra.items())
            for _raw_key, raw_value in raw_extra_items:
                if raw_value is not False:
                    raise ValueError("Gate 2 output flags must be JSON false")
            raise ValueError("Gate 2 output flags must not contain raw extra state")
        return
    if isinstance(value, Mapping):
        for raw_value in value.values():
            if raw_value is not False:
                raise ValueError("Gate 2 output flags must be JSON false")


def _is_raw_output_flag_key(raw_key: object) -> bool:
    if not isinstance(raw_key, str):
        return False
    normalized_key = _normalize_live_surface_string(raw_key)
    return normalized_key in {"output_flag_claims", "output_flags"} or (
        normalized_key.replace("_", "") in {"outputflagclaims", "outputflags"}
    )


def _reject_raw_fixture_input_state(fixture: "Gate2ShadowFixtureInput") -> None:
    raw_model_state = getattr(fixture, "__dict__", {})
    if not isinstance(raw_model_state, Mapping):
        raise ValueError("Gate 2 fixture input raw state must be a mapping")

    field_names = set(Gate2ShadowFixtureInput.model_fields)
    for raw_key, raw_value in raw_model_state.items():
        if _is_raw_output_flag_key(raw_key):
            _reject_raw_output_flag_claims(raw_value)
        if raw_key not in field_names:
            raise ValueError("Gate 2 fixture input must not contain raw extra state")

    raw_extra = getattr(fixture, "__pydantic_extra__", None)
    if raw_extra is not None:
        if not isinstance(raw_extra, Mapping):
            raise ValueError("Gate 2 fixture input raw extra state must be a mapping")
        raw_extra_items = tuple(raw_extra.items())
        for raw_key, raw_value in raw_extra_items:
            if _is_raw_output_flag_key(raw_key):
                _reject_raw_output_flag_claims(raw_value)
        if raw_extra_items:
            raise ValueError("Gate 2 fixture input must not contain raw extra state")


class Gate2TextProjectedAdkEvent(_ShadowFixtureModel):
    """Text-only Gate 2 slice 1 projected ADK Event fixture payload."""

    id: str
    author: str
    role: str
    text: str
    partial: bool = False
    turn_complete: bool = Field(default=False, alias="turnComplete")
    invocation_id: str = Field(alias="invocationId")
    timestamp: int | float | None = None

    @model_validator(mode="after")
    def _reject_production_like_fixture_event(self) -> Self:
        _reject_production_like_value(self.model_dump(mode="python", by_alias=True))
        return self

    def to_adk_event(self) -> Event:
        kwargs: dict[str, object] = {
            "id": self.id,
            "author": self.author,
            "content": types.Content(
                role=self.role,
                parts=[types.Part(text=self.text)],
            ),
            "partial": self.partial,
            "invocation_id": self.invocation_id,
        }
        if self.turn_complete:
            kwargs["turn_complete"] = True
        if self.timestamp is not None:
            kwargs["timestamp"] = self.timestamp
        return Event(**kwargs)


class Gate2ShadowFixtureInput(_ShadowFixtureModel):
    source: AllowedShadowFixtureSource
    turn_id: str = Field(alias="turnId")
    user_prompt: str = Field(alias="userPrompt")
    projected_adk_event_ids: tuple[str, ...] = Field(default=(), alias="projectedAdkEventIds")
    projected_adk_events: tuple[Gate2TextProjectedAdkEvent, ...] = Field(
        default=(),
        alias="projectedAdkEvents",
    )
    projection_turn_id: str | None = Field(default=None, alias="projectionTurnId")
    transcript_refs: tuple[str, ...] = Field(default=(), alias="transcriptRefs")
    sse_refs: tuple[str, ...] = Field(default=(), alias="sseRefs")
    comparison_metadata: dict[str, object] = Field(
        default_factory=dict,
        alias="comparisonMetadata",
    )
    output_flag_claims: Gate2ShadowOutputFlags = Field(
        default_factory=Gate2ShadowOutputFlags,
        alias="outputFlags",
    )

    @field_validator("output_flag_claims", mode="before")
    @classmethod
    def _reject_raw_output_flag_claim_payload(cls, value: object) -> object:
        _reject_raw_output_flag_claims(value)
        return value

    @field_validator("turn_id", "user_prompt")
    @classmethod
    def _reject_empty_strings(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("fixture turn fields must be non-empty")
        _reject_production_like_string(value)
        return value

    @field_validator("projected_adk_event_ids", "transcript_refs", "sse_refs")
    @classmethod
    def _reject_empty_ref_items(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not item.strip() for item in value):
            raise ValueError("fixture refs must contain non-empty strings")
        _reject_production_like_value(value)
        return value

    @field_validator("projection_turn_id")
    @classmethod
    def _reject_unsafe_projection_turn_id(cls, value: str | None) -> str | None:
        if value is not None:
            if not value.strip():
                raise ValueError("projection turn id must be non-empty when present")
            _reject_production_like_string(value)
        return value

    @model_validator(mode="after")
    def _reject_production_like_metadata(self) -> Self:
        _reject_non_json_like_comparison_metadata(self.comparison_metadata)
        _reject_reserved_comparison_metadata_claims(self.comparison_metadata)
        _reject_production_like_value(self.comparison_metadata)
        _reject_raw_output_flag_claims(self.output_flag_claims)
        _reject_production_like_value(self.output_flag_claims)
        return self


class Gate2ShadowFixtureReport(_ShadowFixtureModel):
    _canonical_comparison_metadata: Mapping[str, object] = PrivateAttr()
    _canonical_output_flags: Gate2ShadowOutputFlags = PrivateAttr()

    source_runtime: Literal["TypeScript"] = Field(default="TypeScript", alias="sourceRuntime")
    shadow_runtime: Literal["Python ADK"] = Field(default="Python ADK", alias="shadowRuntime")
    input_source: AllowedShadowFixtureSource = Field(alias="inputSource")
    turn_id: str = Field(alias="turnId")
    mode: Literal["fixture_shadow_audit"] = "fixture_shadow_audit"
    adk_primitives: tuple[Literal["Agent", "Runner", "Event"], ...] = Field(
        default=("Agent", "Runner", "Event"),
        alias="adkPrimitives",
    )
    custom_runtime_loop: Literal[False] = Field(default=False, alias="customRuntimeLoop")
    output_flags: Gate2ShadowOutputFlags = Field(
        default_factory=Gate2ShadowOutputFlags,
        alias="outputFlags",
    )
    projected_adk_event_ids: tuple[str, ...] = Field(alias="projectedAdkEventIds")
    transcript_refs: tuple[str, ...] = Field(alias="transcriptRefs")
    sse_refs: tuple[str, ...] = Field(alias="sseRefs")
    comparison_metadata: Mapping[str, object] = Field(alias="comparisonMetadata")

    @field_validator("turn_id")
    @classmethod
    def _reject_empty_or_production_like_turn_id(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("fixture turn fields must be non-empty")
        _reject_production_like_string(value)
        return value

    @field_validator("projected_adk_event_ids", "transcript_refs", "sse_refs")
    @classmethod
    def _reject_empty_or_production_like_report_refs(
        cls,
        value: tuple[str, ...],
    ) -> tuple[str, ...]:
        if any(not item.strip() for item in value):
            raise ValueError("fixture refs must contain non-empty strings")
        _reject_production_like_value(value)
        return value

    @field_validator("adk_primitives")
    @classmethod
    def _reject_non_canonical_adk_primitives(
        cls,
        value: tuple[Literal["Agent", "Runner", "Event"], ...],
    ) -> tuple[Literal["Agent", "Runner", "Event"], ...]:
        if value != ("Agent", "Runner", "Event"):
            raise ValueError("adkPrimitives must be exactly Agent, Runner, Event")
        return value

    @field_validator("comparison_metadata")
    @classmethod
    def _validate_and_freeze_report_metadata(
        cls,
        value: Mapping[str, object],
    ) -> Mapping[str, object]:
        _reject_non_json_like_comparison_metadata(value)
        _reject_report_boundary_comparison_metadata_claims(
            value,
            allow_report_owned_metadata=False,
        )
        _reject_production_like_value(value)
        return _deep_freeze_json_like(value)

    @model_validator(mode="after")
    def _store_canonical_report_owned_output(self) -> Self:
        snapshot = _deep_freeze_json_like(_deep_thaw_json_like(self.comparison_metadata))
        allow_report_owned_metadata = False
        self._canonical_comparison_metadata = snapshot
        self._canonical_output_flags = Gate2ShadowOutputFlags()
        _store_report_comparison_metadata_snapshot(
            self,
            snapshot,
            allow_report_owned_metadata=allow_report_owned_metadata,
        )
        _store_report_output_snapshot(self)
        return self

    @field_serializer(
        "source_runtime",
        "shadow_runtime",
        "input_source",
        "turn_id",
        "mode",
        "adk_primitives",
        "custom_runtime_loop",
        "projected_adk_event_ids",
        "transcript_refs",
        "sse_refs",
    )
    def _serialize_canonical_report_field(
        self,
        value: object,
        info: FieldSerializationInfo,
    ) -> object:
        return _canonical_report_output_value(
            self,
            str(info.field_name),
            mode=info.mode,
        )

    @field_serializer("comparison_metadata")
    def _serialize_comparison_metadata(
        self,
        value: Mapping[str, object],
    ) -> dict[str, object]:
        snapshot_record = _REPORT_COMPARISON_METADATA_SNAPSHOTS.get(id(self))
        if snapshot_record is None:
            raise TypeError("comparisonMetadata canonical snapshot is unavailable")
        canonical_value, allow_report_owned_metadata = snapshot_record
        thawed = _deep_thaw_json_like(canonical_value)
        if not isinstance(thawed, dict):
            raise TypeError("comparisonMetadata must serialize to an object")
        _reject_non_json_like_comparison_metadata(thawed)
        _reject_report_boundary_comparison_metadata_claims(
            thawed,
            allow_report_owned_metadata=allow_report_owned_metadata,
            allow_trusted_bundle_kind=_REPORT_TRUSTED_BUNDLE_KIND_SNAPSHOTS.get(
                id(self),
                False,
            ),
        )
        _reject_production_like_value(thawed)
        return thawed

    @field_serializer("output_flags")
    def _serialize_output_flags(
        self,
        value: Gate2ShadowOutputFlags,
        info: FieldSerializationInfo,
    ) -> dict[str, bool]:
        return Gate2ShadowOutputFlags().model_dump(
            by_alias=bool(info.by_alias),
            mode=info.mode,
            warnings=False,
        )


def _resolve_gate2_shadow_fixture_path(
    path: str | Path,
    *,
    fixture_root: str | Path | None,
) -> Path:
    fixture_path = Path(path)
    if fixture_root is None:
        return fixture_path

    resolved_root = Path(fixture_root).resolve(strict=True)
    candidate = fixture_path
    if not candidate.is_absolute():
        candidate = resolved_root / candidate
    resolved_candidate = candidate.resolve(strict=True)
    if not resolved_candidate.is_relative_to(resolved_root):
        raise ValueError("fixture path must stay under fixture_root")
    return resolved_candidate


def load_gate2_shadow_fixture(
    path: str | Path,
    *,
    fixture_root: str | Path | None = None,
) -> Gate2ShadowFixtureInput:
    fixture_path = _resolve_gate2_shadow_fixture_path(path, fixture_root=fixture_root)
    with fixture_path.open("r", encoding="utf-8") as fixture_file:
        payload = json.load(fixture_file)
    return Gate2ShadowFixtureInput.model_validate(payload)


def run_gate2_shadow_fixture(
    fixture: Gate2ShadowFixtureInput,
    *,
    base_fixture_dir: str | Path | None = None,
    _trusted_comparison_metadata: Mapping[str, object] | None = None,
) -> Gate2ShadowFixtureReport:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        pass
    else:
        raise RuntimeError(
            "run_gate2_shadow_fixture cannot be called from an active event loop; "
            "use run_gate2_shadow_fixture_async instead"
        )

    return asyncio.run(
        run_gate2_shadow_fixture_async(
            fixture,
            base_fixture_dir=base_fixture_dir,
            _trusted_comparison_metadata=_trusted_comparison_metadata,
        )
    )


async def run_gate2_shadow_fixture_async(
    fixture: Gate2ShadowFixtureInput,
    *,
    base_fixture_dir: str | Path | None = None,
    _trusted_comparison_metadata: Mapping[str, object] | None = None,
) -> Gate2ShadowFixtureReport:
    fixture = _validated_fixture_input_snapshot(fixture)
    _validate_fixture_projection_consistency(fixture, base_fixture_dir=base_fixture_dir)

    comparison_metadata = dict(fixture.comparison_metadata)
    comparison_metadata.setdefault("status", "diagnostic_only")
    comparison_metadata.setdefault("sourceRuntime", "TypeScript")
    comparison_metadata.setdefault("shadowRuntime", "Python ADK")
    comparison_metadata["localRunnerStatus"] = await _collect_local_runner_status_async(fixture)
    comparison_metadata["runnerAdapterCollectEventsCalled"] = True

    projected_events = tuple(event.to_adk_event() for event in fixture.projected_adk_events)
    projected_event_ids = tuple(event.id for event in projected_events)
    if fixture.projected_adk_event_ids and projected_event_ids != fixture.projected_adk_event_ids:
        raise AssertionError(
            f"projected ADK event ids mismatch: {projected_event_ids!r} != {fixture.projected_adk_event_ids!r}"
        )
    comparison_metadata["projectedAdkEventIds"] = list(projected_event_ids)

    if projected_events and base_fixture_dir is not None:
        transcript_body, sse_body = _project_fixture_events(
            projected_events,
            turn_id=fixture.projection_turn_id or fixture.turn_id,
        )
        transcript_comparisons = _compare_expected_outputs(
            base_fixture_dir=Path(base_fixture_dir),
            refs=fixture.transcript_refs,
            actual=transcript_body,
            label="transcript",
        )
        sse_comparisons = _compare_expected_outputs(
            base_fixture_dir=Path(base_fixture_dir),
            refs=fixture.sse_refs,
            actual=sse_body,
            label="sse",
        )
        comparison_metadata["transcriptComparisons"] = transcript_comparisons
        comparison_metadata["sseComparisons"] = sse_comparisons

    return _build_runner_generated_gate2_shadow_fixture_report(
        {
            "inputSource": fixture.source,
            "turnId": fixture.turn_id,
            "outputFlags": Gate2ShadowOutputFlags(),
            "projectedAdkEventIds": projected_event_ids,
            "transcriptRefs": fixture.transcript_refs,
            "sseRefs": fixture.sse_refs,
            "comparisonMetadata": comparison_metadata,
        },
        trusted_comparison_metadata=_trusted_comparison_metadata,
    )


def _build_runner_generated_gate2_shadow_fixture_report(
    payload: Mapping[str, object],
    *,
    trusted_comparison_metadata: Mapping[str, object] | None = None,
) -> Gate2ShadowFixtureReport:
    comparison_metadata = payload.get("comparisonMetadata", {})
    if not isinstance(comparison_metadata, Mapping):
        raise ValueError("comparisonMetadata must serialize to an object")
    _reject_non_json_like_comparison_metadata(comparison_metadata)
    _reject_report_boundary_comparison_metadata_claims(
        comparison_metadata,
        allow_report_owned_metadata=True,
    )
    _reject_production_like_value(comparison_metadata)
    trusted_metadata = _validate_trusted_report_comparison_metadata(
        trusted_comparison_metadata,
    )

    validation_payload = dict(payload)
    validation_payload["comparisonMetadata"] = {}
    report = Gate2ShadowFixtureReport.model_validate(validation_payload)
    canonical_metadata_payload = dict(comparison_metadata)
    canonical_metadata_payload.update(trusted_metadata)
    canonical_metadata = _deep_freeze_json_like(
        _deep_thaw_json_like(canonical_metadata_payload)
    )
    object.__setattr__(report, "comparison_metadata", canonical_metadata)
    object.__setattr__(report, "_canonical_comparison_metadata", canonical_metadata)
    _store_report_comparison_metadata_snapshot(
        report,
        canonical_metadata,
        allow_report_owned_metadata=True,
        allow_trusted_bundle_kind=bool(trusted_metadata),
    )
    _store_report_output_snapshot(report)
    return report


def _validated_fixture_input_snapshot(
    fixture: Gate2ShadowFixtureInput,
) -> Gate2ShadowFixtureInput:
    _reject_raw_fixture_input_state(fixture)
    _reject_raw_output_flag_claims(fixture.output_flag_claims)
    return Gate2ShadowFixtureInput.model_validate(
        fixture.model_dump(by_alias=True, mode="python", warnings=False)
    )


def _validate_fixture_projection_consistency(
    fixture: Gate2ShadowFixtureInput,
    *,
    base_fixture_dir: str | Path | None,
) -> None:
    if fixture.projected_adk_event_ids and not fixture.projected_adk_events:
        raise ValueError(
            "projectedAdkEventIds requires projectedAdkEvents so actual projected ids can be verified"
        )
    if (fixture.transcript_refs or fixture.sse_refs) and not fixture.projected_adk_events:
        raise ValueError("transcriptRefs/sseRefs require projectedAdkEvents for comparison")
    if (fixture.transcript_refs or fixture.sse_refs) and base_fixture_dir is None:
        raise ValueError("base_fixture_dir is required when transcriptRefs or sseRefs are declared")


async def _collect_local_runner_status_async(fixture: Gate2ShadowFixtureInput) -> str:
    from magi_agent.adk_bridge.local_runner import (
        LocalAdkRunnerExecutionBlocked,
        build_local_adk_runner,
    )

    original_flag = os.environ.get(_LOCAL_ADK_RUNNER_FLAG)
    os.environ[_LOCAL_ADK_RUNNER_FLAG] = "1"
    try:
        bundle = build_local_adk_runner()
    finally:
        if original_flag is None:
            os.environ.pop(_LOCAL_ADK_RUNNER_FLAG, None)
        else:
            os.environ[_LOCAL_ADK_RUNNER_FLAG] = original_flag

    adapter = OpenMagiRunnerAdapter(runner=bundle.runner)
    turn_input = RunnerTurnInput(
        user_id="gate2-shadow-user",
        session_id="gate2-shadow-session",
        turn_id=fixture.turn_id,
        invocation_id=fixture.turn_id,
        new_message=types.Content(
            role="user",
            parts=[types.Part(text=fixture.user_prompt)],
        ),
        harness_state=_Gate2ShadowHarnessState(run_on="main", spawn_depth=0),
    )
    try:
        return await _collect_local_runner_events(bundle, adapter, turn_input)
    except LocalAdkRunnerExecutionBlocked:
        return "provider_blocked"


class _Gate2ShadowHarnessState(SimpleNamespace):
    run_on: Literal["main"]
    spawn_depth: Literal[0]


async def _collect_local_runner_events(
    bundle: object,
    adapter: object,
    turn_input: object,
) -> str:
    await bundle.session_service.create_session(
        app_name=bundle.runner.app_name,
        user_id=turn_input.user_id,
        session_id=turn_input.session_id,
    )
    events = await adapter.collect_events(turn_input)
    return f"completed:{len(events)}"


def _project_fixture_events(events: tuple[Event, ...], *, turn_id: str) -> tuple[str, str]:
    from magi_agent.adk_bridge.event_adapter import OpenMagiEventBridge
    from magi_agent.runtime.transcript import TranscriptStore
    from magi_agent.transport.sse import InMemorySseWriter

    bridge = OpenMagiEventBridge()
    writer = InMemorySseWriter()
    writer.start()
    with tempfile.TemporaryDirectory(prefix="gate2-shadow-fixture-") as temp_dir:
        transcript = TranscriptStore(file_path=Path(temp_dir) / "shadow.jsonl")
        for event in events:
            projection = bridge.project_adk_event(event, turn_id=turn_id)
            for agent_event in projection.agent_events:
                writer.agent(agent_event)
            for delta in projection.legacy_deltas:
                writer.legacy_delta(delta)
            for entry in projection.transcript_entries:
                transcript.append(entry)
        writer.legacy_finish()
        transcript_body = transcript.file_path.read_text(encoding="utf-8")
    return transcript_body, writer.body


def _compare_expected_outputs(
    *,
    base_fixture_dir: Path,
    refs: tuple[str, ...],
    actual: str,
    label: str,
) -> dict[str, str]:
    comparisons: dict[str, str] = {}
    resolved_base = base_fixture_dir.resolve(strict=True)
    for ref in refs:
        _reject_production_like_string(ref)
        expected_path = (resolved_base / ref).resolve(strict=True)
        if not expected_path.is_relative_to(resolved_base):
            raise ValueError("fixture output refs must stay under base_fixture_dir")
        expected = expected_path.read_text(encoding="utf-8")
        if actual != expected:
            raise AssertionError(f"{label} fixture mismatch: {ref}")
        comparisons[ref] = "matched"
    return comparisons


__all__ = [
    "Gate2ShadowFixtureInput",
    "Gate2TextProjectedAdkEvent",
    "Gate2ProjectedAdkEvent",
    "Gate2ShadowFixtureReport",
    "Gate2ShadowOutputFlags",
    "load_gate2_shadow_fixture",
    "run_gate2_shadow_fixture",
    "run_gate2_shadow_fixture_async",
]


Gate2ProjectedAdkEvent = Gate2TextProjectedAdkEvent
