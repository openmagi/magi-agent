# PR4 — Lightweight goal-nudge continuation

**Lesson source:** goose has a cheap "keep going until the goal is met" primitive
(`crates/goose/src/agents/agent.rs` ~2254-2289): when the model stops without tool calls but
a `goal`/`grind` objective is unmet, it injects a hidden synthetic *user* message and
continues the loop — no heavy orchestrator. `goal` self-verifies once; `grind` persists to
the turn budget. magi has heavy `meta_orchestration` for autonomy but **no lightweight
nudge-until-done primitive**, and its recipe `completion-criteria` is metadata-only
(`enabled=False`).

**Goal:** add a lightweight, opt-in `GoalNudge` continuation at the engine outer-driver
level. When an ADK turn ends with the goal unmet, re-invoke `run_async` with a synthetic
nudge as the new message, bounded by a hard `max_nudges` cap. Tie "done" to magi's evidence
layer when evidence is declared; otherwise fall back to goose's self-check turn. Default OFF
(`None`).

## Current state (verified, on `origin/main` @ debd41d)

- magi does NOT own the inner loop — ADK's `Runner.run_async` does. The only re-invocation
  point is the outer driver `magi_agent/cli/engine.py` `MagiEngineDriver._drive` (~355). It
  already re-invokes a fresh `run_async` for **recovery** in a `while True` (~447-516), only
  when `yielded_events == 0` (so streamed output is never duplicated).
- The run input is built once (~403-415) from `prompt` →
  `types.Content(role="user", parts=[types.Part(text=prompt)])` and re-fed into the loop.
- `magi_agent/evidence/final_output_gate.py` `FinalOutputGate.evaluate(FinalOutputGateRequest)
  -> FinalOutputGateDecision{status, reason_codes, evidence_refs}` — magi's real
  "done is evidence-backed" primitive (checks `required_evidence` against an evidence ledger).
  `FinalOutputGateConfig.enabled` default False.
- `magi_agent/evidence/ledger.py` `EvidenceLedger` (~247) — source of evidence records.
- `magi_agent/runtime/turn_policy.py` `maybe_apply_max_steps_brake` (~63) is a *stop* brake
  (inverse of a nudge) and itself unwired; share the iteration budget so the two never fight.
- `magi_agent/meta_orchestration/` — heavy plan/spawn/inspect machinery; the new primitive
  must stay distinct and NOT touch it.

## Design

### A. `magi_agent/runtime/goal_nudge.py` (new)
```python
@dataclass(frozen=True)
class GoalNudge:
    goal: str
    mode: Literal["goal", "grind"] = "goal"   # goal=verify-once-per-stop, grind=persist
    max_nudges: int = 3                        # hard anti-infinite-loop cap
    required_evidence: tuple[str, ...] = ()    # ties to FinalOutputGate when non-empty
    domain: str = "general"

def build_nudge_message(nudge: GoalNudge) -> str:
    if nudge.mode == "grind":
        return ("Keep working. The objective is not yet complete:\n\n"
                f"**Goal:** {nudge.goal}\n\nContinue until it is fully done.")
    return ("Before finishing, check whether the following goal has been fully met:\n\n"
            f"**Goal:** {nudge.goal}\n\nIf not, continue working toward it.")

def goal_is_met(nudge: GoalNudge, *, evidence_records) -> bool:
    if nudge.required_evidence:
        decision = FinalOutputGate(FinalOutputGateConfig(enabled=True)).evaluate(
            FinalOutputGateRequest(domain=nudge.domain, outputText="",
                requiredEvidence=nudge.required_evidence,
                evidenceRecords=tuple(evidence_records), modelTier="standard"))
        return decision.status not in ("blocked", "fail")
    return False   # no evidence declared -> rely on the synthetic self-check turn
```
Confirm exact `FinalOutputGateRequest` field names/casing against the installed module
before finalizing.

### B. Engine integration — `engine.py:_drive`
1. Add param `goal_nudge: GoalNudge | None = None` (thread like `harness_state`).
2. Before the loop, init `nudges_used = 0` and `goal_check_pending = False` (mirrors goose's
   per-stop latch).
3. Reset the latch whenever a tool fires, in the event-projection loop:
   `if safe.get("type") == "tool": goal_check_pending = False`.
4. At the clean-break path (turn ended without error), replace the bare `break` with:
   ```python
   if attempt_error is None:
       if goal_nudge is not None and nudges_used < goal_nudge.max_nudges:
           if not goal_is_met(goal_nudge, evidence_records=self._collect_evidence(turn_id)):
               if goal_nudge.mode == "goal" and goal_check_pending:
                   break                       # goal fires once per stop (goose parity)
               goal_check_pending = True
               nudges_used += 1
               runner_input = <fresh runner input with
                   newMessage=Content(role="user",
                       parts=[Part(text=build_nudge_message(goal_nudge))]),
                   same userId/sessionId/turnId/invocationId/harnessState>
               yield RuntimeEvent(type="status",
                   payload={"type": "goal_nudge", "mode": goal_nudge.mode,
                            "nudge": nudges_used, "max": goal_nudge.max_nudges},
                   turn_id=turn_id)
               continue                        # re-invoke run_async
       break
   ```
   `_collect_evidence(turn_id)` reads the existing `EvidenceLedger`. Reuse the *same*
   re-invocation machinery the recovery path already uses (build a fresh `runner_input`,
   `continue` the outer `while`). Do not duplicate that machinery — factor a small helper if
   needed.

### C. Bounds / safety
- `nudges_used < max_nudges` is the hard cap; combine with the existing recovery/turn budget
  so `GoalNudge` cannot exceed `max_iterations`. Share the budget with
  `maybe_apply_max_steps_brake` (PR2) so a nudge never re-opens a turn the brake closed.
- `mode="goal"`: latch prevents more than one nudge per consecutive stop. `mode="grind"`:
  re-nudges each clean stop until `max_nudges`.
- Default `goal_nudge=None` → `_drive` behavior byte-identical to today.

### D. Lightweight vs heavy
Document: use `GoalNudge` for single-agent "keep going until this one objective is met" (no
plan/spawn/inspect). Use `meta_orchestration/` when work needs decomposition into spawned
sub-agents with per-child acceptance. `GoalNudge` lives entirely in `runtime/` + `engine.py`.

## Tests (TDD — write first)
- `tests/runtime/test_goal_nudge.py` (new): `build_nudge_message` text for both modes;
  `goal_is_met` true when required evidence present in ledger, false when missing; false when
  no evidence declared.
- `tests/cli/test_engine_goal_nudge.py` (new, with fake adapter/runner):
  - `goal_nudge=None` → no extra runs (parity with today).
  - `mode="goal"`, evidence missing → exactly ONE nudge re-invocation per stop (latch); then
    breaks even if still unmet.
  - `mode="grind"`, unmet → re-nudges up to `max_nudges`, then stops (hard cap).
  - goal met (evidence present) → no nudge.
  - tool firing resets the latch (re-arm).
  - nudge message threaded as the next `run_async` `newMessage`.

## Acceptance criteria
1. New `runtime/goal_nudge.py` with `GoalNudge`, `build_nudge_message`, `goal_is_met`.
2. `_drive` re-invokes with a nudge only when goal unmet and under `max_nudges`; reuses the
   existing recovery re-invocation path; never duplicates streamed output.
3. `goal` latches once-per-stop; `grind` persists to cap; tool firing re-arms.
4. Evidence-declared goals checked via `FinalOutputGate`; default `None` → no behavior change.
5. `uv run --extra dev pytest -q` green for touched modules.

## Out of scope
- Touching meta_orchestration. Exposing GoalNudge through recipes/CLI flags (engine-level API
  only here). Changing turn/iteration budget defaults.
