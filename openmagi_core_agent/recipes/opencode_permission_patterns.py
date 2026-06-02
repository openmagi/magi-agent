from __future__ import annotations

from fnmatch import fnmatchcase
import re
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from openmagi_core_agent.runtime.control import ControlRequestStore


OpenCodeResearchPermission = Literal[
    "read.workspace",
    "read.external_directory",
    "read.external_repo",
    "web.search",
    "web.fetch",
    "repo.clone",
    "repo.overview",
    "task.spawn.research",
    "task.background.research",
]
OpenCodeResearchPermissionAction = Literal["allow", "deny", "ask"]
OpenCodeResearchPermissionStatus = Literal[
    "disabled",
    "allowed",
    "denied",
    "approval_required",
]

_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    revalidate_instances="never",
    hide_input_in_errors=True,
)
_ACTIVATION_GATE = "policy-fixture-only-hard-deny-preserved"
_SUPPORTED_PERMISSIONS = frozenset(OpenCodeResearchPermission.__args__)
_PUBLIC_PATTERN_RE = r"^[A-Za-z0-9._:/@*?-]+$"
_GLOB_RESOURCE_REF_RE = re.compile(r"[*?\[\]]")
_MANAGED_RESOURCE_REF_RE = re.compile(
    r"^(?:"
    r"repo:[A-Za-z0-9][A-Za-z0-9_.-]{0,63}/[A-Za-z0-9][A-Za-z0-9_.-]{0,127}"
    r"|(?:docs|web|workspace|task|external-repo|external-dir):"
    r"[A-Za-z0-9][A-Za-z0-9_.:@-]{0,127}"
    r")$"
)
_UNSAFE_RESOURCE_REF_RE = re.compile(
    r"(?:^/|://|callback|session|token|secret|cookie|authorization|bearer|"
    r"code=|apikey|api_key)",
    re.IGNORECASE,
)
_UNSAFE_RESOURCE_MARKER_RE = re.compile(
    r"(?:^|[._:/-])(?:\.env|\.ssh|auth|key|keys|credential|credentials|"
    r"config|private|password|passwd|secret|token|cookie|session|callback|"
    r"authorization|bearer)"
    r"(?:$|[._:/-])",
    re.IGNORECASE,
)
_NORMALIZED_UNSAFE_RESOURCE_MARKERS = frozenset(
    {
        "apikey",
        "auth",
        "authorization",
        "bearer",
        "callback",
        "config",
        "cred",
        "creds",
        "cookie",
        "credential",
        "credentials",
        "env",
        "idrsa",
        "key",
        "keys",
        "passwd",
        "password",
        "pem",
        "private",
        "rsa",
        "secret",
        "session",
        "ssh",
        "token",
    }
)
_BROAD_PRODUCTION_PERMISSION_PATTERNS = frozenset(
    {
        "*",
        "repo.*",
        "web.*",
        "task.*",
        "task.*.research",
    }
)


class _OpenCodeResearchPermissionModel(BaseModel):
    model_config = _MODEL_CONFIG

    @classmethod
    def model_construct(
        cls,
        _fields_set: set[str] | None = None,
        **values: object,
    ) -> Self:
        _ = _fields_set
        return cls(**values)

    def model_copy(
        self,
        *,
        update: dict[str, object] | None = None,
        deep: bool = False,
    ) -> Self:
        _ = deep
        data = self.model_dump(by_alias=False, mode="python", warnings=False)
        if update:
            alias_to_name = {
                field.alias: name
                for name, field in self.__class__.model_fields.items()
                if field.alias is not None
            }
            data.update({alias_to_name.get(key, key): value for key, value in update.items()})
        return type(self).model_validate(data)


class OpenCodeResearchPermissionRule(_OpenCodeResearchPermissionModel):
    permission_pattern: str = Field(alias="permissionPattern")
    resource_pattern: str = Field(alias="resourcePattern")
    action: OpenCodeResearchPermissionAction
    reason_code: str = Field(alias="reasonCode")
    scope: Literal["soft_rule", "always_approval"] = "soft_rule"
    production_rule: Literal[False] = Field(default=False, alias="productionRule")

    @field_validator("permission_pattern")
    @classmethod
    def _validate_permission_pattern(cls, value: str) -> str:
        return _validate_permission_pattern(value)

    @field_validator("resource_pattern")
    @classmethod
    def _validate_resource_pattern(cls, value: str) -> str:
        return _validate_public_pattern(value, "resourcePattern")

    @field_validator("reason_code")
    @classmethod
    def _validate_reason_code(cls, value: str) -> str:
        return _validate_public_token(value, "reasonCode")


class OpenCodeResearchHardDeny(_OpenCodeResearchPermissionModel):
    permission_pattern: str = Field(alias="permissionPattern")
    resource_pattern: str = Field(alias="resourcePattern")
    reason_code: str = Field(alias="reasonCode")

    @field_validator("permission_pattern")
    @classmethod
    def _validate_permission_pattern(cls, value: str) -> str:
        return _validate_permission_pattern(value)

    @field_validator("resource_pattern")
    @classmethod
    def _validate_resource_pattern(cls, value: str) -> str:
        return _validate_public_pattern(value, "resourcePattern")

    @field_validator("reason_code")
    @classmethod
    def _validate_reason_code(cls, value: str) -> str:
        return _validate_public_token(value, "reasonCode")


class OpenCodeResearchPermissionRequest(_OpenCodeResearchPermissionModel):
    request_id: str = Field(alias="requestId")
    session_key: str = Field(alias="sessionKey")
    permission: OpenCodeResearchPermission
    resource_ref: str = Field(alias="resourceRef")

    @field_validator("request_id", "session_key")
    @classmethod
    def _validate_public_ref(cls, value: str) -> str:
        return _validate_public_pattern(value, "request")

    @field_validator("resource_ref")
    @classmethod
    def _validate_resource_ref(cls, value: str) -> str:
        return _validate_resource_ref(value)


class OpenCodeResearchPermissionProfile(_OpenCodeResearchPermissionModel):
    enabled: bool = False
    soft_rules: tuple[OpenCodeResearchPermissionRule, ...] = Field(
        default=(),
        alias="softRules",
    )
    hard_denies: tuple[OpenCodeResearchHardDeny, ...] = Field(
        default=(),
        alias="hardDenies",
    )
    activation_gate: Literal["policy-fixture-only-hard-deny-preserved"] = Field(
        default=_ACTIVATION_GATE,
        alias="activationGate",
    )
    default_off: Literal[True] = Field(default=True, alias="defaultOff")
    local_only: Literal[True] = Field(default=True, alias="localOnly")
    fixture_only: Literal[True] = Field(default=True, alias="fixtureOnly")
    live_authority_allowed: Literal[False] = Field(
        default=False,
        alias="liveAuthorityAllowed",
    )
    production_broad_authority_allowed: Literal[False] = Field(
        default=False,
        alias="productionBroadAuthorityAllowed",
    )
    adk_policy_metadata_only: Literal[True] = Field(
        default=True,
        alias="adkPolicyMetadataOnly",
    )

    @model_validator(mode="after")
    def _validate_profile_shape(self) -> Self:
        broad = tuple(_broad_production_grant(rule) for rule in self.soft_rules)
        if any(item is not None for item in broad):
            raise ValueError("production permission profile cannot include broad authority grants")
        return self

    def public_projection(self) -> dict[str, object]:
        return {
            "enabled": self.enabled,
            "activationGate": self.activation_gate,
            "defaultOff": self.default_off,
            "localOnly": self.local_only,
            "fixtureOnly": self.fixture_only,
            "liveAuthorityAllowed": self.live_authority_allowed,
            "productionBroadAuthorityAllowed": self.production_broad_authority_allowed,
            "adkPolicyMetadataOnly": self.adk_policy_metadata_only,
            "permissionTaxonomy": tuple(sorted(_SUPPORTED_PERMISSIONS)),
            "softRuleCount": len(self.soft_rules),
            "hardDenyCount": len(self.hard_denies),
            "productionRuleBroadGrants": [
                item
                for rule in self.soft_rules
                if (item := _broad_production_grant(rule)) is not None
            ],
        }


class OpenCodeResearchPermissionDecision(_OpenCodeResearchPermissionModel):
    status: OpenCodeResearchPermissionStatus
    action: OpenCodeResearchPermissionAction
    permission: OpenCodeResearchPermission
    resource_ref: str = Field(alias="resourceRef")
    reason_codes: tuple[str, ...] = Field(alias="reasonCodes")
    matched_rule_index: int | None = Field(default=None, alias="matchedRuleIndex")
    hard_deny_applied: bool = Field(default=False, alias="hardDenyApplied")
    activation_gate: Literal["policy-fixture-only-hard-deny-preserved"] = Field(
        default=_ACTIVATION_GATE,
        alias="activationGate",
    )
    default_off: Literal[True] = Field(default=True, alias="defaultOff")
    local_only: Literal[True] = Field(default=True, alias="localOnly")
    fixture_only: Literal[True] = Field(default=True, alias="fixtureOnly")
    live_authority_allowed: Literal[False] = Field(
        default=False,
        alias="liveAuthorityAllowed",
    )

    @field_validator("resource_ref")
    @classmethod
    def _validate_resource_ref(cls, value: str) -> str:
        return _validate_resource_ref(value)

    @model_validator(mode="after")
    def _validate_decision_shape(self) -> Self:
        if not self.reason_codes:
            raise ValueError("reasonCodes must be non-empty")
        if self.status == "allowed" and self.action != "allow":
            raise ValueError("allowed status requires allow action")
        if self.status == "denied" and self.action != "deny":
            raise ValueError("denied status requires deny action")
        if self.status == "approval_required" and self.action != "ask":
            raise ValueError("approval_required status requires ask action")
        return self


class OpenCodeResearchPermissionRejectResult(_OpenCodeResearchPermissionModel):
    rejected_request_id: str = Field(alias="rejectedRequestId")
    cancelled_request_ids: tuple[str, ...] = Field(alias="cancelledRequestIds")
    activation_gate: Literal["policy-fixture-only-hard-deny-preserved"] = Field(
        default=_ACTIVATION_GATE,
        alias="activationGate",
    )
    local_only: Literal[True] = Field(default=True, alias="localOnly")
    fixture_only: Literal[True] = Field(default=True, alias="fixtureOnly")
    live_authority_allowed: Literal[False] = Field(
        default=False,
        alias="liveAuthorityAllowed",
    )


def build_default_opencode_research_permission_profile() -> OpenCodeResearchPermissionProfile:
    return OpenCodeResearchPermissionProfile(
        enabled=False,
        hardDenies=(
            OpenCodeResearchHardDeny(
                permissionPattern="repo.clone",
                resourcePattern="file:*",
                reasonCode="local_file_clone_hard_denied",
            ),
            OpenCodeResearchHardDeny(
                permissionPattern="read.external_directory",
                resourcePattern="/Users/*",
                reasonCode="private_host_path_hard_denied",
            ),
        ),
    )


def decide_opencode_research_permission(
    request: OpenCodeResearchPermissionRequest,
    profile: OpenCodeResearchPermissionProfile | None = None,
) -> OpenCodeResearchPermissionDecision:
    source_profile = profile or build_default_opencode_research_permission_profile()
    parsed_profile = OpenCodeResearchPermissionProfile.model_validate(
        source_profile.model_dump(by_alias=True, mode="python", warnings=False)
    )
    parsed_request = OpenCodeResearchPermissionRequest.model_validate(
        request.model_dump(by_alias=True, mode="python", warnings=False)
    )
    if not parsed_profile.enabled:
        return _decision(
            status="disabled",
            action="deny",
            request=parsed_request,
            reason_codes=("permission_profile_disabled",),
        )

    for index, hard_deny in enumerate(parsed_profile.hard_denies):
        if _matches(hard_deny.permission_pattern, parsed_request.permission) and _matches(
            hard_deny.resource_pattern,
            parsed_request.resource_ref,
        ):
            return _decision(
                status="denied",
                action="deny",
                request=parsed_request,
                reason_codes=(hard_deny.reason_code,),
                matched_rule_index=index,
                hard_deny_applied=True,
            )

    matched: tuple[int, OpenCodeResearchPermissionRule] | None = None
    for index, rule in enumerate(parsed_profile.soft_rules):
        if _matches(rule.permission_pattern, parsed_request.permission) and _matches(
            rule.resource_pattern,
            parsed_request.resource_ref,
        ):
            matched = (index, rule)
    if matched is None:
        return _decision(
            status="approval_required",
            action="ask",
            request=parsed_request,
            reason_codes=("approval_required",),
        )

    matched_index, matched_rule = matched
    return _decision(
        status=_status_for_action(matched_rule.action),
        action=matched_rule.action,
        request=parsed_request,
        reason_codes=(matched_rule.reason_code,),
        matched_rule_index=matched_index,
    )


def add_opencode_research_always_approval(
    profile: OpenCodeResearchPermissionProfile,
    request: OpenCodeResearchPermissionRequest,
) -> OpenCodeResearchPermissionProfile:
    parsed_profile = OpenCodeResearchPermissionProfile.model_validate(
        profile.model_dump(by_alias=True, mode="python", warnings=False)
    )
    parsed_request = OpenCodeResearchPermissionRequest.model_validate(
        request.model_dump(by_alias=True, mode="python", warnings=False)
    )
    scoped_rule = OpenCodeResearchPermissionRule(
        permissionPattern=parsed_request.permission,
        resourcePattern=parsed_request.resource_ref,
        action="allow",
        reasonCode="always_approval_scoped_allow",
        scope="always_approval",
    )
    return parsed_profile.model_copy(
        update={"soft_rules": (*parsed_profile.soft_rules, scoped_rule)}
    )


def reject_opencode_research_permission_request(
    store: ControlRequestStore,
    request_id: str,
    *,
    now: int | float,
) -> OpenCodeResearchPermissionRejectResult:
    pending = store.get_pending(request_id)
    if pending is None:
        raise KeyError(f"unknown pending OpenCode research permission request: {request_id}")
    session_key = pending.session_key
    store.resolve_request(request_id, decision="denied", now=now)

    cancelled: list[str] = []
    for sibling in tuple(store.pending_requests):
        if sibling.session_key != session_key:
            continue
        store.cancel_request(
            sibling.request_id,
            reason="opencode_research_permission_rejected_sibling_cancelled",
            now=now,
        )
        cancelled.append(sibling.request_id)
    return OpenCodeResearchPermissionRejectResult(
        rejectedRequestId=request_id,
        cancelledRequestIds=tuple(cancelled),
    )


def _decision(
    *,
    status: OpenCodeResearchPermissionStatus,
    action: OpenCodeResearchPermissionAction,
    request: OpenCodeResearchPermissionRequest,
    reason_codes: tuple[str, ...],
    matched_rule_index: int | None = None,
    hard_deny_applied: bool = False,
) -> OpenCodeResearchPermissionDecision:
    return OpenCodeResearchPermissionDecision(
        status=status,
        action=action,
        permission=request.permission,
        resourceRef=request.resource_ref,
        reasonCodes=reason_codes,
        matchedRuleIndex=matched_rule_index,
        hardDenyApplied=hard_deny_applied,
    )


def _status_for_action(
    action: OpenCodeResearchPermissionAction,
) -> OpenCodeResearchPermissionStatus:
    if action == "allow":
        return "allowed"
    if action == "deny":
        return "denied"
    return "approval_required"


def _matches(pattern: str, value: str) -> bool:
    return fnmatchcase(value, pattern)


def _validate_permission_pattern(value: str) -> str:
    pattern = _validate_public_pattern(value, "permissionPattern")
    if "*" not in pattern and pattern not in _SUPPORTED_PERMISSIONS:
        raise ValueError("permissionPattern must be a known OpenCode research permission")
    prefix = pattern.split("*", 1)[0].rstrip(".")
    if prefix and not any(permission.startswith(prefix) for permission in _SUPPORTED_PERMISSIONS):
        raise ValueError("permissionPattern must target the OpenCode research taxonomy")
    return pattern


def _validate_public_pattern(value: str, field_name: str) -> str:
    clean = value.strip()
    if not clean:
        raise ValueError(f"{field_name} must be non-empty")
    if len(clean) > 160:
        raise ValueError(f"{field_name} must be at most 160 characters")
    if re.fullmatch(_PUBLIC_PATTERN_RE, clean) is None:
        raise ValueError(f"{field_name} contains unsafe characters")
    return clean


def _validate_resource_ref(value: str) -> str:
    clean = _validate_public_pattern(value, "resourceRef")
    if _GLOB_RESOURCE_REF_RE.search(clean) is not None:
        raise ValueError("resourceRef must be a literal managed ref, not a glob pattern")
    if ".." in clean or _MANAGED_RESOURCE_REF_RE.fullmatch(clean) is None:
        raise ValueError("resourceRef must use a managed OpenCode research ref scheme")
    if _UNSAFE_RESOURCE_REF_RE.search(clean) is not None:
        raise ValueError("resourceRef must not contain raw paths, URLs, or auth material")
    if _UNSAFE_RESOURCE_MARKER_RE.search(clean) is not None:
        raise ValueError("resourceRef must not contain private or credential markers")
    normalized = re.sub(r"[^a-z0-9]", "", clean.casefold())
    if any(marker in normalized for marker in _NORMALIZED_UNSAFE_RESOURCE_MARKERS):
        raise ValueError("resourceRef must not contain private or credential markers")
    return clean


def _validate_public_token(value: str, field_name: str) -> str:
    clean = value.strip()
    if not clean:
        raise ValueError(f"{field_name} must be non-empty")
    if not clean.replace("_", "").replace("-", "").isalnum():
        raise ValueError(f"{field_name} must be a public token")
    return clean


def _broad_production_grant(rule: OpenCodeResearchPermissionRule) -> str | None:
    if not rule.production_rule or rule.action != "allow":
        return None
    if (
        rule.permission_pattern in _BROAD_PRODUCTION_PERMISSION_PATTERNS
        or rule.resource_pattern == "*"
    ):
        return f"{rule.permission_pattern}:{rule.resource_pattern}"
    return None


__all__ = [
    "OpenCodeResearchHardDeny",
    "OpenCodeResearchPermissionDecision",
    "OpenCodeResearchPermissionProfile",
    "OpenCodeResearchPermissionRequest",
    "OpenCodeResearchPermissionRule",
    "OpenCodeResearchPermissionRejectResult",
    "add_opencode_research_always_approval",
    "build_default_opencode_research_permission_profile",
    "decide_opencode_research_permission",
    "reject_opencode_research_permission_request",
]
