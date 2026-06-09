from __future__ import annotations

import pytest

from magi_agent.browser.autonomous.engine import (
    BrowserEngine,
    BrowserRunResult,
)


class _FakeHistory:
    """Mimics the duck-typed surface of browser_use AgentHistoryList."""

    def __init__(self, final: str | None, steps: int) -> None:
        self._final = final
        self._steps = steps

    def final_result(self) -> str | None:
        return self._final

    def number_of_steps(self) -> int:
        return self._steps


class _FakeAgent:
    def __init__(self, history: _FakeHistory) -> None:
        self._history = history
        self.max_steps_seen: int | None = None

    async def run(self, max_steps: int = 500) -> _FakeHistory:
        self.max_steps_seen = max_steps
        return self._history


@pytest.mark.asyncio
async def test_run_ok_extracts_summary_and_steps() -> None:
    captured: dict[str, object] = {}

    def factory(*, task, chat_model, on_step, profile_dir):
        captured["task"] = task
        captured["chat_model"] = chat_model
        captured["on_step"] = on_step
        captured["profile_dir"] = profile_dir
        return _FakeAgent(_FakeHistory("found the answer", 7))

    engine = BrowserEngine(agent_factory=factory)
    result = await engine.run(
        task="search for the capital of France",
        chat_model=object(),
        max_steps=12,
        profile_dir="/tmp/profile",
    )

    assert isinstance(result, BrowserRunResult)
    assert result.status == "ok"
    assert result.summary == "found the answer"
    assert result.steps_used == 7
    assert result.error_code is None
    # original task text is preserved in what was handed to the factory
    assert "search for the capital of France" in str(captured["task"])
    assert captured["profile_dir"] == "/tmp/profile"


@pytest.mark.asyncio
async def test_run_folds_start_url_into_task_when_allowed() -> None:
    captured: dict[str, object] = {}

    def factory(*, task, chat_model, on_step, profile_dir):
        captured["task"] = task
        return _FakeAgent(_FakeHistory("ok", 1))

    engine = BrowserEngine(agent_factory=factory)
    result = await engine.run(
        task="read the headline",
        chat_model=object(),
        max_steps=5,
        profile_dir="/tmp/p",
        start_url="https://example.com/",
    )

    assert result.status == "ok"
    task_text = str(captured["task"])
    assert "https://example.com/" in task_text
    assert "read the headline" in task_text


@pytest.mark.asyncio
async def test_run_blocked_start_url_never_builds_agent() -> None:
    def factory(*, task, chat_model, on_step, profile_dir):  # pragma: no cover
        raise AssertionError("factory must not be called for a blocked start_url")

    engine = BrowserEngine(agent_factory=factory)
    result = await engine.run(
        task="poke the metadata service",
        chat_model=object(),
        max_steps=3,
        profile_dir="/tmp/p",
        start_url="http://127.0.0.1/",
    )

    assert result.status == "blocked"
    assert result.error_code is not None
    assert result.error_code != ""


@pytest.mark.asyncio
async def test_run_agent_failure_returns_error() -> None:
    class _BoomAgent:
        async def run(self, max_steps: int = 500):
            raise RuntimeError("chromium exploded")

    def factory(*, task, chat_model, on_step, profile_dir):
        return _BoomAgent()

    engine = BrowserEngine(agent_factory=factory)
    result = await engine.run(
        task="do a thing",
        chat_model=object(),
        max_steps=3,
        profile_dir="/tmp/p",
    )

    assert result.status == "error"
    assert result.error_code == "browser_run_failed"
    assert "chromium exploded" in result.summary


@pytest.mark.asyncio
async def test_run_factory_exception_returns_error() -> None:
    def factory(*, task, chat_model, on_step, profile_dir):
        raise ValueError("could not build agent")

    engine = BrowserEngine(agent_factory=factory)
    result = await engine.run(
        task="t",
        chat_model=object(),
        max_steps=3,
        profile_dir="/tmp/p",
    )

    assert result.status == "error"
    assert result.error_code == "browser_run_failed"


@pytest.mark.asyncio
async def test_run_handles_dict_outcome() -> None:
    def factory(*, task, chat_model, on_step, profile_dir):
        class _DictAgent:
            async def run(self, max_steps: int = 500):
                return {"final": "dict answer", "steps": 4}

        return _DictAgent()

    engine = BrowserEngine(agent_factory=factory)
    result = await engine.run(
        task="t",
        chat_model=object(),
        max_steps=3,
        profile_dir="/tmp/p",
    )

    assert result.status == "ok"
    assert result.summary == "dict answer"
    assert result.steps_used == 4


@pytest.mark.asyncio
async def test_run_handles_none_final_result() -> None:
    def factory(*, task, chat_model, on_step, profile_dir):
        return _FakeAgent(_FakeHistory(None, 2))

    engine = BrowserEngine(agent_factory=factory)
    result = await engine.run(
        task="t",
        chat_model=object(),
        max_steps=3,
        profile_dir="/tmp/p",
    )

    assert result.status == "ok"
    assert result.summary == ""
    assert result.steps_used == 2


class _MidRunBlockAgent:
    """Fake agent that, during run(), invokes on_step with a BLOCKED url
    (mimicking a mid-run navigation that the SSRF guard must abort), then
    returns a normal AgentHistoryList-like outcome -- exactly the shape that
    previously masqueraded as status="ok".
    """

    def __init__(self, on_step, blocked_url: str, history: _FakeHistory) -> None:
        self._on_step = on_step
        self._blocked_url = blocked_url
        self._history = history

    async def run(self, max_steps: int = 500) -> _FakeHistory:
        # The real browser-use loop would arm the cooperative stop from this
        # return value and abort the same step; here we just fire the seam.
        self._on_step(self._blocked_url)
        return self._history


@pytest.mark.asyncio
async def test_run_mid_run_block_surfaces_as_blocked() -> None:
    def factory(*, task, chat_model, on_step, profile_dir):
        # Normal outcome (final_result/number_of_steps work) so the only thing
        # that can mark this run blocked is the recorded mid-run violation.
        return _MidRunBlockAgent(
            on_step,
            "http://169.254.169.254/",
            _FakeHistory("looks fine", 3),
        )

    engine = BrowserEngine(agent_factory=factory)
    result = await engine.run(
        task="exfiltrate cloud metadata",
        chat_model=object(),
        max_steps=5,
        profile_dir="/tmp/p",
    )

    assert result.status == "blocked"
    assert result.error_code is not None
    assert result.error_code != "ok"
    # the recorded reason is the SSRF reason for the blocked url
    from magi_agent.browser.autonomous.safety_hooks import navigation_block_reason

    assert result.error_code == navigation_block_reason("http://169.254.169.254/")


def test_on_step_guard_returns_block_reason_for_unsafe_url() -> None:
    # The guard handed to the factory is a plain url-in / reason-out callable.
    captured: dict[str, object] = {}

    def factory(*, task, chat_model, on_step, profile_dir):
        captured["on_step"] = on_step
        return _FakeAgent(_FakeHistory("ok", 1))

    engine = BrowserEngine(agent_factory=factory)

    import asyncio

    asyncio.run(
        engine.run(
            task="t",
            chat_model=object(),
            max_steps=1,
            profile_dir="/tmp/p",
        )
    )

    guard = captured["on_step"]
    assert callable(guard)
    assert guard("http://127.0.0.1/") is not None
    assert guard("https://example.com/") is None
