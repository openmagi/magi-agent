"""DeepSolve native tool handler (U3).

Binds the deep-solve orchestrator (``magi_agent.solving.deep_solve``) to the
first-party native tool surface. Mirrors ``plugins/native/subagents.py`` for
conventions:

- ALL heavy imports are lazy inside the handler function (never at module level).
- Never-raise contract: any exception → blocked ``ToolResult``.
- Gate order: (a) ``is_deep_solve_enabled()`` → honest disabled result;
  (a2) ``_deep_solve_pack_enabled()`` → honest pack-removed result (U4);
  (b) ``is_live_child_runner_enabled()`` → not_attached result;
  (c) run.

Full-text seam (B1):
  The orchestrator's ``DeepSolveDeps.run_stage`` implementation captures the
  untrimmed final text from ``RealLocalChildRunner`` via the ``full_text_sink``
  parameter added in U3 and stores it in the ``StageResult.full_text`` field.
  This channel is strictly parent-runtime-internal; it is never emitted via
  any public SSE/ToolResult field.

Verdict seam gap (documented):
  ``LocalToolCollector`` is NOT reachable from ``ToolContext`` (no
  ``evidence_collector`` field on ``ToolContext``). The verdict is therefore
  appended to the ``ToolResult.output`` payload directly as
  ``deepSolveVerdict``. Wiring the collector to the native tool context is a
  follow-up task (once ``ToolContext`` carries a dedicated evidence sink
  analogous to ``citationEvidenceSink``).

Thinking-budget seam gap (documented):
  ``RealLocalChildRunner`` currently has no ``thinking_budget`` parameter;
  attempting to pass one to the underlying provider call would require invasive
  re-plumbing of provider dispatch. Instead, solver/verifier stages receive an
  elevated ``budgetMs`` (120 000 ms) to encourage more reasoning time. A
  dedicated ``thinking_budget`` seam is deferred to a follow-up unit.
"""
from __future__ import annotations

import uuid
from collections.abc import Mapping

# NOTE: NO runtime imports at module level — keep import-clean so loading
#       this module never pulls child_runner_live / litellm / google.adk.
from magi_agent.plugins.native._common import digest
from magi_agent.tools.context import ToolContext
from magi_agent.tools.result import ToolResult

# ---------------------------------------------------------------------------
# Ordering for toolset clamping (B3)
# ---------------------------------------------------------------------------

_TOOLSET_ORDER: dict[str, int] = {"none": 0, "readonly": 1, "inherit": 2, "full": 3}
_TOOLSET_NAMES: tuple[str, ...] = ("none", "readonly", "inherit", "full")

# Trace tag so the operator can grep for demotion events without touching logs.
_CLAMP_TRACE_TAG = "[deep_solve.trace] toolset_demoted"


def _emit_clamp_trace(gate: str, request: str) -> None:
    """Emit a trace note when the operator gate demotes a stage toolset."""
    # Use the same _emit_trace mechanism as child_runner_live for
    # operator-grep-ability. Fail silently — trace must never affect execution.
    try:
        from magi_agent.runtime.child_runner_live import _emit_trace  # noqa: PLC0415

        _emit_trace(
            f"{_CLAMP_TRACE_TAG} gate={gate!r} stage_request={request!r} "
            f"→ clamped to {gate!r}"
        )
    except Exception:  # noqa: BLE001
        pass


def clamp_stage_toolset(operator_gate: str, stage_request: str) -> str:
    """Return min(operator_gate, stage_request) on the none<readonly<full order.

    When the gate demotes the stage request (e.g. ``readonly`` → ``none``),
    a trace note is emitted via ``_emit_trace`` so operators can grep for it.

    Unknown values are treated as "none" (fail-closed).

    Args:
        operator_gate:   Resolved ``MAGI_CHILD_RUNNER_TOOLSET`` profile.
        stage_request:   Toolset the pipeline stage requests (from design D5).

    Returns:
        The clamped profile literal.
    """
    gate_ord = _TOOLSET_ORDER.get(operator_gate, 0)
    req_ord = _TOOLSET_ORDER.get(stage_request, 0)
    clamped_ord = min(gate_ord, req_ord)
    clamped = _TOOLSET_NAMES[clamped_ord]
    # Emit a trace note when the gate demotes the request (B3).
    if clamped_ord < req_ord:
        _emit_clamp_trace(operator_gate, stage_request)
    return clamped


# ---------------------------------------------------------------------------
# Result helpers (mirror subagents._spawn_agent_result style)
# ---------------------------------------------------------------------------

_TOOL_NAME = "DeepSolve"


def _deep_solve_result(
    status: str,
    output: Mapping[str, object],
    *,
    error_code: str | None = None,
) -> ToolResult:
    safe_output = dict(output)
    output_digest = digest(safe_output)
    metadata: dict[str, object] = {
        "toolName": _TOOL_NAME,
        "handler": "first_party_native_local",
        "outputDigest": output_digest,
    }
    if error_code:
        metadata["reason"] = error_code
    llm_output: dict[str, object] = {
        "status": safe_output.get("status"),
    }
    verdict = safe_output.get("deepSolveVerdict")
    if isinstance(verdict, dict):
        llm_output["acceptanceBasis"] = verdict.get("acceptance_basis")
    reason = error_code or safe_output.get("reason")
    if isinstance(reason, str) and reason.strip():
        llm_output["reason"] = reason
    summary = safe_output.get("summary")
    if isinstance(summary, str) and summary.strip():
        llm_output["summary"] = summary
    from magi_agent.tools.result import ToolStatus  # noqa: PLC0415

    tool_status: ToolStatus = "ok" if status == "ok" else (
        "error" if status == "error" else "blocked"
    )
    return ToolResult(
        status=tool_status,
        output=safe_output,
        llmOutput=llm_output,
        transcriptOutput={
            "toolName": _TOOL_NAME,
            "outputDigest": output_digest,
        },
        errorCode=error_code,
        errorMessage=error_code,
        metadata=metadata,
    )


# ---------------------------------------------------------------------------
# Pack-installed dispatch gate (U4)
# ---------------------------------------------------------------------------

_DEEP_SOLVE_PACK_ID = "openmagi.deep-solve"


def _deep_solve_pack_enabled() -> bool:
    """Return whether config.toml/packs-state.json leave the bundled pack installed.

    Mirrors ``tools/persistent_python_toolhost.py::_persistent_python_pack_enabled``:
    the manifest loader already applies ``[packs] disable`` (merged with the
    dashboard install/remove sidecar ``packs-state.json``), but the native tool
    handler is registered via ``native_catalog`` independently of pack
    discovery, so dispatch must consult the SAME removal contract to keep the
    pack an honest install axis (design D2). Fail-open on loader errors —
    an infra failure must never block dispatch.
    """
    try:
        from magi_agent.packs.discovery import load_packs_config  # noqa: PLC0415

        return _DEEP_SOLVE_PACK_ID not in set(load_packs_config().disable)
    except Exception:  # noqa: BLE001
        return True


# ---------------------------------------------------------------------------
# Main handler
# ---------------------------------------------------------------------------

async def deep_solve(arguments: dict[str, object], context: ToolContext) -> ToolResult:
    """Run the deep-solve verification-refinement pipeline for a math/CS problem.

    Gate order:
    1. ``is_deep_solve_enabled()`` OFF → honest disabled blocked result.
    2. ``_deep_solve_pack_enabled()`` FALSE (pack removed/uninstalled) →
       honest "deep_solve_pack_removed" blocked result (U4).
    3. ``is_live_child_runner_enabled()`` OFF → honest not_attached blocked result.
    4. Run pipeline.
    """
    # ------------------------------------------------------------------
    # Gate phase — also never-raise (F4): a failing lazy import or flag
    # evaluation degrades to a blocked result, not an exception.
    # ------------------------------------------------------------------
    try:
        from magi_agent.config.env import is_deep_solve_enabled  # noqa: PLC0415
        from magi_agent.runtime.child_runner_live import (  # noqa: PLC0415
            is_live_child_runner_enabled,
        )

        deep_solve_on = is_deep_solve_enabled()
        pack_installed = _deep_solve_pack_enabled()
        child_runner_on = is_live_child_runner_enabled()
    except Exception:  # noqa: BLE001 — NEVER raise out of deep_solve
        return _deep_solve_result(
            "blocked",
            {"status": "blocked", "reason": "deep_solve_gate_error"},
            error_code="deep_solve_gate_error",
        )

    # Gate (a): deep_solve feature flag
    if not deep_solve_on:
        output = {
            "status": "blocked",
            "reason": "deep_solve_disabled",
            "hint": (
                "DeepSolve is disabled. Set MAGI_DEEP_SOLVE_ENABLED=1 "
                "(or ensure the runtime profile is not conservative/safe/off) "
                "to enable the verification-refinement pipeline."
            ),
        }
        return _deep_solve_result("blocked", output, error_code="deep_solve_disabled")

    # Gate (a2): pack installed — removing/uninstalling the openmagi.deep-solve
    # pack blocks dispatch honestly (U4; install axis stays real).
    if not pack_installed:
        output = {
            "status": "blocked",
            "reason": "deep_solve_pack_removed",
            "hint": (
                "The openmagi.deep-solve pack is removed. Reinstall it "
                "(remove it from config.toml [packs] disable or reinstall via "
                "the dashboard) to enable the DeepSolve tool."
            ),
        }
        return _deep_solve_result(
            "blocked", output, error_code="deep_solve_pack_removed"
        )

    # Gate (b): live child runner attached
    if not child_runner_on:
        output = {
            "status": "not_attached",
            "reason": "live_child_runner_disabled",
            "hint": (
                "Live child runner is disabled. Set "
                "MAGI_CHILD_RUNNER_LIVE_ENABLED=1 to enable deep-solve."
            ),
            "liveChildRunnerAttached": False,
        }
        return _deep_solve_result("blocked", output, error_code="live_child_runner_disabled")

    # ------------------------------------------------------------------
    # Live path — all heavy imports are lazy below this line
    # ------------------------------------------------------------------
    try:
        return await _run_deep_solve_live(arguments, context)
    except Exception:  # noqa: BLE001 — NEVER raise out of deep_solve
        fallback_output: dict[str, object] = {
            "status": "blocked",
            "reason": "deep_solve_attach_failed",
            "liveChildRunnerAttached": False,
        }
        return _deep_solve_result(
            "blocked",
            fallback_output,
            error_code="deep_solve_attach_failed",
        )


async def _run_deep_solve_live(
    arguments: dict[str, object],
    context: ToolContext,
) -> ToolResult:
    """Live path: build deps and invoke the orchestrator.

    Separated from ``deep_solve`` so the outer except clause catches ANY
    failure in construction or orchestration without a nested try block.
    """
    from magi_agent.runtime.child_runner_boundary import (  # noqa: PLC0415
        ChildRunnerConfig,
        ChildTaskRequest,
        LocalChildRunnerBoundary,
    )
    from magi_agent.runtime.child_runner_live import (  # noqa: PLC0415
        RealLocalChildRunner,
    )
    from magi_agent.runtime.child_toolset import (  # noqa: PLC0415
        resolve_child_toolset_profile,
    )
    from magi_agent.runtime.public_events import (  # noqa: PLC0415
        child_cancelled_event,
        child_completed_event,
        child_failed_event,
        child_started_event,
    )
    from magi_agent.solving.deep_solve import (  # noqa: PLC0415
        DeepSolveConfig,
        DeepSolveDeps,
        DeepSolveVerdictData,
        ExecutionReport,
        StageResult,
        run_deep_solve,
    )
    from magi_agent.solving.templates import DomainTemplate  # noqa: PLC0415

    # ------------------------------------------------------------------
    # Parse arguments
    # ------------------------------------------------------------------
    problem = str(arguments.get("problem") or "").strip()
    if not problem:
        output: dict[str, object] = {
            "status": "blocked",
            "reason": "deep_solve_missing_problem",
            "hint": "Supply a `problem` argument with the problem statement.",
        }
        return _deep_solve_result(
            "blocked", output, error_code="deep_solve_missing_problem"
        )

    test_command: str | None = (
        str(arguments["test_command"]).strip() or None
        if arguments.get("test_command")
        else None
    ) or (
        str(arguments["tests"]).strip() or None if arguments.get("tests") else None
    )

    raw_domain = arguments.get("domain")
    domain: DomainTemplate | None = (
        str(raw_domain).strip() or None if raw_domain else None  # type: ignore[assignment]
    )

    raw_passes = arguments.get("consecutive_clean_passes")
    if isinstance(raw_passes, int | float) and not isinstance(raw_passes, bool):
        consecutive_clean_passes = max(1, int(raw_passes))
    else:
        consecutive_clean_passes = 3

    language = str(arguments.get("language") or "python3").strip() or "python3"

    req_provider: str | None = (
        str(arguments["provider"]).strip() or None if arguments.get("provider") else None
    )
    req_model: str | None = (
        str(arguments["model"]).strip() or None if arguments.get("model") else None
    )

    config = DeepSolveConfig(
        problem=problem,
        domain=domain,
        test_command=test_command,
        consecutive_clean_passes=consecutive_clean_passes,
        language=language,
    )

    # ------------------------------------------------------------------
    # Parent identifiers for child events
    # ------------------------------------------------------------------
    parent_exec_id = (
        context.session_id or context.turn_id
        or f"deep-solve-parent-{uuid.uuid4().hex[:12]}"
    )
    turn_id = context.turn_id or f"deep-solve-turn-{uuid.uuid4().hex[:12]}"
    run_id = f"deep-solve-{uuid.uuid4().hex[:8]}"

    # ------------------------------------------------------------------
    # Toolset profile from operator gate (B3)
    # ------------------------------------------------------------------
    operator_gate = resolve_child_toolset_profile()

    # ------------------------------------------------------------------
    # Workspace selection (mirrors subagents._child_workspace_for_toolset)
    # ------------------------------------------------------------------
    from magi_agent.plugins.native.subagents import (  # noqa: PLC0415
        _child_workspace_for_toolset,
        _child_event_receipt_ref,
        _emit_agent_event,
    )

    # Receipt ref for the run as a whole (lifecycle events).
    run_receipt_ref = _child_event_receipt_ref(
        parent_execution_id=parent_exec_id,
        task_id=run_id,
    )

    # ------------------------------------------------------------------
    # Emit run-started child event
    # ------------------------------------------------------------------
    from magi_agent.runtime.public_events import child_started_event  # noqa: PLC0415

    await _emit_agent_event(
        context,
        child_started_event(
            task_id=run_id,
            parent_turn_id=turn_id,
            child_receipt_ref=run_receipt_ref,
            agent_name="DeepSolve",
            model=f"{req_provider or 'anthropic'}:{req_model}" if req_model else None,
            task_title=f"Deep-solve: {problem[:60].strip()}...",
        ),
    )

    # ------------------------------------------------------------------
    # Build DeepSolveDeps — the binding layer between orchestrator and runtime
    # ------------------------------------------------------------------

    # Cumulative agents count across all stage runs.
    agents_total: list[int] = [0]  # mutable list to allow mutation from nested func

    # Collected verdicts (should be exactly one).
    verdicts: list[DeepSolveVerdictData] = []

    # Stage child refs for verdict record.
    stage_child_refs: list[str] = []

    async def _run_stage(
        *,
        stage: str,
        role: str,
        toolset_request: str,
        objective: str,
        agents_spent_so_far: int,
    ) -> StageResult:
        """Invoke one pipeline stage via RealLocalChildRunner."""
        # Clamp to operator gate (B3).
        toolset_profile = clamp_stage_toolset(operator_gate, toolset_request)

        stage_task_id = f"{run_id}-{stage}-{uuid.uuid4().hex[:6]}"
        stage_receipt_ref = _child_event_receipt_ref(
            parent_execution_id=parent_exec_id,
            task_id=stage_task_id,
        )

        # Stage workspace: resolve for this stage's clamped toolset.
        stage_workspace, ws_err = _child_workspace_for_toolset(
            toolset_profile, context
        )
        if ws_err and toolset_profile != "none":
            # A readonly/full stage running without its workspace is a silent
            # degrade operators should be able to see (review F5).
            try:
                from magi_agent.runtime.child_runner_live import (  # noqa: PLC0415
                    _emit_trace,
                )

                _emit_trace(
                    f"deep_solve stage={stage!r} workspace degraded: {ws_err!r}"
                )
            except Exception:  # noqa: BLE001
                pass

        # Emit stage-start event.
        await _emit_agent_event(
            context,
            {
                "type": "child_progress",
                "taskId": run_id,
                "detail": f"Stage {stage}: running {role} agent…",
                "childReceiptRef": run_receipt_ref,
            },
        )

        # B1 full-text sink: capture untrimmed output for the orchestrator.
        captured_full_text: list[str] = []

        def _full_text_sink(text: str) -> None:
            captured_full_text.append(text)

        # A3 gap: no native thinking_budget param on RealLocalChildRunner.
        # Instead, give solver/verifier stages an elevated budgetMs so the
        # model has more time to reason. See module docstring for the gap note.
        # The budget is a last-resort hang backstop, not a work budget: a
        # deep-solve stage is real long-form reasoning work and must not be
        # killed mid-solve, so these are effectively unlimited (raised ~30x).
        # A genuinely-hung stage is still bounded; commit 1 preserves partial
        # work even if the backstop fires.
        solver_budget_ms = 3_600_000  # 60 min per solver stage (was 2 min)
        is_solver_stage = role == "coding"
        stage_budget_ms = solver_budget_ms if is_solver_stage else 1_800_000

        runner = RealLocalChildRunner(
            toolset_profile=toolset_profile,
            workspace_root=stage_workspace,
            full_text_sink=_full_text_sink,
            progress_sink=None,  # progress is surfaced via _emit_agent_event above
        )
        child_config = ChildRunnerConfig(
            enabled=True,
            liveChildRunnerEnabled=True,
        )

        parent_memory_mode_value: str = getattr(
            context.memory_mode, "value", str(context.memory_mode)
        )
        request = ChildTaskRequest(
            parentExecutionId=parent_exec_id,
            turnId=turn_id,
            taskId=stage_task_id,
            objective=objective,
            metadata={
                "spawnDepth": context.spawn_depth + 1,
                "parentToolNames": context.parent_tool_names,
                "parentMemoryMode": parent_memory_mode_value,
                "deepSolveStage": stage,
                "deepSolveRole": role,
            },
            provider=req_provider,
            model=req_model,
            budgetMs=stage_budget_ms,
            spawnCap=context.spawn_cap,
        )

        boundary = LocalChildRunnerBoundary(
            child_config,
            child_runner=runner,
            agents_spawned_so_far=agents_spent_so_far,
        )
        result = await boundary.run(request)

        # Project envelope.
        envelope = result.envelope
        sanitized_summary = str(getattr(envelope, "summary", "") or "")
        child_ref = str(getattr(envelope, "childExecutionId", "") or "")

        # B1: use captured untrimmed text when available; fall back to sanitized.
        full_text = captured_full_text[0] if captured_full_text else sanitized_summary

        agents_total[0] = agents_spent_so_far + 1
        if child_ref:
            stage_child_refs.append(child_ref)

        return StageResult(
            stage_id=stage,
            full_text=full_text,
            sanitized_summary=sanitized_summary,
            child_ref=child_ref or None,
            agents_spent=1,
        )

    async def _execute_tests(
        *,
        artifact: str,
        test_command: str,
    ) -> ExecutionReport:
        """Execute test_command through the GOVERNED parent Bash toolhost.

        The command runs on the same surface the model's own Bash tool calls
        take (memory-mode guard + Gate5B toolhost caps/redaction/timeout) —
        never a raw subprocess (design D5/§6: parent toolhost under the
        existing gates). The artifact is written to a run-scoped dir under the
        writable workspace root and cleaned up afterwards.

        Never-raise: execution failure → ExecutionReport with raw_output
        carrying the error string.
        """
        import hashlib  # noqa: PLC0415
        import shlex  # noqa: PLC0415
        import shutil  # noqa: PLC0415
        from pathlib import Path  # noqa: PLC0415

        from magi_agent.tools.core_toolhost import (  # noqa: PLC0415
            standalone_core_tool_handler,
        )

        cmd_digest = "sha256:" + hashlib.sha256(test_command.encode()).hexdigest()
        run_dir: Path | None = None

        try:
            # Run-scoped artifact under the writable workspace root (F2).
            workspace_base = (
                context.spawn_workspace or context.workspace_root or "."
            )
            run_dir = Path(workspace_base).expanduser() / ".deep-solve" / run_id
            run_dir.mkdir(parents=True, exist_ok=True)
            artifact_path = run_dir / "artifact.py"
            artifact_path.write_text(artifact, encoding="utf-8")

            # Governed Bash dispatch — same gates as a model Bash call.
            bash_handler = standalone_core_tool_handler(
                "Bash", command_timeout_ms=120_000
            )
            command = (
                f"DEEP_SOLVE_ARTIFACT={shlex.quote(str(artifact_path))} "
                f"{test_command}"
            )
            result = await bash_handler({"command": command}, context)

            output = result.output if isinstance(result.output, Mapping) else {}
            exit_code = output.get("exitCode")
            stdout = str(output.get("stdout") or "")
            if result.status != "ok":
                # Blocked/error at the gate (memory-mode, toolhost policy…):
                # honest failure report, never a bypass.
                return ExecutionReport(
                    command_digest=cmd_digest,
                    total=1,
                    passed=0,
                    failed_cases=("test_command_blocked",),
                    score=None,
                    raw_output=(
                        f"[{result.status}:{result.error_code or 'unknown'}] "
                        f"{result.error_message or ''} {stdout}"
                    )[:4000],
                )
            passed = exit_code == 0
            return ExecutionReport(
                command_digest=cmd_digest,
                total=1,
                passed=1 if passed else 0,
                failed_cases=() if passed else ("test_command_failed",),
                score=1.0 if passed else 0.0,
                raw_output=stdout[:4000],
            )
        except Exception as exc:  # noqa: BLE001 — never raise
            return ExecutionReport(
                command_digest=cmd_digest,
                total=1,
                passed=0,
                failed_cases=("test_execution_error",),
                score=None,
                raw_output=str(exc)[:2000],
            )
        finally:
            if run_dir is not None:
                shutil.rmtree(run_dir, ignore_errors=True)

    # Strong refs for fire-and-forget progress emits (F6): without these the
    # event loop may GC a pending task before it runs.
    _pending_emits: set[object] = set()

    def _emit_progress(event: Mapping[str, object]) -> None:
        """Emit a progress event to the parent context (fire-and-forget)."""
        import asyncio  # noqa: PLC0415
        import inspect  # noqa: PLC0415

        emitter = context.emit_agent_event
        if not callable(emitter):
            return
        payload = {**event, "taskId": run_id, "childReceiptRef": run_receipt_ref}
        try:
            result = emitter(payload)
            if inspect.isawaitable(result):
                # We're called from a sync context inside the orchestrator.
                # Best-effort: schedule on the running loop, retaining a
                # strong reference so the task is not GC'd mid-flight (F6).
                try:
                    loop = asyncio.get_running_loop()
                    task = loop.create_task(result)
                    _pending_emits.add(task)
                    task.add_done_callback(_pending_emits.discard)
                except RuntimeError:
                    pass
        except Exception:  # noqa: BLE001
            pass

    def _append_verdict(verdict: DeepSolveVerdictData) -> None:
        """Record the run verdict.

        Verdict seam gap: LocalToolCollector is not reachable from ToolContext.
        The verdict is stored in the verdicts list and promoted onto the
        ToolResult.output payload as ``deepSolveVerdict``. See module docstring
        for the follow-up tracking note.
        """
        verdicts.append(verdict)

    class _BoundDeps:
        """Concrete implementation of DeepSolveDeps for this invocation."""

        async def run_stage(
            self,
            *,
            stage: str,
            role: str,
            toolset_request: str,
            objective: str,
            agents_spent_so_far: int,
        ) -> StageResult:
            return await _run_stage(
                stage=stage,
                role=role,
                toolset_request=toolset_request,
                objective=objective,
                agents_spent_so_far=agents_spent_so_far,
            )

        async def execute_tests(
            self, *, artifact: str, test_command: str
        ) -> ExecutionReport:
            return await _execute_tests(artifact=artifact, test_command=test_command)

        def emit_progress(self, event: Mapping[str, object]) -> None:
            _emit_progress(event)

        def append_verdict(self, verdict: DeepSolveVerdictData) -> None:
            _append_verdict(verdict)

    # ------------------------------------------------------------------
    # Run the orchestrator
    # ------------------------------------------------------------------
    outcome = await run_deep_solve(config, _BoundDeps())

    # ------------------------------------------------------------------
    # Emit run-completed event
    # ------------------------------------------------------------------
    from magi_agent.runtime.public_events import child_completed_event  # noqa: PLC0415

    accept_basis = outcome.acceptance_basis
    summary = (
        f"DeepSolve: {accept_basis}. "
        f"Cycles: {outcome.cycles}, refolds: {outcome.refolds}."
    )
    await _emit_agent_event(
        context,
        child_completed_event(
            task_id=run_id,
            child_receipt_ref=run_receipt_ref,
            summary=summary,
        ),
    )

    # ------------------------------------------------------------------
    # Build output (verdict seam: promote to output payload — see gap note)
    # ------------------------------------------------------------------
    verdict_dict: dict[str, object] | None = None
    if verdicts:
        v = verdicts[0]
        verdict_dict = {
            "problem_digest": v.problem_digest,
            "problem_class": v.problem_class,
            "cycles": v.cycles,
            "refolds": v.refolds,
            "acceptance_basis": v.acceptance_basis,
            "final_findings_open": list(v.final_findings_open),
            "per_stage_child_refs": list(v.per_stage_child_refs),
        }

    output: dict[str, object] = {
        "status": "ok" if accept_basis != "rejected" else "rejected",
        "acceptanceBasis": accept_basis,
        "cycles": outcome.cycles,
        "refolds": outcome.refolds,
        "summary": summary,
    }
    if verdict_dict is not None:
        output["deepSolveVerdict"] = verdict_dict
    if accept_basis == "rejected" and outcome.reject_reason:
        output["rejectReason"] = outcome.reject_reason
    if outcome.final_findings_open:
        output["finalFindingsOpen"] = list(outcome.final_findings_open)

    tool_status = "ok" if accept_basis != "rejected" else "blocked"
    error_code = "deep_solve_rejected" if accept_basis == "rejected" else None
    return _deep_solve_result(tool_status, output, error_code=error_code)
