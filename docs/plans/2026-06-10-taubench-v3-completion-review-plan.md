# τ-bench v3 Completion+Scope Self-Review (L4) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a fourth toggleable, default-OFF reliability lever (`completion_review`/L4) that injects one in-loop completion+scope self-review when the agent signals it is concluding, plus harden `run_with_retry` against transient infra errors.

**Architecture:** Pure helpers (`is_conclusion`, `completion_review_nudge`) + a new `completion_review` flag in `ReliabilityConfig` live in `reliability.py`. `episode.py` gains a second independent one-shot nudge block (L4) after the existing L2 block. `harness.py`'s `run_with_retry` gains bounded backoff retries with an injectable `sleep`. All default-OFF / behavior-preserving when the flag is off.

**Tech Stack:** Python 3, pydantic (frozen models), pytest. No tau_bench / no network in the new code or tests.

**Spec:** `docs/plans/2026-06-10-taubench-v3-completion-review-design.md`

**Test command (from worktree root):** `uv run --extra dev --extra cli pytest <path> -q` (add `--extra providers` only if `google` import fails).

---

### Task 1: `completion_review` config + `is_conclusion` + `completion_review_nudge`

**Files:**
- Modify: `magi_agent/benchmarks/taubench/reliability.py`
- Test: `tests/benchmarks/taubench/test_reliability.py`

- [ ] **Step 1: Append these tests to the END of `tests/benchmarks/taubench/test_reliability.py`**

```python
from magi_agent.benchmarks.taubench.reliability import completion_review_nudge, is_conclusion


def test_config_completion_review_default_and_any_enabled() -> None:
    assert ReliabilityConfig().completion_review is False
    assert ReliabilityConfig(completion_review=True).any_enabled is True


def test_is_conclusion_detects_success_claim() -> None:
    assert is_conclusion("Your reservation is booked. Reservation ID R1.") is True


def test_is_conclusion_detects_refusal_and_closure() -> None:
    assert is_conclusion("I'm sorry, I'm unable to do that.") is True
    assert is_conclusion("Unfortunately I cannot cancel this.") is True
    assert is_conclusion("Is there anything else I can help with?") is True


def test_is_conclusion_silent_on_info_question() -> None:
    assert is_conclusion("Can you confirm your travel dates first?") is False


def test_is_conclusion_handles_empty() -> None:
    assert is_conclusion("") is False


def test_completion_review_nudge_is_general_no_domain_tokens() -> None:
    msg = completion_review_nudge()
    assert isinstance(msg, str) and len(msg) > 0
    low = msg.lower()
    for tok in ("flight", "reservation", "cabin", "airline", "baggage", "certificate"):
        assert tok not in low
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --extra dev --extra cli pytest tests/benchmarks/taubench/test_reliability.py -q`
Expected: FAIL — `ImportError: cannot import name 'is_conclusion'` (and `completion_review` attribute error).

- [ ] **Step 3: Add the `completion_review` field to `ReliabilityConfig`**

In `magi_agent/benchmarks/taubench/reliability.py`, change the `ReliabilityConfig` class body to add the fourth field and OR it into `any_enabled`:

```python
class ReliabilityConfig(BaseModel):
    """Toggle for each lever. All default OFF (behavior-preserving)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    arg_validation: bool = False
    dup_write_guard: bool = False
    verify_before_final: bool = False
    completion_review: bool = False

    @property
    def any_enabled(self) -> bool:
        return (
            self.arg_validation
            or self.dup_write_guard
            or self.verify_before_final
            or self.completion_review
        )
```

- [ ] **Step 4: Add `_CONCLUSION_MARKERS`, `is_conclusion`, and `completion_review_nudge`**

In `magi_agent/benchmarks/taubench/reliability.py`, AFTER the existing `verify_final` function and its `_SUCCESS_MARKERS` tuple, and BEFORE `__all__`, add:

```python
# Conclusion = the agent is wrapping up: either a success claim (reuse the L2
# success markers) OR a refusal/closure. Catches both under-action (refusal that
# leaves work undone) and premature success claims. Lowercased substring match.
_CONCLUSION_MARKERS = _SUCCESS_MARKERS + (
    "unable to",
    "not able to",
    "cannot",
    "can't",
    "won't be able",
    "i'm sorry",
    "i am sorry",
    "unfortunately",
    "is there anything else",
    "anything else i can",
)


def is_conclusion(agent_text: str) -> bool:
    """True if the agent text reads like it is concluding the interaction (a
    success claim or a refusal/closure), as opposed to asking for more info."""
    text = (agent_text or "").lower()
    return any(marker in text for marker in _CONCLUSION_MARKERS)


def completion_review_nudge() -> str:
    """A domain-agnostic completion+scope self-review prompt. No ground-truth, no
    domain rules — a general 'did I do all and only what was asked?' check."""
    return (
        "Before you confirm completion or close this out: re-read the user's "
        "messages and list every concrete action they asked you to perform. For "
        "each, state whether you actually executed it (and with which tool call) "
        "or not. Then check whether you performed any action the user did NOT "
        "request. If a requested action is missing, perform it now. If you "
        "performed an unrequested action, correct it. Only confirm completion "
        "once every requested action — and only those — has been done. Do not "
        "claim completion you cannot support."
    )
```

- [ ] **Step 5: Update `__all__`**

In `magi_agent/benchmarks/taubench/reliability.py`, update `__all__` to (alphabetical):

```python
__all__ = [
    "DEFAULT_WRITE_PREFIXES",
    "ReliabilityConfig",
    "WriteLedger",
    "completion_review_nudge",
    "is_conclusion",
    "looks_like_error",
    "validate_args",
    "verify_final",
]
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run --extra dev --extra cli pytest tests/benchmarks/taubench/test_reliability.py -q`
Expected: PASS (existing reliability tests + 6 new).

- [ ] **Step 7: Commit**

```bash
git add magi_agent/benchmarks/taubench/reliability.py tests/benchmarks/taubench/test_reliability.py
git commit -m "feat(taubench): completion_review config + is_conclusion + nudge (L4 pure)"
```

---

### Task 2: L4 one-shot completion-review nudge in `episode.py`

**Files:**
- Modify: `magi_agent/benchmarks/taubench/episode.py`
- Test: `tests/benchmarks/taubench/test_episode.py`

- [ ] **Step 1: Append these tests to the END of `tests/benchmarks/taubench/test_episode.py`**

(The helper `_text_of`, the `_Event`/`_Content`/`_Part` classes, `FakeEnv`, `FakeAction`, `FakeResp`, `EpisodeState`, `run_episode`, and `ReliabilityConfig`/`WriteLedger` imports already exist in this file from earlier tasks.)

```python
def test_l4_injects_completion_review_on_refusal_conclusion() -> None:
    env = FakeEnv(script=[
        FakeResp("user-reply", 0.0, False),
        FakeResp("###STOP###", 1.0, True),
    ])
    state = EpisodeState()
    seen: list[str] = []
    calls = {"n": 0}
    texts = ["I'm sorry, I'm unable to provide that compensation.", "ok done", "bye"]

    def factory(*, instruction, tools):
        class _R:
            async def run_async(self, **kw):
                seen.append(_text_of(kw["new_message"]))
                idx = calls["n"]
                calls["n"] += 1
                yield _Event(texts[idx])
        return _R()

    result = run_episode(
        env, task_index=0, state=state, runner_factory=factory,
        action_factory=FakeAction, respond_action_name="respond", max_steps=6,
        reliability=ReliabilityConfig(completion_review=True),
    )
    respond_contents = [a.kwargs["content"] for a in env.steps if a.name == "respond"]
    assert "I'm sorry, I'm unable to provide that compensation." not in respond_contents
    assert respond_contents[0] == "ok done"
    assert any("every concrete action" in m for m in seen)
    assert result.done is True


def test_l4_silent_on_info_question() -> None:
    env = FakeEnv(script=[FakeResp("###STOP###", 1.0, True)])
    state = EpisodeState()
    calls = {"n": 0}
    texts = ["Can you confirm your travel dates first?"]

    def factory(*, instruction, tools):
        class _R:
            async def run_async(self, **kw):
                idx = calls["n"]
                calls["n"] += 1
                yield _Event(texts[idx])
        return _R()

    result = run_episode(
        env, task_index=0, state=state, runner_factory=factory,
        action_factory=FakeAction, respond_action_name="respond", max_steps=5,
        reliability=ReliabilityConfig(completion_review=True),
    )
    respond_contents = [a.kwargs["content"] for a in env.steps if a.name == "respond"]
    assert respond_contents == ["Can you confirm your travel dates first?"]
    assert result.done is True


def test_l2_and_l4_independent_latches() -> None:
    env = FakeEnv(script=[
        FakeResp("user-reply", 0.0, False),
        FakeResp("###STOP###", 1.0, True),
    ])
    state = EpisodeState()
    led = WriteLedger()  # empty -> L2 fires on the success claim
    seen: list[str] = []
    calls = {"n": 0}
    texts = [
        "Your reservation is booked! Reservation ID X",   # turn 1 -> L2 nudge
        "I'm sorry, I am unable to complete the rest.",    # turn 2 -> L4 nudge
        "ok",                                              # turn 3 -> respond
        "done",                                            # turn 4 -> respond -> STOP
    ]

    def factory(*, instruction, tools):
        class _R:
            async def run_async(self, **kw):
                seen.append(_text_of(kw["new_message"]))
                idx = calls["n"]
                calls["n"] += 1
                yield _Event(texts[idx])
        return _R()

    result = run_episode(
        env, task_index=0, state=state, runner_factory=factory,
        action_factory=FakeAction, respond_action_name="respond", max_steps=8,
        reliability=ReliabilityConfig(verify_before_final=True, completion_review=True),
        ledger=led,
    )
    respond_contents = [a.kwargs["content"] for a in env.steps if a.name == "respond"]
    assert "Your reservation is booked! Reservation ID X" not in respond_contents
    assert "I'm sorry, I am unable to complete the rest." not in respond_contents
    assert respond_contents[0] == "ok"
    assert any("Re-check the tool results" in m for m in seen)   # L2 nudge delivered
    assert any("every concrete action" in m for m in seen)       # L4 nudge delivered
    assert result.done is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --extra dev --extra cli pytest tests/benchmarks/taubench/test_episode.py -q`
Expected: FAIL — the refusal text is routed as a respond (no L4 nudge yet), so `assert ... not in respond_contents` / `assert any("every concrete action" ...)` fail.

- [ ] **Step 3: Import the L4 helpers in `episode.py`**

In `magi_agent/benchmarks/taubench/episode.py`, extend the existing reliability import to:

```python
from magi_agent.benchmarks.taubench.reliability import (
    ReliabilityConfig,
    WriteLedger,
    completion_review_nudge,
    is_conclusion,
    verify_final,
)
```

- [ ] **Step 4: Add the `reviewed` latch**

In `run_episode`, find the existing line `nudged = False` (right after `led = ledger if ledger is not None else WriteLedger()`) and add immediately below it:

```python
    reviewed = False
```

- [ ] **Step 5: Add the L4 block after the existing L2 block**

In `run_episode`'s loop, the existing L2 block is:

```python
        if cfg.verify_before_final and not nudged:
            try:
                nudge = verify_final(led, agent_text)
            except Exception:
                nudge = None
            if nudge:
                nudged = True
                obs = nudge
                continue  # give the agent one grounded turn; skip this respond
```

Immediately AFTER that block (and before the `# the agent's tool calls already hit env.step ...` comment + the respond `env.step`), insert the L4 block:

```python
        if cfg.completion_review and not reviewed:
            try:
                conclude = is_conclusion(agent_text)
            except Exception:
                conclude = False
            if conclude:
                reviewed = True
                obs = completion_review_nudge()
                continue  # one grounded turn to complete/scope-correct; skip respond
```

Do not change anything else in the loop.

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run --extra dev --extra cli pytest tests/benchmarks/taubench/test_episode.py -q`
Expected: PASS (existing episode tests + 3 new).

- [ ] **Step 7: Commit**

```bash
git add magi_agent/benchmarks/taubench/episode.py tests/benchmarks/taubench/test_episode.py
git commit -m "feat(taubench): one-shot completion+scope self-review nudge in loop (L4)"
```

---

### Task 3: Harden `run_with_retry` with bounded backoff

**Files:**
- Modify: `magi_agent/benchmarks/taubench/harness.py`
- Test: `tests/benchmarks/taubench/test_harness.py`

- [ ] **Step 1: Update the two existing retry tests + add new ones**

In `tests/benchmarks/taubench/test_harness.py`, REPLACE the two existing tests
`test_run_with_retry_recovers_on_second` and `test_run_with_retry_persistent_infra_error`
with the versions below (they inject a no-op `sleep` so the backoff adds no real
delay), and append the three new tests:

```python
def test_run_with_retry_recovers_on_second() -> None:
    seq = iter([EpisodeResult(reward=0, done=False, turns=0, infra_error=True),
                EpisodeResult(reward=1.0, done=True, turns=1)])
    assert run_with_retry(lambda: next(seq), sleep=lambda *_: None) == (True, False)


def test_run_with_retry_persistent_infra_error() -> None:
    err = lambda: EpisodeResult(reward=0, done=False, turns=0, infra_error=True)  # noqa: E731
    assert run_with_retry(err, sleep=lambda *_: None) == (False, True)


def test_run_with_retry_no_sleep_on_first_success() -> None:
    slept = {"n": 0}

    def sleep(_seconds: float) -> None:
        slept["n"] += 1

    result = run_with_retry(
        lambda: EpisodeResult(reward=1.0, done=True, turns=1), sleep=sleep
    )
    assert result == (True, False)
    assert slept["n"] == 0


def test_run_with_retry_attempt_and_sleep_counts_on_recovery() -> None:
    seq = [EpisodeResult(reward=0, done=False, turns=0, infra_error=True),
           EpisodeResult(reward=0, done=False, turns=0, infra_error=True),
           EpisodeResult(reward=1.0, done=True, turns=1)]
    counts = {"attempt": 0, "sleep": 0}

    def attempt() -> EpisodeResult:
        r = seq[counts["attempt"]]
        counts["attempt"] += 1
        return r

    def sleep(_seconds: float) -> None:
        counts["sleep"] += 1

    assert run_with_retry(attempt, sleep=sleep) == (True, False)
    assert counts["attempt"] == 3  # initial + 2 retries
    assert counts["sleep"] == 2    # one backoff before each retry


def test_run_with_retry_persistent_attempt_count() -> None:
    counts = {"attempt": 0}

    def attempt() -> EpisodeResult:
        counts["attempt"] += 1
        return EpisodeResult(reward=0, done=False, turns=0, infra_error=True)

    assert run_with_retry(attempt, sleep=lambda *_: None) == (False, True)
    assert counts["attempt"] == 3  # initial + 2 retries (default retries=2)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --extra dev --extra cli pytest tests/benchmarks/taubench/test_harness.py -q`
Expected: FAIL — `run_with_retry() got an unexpected keyword argument 'sleep'`.

- [ ] **Step 3: Update `run_with_retry` in `harness.py`**

In `magi_agent/benchmarks/taubench/harness.py`, add `import time` to the top imports
(after `from __future__ import annotations`), and add `Callable` if not already
imported (the file already imports `from collections.abc import Callable`). Replace
the entire `run_with_retry` function with:

```python
def run_with_retry(
    attempt: Callable[[], EpisodeResult],
    *,
    retries: int = 2,
    sleep: Callable[[float], None] = time.sleep,
    base_delay: float = 2.0,
) -> tuple[bool, bool]:
    """Run an episode attempt; retry up to `retries` times on infra_error, with a
    bounded linear backoff (`base_delay * attempt_number`) between attempts.

    `sleep` is injectable so tests run without real delay. Returns
    (success, infra_failed) where infra_failed=True means every attempt
    infra-errored (counted as a non-success and surfaced rather than silently
    attributed to model failure)."""
    result = attempt()
    tries = 0
    while result.infra_error and tries < retries:
        sleep(base_delay * (tries + 1))
        result = attempt()
        tries += 1
    if result.infra_error:
        return (False, True)
    return (bool(result.done and result.reward >= 1.0), False)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --extra dev --extra cli pytest tests/benchmarks/taubench/test_harness.py -q`
Expected: PASS (existing harness tests + the updated/added retry tests).

- [ ] **Step 5: Commit**

```bash
git add magi_agent/benchmarks/taubench/harness.py tests/benchmarks/taubench/test_harness.py
git commit -m "feat(taubench): bounded-backoff retries in run_with_retry (transient infra)"
```

---

### Task 4: Full-suite regression + default-off check

**Files:** none (verification only)

- [ ] **Step 1: Run the whole τ-bench test module**

Run: `uv run --extra dev --extra cli pytest tests/benchmarks/taubench/ -q`
Expected: PASS — all reliability, tau_env, episode, agent, cli, config, scorer, harness, integration tests green (the prior 60 + the new L4/retry tests).

- [ ] **Step 2: Confirm default-off behavior unchanged**

Confirm the pre-existing tests (those NOT passing `completion_review=True` or a custom `sleep`) still pass unchanged — this is the byte-identical-when-off guarantee. If any pre-existing test regressed, the L4 gating in Task 2 or the retry default in Task 3 is wrong and must be fixed before proceeding.

---

## Measurement (after all tasks land — operator-run, NOT part of TDD)

Run from the worktree with the gate + keys set (Anthropic + OpenAI user-sim). Use
`screen -dmS` + `caffeinate -i` for the long runs.

**Quick subset (airline 50×1), isolate L4:**

```python
# vanilla (all levers off)
run_eval(domain="airline", max_tasks=50, trials=1, config="vanilla", model="claude-sonnet-4-5")
# v3 (completion_review only)
run_eval(domain="airline", max_tasks=50, trials=1, config="vanilla",
         model="claude-sonnet-4-5",
         reliability=ReliabilityConfig(completion_review=True))
```

Compare pass^1 + avg_reward. If v3 shows a meaningful lift, run **v2+v3 (all four
on)** and full **airline 50×4** + per-lever ablation. Single-trial SE ≈ ±0.07 —
the quick subset is a signal, not proof. Report absolute + delta honestly; L4 is a
general mechanism, so a lift implies general improvement, not airline overfit.

---

## Self-Review notes (completed by plan author)

- **Spec coverage:** L4 config+helpers→Task 1; L4 in-loop nudge + independent
  latch→Task 2; `run_with_retry` backoff→Task 3; default-off + regression→Task 4;
  measurement plan→Measurement section. cli.py needs no change (the new field
  rides through `ReliabilityConfig.model_dump()`); noted in spec. All spec
  sections mapped.
- **Type consistency:** `ReliabilityConfig.completion_review`, `is_conclusion(agent_text)`,
  `completion_review_nudge()`, `run_with_retry(attempt, *, retries, sleep, base_delay)`
  — names identical across tasks and call sites. L2 nudge text "Re-check the tool
  results" matches the existing `verify_final` string asserted in Task 2's
  independent-latch test.
- **No placeholders:** every code step has complete code; every run step has an
  exact command + expected result.
