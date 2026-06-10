"""PR5 — Prompt injection (static + dynamic) + reflection cron.

TDD test suite (written first).  PR5 is where active learning items get USED:

* **Static injection** via the recipe ``instructionRefs`` mechanism — a new
  ``instruction:learning:usage`` ref carried by a default-OFF first-party
  recipe pack.  OFF (default) leaves the compiled snapshot byte-identical to
  pre-PR5; the ref only appears when a task profile selects the learning pack.
* **Dynamic injection** via the ``memory_recall`` harness — a learning-recall
  adapter (local-fake, default-disabled) that maps a request's scope to
  ``store.retrieve(active-only, scope)`` and returns recalled rule/example
  text, respecting the existing namespace / redaction / projection policy.
* **Cron** — a reflection job that calls ``run_reflection`` on an interval
  (``MAGI_LEARNING_REFLECTION_INTERVAL``, default 24h), gated by the same
  default-OFF env gate, with a manual trigger that runs one incremental pass.

Hard constraint: injection goes through the recipe/harness path ONLY.  No raw
hooks, no core / message_builder / turn_controller / adk_bridge edits.  This is
asserted structurally below.

No real LLM, no live recall, no network — local-fake adapters only.
"""
from __future__ import annotations

import ast
import asyncio
import json
from pathlib import Path

import pytest

from magi_agent.harness.learning_executor import (
    LearningReflectionConfig,
    _REFLECTION_ENV_VAR,
)
from magi_agent.learning.candidates import (
    LocalFakeTranscriptSource,
    SessionTrace,
)
from magi_agent.learning.eval_gate import run_eval_gate
from magi_agent.learning.injection import (
    LearningRecallAdapter,
    build_learning_recall_payload,
)
from magi_agent.learning.models import LearningScope
from magi_agent.learning.store import SqliteLearningStore
from magi_agent.recipes.compiler import (
    AgentRecipeCompiler,
    PackRegistry,
    ProfileResolutionRequest,
)
from magi_agent.recipes.first_party.learning_usage import (
    LEARNING_USAGE_INSTRUCTION_REF,
    LEARNING_USAGE_INSTRUCTION_TEXT,
    LEARNING_USAGE_PACK_ID,
    build_learning_usage_pack,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _store(tmp_path) -> SqliteLearningStore:
    return SqliteLearningStore(db_path="learning.db", workspace_root=str(tmp_path))


def _passing_checkset():
    from magi_agent.learning.eval_gate import StaticCheckSet

    return StaticCheckSet(before=(1.0, 1.0, 1.0, 1.0), after=(1.0, 1.0, 1.0, 1.0))


def _candidate(
    *,
    kind: str = "example",
    rationale: str = "prefer concise answers",
    task_kind: str = "general",
    tag: str = "style",
    channel: str | None = None,
    sid: str = "sess-1",
):
    from magi_agent.learning.candidates import LearningCandidate
    from magi_agent.learning.models import Provenance

    if kind == "rule":
        content = {"when": "user asks", "then": rationale}
    elif kind == "eval":
        content = {"input": "user asks", "expected": rationale}
    else:
        content = {"situation": "user asks", "behavior": rationale}
    return LearningCandidate(
        kind=kind,
        scope=LearningScope(taskKind=task_kind, tags=(tag,), channel=channel),
        content=content,
        rationale=rationale,
        provenance=Provenance(
            sessionIds=(sid,),
            derivedBy="reflection",
            createdAt="2026-06-03T10:00:00Z",
        ),
        sourceSignalRef=f"signal:diff@{sid}",
    )


def _seed_active_item(
    store: SqliteLearningStore,
    *,
    kind: str = "example",
    rationale: str = "prefer concise answers",
    task_kind: str = "general",
    tag: str = "style",
    channel: str | None = None,
    sid: str = "sess-1",
) -> None:
    """Use the PR4 eval gate to land an ACTIVE item in the store.

    ``example`` + a passing checkset auto-activates (no human approval), so this
    is the simplest way to produce a genuine active item via the real pipeline.
    """
    run_eval_gate(
        (
            _candidate(
                kind=kind,
                rationale=rationale,
                task_kind=task_kind,
                tag=tag,
                channel=channel,
                sid=sid,
            ),
        ),
        store=store,
        checkset=_passing_checkset(),
    )


# ===========================================================================
# Static injection — instruction ref resolves via the compiler
# ===========================================================================


def test_learning_usage_pack_carries_instruction_ref() -> None:
    pack = build_learning_usage_pack()
    assert LEARNING_USAGE_INSTRUCTION_REF == "instruction:learning:usage"
    assert LEARNING_USAGE_INSTRUCTION_REF in pack.instruction_refs
    # default-OFF: not a default-enabled pack, opt-out allowed, non-hard.
    assert pack.default_enabled is False
    assert pack.hard_safety is False
    assert pack.opt_out_allowed is True
    # selectable only when a task profile explicitly asks for learning.
    assert "learning" in pack.task_profile_selectors


def test_learning_usage_pack_sets_common_ownership_tuples() -> None:
    """M4 — the pack sets ADK/OpenMagi ownership tuples like sibling packs.

    Every other first-party pack carries ``adkPrimitiveOwnership`` /
    ``openmagiBoundaryOwnership``.  For catalog consistency the learning-usage
    pack must too (metadata-only — no live refs).
    """
    pack = build_learning_usage_pack()
    assert pack.adk_primitive_ownership, "missing adkPrimitiveOwnership"
    assert pack.openmagi_boundary_ownership, "missing openmagiBoundaryOwnership"
    # uses the same common tuples a sibling first-party pack declares.
    registry = PackRegistry.with_first_party_packs()
    sibling = registry.get("openmagi.context-safety")
    assert pack.adk_primitive_ownership == sibling.adk_primitive_ownership
    assert pack.openmagi_boundary_ownership == sibling.openmagi_boundary_ownership


def test_instruction_text_is_concise_and_model_agnostic() -> None:
    text = LEARNING_USAGE_INSTRUCTION_TEXT
    lowered = text.lower()
    assert "rule" in lowered
    assert "example" in lowered
    # instructs proposal, not self-mutation.
    assert "propose" in lowered
    # model-agnostic: no provider/model names baked in.
    for banned in ("gpt", "claude", "gemini", "openai", "anthropic"):
        assert banned not in lowered


def test_learning_pack_is_registered_first_party_but_default_off() -> None:
    """The learning-usage pack is part of the first-party registry but OFF."""
    registry = PackRegistry.with_first_party_packs()
    assert LEARNING_USAGE_PACK_ID in registry.pack_ids
    pack = registry.get(LEARNING_USAGE_PACK_ID)
    assert pack.default_enabled is False
    assert pack.hard_safety is False


def test_compiled_snapshot_off_does_not_contain_learning_ref() -> None:
    """OFF (default) — no task profile selecting learning → ref absent.

    The learning pack is registered first-party, but without a task profile
    that selects it the compiled snapshot must NOT contain the instruction ref
    and the learning pack must NOT be selected (byte-identical to pre-PR5 for a
    non-learning task profile).
    """
    compiler = AgentRecipeCompiler(PackRegistry.with_first_party_packs())
    request = ProfileResolutionRequest(taskProfile={"taskType": "general"})
    snapshot = compiler.compile(request)

    rendered = json.dumps(
        snapshot.model_dump(by_alias=True, mode="json"), sort_keys=True
    )
    assert LEARNING_USAGE_PACK_ID not in snapshot.selected_pack_ids
    assert LEARNING_USAGE_INSTRUCTION_REF not in snapshot.instruction_refs
    assert LEARNING_USAGE_INSTRUCTION_REF not in rendered


def test_compiled_snapshot_on_includes_instruction_ref_when_selected() -> None:
    compiler = AgentRecipeCompiler(PackRegistry.with_first_party_packs())
    request = ProfileResolutionRequest(taskProfile={"taskType": "learning"})
    snapshot = compiler.compile(request)

    assert LEARNING_USAGE_PACK_ID in snapshot.selected_pack_ids
    assert LEARNING_USAGE_INSTRUCTION_REF in snapshot.instruction_refs


# ===========================================================================
# Dynamic injection — scope-mapped retrieve
# ===========================================================================


def test_injection_returns_only_scope_matching_active_items(tmp_path) -> None:
    store = _store(tmp_path)
    _seed_active_item(store, rationale="general lesson", task_kind="general")
    _seed_active_item(store, rationale="coding lesson", task_kind="coding", sid="sess-2")

    payload = build_learning_recall_payload(
        store,
        tenant_id="local",
        scope=LearningScope(taskKind="general"),
    )
    store.close()

    bodies = [entry.text for entry in payload]
    assert any("general lesson" in b for b in bodies)
    # cross-scope (coding) item excluded.
    assert all("coding lesson" not in b for b in bodies)
    # all returned entries are rule/example kinds.
    assert all(entry.kind in ("rule", "example") for entry in payload)


def test_injection_excludes_proposed_items(tmp_path) -> None:
    """A ``rule`` from a passing gate stays *proposed* (human approval needed).

    retrieve() is active-only, so proposed rules must NOT surface.
    """
    store = _store(tmp_path)
    _seed_active_item(store, kind="rule", rationale="needs approval", task_kind="general")
    payload = build_learning_recall_payload(
        store, tenant_id="local", scope=LearningScope(taskKind="general")
    )
    store.close()
    assert payload == ()


def test_injection_no_store_returns_empty() -> None:
    payload = build_learning_recall_payload(
        None, tenant_id="local", scope=LearningScope(taskKind="general")
    )
    assert payload == ()


def test_injection_excludes_tagless_item_when_request_pins_tags(tmp_path) -> None:
    """I3 — tagless items are narrowly scoped and excluded when tags pinned.

    An active item carrying no tags (``tags=()``) must NOT surface when the
    request scope pins specific tags, because an empty tag set never intersects
    a pinned tag set.  A learning intended to apply globally must carry an
    explicit wildcard/sentinel tag.
    """
    store = _store(tmp_path)
    # Seed an active item whose scope has NO tags (taskKind matches request),
    # via the genuine propose → eval-observation → auto_activate pipeline.
    from magi_agent.learning.models import LearningItem, Provenance

    item = LearningItem(
        id="tagless-1",
        kind="example",
        scope=LearningScope(taskKind="general", tags=()),
        content={"situation": "user asks", "behavior": "global guidance"},
        rationale="global guidance",
        provenance=Provenance(
            sessionIds=("sess-tagless",),
            derivedBy="reflection",
            createdAt="2026-06-03T10:00:00Z",
        ),
    )
    proposed = store.propose(item)
    obs_ref = store.record_eval_observation(
        item_id=proposed.id,
        before={"score": 1.0},
        after={"score": 1.0},
        sample_n=4,
        passed=True,
    )
    activated = store.auto_activate(proposed.id, eval_observation_ref=obs_ref)
    assert activated.status == "active"
    assert activated.scope.tags == ()

    payload = build_learning_recall_payload(
        store,
        tenant_id="local",
        scope=LearningScope(taskKind="general", tags=("style",)),
    )
    store.close()
    # tagless item is excluded under strict opt-in scoping.
    assert payload == ()


# ===========================================================================
# memory_recall adapter — wired but local-fake / disabled by default
# ===========================================================================


def test_learning_recall_adapter_is_local_fake_provider() -> None:
    adapter = LearningRecallAdapter(store=None, tenant_id="local")
    assert adapter.openmagi_local_fake_provider is True


def test_learning_recall_adapter_disabled_by_default_no_store() -> None:
    """No store injected → recall returns recallAllowed=False, no records."""
    from magi_agent.memory.contracts import RecallRequest

    adapter = LearningRecallAdapter(store=None, tenant_id="local")
    result = asyncio.run(
        adapter.recall(
            RecallRequest(
                scope={"taskKind": "general"},
                query="anything",
                purpose="answer_user",
            ),
            policy=object(),
        )
    )
    assert result.recall_allowed is False
    assert result.records == ()


def test_harness_default_off_does_not_call_learning_adapter(tmp_path) -> None:
    from magi_agent.harness.memory_recall import MemoryRecallHarness
    from magi_agent.memory.contracts import RecallRequest
    from magi_agent.memory.namespaces import MemoryNamespacePolicy
    from magi_agent.recipes.first_party.memory_recall import (
        MemoryRecallProjectionPolicy,
    )

    store = _store(tmp_path)
    _seed_active_item(store, rationale="general lesson", task_kind="general")
    adapter = LearningRecallAdapter(store=store, tenant_id="local")

    # default harness is disabled → adapter never consulted.
    result = asyncio.run(
        MemoryRecallHarness(adapter=adapter).recall(
            request=RecallRequest(
                scope={"taskKind": "general"},
                query="continue",
                purpose="answer_user",
            ),
            namespace_policy=MemoryNamespacePolicy(
                namespaceRef="memory-ns:learning.local"
            ),
            projection_policy=MemoryRecallProjectionPolicy(
                latestUserText="continue", maxBytes=2048
            ),
        )
    )
    store.close()
    assert adapter.calls == 0
    assert result.status == "disabled"


def test_harness_enabled_injects_scope_matched_learning_text(tmp_path) -> None:
    from magi_agent.harness.memory_recall import (
        MemoryRecallHarness,
        MemoryRecallHarnessConfig,
    )
    from magi_agent.memory.contracts import RecallRequest
    from magi_agent.memory.namespaces import MemoryNamespacePolicy
    from magi_agent.recipes.first_party.memory_recall import (
        MemoryRecallProjectionPolicy,
    )

    store = _store(tmp_path)
    _seed_active_item(
        store, rationale="keep answers concise and grounded", task_kind="general"
    )
    _seed_active_item(
        store, rationale="coding only lesson", task_kind="coding", sid="sess-2"
    )
    adapter = LearningRecallAdapter(store=store, tenant_id="local")

    result = asyncio.run(
        MemoryRecallHarness(
            MemoryRecallHarnessConfig(enabled=True, localFakeAdapterEnabled=True),
            adapter=adapter,
        ).recall(
            request=RecallRequest(
                scope={"taskKind": "general"},
                query="how should I continue",
                purpose="answer_user",
            ),
            namespace_policy=MemoryNamespacePolicy(
                namespaceRef="memory-ns:learning.local"
            ),
            projection_policy=MemoryRecallProjectionPolicy(
                latestUserText="how should I continue", maxBytes=2048
            ),
        )
    )
    store.close()
    rendered = json.dumps(result.public_projection(), sort_keys=True)

    assert adapter.calls == 1
    assert result.status == "allowed"
    assert "keep answers concise and grounded" in rendered
    # cross-scope item must NOT leak.
    assert "coding only lesson" not in rendered
    # authority flags stay frozen.
    assert result.receipt.authority_flags.live_provider_called is False
    assert result.receipt.authority_flags.prompt_projection_allowed is False
    assert result.receipt.authority_flags.memory_write_allowed is False


# ===========================================================================
# Cron — reflection job + manual trigger
# ===========================================================================


def test_reflection_cron_not_scheduled_when_off(monkeypatch) -> None:
    # PR9a flipped the reflection tier to default-ON; "off" is now the master
    # opt-out switch rather than an unset env var.
    from magi_agent.harness.cron_runtime import LearningReflectionCronJob

    monkeypatch.delenv(_REFLECTION_ENV_VAR, raising=False)
    monkeypatch.setenv("MAGI_LEARNING_ENABLED", "false")
    job = LearningReflectionCronJob()
    assert job.scheduled is False
    assert job.next_fire_at(now=0) is None


def test_reflection_cron_interval_default_24h_when_on(monkeypatch) -> None:
    """I2 — ``next_fire_at`` adds the interval in MS to a ms-epoch ``now``.

    The surrounding cron module's ``now`` is ms-since-epoch (see
    ``CronDefinition.next_fire_at`` / ``datetime.fromtimestamp((now+1)/1000)``),
    so the reflection job must add the 24h interval as MILLISECONDS, not
    seconds.  A realistic ms-epoch ``now`` exercises the unit.
    """
    from magi_agent.harness.cron_runtime import (
        DEFAULT_REFLECTION_INTERVAL_SECONDS,
        LearningReflectionCronJob,
    )

    monkeypatch.setenv(_REFLECTION_ENV_VAR, "1")
    monkeypatch.delenv("MAGI_LEARNING_REFLECTION_INTERVAL", raising=False)
    job = LearningReflectionCronJob()
    assert job.scheduled is True
    assert DEFAULT_REFLECTION_INTERVAL_SECONDS == 86_400
    assert job.interval_seconds == 86_400
    assert job.interval_ms == 86_400 * 1000
    now_ms = 1_780_000_000_000  # realistic ms-since-epoch
    # 24h = 86_400_000 ms, NOT 86_400 ms.
    assert job.next_fire_at(now=now_ms) == now_ms + 86_400_000
    # the value added is ms, so it advances ~24h, not ~86 seconds.
    assert job.next_fire_at(now=now_ms) - now_ms == 86_400_000


def test_reflection_cron_interval_from_env(monkeypatch) -> None:
    from magi_agent.harness.cron_runtime import LearningReflectionCronJob

    monkeypatch.setenv(_REFLECTION_ENV_VAR, "1")
    monkeypatch.setenv("MAGI_LEARNING_REFLECTION_INTERVAL", "3600")
    job = LearningReflectionCronJob()
    assert job.interval_seconds == 3_600
    assert job.interval_ms == 3_600 * 1000
    now_ms = 1_780_000_000_000
    assert job.next_fire_at(now=now_ms) == now_ms + 3_600_000
    # bad value falls back to default.
    monkeypatch.setenv("MAGI_LEARNING_REFLECTION_INTERVAL", "not-a-number")
    assert LearningReflectionCronJob().interval_seconds == 86_400
    # M6 — non-positive values also fall back to the default (seconds AND ms).
    monkeypatch.setenv("MAGI_LEARNING_REFLECTION_INTERVAL", "0")
    zero_job = LearningReflectionCronJob()
    assert zero_job.interval_seconds == 86_400
    assert zero_job.interval_ms == 86_400 * 1000
    monkeypatch.setenv("MAGI_LEARNING_REFLECTION_INTERVAL", "-3")
    neg_job = LearningReflectionCronJob()
    assert neg_job.interval_seconds == 86_400
    assert neg_job.interval_ms == 86_400 * 1000


def test_manual_trigger_runs_one_incremental_pass(monkeypatch, tmp_path) -> None:
    from magi_agent.harness.cron_runtime import LearningReflectionCronJob

    monkeypatch.setenv(_REFLECTION_ENV_VAR, "1")
    traces = (
        SessionTrace(
            sessionId="s1",
            turns=(
                {"role": "user", "text": "no"},
                {"role": "assistant", "text": "draft"},
                {"role": "user", "text": "actually do X instead"},
                {"role": "assistant", "text": "final"},
            ),
            finalOutput="final",
            draftOutput="draft",
            ts="2026-06-03T10:00:00Z",
        ),
    )
    source = LocalFakeTranscriptSource(traces=traces)
    store = _store(tmp_path)
    job = LearningReflectionCronJob(
        source=source,
        store=store,
        config=LearningReflectionConfig(enabled=True),
        checkset=_passing_checkset(),
    )

    result = asyncio.run(job.trigger_now())
    # watermark advanced to the trace ts.
    assert result.status == "ok"
    assert result.watermark == "2026-06-03T10:00:00Z"
    assert job.watermark == "2026-06-03T10:00:00Z"

    # a second pass starting from the advanced watermark reads zero new traces.
    result2 = asyncio.run(job.trigger_now())
    store.close()
    assert result2.counters["traces_read"] == 0


def test_manual_trigger_error_leaves_watermark_unchanged_and_warns(
    monkeypatch, tmp_path, caplog
) -> None:
    """M7 — an error result logs at WARNING and leaves the watermark unchanged.

    Forward-compat for PR7's error path: ``status="error"`` is an existing,
    valid reflection status.  ``trigger_now`` must not advance the watermark and
    must surface the error at WARNING level.
    """
    import logging

    from magi_agent.harness import cron_runtime
    from magi_agent.harness.cron_runtime import LearningReflectionCronJob
    from magi_agent.harness.learning_executor import LearningReflectionResult

    monkeypatch.setenv(_REFLECTION_ENV_VAR, "1")

    async def _fake_run_reflection(**_kwargs):
        return LearningReflectionResult(
            status="error",
            candidates=(),
            watermark="2099-01-01T00:00:00Z",  # must be ignored
            counters={
                "traces_read": 0,
                "signals_extracted": 0,
                "candidates_produced": 0,
            },
        )

    monkeypatch.setattr(cron_runtime, "run_reflection", _fake_run_reflection)

    job = LearningReflectionCronJob(watermark="2026-06-03T10:00:00Z")
    with caplog.at_level(logging.WARNING, logger=cron_runtime.__name__):
        result = asyncio.run(job.trigger_now())

    assert result.status == "error"
    # watermark unchanged — the error result's watermark is ignored.
    assert job.watermark == "2026-06-03T10:00:00Z"
    assert any(rec.levelno >= logging.WARNING for rec in caplog.records)


def test_manual_trigger_off_is_disabled_noop(monkeypatch, tmp_path) -> None:
    from magi_agent.harness.cron_runtime import LearningReflectionCronJob
    from magi_agent.learning.config import ENV_MASTER

    # PR9a: the executor's reflection gate is default-ON; OFF is via master off.
    monkeypatch.delenv(_REFLECTION_ENV_VAR, raising=False)
    monkeypatch.setenv(ENV_MASTER, "false")
    job = LearningReflectionCronJob(config=LearningReflectionConfig(enabled=True))
    result = asyncio.run(job.trigger_now())
    assert result.status == "disabled"
    assert result.watermark is None
    assert job.watermark is None


# ===========================================================================
# No core edits / no raw hooks — structural assertions
# ===========================================================================


def test_injection_modules_have_no_core_or_hook_imports() -> None:
    python_root = Path(__file__).resolve().parents[1]
    module_paths = [
        python_root / "magi_agent/learning/injection.py",
        python_root / "magi_agent/recipes/first_party/learning_usage.py",
    ]
    banned_substrings = (
        "magi_agent.runtime.message_builder",
        "magi_agent.runtime.adk_bridge",
        "magi_agent.openmagi_runtime",
        "beforeLLMCall",
        "beforeSystemPrompt",
    )
    banned_roots = {
        "google",
        "openai",
        "anthropic",
        "httpx",
        "requests",
        "socket",
    }
    for module_path in module_paths:
        source = module_path.read_text()
        for banned in banned_substrings:
            assert banned not in source, (module_path, banned)
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            assert not (banned_roots & {name.split(".")[0] for name in names}), (
                module_path,
                names,
            )


def test_pr5_touched_files_are_within_allowed_set() -> None:
    """The PR5 diff must not touch any forbidden core file."""
    import subprocess

    python_root = Path(__file__).resolve().parents[1]
    proc = subprocess.run(
        ["git", "diff", "learning/pr4-eval-gate..HEAD", "--name-only"],
        cwd=python_root,
        check=False,
        text=True,
        capture_output=True,
    )
    if proc.returncode != 0:
        pytest.skip(f"git diff unavailable: {proc.stderr}")
    changed = {line.strip() for line in proc.stdout.splitlines() if line.strip()}
    forbidden_markers = (
        "runtime/turn_controller",
        "runtime/message_builder",
        "openmagi_runtime",
        "adk_bridge",
    )
    for path in changed:
        for marker in forbidden_markers:
            assert marker not in path, (path, marker)
