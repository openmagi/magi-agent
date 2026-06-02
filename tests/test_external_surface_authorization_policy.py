from __future__ import annotations

from magi_agent.security.external_surface import (
    ExternalSurfacePolicy,
    ExternalSurfaceRequest,
    evaluate_external_surface,
)


def test_network_surface_without_allowlist_fails_closed() -> None:
    decision = evaluate_external_surface(
        ExternalSurfaceRequest(
            surface="telegram",
            transport="messaging",
            caller_id="123",
            session_id="session-a",
            action="dispatch_work",
        ),
        ExternalSurfacePolicy(enabled=True, allowed_callers=()),
    )

    assert decision.allowed is False
    assert decision.reason_codes == ("network_surface_requires_allowlist",)
    assert decision.public_projection()["sessionIdAuthorized"] is False


def test_disabled_policy_fails_closed_by_default() -> None:
    decision = evaluate_external_surface(
        ExternalSurfaceRequest(
            surface="api",
            transport="http",
            caller_id="owner",
            action="dispatch_work",
        ),
        ExternalSurfacePolicy(),
    )

    assert decision.allowed is False
    assert decision.reason_codes == ("external_surface_policy_disabled",)


def test_session_id_does_not_authorize_caller() -> None:
    decision = evaluate_external_surface(
        ExternalSurfaceRequest(
            surface="api",
            transport="http",
            caller_id="intruder",
            session_id="known-session-id",
            action="relay_output",
        ),
        ExternalSurfacePolicy(enabled=True, allowed_callers=("owner",)),
    )

    assert decision.allowed is False
    assert decision.reason_codes == (
        "caller_not_allowlisted",
        "session_id_is_not_authorization",
    )


def test_action_must_be_explicitly_allowed_for_surface() -> None:
    decision = evaluate_external_surface(
        ExternalSurfaceRequest(
            surface="api",
            transport="http",
            caller_id="owner",
            action="relay_output",
        ),
        ExternalSurfacePolicy(
            enabled=True,
            allowed_callers=("owner",),
            allowed_actions=("dispatch_work",),
        ),
    )

    assert decision.allowed is False
    assert decision.reason_codes == ("action_not_allowed_for_surface",)


def test_allowlisted_network_caller_is_allowed_for_configured_actions() -> None:
    decision = evaluate_external_surface(
        ExternalSurfaceRequest(
            surface="telegram",
            transport="messaging",
            caller_id="owner",
            session_id="session-a",
            action="resolve_approval",
        ),
        ExternalSurfacePolicy(
            enabled=True,
            allowed_callers=("owner",),
            allowed_actions=("dispatch_work", "resolve_approval", "relay_output"),
        ),
    )

    assert decision.allowed is True
    assert decision.reason_codes == ("caller_allowlisted",)
    assert decision.public_projection()["allowed"] is True


def test_local_ipc_requires_loopback_or_os_boundary() -> None:
    decision = evaluate_external_surface(
        ExternalSurfaceRequest(
            surface="dashboard",
            transport="local_http",
            caller_id="local-user",
            bind_host="0.0.0.0",
            action="dispatch_work",
        ),
        ExternalSurfacePolicy(enabled=True, local_os_boundary=True),
    )

    assert decision.allowed is False
    assert decision.reason_codes == ("local_surface_bound_to_non_loopback",)


def test_local_loopback_still_requires_os_boundary() -> None:
    decision = evaluate_external_surface(
        ExternalSurfaceRequest(
            surface="dashboard",
            transport="local_http",
            caller_id="local-user",
            bind_host="127.0.0.1",
            action="dispatch_work",
        ),
        ExternalSurfacePolicy(enabled=True, local_os_boundary=False),
    )

    assert decision.allowed is False
    assert decision.reason_codes == ("local_surface_requires_os_boundary",)


def test_public_projection_clamps_bypassed_raw_values() -> None:
    credential_shape = "sk-" + "proj-" + "not-real-" + "token"
    request = ExternalSurfaceRequest.model_construct(
        surface="/private/tmp/raw-surface",
        transport="/Users/kevin/.env",
        caller_id="raw-caller-secret",
        action="Bearer raw-token",
        session_id="/private/session-id",
        bind_host="0.0.0.0",
    )
    decision = evaluate_external_surface(
        request,
        ExternalSurfacePolicy.model_construct(
            enabled=True,
            allowed_callers=("raw-caller-secret",),
            allowed_actions=("Bearer raw-token",),
            local_os_boundary=True,
        ),
    ).model_copy(update={"reason_codes": (credential_shape,)})

    projection = decision.public_projection()
    dumped = repr(projection)

    assert "/private" not in dumped
    assert "/Users" not in dumped
    assert "Bearer" not in dumped
    assert "raw-caller-secret" not in dumped
    assert credential_shape not in dumped
    assert decision.allowed is False
    assert projection["surface"] == "redacted"
    assert projection["transport"] == "unknown"
    assert projection["action"] == "unknown"
    assert projection["allowed"] is False
    assert projection["reasonCodes"] == ["redacted"]
    assert projection["sessionIdAuthorized"] is False


def test_public_projection_does_not_publish_forged_allowed_state() -> None:
    request = ExternalSurfaceRequest.model_construct(
        surface="/private/tmp/raw-surface",
        transport="Bearer raw-token",
        caller_id="owner",
        action="dispatch_work",
        session_id=None,
        bind_host="127.0.0.1",
    )
    decision = evaluate_external_surface(
        request,
        ExternalSurfacePolicy.model_construct(
            enabled=True,
            allowed_callers=("owner",),
            allowed_actions=("dispatch_work",),
            local_os_boundary=True,
        ),
    ).model_copy(
        update={
            "allowed": True,
            "reason_codes": ("caller_allowlisted",),
        },
    )

    projection = decision.public_projection()

    assert decision.allowed is True
    assert projection["allowed"] is False
    assert projection["surface"] == "redacted"
    assert projection["transport"] == "unknown"


def test_public_projection_does_not_publish_allowed_with_redacted_reason() -> None:
    decision = evaluate_external_surface(
        ExternalSurfaceRequest(
            surface="api",
            transport="http",
            caller_id="owner",
            action="dispatch_work",
        ),
        ExternalSurfacePolicy(
            enabled=True,
            allowed_callers=("owner",),
            allowed_actions=("dispatch_work",),
        ),
    ).model_copy(
        update={
            "allowed": True,
            "reason_codes": ("caller_allowlisted", "/private/path"),
        },
    )

    projection = decision.public_projection()

    assert decision.allowed is True
    assert projection["allowed"] is False
    assert projection["reasonCodes"] == ["caller_allowlisted", "redacted"]


def test_raw_hostname_surface_fails_closed_and_is_redacted() -> None:
    decision = evaluate_external_surface(
        ExternalSurfaceRequest(
            surface="api.example.com",
            transport="http",
            caller_id="owner",
            action="dispatch_work",
        ),
        ExternalSurfacePolicy(
            enabled=True,
            allowed_callers=("owner",),
            allowed_actions=("dispatch_work",),
        ),
    )

    projection = decision.public_projection()
    dumped = repr(projection)

    assert decision.allowed is False
    assert decision.reason_codes == ("invalid_surface",)
    assert "api.example.com" not in dumped
    assert projection["surface"] == "redacted"
    assert projection["allowed"] is False


def test_bypassed_truthy_enabled_does_not_authorize_surface() -> None:
    decision = evaluate_external_surface(
        ExternalSurfaceRequest(
            surface="api",
            transport="http",
            caller_id="owner",
            action="dispatch_work",
        ),
        ExternalSurfacePolicy.model_construct(
            enabled="yes",
            allowed_callers=("owner",),
            allowed_actions=("dispatch_work",),
            local_os_boundary=False,
        ),
    )

    assert decision.allowed is False
    assert decision.reason_codes == ("external_surface_policy_disabled",)


def test_bypassed_truthy_local_os_boundary_does_not_authorize_local_surface() -> None:
    decision = evaluate_external_surface(
        ExternalSurfaceRequest(
            surface="dashboard",
            transport="local_http",
            caller_id="local-user",
            bind_host="127.0.0.1",
            action="dispatch_work",
        ),
        ExternalSurfacePolicy.model_construct(
            enabled=True,
            allowed_callers=(),
            allowed_actions=("dispatch_work",),
            local_os_boundary="yes",
        ),
    )

    assert decision.allowed is False
    assert decision.reason_codes == ("local_surface_requires_os_boundary",)
