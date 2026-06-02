from __future__ import annotations

import pytest
from pydantic import ValidationError

from magi_agent.security.advisory import (
    Advisory,
    LazyDependencyDecision,
    LazyDependencyPolicy,
    LazyDependencyRequest,
    check_installed_advisories,
    evaluate_lazy_dependency_request,
)


def test_advisory_check_flags_known_bad_package_versions() -> None:
    findings = check_installed_advisories(
        {"mistralai": "2.4.6", "safe-package": "1.0.0"},
        advisories=(
            Advisory(
                advisory_id="adv-mistralai-246",
                package="mistralai",
                affected_versions=("2.4.6",),
                severity="high",
                remediation=("upgrade mistralai", "rebuild runtime image"),
            ),
        ),
    )

    assert tuple(finding.advisory_id for finding in findings) == (
        "adv-mistralai-246",
    )
    assert findings[0].public_projection()["installedVersion"] == "2.4.6"


def test_lazy_dependency_policy_rejects_unallowlisted_feature() -> None:
    decision = evaluate_lazy_dependency_request(
        LazyDependencyRequest(feature="voice.tts", spec="elevenlabs>=1,<2"),
        LazyDependencyPolicy(
            allow_lazy_installs=True,
            allowed_specs={"web.fetch": ("httpx>=0.27,<1",)},
        ),
    )

    assert decision.allowed is False
    assert decision.reason_codes == ("lazy_dependency_feature_not_allowlisted",)


def test_lazy_dependency_policy_rejects_non_pypi_specs() -> None:
    decision = evaluate_lazy_dependency_request(
        LazyDependencyRequest(
            feature="web.fetch",
            spec="git+https://example.test/pkg.git",
        ),
        LazyDependencyPolicy(
            allow_lazy_installs=True,
            allowed_specs={"web.fetch": ("httpx>=0.27,<1",)},
        ),
    )

    assert decision.allowed is False
    assert decision.reason_codes == (
        "lazy_dependency_spec_not_allowlisted",
        "lazy_dependency_spec_not_pypi_name",
    )


def test_lazy_dependency_policy_allows_exact_allowlisted_spec() -> None:
    decision = evaluate_lazy_dependency_request(
        LazyDependencyRequest(feature="web.fetch", spec="httpx>=0.27,<1"),
        LazyDependencyPolicy(
            allow_lazy_installs=True,
            allowed_specs={"web.fetch": ("httpx>=0.27,<1",)},
        ),
    )

    assert decision.allowed is True
    assert decision.reason_codes == ("lazy_dependency_allowed",)
    assert decision.public_projection()["spec"] == "httpx>=0.27,<1"


def test_allowlisted_token_named_pypi_package_is_not_rejected_by_substring() -> None:
    decision = evaluate_lazy_dependency_request(
        LazyDependencyRequest(feature="nlp.tokenizers", spec="tokenizers>=0.15,<1"),
        LazyDependencyPolicy(
            allow_lazy_installs=True,
            allowed_specs={"nlp.tokenizers": ("tokenizers>=0.15,<1",)},
        ),
    )

    assert decision.allowed is True
    assert decision.reason_codes == ("lazy_dependency_allowed",)


def test_lazy_dependency_policy_is_disabled_by_default() -> None:
    decision = evaluate_lazy_dependency_request(
        LazyDependencyRequest(feature="web.fetch", spec="httpx>=0.27,<1"),
        LazyDependencyPolicy(allowed_specs={"web.fetch": ("httpx>=0.27,<1",)}),
    )

    assert decision.allowed is False
    assert decision.reason_codes == ("lazy_dependency_installs_disabled",)


def test_rejected_lazy_dependency_projection_omits_raw_spec() -> None:
    decision = evaluate_lazy_dependency_request(
        LazyDependencyRequest(
            feature="web.fetch",
            spec="git+https://example.test/pkg.git?credential=redacted",
        ),
        LazyDependencyPolicy(
            allow_lazy_installs=True,
            allowed_specs={"web.fetch": ("httpx>=0.27,<1",)},
        ),
    )

    projection = decision.public_projection()
    dumped = repr(projection)

    assert projection["allowed"] is False
    assert "git+https" not in dumped
    assert "credential" not in dumped
    assert "redacted" not in dumped


def test_rejected_lazy_dependency_decision_omits_raw_spec_from_model_dump() -> None:
    decision = evaluate_lazy_dependency_request(
        LazyDependencyRequest(
            feature="web.fetch",
            spec="git+https://example.test/pkg.git?credential=redacted",
        ),
        LazyDependencyPolicy(
            allow_lazy_installs=True,
            allowed_specs={"web.fetch": ("httpx>=0.27,<1",)},
        ),
    )

    dumped = repr(decision.model_dump())

    assert "git+https" not in dumped
    assert "credential" not in dumped
    assert "redacted" not in dumped


def test_advisory_projection_redacts_unsafe_remediation_text() -> None:
    findings = check_installed_advisories(
        {"mistralai": "2.4.6"},
        advisories=(
            Advisory(
                advisory_id="adv-mistralai-246",
                package="mistralai",
                affected_versions=("2.4.6",),
                severity="high",
                remediation=(
                    "upgrade mistralai",
                    "read /Users/kevin/.ssh/id_rsa before rebuild",
                ),
            ),
        ),
    )

    projection = findings[0].public_projection()
    dumped = repr(projection)

    assert "/Users" not in dumped
    assert "id_rsa" not in dumped
    assert projection["remediation"] == ["upgrade mistralai", "redacted"]


def test_public_projection_rejects_forged_lazy_dependency_allowed_state() -> None:
    decision = LazyDependencyDecision.model_construct(
        allowed=True,
        reason_codes=("lazy_dependency_allowed",),
    )

    projection = decision.public_projection()

    assert projection["allowed"] is False
    assert "spec" not in projection


def test_lazy_dependency_policy_rejects_boolean_coercion() -> None:
    with pytest.raises(ValidationError):
        LazyDependencyPolicy(allow_lazy_installs="true")


def test_lazy_dependency_policy_rejects_raw_specs_in_allowlist() -> None:
    with pytest.raises(ValueError) as excinfo:
        LazyDependencyPolicy(
            allow_lazy_installs=True,
            allowed_specs={
                "web.fetch": (
                    "git+https://example.test/pkg.git?credential=redacted",
                ),
            },
        )
    dumped = str(excinfo.value)

    assert "git+https" not in dumped
    assert "credential" not in dumped
    assert "redacted" not in dumped
