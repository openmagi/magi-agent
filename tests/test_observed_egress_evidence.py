import json

import pytest

from openmagi_core_agent.evidence.observed_egress import (
    LiveEgressTelemetryEvidenceProvider,
    LocalObservedEgressEvidenceProvider,
    NoObservedEgressEvidenceProvider,
    ObservedEgressEvidence,
    build_gate1a_observed_egress_evidence_provider_from_env,
    observed_egress_diagnostics,
)


REQUEST_DIGEST = "sha256:" + "a" * 64
MODEL_ATTEMPT_DIGEST = "sha256:" + "b" * 64
OTHER_DIGEST = "sha256:" + "c" * 64
WINDOW_START = "2026-05-24T10:00:00.000Z"
WINDOW_END = "2026-05-24T10:00:05.000Z"


def _append_egress_event(path, **overrides: object) -> None:
    event = {
        "schemaVersion": "gate1a.egressProxyTelemetry.v1",
        "observedAt": "2026-05-24T10:00:01.000Z",
        "requestDigest": REQUEST_DIGEST,
        "correlationDigest": REQUEST_DIGEST,
        "modelAttemptDigest": MODEL_ATTEMPT_DIGEST,
        "egressHostClass": "gemini_proxy",
        "evidenceSource": "gate5b_egress_proxy",
        "redactionStatus": "public_safe",
        "decisionReason": "connect_tunnel_established",
    }
    event.update(overrides)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, sort_keys=True) + "\n")


def test_observed_egress_evidence_contract_is_digest_only_and_public_safe() -> None:
    evidence = ObservedEgressEvidence.model_validate(
        {
            "requestDigest": REQUEST_DIGEST,
            "modelAttemptDigest": MODEL_ATTEMPT_DIGEST,
            "providerRequestCount": 1,
            "egressTunnelCount": 2,
            "egressHostClasses": ["gemini_proxy"],
            "observedWindowStart": "2026-05-24T10:00:00.000Z",
            "observedWindowEnd": "2026-05-24T10:00:01.000Z",
            "evidenceSource": "local_fixture",
            "redactionStatus": "public_safe",
            "decisionReason": "observed_gemini_proxy_tunnel",
        }
    )

    payload = evidence.model_dump(by_alias=True, mode="json")

    assert payload["requestDigest"] == REQUEST_DIGEST
    assert payload["modelAttemptDigest"] == MODEL_ATTEMPT_DIGEST
    assert payload["providerRequestCount"] == 1
    assert payload["egressTunnelCount"] == 2
    assert payload["egressHostClasses"] == ["gemini_proxy"]
    assert payload["evidenceSource"] == "local_fixture"
    assert "generativelanguage.googleapis.com" not in str(payload)
    assert "/Users/" not in str(payload)
    assert "Bearer" not in str(payload)


@pytest.mark.parametrize(
    "payload",
    [
        {
            "requestDigest": "generativelanguage.googleapis.com",
            "providerRequestCount": 1,
            "egressTunnelCount": 1,
            "egressHostClasses": ["gemini_proxy"],
            "observedWindowStart": "2026-05-24T10:00:00.000Z",
            "observedWindowEnd": "2026-05-24T10:00:01.000Z",
            "evidenceSource": "local_fixture",
            "redactionStatus": "public_safe",
            "decisionReason": "observed_gemini_proxy_tunnel",
        },
        {
            "requestDigest": REQUEST_DIGEST,
            "providerRequestCount": 1,
            "egressTunnelCount": 1,
            "egressHostClasses": ["generativelanguage.googleapis.com"],
            "observedWindowStart": "2026-05-24T10:00:00.000Z",
            "observedWindowEnd": "2026-05-24T10:00:01.000Z",
            "evidenceSource": "local_fixture",
            "redactionStatus": "public_safe",
            "decisionReason": "observed_gemini_proxy_tunnel",
        },
        {
            "requestDigest": REQUEST_DIGEST,
            "providerRequestCount": 1,
            "egressTunnelCount": 1,
            "egressHostClasses": ["gemini_proxy"],
            "observedWindowStart": "2026-05-24T10:00:00.000Z",
            "observedWindowEnd": "2026-05-24T10:00:01.000Z",
            "evidenceSource": "local_fixture",
            "redactionStatus": "public_safe",
            "decisionReason": "/Users/kevin/.config/token",
        },
    ],
)
def test_observed_egress_evidence_rejects_raw_hosts_paths_and_secret_markers(
    payload: dict[str, object],
) -> None:
    with pytest.raises(ValueError):
        ObservedEgressEvidence.model_validate(payload)


def test_no_observed_egress_provider_is_default_off_and_not_ready() -> None:
    provider = NoObservedEgressEvidenceProvider()

    assert provider.collect(request_digest=REQUEST_DIGEST) is None
    assert observed_egress_diagnostics(provider) == {
        "observedEgressEvidenceAvailable": False,
        "gate1aEgressEvidenceReady": False,
        "egressEvidenceSource": "none",
        "egressEvidenceReadinessReason": "no_live_correlation_source_configured",
    }


def test_observed_egress_diagnostics_redacts_unsafe_provider_labels() -> None:
    class UnsafeProvider(NoObservedEgressEvidenceProvider):
        evidence_source = "api_key"
        readiness_reason = "private_token"

    assert observed_egress_diagnostics(UnsafeProvider()) == {
        "observedEgressEvidenceAvailable": False,
        "gate1aEgressEvidenceReady": False,
        "egressEvidenceSource": "redacted",
        "egressEvidenceReadinessReason": "redacted",
    }


def test_live_egress_telemetry_source_disabled_keeps_readiness_false() -> None:
    provider = build_gate1a_observed_egress_evidence_provider_from_env({})

    assert isinstance(provider, NoObservedEgressEvidenceProvider)
    assert observed_egress_diagnostics(provider) == {
        "observedEgressEvidenceAvailable": False,
        "gate1aEgressEvidenceReady": False,
        "egressEvidenceSource": "none",
        "egressEvidenceReadinessReason": "no_live_correlation_source_configured",
    }


def test_live_egress_telemetry_source_requires_existing_readable_file(tmp_path) -> None:
    provider = build_gate1a_observed_egress_evidence_provider_from_env(
        {
            "CORE_AGENT_PYTHON_GATE1A_EGRESS_EVIDENCE_SOURCE": "egress_proxy_telemetry",
            "CORE_AGENT_PYTHON_GATE1A_EGRESS_TELEMETRY_PATH": str(
                tmp_path / "missing.jsonl"
            ),
            "CORE_AGENT_PYTHON_GATE1A_EGRESS_CORRELATION_MODE": "proxy_connect_headers",
        }
    )

    assert observed_egress_diagnostics(provider) == {
        "observedEgressEvidenceAvailable": False,
        "gate1aEgressEvidenceReady": False,
        "egressEvidenceSource": "gate5b_egress_proxy",
        "egressEvidenceReadinessReason": "telemetry_source_unavailable",
    }


def test_live_egress_telemetry_source_requires_explicit_correlation_mode(
    tmp_path,
) -> None:
    telemetry_path = tmp_path / "egress.jsonl"
    telemetry_path.write_text("", encoding="utf-8")

    provider = build_gate1a_observed_egress_evidence_provider_from_env(
        {
            "CORE_AGENT_PYTHON_GATE1A_EGRESS_EVIDENCE_SOURCE": "egress_proxy_telemetry",
            "CORE_AGENT_PYTHON_GATE1A_EGRESS_TELEMETRY_PATH": str(telemetry_path),
        }
    )

    assert observed_egress_diagnostics(provider) == {
        "observedEgressEvidenceAvailable": False,
        "gate1aEgressEvidenceReady": False,
        "egressEvidenceSource": "gate5b_egress_proxy",
        "egressEvidenceReadinessReason": "correlation_source_not_configured",
    }


def test_live_egress_telemetry_source_requires_proxy_connect_header_source_for_readiness(
    tmp_path,
) -> None:
    telemetry_path = tmp_path / "egress.jsonl"
    telemetry_path.write_text("", encoding="utf-8")

    provider = build_gate1a_observed_egress_evidence_provider_from_env(
        {
            "CORE_AGENT_PYTHON_GATE1A_EGRESS_EVIDENCE_SOURCE": "egress_proxy_telemetry",
            "CORE_AGENT_PYTHON_GATE1A_EGRESS_TELEMETRY_PATH": str(telemetry_path),
            "CORE_AGENT_PYTHON_GATE1A_EGRESS_CORRELATION_MODE": "proxy_connect_headers",
        }
    )

    assert observed_egress_diagnostics(provider) == {
        "observedEgressEvidenceAvailable": True,
        "gate1aEgressEvidenceReady": False,
        "egressEvidenceSource": "gate5b_egress_proxy",
        "egressEvidenceReadinessReason": "proxy_connect_header_source_unavailable",
    }


def test_live_egress_telemetry_source_reports_ready_with_proxy_connect_header_source(
    tmp_path,
) -> None:
    telemetry_path = tmp_path / "egress.jsonl"
    telemetry_path.write_text("", encoding="utf-8")

    provider = build_gate1a_observed_egress_evidence_provider_from_env(
        {
            "CORE_AGENT_PYTHON_GATE1A_EGRESS_EVIDENCE_SOURCE": "egress_proxy_telemetry",
            "CORE_AGENT_PYTHON_GATE1A_EGRESS_TELEMETRY_PATH": str(telemetry_path),
            "CORE_AGENT_PYTHON_GATE1A_EGRESS_CORRELATION_MODE": "proxy_connect_headers",
            "HTTPS_PROXY": (
                "http://gate5b-gemini-egress-proxy.clawy-system.svc.cluster.local:8080"
            ),
        }
    )

    assert observed_egress_diagnostics(provider) == {
        "observedEgressEvidenceAvailable": True,
        "gate1aEgressEvidenceReady": True,
        "egressEvidenceSource": "gate5b_egress_proxy",
        "egressEvidenceReadinessReason": "live_correlation_source_ready",
    }


def test_live_egress_telemetry_source_rejects_malformed_telemetry_readiness(
    tmp_path,
) -> None:
    telemetry_path = tmp_path / "egress.jsonl"
    telemetry_path.write_text("{not json}\n", encoding="utf-8")

    provider = build_gate1a_observed_egress_evidence_provider_from_env(
        {
            "CORE_AGENT_PYTHON_GATE1A_EGRESS_EVIDENCE_SOURCE": "egress_proxy_telemetry",
            "CORE_AGENT_PYTHON_GATE1A_EGRESS_TELEMETRY_PATH": str(telemetry_path),
            "CORE_AGENT_PYTHON_GATE1A_EGRESS_CORRELATION_MODE": "proxy_connect_headers",
            "HTTPS_PROXY": (
                "http://gate5b-gemini-egress-proxy.clawy-system.svc.cluster.local:8080"
            ),
        }
    )

    assert observed_egress_diagnostics(provider) == {
        "observedEgressEvidenceAvailable": False,
        "gate1aEgressEvidenceReady": False,
        "egressEvidenceSource": "gate5b_egress_proxy",
        "egressEvidenceReadinessReason": "telemetry_source_unavailable",
    }


def test_live_egress_telemetry_collects_one_observed_gemini_proxy_tunnel(tmp_path) -> None:
    telemetry_path = tmp_path / "egress.jsonl"
    _append_egress_event(telemetry_path)
    provider = LiveEgressTelemetryEvidenceProvider(telemetry_path)

    evidence = provider.collect(
        request_digest=REQUEST_DIGEST,
        model_attempt_digest=MODEL_ATTEMPT_DIGEST,
        observed_window_start=WINDOW_START,
        observed_window_end=WINDOW_END,
    )

    assert evidence is not None
    assert evidence.provider_request_count == 1
    assert evidence.egress_tunnel_count == 1
    assert evidence.egress_host_classes == ("gemini_proxy",)
    assert evidence.observed_window_start == "2026-05-24T10:00:01.000Z"
    assert evidence.observed_window_end == "2026-05-24T10:00:01.000Z"
    assert evidence.evidence_source == "gate5b_egress_proxy"
    assert evidence.decision_reason == "observed_gemini_proxy_tunnel"


def test_live_egress_telemetry_collects_two_bounded_gemini_proxy_tunnels(
    tmp_path,
) -> None:
    telemetry_path = tmp_path / "egress.jsonl"
    _append_egress_event(telemetry_path, observedAt="2026-05-24T10:00:01.000Z")
    _append_egress_event(telemetry_path, observedAt="2026-05-24T10:00:02.000Z")
    provider = LiveEgressTelemetryEvidenceProvider(telemetry_path)

    evidence = provider.collect(
        request_digest=REQUEST_DIGEST,
        model_attempt_digest=MODEL_ATTEMPT_DIGEST,
        observed_window_start=WINDOW_START,
        observed_window_end=WINDOW_END,
    )

    assert evidence is not None
    assert evidence.provider_request_count == 1
    assert evidence.egress_tunnel_count == 2
    assert evidence.egress_host_classes == ("gemini_proxy",)
    assert evidence.observed_window_start == "2026-05-24T10:00:01.000Z"
    assert evidence.observed_window_end == "2026-05-24T10:00:02.000Z"


def test_live_egress_telemetry_reports_non_gemini_observed_class_for_policy_fail(
    tmp_path,
) -> None:
    telemetry_path = tmp_path / "egress.jsonl"
    _append_egress_event(
        telemetry_path,
        egressHostClass="non_gemini_public",
        decisionReason="connect_tunnel_established",
    )
    provider = LiveEgressTelemetryEvidenceProvider(telemetry_path)

    evidence = provider.collect(
        request_digest=REQUEST_DIGEST,
        model_attempt_digest=MODEL_ATTEMPT_DIGEST,
        observed_window_start=WINDOW_START,
        observed_window_end=WINDOW_END,
    )

    assert evidence is not None
    assert evidence.egress_host_classes == ("non_gemini_public",)
    assert evidence.egress_tunnel_count == 1


def test_live_egress_telemetry_ignores_outside_window_events(tmp_path) -> None:
    telemetry_path = tmp_path / "egress.jsonl"
    _append_egress_event(telemetry_path, observedAt="2026-05-24T09:59:59.999Z")
    provider = LiveEgressTelemetryEvidenceProvider(telemetry_path)

    assert (
        provider.collect(
            request_digest=REQUEST_DIGEST,
            model_attempt_digest=MODEL_ATTEMPT_DIGEST,
            observed_window_start=WINDOW_START,
            observed_window_end=WINDOW_END,
        )
        is None
    )


def test_live_egress_telemetry_fails_missing_and_ambiguous_correlation(
    tmp_path,
) -> None:
    missing_path = tmp_path / "missing-correlation.jsonl"
    _append_egress_event(
        missing_path,
        requestDigest=None,
        correlationDigest=None,
        modelAttemptDigest=None,
    )
    missing_provider = LiveEgressTelemetryEvidenceProvider(missing_path)

    assert (
        missing_provider.collect(
            request_digest=REQUEST_DIGEST,
            model_attempt_digest=MODEL_ATTEMPT_DIGEST,
            observed_window_start=WINDOW_START,
            observed_window_end=WINDOW_END,
        )
        is None
    )

    missing_model_attempt_path = tmp_path / "missing-model-attempt.jsonl"
    _append_egress_event(missing_model_attempt_path, modelAttemptDigest=None)
    missing_model_attempt_provider = LiveEgressTelemetryEvidenceProvider(
        missing_model_attempt_path
    )

    assert (
        missing_model_attempt_provider.collect(
            request_digest=REQUEST_DIGEST,
            model_attempt_digest=MODEL_ATTEMPT_DIGEST,
            observed_window_start=WINDOW_START,
            observed_window_end=WINDOW_END,
        )
        is None
    )

    ambiguous_path = tmp_path / "ambiguous-correlation.jsonl"
    _append_egress_event(ambiguous_path)
    _append_egress_event(ambiguous_path, modelAttemptDigest=OTHER_DIGEST)
    ambiguous_provider = LiveEgressTelemetryEvidenceProvider(ambiguous_path)

    assert (
        ambiguous_provider.collect(
            request_digest=REQUEST_DIGEST,
            model_attempt_digest=MODEL_ATTEMPT_DIGEST,
            observed_window_start=WINDOW_START,
            observed_window_end=WINDOW_END,
        )
        is None
    )


def test_live_egress_telemetry_preserves_too_many_observed_tunnels_for_policy_fail(
    tmp_path,
) -> None:
    telemetry_path = tmp_path / "egress.jsonl"
    _append_egress_event(telemetry_path, observedAt="2026-05-24T10:00:01.000Z")
    _append_egress_event(telemetry_path, observedAt="2026-05-24T10:00:02.000Z")
    _append_egress_event(telemetry_path, observedAt="2026-05-24T10:00:03.000Z")
    provider = LiveEgressTelemetryEvidenceProvider(telemetry_path)

    evidence = provider.collect(
        request_digest=REQUEST_DIGEST,
        model_attempt_digest=MODEL_ATTEMPT_DIGEST,
        observed_window_start=WINDOW_START,
        observed_window_end=WINDOW_END,
    )

    assert evidence is not None
    assert evidence.egress_tunnel_count == 3


def test_live_egress_telemetry_rejects_raw_host_auth_body_cookie_token_and_private_path(
    tmp_path,
) -> None:
    telemetry_path = tmp_path / "egress.jsonl"
    _append_egress_event(
        telemetry_path,
        rawHost="generativelanguage.googleapis.com",
        authorization="Bearer raw-token",
        cookie="session=raw-cookie",
        requestBody="raw prompt text",
        decisionReason="/Users/kevin/private/token",
    )
    provider = LiveEgressTelemetryEvidenceProvider(telemetry_path)

    assert (
        provider.collect(
            request_digest=REQUEST_DIGEST,
            model_attempt_digest=MODEL_ATTEMPT_DIGEST,
            observed_window_start=WINDOW_START,
            observed_window_end=WINDOW_END,
        )
        is None
    )


def test_local_fixture_provider_can_emit_evidence_but_is_not_activation_ready() -> None:
    evidence = ObservedEgressEvidence.model_validate(
        {
            "requestDigest": REQUEST_DIGEST,
            "providerRequestCount": 1,
            "egressTunnelCount": 1,
            "egressHostClasses": ["gemini_proxy"],
            "observedWindowStart": "2026-05-24T10:00:00.000Z",
            "observedWindowEnd": "2026-05-24T10:00:01.000Z",
            "evidenceSource": "local_fixture",
            "redactionStatus": "public_safe",
            "decisionReason": "observed_gemini_proxy_tunnel",
        }
    )
    provider = LocalObservedEgressEvidenceProvider(evidence)

    observed = provider.collect(request_digest=REQUEST_DIGEST)

    assert observed == evidence
    assert provider.collect(request_digest="sha256:" + "c" * 64) is None
    assert observed_egress_diagnostics(provider)["observedEgressEvidenceAvailable"] is True
    assert observed_egress_diagnostics(provider)["gate1aEgressEvidenceReady"] is False
