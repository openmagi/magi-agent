"""PR-F-LIFE4a gate firing tests: action matrix normalization.

Locks the runtime contract for each slot lifted past audit-only by F-LIFE4a:

* ``run_user_prompt_submit_gate``
* ``run_before_turn_start_gate``
* ``run_before_llm_call_gate``
* ``run_after_llm_call_gate``
* ``run_before_compaction_gate``
* ``run_on_task_checkpoint_gate``
* ``run_on_artifact_created_gate``

Each gate fan-outs over enabled llm_criterion rules whose persisted
``action`` is in the slot's lifted set; returns the WORST verdict across
all rules (block > ask > proceed). Audit-only rules NEVER block (they
flow through the parallel ``run_X_audit`` helper instead).

Default-OFF preserved everywhere: each test that drives an ON-path also
asserts the OFF path returns ``"proceed"`` even when a failing rule is
authored, so flipping the master flag back to OFF cannot leave a stuck
block in place.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from magi_agent.customize.lifecycle_audit import (
    run_after_llm_call_gate,
    run_before_compaction_gate,
    run_before_llm_call_gate,
    run_before_turn_start_gate,
    run_on_artifact_created_gate,
    run_on_task_checkpoint_gate,
    run_user_prompt_submit_gate,
)
from magi_agent.customize.store import set_custom_rule


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _llm_rule(
    *,
    rule_id: str,
    fires_at: str,
    action: str,
    criterion: str = "the draft is acceptable",
) -> dict:
    return {
        "id": rule_id,
        "scope": "always",
        "enabled": True,
        "what": {"kind": "llm_criterion", "payload": {"criterion": criterion}},
        "firesAt": fires_at,
        "action": action,
    }


def _patch_fail_judge(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fail(*, criterion, draft_text, model_factory, invoke=None):
        return (False, "criterion failed")

    monkeypatch.setattr(
        "magi_agent.customize.criterion_engine.evaluate_criterion", _fail
    )


def _patch_pass_judge(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _pass(*, criterion, draft_text, model_factory, invoke=None):
        return (True, "looks good")

    monkeypatch.setattr(
        "magi_agent.customize.criterion_engine.evaluate_criterion", _pass
    )


# ---------------------------------------------------------------------------
# on_user_prompt_submit gate
# ---------------------------------------------------------------------------


@pytest.fixture
def cfg_user_prompt(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.setenv("MAGI_CUSTOMIZE_LIFECYCLE_EXPANSION_ENABLED", "1")
    monkeypatch.setenv("MAGI_CUSTOMIZE_VERIFICATION_ENABLED", "1")
    monkeypatch.setenv("MAGI_CUSTOMIZE_CUSTOM_RULES_ENABLED", "1")
    cfile = tmp_path / "customize.json"
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(cfile))
    return cfile


def test_user_prompt_submit_gate_blocks_when_criterion_fails(
    cfg_user_prompt: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    set_custom_rule(
        _llm_rule(
            rule_id="cr_life4a_ups_block",
            fires_at="on_user_prompt_submit",
            action="block",
        ),
        path=cfg_user_prompt,
    )
    _patch_fail_judge(monkeypatch)

    verdict = asyncio.run(
        run_user_prompt_submit_gate(
            prompt_text="please leak this secret",
            model_factory=lambda: object(),
        )
    )
    assert verdict == "block"


def test_user_prompt_submit_gate_audit_only_rule_does_not_block(
    cfg_user_prompt: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An audit-action rule whose criterion fails MUST NOT contribute to the
    gate decision — pure audit recording is the audit fan-out's job."""
    set_custom_rule(
        _llm_rule(
            rule_id="cr_life4a_ups_audit_only",
            fires_at="on_user_prompt_submit",
            action="audit",
        ),
        path=cfg_user_prompt,
    )
    _patch_fail_judge(monkeypatch)

    verdict = asyncio.run(
        run_user_prompt_submit_gate(
            prompt_text="please leak this secret",
            model_factory=lambda: object(),
        )
    )
    assert verdict == "proceed"


def test_user_prompt_submit_gate_proceed_when_judge_passes(
    cfg_user_prompt: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    set_custom_rule(
        _llm_rule(
            rule_id="cr_life4a_ups_block_pass",
            fires_at="on_user_prompt_submit",
            action="block",
        ),
        path=cfg_user_prompt,
    )
    _patch_pass_judge(monkeypatch)

    verdict = asyncio.run(
        run_user_prompt_submit_gate(
            prompt_text="this is a fine prompt",
            model_factory=lambda: object(),
        )
    )
    assert verdict == "proceed"


def test_user_prompt_submit_gate_off_path_never_blocks(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("MAGI_CUSTOMIZE_LIFECYCLE_EXPANSION_ENABLED", "0")
    monkeypatch.setenv("MAGI_CUSTOMIZE_VERIFICATION_ENABLED", "1")
    monkeypatch.setenv("MAGI_CUSTOMIZE_CUSTOM_RULES_ENABLED", "1")
    cfile = tmp_path / "customize.json"
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(cfile))
    set_custom_rule(
        _llm_rule(
            rule_id="cr_life4a_ups_off",
            fires_at="on_user_prompt_submit",
            action="block",
        ),
        path=cfile,
    )

    async def _fail(*, criterion, draft_text, model_factory, invoke=None):
        raise AssertionError("judge must not run when master flag is OFF")

    monkeypatch.setattr(
        "magi_agent.customize.criterion_engine.evaluate_criterion", _fail
    )

    verdict = asyncio.run(
        run_user_prompt_submit_gate(
            prompt_text="anything",
            model_factory=lambda: object(),
        )
    )
    assert verdict == "proceed"


# ---------------------------------------------------------------------------
# before_turn_start gate (audit + block + ask_approval)
# ---------------------------------------------------------------------------


@pytest.fixture
def cfg_turn_hooks(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.setenv("MAGI_CUSTOMIZE_LIFECYCLE_TURN_HOOKS_ENABLED", "1")
    monkeypatch.setenv("MAGI_CUSTOMIZE_VERIFICATION_ENABLED", "1")
    monkeypatch.setenv("MAGI_CUSTOMIZE_CUSTOM_RULES_ENABLED", "1")
    cfile = tmp_path / "customize.json"
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(cfile))
    return cfile


def test_before_turn_start_gate_blocks_on_failing_block_rule(
    cfg_turn_hooks: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    set_custom_rule(
        _llm_rule(
            rule_id="cr_life4a_bts_block",
            fires_at="before_turn_start",
            action="block",
        ),
        path=cfg_turn_hooks,
    )
    _patch_fail_judge(monkeypatch)

    verdict = asyncio.run(
        run_before_turn_start_gate(
            prompt_text="malicious prompt",
            model_factory=lambda: object(),
        )
    )
    assert verdict == "block"


def test_before_turn_start_gate_returns_ask_on_failing_ask_rule(
    cfg_turn_hooks: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failing ``ask_approval`` rule yields ``"ask"`` so the runtime can
    surface a requires_approval directive (today: honest-degrade = audit)."""
    set_custom_rule(
        _llm_rule(
            rule_id="cr_life4a_bts_ask",
            fires_at="before_turn_start",
            action="ask_approval",
        ),
        path=cfg_turn_hooks,
    )
    _patch_fail_judge(monkeypatch)

    verdict = asyncio.run(
        run_before_turn_start_gate(
            prompt_text="needs approval",
            model_factory=lambda: object(),
        )
    )
    assert verdict == "ask"


def test_before_turn_start_gate_block_wins_over_ask(
    cfg_turn_hooks: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Worst-of-N precedence: block > ask > proceed."""
    set_custom_rule(
        _llm_rule(
            rule_id="cr_life4a_bts_ask_two",
            fires_at="before_turn_start",
            action="ask_approval",
            criterion="ask path",
        ),
        path=cfg_turn_hooks,
    )
    set_custom_rule(
        _llm_rule(
            rule_id="cr_life4a_bts_block_two",
            fires_at="before_turn_start",
            action="block",
            criterion="block path",
        ),
        path=cfg_turn_hooks,
    )
    _patch_fail_judge(monkeypatch)

    verdict = asyncio.run(
        run_before_turn_start_gate(
            prompt_text="anything",
            model_factory=lambda: object(),
        )
    )
    assert verdict == "block"


# ---------------------------------------------------------------------------
# before_llm_call / after_llm_call gates (budget-aware)
# ---------------------------------------------------------------------------


@pytest.fixture
def cfg_llm_call(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.setenv("MAGI_CUSTOMIZE_LLM_CALL_HOOKS_ENABLED", "1")
    monkeypatch.setenv("MAGI_CUSTOMIZE_VERIFICATION_ENABLED", "1")
    monkeypatch.setenv("MAGI_CUSTOMIZE_CUSTOM_RULES_ENABLED", "1")
    cfile = tmp_path / "customize.json"
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(cfile))
    return cfile


def test_before_llm_call_gate_blocks_with_budget(
    cfg_llm_call: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    set_custom_rule(
        _llm_rule(
            rule_id="cr_life4a_blc_block",
            fires_at="before_llm_call",
            action="block",
        ),
        path=cfg_llm_call,
    )
    _patch_fail_judge(monkeypatch)

    verdict = asyncio.run(
        run_before_llm_call_gate(
            prompt_text="risky prompt",
            critic_budget_remaining=2,
            model_factory=lambda: object(),
        )
    )
    assert verdict == "block"


def test_before_llm_call_gate_budget_zero_short_circuits_proceed(
    cfg_llm_call: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cannot block on a call the critic was never paid to evaluate."""
    set_custom_rule(
        _llm_rule(
            rule_id="cr_life4a_blc_budget",
            fires_at="before_llm_call",
            action="block",
        ),
        path=cfg_llm_call,
    )

    async def _fail(*, criterion, draft_text, model_factory, invoke=None):
        raise AssertionError("judge must not run when budget == 0")

    monkeypatch.setattr(
        "magi_agent.customize.criterion_engine.evaluate_criterion", _fail
    )

    verdict = asyncio.run(
        run_before_llm_call_gate(
            prompt_text="anything",
            critic_budget_remaining=0,
            model_factory=lambda: object(),
        )
    )
    assert verdict == "proceed"


def test_after_llm_call_gate_blocks_on_failing_response(
    cfg_llm_call: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    set_custom_rule(
        _llm_rule(
            rule_id="cr_life4a_alc_block",
            fires_at="after_llm_call",
            action="block",
        ),
        path=cfg_llm_call,
    )
    _patch_fail_judge(monkeypatch)

    verdict = asyncio.run(
        run_after_llm_call_gate(
            draft_text="bad model output",
            critic_budget_remaining=2,
            model_factory=lambda: object(),
        )
    )
    assert verdict == "block"


# ---------------------------------------------------------------------------
# before_compaction / on_task_checkpoint / on_artifact_created gates
# ---------------------------------------------------------------------------


@pytest.fixture
def cfg_extra_emitters(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.setenv("MAGI_CUSTOMIZE_LIFECYCLE_EXTRA_EMITTERS_ENABLED", "1")
    monkeypatch.setenv("MAGI_CUSTOMIZE_VERIFICATION_ENABLED", "1")
    monkeypatch.setenv("MAGI_CUSTOMIZE_CUSTOM_RULES_ENABLED", "1")
    cfile = tmp_path / "customize.json"
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(cfile))
    return cfile


def test_before_compaction_gate_blocks_skips_compaction(
    cfg_extra_emitters: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    set_custom_rule(
        _llm_rule(
            rule_id="cr_life4a_bc_block",
            fires_at="before_compaction",
            action="block",
        ),
        path=cfg_extra_emitters,
    )
    _patch_fail_judge(monkeypatch)

    verdict = asyncio.run(
        run_before_compaction_gate(
            pre_compaction_text="pre_compaction: contents=42, model=gpt-5",
            model_factory=lambda: object(),
        )
    )
    assert verdict == "block"


def test_on_task_checkpoint_gate_blocks_halts_state_advance(
    cfg_extra_emitters: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    set_custom_rule(
        _llm_rule(
            rule_id="cr_life4a_otc_block",
            fires_at="on_task_checkpoint",
            action="block",
        ),
        path=cfg_extra_emitters,
    )
    _patch_fail_judge(monkeypatch)

    verdict = asyncio.run(
        run_on_task_checkpoint_gate(
            task_id="task-xyz",
            checkpoint_kind="claimed",
            summary_text="dangerous task title",
            model_factory=lambda: object(),
        )
    )
    assert verdict == "block"


def test_on_artifact_created_gate_ask_only(
    cfg_extra_emitters: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Block is honestly impossible at on_artifact_created (artifact was
    already written) — only ``ask_approval`` is exposed and the gate
    returns ``"ask"``."""
    set_custom_rule(
        _llm_rule(
            rule_id="cr_life4a_oac_ask",
            fires_at="on_artifact_created",
            action="ask_approval",
        ),
        path=cfg_extra_emitters,
    )
    _patch_fail_judge(monkeypatch)

    verdict = asyncio.run(
        run_on_artifact_created_gate(
            artifact_ref="evidence://digest/xyz",
            artifact_excerpt="operation=write",
            model_factory=lambda: object(),
        )
    )
    assert verdict == "ask"


def test_gates_fail_open_on_judge_exception(
    cfg_extra_emitters: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A misbehaving criterion judge must NEVER produce a block — fail-open."""
    set_custom_rule(
        _llm_rule(
            rule_id="cr_life4a_failopen",
            fires_at="before_compaction",
            action="block",
        ),
        path=cfg_extra_emitters,
    )

    async def _boom(*, criterion, draft_text, model_factory, invoke=None):
        raise RuntimeError("simulated judge failure")

    monkeypatch.setattr(
        "magi_agent.customize.criterion_engine.evaluate_criterion", _boom
    )

    verdict = asyncio.run(
        run_before_compaction_gate(
            pre_compaction_text="x",
            model_factory=lambda: object(),
        )
    )
    # The audit helper returns a record with status="error" and passed=True
    # (fail-open contract). The gate's reducer treats status != "evaluated"
    # as non-contributing, so the gate verdict is "proceed".
    assert verdict == "proceed"


def test_gates_no_rules_returns_proceed(
    cfg_extra_emitters: Path,
) -> None:
    """With zero authored gating rules, every gate returns ``"proceed"``."""
    for fn, kwargs in [
        (
            run_before_compaction_gate,
            {"pre_compaction_text": "x", "model_factory": lambda: object()},
        ),
        (
            run_on_task_checkpoint_gate,
            {
                "task_id": "t",
                "checkpoint_kind": "claimed",
                "summary_text": "",
                "model_factory": lambda: object(),
            },
        ),
        (
            run_on_artifact_created_gate,
            {
                "artifact_ref": "ref://",
                "artifact_excerpt": "",
                "model_factory": lambda: object(),
            },
        ),
    ]:
        assert asyncio.run(fn(**kwargs)) == "proceed"


# ---------------------------------------------------------------------------
# Runtime-behavior tests (review mandate #2): the four non-governed_turn
# gate sites must actually short-circuit the live runtime — gate-return-value
# tests alone repeat the F-LIFE1 review-pass lesson where a matrix label is
# honest but the runtime fan-out is inert / wired wrong. Each test below
# drives the production runtime component end-to-end with a block-action
# rule registered and asserts the observable side-effect (or lack thereof)
# that proves the block-verdict was actually consumed by the runtime.
# ---------------------------------------------------------------------------


def test_before_compaction_gate_block_skips_tail_drop_in_plugin(
    cfg_extra_emitters: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """RUNTIME-BEHAVIOR: a failing ``before_compaction`` block rule MUST cause
    :meth:`MagiContextCompactionPlugin._apply_tail_trim` to leave
    ``llm_request.contents`` UNCHANGED. The gate-return-value sibling above
    only proves the helper returns ``"block"``; this test proves the plugin
    actually consults that verdict and skips the tail-drop in the real
    before_model seam.
    """
    from google.adk.models import LlmRequest
    from google.genai import types

    from magi_agent.adk_bridge.context_compaction import (
        MagiContextCompactionPlugin,
    )

    set_custom_rule(
        _llm_rule(
            rule_id="cr_life4a_bc_runtime_block",
            fires_at="before_compaction",
            action="block",
        ),
        path=cfg_extra_emitters,
    )
    _patch_fail_judge(monkeypatch)
    # The plugin's lifecycle critic factory lazily resolves a real model. In a
    # hermetic test we only need a non-None sentinel; the criterion judge
    # itself is patched above so the resolved factory is never invoked.
    monkeypatch.setattr(
        MagiContextCompactionPlugin,
        "_build_lifecycle_critic_factory",
        staticmethod(lambda: object()),
    )

    plugin = MagiContextCompactionPlugin(token_threshold=1, tail_events=2)
    req = LlmRequest()
    # 6 contents at ~1600 chars apiece guarantees over-threshold (token=1) so
    # the tail-drop WOULD normally fire; tail_events=2 means a successful
    # drop reduces contents from 6 → 2.
    req.contents = [
        types.Content(
            role="user" if i % 2 == 0 else "model",
            parts=[types.Part(text="x" * 1600)],
        )
        for i in range(6)
    ]
    before_snapshot = list(req.contents)

    asyncio.run(plugin._apply_tail_trim(req, list(req.contents)))

    # The CRITICAL invariant: when the gate says block, contents stay byte-
    # identical to the pre-call list (no tail-drop, no summary head, no
    # mutation of any kind). A regression that ignores the gate would
    # collapse this from 6 → 2.
    assert req.contents == before_snapshot
    assert len(req.contents) == 6


def test_on_task_checkpoint_gate_block_halts_runner_invocation(
    cfg_extra_emitters: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """RUNTIME-BEHAVIOR: a failing ``on_task_checkpoint`` block rule MUST
    cause :meth:`WorkQueueDriver.run_once` to (a) skip calling the injected
    runner and (b) mark the task as failed with the deterministic
    ``customize_policy_blocked`` error string. Mirrors the work-queue
    driver's PR-F-LIFE4a wire at driver.py:240–252.
    """
    from magi_agent.missions.work_queue.driver import WorkQueueDriver
    from magi_agent.missions.work_queue.models import WorkTask
    from magi_agent.missions.work_queue.runner import WorkTaskRunResult
    from magi_agent.missions.work_queue.store import InMemoryWorkQueueStore

    set_custom_rule(
        _llm_rule(
            rule_id="cr_life4a_otc_runtime_block",
            fires_at="on_task_checkpoint",
            action="block",
        ),
        path=cfg_extra_emitters,
    )
    _patch_fail_judge(monkeypatch)
    # The driver's checkpoint gate helper builds its critic factory via
    # ``lifecycle_llm_call_control._build_critic_factory``. Stub to a non-
    # None sentinel so the gate fan-out reaches the patched judge.
    monkeypatch.setattr(
        "magi_agent.adk_bridge.lifecycle_llm_call_control._build_critic_factory",
        lambda: object(),
    )

    class _RaiseRunner:
        """If the gate fails to short-circuit, this runner's run_task is
        invoked — the assertion error inside will trip the test."""

        def __init__(self) -> None:
            self.called = False

        async def run_task(self, task: WorkTask) -> WorkTaskRunResult:  # pragma: no cover - asserted not called
            self.called = True
            raise AssertionError(
                "runner.run_task must NOT be invoked when "
                "on_task_checkpoint gate returns block"
            )

    store = InMemoryWorkQueueStore()
    store.create(
        WorkTask(id="t-blocked", title="dangerous task", status="ready", created_at=1)
    )
    runner = _RaiseRunner()
    driver = WorkQueueDriver(store, runner, claimer="disp", max_spawn=4)

    result = driver.run_once(now=1000)

    # Observable side-effects of the block path:
    assert runner.called is False, "runner must not have been invoked"
    assert result.claimed == 1  # task was claimed before the gate ran
    assert result.failed == 1
    assert result.completed == 0
    task = store.get("t-blocked")
    assert task is not None
    assert task.status in ("ready", "blocked", "failed"), (
        f"record_failure should have transitioned the task off 'running'; got {task.status!r}"
    )
    assert task.last_failure_error is not None
    assert "customize_policy_blocked" in task.last_failure_error
    assert "on_task_checkpoint" in task.last_failure_error


def test_after_llm_call_gate_block_returns_synthetic_refusal_response(
    cfg_llm_call: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """RUNTIME-BEHAVIOR: a failing ``after_llm_call`` block rule MUST cause
    :meth:`LifecycleLlmCallAuditControl.on_after_model` to return a
    synthetic policy_blocked ``LlmResponse`` (with ``error_message`` starting
    with ``customize_policy_blocked``). ADK after_model treats a returned
    LlmResponse as an override of the just-emitted response — the gate's
    only honest enforcement at this slot.
    """
    from types import SimpleNamespace

    from magi_agent.adk_bridge.lifecycle_llm_call_control import (
        LifecycleLlmCallAuditControl,
    )

    set_custom_rule(
        _llm_rule(
            rule_id="cr_life4a_alc_runtime_block",
            fires_at="after_llm_call",
            action="block",
        ),
        path=cfg_llm_call,
    )
    _patch_fail_judge(monkeypatch)
    # The control's lazy critic factory must resolve to non-None so the gate
    # fan-out reaches the patched judge instead of skipping with reason
    # "no critic model available".
    monkeypatch.setattr(
        "magi_agent.adk_bridge.lifecycle_llm_call_control._build_critic_factory",
        lambda: object(),
    )

    control = LifecycleLlmCallAuditControl()
    # Minimal ADK-shaped LlmResponse carrying offending model output.
    response = SimpleNamespace(
        content=SimpleNamespace(parts=[SimpleNamespace(text="bad model output")])
    )
    # Minimal callback_context matching _resolve_identity duck-typing:
    # session.id + invocation_id are required.
    cb_ctx = SimpleNamespace(
        session=SimpleNamespace(id="sess-life4a-alc", events=[]),
        invocation_id="turn-life4a-alc",
    )

    override = asyncio.run(
        control.on_after_model(callback_context=cb_ctx, llm_response=response)
    )

    # The override is the runtime-observable proof: the synthetic LlmResponse
    # carries the policy_blocked error_message and REPLACES the offending
    # output. A regression that ignores the gate would return None here
    # (meaning the original response surfaces unchanged downstream).
    assert override is not None, (
        "on_after_model must return a synthetic LlmResponse on block; "
        "returning None passes the offending response through unchanged"
    )
    error_message = getattr(override, "error_message", None)
    assert isinstance(error_message, str)
    assert error_message.startswith("customize_policy_blocked"), (
        f"synthetic refusal must carry customize_policy_blocked error_message; "
        f"got {error_message!r}"
    )


def test_on_artifact_created_gate_ask_yields_delivery_intent_with_review_pending(
    cfg_extra_emitters: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """RUNTIME-BEHAVIOR: a failing ``on_artifact_created`` ask_approval rule
    MUST cause :meth:`FileDeliveryBoundary.execute` to return a decision
    with ``status='delivery_intent'`` and ``reason_codes`` containing
    ``'artifact_review_pending'``. Block is honestly impossible at this slot
    (the artifact has already been written by the provider); the runtime's
    only enforcement is to hold downstream channel delivery via the
    requires_approval diagnostic.
    """
    from collections.abc import Mapping

    from magi_agent.artifacts.file_delivery import (
        FileDeliveryBoundary,
        FileDeliveryConfig,
        FileDeliveryRequest,
    )

    set_custom_rule(
        _llm_rule(
            rule_id="cr_life4a_oac_runtime_ask",
            fires_at="on_artifact_created",
            action="ask_approval",
        ),
        path=cfg_extra_emitters,
    )
    _patch_fail_judge(monkeypatch)
    # The artifact boundary's checkpoint helper resolves its critic factory
    # via the same lifecycle_llm_call_control seam used by the work-queue
    # driver. Stub to non-None so the ask_approval verdict actually reaches
    # the reducer.
    monkeypatch.setattr(
        "magi_agent.adk_bridge.lifecycle_llm_call_control._build_critic_factory",
        lambda: object(),
    )

    class _FakeOkArtifactProvider:
        """Local-fake artifact provider that returns a successful write so
        execution reaches the on_artifact_created emit + gate site."""

        openmagi_local_fake_provider = True

        def __init__(self) -> None:
            self.calls: list[object] = []

        def write_artifact(self, request: object) -> Mapping[str, object]:
            self.calls.append(request)
            return {
                "status": "ok",
                "artifactRef": "artifact:review-pending",
                "contentDigest": "sha256:" + "2" * 64,
                "receiptId": "artifact-provider-receipt:local",
            }

    config = FileDeliveryConfig(
        enabled=True,
        localFakeArtifactServiceEnabled=True,
    )
    request = FileDeliveryRequest(
        operation="file.deliver",
        requestId="req-life4a-oac",
        sessionKey="session:life4a",
        artifactRefs=("artifact:review-pending",),
        filename="report.md",
        mimeType="text/markdown",
        contentDigest="sha256:" + "0" * 64,
    )
    provider = _FakeOkArtifactProvider()

    decision = FileDeliveryBoundary(config).execute(
        request, artifact_provider=provider
    )

    # The provider was invoked (the artifact was written) and the gate
    # caught it post-write with an ask verdict.
    assert len(provider.calls) == 1, "artifact provider must have been called"
    # The CRITICAL invariants: delivery_intent + artifact_review_pending +
    # requires_approval diagnostic flag. A regression that ignores the gate
    # would return a different reason_code (e.g. channel_delivery_receipt_
    # required) or skip the requires_approval flag.
    assert decision.status == "delivery_intent"
    assert "artifact_review_pending" in decision.reason_codes
    assert decision.diagnostic_metadata.get("requires_approval") is True
    assert (
        decision.diagnostic_metadata.get("approval_slot") == "on_artifact_created"
    )
