# τ-bench v2 Reliability Checkpoint Pack Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add three toggleable, general agent-reliability levers at the τ-bench driver boundary (arg validation, duplicate-write guard, success-claim grounding) and thread them through the harness, all default-OFF.

**Architecture:** A new pure module `reliability.py` holds `ReliabilityConfig`, `validate_args`, `WriteLedger`, `looks_like_error`, and `verify_final`. `tau_env.py` applies L1 (validate) + L3 (dup guard) before `env.step` and records write outcomes into a shared `WriteLedger`. `episode.py` applies L2 (one-shot nudge) before routing a success-claiming respond. `agent.py` creates one shared ledger per episode and threads config+ledger into both builders. `cli.py` exposes a `reliability` param on `run_eval`. Levers fail-open (a lever exception degrades to no-intervention).

**Tech Stack:** Python 3, pydantic (frozen models), google.adk FunctionTool (already used), pytest. tau_bench is NOT imported by the new code or tests.

**Spec:** `docs/plans/2026-06-09-taubench-v2-reliability-design.md`

**Plan-level refinements over the spec (intentional, within spec guardrails):**
- `is_repeat_write` keys on `(tool_name, normalized args)` identity, not write-type alone — avoids false-blocking a legitimately different write of the same type (spec: "be conservative, avoid false blocks").
- `verify_final` success markers are assertion phrases ("is booked", "reservation id", "is confirmed", "successfully", ...) rather than bare words like "confirm"/"completed" — avoids treating a clarifying question ("Can you confirm your dates?") as a success claim (spec: "if too fragile, narrow it").

**Test command (run from the worktree root):**
`uv run --extra dev --extra cli pytest <path> -q`
If imports of `google.genai`/`google.adk` fail, add `--extra providers`. If a stray `~/.magi/config.toml` causes provider-config pollution, prefix with `MAGI_CONFIG=$(mktemp)`.

---

### Task 1: `ReliabilityConfig` + `validate_args`

**Files:**
- Create: `magi_agent/benchmarks/taubench/reliability.py`
- Test: `tests/benchmarks/taubench/test_reliability.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/benchmarks/taubench/test_reliability.py`:

```python
# tests/benchmarks/taubench/test_reliability.py
from __future__ import annotations

from magi_agent.benchmarks.taubench.reliability import ReliabilityConfig, validate_args

AIRLINE_SPEC = {
    "type": "object",
    "properties": {
        "flight_type": {"type": "string", "enum": ["one_way", "round_trip"]},
        "passengers": {"type": "integer"},
        "user_id": {"type": "string"},
    },
    "required": ["user_id"],
}


def test_config_defaults_all_off() -> None:
    c = ReliabilityConfig()
    assert (c.arg_validation, c.dup_write_guard, c.verify_before_final) == (False, False, False)
    assert c.any_enabled is False


def test_config_any_enabled() -> None:
    assert ReliabilityConfig(arg_validation=True).any_enabled is True


def test_validate_args_rejects_enum_mismatch() -> None:
    msg = validate_args(AIRLINE_SPEC, {"user_id": "u1", "flight_type": "one way"})
    assert msg is not None and "flight_type" in msg


def test_validate_args_rejects_missing_required() -> None:
    msg = validate_args(AIRLINE_SPEC, {"flight_type": "one_way"})
    assert msg is not None and "user_id" in msg


def test_validate_args_rejects_wrong_type() -> None:
    msg = validate_args(AIRLINE_SPEC, {"user_id": "u1", "passengers": "two"})
    assert msg is not None and "passengers" in msg


def test_validate_args_rejects_bool_for_integer() -> None:
    msg = validate_args(AIRLINE_SPEC, {"user_id": "u1", "passengers": True})
    assert msg is not None and "passengers" in msg


def test_validate_args_accepts_valid() -> None:
    assert validate_args(
        AIRLINE_SPEC, {"user_id": "u1", "flight_type": "one_way", "passengers": 2}
    ) is None


def test_validate_args_accepts_unknown_optional_key() -> None:
    assert validate_args(AIRLINE_SPEC, {"user_id": "u1", "note": "anything"}) is None


def test_validate_args_no_properties_passes() -> None:
    assert validate_args({"type": "object"}, {"x": 1}) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --extra dev --extra cli pytest tests/benchmarks/taubench/test_reliability.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'magi_agent.benchmarks.taubench.reliability'`.

- [ ] **Step 3: Create `reliability.py` with config + validation**

Create `magi_agent/benchmarks/taubench/reliability.py`:

```python
# magi_agent/benchmarks/taubench/reliability.py
"""Pure, network-free reliability levers for the τ-bench driver boundary.

No tau_bench import, no ADK import. Three general levers:
- L1 validate_args: schema-driven argument validation before a tool runs.
- L3 WriteLedger / dup guard: block re-executing an identical successful write.
- L2 verify_final: ground a success claim against recorded write outcomes.
"""
from __future__ import annotations

import json

from pydantic import BaseModel, ConfigDict


class ReliabilityConfig(BaseModel):
    """Toggle for each lever. All default OFF (behavior-preserving)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    arg_validation: bool = False
    dup_write_guard: bool = False
    verify_before_final: bool = False

    @property
    def any_enabled(self) -> bool:
        return self.arg_validation or self.dup_write_guard or self.verify_before_final


_JSON_TYPE_CHECKS: dict[str, type | tuple[type, ...]] = {
    "string": str,
    "integer": int,
    "number": (int, float),
    "boolean": bool,
    "object": dict,
    "array": (list, tuple),
}


def _type_ok(value: object, expected: str) -> bool:
    py = _JSON_TYPE_CHECKS.get(expected)
    if py is None:
        return True  # unknown type spec -> do not reject
    if expected in ("integer", "number") and isinstance(value, bool):
        return False  # bool is a subclass of int; reject it for numeric fields
    return isinstance(value, py)


def validate_args(parameters: dict, arguments: dict) -> str | None:
    """Validate `arguments` against a tau_bench tool spec's `parameters` schema.

    Returns a corrective message string on a CLEAR violation (missing required
    key, enum mismatch, wrong primitive type), else None. Conservative: unknown
    keys and unconstrained values pass so the lever never false-blocks a
    plausible call.
    """
    if not isinstance(parameters, dict):
        return None
    props = parameters.get("properties")
    if not isinstance(props, dict):
        return None
    args = arguments or {}
    required = parameters.get("required") or []
    missing = [k for k in required if k not in args]
    if missing:
        return (
            f"Invalid arguments: missing required parameter(s) {missing}. "
            "Supply them and call the tool again."
        )
    problems: list[str] = []
    for key, value in args.items():
        spec = props.get(key)
        if not isinstance(spec, dict):
            continue  # unknown-but-plausible key: do not reject
        enum = spec.get("enum")
        if isinstance(enum, list) and enum and value not in enum:
            problems.append(f"{key}={value!r} is not one of {enum}")
            continue
        expected = spec.get("type")
        if isinstance(expected, str) and not _type_ok(value, expected):
            problems.append(f"{key}={value!r} is not of type {expected}")
    if problems:
        return (
            "Invalid arguments: " + "; ".join(problems)
            + ". Correct them and call the tool again."
        )
    return None


__all__ = ["ReliabilityConfig", "validate_args"]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --extra dev --extra cli pytest tests/benchmarks/taubench/test_reliability.py -q`
Expected: PASS (8 tests).

- [ ] **Step 5: Commit**

```bash
git add magi_agent/benchmarks/taubench/reliability.py tests/benchmarks/taubench/test_reliability.py
git commit -m "feat(taubench): reliability config + schema-driven arg validation (L1)"
```

---

### Task 2: `WriteLedger` + `looks_like_error`

**Files:**
- Modify: `magi_agent/benchmarks/taubench/reliability.py`
- Test: `tests/benchmarks/taubench/test_reliability.py`

- [ ] **Step 1: Add the failing tests**

Append to `tests/benchmarks/taubench/test_reliability.py`:

```python
from magi_agent.benchmarks.taubench.reliability import WriteLedger, looks_like_error


def test_is_write_by_prefix() -> None:
    led = WriteLedger()
    assert led.is_write("book_reservation") is True
    assert led.is_write("cancel_reservation") is True
    assert led.is_write("update_reservation_flights") is True
    assert led.is_write("send_certificate") is True
    assert led.is_write("get_reservation_details") is False


def test_repeat_write_same_name_and_args_order_independent() -> None:
    led = WriteLedger()
    led.record("book_reservation", {"user_id": "u1", "flight": "F1"}, ok=True)
    assert led.is_repeat_write("book_reservation", {"flight": "F1", "user_id": "u1"}) is True


def test_not_repeat_when_args_differ() -> None:
    led = WriteLedger()
    led.record("book_reservation", {"flight": "F1"}, ok=True)
    assert led.is_repeat_write("book_reservation", {"flight": "F2"}) is False


def test_not_repeat_when_prior_write_failed() -> None:
    led = WriteLedger()
    led.record("book_reservation", {"flight": "F1"}, ok=False)
    assert led.is_repeat_write("book_reservation", {"flight": "F1"}) is False


def test_had_successful_write_and_last_errored_transitions() -> None:
    led = WriteLedger()
    assert led.had_successful_write() is False
    assert led.last_write_errored() is False  # empty ledger -> not errored
    led.record("book_reservation", {"x": 1}, ok=False)
    assert led.had_successful_write() is False
    assert led.last_write_errored() is True
    led.record("book_reservation", {"x": 1}, ok=True)
    assert led.had_successful_write() is True
    assert led.last_write_errored() is False


def test_looks_like_error() -> None:
    assert looks_like_error("Error: bad action") is True
    assert looks_like_error("  error - nope") is True
    assert looks_like_error("Reservation booked id=R1") is False
    assert looks_like_error(123) is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --extra dev --extra cli pytest tests/benchmarks/taubench/test_reliability.py -q`
Expected: FAIL with `ImportError: cannot import name 'WriteLedger'`.

- [ ] **Step 3: Add `WriteLedger` + `looks_like_error` to `reliability.py`**

In `magi_agent/benchmarks/taubench/reliability.py`, add after `validate_args` (before `__all__`):

```python
DEFAULT_WRITE_PREFIXES = ("book_", "cancel_", "update_", "send_")


class WriteLedger:
    """Per-episode record of write-tool calls and their outcomes.

    A "write" is any tool whose name starts with a configured prefix. A "repeat"
    write is an identical (name, normalized-args) write that already succeeded.
    """

    def __init__(self, write_prefixes: tuple[str, ...] = DEFAULT_WRITE_PREFIXES) -> None:
        self._prefixes = tuple(write_prefixes)
        self._records: list[tuple[str, str, bool]] = []  # (name, args_key, ok)

    def is_write(self, tool_name: str) -> bool:
        return any(tool_name.startswith(p) for p in self._prefixes)

    @staticmethod
    def _key(arguments: dict) -> str:
        try:
            return json.dumps(arguments or {}, sort_keys=True, default=str)
        except (TypeError, ValueError):
            return repr(arguments)

    def record(self, tool_name: str, arguments: dict, *, ok: bool) -> None:
        self._records.append((tool_name, self._key(arguments), ok))

    def is_repeat_write(self, tool_name: str, arguments: dict) -> bool:
        key = self._key(arguments)
        return any(
            name == tool_name and arg_key == key and ok
            for (name, arg_key, ok) in self._records
        )

    def had_successful_write(self) -> bool:
        return any(ok for (_name, _key, ok) in self._records)

    def last_write_errored(self) -> bool:
        if not self._records:
            return False
        return not self._records[-1][2]


def looks_like_error(observation: object) -> bool:
    """True if an env observation string indicates a tool error."""
    return isinstance(observation, str) and observation.strip().lower().startswith("error")
```

And update `__all__`:

```python
__all__ = [
    "DEFAULT_WRITE_PREFIXES",
    "ReliabilityConfig",
    "WriteLedger",
    "looks_like_error",
    "validate_args",
]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --extra dev --extra cli pytest tests/benchmarks/taubench/test_reliability.py -q`
Expected: PASS (14 tests).

- [ ] **Step 5: Commit**

```bash
git add magi_agent/benchmarks/taubench/reliability.py tests/benchmarks/taubench/test_reliability.py
git commit -m "feat(taubench): write ledger + error detection for dup-guard/grounding"
```

---

### Task 3: `verify_final`

**Files:**
- Modify: `magi_agent/benchmarks/taubench/reliability.py`
- Test: `tests/benchmarks/taubench/test_reliability.py`

- [ ] **Step 1: Add the failing tests**

Append to `tests/benchmarks/taubench/test_reliability.py`:

```python
from magi_agent.benchmarks.taubench.reliability import verify_final


def test_verify_final_nudges_on_success_claim_without_write() -> None:
    led = WriteLedger()
    msg = verify_final(led, "Your reservation is booked! Reservation ID HATHAT")
    assert msg is not None


def test_verify_final_nudges_when_last_write_errored() -> None:
    led = WriteLedger()
    led.record("book_reservation", {"x": 1}, ok=False)
    assert verify_final(led, "All set — your booking is confirmed.") is not None


def test_verify_final_silent_when_success_backed_by_write() -> None:
    led = WriteLedger()
    led.record("book_reservation", {"x": 1}, ok=True)
    assert verify_final(led, "Your reservation is booked. Reservation ID R1.") is None


def test_verify_final_silent_without_success_language() -> None:
    led = WriteLedger()
    assert verify_final(led, "Can you confirm your travel dates first?") is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --extra dev --extra cli pytest tests/benchmarks/taubench/test_reliability.py -q`
Expected: FAIL with `ImportError: cannot import name 'verify_final'`.

- [ ] **Step 3: Add `verify_final` to `reliability.py`**

In `magi_agent/benchmarks/taubench/reliability.py`, add after `looks_like_error`:

```python
# Assertion-style success phrases only. Deliberately excludes bare "confirm"/
# "completed" so a clarifying question ("Can you confirm your dates?") is not
# mistaken for a success claim.
_SUCCESS_MARKERS = (
    "is booked",
    "has been booked",
    "reservation id",
    "confirmation number",
    "is confirmed",
    "booking is confirmed",
    "successfully",
    "has been cancelled",
    "has been canceled",
    "has been completed",
)


def verify_final(ledger: WriteLedger, agent_text: str) -> str | None:
    """If the agent asserts success but the ledger does not support it (last
    write errored, or no successful write at all), return a one-time corrective
    message; else None. The caller enforces the one-shot-per-episode bound.
    """
    text = (agent_text or "").lower()
    if not any(marker in text for marker in _SUCCESS_MARKERS):
        return None
    if ledger.last_write_errored() or not ledger.had_successful_write():
        return (
            "Before confirming success to the user: your records show no "
            "successful write operation (the last write either failed or never "
            "happened). Re-check the tool results, then either perform the "
            "required action correctly or tell the user it did not complete. "
            "Do not claim success the tool results do not support."
        )
    return None
```

And add `"verify_final"` to `__all__` (keep alphabetical):

```python
__all__ = [
    "DEFAULT_WRITE_PREFIXES",
    "ReliabilityConfig",
    "WriteLedger",
    "looks_like_error",
    "validate_args",
    "verify_final",
]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --extra dev --extra cli pytest tests/benchmarks/taubench/test_reliability.py -q`
Expected: PASS (18 tests).

- [ ] **Step 5: Commit**

```bash
git add magi_agent/benchmarks/taubench/reliability.py tests/benchmarks/taubench/test_reliability.py
git commit -m "feat(taubench): success-claim grounding check (verify_final / L2)"
```

---

### Task 4: Wire L1 + L3 + write-recording into `tau_env.py`

**Files:**
- Modify: `magi_agent/benchmarks/taubench/tau_env.py`
- Test: `tests/benchmarks/taubench/test_tau_env.py`

- [ ] **Step 1: Add the failing tests**

Append to `tests/benchmarks/taubench/test_tau_env.py`:

```python
from magi_agent.benchmarks.taubench.reliability import ReliabilityConfig, WriteLedger


@dataclass
class _BookEnv:
    tools_info: tuple = (
        {"type": "function", "function": {"name": "book_reservation", "description": "d",
         "parameters": {"type": "object",
            "properties": {
                "flight_type": {"type": "string", "enum": ["one_way", "round_trip"]},
                "user_id": {"type": "string"}},
            "required": ["user_id"]}}},
    )
    steps: list = field(default_factory=list)

    def step(self, action):
        self.steps.append(action)
        return FakeResp(observation="Reservation booked id=R1", reward=0.0, done=False)


def test_l1_blocks_invalid_enum_without_stepping() -> None:
    env = _BookEnv()
    led = WriteLedger()
    cfg = ReliabilityConfig(arg_validation=True)
    callables = build_env_tool_callables(
        env, state=EpisodeState(), action_factory=FakeAction, reliability=cfg, ledger=led
    )
    out = asyncio.run(callables["book_reservation"]({"user_id": "u1", "flight_type": "one way"}, None))
    assert "flight_type" in str(out)
    assert env.steps == []  # never executed
    assert led.had_successful_write() is False


def test_l3_blocks_duplicate_write() -> None:
    env = _BookEnv()
    led = WriteLedger()
    cfg = ReliabilityConfig(dup_write_guard=True)
    callables = build_env_tool_callables(
        env, state=EpisodeState(), action_factory=FakeAction, reliability=cfg, ledger=led
    )
    args = {"user_id": "u1", "flight_type": "one_way"}
    out1 = asyncio.run(callables["book_reservation"](dict(args), None))
    assert "booked" in str(out1).lower()
    out2 = asyncio.run(callables["book_reservation"](dict(args), None))
    assert "uplicate" in str(out2)  # "Duplicate write blocked..."
    assert len(env.steps) == 1  # second call never executed


def test_records_successful_write_in_ledger() -> None:
    env = _BookEnv()
    led = WriteLedger()
    cfg = ReliabilityConfig(dup_write_guard=True)
    callables = build_env_tool_callables(
        env, state=EpisodeState(), action_factory=FakeAction, reliability=cfg, ledger=led
    )
    asyncio.run(callables["book_reservation"]({"user_id": "u1"}, None))
    assert led.had_successful_write() is True


def test_levers_off_by_default_do_not_interfere() -> None:
    env = _BookEnv()
    callables = build_env_tool_callables(env, state=EpisodeState(), action_factory=FakeAction)
    out = asyncio.run(callables["book_reservation"]({"user_id": "u1", "flight_type": "one way"}, None))
    assert "booked" in str(out).lower()  # executed despite invalid enum (validation off)
    assert len(env.steps) == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --extra dev --extra cli pytest tests/benchmarks/taubench/test_tau_env.py -q`
Expected: FAIL — `build_env_tool_callables() got an unexpected keyword argument 'reliability'`.

- [ ] **Step 3: Update `tau_env.py` to apply levers**

Replace the imports + `build_env_tool_callables` + `build_env_function_tools` signature in `magi_agent/benchmarks/taubench/tau_env.py`.

Update the top import block:

```python
from magi_agent.benchmarks.taubench.episode import EpisodeState
from magi_agent.benchmarks.taubench.reliability import (
    ReliabilityConfig,
    WriteLedger,
    looks_like_error,
    validate_args,
)
```

Replace `build_env_tool_callables` with:

```python
def build_env_tool_callables(
    env: Any,
    *,
    state: EpisodeState,
    action_factory: Callable[..., Any],
    reliability: ReliabilityConfig | None = None,
    ledger: WriteLedger | None = None,
) -> dict[str, Callable]:
    """One async callable per tool. Applies the reliability levers (when enabled),
    then calls env.step(Action(name, kwargs)), records (reward, done) into state,
    and records write outcomes into `ledger`. Returns the observation string.

    Levers fail-open: an exception inside a lever degrades to no-intervention.
    """
    cfg = reliability or ReliabilityConfig()
    led = ledger if ledger is not None else WriteLedger()
    callables: dict[str, Callable] = {}
    for spec in _tool_specs(env):
        name = spec["name"]
        params = spec["parameters"]

        def _make(tool_name: str, tool_params: dict) -> Callable:
            async def invoke(arguments: dict, tool_context: object = None) -> str:
                args = dict(arguments or {})
                if cfg.arg_validation:
                    try:
                        message = validate_args(tool_params, args)
                    except Exception:
                        message = None
                    if message:
                        return message
                try:
                    is_write = led.is_write(tool_name)
                except Exception:
                    is_write = False
                if cfg.dup_write_guard and is_write:
                    try:
                        repeat = led.is_repeat_write(tool_name, args)
                    except Exception:
                        repeat = False
                    if repeat:
                        return (
                            f"Duplicate write blocked: '{tool_name}' with these "
                            "arguments already completed successfully. Do not repeat it."
                        )
                try:
                    resp = env.step(action_factory(name=tool_name, kwargs=dict(args)))
                except Exception as exc:  # surface as observation, not infra error
                    if is_write:
                        try:
                            led.record(tool_name, args, ok=False)
                        except Exception:
                            pass
                    return f"Error: {exc}"
                state.observe(resp.reward, resp.done)
                obs = resp.observation
                if is_write:
                    try:
                        led.record(tool_name, args, ok=not looks_like_error(obs))
                    except Exception:
                        pass
                return obs

            invoke.__name__ = tool_name
            return invoke

        callables[name] = _make(name, params)
    return callables
```

Update the `build_env_function_tools` signature and its call to `build_env_tool_callables` (only those two lines change; the enrichment body stays identical):

```python
def build_env_function_tools(
    env: Any,
    *,
    state: EpisodeState,
    action_factory: Callable[..., Any],
    reliability: ReliabilityConfig | None = None,
    ledger: WriteLedger | None = None,
) -> list[object]:
```

and inside it:

```python
    callables = build_env_tool_callables(
        env,
        state=state,
        action_factory=action_factory,
        reliability=reliability,
        ledger=ledger,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --extra dev --extra cli pytest tests/benchmarks/taubench/test_tau_env.py -q`
Expected: PASS (existing 3 tests + 4 new = 7).

- [ ] **Step 5: Commit**

```bash
git add magi_agent/benchmarks/taubench/tau_env.py tests/benchmarks/taubench/test_tau_env.py
git commit -m "feat(taubench): apply L1 arg-validation + L3 dup-write guard in tool boundary"
```

---

### Task 5: One-shot L2 nudge in `episode.py`

**Files:**
- Modify: `magi_agent/benchmarks/taubench/episode.py`
- Test: `tests/benchmarks/taubench/test_episode.py`

- [ ] **Step 1: Add the failing tests**

Append to `tests/benchmarks/taubench/test_episode.py`:

```python
from magi_agent.benchmarks.taubench.reliability import ReliabilityConfig, WriteLedger


def _text_of(content) -> str:
    parts = getattr(content, "parts", None) or []
    return "".join(getattr(p, "text", "") or "" for p in parts)


def test_l2_injects_one_nudge_on_unsupported_success_claim() -> None:
    env = FakeEnv(script=[
        FakeResp("user-reply-after-nudge", 0.0, False),  # respond after the 2nd turn
        FakeResp("###STOP###", 1.0, True),                # respond after the 3rd turn -> done
    ])
    state = EpisodeState()
    led = WriteLedger()
    seen: list[str] = []
    calls = {"n": 0}
    texts = ["Your reservation is booked! Reservation ID HATHAT", "ok, fixing now", "done"]

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
        reliability=ReliabilityConfig(verify_before_final=True), ledger=led,
    )
    respond_contents = [a.kwargs["content"] for a in env.steps if a.name == "respond"]
    # The unsupported success claim was NOT routed as a respond — the nudge replaced it.
    assert "Your reservation is booked! Reservation ID HATHAT" not in respond_contents
    assert respond_contents[0] == "ok, fixing now"
    # The nudge was delivered to the agent as the next observation.
    assert any("Re-check the tool results" in m for m in seen)
    assert result.done is True


def test_l2_silent_when_write_succeeded() -> None:
    env = FakeEnv(script=[FakeResp("###STOP###", 1.0, True)])
    state = EpisodeState()
    led = WriteLedger()
    led.record("book_reservation", {"x": 1}, ok=True)
    calls = {"n": 0}
    texts = ["Your reservation is booked. Reservation ID R1."]

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
        reliability=ReliabilityConfig(verify_before_final=True), ledger=led,
    )
    respond_contents = [a.kwargs["content"] for a in env.steps if a.name == "respond"]
    assert respond_contents == ["Your reservation is booked. Reservation ID R1."]
    assert result.done is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --extra dev --extra cli pytest tests/benchmarks/taubench/test_episode.py -q`
Expected: FAIL — `run_episode() got an unexpected keyword argument 'reliability'`.

- [ ] **Step 3: Update `run_episode` to apply L2**

In `magi_agent/benchmarks/taubench/episode.py`, add the import near the top (after the existing imports):

```python
from magi_agent.benchmarks.taubench.reliability import (
    ReliabilityConfig,
    WriteLedger,
    verify_final,
)
```

Extend the `run_episode` signature with the two new keyword-only params (append after `session_id`):

```python
def run_episode(
    env: Any,
    task_index: int,
    *,
    state: EpisodeState,
    runner_factory: Callable[..., Any],
    action_factory: Callable[[str, dict], Any],
    respond_action_name: str,
    max_steps: int,
    instruction: str | None = None,
    tools: list[object] | None = None,
    session_id: str | None = None,
    reliability: ReliabilityConfig | None = None,
    ledger: WriteLedger | None = None,
) -> EpisodeResult:
```

Immediately after `episode_session_id = ...` (before `_run_turn`), add:

```python
    cfg = reliability or ReliabilityConfig()
    led = ledger if ledger is not None else WriteLedger()
    nudged = False
```

Replace the loop body's tail (the part from `turns += 1` to the end of the loop) with:

```python
        turns += 1
        if state.done:
            break
        if cfg.verify_before_final and not nudged:
            try:
                nudge = verify_final(led, agent_text)
            except Exception:
                nudge = None
            if nudge:
                nudged = True
                obs = nudge
                continue  # give the agent one grounded turn; skip this respond
        # the agent's tool calls already hit env.step during the turn (via FunctionTools,
        # which call state.observe). Now route the agent's user-facing text as a respond.
        resp = env.step(
            action_factory(name=respond_action_name, kwargs={"content": agent_text})
        )
        state.observe(resp.reward, resp.done)
        obs = resp.observation
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --extra dev --extra cli pytest tests/benchmarks/taubench/test_episode.py -q`
Expected: PASS (existing 4 tests + 2 new = 6).

- [ ] **Step 5: Commit**

```bash
git add magi_agent/benchmarks/taubench/episode.py tests/benchmarks/taubench/test_episode.py
git commit -m "feat(taubench): one-shot success-claim grounding nudge in episode loop (L2)"
```

---

### Task 6: Thread config + shared ledger through `agent.py`

**Files:**
- Modify: `magi_agent/benchmarks/taubench/agent.py`
- Test (create): `tests/benchmarks/taubench/test_agent.py`

- [ ] **Step 1: Write the failing test**

Create `tests/benchmarks/taubench/test_agent.py`:

```python
# tests/benchmarks/taubench/test_agent.py
from __future__ import annotations

import sys
import types
from dataclasses import dataclass

from magi_agent.benchmarks.taubench.reliability import ReliabilityConfig


@dataclass
class FakeAction:
    name: str
    kwargs: dict


def _install_fake_tau_bench(monkeypatch) -> None:
    base = types.ModuleType("tau_bench.agents.base")

    class Agent:  # minimal base
        pass

    base.Agent = Agent

    tb_types = types.ModuleType("tau_bench.types")
    tb_types.RESPOND_ACTION_NAME = "respond"
    tb_types.Action = FakeAction

    @dataclass
    class SolveResult:
        reward: float
        info: dict
        messages: list
        total_cost: float

    tb_types.SolveResult = SolveResult

    monkeypatch.setitem(sys.modules, "tau_bench", types.ModuleType("tau_bench"))
    monkeypatch.setitem(sys.modules, "tau_bench.agents", types.ModuleType("tau_bench.agents"))
    monkeypatch.setitem(sys.modules, "tau_bench.agents.base", base)
    monkeypatch.setitem(sys.modules, "tau_bench.types", tb_types)


def test_solve_threads_config_and_one_shared_ledger(monkeypatch) -> None:
    _install_fake_tau_bench(monkeypatch)
    from magi_agent.benchmarks.taubench import agent as agent_mod
    from magi_agent.benchmarks.taubench.episode import EpisodeResult

    captured: dict[str, object] = {}

    def fake_build_tools(env, *, state, action_factory, reliability=None, ledger=None):
        captured["tools_reliability"] = reliability
        captured["tools_ledger"] = ledger
        return ["TOOL"]

    def fake_run_episode(
        env, task_index, *, state, runner_factory, action_factory,
        respond_action_name, max_steps, instruction=None, tools=None,
        session_id=None, reliability=None, ledger=None,
    ):
        captured["ep_reliability"] = reliability
        captured["ep_ledger"] = ledger
        return EpisodeResult(reward=1.0, done=True, turns=1)

    monkeypatch.setattr(agent_mod, "build_env_function_tools", fake_build_tools)
    monkeypatch.setattr(agent_mod, "run_episode", fake_run_episode)

    @dataclass
    class FakeEnv:
        wiki: str = "POLICY"

    cfg = ReliabilityConfig(arg_validation=True, dup_write_guard=True, verify_before_final=True)
    agent = agent_mod.build_magi_tau_agent(runner_factory=lambda **k: None, reliability=cfg)
    result = agent.solve(FakeEnv(), task_index=0, max_num_steps=5)

    assert result.reward == 1.0
    assert captured["tools_reliability"] is cfg
    assert captured["ep_reliability"] is cfg
    assert captured["tools_ledger"] is not None
    assert captured["tools_ledger"] is captured["ep_ledger"]  # SAME shared ledger


def test_solve_defaults_reliability_off(monkeypatch) -> None:
    _install_fake_tau_bench(monkeypatch)
    from magi_agent.benchmarks.taubench import agent as agent_mod
    from magi_agent.benchmarks.taubench.episode import EpisodeResult

    captured: dict[str, object] = {}

    def fake_build_tools(env, *, state, action_factory, reliability=None, ledger=None):
        captured["reliability"] = reliability
        return []

    def fake_run_episode(env, task_index, **kw):
        captured["ep_reliability"] = kw.get("reliability")
        return EpisodeResult(reward=0.0, done=False, turns=0)

    monkeypatch.setattr(agent_mod, "build_env_function_tools", fake_build_tools)
    monkeypatch.setattr(agent_mod, "run_episode", fake_run_episode)

    @dataclass
    class FakeEnv:
        wiki: str = "POLICY"

    agent = agent_mod.build_magi_tau_agent(runner_factory=lambda **k: None)
    agent.solve(FakeEnv(), task_index=0)
    assert captured["reliability"] is None
    assert captured["ep_reliability"] is None
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run --extra dev --extra cli pytest tests/benchmarks/taubench/test_agent.py -q`
Expected: FAIL — `build_magi_tau_agent() got an unexpected keyword argument 'reliability'`.

- [ ] **Step 3: Update `agent.py`**

Replace `build_magi_tau_agent` in `magi_agent/benchmarks/taubench/agent.py`:

```python
def build_magi_tau_agent(
    *,
    runner_factory: Callable[..., Any],
    reliability: Any = None,
) -> Any:
    """Construct a tau_bench Agent subclass bound to a magi runner_factory.

    runner_factory(*, instruction, tools) -> object with .run_async(...).
    reliability: optional ReliabilityConfig enabling the v2 driver-boundary
    levers (default None = all levers off). One WriteLedger is created per
    solve() and shared between the tool builders (L1/L3) and the episode loop
    (L2) so all levers see the same write history.
    """
    from tau_bench.agents.base import Agent  # noqa: PLC0415
    from tau_bench.types import RESPOND_ACTION_NAME, Action, SolveResult  # noqa: PLC0415

    from magi_agent.benchmarks.taubench.reliability import WriteLedger  # noqa: PLC0415

    class MagiTauAgent(Agent):
        def solve(
            self,
            env: Any,
            task_index: int | None = None,
            max_num_steps: int = 30,
        ) -> Any:
            state = EpisodeState()
            ledger = WriteLedger()
            tools = build_env_function_tools(
                env,
                state=state,
                action_factory=Action,
                reliability=reliability,
                ledger=ledger,
            )
            result = run_episode(
                env,
                task_index if task_index is not None else 0,
                state=state,
                runner_factory=runner_factory,
                action_factory=Action,
                respond_action_name=RESPOND_ACTION_NAME,
                max_steps=max_num_steps,
                instruction=env.wiki,
                tools=tools,
                reliability=reliability,
                ledger=ledger,
            )
            return SolveResult(
                reward=result.reward,
                info={"turns": result.turns, "infra_error": result.infra_error},
                messages=[],
                total_cost=0.0,
            )

    return MagiTauAgent()
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run --extra dev --extra cli pytest tests/benchmarks/taubench/test_agent.py -q`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add magi_agent/benchmarks/taubench/agent.py tests/benchmarks/taubench/test_agent.py
git commit -m "feat(taubench): thread reliability config + shared write ledger through agent"
```

---

### Task 7: Expose `reliability` on `run_eval` in `cli.py`

**Files:**
- Modify: `magi_agent/benchmarks/taubench/cli.py`
- Test: `tests/benchmarks/taubench/test_cli.py`

- [ ] **Step 1: Update the existing fake + add a forwarding test**

In `tests/benchmarks/taubench/test_cli.py`, update the existing
`test_run_eval_uses_tau_bench_task_split_keyword_without_live_calls` fake signature:

```python
    def fake_build_magi_tau_agent(*, runner_factory: object, reliability: object = None) -> object:
```

and at the end of that same test, after the `assert calls == [...]` block, add:

```python
    assert '"reliability": null' in capsys.readouterr().out
```

(Note: that test currently asserts on `capsys.readouterr().out` for `"config"`. Combine into ONE `readouterr()` call to avoid consuming the buffer twice — replace the final `assert '"config": "vanilla"' in capsys.readouterr().out` line with:)

```python
    out = capsys.readouterr().out
    assert '"config": "vanilla"' in out
    assert '"reliability": null' in out
```

Then append a new test:

```python
def test_run_eval_forwards_reliability_config(monkeypatch, capsys) -> None:
    from magi_agent.benchmarks.taubench import cli
    from magi_agent.benchmarks.taubench.reliability import ReliabilityConfig

    captured: dict[str, object] = {}

    class FakeEnv:
        tasks = [object()]

    def fake_get_env(domain, *, user_strategy, user_model, task_split,
                     user_provider=None, task_index=None):
        return FakeEnv()

    def fake_build_magi_tau_agent(*, runner_factory, reliability=None):
        captured["reliability"] = reliability

        class FakeAgent:
            def solve(self, env, task_index=None, max_num_steps=30):
                return SimpleNamespace(reward=1.0, info={"turns": 1, "infra_error": False})

        return FakeAgent()

    tau_bench = types.ModuleType("tau_bench")
    tau_bench_envs = types.ModuleType("tau_bench.envs")
    tau_bench_envs.get_env = fake_get_env
    fake_litellm = types.ModuleType("litellm")
    fake_agent_module = types.ModuleType("magi_agent.benchmarks.taubench.agent")
    fake_agent_module.build_magi_tau_agent = fake_build_magi_tau_agent
    fake_runner_module = types.ModuleType("magi_agent.cli.real_runner")
    fake_runner_module.build_cli_model_runner = lambda *a, **k: object()

    monkeypatch.setitem(sys.modules, "tau_bench", tau_bench)
    monkeypatch.setitem(sys.modules, "tau_bench.envs", tau_bench_envs)
    monkeypatch.setitem(sys.modules, "litellm", fake_litellm)
    monkeypatch.setitem(sys.modules, "magi_agent.benchmarks.taubench.agent", fake_agent_module)
    monkeypatch.setitem(sys.modules, "magi_agent.cli.real_runner", fake_runner_module)
    monkeypatch.setenv("MAGI_TAUBENCH_ENABLED", "1")

    cfg = ReliabilityConfig(arg_validation=True)
    cli.run_eval(domain="airline", max_tasks=1, trials=1, config="vanilla",
                 api_key="test-key", reliability=cfg)

    assert captured["reliability"] is cfg
    out = capsys.readouterr().out
    assert '"arg_validation": true' in out
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --extra dev --extra cli pytest tests/benchmarks/taubench/test_cli.py -q`
Expected: FAIL — `run_eval() got an unexpected keyword argument 'reliability'`.

- [ ] **Step 3: Update `cli.py`**

In `magi_agent/benchmarks/taubench/cli.py`, add the import near the top imports:

```python
from magi_agent.benchmarks.taubench.reliability import ReliabilityConfig
```

Add the `reliability` parameter to `run_eval` (append after `api_key`):

```python
def run_eval(
    *,
    domain: str = "airline",
    max_tasks: int | None = None,
    trials: int = 4,
    config: Config = "full",
    profile: str | None = None,
    model: str = DEFAULT_AGENT_MODEL,
    api_key: str | None = None,
    reliability: ReliabilityConfig | None = None,
) -> None:
```

Change the agent construction line from
`agent = build_magi_tau_agent(runner_factory=runner_factory)` to:

```python
                agent = build_magi_tau_agent(
                    runner_factory=runner_factory, reliability=reliability
                )
```

Change the `output` dict to include reliability:

```python
    output = {
        **report.model_dump(),
        "config": config,
        "domain": domain,
        "infra_error_count": infra_error_count,
        "reliability": reliability.model_dump() if reliability is not None else None,
    }
```

Add `ReliabilityConfig` to `__all__`:

```python
__all__ = [
    "DEFAULT_AGENT_MODEL",
    "GateDisabledError",
    "ReliabilityConfig",
    "ensure_enabled",
    "main",
    "run_eval",
]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --extra dev --extra cli pytest tests/benchmarks/taubench/test_cli.py -q`
Expected: PASS (existing tests still green + the new forwarding test).

- [ ] **Step 5: Commit**

```bash
git add magi_agent/benchmarks/taubench/cli.py tests/benchmarks/taubench/test_cli.py
git commit -m "feat(taubench): expose reliability config on run_eval + emit it in report"
```

---

### Task 8: Full-suite regression check

**Files:** none (verification only)

- [ ] **Step 1: Run the whole τ-bench test module**

Run: `uv run --extra dev --extra cli pytest tests/benchmarks/taubench/ -q`
Expected: PASS — all reliability, tau_env, episode, agent, cli, config, scorer, harness tests green. If `google` import errors appear, re-run with `--extra providers` added.

- [ ] **Step 2: Confirm default-off behavior is unchanged**

Confirm the pre-existing tests (those NOT passing a `reliability=` arg) still pass unchanged — this is the byte-identical-when-off guarantee. No code change; if any pre-existing test regressed, the lever gating in Task 4/5 is wrong (a lever ran while its flag was off) and must be fixed before proceeding.

---

## Measurement (after all tasks land — operator-run, NOT part of TDD)

Run from the worktree with the gate + keys set (Anthropic via `ANTHROPIC_API_KEY`, OpenAI user-sim key in env). Use `screen -dmS` + `caffeinate -i` for the long runs (per prior live-run practice).

**Quick subset first** (airline 50×1):

```python
# vanilla-clean baseline
run_eval(domain="airline", max_tasks=50, trials=1, config="vanilla", model="claude-sonnet-4-5")
# v2 (all three levers on)
run_eval(domain="airline", max_tasks=50, trials=1, config="vanilla",
         model="claude-sonnet-4-5",
         reliability=ReliabilityConfig(arg_validation=True, dup_write_guard=True, verify_before_final=True))
```

Compare pass^1 + avg_reward. If v2 shows a meaningful lift, run full **airline 50×4** vanilla vs v2, then per-lever ablation (one lever on at a time). Report absolute scores + deltas honestly; the levers are general, so a lift implies a general reliability improvement, not airline overfit.

Delete `/tmp/oai_key` after the measurement run — it holds the prod platform OpenAI key.

---

## Self-Review notes (completed by plan author)

- **Spec coverage:** L1→Task 4, L2→Task 5, L3→Task 4, `reliability.py`→Tasks 1-3, threading→Tasks 6-7, default-off guarantee→Task 8, measurement plan→Measurement section. All spec sections mapped.
- **Type consistency:** `ReliabilityConfig(arg_validation, dup_write_guard, verify_before_final)`, `validate_args(parameters, arguments)`, `WriteLedger.is_write/record/is_repeat_write/had_successful_write/last_write_errored`, `looks_like_error(observation)`, `verify_final(ledger, agent_text)` — names identical across all tasks and call sites.
- **No placeholders:** every code step has complete code; every run step has an exact command + expected result.
