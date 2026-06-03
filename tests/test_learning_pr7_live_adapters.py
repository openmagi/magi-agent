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
