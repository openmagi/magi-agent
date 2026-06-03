"""PR7 — Live adapter promotion (local-fake → real) behind a separate gate.

PR1–PR6 built the Learning Layer default-OFF with local-fake stand-ins
everywhere.  PR7 is the ONLY PR that promotes those stand-ins to REAL adapters,
and it does so EXACTLY the way ``harness/workflow_executor.py`` promotes
spec→live:

* a SEPARATE explicit gate (``MAGI_LEARNING_LIVE_ENABLED``, default OFF) plus a
  readiness stage (``gates/learning_live_readiness.py``) that must pass before
  any real adapter binds, mirroring ``workflow_executor_readiness.py``;
* a shadow → live ladder (shadow computes the real adapter outputs but writes /
  injects NOTHING; live actually binds);
* authority is introduced WITHOUT flipping any ``Literal[False]`` flag — the
  frozen flags stay a permanent attestation that the executor/recall CORE does
  not self-attach live authority.  Promotion is recorded in an AUDIT record, not
  by mutating the frozen flags.

Hard constraints asserted below:
* OFF / not-ready → byte-identical to PR1–PR6 (local-fake retained, no audit).
* The three frozen authority flags on the reflection executor remain
  ``Literal[False]`` even when live.
* No real network / LLM in tests — a deterministic real-Protocol stub labeler
  and a fixture-backed session store stand in for the live surfaces.
"""
from __future__ import annotations

import asyncio
from typing import Literal, get_args, get_origin

import pytest

from magi_agent.gates.learning_live_readiness import (
    LearningLiveExecutionMode,
    LearningLiveReadinessConfig,
    learning_live_readiness_health_metadata,
    resolve_learning_live_execution_mode,
    _LIVE_ENV_VAR,
)
from magi_agent.harness.learning_executor import (
    LearningReflectionConfig,
    LearningReflectionResult,
    _REFLECTION_ENV_VAR,
    run_reflection,
)
from magi_agent.harness.memory_recall import (
    build_gated_live_learning_recall_harness,
    build_learning_recall_harness,
)
from magi_agent.harness.memory_write import (
    MemoryWriteHarness,
    MemoryWritePolicy,
    MemoryWriteRequest,
    build_gated_live_learning_write_harness,
)
from magi_agent.learning.candidates import (
    LocalFakeTranscriptSource,
    SessionTrace,
    TranscriptSource,
)
from magi_agent.learning.labeler import Labeler, LocalFakeLabeler, LabeledLearning
from magi_agent.learning.live import (
    LearningLiveAuditRecord,
    LearningLiveBinding,
    LlmBackedLabeler,
    RealTranscriptSource,
    build_live_learning_binding,
)
from magi_agent.learning.signals import Signal
from magi_agent.learning.store import SqliteLearningStore
from magi_agent.storage.session_store import SessionSqliteStore, SessionStoreConfig


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


def _learning_store(tmp_path) -> SqliteLearningStore:
    return SqliteLearningStore(db_path="learning.db", workspace_root=str(tmp_path))


def _session_store(tmp_path) -> SessionSqliteStore:
    store = SessionSqliteStore(
        SessionStoreConfig(enabled=True, db_path="sessions.db"),
        workspace_root=str(tmp_path),
    )
    return store


def _seed_sessions(store: SessionSqliteStore) -> None:
    """Persist a few real sessions whose state carries a draft→final diff signal."""
    for i in range(4):
        store.save_sync(
            session_id=f"sess-{i}",
            app_name="magi",
            user_id="user-1",
            state={
                "turns": [
                    {"role": "user", "text": f"please answer q{i}"},
                    {"role": "assistant", "text": "final answer"},
                ],
                "finalOutput": "use a concise tone",
                "draftOutput": "use a verbose, wordy and meandering tone",
            },
        )


class _StubModelClient:
    """Deterministic real-Protocol model client — NO network, NO LLM.

    Satisfies ``LabelerModelClient`` so ``LlmBackedLabeler`` (the REAL labeler
    binding) can be exercised without an API call.  Returns a stable label type
    per signal kind so the test is deterministic.
    """

    def __init__(self) -> None:
        self.calls = 0

    def label_signal(self, *, signal_kind: str, summary: str) -> dict[str, str]:
        self.calls += 1
        return {"type": "style", "lesson": f"live[{signal_kind}] {summary}"}


# ---------------------------------------------------------------------------
# 1. Gate OFF → local-fake retained, no audit, byte-identical to PR1–PR6
# ---------------------------------------------------------------------------


def test_gate_off_keeps_local_fake_no_audit(tmp_path, monkeypatch):
    monkeypatch.delenv(_LIVE_ENV_VAR, raising=False)
    monkeypatch.setenv(_REFLECTION_ENV_VAR, "1")

    session_store = _session_store(tmp_path)
    _seed_sessions(session_store)
    learning_store = _learning_store(tmp_path)

    binding = build_live_learning_binding(
        session_store=session_store,
        learning_store=learning_store,
        model_client=_StubModelClient(),
        readiness=LearningLiveReadinessConfig(),  # default OFF
        bot_id="bot-1",
        user_id="user-1",
    )

    # Gate OFF → nothing real binds.
    assert binding.mode == "disabled"
    assert binding.audit is None
    assert isinstance(binding.transcript_source, LocalFakeTranscriptSource)
    assert isinstance(binding.labeler, LocalFakeLabeler)
    assert binding.recall_live_bound is False
    assert binding.write_live_bound is False


def test_gate_off_reflection_byte_identical(tmp_path, monkeypatch):
    """OFF path reflection result is identical to the PR1–PR6 local-fake path."""
    monkeypatch.delenv(_LIVE_ENV_VAR, raising=False)
    monkeypatch.setenv(_REFLECTION_ENV_VAR, "1")

    session_store = _session_store(tmp_path)
    _seed_sessions(session_store)

    binding = build_live_learning_binding(
        session_store=session_store,
        learning_store=None,
        model_client=_StubModelClient(),
        readiness=LearningLiveReadinessConfig(),
        bot_id="bot-1",
        user_id="user-1",
    )

    async def _run() -> LearningReflectionResult:
        return await run_reflection(
            config=LearningReflectionConfig(enabled=True),
            source=binding.transcript_source,
            labeler=binding.labeler,
        )

    result = asyncio.run(_run())
    # Local-fake source (empty) → no traces read → no candidates: same as PR6 OFF.
    assert result.status == "ok"
    assert result.candidates == ()
    assert result.counters["traces_read"] == 0


# ---------------------------------------------------------------------------
# 2. Readiness not-ready (gate ON, preconditions unmet) → still local-fake
# ---------------------------------------------------------------------------


def test_gate_on_but_not_ready_keeps_local_fake(tmp_path, monkeypatch):
    monkeypatch.setenv(_LIVE_ENV_VAR, "1")

    # enabled=True but shadow disabled + no scope → readiness fails closed.
    readiness = LearningLiveReadinessConfig(enabled=True)
    mode = resolve_learning_live_execution_mode(
        readiness, bot_id="bot-1", user_id="user-1"
    )
    assert mode == "disabled"

    binding = build_live_learning_binding(
        session_store=_session_store(tmp_path),
        learning_store=_learning_store(tmp_path),
        model_client=_StubModelClient(),
        readiness=readiness,
        bot_id="bot-1",
        user_id="user-1",
    )
    assert binding.mode == "disabled"
    assert binding.audit is None
    assert isinstance(binding.transcript_source, LocalFakeTranscriptSource)
    assert isinstance(binding.labeler, LocalFakeLabeler)


# ---------------------------------------------------------------------------
# 3. Gate ON + ready → real adapters bind + audit emitted
# ---------------------------------------------------------------------------


def _ready_config(*, bot_id: str = "bot-1", user_id: str = "user-1", live: bool = True):
    import hashlib

    def _digest(value: str) -> str:
        return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()

    return LearningLiveReadinessConfig(
        enabled=True,
        killSwitchEnabled=False,
        shadowModeEnabled=True,
        selectedBotDigest=_digest(bot_id),
        selectedOwnerUserIdDigest=_digest(user_id),
        environment="staging",
        environmentAllowlist=("staging",),
        promotedGate=5 if live else 0,
        canaryPromotionConfirmed=live,
    )


def test_gate_on_ready_binds_real_adapters_and_emits_audit(tmp_path, monkeypatch):
    monkeypatch.setenv(_LIVE_ENV_VAR, "1")
    monkeypatch.setenv(_REFLECTION_ENV_VAR, "1")

    session_store = _session_store(tmp_path)
    _seed_sessions(session_store)
    learning_store = _learning_store(tmp_path)
    model_client = _StubModelClient()

    readiness = _ready_config(live=True)
    assert resolve_learning_live_execution_mode(
        readiness, bot_id="bot-1", user_id="user-1"
    ) == "live"

    binding = build_live_learning_binding(
        session_store=session_store,
        learning_store=learning_store,
        model_client=model_client,
        readiness=readiness,
        bot_id="bot-1",
        user_id="user-1",
    )

    assert binding.mode == "live"
    # Real adapters bound.
    assert isinstance(binding.transcript_source, RealTranscriptSource)
    assert isinstance(binding.labeler, LlmBackedLabeler)
    assert binding.recall_live_bound is True
    assert binding.write_live_bound is True

    # Audit record emitted with correct contents.
    audit = binding.audit
    assert isinstance(audit, LearningLiveAuditRecord)
    assert audit.execution_mode == "live"
    assert audit.gate_enabled is True
    assert audit.readiness_ready is True
    assert "transcript_source" in audit.promoted_adapters
    assert "labeler" in audit.promoted_adapters
    assert "memory_recall" in audit.promoted_adapters
    assert "memory_write" in audit.promoted_adapters
    assert audit.promoted_at  # non-empty timestamp

    # I3 — scope identity present so PR8 fleet/canary telemetry can correlate.
    import hashlib

    assert audit.bot_id == "bot-1"
    assert audit.tenant_id == "local"  # default tenant
    # user_id is NEVER stored raw — only a sha256 digest.
    expected_digest = "sha256:" + hashlib.sha256(b"user-1").hexdigest()
    assert audit.user_id_digest == expected_digest
    assert "user-1" not in audit.user_id_digest


def test_real_transcript_source_reads_persisted_sessions_watermark(tmp_path, monkeypatch):
    monkeypatch.setenv(_LIVE_ENV_VAR, "1")

    session_store = _session_store(tmp_path)
    _seed_sessions(session_store)
    binding = build_live_learning_binding(
        session_store=session_store,
        learning_store=_learning_store(tmp_path),
        model_client=_StubModelClient(),
        readiness=_ready_config(live=True),
        bot_id="bot-1",
        user_id="user-1",
    )
    src = binding.transcript_source
    assert isinstance(src, RealTranscriptSource)

    async def _read(wm):
        return await src.read_since(wm)

    # Full read (no watermark) → all persisted sessions surface as traces.
    all_traces = asyncio.run(_read(None))
    assert len(all_traces) == 4
    assert all(isinstance(t, SessionTrace) for t in all_traces)
    assert all(t.ts.endswith("Z") for t in all_traces)

    # Watermark-incremental: reading since the max ts returns nothing new.
    max_ts = max(t.ts for t in all_traces)
    assert asyncio.run(_read(max_ts)) == ()

    # Watermark below the max returns the strictly-newer subset.
    sorted_ts = sorted(t.ts for t in all_traces)
    newer = asyncio.run(_read(sorted_ts[0]))
    assert all(t.ts > sorted_ts[0] for t in newer)
    assert len(newer) == 3


def test_real_transcript_source_drives_real_reflection(tmp_path, monkeypatch):
    """End-to-end: real source + real (stub) labeler produce candidates."""
    monkeypatch.setenv(_LIVE_ENV_VAR, "1")
    monkeypatch.setenv(_REFLECTION_ENV_VAR, "1")

    session_store = _session_store(tmp_path)
    _seed_sessions(session_store)
    model_client = _StubModelClient()

    binding = build_live_learning_binding(
        session_store=session_store,
        learning_store=None,
        model_client=model_client,
        readiness=_ready_config(live=True),
        bot_id="bot-1",
        user_id="user-1",
    )

    async def _run() -> LearningReflectionResult:
        return await run_reflection(
            config=LearningReflectionConfig(enabled=True),
            source=binding.transcript_source,
            labeler=binding.labeler,
        )

    result = asyncio.run(_run())
    assert result.status == "ok"
    assert result.counters["traces_read"] == 4
    assert len(result.candidates) > 0
    # The real (stub) labeler was actually consulted.
    assert model_client.calls > 0


# ---------------------------------------------------------------------------
# 4. Shadow mode → real adapters COMPUTED but no write/inject (observe only)
# ---------------------------------------------------------------------------


def test_shadow_mode_computes_real_but_no_write(tmp_path, monkeypatch):
    monkeypatch.setenv(_LIVE_ENV_VAR, "1")

    session_store = _session_store(tmp_path)
    _seed_sessions(session_store)
    readiness = _ready_config(live=False)  # shadow-ready, not canary-promoted

    assert resolve_learning_live_execution_mode(
        readiness, bot_id="bot-1", user_id="user-1"
    ) == "shadow"

    binding = build_live_learning_binding(
        session_store=session_store,
        learning_store=_learning_store(tmp_path),
        model_client=_StubModelClient(),
        readiness=readiness,
        bot_id="bot-1",
        user_id="user-1",
    )

    assert binding.mode == "shadow"
    # Real transcript source + labeler are computed (observe-only)...
    assert isinstance(binding.transcript_source, RealTranscriptSource)
    assert isinstance(binding.labeler, LlmBackedLabeler)
    # ...but recall/write are NOT live-bound in shadow (no inject / no write).
    assert binding.recall_live_bound is False
    assert binding.write_live_bound is False
    # An audit IS emitted for shadow promotion (observability), distinct from live.
    assert binding.audit is not None
    assert binding.audit.execution_mode == "shadow"
    assert "memory_write" not in binding.audit.promoted_adapters
    assert "memory_recall" not in binding.audit.promoted_adapters
    assert "transcript_source" in binding.audit.promoted_adapters


# ---------------------------------------------------------------------------
# 5. Frozen flags audit — the three Literal[False] flags stay Literal[False]
# ---------------------------------------------------------------------------


def _frozen_flag_is_literal_false(model_cls, field_name: str) -> bool:
    annotation = model_cls.model_fields[field_name].annotation
    return get_origin(annotation) is Literal and get_args(annotation) == (False,)


def test_frozen_authority_flags_remain_literal_false_even_when_live(tmp_path, monkeypatch):
    monkeypatch.setenv(_LIVE_ENV_VAR, "1")
    monkeypatch.setenv(_REFLECTION_ENV_VAR, "1")

    # Static attestation: the three flags are typed Literal[False] on BOTH
    # the config and the result — PR7 must NOT widen them.
    for field in (
        "llm_attached",
        "production_write_enabled",
        "real_transcript_source_attached",
    ):
        assert _frozen_flag_is_literal_false(LearningReflectionConfig, field)
        assert _frozen_flag_is_literal_false(LearningReflectionResult, field)

    # And the running result keeps them False even on the real-adapter path.
    session_store = _session_store(tmp_path)
    _seed_sessions(session_store)
    binding = build_live_learning_binding(
        session_store=session_store,
        learning_store=None,
        model_client=_StubModelClient(),
        readiness=_ready_config(live=True),
        bot_id="bot-1",
        user_id="user-1",
    )

    async def _run() -> LearningReflectionResult:
        return await run_reflection(
            config=LearningReflectionConfig(enabled=True),
            source=binding.transcript_source,
            labeler=binding.labeler,
        )

    result = asyncio.run(_run())
    assert result.llm_attached is False
    assert result.production_write_enabled is False
    assert result.real_transcript_source_attached is False
    # The promotion is recorded in the audit, NOT by flipping a flag.
    assert binding.audit is not None
    assert binding.audit.promoted_adapters  # something WAS promoted


def test_live_readiness_config_authority_flag_locked_false():
    """Even a forged truthy authority value is coerced to False (gate-derived)."""
    cfg = LearningLiveReadinessConfig.model_validate(
        {"enabled": True, "liveAuthorityAllowed": True}
    )
    assert cfg.live_authority_allowed is False
    assert _frozen_flag_is_literal_false(
        LearningLiveReadinessConfig, "live_authority_allowed"
    )


# ---------------------------------------------------------------------------
# 6. Readiness stage transitions gate the binding
# ---------------------------------------------------------------------------


def test_readiness_transition_not_ready_to_ready_gates_binding(tmp_path, monkeypatch):
    monkeypatch.setenv(_LIVE_ENV_VAR, "1")
    session_store = _session_store(tmp_path)
    _seed_sessions(session_store)

    # Stage A: not ready (shadow disabled) → disabled, local-fake.
    not_ready = LearningLiveReadinessConfig(enabled=True, shadowModeEnabled=False)
    binding_a = build_live_learning_binding(
        session_store=session_store,
        learning_store=_learning_store(tmp_path),
        model_client=_StubModelClient(),
        readiness=not_ready,
        bot_id="bot-1",
        user_id="user-1",
    )
    assert binding_a.mode == "disabled"
    assert isinstance(binding_a.transcript_source, LocalFakeTranscriptSource)

    # Stage B: shadow-ready → real source computed, observe-only.
    binding_b = build_live_learning_binding(
        session_store=session_store,
        learning_store=_learning_store(tmp_path),
        model_client=_StubModelClient(),
        readiness=_ready_config(live=False),
        bot_id="bot-1",
        user_id="user-1",
    )
    assert binding_b.mode == "shadow"
    assert isinstance(binding_b.transcript_source, RealTranscriptSource)

    # Stage C: canary-promoted → live binding.
    binding_c = build_live_learning_binding(
        session_store=session_store,
        learning_store=_learning_store(tmp_path),
        model_client=_StubModelClient(),
        readiness=_ready_config(live=True),
        bot_id="bot-1",
        user_id="user-1",
    )
    assert binding_c.mode == "live"
    assert binding_c.write_live_bound is True


def test_live_recall_and_write_harnesses_bound_when_live(tmp_path, monkeypatch):
    """The live binding exposes real recall + write harnesses gated ON."""
    monkeypatch.setenv(_LIVE_ENV_VAR, "1")
    session_store = _session_store(tmp_path)
    _seed_sessions(session_store)
    learning_store = _learning_store(tmp_path)

    binding = build_live_learning_binding(
        session_store=session_store,
        learning_store=learning_store,
        model_client=_StubModelClient(),
        readiness=_ready_config(live=True),
        bot_id="bot-1",
        user_id="user-1",
    )
    # recall harness is bound + enabled (live), write harness present.
    assert binding.recall_harness is not None
    assert binding.recall_harness.config.enabled is True
    assert isinstance(binding.write_harness, MemoryWriteHarness)
    # Memory-write harness authority flags stay frozen-False even when bound.
    assert binding.write_harness.config.production_write_enabled is False


def test_gated_recall_and_write_factories_only_bind_when_live(tmp_path, monkeypatch):
    """The harness-level gated factories return a real harness ONLY when live."""
    monkeypatch.setenv(_LIVE_ENV_VAR, "1")
    learning_store = _learning_store(tmp_path)

    # live → both gated factories return an enabled real harness.
    live_cfg = _ready_config(live=True)
    recall = build_gated_live_learning_recall_harness(
        store=learning_store, readiness=live_cfg, bot_id="bot-1", user_id="user-1"
    )
    write = build_gated_live_learning_write_harness(
        readiness=live_cfg, bot_id="bot-1", user_id="user-1"
    )
    assert recall is not None and recall.config.enabled is True
    assert isinstance(write, MemoryWriteHarness) and write.config.enabled is True
    # Frozen authority flags stay False even when enabled.
    assert write.config.production_write_enabled is False

    # shadow → no live binding (observe-only).
    shadow_cfg = _ready_config(live=False)
    assert (
        build_gated_live_learning_recall_harness(
            store=learning_store, readiness=shadow_cfg, bot_id="bot-1", user_id="user-1"
        )
        is None
    )
    assert (
        build_gated_live_learning_write_harness(
            readiness=shadow_cfg, bot_id="bot-1", user_id="user-1"
        )
        is None
    )


def test_gated_factories_disabled_when_env_off(tmp_path, monkeypatch):
    """Env gate OFF → gated factories never bind, even with a live-ready config."""
    monkeypatch.delenv(_LIVE_ENV_VAR, raising=False)
    live_cfg = _ready_config(live=True)
    assert (
        build_gated_live_learning_recall_harness(
            store=_learning_store(tmp_path),
            readiness=live_cfg,
            bot_id="bot-1",
            user_id="user-1",
        )
        is None
    )
    assert (
        build_gated_live_learning_write_harness(
            readiness=live_cfg, bot_id="bot-1", user_id="user-1"
        )
        is None
    )


def test_labeler_protocol_satisfied_by_real_binding():
    """LlmBackedLabeler conforms to the Labeler Protocol (runtime check)."""
    labeler = LlmBackedLabeler(model_client=_StubModelClient())
    assert isinstance(labeler, Labeler)
    sig = Signal(
        kind="diff", sessionId="s1", summary="concise vs verbose", evidence={}
    )
    trace = SessionTrace(
        sessionId="s1",
        turns=(),
        finalOutput="concise",
        draftOutput="verbose",
        ts="2026-06-03T10:00:00Z",
    )
    label = labeler.label(sig, trace)
    assert isinstance(label, LabeledLearning)


# ---------------------------------------------------------------------------
# 7. C1 — the LIVE write harness can actually PERSIST (not silently blocked)
# ---------------------------------------------------------------------------


def test_live_write_harness_actually_persists_not_blocked(tmp_path, monkeypatch):
    """C1 regression: the live-bound write harness must NOT block every write.

    Previously ``build_live_learning_binding`` constructed
    ``MemoryWriteHarness({"enabled": True})`` — but
    ``localFakeAdapterEnabled`` defaults False, so EVERY ``write()`` returned
    ``status="blocked"`` (``local_fake_memory_write_disabled``).  The earlier
    test only checked ``isinstance``, which let this slip.  This test CALLS
    ``write()`` on the live-bound harness and asserts it can persist.
    """
    monkeypatch.setenv(_LIVE_ENV_VAR, "1")
    session_store = _session_store(tmp_path)
    _seed_sessions(session_store)
    learning_store = _learning_store(tmp_path)

    binding = build_live_learning_binding(
        session_store=session_store,
        learning_store=learning_store,
        model_client=_StubModelClient(),
        readiness=_ready_config(live=True),
        bot_id="bot-1",
        user_id="user-1",
    )
    assert isinstance(binding.write_harness, MemoryWriteHarness)
    # The operational enable is ON (NOT the frozen Literal[False] authority).
    assert binding.write_harness.config.local_fake_adapter_enabled is True
    assert binding.write_harness.config.production_write_enabled is False

    result = asyncio.run(
        binding.write_harness.write(
            request=MemoryWriteRequest(
                providerId="agentmemory",
                turnId="turn-live-1",
                operation="remember",
                content="a learned lesson worth persisting",
                evidenceRefs=("evidence:learned-lesson",),
            ),
            policy=MemoryWritePolicy(
                policyRef="policy:learning-write",
                policySnapshotRef="policy-snapshot:pr7",
                localFakeSuccessAllowed=True,
            ),
        )
    )

    # The whole point: NOT blocked — the harness can persist.
    assert result.status != "blocked"
    assert result.status == "success"
    assert "local_fake_memory_write_disabled" not in result.reason_codes
    assert result.receipt is not None


# ---------------------------------------------------------------------------
# 8. I1 — the session read is bounded (query-level watermark + cap)
# ---------------------------------------------------------------------------


def test_list_sync_query_level_since_and_limit(tmp_path):
    """I1: list_sync applies WHERE updated_at > ? and LIMIT at the SQL layer.

    Default behavior (no since/limit) is unchanged; the new optional args push
    the watermark + cap to SQL so a reflection read never scans the whole table.
    """
    store = _session_store(tmp_path)
    watermarks: list[str] = []
    for i in range(6):
        store.save_sync(
            session_id=f"q-{i}",
            app_name="magi",
            user_id="user-1",
            state={"finalOutput": f"o{i}"},
        )
        loaded = store.load_sync("magi", "user-1", f"q-{i}")
        assert loaded is not None
        watermarks.append(loaded["updated_at"])

    # Default: all 6 rows, unchanged behavior (DESC).
    assert len(store.list_sync("magi", "user-1")) == 6

    # since-filter: only rows strictly newer than the 3rd watermark surface.
    cutoff = watermarks[2]
    newer = store.list_sync("magi", "user-1", since=cutoff)
    assert all(r["updated_at"] > cutoff for r in newer)
    assert {r["id"] for r in newer} == {"q-3", "q-4", "q-5"}

    # cap: LIMIT applied at the SQL layer.
    capped = store.list_sync("magi", "user-1", since=cutoff, limit=2)
    assert len(capped) == 2
    assert all(r["updated_at"] > cutoff for r in capped)


def test_real_transcript_source_respects_read_cap(tmp_path, monkeypatch):
    """The real source threads its read_cap into the bounded query."""
    monkeypatch.setenv(_LIVE_ENV_VAR, "1")
    session_store = _session_store(tmp_path)
    _seed_sessions(session_store)  # 4 sessions

    src = RealTranscriptSource(
        store=session_store, app_name="magi", user_id="user-1", read_cap=2
    )
    traces = asyncio.run(src.read_since(None))
    # The cap is enforced at the query layer — never the whole table.
    assert len(traces) == 2


# ---------------------------------------------------------------------------
# 9. I2 — watermark precision: Z-without-microseconds compares consistently
# ---------------------------------------------------------------------------


def test_watermark_normalizes_z_without_microseconds(tmp_path, monkeypatch):
    """I2: a Z-timestamp WITHOUT microseconds must compare correctly.

    ``...:00Z`` (no microseconds) vs ``...:00.000001Z`` — naively comparing the
    raw strings mis-orders same-second sessions because ``Z`` (0x5A) sorts after
    ``.`` (0x2E).  Normalizing both to canonical 6-digit form fixes it.
    """
    monkeypatch.setenv(_LIVE_ENV_VAR, "1")

    class _FakeStore:
        def list_sync(self, app_name, user_id=None, *, since=None, limit=None):
            rows = [
                {"id": "no-us", "updated_at": "2026-06-03T10:00:00Z",
                 "state": {"finalOutput": "a"}},
                {"id": "with-us", "updated_at": "2026-06-03T10:00:00.000001Z",
                 "state": {"finalOutput": "b"}},
            ]
            if since is not None:
                rows = [r for r in rows if r["updated_at"] > since]
            return rows[:limit] if limit is not None else rows

    src = RealTranscriptSource(store=_FakeStore(), app_name="magi", user_id="user-1")

    all_traces = asyncio.run(src.read_since(None))
    by_id = {t.session_id: t.ts for t in all_traces}
    # Both normalized to 6-digit microseconds → both end in canonical form.
    assert by_id["no-us"] == "2026-06-03T10:00:00.000000Z"
    assert by_id["with-us"] == "2026-06-03T10:00:00.000001Z"
    # And they order correctly: the no-microseconds ts is strictly earlier.
    assert by_id["no-us"] < by_id["with-us"]

    # Using the no-microseconds ts as a watermark must NOT swallow the later one.
    newer = asyncio.run(src.read_since(by_id["no-us"]))
    assert {t.session_id for t in newer} == {"with-us"}


def test_normalize_ts_returns_none_on_unparseable_z():
    """A Z-string that can't be parsed returns None (row skipped, no crash)."""
    from magi_agent.learning.live import _normalize_ts

    assert _normalize_ts("not-a-timestampZ") is None
    assert _normalize_ts("") is None
    assert _normalize_ts(None) is None


# ---------------------------------------------------------------------------
# 10. MINOR — labeler model-client raising falls back (batch not aborted)
# ---------------------------------------------------------------------------


class _ErrorStubModelClient:
    """Real-Protocol model client that RAISES — exercises the fallback path."""

    def label_signal(self, *, signal_kind: str, summary: str):
        raise RuntimeError("simulated model failure")


def test_labeler_falls_back_when_model_client_raises():
    labeler = LlmBackedLabeler(model_client=_ErrorStubModelClient())
    sig = Signal(
        kind="diff", sessionId="s1", summary="concise vs verbose", evidence={}
    )
    trace = SessionTrace(
        sessionId="s1",
        turns=(),
        finalOutput="concise",
        draftOutput="verbose",
        ts="2026-06-03T10:00:00.000000Z",
    )
    # No exception propagates; a valid local-fake LabeledLearning is returned.
    label = labeler.label(sig, trace)
    assert isinstance(label, LabeledLearning)
    assert label.lesson


# ---------------------------------------------------------------------------
# 11. MINOR — backward transition live → shadow → disabled (no sticky state)
# ---------------------------------------------------------------------------


def test_backward_transition_live_to_shadow_to_disabled(tmp_path, monkeypatch):
    monkeypatch.setenv(_LIVE_ENV_VAR, "1")
    session_store = _session_store(tmp_path)
    _seed_sessions(session_store)

    def _bind(readiness):
        return build_live_learning_binding(
            session_store=session_store,
            learning_store=_learning_store(tmp_path),
            model_client=_StubModelClient(),
            readiness=readiness,
            bot_id="bot-1",
            user_id="user-1",
        )

    # live → real adapters + write bound.
    live = _bind(_ready_config(live=True))
    assert live.mode == "live"
    assert live.write_live_bound is True

    # shadow (canary_promotion_confirmed off) → observe-only, no write bind.
    shadow = _bind(_ready_config(live=False))
    assert shadow.mode == "shadow"
    assert shadow.write_live_bound is False
    assert isinstance(shadow.transcript_source, RealTranscriptSource)

    # disabled (env OFF) → back to local-fake, no audit, no sticky state.
    monkeypatch.delenv(_LIVE_ENV_VAR, raising=False)
    disabled = _bind(_ready_config(live=True))
    assert disabled.mode == "disabled"
    assert disabled.audit is None
    assert isinstance(disabled.transcript_source, LocalFakeTranscriptSource)
    assert isinstance(disabled.labeler, LocalFakeLabeler)
    assert disabled.recall_live_bound is False
    assert disabled.write_live_bound is False
