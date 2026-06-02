from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator


Severity = Literal["low", "medium", "high", "critical"]

_PYPI_SPEC_RE = re.compile(
    r"^[A-Za-z0-9_.-]+(?:[<>=!~]=?[^,\s]+)?(?:,[<>=!~]=?[^,\s]+)*$",
)
_PUBLIC_ID_RE = re.compile(r"^[a-z0-9_.:-]{3,160}$")
_PUBLIC_PACKAGE_RE = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")
_PUBLIC_VERSION_RE = re.compile(r"^[A-Za-z0-9_.!+*-]{1,128}$")
_PUBLIC_FEATURE_RE = re.compile(r"^[a-z][a-z0-9_.:-]{1,127}$")
_PUBLIC_REMEDIATION_RE = re.compile(r"^[A-Za-z0-9 .,;:()/_+-]{1,160}$")
_PUBLIC_REASON_CODES = {
    "lazy_dependency_allowed",
    "lazy_dependency_feature_not_allowlisted",
    "lazy_dependency_installs_disabled",
    "lazy_dependency_spec_not_allowlisted",
    "lazy_dependency_spec_not_pypi_name",
}
_SENSITIVE_FRAGMENTS = (
    ".env",
    ".netrc",
    "/users/",
    "api_key",
    "apikey",
    "auth",
    "cookie",
    "credential",
    "id_rsa",
    "private",
    "secret",
    "token",
)


class Advisory(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True, extra="forbid")

    advisory_id: str = Field(alias="advisoryId")
    package: str
    affected_versions: tuple[str, ...] = Field(alias="affectedVersions")
    severity: Severity
    remediation: tuple[str, ...]


class AdvisoryFinding(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True, extra="forbid")

    advisory_id: str = Field(alias="advisoryId")
    package: str
    installed_version: str = Field(alias="installedVersion")
    severity: Severity
    remediation: tuple[str, ...]

    def public_projection(self) -> dict[str, object]:
        return {
            "advisoryId": _public_id(self.advisory_id),
            "package": _public_package(self.package),
            "installedVersion": _public_version(self.installed_version),
            "severity": self.severity,
            "remediation": [
                _public_remediation_item(item) for item in self.remediation
            ],
        }


class LazyDependencyRequest(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True, extra="forbid")

    feature: str
    spec: str

    @field_validator("feature", "spec")
    @classmethod
    def _non_empty(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("lazy dependency request fields must be non-empty")
        return normalized


class LazyDependencyPolicy(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True, extra="forbid")

    allow_lazy_installs: StrictBool = Field(default=False, alias="allowLazyInstalls")
    allowed_specs: Mapping[str, tuple[str, ...]] = Field(
        default_factory=dict,
        alias="allowedSpecs",
    )

    def __init__(self, **data: object) -> None:
        allowed_specs = data.get("allowedSpecs", data.get("allowed_specs", {}))
        if _allowed_specs_contains_unsafe_entry(allowed_specs):
            raise ValueError(
                "allowed lazy dependency specs must be public PyPI allowlist entries",
            )
        super().__init__(**data)


class LazyDependencyDecision(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True, extra="forbid")

    allowed: bool
    reason_codes: tuple[str, ...] = Field(alias="reasonCodes")
    feature: str = "unknown"
    spec: str | None = None
    decision_digest: str = Field(default="", alias="decisionDigest")

    def public_projection(self) -> dict[str, object]:
        reason_codes = _public_reason_codes(self.reason_codes)
        feature = _public_feature(getattr(self, "feature", "unknown"))
        spec = _public_spec(getattr(self, "spec", ""))
        allowed = (
            self.allowed is True
            and feature != "unknown"
            and spec is not None
            and reason_codes == ["lazy_dependency_allowed"]
            and self.decision_digest
            == _decision_digest(
                allowed=True,
                feature=feature,
                reason_codes=("lazy_dependency_allowed",),
                spec=spec,
            )
        )
        projection: dict[str, object] = {
            "feature": feature,
            "allowed": allowed,
            "reasonCodes": reason_codes,
        }
        if allowed and spec is not None:
            projection["spec"] = spec
        return projection


def check_installed_advisories(
    installed_versions: Mapping[str, str],
    *,
    advisories: tuple[Advisory, ...],
) -> tuple[AdvisoryFinding, ...]:
    findings: list[AdvisoryFinding] = []
    normalized_versions = {
        name.casefold(): version for name, version in installed_versions.items()
    }
    for advisory in advisories:
        installed = normalized_versions.get(advisory.package.casefold())
        if installed is None or installed not in advisory.affected_versions:
            continue
        findings.append(
            AdvisoryFinding(
                advisoryId=advisory.advisory_id,
                package=advisory.package,
                installedVersion=installed,
                severity=advisory.severity,
                remediation=advisory.remediation,
            ),
        )
    return tuple(findings)


def evaluate_lazy_dependency_request(
    request: LazyDependencyRequest,
    policy: LazyDependencyPolicy,
) -> LazyDependencyDecision:
    reasons: list[str] = []
    allowed_specs = tuple(policy.allowed_specs.get(request.feature, ()))
    if not policy.allow_lazy_installs:
        reasons.append("lazy_dependency_installs_disabled")
    if not allowed_specs:
        reasons.append("lazy_dependency_feature_not_allowlisted")
    elif request.spec not in allowed_specs:
        reasons.append("lazy_dependency_spec_not_allowlisted")
    if _public_spec(request.spec) is None:
        reasons.append("lazy_dependency_spec_not_pypi_name")
    if reasons:
        return _make_decision(
            allowed=False,
            reason_codes=tuple(dict.fromkeys(reasons)),
            request=request,
        )
    return _make_decision(
        allowed=True,
        reason_codes=("lazy_dependency_allowed",),
        request=request,
    )


def _make_decision(
    *,
    allowed: bool,
    reason_codes: tuple[str, ...],
    request: LazyDependencyRequest,
) -> LazyDependencyDecision:
    public_feature = _public_feature(request.feature)
    public_spec = _public_spec(request.spec) if allowed else None
    return LazyDependencyDecision(
        allowed=allowed,
        feature=public_feature,
        reasonCodes=reason_codes,
        spec=public_spec,
        decisionDigest=_decision_digest(
            allowed=allowed,
            feature=public_feature,
            reason_codes=reason_codes,
            spec=public_spec,
        ),
    )


def _decision_digest(
    *,
    allowed: bool,
    feature: str,
    reason_codes: tuple[str, ...],
    spec: str | None,
) -> str:
    payload = {
        "allowed": allowed,
        "feature": _public_feature(feature),
        "reasonCodes": _public_reason_codes(reason_codes),
        "schema": "openmagi.lazyDependencyDecision.v1",
        "spec": _public_spec(spec or "") or "redacted",
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _public_id(value: object) -> str:
    text = str(value)
    if _PUBLIC_ID_RE.fullmatch(text):
        return text
    return "redacted"


def _public_package(value: object) -> str:
    text = str(value)
    if _PUBLIC_PACKAGE_RE.fullmatch(text):
        return text
    return "redacted"


def _public_version(value: object) -> str:
    text = str(value)
    if _PUBLIC_VERSION_RE.fullmatch(text):
        return text
    return "redacted"


def _public_feature(value: object) -> str:
    text = str(value)
    if _PUBLIC_FEATURE_RE.fullmatch(text):
        return text
    return "unknown"


def _public_spec(value: object) -> str | None:
    text = str(value)
    if (
        _PYPI_SPEC_RE.fullmatch(text)
        and _PUBLIC_PACKAGE_RE.fullmatch(_spec_package_name(text))
    ):
        return text
    return None


def _spec_package_name(spec: str) -> str:
    package = spec
    for separator in ("<", ">", "=", "!", "~"):
        package = package.split(separator, maxsplit=1)[0]
    return package.rstrip(",")


def _public_remediation_item(value: object) -> str:
    text = str(value)
    if (
        _PUBLIC_REMEDIATION_RE.fullmatch(text)
        and not _contains_sensitive_fragment(text)
    ):
        return text
    return "redacted"


def _public_reason_codes(reason_codes: object) -> list[str]:
    if not isinstance(reason_codes, tuple):
        return ["redacted"]
    public: list[str] = []
    for reason_code in reason_codes:
        value = str(reason_code)
        if value in _PUBLIC_REASON_CODES:
            public.append(value)
        else:
            public.append("redacted")
    return public or ["redacted"]


def _allowed_specs_contains_unsafe_entry(value: object) -> bool:
    if not isinstance(value, Mapping):
        return True
    for feature, specs in value.items():
        if _public_feature(feature) == "unknown":
            return True
        if isinstance(specs, str) or not isinstance(specs, (tuple, list)):
            return True
        for spec in specs:
            if _public_spec(spec) is None:
                return True
    return False


def _contains_sensitive_fragment(value: str) -> bool:
    folded = value.casefold()
    return any(fragment in folded for fragment in _SENSITIVE_FRAGMENTS)
