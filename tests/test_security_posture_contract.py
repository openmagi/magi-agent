from __future__ import annotations

import pytest

from openmagi_core_agent.security.posture import (
    SecurityControl,
    SecurityPostureRequest,
    evaluate_security_posture,
)


_SENSITIVE_SHAPED_CONTROL = "sk" + "-proj-secret-token"


def test_in_process_controls_are_not_reported_as_boundaries() -> None:
    decision = evaluate_security_posture(
        SecurityPostureRequest(
            controls=(
                SecurityControl(name="approval-gate", kind="approval", enforced=True),
                SecurityControl(name="redaction", kind="redaction", enforced=True),
                SecurityControl(name="context-scan", kind="scanner", enforced=True),
                SecurityControl(name="tool-allowlist", kind="tool_allowlist", enforced=True),
            ),
            untrusted_inputs=("web", "mcp", "messaging"),
        )
    )

    assert decision.boundary_class == "no_os_boundary"
    assert decision.production_ready is False
    assert decision.reason_codes == (
        "in_process_controls_are_heuristics",
        "untrusted_inputs_require_whole_process_isolation",
        "no_os_level_isolation",
    )
    assert decision.public_projection()["controls"][0]["boundaryClass"] == "heuristic"


def test_terminal_backend_is_shell_boundary_but_not_plugin_boundary() -> None:
    decision = evaluate_security_posture(
        SecurityPostureRequest(
            controls=(
                SecurityControl(
                    name="docker-terminal",
                    kind="terminal_backend_isolation",
                    enforced=True,
                ),
            ),
            untrusted_inputs=("web",),
            plugin_loading_enabled=True,
            mcp_enabled=True,
        )
    )

    assert decision.boundary_class == "terminal_backend_only"
    assert decision.production_ready is False
    assert "terminal_backend_does_not_confine_agent_process" in decision.reason_codes
    assert "whole_process_required_for_plugins_or_mcp" in decision.reason_codes


def test_whole_process_isolation_with_auth_and_credentials_can_be_boundary_ready() -> None:
    decision = evaluate_security_posture(
        SecurityPostureRequest(
            controls=(
                SecurityControl(
                    name="openshell-or-container",
                    kind="whole_process_isolation",
                    enforced=True,
                ),
                SecurityControl(name="network-policy", kind="network_policy", enforced=True),
                SecurityControl(
                    name="credential-broker",
                    kind="credential_broker",
                    enforced=True,
                ),
                SecurityControl(
                    name="external-allowlist",
                    kind="external_allowlist",
                    enforced=True,
                ),
            ),
            untrusted_inputs=("web", "messaging"),
            plugin_loading_enabled=False,
            mcp_enabled=False,
        )
    )

    assert decision.boundary_class == "whole_process_boundary"
    assert decision.production_ready is True
    assert decision.reason_codes == ("whole_process_boundary_ready",)


def test_control_names_must_be_public_safe_identifiers() -> None:
    with pytest.raises(ValueError, match="security control name must be public-safe"):
        SecurityControl(name="/private/tmp/raw-secret-path", kind="scanner", enforced=True)


def test_public_projection_sanitizes_names_even_if_validation_is_bypassed() -> None:
    control = SecurityControl.model_construct(
        name=f"/private/tmp/{_SENSITIVE_SHAPED_CONTROL}",
        kind="scanner",
        enforced=True,
    )
    decision = evaluate_security_posture(
        SecurityPostureRequest.model_construct(
            controls=(control,),
            untrusted_inputs=("web",),
            plugin_loading_enabled=False,
            mcp_enabled=False,
        )
    )

    dumped = repr(decision.public_projection())

    assert "/private" not in dumped
    assert _SENSITIVE_SHAPED_CONTROL not in dumped
    assert decision.public_projection()["controls"][0]["name"] == "redacted"


def test_public_projection_clamps_all_fields_when_validation_is_bypassed() -> None:
    control = SecurityControl.model_construct(
        name="safe-control",
        kind="/Users/kevin/.env",
        enforced="Bearer raw-token",
    )
    decision = evaluate_security_posture(
        SecurityPostureRequest.model_construct(
            controls=(control,),
            untrusted_inputs=("/private/tmp/raw-prompt",),
            plugin_loading_enabled=False,
            mcp_enabled=False,
        )
    ).model_copy(
        update={
            "boundary_class": "/private/tmp/raw-boundary",
            "reason_codes": (_SENSITIVE_SHAPED_CONTROL, "/Users/kevin/.env"),
        }
    )

    projection = decision.public_projection()
    dumped = repr(projection)

    assert "/private" not in dumped
    assert "/Users" not in dumped
    assert "Bearer" not in dumped
    assert _SENSITIVE_SHAPED_CONTROL not in dumped
    assert projection["boundaryClass"] == "invalid"
    assert projection["reasonCodes"] == ["redacted"]
    assert projection["controls"][0]["kind"] == "unknown"
    assert projection["controls"][0]["enforced"] is False
    assert projection["controls"][0]["boundaryClass"] == "heuristic"


def test_public_projection_redacts_common_credential_shapes() -> None:
    credential_like_values = (
        "".join(("sk", "_live_", "a" * 32)),
        "".join(("gh", "p_", "a" * 32)),
        "".join(("xo", "xb-", "1" * 12, "-", "2" * 12, "-", "a" * 24)),
        "".join(("AK", "IA", "A" * 16)),
        "".join(("api", "_key_", "a" * 24)),
    )

    for value in credential_like_values:
        control = SecurityControl.model_construct(
            name=value,
            kind="scanner",
            enforced=True,
        )
        decision = evaluate_security_posture(
            SecurityPostureRequest.model_construct(
                controls=(control,),
                untrusted_inputs=(),
                plugin_loading_enabled=False,
                mcp_enabled=False,
            )
        ).model_copy(update={"reason_codes": (value.lower(),)})
        dumped = repr(decision.public_projection())

        assert value not in dumped
        assert value.lower() not in dumped
        assert decision.public_projection()["controls"][0]["name"] == "redacted"
        assert decision.public_projection()["reasonCodes"] == ["redacted"]


def test_bypassed_non_bool_enforced_does_not_make_boundary_ready() -> None:
    decision = evaluate_security_posture(
        SecurityPostureRequest.model_construct(
            controls=(
                SecurityControl.model_construct(
                    name="whole-process",
                    kind="whole_process_isolation",
                    enforced="yes",
                ),
                SecurityControl.model_construct(
                    name="network-policy",
                    kind="network_policy",
                    enforced="yes",
                ),
                SecurityControl.model_construct(
                    name="credential-broker",
                    kind="credential_broker",
                    enforced="yes",
                ),
                SecurityControl.model_construct(
                    name="external-allowlist",
                    kind="external_allowlist",
                    enforced="yes",
                ),
            ),
            untrusted_inputs=(),
            plugin_loading_enabled=False,
            mcp_enabled=False,
        )
    )

    assert decision.boundary_class == "no_os_boundary"
    assert decision.production_ready is False
    assert "no_os_level_isolation" in decision.reason_codes


def test_whole_process_boundary_requires_external_allowlist_even_without_inputs() -> None:
    decision = evaluate_security_posture(
        SecurityPostureRequest(
            controls=(
                SecurityControl(
                    name="whole-process",
                    kind="whole_process_isolation",
                    enforced=True,
                ),
                SecurityControl(name="network-policy", kind="network_policy", enforced=True),
                SecurityControl(
                    name="credential-broker",
                    kind="credential_broker",
                    enforced=True,
                ),
            ),
        )
    )

    assert decision.boundary_class == "whole_process_boundary"
    assert decision.production_ready is False
    assert "external_allowlist_missing" in decision.reason_codes
