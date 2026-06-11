# Phase 0 — Control-Plane Golden Oracle (load-bearing)

> **For agentic workers:** REQUIRED SUB-SKILL: `superpowers:subagent-driven-development` or
> `superpowers:executing-plans`. Read `00-BLUEPRINT.md` first. This phase has **no dependencies**
> and **gates all later verification** — build it first.

**Goal:** A headless, no-API-key regression oracle that captures the *control-plane decision
sequence* for the 4 hard scenarios as frozen golden JSON, so any later change to the 6 LoopControls
can be diffed against pre-migration behavior. Without it, control-plane verification is "read the
code and hope"; with it, it is `pytest` + diff.

**Architecture:** A `ControlPlaneRecorder` observes the `ControlPlanePlugin` fan-out during a
fake-model mock-runner turn and serializes a normalized decision trace (tool allow/deny/rewrite,
`llm_request` mutations, reinjected message digests, compaction-fired flags, after-tool overrides).
Four scenario drivers trigger the 4 hard seams in isolation. Golden files are captured once on the
pristine `feat/neutral-runtime` base, then asserted unchanged.

**Tech stack:** pytest, fake-model (`LOCAL_DEV_MODEL_SENTINEL="local-dev"`), the existing mock
runner harness in `magi_agent/cli/tests/test_engine.py` (`MockRunner`/`GatedRunner`) and the
control-plane tests under `tests/adk_bridge/`.

---

## Task 0.1: Establish a clean baseline (deps + existing control-plane tests)

**Files:** none created — environment + sanity.

- [ ] **Step 1: Sync deps in the worktree**

Run:
```bash
cd /Users/kevin/Desktop/claude_code/magi-agent/.worktrees/neutral-runtime
uv sync --extra dev
```
Expected: resolves, no error. (If a `cli` extra is referenced by tests, also `--extra cli`.)

- [ ] **Step 2: Run the existing control-plane behavior tests as baseline**

Run (isolate config to avoid `~/.magi/config.toml` contamination — known gotcha):
```bash
MAGI_CONFIG="$(mktemp -d)/config.toml" uv run pytest \
  tests/adk_bridge/test_control_plane.py \
  tests/adk_bridge/test_max_steps_brake_control.py \
  tests/adk_bridge/test_context_compaction_plugin.py \
  tests/adk_bridge/test_extended_plugin_on_tool_error.py \
  tests/test_resilience_plugin_wiring.py \
  tests/test_ga_constraint_control.py \
  tests/test_runner_plugin_composition.py -q
```
Expected: all pass (≈96 tests). If any fail on the pristine base, **STOP and report** — do not build
the oracle on a red baseline.

- [ ] **Step 3: Read the harness you will reuse** (no edit)

Read these to learn the exact APIs (do NOT fabricate them):
- `magi_agent/cli/tests/test_engine.py` — `MockRunner`, `GatedRunner`, how a fake-model turn is
  driven and how ADK Events are injected.
- `magi_agent/adk_bridge/control_plane.py` — `ControlPlanePlugin`, `ControlPlane`, the
  `before_tool_callback` / `after_tool_callback` / `before_model_callback` / `after_agent_callback`
  fan-out, and `build_default_plugin()`.
- `tests/adk_bridge/test_context_compaction_plugin.py`, `tests/test_resilience_plugin_wiring.py`,
  `tests/adk_bridge/test_extended_plugin_on_tool_error.py`, `tests/test_ga_constraint_control.py`
  — these already trigger the 4 seams; reuse their trigger setups.

Record (in the commit message of 0.2) the exact symbol names/signatures you will call.

---

## Task 0.2: The `ControlPlaneRecorder` (decision-trace capture)

**Files:**
- Create: `tests/fixtures/neutral_runtime_golden/recorder.py`
- Test: `tests/fixtures/neutral_runtime_golden/test_recorder_self.py`

- [ ] **Step 1: Write the failing self-test for the recorder**

```python
# tests/fixtures/neutral_runtime_golden/test_recorder_self.py
from tests.fixtures.neutral_runtime_golden.recorder import ControlPlaneRecorder, normalize_trace


def test_recorder_captures_a_deny_then_normalizes_stable():
    rec = ControlPlaneRecorder()
    rec.record_before_tool(tool_name="FileEdit", tool_args={"path": "a.py"},
                           decision={"action": "deny", "reason": "loop guard"})
    rec.record_reinject(role="user", text_digest="sha256:deadbeef", source="max_steps")
    trace = normalize_trace(rec.events)
    assert trace == [
        {"kind": "before_tool", "tool": "FileEdit", "args_digest": _ANY_SHA,
         "decision": "deny", "reason": "loop guard"},
        {"kind": "reinject", "role": "user", "text_digest": "sha256:deadbeef", "source": "max_steps"},
    ]
```
Replace `_ANY_SHA` with a matcher: assert the `args_digest` is a `sha256:` string (args are digested,
not stored raw, so the golden is stable and secret-free).

- [ ] **Step 2: Run it, see it fail**

Run: `uv run pytest tests/fixtures/neutral_runtime_golden/test_recorder_self.py -q`
Expected: FAIL (module not found).

- [ ] **Step 3: Implement the recorder**

```python
# tests/fixtures/neutral_runtime_golden/recorder.py
from __future__ import annotations
import hashlib, json
from dataclasses import dataclass, field
from typing import Any


def _digest(obj: Any) -> str:
    blob = json.dumps(obj, sort_keys=True, default=str).encode()
    return "sha256:" + hashlib.sha256(blob).hexdigest()[:16]


@dataclass
class ControlPlaneRecorder:
    """Observes control-plane decisions during a fake-model turn.

    Hook each ControlPlanePlugin callback to the matching record_* method (Task 0.3 wires this).
    Stores order-preserving, content-addressed events — stable + secret-free for golden diffing.
    """
    events: list[dict[str, Any]] = field(default_factory=list)

    def record_before_tool(self, *, tool_name: str, tool_args: dict, decision: dict) -> None:
        self.events.append({"kind": "before_tool", "tool": tool_name,
                            "args_digest": _digest(tool_args),
                            "decision": decision.get("action", "allow"),
                            "reason": decision.get("reason")})

    def record_after_tool(self, *, tool_name: str, override: dict | None) -> None:
        self.events.append({"kind": "after_tool", "tool": tool_name,
                            "override": None if override is None else _digest(override)})

    def record_before_model(self, *, mutated: bool, tools_cleared: bool) -> None:
        self.events.append({"kind": "before_model", "mutated": bool(mutated),
                            "tools_cleared": bool(tools_cleared)})

    def record_reinject(self, *, role: str, text_digest: str, source: str) -> None:
        self.events.append({"kind": "reinject", "role": role,
                            "text_digest": text_digest, "source": source})

    def record_compaction(self, *, fired: bool, kept_tail: int | None) -> None:
        self.events.append({"kind": "compaction", "fired": bool(fired), "kept_tail": kept_tail})


def normalize_trace(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop None-only keys? No — keep schema stable. Just return a deep copy in order."""
    return [dict(e) for e in events]
```

- [ ] **Step 4: Run it, see it pass**

Run: `uv run pytest tests/fixtures/neutral_runtime_golden/test_recorder_self.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/fixtures/neutral_runtime_golden/recorder.py \
        tests/fixtures/neutral_runtime_golden/test_recorder_self.py
git commit -m "test(oracle): add ControlPlaneRecorder decision-trace capture"
```

---

## Task 0.3: Recorder ↔ ControlPlanePlugin tap

**Files:**
- Create: `tests/fixtures/neutral_runtime_golden/tap.py`
- Test: `tests/fixtures/neutral_runtime_golden/test_tap.py`

**Goal:** A helper `recording_plane(controls, recorder)` that wraps `build_default_plugin(...)`'s
plugin (or a `ControlPlane` you assemble from the same controls) so every fan-out decision is
mirrored into the recorder, **without changing control behavior**.

- [ ] **Step 1: Read** `adk_bridge/control_plane.py` to find the exact wrap point — either
  (a) the `ControlPlanePlugin` methods, or (b) a per-control decorator at `ControlPlane.register`.
  Choose the seam that observes the *final* decision (after fan-out ordering: first-deny-wins,
  rewrite-mutates, after-tool-first-non-None).

- [ ] **Step 2: Write the failing test** that drives one before_tool deny through the real plugin
  and asserts the recorder captured exactly one `before_tool/deny` event. Use the trigger setup
  copied from `tests/test_resilience_plugin_wiring.py`'s loop-guard test (read it; reuse its mock
  tool-context + duplicate-call construction).

- [ ] **Step 3: Run, see it fail.** `uv run pytest tests/fixtures/neutral_runtime_golden/test_tap.py -q`

- [ ] **Step 4: Implement `tap.py`** — a thin observer that subscribes to the plugin's decisions
  and calls the matching `recorder.record_*`. It MUST be pure-observe (return values unchanged).

- [ ] **Step 5: Run, see it pass.**

- [ ] **Step 6: Commit** `test(oracle): tap ControlPlanePlugin decisions into recorder`.

---

## Task 0.4: Four scenario drivers (one per hard seam)

**Files:**
- Create: `tests/fixtures/neutral_runtime_golden/scenarios.py`
- Test: `tests/fixtures/neutral_runtime_golden/test_scenarios_smoke.py`

Each driver returns a recorded+normalized trace. Build each by **copying the trigger setup from the
existing test that already exercises that seam** (do not invent triggers):

| Driver | Seam | Copy trigger from |
|---|---|---|
| `run_loop_guard_scenario()` | S-C resilience loop-guard | `tests/test_resilience_plugin_wiring.py` (Nth identical tool call) |
| `run_compaction_scenario()` | S-D compaction threshold | `tests/adk_bridge/test_context_compaction_plugin.py` (token over threshold) |
| `run_edit_retry_scenario()` | S-C edit-retry | `tests/adk_bridge/test_extended_plugin_on_tool_error.py` (edit error dict + raise) |
| `run_ga_constraint_scenario()` | S-A GA reinjection | `tests/test_ga_constraint_control.py` (missing owed deliverable) |

- [ ] **Step 1:** Write `test_scenarios_smoke.py` asserting each driver returns a non-empty trace
  whose first event matches the expected seam signature (e.g. loop-guard → a `before_tool/deny`;
  compaction → a `compaction/fired:true`; edit-retry → an `after_tool` override; GA → a `reinject`
  with `source` containing `ga`/`constraint`). Run → fail.

- [ ] **Step 2:** Implement the 4 drivers in `scenarios.py`, each: assemble the relevant control(s)
  via the same construction the source test uses, wrap with `recording_plane`, drive the trigger,
  return `normalize_trace(recorder.events)`.

- [ ] **Step 3:** Run → pass. **Commit** `test(oracle): 4 scenario drivers for the hard seams`.

---

## Task 0.5: Capture golden + the regression assertion

**Files:**
- Create (generated): `tests/fixtures/neutral_runtime_golden/golden/{loop_guard,compaction,edit_retry,ga_constraint}.json`
- Create: `tests/fixtures/neutral_runtime_golden/test_golden_regression.py`
- Create: `tests/fixtures/neutral_runtime_golden/capture.py` (regen utility, `python -m`)

- [ ] **Step 1:** Write `capture.py` — runs the 4 drivers and writes each normalized trace to its
  golden JSON (pretty, sorted keys). Guard it behind an explicit flag so it never auto-overwrites:

```python
# python -m tests.fixtures.neutral_runtime_golden.capture --write
```

- [ ] **Step 2:** Write `test_golden_regression.py` — for each scenario, run the driver and assert
  the live trace equals the committed golden JSON (exact). On mismatch, the assertion message must
  print a unified diff and remind: "control-plane behavior changed — if intended, re-run capture
  --write and review the diff in the PR."

- [ ] **Step 3:** Generate the goldens **on the pristine base** (this branch, before any control
  migration):
```bash
MAGI_CONFIG="$(mktemp -d)/config.toml" uv run python -m tests.fixtures.neutral_runtime_golden.capture --write
```

- [ ] **Step 4:** Run the regression test → it must PASS against the just-captured goldens:
```bash
uv run pytest tests/fixtures/neutral_runtime_golden/test_golden_regression.py -q
```

- [ ] **Step 5: Commit** the goldens + test:
```bash
git add tests/fixtures/neutral_runtime_golden/
git commit -m "test(oracle): freeze control-plane golden traces for 4 hard seams"
```

---

## Acceptance criteria (Phase 0 done)

- [ ] `uv run pytest tests/fixtures/neutral_runtime_golden/ -q` is green, **no API keys**, headless.
- [ ] The 4 goldens exist and encode a non-trivial decision trace per seam (deny / compaction-fired /
  after-tool-override / reinject respectively).
- [ ] `capture --write` is the **only** way to mutate goldens; the regression test fails loudly on
  any unreviewed control-plane behavior change.
- [ ] Documented one-liner for later phases: *"Before/after any control-plane edit, run the golden
  regression; a diff = a behavior change to review."*

## Rollback

Phase 0 is purely additive under `tests/fixtures/neutral_runtime_golden/`. Revert =
`git revert` the phase commits or delete the directory; nothing in the runtime depends on it.

## Hand-off to later phases

Phase 5 (control-plane migration) MUST, for each migrated control, keep this regression green —
or, when a behavior change is *intentional and correct*, regenerate the affected golden with
`capture --write` and call out the diff explicitly in that task's commit. This is the mechanism that
turns "did we silently break the agent loop?" into a `pytest` answer.
