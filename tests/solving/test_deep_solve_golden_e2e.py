"""Golden end-to-end tests for the DeepSolve pipeline (U5).

Drives the REAL ``magi_agent.plugins.native.deep_solve.deep_solve`` handler
end-to-end with scripted child transcripts via a patched
``LocalChildRunnerBoundary.run``.

Three scenarios:
(a) Executable path -- verify finds bugs, refine fixes them, tests then pass
    -> acceptanceBasis="tests_passed", deepSolveVerdict present, child lifecycle
    progress events emitted.
(b) Plateau -> refold -> post-refold verify finds nothing -> tests pass -> accept
    -> refolds==1 in verdict.
(c) Refold -> plateau persists -> rejected with honest rejectReason and
    finalFindingsOpen set.

All gates are patched ON:
- is_deep_solve_enabled -> True
- _deep_solve_pack_enabled -> True
- is_live_child_runner_enabled -> True

No network, no real child runner.  LocalChildRunnerBoundary.run is patched via
patch.object to return scripted ChildRunnerResult objects, and
standalone_core_tool_handler is patched to return scripted Bash results.

Key lesson: ChildRunnerResult.status must be "ok" (not "completed"); "completed"
is the status on ChildRunnerEnvelopeRef, not on the containing result.
"""
from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any
from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# Lazy import helper (ensures heavy imports stay out of module load time)
# ---------------------------------------------------------------------------

def _import_boundary() -> Any:
    from magi_agent.runtime.child_runner_boundary import LocalChildRunnerBoundary
    return LocalChildRunnerBoundary


# ---------------------------------------------------------------------------
# Context factory
# ---------------------------------------------------------------------------

def _make_context(tmp_path: Any | None = None) -> Any:
    from magi_agent.tools.context import ToolContext

    emitted_events: list[dict[str, Any]] = []

    def _emit(event: Mapping[str, object]) -> None:
        emitted_events.append(dict(event))

    workspace = str(tmp_path) if tmp_path is not None else "/tmp/ds-e2e-test"
    ctx = ToolContext.model_validate({
        "botId": "test-bot-e2e",
        "sessionId": "sess-e2e-001",
        "turnId": "turn-e2e-001",
        "workspaceRoot": workspace,
        "emitAgentEvent": _emit,
        "spawnDepth": 0,
    })
    ctx._emitted = emitted_events  # type: ignore[attr-defined]
    return ctx


def _make_args(**overrides: Any) -> dict[str, object]:
    defaults: dict[str, object] = {
        "problem": (
            "Given a list of integers, find the maximum subarray sum "
            "(Kadane's algorithm)."
        ),
        "test_command": "pytest -q tests/test_solution.py",
        "language": "python3",
    }
    defaults.update(overrides)
    return defaults


def _findings_block(findings: list[dict[str, Any]]) -> str:
    """Render a findings block parseable by _parse_findings."""
    return "```findings\n" + json.dumps(findings) + "\n```"


# ---------------------------------------------------------------------------
# Scripted boundary result builder
# ---------------------------------------------------------------------------

def _make_boundary_result(
    *,
    task_id: str,
    summary: str,
    child_exec_id: str = "child-exec-001",
    parent_exec_id: str = "deep-solve-parent-e2e",
) -> Any:
    """Build a ChildRunnerResult for a successful scripted stage call.

    Note: ChildRunnerResult.status must be "ok" (the result-level status),
    whereas ChildRunnerEnvelopeRef.status uses "completed" (envelope-level).
    """
    from magi_agent.runtime.child_runner_boundary import (
        ChildRunnerEnvelopeRef,
        ChildRunnerResult,
    )

    envelope = ChildRunnerEnvelopeRef.model_validate({
        "childRef": f"child:{child_exec_id}",
        "taskId": task_id,
        "childExecutionId": child_exec_id,
        "parentExecutionId": parent_exec_id,
        "status": "completed",
        "summary": summary[:510],  # match the sanitizer cap
    })

    return ChildRunnerResult.model_validate({
        "status": "ok",        # result-level: ok | blocked | disabled | error
        "taskId": task_id,
        "promptRef": f"prompt:{task_id}",
        "envelope": envelope,
    })


# ---------------------------------------------------------------------------
# Scripted boundary dispatcher
#
# Patches LocalChildRunnerBoundary.run.  Each script entry is either:
#   str                         -- full_text, no findings block injected
#   (str, list[dict])          -- full_text + findings block appended
#
# The sink on the runner is called so StageResult.full_text is populated;
# falling back to sanitized_summary (same text) is also fine for findings
# parsing since both are capped at ~510 chars which accommodates our scripts.
# ---------------------------------------------------------------------------

class _ScriptedBoundary:
    def __init__(
        self,
        scripts: list[str | tuple[str, list[dict[str, Any]]]],
    ) -> None:
        self._scripts = scripts
        self._idx = 0
        self.call_log: list[dict[str, Any]] = []

    async def run(self, request: Any) -> Any:
        """Replacement for LocalChildRunnerBoundary.run.

        When patch.object sets this as the class attribute, Python's descriptor
        protocol for non-plain-functions means the boundary instance is NOT
        automatically prepended -- the call is scripted.run(request) directly.
        """
        idx = self._idx
        self._idx += 1

        script = self._scripts[idx] if idx < len(self._scripts) else ("", [])
        if isinstance(script, tuple):
            text, extra_findings = script
        else:
            text = script
            extra_findings = []

        full_text = text
        if extra_findings:
            full_text = text + "\n\n" + _findings_block(extra_findings)

        metadata = getattr(request, "metadata", {}) or {}
        self.call_log.append({
            "idx": idx,
            "stage": metadata.get("deepSolveStage", "?"),
            "task_id": str(getattr(request, "taskId", f"task-{idx}")),
        })

        # Note: we cannot easily reach the boundary instance's runner here since
        # boundary_self is not passed. The full_text_sink would normally be called
        # by the real runner; since it isn't, StageResult.full_text falls back to
        # sanitized_summary (the envelope.summary field, which IS our full_text
        # truncated to 510 chars). Our scripts are short enough that findings blocks
        # survive the truncation.

        task_id = str(getattr(request, "taskId", f"task-{idx}"))
        return _make_boundary_result(
            task_id=task_id,
            summary=full_text,
            child_exec_id=f"child-{idx:03d}",
            parent_exec_id="deep-solve-parent-e2e",
        )


def _make_bash_handler_factory(exit_codes: list[int]) -> Any:
    """Return a standalone_core_tool_handler factory with scripted exit codes."""
    call_idx = [0]

    def _factory(tool_name: str, **kwargs: Any) -> Any:
        assert tool_name == "Bash"

        async def _bash(arguments: dict[str, object], context: Any) -> Any:
            from magi_agent.tools.result import ToolResult

            idx = call_idx[0]
            code = exit_codes[idx] if idx < len(exit_codes) else exit_codes[-1]
            call_idx[0] += 1
            stdout = "1 passed\n" if code == 0 else "1 failed - AssertionError\n"
            return ToolResult(
                status="ok",
                output={"exitCode": code, "stdout": stdout},
            )

        return _bash

    return _factory


# ---------------------------------------------------------------------------
# Common gate-ON patch stack
# ---------------------------------------------------------------------------

def _gate_patches() -> list[Any]:
    return [
        patch("magi_agent.config.env.is_deep_solve_enabled", return_value=True),
        patch(
            "magi_agent.plugins.native.deep_solve._deep_solve_pack_enabled",
            return_value=True,
        ),
        patch(
            "magi_agent.runtime.child_runner_live.is_live_child_runner_enabled",
            return_value=True,
        ),
    ]


# ---------------------------------------------------------------------------
# Scenario (a): executable path -- fail -> refine -> tests pass
# ---------------------------------------------------------------------------

class TestScenarioA_ExecutableAccept:
    """Verify finds critical bug, refine fixes it, tests pass on cycle 1."""

    @pytest.mark.asyncio
    async def test_executable_accept_verdict_and_events(self, tmp_path: Any) -> None:
        from magi_agent.plugins.native.deep_solve import deep_solve
        from magi_agent.runtime.child_runner_boundary import LocalChildRunnerBoundary

        ctx = _make_context(tmp_path)
        args = _make_args()

        bug_finding = [
            {
                "stage": "verify",
                "category": "critical_logic",
                "severity": "critical",
                "description": "Handling of all-negative subarrays is wrong.",
            }
        ]

        # Stage scripts in call order:
        # 0: S1 solve      -- initial attempt
        # 1: S2 improve    -- improved attempt
        # 2: S3 verify     -- finds critical bug
        # 3: S4 adjudicate -- confirms bug
        # 4: S5 refine     -- fixed solution (no findings)
        # (S5.5 execute_tests via Bash: fail on cycle 1, pass on cycle 1 fixed)
        scripts: list[str | tuple[str, list[dict[str, Any]]]] = [
            "def max_subarray(nums):\n    return max(nums)",
            "def max_subarray(nums):\n    return max(nums)",
            ("Verification report.", bug_finding),
            ("Adjudicator confirms bug.", bug_finding),
            "def max_subarray(nums):\n    cur=best=nums[0]\n    for n in nums[1:]:\n        cur=max(n,cur+n)\n        best=max(best,cur)\n    return best",
        ]

        scripted = _ScriptedBoundary(scripts)
        bash_factory = _make_bash_handler_factory([1, 0])  # fail then pass

        gate_ctx = _gate_patches()
        with (
            gate_ctx[0], gate_ctx[1], gate_ctx[2],
            patch.object(LocalChildRunnerBoundary, "run", new=scripted.run),
            patch(
                "magi_agent.tools.core_toolhost.standalone_core_tool_handler",
                side_effect=bash_factory,
            ),
        ):
            result = await deep_solve(args, ctx)

        # Top-level result
        assert result.status == "ok", f"Expected ok, got {result.status}: {result}"
        output = result.output or {}
        assert output.get("acceptanceBasis") == "tests_passed", output

        # Verdict present and correct
        verdict = output.get("deepSolveVerdict")
        assert verdict is not None, "deepSolveVerdict missing from output"
        assert verdict["acceptance_basis"] == "tests_passed"
        assert verdict["cycles"] >= 1
        assert verdict["refolds"] == 0

        # Child lifecycle progress events were emitted
        emitted = getattr(ctx, "_emitted", [])
        event_types = {e.get("type") for e in emitted}
        assert "child_started" in event_types, (
            f"Missing child_started in {event_types}. Events: {emitted[:5]}"
        )

        # At least one child_progress or stage event
        stage_events = [
            e for e in emitted
            if e.get("type") == "child_progress" or "stage" in e
        ]
        assert len(stage_events) > 0, f"No stage progress events. Events: {emitted}"

        # Boundary was called for the expected stages
        stages_called = [c["stage"] for c in scripted.call_log]
        assert "solve" in stages_called, f"solve stage missing: {stages_called}"
        assert "verify" in stages_called, f"verify stage missing: {stages_called}"
        assert "refine" in stages_called, f"refine stage missing: {stages_called}"


# ---------------------------------------------------------------------------
# Scenario (b): plateau -> refold -> post-refold tests pass
# ---------------------------------------------------------------------------

class TestScenarioB_PlateauRefold:
    """No-progress detected on cycle 1, refold triggered, cycle 2 tests pass.

    refolds==1 must appear in the verdict.
    """

    @pytest.mark.asyncio
    async def test_refold_then_accept(self, tmp_path: Any) -> None:
        from magi_agent.plugins.native.deep_solve import deep_solve
        from magi_agent.runtime.child_runner_boundary import LocalChildRunnerBoundary

        ctx = _make_context(tmp_path)
        args = _make_args()

        # No-progress ladder (fingerprint-based for executable class):
        # - Cycle 1: bug found -> tests fail. Fingerprints are NEW -> no plateau.
        # - Cycle 2: SAME bug (same fingerprint) -> tests fail.
        #   all_seen=True AND blocking_findings non-empty -> is_no_progress=True.
        #   plateau_streak=1, refolds==0 -> trigger refold (S6).
        # - Cycle 3 (post-refold): verify clean -> tests pass -> accept.
        # Expected: refolds==1 in verdict.
        bug_finding = [
            {
                "stage": "verify",
                "category": "critical_logic",
                "severity": "critical",
                "description": "Off-by-one in boundary condition.",
            }
        ]

        # Script index order:
        # 0: S1 solve
        # 1: S2 improve
        # 2: S3 verify cycle 1   -- critical bug (adds fingerprint)
        # 3: S4 adjudicate c1    -- confirms bug
        # 4: S5 refine cycle 1   -- still failing
        # (Bash c1: fail -> cycle ends, fingerprints={fp1})
        # (no plateau c1: prev_failed=None so executable fallback not triggered;
        #  fingerprint IS new so fingerprint check also doesn't trigger yet)
        # 5: S3 verify cycle 2   -- SAME bug (fingerprint already seen)
        # 6: S4 adjudicate c2    -- confirms
        # 7: S5 refine cycle 2   -- still failing
        # (Bash c2: fail -> all_seen=True -> is_no_progress=True -> plateau=1 -> refold)
        # 8: S6 refold
        # 9: S3 verify cycle 3   -- no findings
        # 10: S4 adjudicate c3   -- no findings
        # 11: S5 refine cycle 3  -- clean
        # (Bash c3: pass -> accept)
        scripts: list[str | tuple[str, list[dict[str, Any]]]] = [
            "def solve(a): return sum(a)",         # 0: S1
            "def solve(a): return sum(a)",         # 1: S2
            ("Verify c1.", bug_finding),            # 2: S3 c1 (NEW fingerprint)
            ("Adj c1.", bug_finding),               # 3: S4 c1
            "def solve(a): return sum(a)+1",        # 4: S5 c1
            # Bash c1: fail -- no plateau (new fingerprint, prev_failed=None)
            ("Verify c2.", bug_finding),            # 5: S3 c2 (SAME fingerprint -> all_seen)
            ("Adj c2.", bug_finding),               # 6: S4 c2
            "def solve(a): return sum(a)+1",        # 7: S5 c2
            # Bash c2: fail -> all_seen + blocking -> is_no_progress -> plateau=1 -> REFOLD
            "def solve(a): return max(a)",          # 8: S6 refold
            ("Verify c3.", []),                     # 9: S3 c3 (clean)
            ("Adj c3.", []),                        # 10: S4 c3 (clean)
            "def solve(a): return max(a)",          # 11: S5 c3
            # Bash c3: pass -> accept
        ]

        scripted = _ScriptedBoundary(scripts)
        bash_factory = _make_bash_handler_factory([1, 1, 0])  # c1 fail, c2 fail, c3 pass

        gate_ctx = _gate_patches()
        with (
            gate_ctx[0], gate_ctx[1], gate_ctx[2],
            patch.object(LocalChildRunnerBoundary, "run", new=scripted.run),
            patch(
                "magi_agent.tools.core_toolhost.standalone_core_tool_handler",
                side_effect=bash_factory,
            ),
        ):
            result = await deep_solve(args, ctx)

        assert result.status == "ok", f"Expected ok, got {result.status}: {result}"
        output = result.output or {}
        assert output.get("acceptanceBasis") == "tests_passed", output

        verdict = output.get("deepSolveVerdict")
        assert verdict is not None, "deepSolveVerdict missing"
        assert verdict["refolds"] == 1, (
            f"Expected refolds==1 (one refold happened), got {verdict['refolds']}"
        )

        stages_called = [c["stage"] for c in scripted.call_log]
        assert "refold" in stages_called, (
            f"refold stage not called: {stages_called}"
        )


# ---------------------------------------------------------------------------
# Scenario (c): refold -> plateau persists -> rejected
# ---------------------------------------------------------------------------

class TestScenarioC_RejectAfterRefold:
    """Refold triggers, but post-refold verify still finds the same bug (same
    fingerprint) and tests still fail -> plateau_streak reaches 2 -> rejected.

    Checks: rejectReason is honest (mentions plateau/refold), finalFindingsOpen
    is non-empty, verdict.refolds==1.
    """

    @pytest.mark.asyncio
    async def test_plateau_post_refold_reject(self, tmp_path: Any) -> None:
        from magi_agent.plugins.native.deep_solve import deep_solve
        from magi_agent.runtime.child_runner_boundary import LocalChildRunnerBoundary

        ctx = _make_context(tmp_path)
        args = _make_args()

        # Fingerprint-based no-progress detection across 3 cycles:
        # Cycle 1: bug found (NEW fingerprint), tests fail. No plateau (new fp).
        # Cycle 2: SAME bug (all_seen=True), tests fail. -> plateau=1 -> refold.
        # Cycle 3 (post-refold): SAME bug AGAIN (still all_seen), tests fail.
        #   plateau_streak=2 >= 2 -> REJECT with honest rejectReason.
        bug_finding = [
            {
                "stage": "verify",
                "category": "critical_logic",
                "severity": "critical",
                "description": "Persistent logic error that cannot be fixed.",
            }
        ]

        # Script order:
        # 0: S1 solve
        # 1: S2 improve
        # 2: S3 verify c1   -- bug (NEW fingerprint)
        # 3: S4 adj c1      -- confirms
        # 4: S5 refine c1   -- still wrong
        # (Bash c1: fail -> no plateau yet, fp is new)
        # 5: S3 verify c2   -- SAME bug (all_seen=True -> is_no_progress)
        # 6: S4 adj c2      -- confirms
        # 7: S5 refine c2   -- still wrong
        # (Bash c2: fail -> plateau=1 -> refold since refolds==0)
        # 8: S6 refold
        # 9: S3 verify c3   -- SAME bug (still all_seen -> is_no_progress)
        # 10: S4 adj c3     -- confirms
        # 11: S5 refine c3  -- still wrong
        # (Bash c3: fail -> plateau=2 -> REJECT)
        scripts: list[str | tuple[str, list[dict[str, Any]]]] = [
            "def solve(a): pass",                   # 0: S1
            "def solve(a): pass",                   # 1: S2
            ("Verify c1.", bug_finding),             # 2: S3 c1 (NEW fp)
            ("Adj c1.", bug_finding),                # 3: S4 c1
            "def solve(a): pass",                   # 4: S5 c1
            # Bash c1: fail. No plateau (new fp).
            ("Verify c2.", bug_finding),             # 5: S3 c2 (SAME fp -> all_seen)
            ("Adj c2.", bug_finding),                # 6: S4 c2
            "def solve(a): pass",                   # 7: S5 c2
            # Bash c2: fail. plateau=1 -> refold.
            "def solve(a): pass",                   # 8: S6 refold
            ("Verify c3.", bug_finding),             # 9: S3 c3 (SAME fp)
            ("Adj c3.", bug_finding),                # 10: S4 c3
            "def solve(a): pass",                   # 11: S5 c3
            # Bash c3: fail. plateau=2 -> REJECT.
        ]

        scripted = _ScriptedBoundary(scripts)
        bash_factory = _make_bash_handler_factory([1, 1, 1, 1])  # always fail

        gate_ctx = _gate_patches()
        with (
            gate_ctx[0], gate_ctx[1], gate_ctx[2],
            patch.object(LocalChildRunnerBoundary, "run", new=scripted.run),
            patch(
                "magi_agent.tools.core_toolhost.standalone_core_tool_handler",
                side_effect=bash_factory,
            ),
        ):
            result = await deep_solve(args, ctx)

        assert result.status == "blocked", (
            f"Expected blocked (rejected), got {result.status}: {result}"
        )
        assert result.error_code == "deep_solve_rejected", (
            f"Expected deep_solve_rejected, got {result.error_code}"
        )
        output = result.output or {}
        assert output.get("acceptanceBasis") == "rejected", output

        # Honest rejectReason mentioning plateau or refold
        reject_reason = output.get("rejectReason", "")
        assert reject_reason, "rejectReason should be non-empty for rejected outcome"
        assert any(
            kw in reject_reason.lower()
            for kw in ("plateau", "refold", "no-progress", "no_progress")
        ), f"rejectReason should mention plateau/refold: {reject_reason!r}"

        # finalFindingsOpen carries the open finding descriptions
        final_open = output.get("finalFindingsOpen", [])
        assert len(final_open) > 0, "finalFindingsOpen should be non-empty on rejection"
        assert any("Persistent" in str(f) for f in final_open), (
            f"Expected 'Persistent' in finalFindingsOpen: {final_open}"
        )

        # Verdict present with refolds==1
        verdict = output.get("deepSolveVerdict")
        assert verdict is not None, "deepSolveVerdict missing on rejected path"
        assert verdict["refolds"] == 1, (
            f"Expected refolds==1 after one refold attempt, got {verdict['refolds']}"
        )
