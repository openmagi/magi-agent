from __future__ import annotations

import pytest
from pydantic import ValidationError

from openmagi_core_agent.security.sandbox_preflight import (
    SandboxPreflightReport,
    SandboxPreflightRequest,
    evaluate_sandbox_preflight,
)


def test_local_backend_is_development_only() -> None:
    report = evaluate_sandbox_preflight(
        SandboxPreflightRequest(
            backend="local",
            untrusted_inputs=("web",),
            mode="gateway",
        ),
    )

    assert report.ready is False
    assert report.boundary_class == "no_os_boundary"
    assert "local_backend_has_no_os_isolation" in report.reason_codes
    assert "untrusted_inputs_require_whole_process_isolation" in report.reason_codes


def test_terminal_backend_does_not_cover_plugins_or_mcp() -> None:
    report = evaluate_sandbox_preflight(
        SandboxPreflightRequest(
            backend="docker_terminal",
            untrusted_inputs=("web",),
            plugin_loading_enabled=True,
            mcp_enabled=True,
            mode="gateway",
            non_root=True,
            resource_limits={"cpu": 1, "memoryMb": 2048, "pids": 256},
        ),
    )

    assert report.ready is False
    assert report.boundary_class == "terminal_backend_only"
    assert "whole_process_required_for_plugins_or_mcp" in report.reason_codes


def test_whole_process_sandbox_requires_limits_non_root_and_network_policy() -> None:
    report = evaluate_sandbox_preflight(
        SandboxPreflightRequest(
            backend="whole_process_container",
            untrusted_inputs=("web", "messaging"),
            mode="gateway",
            non_root=True,
            network_default_deny=True,
            mounted_paths=("/workspace", "/tmp/openmagi-artifacts"),
            resource_limits={"cpu": 2, "memoryMb": 4096, "pids": 256},
        ),
    )

    assert report.ready is True
    assert report.boundary_class == "whole_process_boundary"
    assert report.reason_codes == ("sandbox_preflight_ready",)
    assert report.public_projection()["resourceLimits"]["memoryMb"] == 4096


def test_root_mount_and_missing_limits_are_denied() -> None:
    report = evaluate_sandbox_preflight(
        SandboxPreflightRequest(
            backend="whole_process_container",
            mode="gateway",
            non_root=False,
            network_default_deny=False,
            mounted_paths=("/", "/workspace"),
            resource_limits={"cpu": 0},
        ),
    )

    assert report.ready is False
    assert report.reason_codes == (
        "sandbox_must_run_non_root",
        "network_default_deny_required",
        "root_mount_denied",
        "cpu_limit_required",
        "memory_limit_required",
        "pids_limit_required",
    )


def test_public_projection_omits_mounts_and_unapproved_limit_keys() -> None:
    report = evaluate_sandbox_preflight(
        SandboxPreflightRequest(
            backend="whole_process_container",
            mode="gateway",
            non_root=True,
            network_default_deny=True,
            mounted_paths=("/private/tmp/work", "/workspace"),
            resource_limits={
                "cpu": 2,
                "memoryMb": 4096,
                "pids": 256,
                "secretPath": 1,
            },
        ),
    )

    projection = report.public_projection()
    dumped = repr(projection)

    assert "/private" not in dumped
    assert "secretPath" not in dumped
    assert projection["resourceLimits"] == {
        "cpu": 2,
        "memoryMb": 4096,
        "pids": 256,
    }


def test_unapproved_mount_paths_are_denied() -> None:
    report = evaluate_sandbox_preflight(
        SandboxPreflightRequest(
            backend="whole_process_container",
            mode="gateway",
            non_root=True,
            network_default_deny=True,
            mounted_paths=("/proc", "/workspace/../etc"),
            resource_limits={"cpu": 2, "memoryMb": 4096, "pids": 256},
        ),
    )

    projection = report.public_projection()
    dumped = repr(projection)

    assert report.ready is False
    assert "unapproved_mount_path" in report.reason_codes
    assert "/proc" not in dumped
    assert "/etc" not in dumped


def test_bypassed_truthy_flags_do_not_claim_ready() -> None:
    request = SandboxPreflightRequest.model_construct(
        backend="whole_process_container",
        mode="gateway",
        untrusted_inputs=("web",),
        plugin_loading_enabled=False,
        mcp_enabled=False,
        non_root="yes",
        network_default_deny="yes",
        mounted_paths=("/workspace",),
        resource_limits={"cpu": 2, "memoryMb": 4096, "pids": 256},
    )

    report = evaluate_sandbox_preflight(request)

    assert report.ready is False
    assert report.reason_codes == (
        "sandbox_must_run_non_root",
        "network_default_deny_required",
    )


def test_normal_boolean_strings_are_rejected() -> None:
    with pytest.raises(ValidationError):
        SandboxPreflightRequest(
            backend="whole_process_container",
            mode="gateway",
            non_root="yes",
            network_default_deny=True,
            mounted_paths=("/workspace",),
            resource_limits={"cpu": 2, "memoryMb": 4096, "pids": 256},
        )


def test_resource_limit_strings_are_rejected() -> None:
    with pytest.raises(ValidationError):
        SandboxPreflightRequest(
            backend="whole_process_container",
            mode="gateway",
            non_root=True,
            network_default_deny=True,
            mounted_paths=("/workspace",),
            resource_limits={"cpu": "2", "memoryMb": 4096, "pids": 256},
        )


def test_cpu_limit_is_required_for_whole_process_ready() -> None:
    report = evaluate_sandbox_preflight(
        SandboxPreflightRequest(
            backend="whole_process_container",
            mode="gateway",
            non_root=True,
            network_default_deny=True,
            mounted_paths=("/workspace",),
            resource_limits={"memoryMb": 4096, "pids": 256},
        ),
    )

    assert report.ready is False
    assert report.reason_codes == ("cpu_limit_required",)


def test_public_projection_does_not_publish_forged_ready_state() -> None:
    report = SandboxPreflightReport.model_construct(
        ready=True,
        boundary_class="whole_process_boundary",
        reason_codes=("sandbox_preflight_ready", "/private/path"),
        request=SandboxPreflightRequest.model_construct(
            backend="/private/runtime",
            mode="gateway",
            resource_limits={"memoryMb": 4096, "secretPath": 1},
        ),
    )

    projection = report.public_projection()
    dumped = repr(projection)

    assert report.ready is True
    assert projection["ready"] is False
    assert "/private" not in dumped
    assert "secretPath" not in dumped


def test_public_projection_redacts_sensitive_reason_codes() -> None:
    sensitive_reason = "credential_" + "token"
    report = SandboxPreflightReport.model_construct(
        ready=True,
        boundary_class="whole_process_boundary",
        reason_codes=("sandbox_preflight_ready", sensitive_reason),
        request=SandboxPreflightRequest.model_construct(
            backend="whole_process_container",
            mode="gateway",
            resource_limits={"cpu": 2, "memoryMb": 4096, "pids": 256},
        ),
    )

    projection = report.public_projection()
    dumped = repr(projection)

    assert sensitive_reason not in dumped
    assert projection["ready"] is False
    assert projection["reasonCodes"] == ["sandbox_preflight_ready", "redacted"]


def test_public_projection_redacts_unknown_reason_codes() -> None:
    unknown_reason = "provider_payload_ref"
    report = SandboxPreflightReport.model_construct(
        ready=True,
        boundary_class="whole_process_boundary",
        reason_codes=("sandbox_preflight_ready", unknown_reason),
        request=SandboxPreflightRequest.model_construct(
            backend="whole_process_container",
            mode="gateway",
            non_root=True,
            network_default_deny=True,
            mounted_paths=("/workspace",),
            resource_limits={"cpu": 2, "memoryMb": 4096, "pids": 256},
        ),
    )

    projection = report.public_projection()
    dumped = repr(projection)

    assert unknown_reason not in dumped
    assert projection["ready"] is False
    assert projection["reasonCodes"] == ["sandbox_preflight_ready", "redacted"]


def test_public_projection_requires_ready_reason_for_ready_state() -> None:
    report = SandboxPreflightReport.model_construct(
        ready=True,
        boundary_class="whole_process_boundary",
        reason_codes=("sandbox_must_run_non_root",),
        request=SandboxPreflightRequest.model_construct(
            backend="whole_process_container",
            mode="gateway",
            resource_limits={"cpu": 2, "memoryMb": 4096, "pids": 256},
        ),
    )

    projection = report.public_projection()

    assert report.ready is True
    assert projection["ready"] is False
    assert projection["reasonCodes"] == ["sandbox_must_run_non_root"]


def test_public_projection_recomputes_required_ready_prerequisites() -> None:
    report = SandboxPreflightReport.model_construct(
        ready=True,
        boundary_class="whole_process_boundary",
        reason_codes=("sandbox_preflight_ready",),
        request=SandboxPreflightRequest.model_construct(
            backend="whole_process_container",
            mode="gateway",
            non_root=True,
            network_default_deny=True,
            mounted_paths=("/private/tmp/work",),
            resource_limits={"memoryMb": 4096, "pids": 256},
        ),
    )

    projection = report.public_projection()
    dumped = repr(projection)

    assert report.ready is True
    assert projection["ready"] is False
    assert "/private" not in dumped
