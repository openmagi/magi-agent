from __future__ import annotations

from pydantic import ValidationError

from openmagi_core_agent.runtime.no_agent_watchdog import (
    NoAgentWatchdogAuthorityFlags,
    NoAgentWatchdogDecision,
    NoAgentWatchdogRequest,
    evaluate_no_agent_watchdog,
)


def test_no_agent_watchdog_is_silent_for_empty_success_without_waking_agent() -> None:
    decision = evaluate_no_agent_watchdog(
        NoAgentWatchdogRequest(
            watchdogId="watchdog:nightly-check",
            tickId="tick:20260528T190000Z",
            jobRef="job:billing-health",
            stdout="",
            exitCode=0,
            timedOut=False,
            wakeAgent=True,
        )
    )

    assert decision.status == "silent_healthy"
    assert decision.wake_agent is False
    assert decision.alert_required is False
    assert decision.reason_codes == ("empty_output_success",)
    projection = decision.public_projection()
    assert projection["wakeAgent"] is False
    assert projection["alertRequired"] is False
    assert projection["stdoutPreview"] is None
    assert set(projection["authorityFlags"].values()) == {False}


def test_no_agent_watchdog_alerts_on_non_empty_output_without_channel_delivery() -> None:
    decision = evaluate_no_agent_watchdog(
        NoAgentWatchdogRequest(
            watchdogId="watchdog:nightly-check",
            tickId="tick:20260528T190100Z",
            jobRef="job:billing-health",
            stdout="invoice sync drift detected",
            exitCode=0,
        )
    )

    projection = decision.public_projection()

    assert decision.status == "alert_output"
    assert decision.alert_required is True
    assert decision.alert_kind == "output"
    assert projection["stdoutDigest"].startswith("sha256:")
    assert projection["stdoutPreview"] is None
    assert "invoice sync drift detected" not in str(projection)
    assert projection["authorityFlags"]["wakeAgent"] is False
    assert projection["authorityFlags"]["modelCallEnabled"] is False
    assert projection["authorityFlags"]["providerCallEnabled"] is False
    assert projection["authorityFlags"]["toolExecutionEnabled"] is False
    assert projection["authorityFlags"]["childExecutionEnabled"] is False
    assert projection["authorityFlags"]["channelDeliveryEnabled"] is False


def test_no_agent_watchdog_alerts_on_failure_and_timeout() -> None:
    failed = evaluate_no_agent_watchdog(
        NoAgentWatchdogRequest(
            watchdogId="watchdog:nightly-check",
            tickId="tick:failure",
            jobRef="job:billing-health",
            stdout="operator log detail",
            exitCode=2,
        )
    )
    timed_out = evaluate_no_agent_watchdog(
        NoAgentWatchdogRequest(
            watchdogId="watchdog:nightly-check",
            tickId="tick:timeout",
            jobRef="job:billing-health",
            stdout="timeout log detail",
            exitCode=0,
            timedOut=True,
        )
    )
    failed_projection = failed.public_projection()
    timed_out_projection = timed_out.public_projection()

    assert failed.status == "alert_failure"
    assert failed.alert_kind == "failure"
    assert failed.alert_required is True
    assert failed.reason_codes == ("non_zero_exit",)
    assert timed_out.status == "alert_timeout"
    assert timed_out.alert_kind == "timeout"
    assert timed_out.alert_required is True
    assert timed_out.reason_codes == ("timeout_failure",)
    assert failed_projection["stdoutDigest"] is None
    assert failed_projection["stdoutPreview"] is None
    assert timed_out_projection["stdoutDigest"] is None
    assert timed_out_projection["stdoutPreview"] is None
    assert "operator log detail" not in str(failed_projection)
    assert "timeout log detail" not in str(timed_out_projection)
    assert failed_projection["authorityFlags"]["channelDeliveryEnabled"] is False
    assert timed_out_projection["authorityFlags"]["channelDeliveryEnabled"] is False


def test_no_agent_watchdog_denies_recursive_scheduler_requests() -> None:
    decision = evaluate_no_agent_watchdog(
        NoAgentWatchdogRequest(
            watchdogId="watchdog:nightly-check",
            tickId="tick:recursive",
            jobRef="job:billing-health",
            stdout="schedule another cron run",
            exitCode=0,
            recursiveSchedulerRequested=True,
        )
    )

    projection = decision.public_projection()

    assert decision.status == "blocked_recursive_scheduler"
    assert decision.alert_kind == "recursive_scheduler_denied"
    assert decision.reason_codes == ("recursive_scheduler_denied",)
    assert projection["recursiveSchedulerDenied"] is True
    assert projection["authorityFlags"]["schedulerAttached"] is False
    assert projection["authorityFlags"]["wakeAgent"] is False


def test_no_agent_watchdog_public_projection_redacts_private_output() -> None:
    decision = evaluate_no_agent_watchdog(
        NoAgentWatchdogRequest(
            watchdogId="watchdog:nightly-check",
            tickId="tick:redacted",
            jobRef="job:billing-health",
            stdout=(
                "raw output /Users/kevin/private token ghp_watchdogSecret "
                "Authorization: Bearer unsafe-token"
            ),
            exitCode=0,
        )
    )

    encoded = str(decision.public_projection())

    assert "ghp_watchdogSecret" not in encoded
    assert "Authorization" not in encoded
    assert "/Users/kevin" not in encoded
    projection = decision.public_projection()
    assert projection["stdoutDigest"].startswith("sha256:")
    assert projection["stdoutPreview"] is None


def test_no_agent_watchdog_rejects_authority_shaped_refs_and_metadata() -> None:
    for payload in (
        {
            "watchdogId": "watchdog:nightly-check",
            "tickId": "tick:bad",
            "jobRef": "provider:openai",
            "stdout": "",
        },
        {
            "watchdogId": "watchdog:nightly-check",
            "tickId": "tick:bad",
            "jobRef": "job:billing-health",
            "stdout": "",
            "metadata": {"modelCallEnabled": True},
        },
    ):
        try:
            NoAgentWatchdogRequest(**payload)
        except ValidationError:
            pass
        else:
            raise AssertionError("authority-shaped watchdog input should be rejected")


def test_no_agent_watchdog_forged_decision_projection_is_sanitized_and_default_off() -> None:
    for payload in (
        {
            "status": "/Users/kevin/private-status",
            "alertKind": "output",
            "watchdogId": "watchdog:nightly-check",
            "tickId": "tick:forged",
            "jobRef": "job:billing-health",
            "alertRequired": True,
            "exitCode": 0,
            "timedOut": False,
        },
        {
            "status": "alert_output",
            "alertKind": "output",
            "watchdogId": "/Users/kevin/private-watchdog",
            "tickId": "scheduler.tick",
            "jobRef": "provider:openai",
            "wakeAgent": True,
            "alertRequired": True,
            "stdoutDigest": "not-a-digest",
            "stdoutPreview": "raw output /Users/kevin/private ghp_watchdogSecret",
            "exitCode": 0,
            "timedOut": False,
            "recursiveSchedulerDenied": False,
            "reasonCodes": ("scheduler.tick",),
            "authorityFlags": NoAgentWatchdogAuthorityFlags.model_construct(
                wakeAgent=True,
                modelCallEnabled=True,
                channelDeliveryEnabled=True,
            ),
        },
    ):
        try:
            NoAgentWatchdogDecision.model_construct(**payload)
        except (TypeError, ValueError, ValidationError):
            pass
        else:
            raise AssertionError("forged watchdog decisions should revalidate on construct")


def test_no_agent_watchdog_model_copy_cannot_enable_authority_or_leak_output() -> None:
    decision = evaluate_no_agent_watchdog(
        NoAgentWatchdogRequest(
            watchdogId="watchdog:nightly-check",
            tickId="tick:copy",
            jobRef="job:billing-health",
            stdout="public alert",
        )
    )

    for update in (
        {"wakeAgent": True},
        {"stdoutPreview": "raw output /Users/kevin/private ghp_watchdogSecret"},
        {"authorityFlags": {"modelCallEnabled": True}},
    ):
        try:
            decision.model_copy(update=update)
        except (TypeError, ValueError, ValidationError):
            pass
        else:
            raise AssertionError("watchdog decisions should not accept unsafe copy updates")
