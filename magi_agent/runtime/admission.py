from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping, Sequence
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator


RuntimeAdmissionStatus = Literal["compiled", "staged", "ready", "active", "disabled"]

_DIGEST_PREFIX = "sha256:"
_MODEL_CONFIG = ConfigDict(frozen=True, populate_by_name=True, extra="forbid")
_COMPILED_SNAPSHOT_KIND = "compiled_snapshot"
_SUPPORTED_POLICY_VERSIONS = ("runtime-admission/v1",)
_DEFAULT_ALLOWED_STATUSES = ("compiled", "staged", "ready")
_DEFAULT_FORBIDDEN_TOOLS = (
    "runtime.activation",
    "runtime.credentials",
    "runtime.model",
    "runtime.network",
)
_UNBOUNDED_TOOL_MARKERS = ("*", "all", "any", "tool:*", "tools:*")


class _Blocker(Protocol):
    def __call__(
        self,
        code: str,
        *,
        path: str | None = None,
        ref: str | None = None,
    ) -> None: ...


class RuntimeAdmissionRequest(BaseModel):
    model_config = _MODEL_CONFIG

    supported_policy_versions: tuple[str, ...] = Field(
        default=_SUPPORTED_POLICY_VERSIONS, alias="supportedPolicyVersions"
    )
    allowed_statuses: tuple[str, ...] = Field(
        default=_DEFAULT_ALLOWED_STATUSES, alias="allowedStatuses"
    )
    allow_active_snapshot: StrictBool = Field(
        default=False, alias="allowActiveSnapshot"
    )
    max_tool_allowlist_size: int = Field(default=64, alias="maxToolAllowlistSize")
    forbidden_tools: tuple[str, ...] = Field(
        default=_DEFAULT_FORBIDDEN_TOOLS, alias="forbiddenTools"
    )

    @field_validator(
        "supported_policy_versions",
        "allowed_statuses",
        "forbidden_tools",
        mode="before",
    )
    @classmethod
    def _normalize_strings(cls, value: object) -> tuple[str, ...]:
        values = _string_tuple(value)
        if not values:
            raise ValueError("runtime admission request tuples must not be empty")
        return values

    @field_validator("max_tool_allowlist_size")
    @classmethod
    def _validate_allowlist_size(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("maxToolAllowlistSize must be positive")
        return value


class RuntimeAdmissionIssue(BaseModel):
    model_config = _MODEL_CONFIG

    code: str
    path: str | None = None
    ref: str | None = None


class RuntimeAdmissionResult(BaseModel):
    model_config = _MODEL_CONFIG

    allowed: bool
    compiled_snapshot_digest: str | None = Field(
        default=None, alias="compiledSnapshotDigest"
    )
    policy_version: str | None = Field(default=None, alias="policyVersion")
    status: str | None = None
    reason_codes: tuple[str, ...] = Field(default=(), alias="reasonCodes")
    issues: tuple[RuntimeAdmissionIssue, ...] = ()


def runtime_admission_check(
    snapshot: Mapping[str, object],
    request: RuntimeAdmissionRequest | Mapping[str, object] | None = None,
) -> RuntimeAdmissionResult:
    admission_request = (
        request
        if isinstance(request, RuntimeAdmissionRequest)
        else RuntimeAdmissionRequest.model_validate(request or {})
    )
    issues: list[RuntimeAdmissionIssue] = []
    reason_codes: list[str] = []

    def block(code: str, *, path: str | None = None, ref: str | None = None) -> None:
        reason = f"{code}:{ref}" if ref is not None else code
        reason_codes.append(reason)
        issues.append(RuntimeAdmissionIssue(code=code, path=path, ref=ref))

    compiled_digest = _optional_string(snapshot.get("compiledSnapshotDigest"))
    snapshot_kind = _optional_string(snapshot.get("snapshotKind"))
    if snapshot_kind != _COMPILED_SNAPSHOT_KIND:
        block(
            "compiled_snapshot_kind_required",
            path="snapshotKind",
            ref=snapshot_kind or "missing",
        )

    if compiled_digest is None:
        block("compiled_snapshot_digest_missing", path="compiledSnapshotDigest")
    elif not _is_digest(compiled_digest):
        block("compiled_snapshot_digest_invalid", path="compiledSnapshotDigest")
    else:
        expected_digest = digest_compiled_snapshot_payload(snapshot)
        if compiled_digest != expected_digest:
            block("compiled_snapshot_digest_mismatch", path="compiledSnapshotDigest")

    if _has_duplicate_policy_section(snapshot, "policyVersion"):
        block(
            "policy_section_duplicate",
            path="policyVersion",
            ref="policyVersion",
        )

    policy_version = _optional_string(
        snapshot.get("policyVersion")
    ) or _section_string(snapshot, "policyVersion")
    if policy_version not in set(admission_request.supported_policy_versions):
        block(
            "unsupported_policy_version",
            path="policyVersion",
        )

    status = _optional_string(snapshot.get("status"))
    if status == "disabled":
        block("snapshot_status_disabled", path="status")
    elif status == "active":
        if not _active_snapshot_allowed(snapshot, admission_request):
            block("active_snapshot_gate_missing", path="status")
    elif status not in set(admission_request.allowed_statuses):
        block("snapshot_status_not_allowed", path="status", ref=status or "missing")

    _check_hard_invariants(snapshot, block)
    _check_projection_policy(snapshot, block)
    _check_tool_policy(snapshot, admission_request, block)
    _check_authority_flags(snapshot, block)
    _check_approval_policy(snapshot, block)

    return RuntimeAdmissionResult(
        allowed=not reason_codes,
        compiledSnapshotDigest=compiled_digest,
        policyVersion=policy_version,
        status=status,
        reasonCodes=tuple(reason_codes),
        issues=tuple(issues),
    )


def digest_compiled_snapshot_payload(snapshot: Mapping[str, object]) -> str:
    payload = dict(snapshot)
    payload.pop("compiledSnapshotDigest", None)
    return _digest_json(payload)


def _check_hard_invariants(
    snapshot: Mapping[str, object],
    block: _Blocker,
) -> None:
    if _has_duplicate_policy_section(snapshot, "hardInvariants"):
        _call_block(
            block,
            "policy_section_duplicate",
            path="hardInvariants",
            ref="hardInvariants",
        )
    invariants = _sequence(_section(snapshot, "hardInvariants"))
    if not invariants:
        _call_block(block, "hard_invariant_missing", path="hardInvariants")
        return

    for index, invariant in enumerate(invariants):
        if not isinstance(invariant, Mapping):
            _call_block(
                block,
                "hard_invariant_invalid",
                path=f"hardInvariants.{index}",
            )
            continue
        invariant_id = _optional_string(invariant.get("invariantId")) or f"index-{index}"
        if invariant.get("ok") is not True or invariant.get("mode") != "enforced":
            _call_block(
                block,
                "hard_invariant_not_enforced",
                path=f"hardInvariants.{index}",
                ref=invariant_id,
            )


def _check_projection_policy(
    snapshot: Mapping[str, object],
    block: _Blocker,
) -> None:
    if _has_duplicate_policy_section(snapshot, "projectionPolicy"):
        _call_block(
            block,
            "policy_section_duplicate",
            path="projectionPolicy",
            ref="projectionPolicy",
        )
    policy = _section(snapshot, "projectionPolicy")
    if not isinstance(policy, Mapping):
        _call_block(block, "projection_policy_missing", path="projectionPolicy")
        return

    mode = _optional_string(policy.get("mode"))
    raw_enabled = policy.get("rawGovernedProjectionEnabled") is True
    if mode == "raw_governed" or raw_enabled:
        _call_block(
            block,
            "raw_governed_projection_disabled",
            path="projectionPolicy",
        )


def _check_tool_policy(
    snapshot: Mapping[str, object],
    request: RuntimeAdmissionRequest,
    block: _Blocker,
) -> None:
    if _has_duplicate_policy_section(snapshot, "toolPolicy"):
        _call_block(
            block,
            "policy_section_duplicate",
            path="toolPolicy",
            ref="toolPolicy",
        )
    policy = _section(snapshot, "toolPolicy")
    if not isinstance(policy, Mapping):
        _call_block(block, "tool_policy_missing", path="toolPolicy")
        return

    _check_tool_ref_values(
        policy,
        ("toolAllowlist", "allowedToolRefs"),
        block,
        "toolPolicy.toolAllowlist",
    )
    _check_tool_ref_values(
        policy,
        ("forbiddenTools", "forbiddenToolRefs", "deniedToolRefs", "deniedTools"),
        block,
        "toolPolicy.forbiddenTools",
    )

    if (
        policy.get("allowAllTools") is True
        or policy.get("unboundedToolsAllowed") is True
    ):
        _call_block(block, "tool_allowlist_unbounded", path="toolPolicy")

    allowlist = _tool_refs(policy, "toolAllowlist", "allowedToolRefs")
    if not allowlist:
        _call_block(block, "tool_allowlist_missing", path="toolPolicy.toolAllowlist")
        return
    if len(allowlist) > request.max_tool_allowlist_size:
        _call_block(block, "tool_allowlist_too_large", path="toolPolicy.toolAllowlist")

    seen: set[str] = set()
    for tool_ref in allowlist:
        marker = tool_ref.strip().lower()
        if not marker:
            _call_block(block, "tool_allowlist_ref_invalid", path="toolPolicy.toolAllowlist")
            continue
        if marker in _UNBOUNDED_TOOL_MARKERS or "*" in marker:
            _call_block(
                block,
                "tool_allowlist_unbounded",
                path="toolPolicy.toolAllowlist",
                ref=tool_ref,
            )
        if tool_ref in seen:
            _call_block(
                block,
                "tool_allowlist_duplicate",
                path="toolPolicy.toolAllowlist",
                ref=tool_ref,
            )
        seen.add(tool_ref)

    forbidden = set(_DEFAULT_FORBIDDEN_TOOLS)
    forbidden.update(request.forbidden_tools)
    forbidden.update(_tool_refs(policy, "forbiddenTools", "forbiddenToolRefs"))
    forbidden.update(_tool_refs(policy, "deniedToolRefs", "deniedTools"))
    for tool_ref in allowlist:
        if tool_ref in forbidden:
            _call_block(
                block,
                "forbidden_tool_allowlisted",
                path="toolPolicy.toolAllowlist",
                ref=tool_ref,
            )


def _check_authority_flags(
    snapshot: Mapping[str, object],
    block: _Blocker,
) -> None:
    flags = snapshot.get("authorityFlags")
    if not isinstance(flags, Mapping):
        _call_block(block, "authority_flags_missing", path="authorityFlags")
        return

    for name, value in flags.items():
        flag_name = str(name)
        if value is True:
            _call_block(
                block,
                "authority_flag_forged",
                path=f"authorityFlags.{flag_name}",
                ref=flag_name,
            )
        elif value is not False:
            _call_block(
                block,
                "authority_flag_invalid",
                path=f"authorityFlags.{flag_name}",
                ref=flag_name,
            )


def _check_approval_policy(
    snapshot: Mapping[str, object],
    block: _Blocker,
) -> None:
    if _has_duplicate_policy_section(snapshot, "approvalPolicy"):
        _call_block(
            block,
            "policy_section_duplicate",
            path="approvalPolicy",
            ref="approvalPolicy",
        )
    policy = _section(snapshot, "approvalPolicy")
    if not isinstance(policy, Mapping):
        _call_block(block, "approval_policy_missing", path="approvalPolicy")
        return

    requires_approval = (
        policy.get("requiresHumanReview") is True
        or policy.get("requiresApproval") is True
    )
    bypass_enabled = (
        policy.get("approvalBypassed") is True
        or policy.get("bypassAllowed") is True
    )
    approval_required_disabled = policy.get("approvalRequired") is False
    if bypass_enabled:
        _call_block(
            block,
            "approval_bypass_flag_forbidden",
            path="approvalPolicy",
        )
    if requires_approval and approval_required_disabled:
        _call_block(
            block,
            "approval_requirement_bypassed",
            path="approvalPolicy",
        )
    if policy.get("allowAutoActivation") is True:
        _call_block(
            block,
            "approval_auto_activation_forbidden",
            path="approvalPolicy.allowAutoActivation",
        )


def _active_snapshot_allowed(
    snapshot: Mapping[str, object],
    request: RuntimeAdmissionRequest,
) -> bool:
    _ = snapshot
    return request.allow_active_snapshot


def _tool_refs(policy: Mapping[object, object], *keys: str) -> tuple[str, ...]:
    refs: list[str] = []
    for key in keys:
        value = policy.get(key)
        if isinstance(value, str):
            refs.append(value)
        elif isinstance(value, Iterable) and not isinstance(value, Mapping):
            refs.extend(item for item in value if isinstance(item, str))
    return tuple(refs)


def _check_tool_ref_values(
    policy: Mapping[object, object],
    keys: tuple[str, ...],
    block: _Blocker,
    path_prefix: str,
) -> None:
    for key in keys:
        if key not in policy:
            continue
        value = policy.get(key)
        if isinstance(value, str):
            continue
        if isinstance(value, Mapping):
            _call_block(
                block,
                "tool_ref_invalid",
                path=f"{path_prefix}.{key}",
                ref=key,
            )
            continue
        if not isinstance(value, Iterable):
            _call_block(
                block,
                "tool_ref_invalid",
                path=f"{path_prefix}.{key}",
                ref=key,
            )
            continue
        for index, item in enumerate(value):
            if not isinstance(item, str):
                _call_block(
                    block,
                    "tool_ref_invalid",
                    path=f"{path_prefix}.{key}.{index}",
                    ref=key,
                )


def _has_duplicate_policy_section(snapshot: Mapping[str, object], key: str) -> bool:
    effective_policy = snapshot.get("effectivePolicy")
    return key in snapshot and isinstance(effective_policy, Mapping) and key in effective_policy


def _section(snapshot: Mapping[str, object], key: str) -> object:
    if key in snapshot:
        return snapshot.get(key)
    effective_policy = snapshot.get("effectivePolicy")
    if isinstance(effective_policy, Mapping) and key in effective_policy:
        return effective_policy.get(key)
    return None


def _section_string(snapshot: Mapping[str, object], key: str) -> str | None:
    return _optional_string(_section(snapshot, key))


def _string_tuple(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, Iterable):
        result: list[str] = []
        for item in value:
            if not isinstance(item, str):
                return ()
            result.append(item)
        return tuple(result)
    return ()


def _sequence(value: object) -> Sequence[object]:
    if isinstance(value, Sequence) and not isinstance(value, str):
        return value
    return ()


def _optional_string(value: object) -> str | None:
    if isinstance(value, str) and value.strip():
        return value
    return None


def _is_digest(value: str) -> bool:
    suffix = value.removeprefix(_DIGEST_PREFIX)
    return (
        value.startswith(_DIGEST_PREFIX)
        and len(suffix) == 64
        and all(char in "0123456789abcdef" for char in suffix)
    )


def _digest_json(value: object) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return _DIGEST_PREFIX + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _call_block(
    block: _Blocker,
    code: str,
    *,
    path: str | None = None,
    ref: str | None = None,
) -> None:
    block(code, path=path, ref=ref)
