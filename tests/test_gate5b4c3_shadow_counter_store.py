from __future__ import annotations

import json

from magi_agent.shadow.gate5b4c3_shadow_counter_store import (
    Gate5B4C3ShadowCounterStore,
)


BOT_DIGEST = "sha256:" + "a" * 64
OWNER_DIGEST = "sha256:" + "b" * 64
REQUEST_DIGEST = "sha256:" + "c" * 64
SECOND_REQUEST_DIGEST = "sha256:" + "d" * 64


def _reserved_finished_store(tmp_path, request_digest: str = REQUEST_DIGEST):
    path = tmp_path / "gate5b-shadow-counters.json"
    store = Gate5B4C3ShadowCounterStore(path)
    reservation = store.reserve(
        request_digest=request_digest,
        shadow_generation_id="shadow_gen_egress_policy",
        selected_bot_digest=BOT_DIGEST,
        trusted_owner_user_id_digest=OWNER_DIGEST,
        environment="production",
        max_daily_generation_runs=10,
        max_daily_generation_cost_usd=0.50,
        max_concurrent_generation_runs=1,
        max_pending_generation_runs=1,
        cost_cap_usd=0.05,
        now_ms=1_779_200_000_000,
    )
    store.finish(
        reservation,
        status="runner_completed",
        reason="runner_completed",
        report_digest="sha256:" + "d" * 64,
        now_ms=1_779_200_002_000,
    )
    return path, store


def _request_record(path, request_digest: str = REQUEST_DIGEST):
    raw = json.loads(path.read_text(encoding="utf-8"))
    return next(iter(raw["scopes"].values()))["requests"][request_digest]


def test_counter_store_directory_path_keeps_lock_inside_writable_counter_dir(
    tmp_path,
) -> None:
    parent = tmp_path / "readonly-parent"
    counter_dir = parent / "gate5b-shadow-counters"
    parent.mkdir()
    counter_dir.mkdir()
    parent.chmod(0o500)
    counter_dir.chmod(0o700)

    try:
        store = Gate5B4C3ShadowCounterStore(counter_dir)

        reservation = store.reserve(
            request_digest=REQUEST_DIGEST,
            shadow_generation_id="shadow_gen_001",
            selected_bot_digest=BOT_DIGEST,
            trusted_owner_user_id_digest=OWNER_DIGEST,
            environment="production",
            max_daily_generation_runs=1,
            max_daily_generation_cost_usd=0.05,
            max_concurrent_generation_runs=1,
            max_pending_generation_runs=1,
            cost_cap_usd=0.05,
            now_ms=1_779_200_000_000,
        )

        assert reservation.status == "reserved"
        assert (counter_dir / ".lock").exists()
        assert (counter_dir / "state.json").exists()
        assert not (parent / ".gate5b-shadow-counters.lock").exists()
    finally:
        parent.chmod(0o700)
        counter_dir.chmod(0o700)


def test_counter_store_accepts_configured_counter_directory_path(tmp_path) -> None:
    counter_dir = tmp_path / "gate5b-shadow-counters"
    counter_dir.mkdir()
    store = Gate5B4C3ShadowCounterStore(counter_dir)

    reservation = store.reserve(
        request_digest=REQUEST_DIGEST,
        shadow_generation_id="shadow_gen_001",
        selected_bot_digest=BOT_DIGEST,
        trusted_owner_user_id_digest=OWNER_DIGEST,
        environment="production",
        max_daily_generation_runs=1,
        max_daily_generation_cost_usd=0.05,
        max_concurrent_generation_runs=1,
        max_pending_generation_runs=1,
        cost_cap_usd=0.05,
        now_ms=1_779_200_000_000,
    )
    store.finish(
        reservation,
        status="fallback_served",
        reason="typescript_fallback",
        report_digest="sha256:" + "d" * 64,
        now_ms=1_779_200_001_000,
    )

    duplicate = Gate5B4C3ShadowCounterStore(counter_dir).reserve(
        request_digest=REQUEST_DIGEST,
        shadow_generation_id="shadow_gen_001_retry",
        selected_bot_digest=BOT_DIGEST,
        trusted_owner_user_id_digest=OWNER_DIGEST,
        environment="production",
        max_daily_generation_runs=1,
        max_daily_generation_cost_usd=0.05,
        max_concurrent_generation_runs=1,
        max_pending_generation_runs=1,
        cost_cap_usd=0.05,
        now_ms=1_779_200_002_000,
    )

    raw = json.loads((counter_dir / "state.json").read_text(encoding="utf-8"))
    assert raw["schemaVersion"] == "gate5b4c3.shadowCounterStore.v1"
    assert duplicate.status == "duplicate_replay"
    assert duplicate.previous_report_digest == "sha256:" + "d" * 64


def test_counter_store_blocks_same_digest_while_in_flight_and_records_terminal_fallback(
    tmp_path,
) -> None:
    path = tmp_path / "gate5b-shadow-counters.json"
    store = Gate5B4C3ShadowCounterStore(path)

    first = store.reserve(
        request_digest=REQUEST_DIGEST,
        shadow_generation_id="shadow_gen_001",
        selected_bot_digest=BOT_DIGEST,
        trusted_owner_user_id_digest=OWNER_DIGEST,
        environment="production",
        max_daily_generation_runs=1,
        max_daily_generation_cost_usd=0.05,
        max_concurrent_generation_runs=1,
        max_pending_generation_runs=1,
        cost_cap_usd=0.05,
        now_ms=1_779_200_000_000,
    )
    duplicate = store.reserve(
        request_digest=REQUEST_DIGEST,
        shadow_generation_id="shadow_gen_001_dashboard_retry",
        selected_bot_digest=BOT_DIGEST,
        trusted_owner_user_id_digest=OWNER_DIGEST,
        environment="production",
        max_daily_generation_runs=1,
        max_daily_generation_cost_usd=0.05,
        max_concurrent_generation_runs=1,
        max_pending_generation_runs=1,
        cost_cap_usd=0.05,
        now_ms=1_779_200_000_500,
    )

    assert first.status == "reserved"
    assert duplicate.status == "blocked"
    assert duplicate.reason == "duplicate_in_flight"
    assert duplicate.should_invoke_runner is False
    assert duplicate.counter_state.daily_generation_runs_used == 1
    assert duplicate.counter_state.in_flight_generation_runs == 1
    assert duplicate.counter_state.pending_generation_runs == 1

    finished = store.finish(
        first,
        status="fallback_served",
        reason="typescript_fallback",
        report_digest="sha256:" + "d" * 64,
        now_ms=1_779_200_001_000,
    )

    raw = json.loads(path.read_text(encoding="utf-8"))
    record = next(iter(raw["scopes"].values()))["requests"][REQUEST_DIGEST]
    assert record["status"] == "fallback_served"
    assert record["reason"] == "typescript_fallback"
    assert finished.in_flight_generation_runs == 0
    assert finished.pending_generation_runs == 0


def test_counter_store_persists_public_safe_runner_error_diagnostic(tmp_path) -> None:
    path = tmp_path / "gate5b-shadow-counters.json"
    store = Gate5B4C3ShadowCounterStore(path)
    reservation = store.reserve(
        request_digest=REQUEST_DIGEST,
        shadow_generation_id="shadow_gen_runner_error",
        selected_bot_digest=BOT_DIGEST,
        trusted_owner_user_id_digest=OWNER_DIGEST,
        environment="production",
        max_daily_generation_runs=1,
        max_daily_generation_cost_usd=0.05,
        max_concurrent_generation_runs=1,
        max_pending_generation_runs=1,
        cost_cap_usd=0.05,
        now_ms=1_779_200_000_000,
    )

    store.finish(
        reservation,
        status="error",
        reason="runner_error",
        runner_error_diagnostic={
            "schemaVersion": "gate5b4c3.runnerErrorDiagnostic.v1",
            "stage": "provider_client_setup",
            "reasonCode": "provider_client_setup_failed",
            "exceptionClass": "RuntimeError",
            "exceptionCategory": "provider_client_setup_failure",
            "requestDigest": REQUEST_DIGEST,
            "traceIdDigest": "sha256:" + "1" * 64,
            "modelAttemptDigest": "sha256:" + "2" * 64,
            "routeMode": "shadow_generation_diagnostic",
            "gateMode": "gate1a_readonly_tools",
            "correlationMode": "proxy_connect_headers",
            "toolsPolicy": "shadow_readonly",
            "activeToolNames": ["read_workspace_index", "unsafe /private/path"],
            "errorPreview": "FunctionTool schema mismatch [REDACTED] [REDACTED]",
            "tracebackMarkers": [
                "google.adk.runners:run_async",
                "/private/path",
                "magi_agent.shadow.gate5b4c3_live_runner_boundary:_invoke",
            ],
            "adkInvoked": True,
            "runnerAttempted": True,
            "modelCallAttempted": False,
            "gate1aEgressEvidenceReady": True,
            "exceptionMessage": "Authorization: Bearer raw-token at /Users/kevin/private",
            "rawTraceback": "File /Users/kevin/private/token.py",
        },
        now_ms=1_779_200_001_000,
    )

    raw = json.loads(path.read_text(encoding="utf-8"))
    record = next(iter(raw["scopes"].values()))["requests"][REQUEST_DIGEST]
    diagnostic = record["runnerErrorDiagnostic"]
    assert diagnostic == {
        "activeToolNames": ["read_workspace_index"],
        "adkInvoked": True,
        "correlationMode": "proxy_connect_headers",
        "exceptionCategory": "provider_client_setup_failure",
        "exceptionClass": "RuntimeError",
        "errorPreview": "FunctionTool schema mismatch [REDACTED] [REDACTED]",
        "gate1aEgressEvidenceReady": True,
        "gateMode": "gate1a_readonly_tools",
        "modelAttemptDigest": "sha256:" + "2" * 64,
        "modelCallAttempted": False,
        "reasonCode": "provider_client_setup_failed",
        "requestDigest": REQUEST_DIGEST,
        "routeMode": "shadow_generation_diagnostic",
        "runnerAttempted": True,
        "schemaVersion": "gate5b4c3.runnerErrorDiagnostic.v1",
        "stage": "provider_client_setup",
        "toolsPolicy": "shadow_readonly",
        "traceIdDigest": "sha256:" + "1" * 64,
        "tracebackMarkers": [
            "google.adk.runners:run_async",
            "magi_agent.shadow.gate5b4c3_live_runner_boundary:_invoke",
        ],
    }
    serialized = json.dumps(record)
    for forbidden in ("raw-token", "Authorization:", "/Users/kevin", "/private/path"):
        assert forbidden not in serialized

    evidence = store.validate_delivery_evidence(
        request_digest=REQUEST_DIGEST,
        selected_bot_digest=BOT_DIGEST,
        trusted_owner_user_id_digest=OWNER_DIGEST,
        environment="production",
        gate="gate1a_readonly_tools",
    )
    assert evidence.runner_error_diagnostic is not None
    assert evidence.runner_error_diagnostic["stage"] == "provider_client_setup"


def test_counter_store_persists_daily_cost_and_idempotency(tmp_path) -> None:
    path = tmp_path / "gate5b-shadow-counters.json"
    store = Gate5B4C3ShadowCounterStore(path)

    reservation = store.reserve(
        request_digest=REQUEST_DIGEST,
        shadow_generation_id="shadow_gen_001",
        selected_bot_digest=BOT_DIGEST,
        trusted_owner_user_id_digest=OWNER_DIGEST,
        environment="production",
        max_daily_generation_runs=1,
        max_daily_generation_cost_usd=0.05,
        max_concurrent_generation_runs=1,
        max_pending_generation_runs=1,
        cost_cap_usd=0.05,
        now_ms=1_779_200_000_000,
    )

    assert reservation.status == "reserved"
    assert reservation.should_invoke_runner is True
    assert reservation.counter_state.daily_generation_runs_used == 1
    assert reservation.counter_state.daily_generation_cost_usd_used == 0.05
    assert reservation.counter_state.in_flight_generation_runs == 1
    assert reservation.counter_state.pending_generation_runs == 1

    store.finish(
        reservation,
        status="completed",
        reason="runner_completed",
        report_digest="sha256:" + "d" * 64,
        comparison_artifact_digest="sha256:" + "e" * 64,
        now_ms=1_779_200_001_000,
    )

    restarted = Gate5B4C3ShadowCounterStore(path)
    duplicate = restarted.reserve(
        request_digest=REQUEST_DIGEST,
        shadow_generation_id="shadow_gen_001_retry",
        selected_bot_digest=BOT_DIGEST,
        trusted_owner_user_id_digest=OWNER_DIGEST,
        environment="production",
        max_daily_generation_runs=1,
        max_daily_generation_cost_usd=0.05,
        max_concurrent_generation_runs=1,
        max_pending_generation_runs=1,
        cost_cap_usd=0.05,
        now_ms=1_779_200_002_000,
    )

    assert duplicate.status == "duplicate_replay"
    assert duplicate.should_invoke_runner is False
    assert duplicate.counter_state.daily_generation_runs_used == 1
    assert duplicate.counter_state.daily_generation_cost_usd_used == 0.05
    assert duplicate.previous_report_digest == "sha256:" + "d" * 64
    assert duplicate.previous_comparison_artifact_digest == "sha256:" + "e" * 64


def test_counter_store_directory_path_keeps_state_and_lock_inside_directory(tmp_path) -> None:
    counter_dir = tmp_path / "gate5b-shadow-counters"
    counter_dir.mkdir()
    store = Gate5B4C3ShadowCounterStore(counter_dir)

    reservation = store.reserve(
        request_digest=REQUEST_DIGEST,
        shadow_generation_id="shadow_gen_001",
        selected_bot_digest=BOT_DIGEST,
        trusted_owner_user_id_digest=OWNER_DIGEST,
        environment="production",
        max_daily_generation_runs=2,
        max_daily_generation_cost_usd=0.10,
        max_concurrent_generation_runs=1,
        max_pending_generation_runs=1,
        cost_cap_usd=0.05,
        now_ms=1_779_200_000_000,
    )

    assert reservation.status == "reserved"
    assert (counter_dir / "state.json").is_file()
    assert (counter_dir / ".lock").is_file()
    assert not (counter_dir / ".state.json.lock").exists()
    assert not (tmp_path / ".gate5b-shadow-counters.lock").exists()


def test_counter_store_tracks_user_visible_delivery_terminal_statuses(tmp_path) -> None:
    terminal_statuses = (
        "runner_completed",
        "served_to_client",
        "completed_after_client_timeout",
        "client_aborted",
        "fallback_served",
    )

    for index, status in enumerate(terminal_statuses):
        path = tmp_path / f"gate5b-shadow-counters-{status}.json"
        store = Gate5B4C3ShadowCounterStore(path)
        request_digest = "sha256:" + f"{index + 1:x}" * 64
        reservation = store.reserve(
            request_digest=request_digest,
            shadow_generation_id=f"shadow_gen_{index + 1:03d}",
            selected_bot_digest=BOT_DIGEST,
            trusted_owner_user_id_digest=OWNER_DIGEST,
            environment="production",
            max_daily_generation_runs=10,
            max_daily_generation_cost_usd=0.50,
            max_concurrent_generation_runs=1,
            max_pending_generation_runs=1,
            cost_cap_usd=0.05,
            now_ms=1_779_200_000_000 + index,
        )

        state = store.finish(
            reservation,
            status=status,
            reason=status,
            report_digest="sha256:" + "d" * 64,
            now_ms=1_779_200_001_000 + index,
        )

        raw = json.loads(path.read_text(encoding="utf-8"))
        request_records = next(iter(raw["scopes"].values()))["requests"]
        assert request_records[request_digest]["status"] == status
        assert state.in_flight_generation_runs == 0
        assert state.pending_generation_runs == 0

        duplicate = Gate5B4C3ShadowCounterStore(path).reserve(
            request_digest=request_digest,
            shadow_generation_id=f"shadow_gen_{index + 1:03d}_retry",
            selected_bot_digest=BOT_DIGEST,
            trusted_owner_user_id_digest=OWNER_DIGEST,
            environment="production",
            max_daily_generation_runs=10,
            max_daily_generation_cost_usd=0.50,
            max_concurrent_generation_runs=1,
            max_pending_generation_runs=1,
            cost_cap_usd=0.05,
            now_ms=1_779_200_002_000 + index,
        )
        assert duplicate.status == "duplicate_replay"
        assert duplicate.previous_report_digest == "sha256:" + "d" * 64


def test_counter_store_records_delivery_receipt_without_double_counting(tmp_path) -> None:
    path = tmp_path / "gate5b-shadow-counters.json"
    store = Gate5B4C3ShadowCounterStore(path)
    reservation = store.reserve(
        request_digest=REQUEST_DIGEST,
        shadow_generation_id="shadow_gen_delivery_001",
        selected_bot_digest=BOT_DIGEST,
        trusted_owner_user_id_digest=OWNER_DIGEST,
        environment="production",
        max_daily_generation_runs=10,
        max_daily_generation_cost_usd=0.50,
        max_concurrent_generation_runs=1,
        max_pending_generation_runs=1,
        cost_cap_usd=0.05,
        now_ms=1_779_200_000_000,
    )
    store.finish(
        reservation,
        status="runner_completed",
        reason="runner_completed",
        report_digest="sha256:" + "d" * 64,
        now_ms=1_779_200_002_000,
    )

    receipt = store.record_delivery_receipt(
        request_digest=REQUEST_DIGEST,
        selected_bot_digest=BOT_DIGEST,
        trusted_owner_user_id_digest=OWNER_DIGEST,
        environment="production",
        delivery_status="served_to_client",
        reason="served_to_client",
        body_digest="sha256:" + "1" * 64,
        route_decision="python_selected",
        response_authority="python",
        gate="gate1a_readonly_tools",
        sse_frame_count=4,
        tool_receipt_count=1,
        model_attempt_count=1,
        provider_request_count=1,
        expected_model_attempt_count=1,
        egress_tunnel_count=1,
        egress_discipline_mode="bounded_provider_tunnels",
        egress_evidence_status="observed_egress_evidence_present",
        max_provider_tunnels_per_model_attempt=2,
        egress_host_classes=("gemini_proxy",),
        egress_window_started_at="2026-05-24T02:31:35.000Z",
        egress_window_ended_at="2026-05-24T02:31:41.000Z",
        now_ms=1_779_200_003_000,
    )

    raw = json.loads(path.read_text(encoding="utf-8"))
    scope = next(iter(raw["scopes"].values()))
    record = scope["requests"][REQUEST_DIGEST]
    assert receipt.status == "recorded"
    assert record["status"] == "runner_completed"
    assert record["deliveryStatus"] == "served_to_client"
    assert record["deliveryReason"] == "served_to_client"
    assert record["deliveryRecordedAtMs"] == 1_779_200_003_000
    assert record["deliveryReceiptCount"] == 1
    assert record["bodyDigest"] == "sha256:" + "1" * 64
    assert record["routeDecision"] == "python_selected"
    assert record["responseAuthority"] == "python"
    assert record["gate"] == "gate1a_readonly_tools"
    assert record["sseFrameCount"] == 4
    assert record["toolReceiptCount"] == 1
    assert record["modelAttemptCount"] == 1
    assert record["providerRequestCount"] == 1
    assert record["egressTunnelCount"] == 1
    assert record["egressDisciplineMode"] == "bounded_provider_tunnels"
    assert record["maxProviderTunnelsPerModelAttempt"] == 2
    assert record["expectedEgressTunnelRange"] == {"min": 0, "max": 2}
    assert record["deliveryEvidenceStatus"] == "delivery_evidence_ok"
    assert scope["state"]["dailyGenerationRunsUsed"] == 1
    assert scope["state"]["dailyGenerationCostUsdUsed"] == 0.05


def test_counter_store_records_duplicate_delivery_receipt_without_budget_increment(
    tmp_path,
) -> None:
    path = tmp_path / "gate5b-shadow-counters.json"
    store = Gate5B4C3ShadowCounterStore(path)
    reservation = store.reserve(
        request_digest=REQUEST_DIGEST,
        shadow_generation_id="shadow_gen_delivery_002",
        selected_bot_digest=BOT_DIGEST,
        trusted_owner_user_id_digest=OWNER_DIGEST,
        environment="production",
        max_daily_generation_runs=10,
        max_daily_generation_cost_usd=0.50,
        max_concurrent_generation_runs=1,
        max_pending_generation_runs=1,
        cost_cap_usd=0.05,
        now_ms=1_779_200_000_000,
    )
    store.finish(reservation, status="runner_completed", reason="runner_completed")

    first = store.record_delivery_receipt(
        request_digest=REQUEST_DIGEST,
        selected_bot_digest=BOT_DIGEST,
        trusted_owner_user_id_digest=OWNER_DIGEST,
        environment="production",
        delivery_status="served_to_client",
        reason="served_to_client",
    )
    second = store.record_delivery_receipt(
        request_digest=REQUEST_DIGEST,
        selected_bot_digest=BOT_DIGEST,
        trusted_owner_user_id_digest=OWNER_DIGEST,
        environment="production",
        delivery_status="served_to_client",
        reason="served_to_client",
    )

    raw = json.loads(path.read_text(encoding="utf-8"))
    scope = next(iter(raw["scopes"].values()))
    record = scope["requests"][REQUEST_DIGEST]
    assert first.status == "recorded"
    assert second.status == "duplicate"
    assert record["deliveryStatus"] == "served_to_client"
    assert record["deliveryReceiptCount"] == 2
    assert record["deliveryDuplicateCount"] == 1
    assert scope["state"]["dailyGenerationRunsUsed"] == 1


def test_counter_store_records_gate1a_chat_proxy_fallback_without_prior_python_counter(
    tmp_path,
) -> None:
    path = tmp_path / "gate5b-shadow-counters.json"
    store = Gate5B4C3ShadowCounterStore(path)

    receipt = store.record_delivery_receipt(
        request_digest=REQUEST_DIGEST,
        selected_bot_digest=BOT_DIGEST,
        trusted_owner_user_id_digest=OWNER_DIGEST,
        environment="production",
        delivery_status="fallback_served",
        reason="python_error",
        body_digest="sha256:" + "1" * 64,
        route_decision="typescript_fallback",
        response_authority="typescript",
        gate="gate1a_readonly_tools",
        served_at="2026-05-25T18:00:00.000Z",
        fallback_reason="runner_error",
        python_attempted=True,
        python_counter_record_present=False,
        now_ms=1_779_200_003_000,
    )

    raw = json.loads(path.read_text(encoding="utf-8"))
    scope = next(iter(raw["scopes"].values()))
    record = scope["requests"][REQUEST_DIGEST]
    assert receipt.status == "recorded"
    assert record["status"] == "fallback_served"
    assert record["attemptEvidenceSource"] == "chat_proxy_fallback_receipt"
    assert record["pythonAttempted"] is True
    assert record["pythonCounterRecordPresent"] is False
    assert record["deliveryStatus"] == "fallback_served"
    assert record["bodyDigest"] == "sha256:" + "1" * 64
    assert record["servedAt"] == "2026-05-25T18:00:00.000Z"
    assert record["modelAttemptCount"] == 0
    assert record["providerRequestCount"] == 0
    assert scope["state"]["dailyGenerationRunsUsed"] == 0
    assert scope["state"]["inFlightGenerationRuns"] == 0
    assert scope["state"]["pendingGenerationRuns"] == 0

    evidence = store.validate_delivery_evidence(
        request_digest=REQUEST_DIGEST,
        selected_bot_digest=BOT_DIGEST,
        trusted_owner_user_id_digest=OWNER_DIGEST,
        environment="production",
        gate="gate1a_readonly_tools",
    )
    assert evidence.status == "passed"
    assert evidence.reason == "delivery_evidence_ok"
    assert evidence.attempt_evidence_source == "chat_proxy_fallback_receipt"
    assert evidence.delivery_status == "fallback_served"
    assert evidence.model_attempt_count == 0
    assert evidence.provider_request_count == 0


def test_counter_store_records_gate8_research_first_attempt_before_receipt(
    tmp_path,
) -> None:
    path = tmp_path / "gate5b-shadow-counters.json"
    store = Gate5B4C3ShadowCounterStore(path)
    source_ledger_digest = "sha256:" + "e" * 64
    output_digest = "sha256:" + "f" * 64

    state = store.record_gate8_research_first_canary_evidence(
        request_digest=REQUEST_DIGEST,
        selected_bot_digest=BOT_DIGEST,
        trusted_owner_user_id_digest=OWNER_DIGEST,
        environment="production",
        source_ledger_digest=source_ledger_digest,
        output_digest=output_digest,
        now_ms=1_779_200_003_000,
    )
    receipt = store.record_delivery_receipt(
        request_digest=REQUEST_DIGEST,
        selected_bot_digest=BOT_DIGEST,
        trusted_owner_user_id_digest=OWNER_DIGEST,
        environment="production",
        delivery_status="served_to_client",
        reason="served_to_client",
        body_digest="sha256:" + "1" * 64,
        route_decision="python_selected",
        response_authority="python",
        gate="gate8_selected_python_authority",
        output_digest=output_digest,
        python_attempted=True,
        python_counter_record_present=True,
        sse_frame_count=6,
        tool_receipt_count=0,
        model_attempt_count=0,
        provider_request_count=0,
        expected_model_attempt_count=0,
        now_ms=1_779_200_004_000,
    )

    raw = json.loads(path.read_text(encoding="utf-8"))
    scope = next(iter(raw["scopes"].values()))
    record = scope["requests"][REQUEST_DIGEST]
    assert state.daily_generation_runs_used == 0
    assert state.in_flight_generation_runs == 0
    assert receipt.status == "recorded"
    assert record["status"] == "research_first_selected_readonly_completed"
    assert record["attemptEvidenceSource"] == "python_counter_record"
    assert record["gate"] == "gate8_selected_python_authority"
    assert record["sourceLedgerDigest"] == source_ledger_digest
    assert record["outputDigest"] == output_digest
    assert record["deliveryStatus"] == "served_to_client"
    assert record["routeDecision"] == "python_selected"
    assert record["responseAuthority"] == "python"
    assert record["toolReceiptCount"] == 0
    assert record["modelAttemptCount"] == 0
    assert record["providerRequestCount"] == 0
    assert scope["state"]["dailyGenerationRunsUsed"] == 0
    assert scope["state"]["dailyGenerationCostUsdUsed"] == 0


def test_counter_store_missing_gate1a_attempt_evidence_fails_hard(tmp_path) -> None:
    store = Gate5B4C3ShadowCounterStore(tmp_path / "gate5b-shadow-counters.json")

    evidence = store.validate_delivery_evidence(
        request_digest=REQUEST_DIGEST,
        selected_bot_digest=BOT_DIGEST,
        trusted_owner_user_id_digest=OWNER_DIGEST,
        environment="production",
        gate="gate1a_readonly_tools",
    )

    assert evidence.status == "failed"
    assert evidence.reason == "missing_attempt_evidence"
    assert evidence.attempt_evidence_source == "missing_attempt_evidence"


def test_counter_store_does_not_reuse_prior_selected_scope_record_for_new_attempt(
    tmp_path,
) -> None:
    path = tmp_path / "gate5b-shadow-counters.json"
    store = Gate5B4C3ShadowCounterStore(path)
    reservation = store.reserve(
        request_digest=SECOND_REQUEST_DIGEST,
        shadow_generation_id="shadow_gen_prior_fallback",
        selected_bot_digest=BOT_DIGEST,
        trusted_owner_user_id_digest=OWNER_DIGEST,
        environment="production",
        max_daily_generation_runs=10,
        max_daily_generation_cost_usd=0.50,
        max_concurrent_generation_runs=1,
        max_pending_generation_runs=1,
        cost_cap_usd=0.05,
        now_ms=1_779_200_000_000,
    )
    store.finish(
        reservation,
        status="fallback_served",
        reason="runner_error",
        now_ms=1_779_200_001_000,
    )

    evidence = store.validate_delivery_evidence(
        request_digest=REQUEST_DIGEST,
        selected_bot_digest=BOT_DIGEST,
        trusted_owner_user_id_digest=OWNER_DIGEST,
        environment="production",
        gate="gate1a_readonly_tools",
    )

    raw = json.loads(path.read_text(encoding="utf-8"))
    scope = next(iter(raw["scopes"].values()))
    assert list(scope["requests"]) == [SECOND_REQUEST_DIGEST]
    assert evidence.status == "failed"
    assert evidence.reason == "missing_attempt_evidence"
    assert evidence.attempt_evidence_source == "missing_attempt_evidence"


def test_counter_store_evidence_validation_fails_runner_completed_without_receipt(
    tmp_path,
) -> None:
    path = tmp_path / "gate5b-shadow-counters.json"
    store = Gate5B4C3ShadowCounterStore(path)
    reservation = store.reserve(
        request_digest=REQUEST_DIGEST,
        shadow_generation_id="shadow_gen_missing_receipt",
        selected_bot_digest=BOT_DIGEST,
        trusted_owner_user_id_digest=OWNER_DIGEST,
        environment="production",
        max_daily_generation_runs=10,
        max_daily_generation_cost_usd=0.50,
        max_concurrent_generation_runs=1,
        max_pending_generation_runs=1,
        cost_cap_usd=0.05,
        now_ms=1_779_200_000_000,
    )
    store.finish(
        reservation,
        status="runner_completed",
        reason="runner_completed",
        report_digest="sha256:" + "d" * 64,
        now_ms=1_779_200_002_000,
    )

    evidence = store.validate_delivery_evidence(
        request_digest=REQUEST_DIGEST,
        selected_bot_digest=BOT_DIGEST,
        trusted_owner_user_id_digest=OWNER_DIGEST,
        environment="production",
        gate="gate1a_readonly_tools",
    )

    assert evidence.status == "failed"
    assert evidence.reason == "missing_delivery_receipt"
    assert evidence.delivery_status is None
    assert evidence.attempt_evidence_source == "python_counter_record"


def test_counter_store_allows_one_gemini_tunnel_per_model_attempt(tmp_path) -> None:
    path, store = _reserved_finished_store(tmp_path)

    receipt = store.record_delivery_receipt(
        request_digest=REQUEST_DIGEST,
        selected_bot_digest=BOT_DIGEST,
        trusted_owner_user_id_digest=OWNER_DIGEST,
        environment="production",
        delivery_status="served_to_client",
        reason="served_to_client",
        body_digest="sha256:" + "1" * 64,
        route_decision="python_selected",
        response_authority="python",
        gate="gate1a_readonly_tools",
        sse_frame_count=4,
        tool_receipt_count=1,
        model_attempt_count=1,
        provider_request_count=1,
        egress_tunnel_count=1,
        egress_discipline_mode="bounded_provider_tunnels",
        egress_evidence_status="observed_egress_evidence_present",
        max_provider_tunnels_per_model_attempt=2,
        egress_host_classes=("gemini_proxy",),
        egress_window_started_at="2026-05-24T02:31:35.000Z",
        egress_window_ended_at="2026-05-24T02:31:41.000Z",
        egress_correlation_digest="sha256:" + "9" * 64,
        now_ms=1_779_200_003_000,
    )
    evidence = store.validate_delivery_evidence(
        request_digest=REQUEST_DIGEST,
        selected_bot_digest=BOT_DIGEST,
        trusted_owner_user_id_digest=OWNER_DIGEST,
        environment="production",
        gate="gate1a_readonly_tools",
    )
    record = _request_record(path)

    assert receipt.status == "recorded"
    assert evidence.status == "passed"
    assert evidence.reason == "delivery_evidence_ok"
    assert record["egressTunnelCount"] == 1
    assert record["expectedEgressTunnelRange"] == {"min": 0, "max": 2}
    assert record["egressDisciplineMode"] == "bounded_provider_tunnels"
    assert record["egressDisciplineReason"] == "bounded_provider_tunnels_ok"
    assert record["egressHostClasses"] == ["gemini_proxy"]


def test_counter_store_allows_two_gemini_tunnels_in_bounded_provider_mode(
    tmp_path,
) -> None:
    path, store = _reserved_finished_store(tmp_path)

    store.record_delivery_receipt(
        request_digest=REQUEST_DIGEST,
        selected_bot_digest=BOT_DIGEST,
        trusted_owner_user_id_digest=OWNER_DIGEST,
        environment="production",
        delivery_status="served_to_client",
        reason="served_to_client",
        body_digest="sha256:" + "1" * 64,
        route_decision="python_selected",
        response_authority="python",
        gate="gate1a_readonly_tools",
        sse_frame_count=4,
        tool_receipt_count=2,
        model_attempt_count=1,
        provider_request_count=1,
        egress_tunnel_count=2,
        egress_discipline_mode="bounded_provider_tunnels",
        egress_evidence_status="observed_egress_evidence_present",
        max_provider_tunnels_per_model_attempt=2,
        egress_host_classes=("gemini_proxy",),
        egress_window_started_at="2026-05-24T02:31:35.000Z",
        egress_window_ended_at="2026-05-24T02:31:41.000Z",
        egress_correlation_digest="sha256:" + "9" * 64,
        now_ms=1_779_200_003_000,
    )
    evidence = store.validate_delivery_evidence(
        request_digest=REQUEST_DIGEST,
        selected_bot_digest=BOT_DIGEST,
        trusted_owner_user_id_digest=OWNER_DIGEST,
        environment="production",
        gate="gate1a_readonly_tools",
    )
    record = _request_record(path)

    assert record["deliveryEvidenceStatus"] == "delivery_evidence_ok"
    assert record["egressDisciplineReason"] == "bounded_provider_tunnels_ok"
    assert evidence.status == "passed"


def test_counter_store_fails_too_many_gemini_tunnels_in_bounded_provider_mode(
    tmp_path,
) -> None:
    path, store = _reserved_finished_store(tmp_path)

    store.record_delivery_receipt(
        request_digest=REQUEST_DIGEST,
        selected_bot_digest=BOT_DIGEST,
        trusted_owner_user_id_digest=OWNER_DIGEST,
        environment="production",
        delivery_status="served_to_client",
        reason="served_to_client",
        body_digest="sha256:" + "1" * 64,
        route_decision="python_selected",
        response_authority="python",
        gate="gate1a_readonly_tools",
        sse_frame_count=4,
        tool_receipt_count=1,
        model_attempt_count=1,
        provider_request_count=1,
        egress_tunnel_count=3,
        egress_discipline_mode="bounded_provider_tunnels",
        egress_evidence_status="observed_egress_evidence_present",
        max_provider_tunnels_per_model_attempt=2,
        egress_host_classes=("gemini_proxy",),
        egress_window_started_at="2026-05-24T02:31:35.000Z",
        egress_window_ended_at="2026-05-24T02:31:41.000Z",
        now_ms=1_779_200_003_000,
    )
    evidence = store.validate_delivery_evidence(
        request_digest=REQUEST_DIGEST,
        selected_bot_digest=BOT_DIGEST,
        trusted_owner_user_id_digest=OWNER_DIGEST,
        environment="production",
        gate="gate1a_readonly_tools",
    )
    record = _request_record(path)

    assert record["deliveryEvidenceStatus"] == "egress_count_anomaly"
    assert record["egressDisciplineReason"] == "egress_tunnel_count_exceeded"
    assert evidence.status == "failed"
    assert evidence.reason == "egress_count_anomaly"


def test_counter_store_fails_non_gemini_tunnel_host_class(tmp_path) -> None:
    path, store = _reserved_finished_store(tmp_path)

    store.record_delivery_receipt(
        request_digest=REQUEST_DIGEST,
        selected_bot_digest=BOT_DIGEST,
        trusted_owner_user_id_digest=OWNER_DIGEST,
        environment="production",
        delivery_status="served_to_client",
        reason="served_to_client",
        body_digest="sha256:" + "1" * 64,
        route_decision="python_selected",
        response_authority="python",
        gate="gate1a_readonly_tools",
        sse_frame_count=4,
        tool_receipt_count=1,
        model_attempt_count=1,
        provider_request_count=1,
        egress_tunnel_count=1,
        egress_discipline_mode="bounded_provider_tunnels",
        egress_evidence_status="observed_egress_evidence_present",
        max_provider_tunnels_per_model_attempt=2,
        egress_host_classes=("non_gemini_public",),
        egress_window_started_at="2026-05-24T02:31:35.000Z",
        egress_window_ended_at="2026-05-24T02:31:41.000Z",
        now_ms=1_779_200_003_000,
    )
    evidence = store.validate_delivery_evidence(
        request_digest=REQUEST_DIGEST,
        selected_bot_digest=BOT_DIGEST,
        trusted_owner_user_id_digest=OWNER_DIGEST,
        environment="production",
        gate="gate1a_readonly_tools",
    )
    record = _request_record(path)

    assert record["deliveryEvidenceStatus"] == "egress_policy_violation"
    assert record["egressDisciplineReason"] == "unexpected_egress_host_class"
    assert evidence.status == "failed"
    assert evidence.reason == "egress_policy_violation"


def test_counter_store_fails_tunnel_without_sanitized_host_class(tmp_path) -> None:
    path, store = _reserved_finished_store(tmp_path)

    store.record_delivery_receipt(
        request_digest=REQUEST_DIGEST,
        selected_bot_digest=BOT_DIGEST,
        trusted_owner_user_id_digest=OWNER_DIGEST,
        environment="production",
        delivery_status="served_to_client",
        reason="served_to_client",
        body_digest="sha256:" + "1" * 64,
        route_decision="python_selected",
        response_authority="python",
        gate="gate1a_readonly_tools",
        sse_frame_count=4,
        tool_receipt_count=1,
        model_attempt_count=1,
        provider_request_count=1,
        egress_tunnel_count=1,
        egress_discipline_mode="bounded_provider_tunnels",
        egress_evidence_status="observed_egress_evidence_present",
        max_provider_tunnels_per_model_attempt=2,
        egress_window_started_at="2026-05-24T02:31:35.000Z",
        egress_window_ended_at="2026-05-24T02:31:41.000Z",
        now_ms=1_779_200_003_000,
    )
    evidence = store.validate_delivery_evidence(
        request_digest=REQUEST_DIGEST,
        selected_bot_digest=BOT_DIGEST,
        trusted_owner_user_id_digest=OWNER_DIGEST,
        environment="production",
        gate="gate1a_readonly_tools",
    )
    record = _request_record(path)

    assert record["deliveryEvidenceStatus"] == "missing_observed_egress_evidence"
    assert record["egressDisciplineReason"] == "missing_observed_egress_evidence"
    assert evidence.status == "failed"
    assert evidence.reason == "missing_observed_egress_evidence"


def test_counter_store_fails_tunnel_outside_gate_window(tmp_path) -> None:
    path, store = _reserved_finished_store(tmp_path)

    store.record_delivery_receipt(
        request_digest=REQUEST_DIGEST,
        selected_bot_digest=BOT_DIGEST,
        trusted_owner_user_id_digest=OWNER_DIGEST,
        environment="production",
        delivery_status="served_to_client",
        reason="served_to_client",
        body_digest="sha256:" + "1" * 64,
        route_decision="python_selected",
        response_authority="python",
        gate="gate1a_readonly_tools",
        sse_frame_count=4,
        tool_receipt_count=1,
        model_attempt_count=1,
        provider_request_count=1,
        egress_tunnel_count=1,
        egress_discipline_mode="bounded_provider_tunnels",
        egress_evidence_status="observed_egress_evidence_present",
        max_provider_tunnels_per_model_attempt=2,
        egress_host_classes=("gemini_proxy",),
        egress_window_started_at="2026-05-24T02:31:35.000Z",
        egress_window_ended_at="2026-05-24T02:31:41.000Z",
        egress_outside_gate_window=True,
        now_ms=1_779_200_003_000,
    )
    evidence = store.validate_delivery_evidence(
        request_digest=REQUEST_DIGEST,
        selected_bot_digest=BOT_DIGEST,
        trusted_owner_user_id_digest=OWNER_DIGEST,
        environment="production",
        gate="gate1a_readonly_tools",
    )
    record = _request_record(path)

    assert record["deliveryEvidenceStatus"] == "egress_policy_violation"
    assert record["egressDisciplineReason"] == "egress_outside_gate_window"
    assert evidence.status == "failed"
    assert evidence.reason == "egress_policy_violation"


def test_counter_store_fails_tunnel_without_model_attempt(tmp_path) -> None:
    path, store = _reserved_finished_store(tmp_path)

    store.record_delivery_receipt(
        request_digest=REQUEST_DIGEST,
        selected_bot_digest=BOT_DIGEST,
        trusted_owner_user_id_digest=OWNER_DIGEST,
        environment="production",
        delivery_status="served_to_client",
        reason="served_to_client",
        body_digest="sha256:" + "1" * 64,
        route_decision="python_selected",
        response_authority="python",
        gate="gate1a_readonly_tools",
        sse_frame_count=4,
        tool_receipt_count=1,
        model_attempt_count=0,
        provider_request_count=0,
        egress_tunnel_count=1,
        egress_discipline_mode="bounded_provider_tunnels",
        egress_evidence_status="observed_egress_evidence_present",
        max_provider_tunnels_per_model_attempt=2,
        egress_host_classes=("gemini_proxy",),
        egress_window_started_at="2026-05-24T02:31:35.000Z",
        egress_window_ended_at="2026-05-24T02:31:41.000Z",
        now_ms=1_779_200_003_000,
    )
    evidence = store.validate_delivery_evidence(
        request_digest=REQUEST_DIGEST,
        selected_bot_digest=BOT_DIGEST,
        trusted_owner_user_id_digest=OWNER_DIGEST,
        environment="production",
        gate="gate1a_readonly_tools",
    )
    record = _request_record(path)

    assert record["deliveryEvidenceStatus"] == "egress_without_model_attempt"
    assert evidence.status == "failed"
    assert evidence.reason == "egress_without_model_attempt"


def test_counter_store_fails_model_attempt_without_egress_policy(tmp_path) -> None:
    path, store = _reserved_finished_store(tmp_path)

    store.record_delivery_receipt(
        request_digest=REQUEST_DIGEST,
        selected_bot_digest=BOT_DIGEST,
        trusted_owner_user_id_digest=OWNER_DIGEST,
        environment="production",
        delivery_status="served_to_client",
        reason="served_to_client",
        body_digest="sha256:" + "1" * 64,
        route_decision="python_selected",
        response_authority="python",
        gate="gate1a_readonly_tools",
        sse_frame_count=4,
        tool_receipt_count=1,
        model_attempt_count=1,
        provider_request_count=1,
        egress_tunnel_count=1,
        egress_evidence_status="observed_egress_evidence_present",
        now_ms=1_779_200_003_000,
    )
    evidence = store.validate_delivery_evidence(
        request_digest=REQUEST_DIGEST,
        selected_bot_digest=BOT_DIGEST,
        trusted_owner_user_id_digest=OWNER_DIGEST,
        environment="production",
        gate="gate1a_readonly_tools",
    )
    record = _request_record(path)

    assert record["deliveryEvidenceStatus"] == "missing_egress_policy"
    assert evidence.status == "failed"
    assert evidence.reason == "missing_egress_policy"


def test_counter_store_strict_single_tunnel_mode_fails_two_tunnels(tmp_path) -> None:
    path, store = _reserved_finished_store(tmp_path)

    store.record_delivery_receipt(
        request_digest=REQUEST_DIGEST,
        selected_bot_digest=BOT_DIGEST,
        trusted_owner_user_id_digest=OWNER_DIGEST,
        environment="production",
        delivery_status="served_to_client",
        reason="served_to_client",
        body_digest="sha256:" + "1" * 64,
        route_decision="python_selected",
        response_authority="python",
        gate="gate1a_readonly_tools",
        sse_frame_count=4,
        tool_receipt_count=1,
        model_attempt_count=1,
        provider_request_count=1,
        egress_tunnel_count=2,
        egress_discipline_mode="strict_single_tunnel",
        egress_evidence_status="observed_egress_evidence_present",
        max_provider_tunnels_per_model_attempt=1,
        egress_host_classes=("gemini_proxy",),
        egress_window_started_at="2026-05-24T02:31:35.000Z",
        egress_window_ended_at="2026-05-24T02:31:41.000Z",
        now_ms=1_779_200_003_000,
    )
    evidence = store.validate_delivery_evidence(
        request_digest=REQUEST_DIGEST,
        selected_bot_digest=BOT_DIGEST,
        trusted_owner_user_id_digest=OWNER_DIGEST,
        environment="production",
        gate="gate1a_readonly_tools",
    )
    record = _request_record(path)

    assert record["deliveryEvidenceStatus"] == "egress_count_anomaly"
    assert record["egressDisciplineReason"] == "strict_single_tunnel_exceeded"
    assert evidence.status == "failed"
    assert evidence.reason == "egress_count_anomaly"


def test_counter_store_records_no_tool_invocation_independently_from_egress(
    tmp_path,
) -> None:
    path, store = _reserved_finished_store(tmp_path)

    receipt = store.record_delivery_receipt(
        request_digest=REQUEST_DIGEST,
        selected_bot_digest=BOT_DIGEST,
        trusted_owner_user_id_digest=OWNER_DIGEST,
        environment="production",
        delivery_status="served_to_client",
        reason="served_to_client",
        body_digest="sha256:" + "1" * 64,
        route_decision="python_selected",
        response_authority="python",
        gate="gate1a_readonly_tools",
        sse_frame_count=4,
        tool_receipt_count=0,
        model_attempt_count=1,
        provider_request_count=1,
        egress_tunnel_count=1,
        egress_discipline_mode="bounded_provider_tunnels",
        egress_evidence_status="observed_egress_evidence_present",
        max_provider_tunnels_per_model_attempt=2,
        egress_host_classes=("gemini_proxy",),
        egress_window_started_at="2026-05-24T02:31:35.000Z",
        egress_window_ended_at="2026-05-24T02:31:41.000Z",
        now_ms=1_779_200_003_000,
    )
    evidence = store.validate_delivery_evidence(
        request_digest=REQUEST_DIGEST,
        selected_bot_digest=BOT_DIGEST,
        trusted_owner_user_id_digest=OWNER_DIGEST,
        environment="production",
        gate="gate1a_readonly_tools",
    )
    record = _request_record(path)

    assert receipt.status == "recorded"
    assert record["deliveryEvidenceStatus"] == "no_tool_invocation"
    assert record["egressDisciplineReason"] == "bounded_provider_tunnels_ok"
    assert record["toolEvidenceStatus"] == "no_tool_invocation"
    assert evidence.status == "failed"
    assert evidence.reason == "no_tool_invocation"


def test_counter_store_egress_evidence_rejects_raw_or_secret_values(tmp_path) -> None:
    _path, store = _reserved_finished_store(tmp_path)

    unsafe_values = [
        {"egress_host_classes": ("generativelanguage.googleapis.com:443",)},
        {"egress_host_classes": ("Bearer-token",)},
        {"egress_correlation_digest": "/private/path"},
    ]
    for kwargs in unsafe_values:
        try:
            store.record_delivery_receipt(
                request_digest=REQUEST_DIGEST,
                selected_bot_digest=BOT_DIGEST,
                trusted_owner_user_id_digest=OWNER_DIGEST,
                environment="production",
                delivery_status="served_to_client",
                reason="served_to_client",
                body_digest="sha256:" + "1" * 64,
                route_decision="python_selected",
                response_authority="python",
                gate="gate1a_readonly_tools",
                sse_frame_count=4,
                tool_receipt_count=1,
                model_attempt_count=1,
                provider_request_count=1,
                egress_tunnel_count=1,
                egress_discipline_mode="bounded_provider_tunnels",
        egress_evidence_status="observed_egress_evidence_present",
                max_provider_tunnels_per_model_attempt=2,
                egress_window_started_at="2026-05-24T02:31:35.000Z",
                egress_window_ended_at="2026-05-24T02:31:41.000Z",
                **kwargs,
            )
        except ValueError:
            continue
        raise AssertionError("unsafe egress evidence value was accepted")


def test_counter_store_two_distinct_requests_get_two_records(tmp_path) -> None:
    path = tmp_path / "gate5b-shadow-counters.json"
    store = Gate5B4C3ShadowCounterStore(path)

    for index, digest_char in enumerate(("c", "d")):
        request_digest = "sha256:" + digest_char * 64
        reservation = store.reserve(
            request_digest=request_digest,
            shadow_generation_id=f"shadow_gen_{index}",
            selected_bot_digest=BOT_DIGEST,
            trusted_owner_user_id_digest=OWNER_DIGEST,
            environment="production",
            max_daily_generation_runs=10,
            max_daily_generation_cost_usd=0.50,
            max_concurrent_generation_runs=1,
            max_pending_generation_runs=1,
            cost_cap_usd=0.05,
            now_ms=1_779_200_000_000 + index,
        )
        assert reservation.status == "reserved"
        store.finish(
            reservation,
            status="runner_completed",
            reason="runner_completed",
            report_digest="sha256:" + f"{index + 1:x}" * 64,
            now_ms=1_779_200_002_000 + index,
        )

    raw = json.loads(path.read_text(encoding="utf-8"))
    scope = next(iter(raw["scopes"].values()))
    assert len(scope["requests"]) == 2
    assert scope["state"]["dailyGenerationRunsUsed"] == 2


def test_counter_store_blocks_after_daily_cap_without_incrementing(tmp_path) -> None:
    store = Gate5B4C3ShadowCounterStore(tmp_path / "gate5b-shadow-counters.json")

    first = store.reserve(
        request_digest=REQUEST_DIGEST,
        shadow_generation_id="shadow_gen_001",
        selected_bot_digest=BOT_DIGEST,
        trusted_owner_user_id_digest=OWNER_DIGEST,
        environment="production",
        max_daily_generation_runs=1,
        max_daily_generation_cost_usd=0.05,
        max_concurrent_generation_runs=1,
        max_pending_generation_runs=1,
        cost_cap_usd=0.05,
        now_ms=1_779_200_000_000,
    )
    store.finish(first, status="completed", reason="runner_completed", now_ms=1_779_200_001_000)

    blocked = store.reserve(
        request_digest="sha256:" + "f" * 64,
        shadow_generation_id="shadow_gen_002",
        selected_bot_digest=BOT_DIGEST,
        trusted_owner_user_id_digest=OWNER_DIGEST,
        environment="production",
        max_daily_generation_runs=1,
        max_daily_generation_cost_usd=0.05,
        max_concurrent_generation_runs=1,
        max_pending_generation_runs=1,
        cost_cap_usd=0.05,
        now_ms=1_779_200_002_000,
    )

    assert blocked.status == "blocked"
    assert blocked.reason == "daily_cap_exhausted"
    assert blocked.should_invoke_runner is False
    assert blocked.counter_state.daily_generation_runs_used == 1
    assert blocked.counter_state.daily_generation_cost_usd_used == 0.05


def test_gate1a_selected_attempt_preflight_allows_fresh_digest(tmp_path) -> None:
    store = Gate5B4C3ShadowCounterStore(tmp_path / "gate5b-shadow-counters.json")

    preflight = store.preflight_gate1a_selected_attempt(
        request_digest=REQUEST_DIGEST,
        selected_bot_digest=BOT_DIGEST,
        trusted_owner_user_id_digest=OWNER_DIGEST,
        environment="production",
        max_daily_generation_runs=1,
        max_daily_generation_cost_usd=0.05,
        max_concurrent_generation_runs=1,
        max_pending_generation_runs=1,
        cost_cap_usd=0.05,
        fallback_receipt_path_available=True,
        now_ms=1_779_200_000_000,
    )

    assert preflight.status == "ready"
    assert preflight.reason == "fresh_attempt_ready"
    assert preflight.counter_store_writable is True
    assert preflight.fallback_receipt_path_available is True


def test_gate1a_selected_attempt_preflight_blocks_exhausted_budget(tmp_path) -> None:
    path = tmp_path / "gate5b-shadow-counters.json"
    store = Gate5B4C3ShadowCounterStore(path)
    first = store.reserve(
        request_digest=REQUEST_DIGEST,
        shadow_generation_id="shadow_gen_budget",
        selected_bot_digest=BOT_DIGEST,
        trusted_owner_user_id_digest=OWNER_DIGEST,
        environment="production",
        max_daily_generation_runs=1,
        max_daily_generation_cost_usd=0.05,
        max_concurrent_generation_runs=1,
        max_pending_generation_runs=1,
        cost_cap_usd=0.05,
        now_ms=1_779_200_000_000,
    )
    store.finish(first, status="completed", reason="runner_completed", now_ms=1_779_200_001_000)

    preflight = Gate5B4C3ShadowCounterStore(path).preflight_gate1a_selected_attempt(
        request_digest=SECOND_REQUEST_DIGEST,
        selected_bot_digest=BOT_DIGEST,
        trusted_owner_user_id_digest=OWNER_DIGEST,
        environment="production",
        max_daily_generation_runs=1,
        max_daily_generation_cost_usd=0.05,
        max_concurrent_generation_runs=1,
        max_pending_generation_runs=1,
        cost_cap_usd=0.05,
        fallback_receipt_path_available=True,
        now_ms=1_779_200_002_000,
    )

    assert preflight.status == "blocked"
    assert preflight.reason == "budget_exhausted"


def test_gate1a_selected_attempt_preflight_blocks_digest_collision(tmp_path) -> None:
    path = tmp_path / "gate5b-shadow-counters.json"
    store = Gate5B4C3ShadowCounterStore(path)
    first = store.reserve(
        request_digest=REQUEST_DIGEST,
        shadow_generation_id="shadow_gen_collision",
        selected_bot_digest=BOT_DIGEST,
        trusted_owner_user_id_digest=OWNER_DIGEST,
        environment="production",
        max_daily_generation_runs=2,
        max_daily_generation_cost_usd=0.10,
        max_concurrent_generation_runs=1,
        max_pending_generation_runs=1,
        cost_cap_usd=0.05,
        now_ms=1_779_200_000_000,
    )
    store.finish(first, status="fallback_served", reason="typescript_fallback")

    preflight = Gate5B4C3ShadowCounterStore(path).preflight_gate1a_selected_attempt(
        request_digest=REQUEST_DIGEST,
        selected_bot_digest=BOT_DIGEST,
        trusted_owner_user_id_digest=OWNER_DIGEST,
        environment="production",
        max_daily_generation_runs=2,
        max_daily_generation_cost_usd=0.10,
        max_concurrent_generation_runs=1,
        max_pending_generation_runs=1,
        cost_cap_usd=0.05,
        fallback_receipt_path_available=True,
        now_ms=1_779_200_002_000,
    )

    assert preflight.status == "blocked"
    assert preflight.reason == "idempotency_collision"


def test_gate1a_selected_attempt_preflight_blocks_unwritable_counter_store(
    tmp_path,
) -> None:
    class UnreadableCounterStore(Gate5B4C3ShadowCounterStore):
        def _load(self):  # type: ignore[override]
            raise PermissionError("permission denied")

    preflight = UnreadableCounterStore(
        tmp_path / "gate5b-shadow-counters.json"
    ).preflight_gate1a_selected_attempt(
        request_digest=REQUEST_DIGEST,
        selected_bot_digest=BOT_DIGEST,
        trusted_owner_user_id_digest=OWNER_DIGEST,
        environment="production",
        max_daily_generation_runs=1,
        max_daily_generation_cost_usd=0.05,
        max_concurrent_generation_runs=1,
        max_pending_generation_runs=1,
        cost_cap_usd=0.05,
        fallback_receipt_path_available=True,
        now_ms=1_779_200_000_000,
    )

    assert preflight.status == "blocked"
    assert preflight.reason == "counter_store_unwritable"
    assert preflight.counter_store_writable is False


def test_gate1a_selected_attempt_preflight_blocks_inconsistent_pending_state(
    tmp_path,
) -> None:
    path = tmp_path / "gate5b-shadow-counters.json"
    store = Gate5B4C3ShadowCounterStore(path)
    store.reserve(
        request_digest=REQUEST_DIGEST,
        shadow_generation_id="shadow_gen_inconsistent",
        selected_bot_digest=BOT_DIGEST,
        trusted_owner_user_id_digest=OWNER_DIGEST,
        environment="production",
        max_daily_generation_runs=10,
        max_daily_generation_cost_usd=0.50,
        max_concurrent_generation_runs=5,
        max_pending_generation_runs=5,
        cost_cap_usd=0.05,
        now_ms=1_779_200_000_000,
    )
    raw = json.loads(path.read_text(encoding="utf-8"))
    scope = next(iter(raw["scopes"].values()))
    scope["state"]["pendingGenerationRuns"] = 2
    path.write_text(json.dumps(raw), encoding="utf-8")

    preflight = Gate5B4C3ShadowCounterStore(path).preflight_gate1a_selected_attempt(
        request_digest=SECOND_REQUEST_DIGEST,
        selected_bot_digest=BOT_DIGEST,
        trusted_owner_user_id_digest=OWNER_DIGEST,
        environment="production",
        max_daily_generation_runs=10,
        max_daily_generation_cost_usd=0.50,
        max_concurrent_generation_runs=5,
        max_pending_generation_runs=5,
        cost_cap_usd=0.05,
        fallback_receipt_path_available=True,
        now_ms=1_779_200_001_000,
    )

    assert preflight.status == "blocked"
    assert preflight.reason == "pending_inflight_inconsistent"


def test_gate1a_selected_attempt_preflight_blocks_when_fallback_receipt_path_unavailable(
    tmp_path,
) -> None:
    store = Gate5B4C3ShadowCounterStore(tmp_path / "gate5b-shadow-counters.json")

    preflight = store.preflight_gate1a_selected_attempt(
        request_digest=REQUEST_DIGEST,
        selected_bot_digest=BOT_DIGEST,
        trusted_owner_user_id_digest=OWNER_DIGEST,
        environment="production",
        max_daily_generation_runs=1,
        max_daily_generation_cost_usd=0.05,
        max_concurrent_generation_runs=1,
        max_pending_generation_runs=1,
        cost_cap_usd=0.05,
        fallback_receipt_path_available=False,
        now_ms=1_779_200_000_000,
    )

    assert preflight.status == "blocked"
    assert preflight.reason == "fallback_receipt_path_unavailable"


def test_counter_store_releases_stale_in_flight_on_restart(tmp_path) -> None:
    path = tmp_path / "gate5b-shadow-counters.json"
    store = Gate5B4C3ShadowCounterStore(path, stale_after_ms=1_000)

    store.reserve(
        request_digest=REQUEST_DIGEST,
        shadow_generation_id="shadow_gen_001",
        selected_bot_digest=BOT_DIGEST,
        trusted_owner_user_id_digest=OWNER_DIGEST,
        environment="production",
        max_daily_generation_runs=10,
        max_daily_generation_cost_usd=0.50,
        max_concurrent_generation_runs=1,
        max_pending_generation_runs=1,
        cost_cap_usd=0.05,
        now_ms=1_779_200_000_000,
    )

    restarted = Gate5B4C3ShadowCounterStore(path, stale_after_ms=1_000)
    next_reservation = restarted.reserve(
        request_digest="sha256:" + "f" * 64,
        shadow_generation_id="shadow_gen_002",
        selected_bot_digest=BOT_DIGEST,
        trusted_owner_user_id_digest=OWNER_DIGEST,
        environment="production",
        max_daily_generation_runs=10,
        max_daily_generation_cost_usd=0.50,
        max_concurrent_generation_runs=1,
        max_pending_generation_runs=1,
        cost_cap_usd=0.05,
        now_ms=1_779_200_002_000,
    )

    assert next_reservation.status == "reserved"
    assert next_reservation.counter_state.stale_in_flight_released == 1
    assert next_reservation.counter_state.in_flight_generation_runs == 1
    assert next_reservation.counter_state.pending_generation_runs == 1


def test_counter_store_treats_stale_same_request_as_idempotent_replay(tmp_path) -> None:
    path = tmp_path / "gate5b-shadow-counters.json"
    store = Gate5B4C3ShadowCounterStore(path, stale_after_ms=1_000)

    store.reserve(
        request_digest=REQUEST_DIGEST,
        shadow_generation_id="shadow_gen_001",
        selected_bot_digest=BOT_DIGEST,
        trusted_owner_user_id_digest=OWNER_DIGEST,
        environment="production",
        max_daily_generation_runs=10,
        max_daily_generation_cost_usd=0.50,
        max_concurrent_generation_runs=1,
        max_pending_generation_runs=1,
        cost_cap_usd=0.05,
        now_ms=1_779_200_000_000,
    )

    duplicate = Gate5B4C3ShadowCounterStore(path, stale_after_ms=1_000).reserve(
        request_digest=REQUEST_DIGEST,
        shadow_generation_id="shadow_gen_001_retry",
        selected_bot_digest=BOT_DIGEST,
        trusted_owner_user_id_digest=OWNER_DIGEST,
        environment="production",
        max_daily_generation_runs=10,
        max_daily_generation_cost_usd=0.50,
        max_concurrent_generation_runs=1,
        max_pending_generation_runs=1,
        cost_cap_usd=0.05,
        now_ms=1_779_200_002_000,
    )

    assert duplicate.status == "duplicate_replay"
    assert duplicate.should_invoke_runner is False
    assert duplicate.counter_state.daily_generation_runs_used == 1
    assert duplicate.counter_state.daily_generation_cost_usd_used == 0.05
    assert duplicate.counter_state.in_flight_generation_runs == 0
    assert duplicate.counter_state.pending_generation_runs == 0
    assert duplicate.counter_state.stale_in_flight_released == 1


def test_counter_store_uses_advisory_lock_for_read_modify_write() -> None:
    import inspect
    from magi_agent.shadow import gate5b4c3_shadow_counter_store

    source = inspect.getsource(gate5b4c3_shadow_counter_store.Gate5B4C3ShadowCounterStore)

    assert "fcntl.flock" in source
    assert "@_with_exclusive_lock" in source


def test_counter_store_pending_cap_is_enforced(tmp_path) -> None:
    store = Gate5B4C3ShadowCounterStore(tmp_path / "gate5b-shadow-counters.json")

    first = store.reserve(
        request_digest=REQUEST_DIGEST,
        shadow_generation_id="shadow_gen_001",
        selected_bot_digest=BOT_DIGEST,
        trusted_owner_user_id_digest=OWNER_DIGEST,
        environment="production",
        max_daily_generation_runs=10,
        max_daily_generation_cost_usd=0.50,
        max_concurrent_generation_runs=10,
        max_pending_generation_runs=1,
        cost_cap_usd=0.05,
        now_ms=1_779_200_000_000,
    )
    blocked = store.reserve(
        request_digest="sha256:" + "f" * 64,
        shadow_generation_id="shadow_gen_002",
        selected_bot_digest=BOT_DIGEST,
        trusted_owner_user_id_digest=OWNER_DIGEST,
        environment="production",
        max_daily_generation_runs=10,
        max_daily_generation_cost_usd=0.50,
        max_concurrent_generation_runs=10,
        max_pending_generation_runs=1,
        cost_cap_usd=0.05,
        now_ms=1_779_200_001_000,
    )

    assert first.status == "reserved"
    assert first.counter_state.pending_generation_runs == 1
    assert blocked.status == "blocked"
    assert blocked.reason == "pending_cap_exhausted"
    assert blocked.counter_state.pending_generation_runs == 1
