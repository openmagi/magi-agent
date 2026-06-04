"""A4 — Local delivery + [SILENT] suppression + recency-aware target resolution.

TDD: RED → GREEN → REFACTOR

Behavior matrix under test:
  - CronTurnResult carries output text (backward-compatible default empty).
  - [SILENT] exact (stripped+upper == "[SILENT]") → suppressed + audit receipt saved.
  - [SILENT] mixed with other text → delivered normally (NOT suppressed).
  - Target resolution: recent active session wins over explicit target.
  - Target resolution: no active session → explicit target used.
  - Target resolution: neither → local default sink.
  - Gate OFF / shadow ON → delivery never called.
  - Gate live → delivery called, returns DeliveryReceipt.
  - DeliveryReceipt: status delivered | suppressed_silent | skipped.
  - Evidence redaction: output content not stored raw; length+hash stored.
  - Module import purity: scheduler_delivery has no network/ADK top-level imports.
"""
from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Any

import pytest


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

def _now_dt(ms: int = 1_000_000) -> datetime:
    return datetime.fromtimestamp(ms / 1000, tz=UTC)


def _make_lease(*, owner_digest: str = "owner:test-abc", now_ms: int = 1_000_000) -> Any:
    from magi_agent.harness.scheduler_runtime import SchedulerLease

    return SchedulerLease(
        leaseId="lease:test-abc",
        ownerDigest=owner_digest,
        acquiredAt=now_ms - 1000,
        expiresAt=now_ms + 60_000,
    )


def _make_source(jobs: list[dict[str, Any]]) -> Any:
    from magi_agent.harness.scheduler_executor import InMemoryJobSource, ScheduledJobRecord

    records: list[ScheduledJobRecord] = []
    for j in jobs:
        records.append(
            ScheduledJobRecord(
                jobId=j["job_id"],
                scheduleExpr=j["schedule_expr"],
                lastFire=j.get("last_fire"),
                nextRun=datetime.fromtimestamp(j["next_run_ms"] / 1000, tz=UTC),
            )
        )
    return InMemoryJobSource(records)


class _FakeCronTurnRunner:
    """Records each turn plan it is asked to run; returns a configurable result."""

    def __init__(self, *, status: str = "completed", output: str = "Turn output text.") -> None:
        self.calls: list[Any] = []
        self._status = status
        self._output = output

    async def run_turn(self, plan: Any) -> Any:
        from magi_agent.harness.scheduler_job_execution import CronTurnResult

        self.calls.append(plan)
        return CronTurnResult(
            status=self._status,  # type: ignore[arg-type]
            jobId=plan.job_id,
            runnerInvoked=True,
            output=self._output,
        )


# ---------------------------------------------------------------------------
# 1.  CronTurnResult.output field — backward-compatible
# ---------------------------------------------------------------------------

def test_cron_turn_result_has_output_field() -> None:
    """CronTurnResult gains an `output` field defaulting to empty string."""
    from magi_agent.harness.scheduler_job_execution import CronTurnResult

    result = CronTurnResult(status="completed", jobId="job:abc", runnerInvoked=True)
    assert hasattr(result, "output")
    assert result.output == ""


def test_cron_turn_result_output_preserved() -> None:
    """Output text is preserved on the result."""
    from magi_agent.harness.scheduler_job_execution import CronTurnResult

    result = CronTurnResult(
        status="completed", jobId="job:abc", runnerInvoked=True, output="Hello world!"
    )
    assert result.output == "Hello world!"


def test_cron_turn_result_is_frozen() -> None:
    """CronTurnResult stays frozen after adding output."""
    from magi_agent.harness.scheduler_job_execution import CronTurnResult

    result = CronTurnResult(status="completed", jobId="job:abc", runnerInvoked=True, output="x")
    with pytest.raises(Exception):
        result.output = "mutated"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# 2.  SILENT_MARKER constant + is_silent() logic
# ---------------------------------------------------------------------------

def test_silent_marker_constant() -> None:
    from magi_agent.harness.scheduler_delivery import SILENT_MARKER

    assert SILENT_MARKER == "[SILENT]"


def test_is_silent_exact_match() -> None:
    """[SILENT] alone (stripped) is suppressed."""
    from magi_agent.harness.scheduler_delivery import is_silent_output

    assert is_silent_output("[SILENT]") is True
    assert is_silent_output("  [SILENT]  ") is True
    assert is_silent_output("[silent]") is True
    assert is_silent_output("\t[SILENT]\n") is True


def test_is_silent_mixed_with_text_is_not_silent() -> None:
    """[SILENT] embedded in real content must NOT suppress."""
    from magi_agent.harness.scheduler_delivery import is_silent_output

    assert is_silent_output("[SILENT] extra text") is False
    assert is_silent_output("prefix [SILENT]") is False
    assert is_silent_output("[SILENT]\nmore content") is False
    assert is_silent_output("") is False


# ---------------------------------------------------------------------------
# 3.  DeliveryTarget + LocalLogDeliverySink
# ---------------------------------------------------------------------------

def test_delivery_target_is_importable() -> None:
    from magi_agent.harness.scheduler_delivery import DeliveryTarget  # noqa: F401


def test_local_log_delivery_sink_is_delivery_target() -> None:
    from magi_agent.harness.scheduler_delivery import DeliveryTarget, LocalLogDeliverySink

    sink = LocalLogDeliverySink()
    assert isinstance(sink, DeliveryTarget)


def test_local_log_delivery_sink_is_frozen() -> None:
    """LocalLogDeliverySink must be a frozen model."""
    from magi_agent.harness.scheduler_delivery import LocalLogDeliverySink

    sink = LocalLogDeliverySink()
    with pytest.raises(Exception):
        sink.sink_id = "mutated"  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# 4.  deliver() — normal delivery path
# ---------------------------------------------------------------------------

def test_deliver_returns_receipt_delivered() -> None:
    from magi_agent.harness.scheduler_job_execution import CronTurnResult
    from magi_agent.harness.scheduler_delivery import LocalLogDeliverySink, deliver

    result = CronTurnResult(
        status="completed", jobId="job:deliver-001", runnerInvoked=True, output="Some result."
    )
    sink = LocalLogDeliverySink()
    receipt = deliver(result, target=sink)
    assert receipt.status == "delivered"
    assert receipt.job_id == "job:deliver-001"


def test_deliver_receipt_is_frozen() -> None:
    from magi_agent.harness.scheduler_job_execution import CronTurnResult
    from magi_agent.harness.scheduler_delivery import DeliveryReceipt, LocalLogDeliverySink, deliver

    result = CronTurnResult(
        status="completed", jobId="job:frozen-001", runnerInvoked=True, output="x"
    )
    receipt = deliver(result, target=LocalLogDeliverySink())
    assert isinstance(receipt, DeliveryReceipt)
    with pytest.raises(Exception):
        receipt.status = "mutated"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# 5.  deliver() — [SILENT] suppression
# ---------------------------------------------------------------------------

def test_deliver_silent_exact_returns_suppressed() -> None:
    """Exact [SILENT] output → suppressed_silent, but output still in receipt for audit."""
    from magi_agent.harness.scheduler_job_execution import CronTurnResult
    from magi_agent.harness.scheduler_delivery import LocalLogDeliverySink, deliver

    result = CronTurnResult(
        status="completed", jobId="job:silent-001", runnerInvoked=True, output="[SILENT]"
    )
    receipt = deliver(result, target=LocalLogDeliverySink())
    assert receipt.status == "suppressed_silent"
    # Output length/hash recorded for audit — content NOT stored raw.
    assert receipt.output_length == len("[SILENT]")
    assert receipt.output_digest is not None
    assert receipt.output_digest.startswith("sha256:")


def test_deliver_silent_mixed_returns_delivered() -> None:
    """[SILENT] mixed with other text → normal delivery, NOT suppressed."""
    from magi_agent.harness.scheduler_job_execution import CronTurnResult
    from magi_agent.harness.scheduler_delivery import LocalLogDeliverySink, deliver

    result = CronTurnResult(
        status="completed",
        jobId="job:silent-mixed-001",
        runnerInvoked=True,
        output="[SILENT] but also real content",
    )
    receipt = deliver(result, target=LocalLogDeliverySink())
    assert receipt.status == "delivered"


def test_deliver_silent_whitespace_variants_suppressed() -> None:
    """Whitespace-padded [SILENT] variants are suppressed."""
    from magi_agent.harness.scheduler_job_execution import CronTurnResult
    from magi_agent.harness.scheduler_delivery import LocalLogDeliverySink, deliver

    for variant in ("  [SILENT]", "[SILENT]  ", "  [silent]  "):
        result = CronTurnResult(
            status="completed", jobId="job:silent-ws", runnerInvoked=True, output=variant
        )
        receipt = deliver(result, target=LocalLogDeliverySink())
        assert receipt.status == "suppressed_silent", f"Expected suppressed for {variant!r}"


# ---------------------------------------------------------------------------
# 6.  DeliveryReceipt — evidence redaction
# ---------------------------------------------------------------------------

def test_delivery_receipt_does_not_store_raw_output() -> None:
    """Raw output text must NOT appear anywhere in the receipt model dump."""
    from magi_agent.harness.scheduler_job_execution import CronTurnResult
    from magi_agent.harness.scheduler_delivery import LocalLogDeliverySink, deliver

    secret_output = "Secret output text: sk-abcdef123456"
    result = CronTurnResult(
        status="completed", jobId="job:redact-001", runnerInvoked=True, output=secret_output
    )
    receipt = deliver(result, target=LocalLogDeliverySink())
    dump = str(receipt.model_dump(by_alias=True))
    assert secret_output not in dump, "Raw output text must NOT appear in receipt"


def test_delivery_receipt_stores_length_and_digest() -> None:
    """Receipt stores output length and sha256 digest for audit (not raw text)."""
    from magi_agent.harness.scheduler_job_execution import CronTurnResult
    from magi_agent.harness.scheduler_delivery import LocalLogDeliverySink, deliver

    output = "A known output string for digest verification."
    expected_digest = "sha256:" + hashlib.sha256(output.encode()).hexdigest()

    result = CronTurnResult(
        status="completed", jobId="job:digest-001", runnerInvoked=True, output=output
    )
    receipt = deliver(result, target=LocalLogDeliverySink())
    assert receipt.output_length == len(output)
    assert receipt.output_digest == expected_digest


def test_delivery_receipt_evidence_has_redacted_fields() -> None:
    """The EvidenceRecord stored in the receipt must use length/hash not raw text."""
    from magi_agent.harness.scheduler_job_execution import CronTurnResult
    from magi_agent.harness.scheduler_delivery import LocalLogDeliverySink, deliver

    secret_output = "TOP SECRET DATA sk-live-abcdef"
    result = CronTurnResult(
        status="completed", jobId="job:ev-redact-001", runnerInvoked=True, output=secret_output
    )
    receipt = deliver(result, target=LocalLogDeliverySink())
    assert receipt.evidence is not None
    ev_fields = dict(receipt.evidence.fields)
    # Raw text must not appear in evidence fields.
    assert secret_output not in str(ev_fields)
    # But redacted metadata (length, digest) should be present.
    assert "outputLength" in ev_fields
    assert "outputDigest" in ev_fields


# ---------------------------------------------------------------------------
# 7.  resolve_delivery_target() — pure function, recency-wins rule
# ---------------------------------------------------------------------------

def _make_job(job_id: str = "job:resolve-001") -> Any:
    from magi_agent.harness.scheduler_executor import ScheduledJobRecord

    return ScheduledJobRecord(
        jobId=job_id,
        scheduleExpr="every 10m",
        lastFire=None,
        nextRun=datetime.fromtimestamp(1_000_000 / 1000, tz=UTC),
    )


def test_resolve_target_recent_session_wins_over_explicit() -> None:
    """Recent last_active_session (non-None) beats an explicit_target."""
    from magi_agent.harness.scheduler_delivery import (
        LocalLogDeliverySink,
        SessionAwareDeliveryTarget,
        resolve_delivery_target,
    )

    job = _make_job()
    # Provide both a recent session and an explicit target.
    explicit = LocalLogDeliverySink()
    recent_session = SessionAwareDeliveryTarget(session_id="session:active-123")
    target = resolve_delivery_target(
        job, last_active_session=recent_session, explicit_target=explicit
    )
    assert isinstance(target, SessionAwareDeliveryTarget)
    assert target.session_id == "session:active-123"


def test_resolve_target_no_session_uses_explicit() -> None:
    """No active session → explicit_target is used."""
    from magi_agent.harness.scheduler_delivery import LocalLogDeliverySink, resolve_delivery_target

    job = _make_job()
    explicit = LocalLogDeliverySink()
    target = resolve_delivery_target(job, last_active_session=None, explicit_target=explicit)
    assert isinstance(target, LocalLogDeliverySink)


def test_resolve_target_neither_uses_local_default() -> None:
    """No session AND no explicit target → default local sink."""
    from magi_agent.harness.scheduler_delivery import LocalLogDeliverySink, resolve_delivery_target

    job = _make_job()
    target = resolve_delivery_target(job, last_active_session=None, explicit_target=None)
    assert isinstance(target, LocalLogDeliverySink)


def test_resolve_target_is_pure_function() -> None:
    """resolve_delivery_target has no side effects — calling it twice gives same result."""
    from magi_agent.harness.scheduler_delivery import LocalLogDeliverySink, resolve_delivery_target

    job = _make_job()
    t1 = resolve_delivery_target(job)
    t2 = resolve_delivery_target(job)
    assert type(t1) is type(t2)


# ---------------------------------------------------------------------------
# 8.  Gate OFF / shadow → NO delivery
# ---------------------------------------------------------------------------

def test_gate_off_no_delivery_called(tmp_path: Any) -> None:
    """Gate OFF (default): execute_due_jobs returns no delivery receipts."""
    from magi_agent.harness.scheduler_job_execution import execute_due_jobs

    now_ms = 1_000_000
    now = _now_dt(now_ms)
    lease = _make_lease(now_ms=now_ms)
    runner = _FakeCronTurnRunner(output="Some result.")

    source = _make_source(
        [{"job_id": "job:gateoff-001", "schedule_expr": "every 10m", "next_run_ms": now_ms - 500}]
    )
    result = execute_due_jobs(
        now=now,
        source=source,
        lease=lease,
        lock_dir=tmp_path,
        owner_digest="owner:test-abc",
        runner=runner,
        config=None,  # default → disabled
    )
    assert runner.calls == []
    assert result.executions == ()
    # No delivery receipts on executions (gate off means no execution at all).


def test_shadow_no_delivery_called(tmp_path: Any) -> None:
    """Shadow mode: runner not invoked, no delivery happens."""
    from magi_agent.harness.scheduler_job_execution import JobExecutionConfig, execute_due_jobs

    now_ms = 1_000_000
    now = _now_dt(now_ms)
    lease = _make_lease(now_ms=now_ms)
    runner = _FakeCronTurnRunner(output="Some result.")

    config = JobExecutionConfig(executor_enabled=True, shadow=True, timeout_seconds=600.0)
    source = _make_source(
        [{"job_id": "job:shadow-del-001", "schedule_expr": "every 10m", "next_run_ms": now_ms - 500}]
    )
    result = execute_due_jobs(
        now=now, source=source, lease=lease, lock_dir=tmp_path,
        owner_digest="owner:test-abc", runner=runner, config=config,
    )
    assert runner.calls == [], "runner must not be called in shadow mode"
    ex = result.executions[0]
    assert ex.runner_invoked is False
    # No delivery receipt in shadow executions.
    assert ex.delivery_receipt is None


# ---------------------------------------------------------------------------
# 9.  Live path → delivery receipt attached to JobExecution
# ---------------------------------------------------------------------------

def test_live_delivery_receipt_attached(tmp_path: Any) -> None:
    """Live turn: delivery receipt is attached to JobExecution after successful turn."""
    from magi_agent.harness.scheduler_job_execution import JobExecutionConfig, execute_due_jobs

    now_ms = 1_000_000
    now = _now_dt(now_ms)
    lease = _make_lease(now_ms=now_ms)
    runner = _FakeCronTurnRunner(output="Daily report generated successfully.")

    config = JobExecutionConfig(executor_enabled=True, shadow=False, timeout_seconds=600.0)
    source = _make_source(
        [{"job_id": "job:live-del-001", "schedule_expr": "every 10m", "next_run_ms": now_ms - 500}]
    )
    result = execute_due_jobs(
        now=now, source=source, lease=lease, lock_dir=tmp_path,
        owner_digest="owner:test-abc", runner=runner, config=config,
    )

    assert len(result.executions) == 1
    ex = result.executions[0]
    assert ex.runner_invoked is True
    assert ex.delivery_receipt is not None
    assert ex.delivery_receipt.status == "delivered"
    assert ex.delivery_receipt.job_id == "job:live-del-001"


def test_live_silent_output_gives_suppressed_receipt(tmp_path: Any) -> None:
    """Live turn returning [SILENT] → delivery receipt status is suppressed_silent."""
    from magi_agent.harness.scheduler_job_execution import JobExecutionConfig, execute_due_jobs

    now_ms = 1_000_000
    now = _now_dt(now_ms)
    lease = _make_lease(now_ms=now_ms)
    runner = _FakeCronTurnRunner(output="[SILENT]")

    config = JobExecutionConfig(executor_enabled=True, shadow=False, timeout_seconds=600.0)
    source = _make_source(
        [{"job_id": "job:silent-live-001", "schedule_expr": "every 10m", "next_run_ms": now_ms - 500}]
    )
    result = execute_due_jobs(
        now=now, source=source, lease=lease, lock_dir=tmp_path,
        owner_digest="owner:test-abc", runner=runner, config=config,
    )

    ex = result.executions[0]
    assert ex.delivery_receipt is not None
    assert ex.delivery_receipt.status == "suppressed_silent"


def test_live_timed_out_turn_has_skipped_receipt(tmp_path: Any) -> None:
    """Timed-out turn → delivery receipt is skipped (no content to deliver)."""
    from magi_agent.harness.scheduler_job_execution import JobExecutionConfig, execute_due_jobs

    now_ms = 1_000_000
    now = _now_dt(now_ms)
    lease = _make_lease(now_ms=now_ms)
    runner = _FakeCronTurnRunner(status="timed_out", output="")

    config = JobExecutionConfig(executor_enabled=True, shadow=False, timeout_seconds=600.0)
    source = _make_source(
        [{"job_id": "job:timeout-del-001", "schedule_expr": "every 10m", "next_run_ms": now_ms - 500}]
    )
    result = execute_due_jobs(
        now=now, source=source, lease=lease, lock_dir=tmp_path,
        owner_digest="owner:test-abc", runner=runner, config=config,
    )

    ex = result.executions[0]
    assert ex.delivery_receipt is not None
    assert ex.delivery_receipt.status == "skipped"


# ---------------------------------------------------------------------------
# 10.  Module import purity — scheduler_delivery
# ---------------------------------------------------------------------------

def test_scheduler_delivery_no_live_network_imports() -> None:
    """scheduler_delivery must not import ADK/network modules at top level."""
    import ast
    import subprocess
    import sys
    from pathlib import Path

    src = (
        Path(__file__).parent.parent
        / "magi_agent"
        / "harness"
        / "scheduler_delivery.py"
    )
    tree = ast.parse(src.read_text())
    direct_imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                direct_imports.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                direct_imports.add(node.module.split(".")[0])
    dangerous_direct = {"urllib", "socket", "subprocess", "http", "requests"} & direct_imports
    assert not dangerous_direct, (
        f"scheduler_delivery directly imports dangerous stdlib: {dangerous_direct}"
    )

    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import importlib
import sys

importlib.import_module("magi_agent.harness.scheduler_delivery")

forbidden_prefixes = (
    "google.adk",
    "google.genai",
    "magi_agent.adk_bridge",
    "magi_agent.transport",
    "magi_agent.routing",
    "magi_agent.deploy",
    "magi_agent.chat_proxy",
    "magi_agent.runtime_selector",
    "magi_agent.k8s",
    "kubernetes",
    "telegram",
    "discord",
    "requests",
    "httpx",
    "aiohttp",
    "playwright",
    "selenium",
)
loaded = [
    name
    for name in sys.modules
    if any(name == prefix or name.startswith(f"{prefix}.") for prefix in forbidden_prefixes)
]
if loaded:
    raise AssertionError(f"forbidden live/infra modules loaded: {loaded}")
""",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
