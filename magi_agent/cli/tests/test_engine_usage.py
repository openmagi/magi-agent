"""Usage/cost honesty: EngineResult.usage populated from ADK usage_metadata.

Sync tests driving async via ``asyncio.run(...)`` (package convention; see
``test_engine.py``). Heavy ``google.adk``/``google.genai`` imports are allowed in
test modules — the import-cleanliness invariant only constrains ``cli/engine.py``
at module load (``test_engine.py::test_engine_import_clean_in_fresh_interpreter``).
"""

from __future__ import annotations

import asyncio

import pytest

from magi_agent.cli.contracts import EngineResult, Terminal
from magi_agent.cli.engine import MagiEngineDriver
from magi_agent.cli.headless import drain
from magi_agent.runtime.output_continuation import OutputContinuationConfig

from google.adk.events import Event  # noqa: E402
from google.genai import types  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers (real ADK objects)
# ---------------------------------------------------------------------------
def _usage_md(
    prompt: int | None = None,
    candidates: int | None = None,
    cached: int | None = None,
    total: int | None = None,
) -> object:
    return types.GenerateContentResponseUsageMetadata(
        prompt_token_count=prompt,
        candidates_token_count=candidates,
        cached_content_token_count=cached,
        total_token_count=total,
    )


def _ev_with_usage(md: object) -> Event:
    return Event(
        author="model",
        content=types.Content(role="model", parts=[types.Part(text="x")]),
        usage_metadata=md,
    )


def _final_event(
    text: str = "done",
    *,
    prompt: int | None = None,
    candidates: int | None = None,
    cached: int | None = None,
    total: int | None = None,
) -> Event:
    return Event(
        author="model",
        partial=False,
        turn_complete=True,
        content=types.Content(role="model", parts=[types.Part(text=text)]),
        usage_metadata=_usage_md(prompt, candidates, cached, total),
    )


def _text_event(text: str, *, partial: bool = True, turn_complete: bool = False) -> Event:
    return Event(
        author="model",
        partial=partial,
        turn_complete=turn_complete,
        content=types.Content(role="model", parts=[types.Part(text=text)]),
    )


class MockRunner:
    def __init__(self, events: list[Event]) -> None:
        self._events = events

    async def run_async(self, **_kwargs: object):
        for event in self._events:
            yield event


def _turn_input(session_id: str, turn_id: str = "turn-1", prompt: str = "go") -> dict:
    return {"prompt": prompt, "session_id": session_id, "turn_id": turn_id}


# ---------------------------------------------------------------------------
# T1 — _adk_usage_metadata extractor (helper unit, import-clean)
# ---------------------------------------------------------------------------
def test_adk_usage_metadata_helper_aliases_and_nesting() -> None:
    from magi_agent.cli.engine import _adk_usage_metadata

    # snake_case real ADK type; no total_token_count -> total omitted (no fabrication)
    assert _adk_usage_metadata(
        _ev_with_usage(_usage_md(prompt=100, candidates=23, cached=5))
    ) == {"input_tokens": 100, "output_tokens": 23, "cache_read_tokens": 5}

    # provider total_token_count taken verbatim
    assert _adk_usage_metadata(
        _ev_with_usage(_usage_md(prompt=10, candidates=2, total=12))
    ) == {"input_tokens": 10, "output_tokens": 2, "total_tokens": 12}

    # camelCase aliases via a duck-typed object
    class _CamelMeta:
        promptTokenCount = 7
        candidatesTokenCount = 3

    class _CamelEv:
        usageMetadata = _CamelMeta()

    assert _adk_usage_metadata(_CamelEv()) == {"input_tokens": 7, "output_tokens": 3}

    # nested descent into llm_response
    class _Nested:
        usage_metadata = None
        llm_response = _CamelEv()

    assert _adk_usage_metadata(_Nested()) == {"input_tokens": 7, "output_tokens": 3}

    # absent usage anywhere -> None
    assert _adk_usage_metadata(Event(author="model")) is None

    # over-depth (nested deeper than depth>3) -> None
    deep = {"response": {"response": {"response": {"response": {"usage_metadata": _CamelMeta()}}}}}
    assert _adk_usage_metadata(deep) is None

    # zero/missing counts are omitted, not emitted as 0
    assert _adk_usage_metadata(_ev_with_usage(_usage_md(prompt=0, candidates=4))) == {
        "output_tokens": 4
    }


# ---------------------------------------------------------------------------
# T2 — _fold_usage summing helper
# ---------------------------------------------------------------------------
def test_fold_usage_sums_keys() -> None:
    from magi_agent.cli.engine import _fold_usage

    turn: dict[str, object] = {}
    _fold_usage(turn, {"input_tokens": 100, "output_tokens": 50})
    _fold_usage(turn, {"input_tokens": 80, "output_tokens": 40, "total_tokens": 120})
    assert turn == {"input_tokens": 180, "output_tokens": 90, "total_tokens": 120}


# ---------------------------------------------------------------------------
# T3 — main event loop populates usage (and stays empty when absent)
# ---------------------------------------------------------------------------
def test_usage_populated_from_adk_usage_metadata() -> None:
    runner = MockRunner(
        [_text_event("hi "), _final_event("done", prompt=100, candidates=23, cached=5)]
    )
    driver = MagiEngineDriver(runner=runner)
    _events, terminal = asyncio.run(
        drain(driver.run_turn_stream(None, _turn_input("s-usage"), cancel=asyncio.Event()))
    )
    assert terminal.usage.get("input_tokens") == 100
    assert terminal.usage.get("output_tokens") == 23
    assert terminal.usage.get("cache_read_tokens") == 5


def test_usage_empty_when_metadata_absent() -> None:
    runner = MockRunner([_text_event("hi "), _text_event("done", partial=False, turn_complete=True)])
    driver = MagiEngineDriver(runner=runner)
    _events, terminal = asyncio.run(
        drain(driver.run_turn_stream(None, _turn_input("s-empty"), cancel=asyncio.Event()))
    )
    assert terminal.usage == {}


# ---------------------------------------------------------------------------
# T4 — usage SUMS across re-invocations (output-continuation), not last-wins
# ---------------------------------------------------------------------------
def _finish_event(text: str, finish: object, *, prompt: int, candidates: int) -> Event:
    return Event(
        author="model",
        partial=False,
        content=types.Content(role="model", parts=[types.Part(text=text)]),
        finish_reason=finish,
        usage_metadata=_usage_md(prompt=prompt, candidates=candidates),
    )


class _TruncateThenCompleteUsageRunner:
    """Attempt 1 truncates (MAX_TOKENS) with usage 100/50; attempt 2 completes
    (STOP) with usage 80/40. A correct fold SUMS to 180/90."""

    def __init__(self) -> None:
        self.invocations = 0

    async def run_async(self, **_kwargs: object):
        self.invocations += 1
        if self.invocations == 1:
            yield _text_event("Part one ", partial=True)
            yield _finish_event("Part one", types.FinishReason.MAX_TOKENS, prompt=100, candidates=50)
        else:
            yield _text_event("Part two ", partial=True)
            yield _finish_event("Part two", types.FinishReason.STOP, prompt=80, candidates=40)


def test_usage_summed_across_continuation_attempts() -> None:
    runner = _TruncateThenCompleteUsageRunner()
    driver = MagiEngineDriver(
        runner=runner,
        output_continuation=OutputContinuationConfig(enabled=True, max_continuations=4),
    )
    _events, terminal = asyncio.run(
        drain(driver.run_turn_stream(None, _turn_input("s-cont-usage"), cancel=asyncio.Event()))
    )
    assert runner.invocations == 2
    # SUM across the two real run_async invocations — NOT the last attempt's 80/40.
    assert terminal.usage.get("input_tokens") == 180
    assert terminal.usage.get("output_tokens") == 90


# ---------------------------------------------------------------------------
# T7 — aborted terminal carries the partial usage accumulated before cancel
# ---------------------------------------------------------------------------
def _call_event_with_usage(
    name: str, args: dict, call_id: str, *, prompt: int, candidates: int
) -> Event:
    return Event(
        author="model",
        content=types.Content(
            role="model",
            parts=[types.Part(function_call=types.FunctionCall(name=name, args=args, id=call_id))],
        ),
        usage_metadata=_usage_md(prompt=prompt, candidates=candidates),
    )


class _UsageThenCancelRunner:
    """Yields one usage-bearing event, then sets ``cancel`` and blocks — so the
    engine folds the partial usage before the cancel breaks the loop."""

    def __init__(self, cancel: asyncio.Event, usage_event: Event) -> None:
        self._cancel = cancel
        self._usage_event = usage_event
        self._gate = asyncio.Event()

    async def run_async(self, **_kwargs: object):
        yield self._usage_event
        self._cancel.set()
        await self._gate.wait()  # never released; cancel breaks the consumer loop
        yield _text_event("never")  # pragma: no cover


def test_aborted_terminal_carries_partial_usage() -> None:
    cancel = asyncio.Event()
    usage_ev = _call_event_with_usage("Bash", {"cmd": "x"}, "c1", prompt=40, candidates=10)
    driver = MagiEngineDriver(runner=_UsageThenCancelRunner(cancel, usage_ev))
    _events, terminal = asyncio.run(
        drain(driver.run_turn_stream(None, _turn_input("s-cancel-usage"), cancel=cancel))
    )
    assert isinstance(terminal, EngineResult)
    assert terminal.terminal is Terminal.aborted
    assert terminal.usage.get("input_tokens") == 40
    assert terminal.usage.get("output_tokens") == 10


# ---------------------------------------------------------------------------
# T5 — zero-edit guard loop tokens are folded into the turn total
# ---------------------------------------------------------------------------
class _ZeroEditUsageRunner:
    """Invocation 1 (main) describes an edit but makes no file-edit tool calls,
    so the zero-edit guard fires a 2nd invocation. Both carry usage."""

    def __init__(self) -> None:
        self.invocations = 0

    async def run_async(self, **_kwargs: object):
        self.invocations += 1
        if self.invocations == 1:
            yield _text_event("I would edit the file ", partial=True)
            yield _final_event("described only", prompt=100, candidates=50)
        else:
            yield _text_event("applying ", partial=True)
            yield _final_event("done", prompt=30, candidates=20)


def test_usage_zero_edit_guard_tokens_counted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAGI_EVAL_ZERO_EDIT_GUARD_ENABLED", "1")
    runner = _ZeroEditUsageRunner()
    driver = MagiEngineDriver(runner=runner)
    _events, terminal = asyncio.run(
        drain(driver.run_turn_stream(None, _turn_input("s-zeroedit"), cancel=asyncio.Event()))
    )
    assert runner.invocations == 2  # the guard fired a genuine second invocation
    assert terminal.usage.get("input_tokens") == 130  # 100 (main) + 30 (guard)
    assert terminal.usage.get("output_tokens") == 70  # 50 (main) + 20 (guard)


# ---------------------------------------------------------------------------
# T6 — coding-repair loop tokens are folded into the turn total
# ---------------------------------------------------------------------------
class _TwoInvocationUsageRunner:
    def __init__(self, first: tuple[int, int], second: tuple[int, int]) -> None:
        self._first = first
        self._second = second
        self.invocations = 0

    async def run_async(self, **_kwargs: object):
        self.invocations += 1
        prompt, candidates = self._first if self.invocations == 1 else self._second
        yield _text_event("chunk ", partial=True)
        yield _final_event("done", prompt=prompt, candidates=candidates)


def test_usage_includes_coding_repair_loop_tokens(monkeypatch: pytest.MonkeyPatch) -> None:
    import magi_agent.cli.engine as engine_mod

    # Force the default-ON-locally repair loop on deterministically.
    monkeypatch.setattr(engine_mod, "_coding_repair_loop_enabled", lambda: True)
    monkeypatch.setattr(engine_mod, "_coding_repair_max_attempts", lambda _policy: 2)

    runner = _TwoInvocationUsageRunner((100, 50), (25, 15))
    driver = MagiEngineDriver(runner=runner)

    # Pre-final gate blocks once (triggering one repair re-invocation), then clears.
    calls = {"n": 0}

    def _fake_gate(**_kwargs: object) -> dict[str, object] | None:
        calls["n"] += 1
        if calls["n"] == 1:
            return {
                "type": "pre_final_evidence_gate",
                "decision": "block",
                "repairDecision": {"action": "continue_repair"},
                "repairPolicy": {},
                "missingEvidence": [],
                "missingValidators": [],
            }
        return None

    monkeypatch.setattr(driver, "_pre_final_gate_payload", _fake_gate)

    _events, terminal = asyncio.run(
        drain(driver.run_turn_stream(None, _turn_input("s-repair"), cancel=asyncio.Event()))
    )
    assert runner.invocations == 2  # main + one repair re-invocation
    assert terminal.usage.get("input_tokens") == 125  # 100 (main) + 25 (repair)
    assert terminal.usage.get("output_tokens") == 65  # 50 (main) + 15 (repair)
