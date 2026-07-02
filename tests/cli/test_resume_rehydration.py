"""PR-04-PR2: wire ``--resume``/``--continue`` rehydration into the engine.

The resume reconstruction machine (``session_log.prepare_resume`` ->
``ResumeContext.initial_messages``) and the engine's end-to-end
``initial_messages`` seam were both already built, but the engine ``_drive``
DISCARDED ``initial_messages`` (``_ = initial_messages``). These tests pin the
newly-activated consumption: when a resumed turn carries prior
user/assistant messages, the engine prepends a synthesized transcript to the
opening user content the runner sees, so the model actually replays the prior
conversation.

Default-OFF / no-op invariant: with ``initial_messages=[]`` (no session log /
fresh session) the runner content is byte-identical to pre-PR2 behavior.
"""

from __future__ import annotations

import asyncio

import pytest

from magi_agent.cli.contracts import EngineResult, RuntimeEvent  # noqa: F401
from magi_agent.cli.engine import MagiEngineDriver
from magi_agent.cli.headless import drain
from magi_agent.cli.session_log import SessionLog, prepare_resume

# Heavy ADK imports are allowed in the TEST module (not in engine.py).
from google.adk.events import Event  # noqa: E402
from google.genai import types  # noqa: E402


def _text_event(text: str, *, partial: bool = True) -> Event:
    return Event(
        author="model",
        partial=partial,
        content=types.Content(role="model", parts=[types.Part(text=text)]),
    )


class CapturingRunner:
    """Records the ``new_message`` Content it is handed, then yields a reply.

    ``OpenMagiRunnerAdapter`` calls ``runner.run_async(new_message=..., ...)``,
    so capturing that kwarg lets us assert exactly what the engine fed the model.
    """

    def __init__(self) -> None:
        self.seen_text: str | None = None

    async def run_async(self, **kwargs: object):
        new_message = kwargs.get("new_message")
        parts = getattr(new_message, "parts", None) or []
        texts = [getattr(p, "text", None) or "" for p in parts]
        self.seen_text = "".join(texts)
        yield _text_event("ok")


def _turn_input(
    session_id: str,
    *,
    prompt: str = "what did I just ask?",
    initial_messages: list[dict[str, str]] | None = None,
) -> dict:
    ti: dict = {"prompt": prompt, "session_id": session_id, "turn_id": "turn-1"}
    if initial_messages is not None:
        ti["initial_messages"] = initial_messages
    return ti


# ---------------------------------------------------------------------------
# 1. initial_messages are prepended into the runner content (consumption).
# ---------------------------------------------------------------------------
def test_initial_messages_are_prepended_to_runner_content() -> None:
    runner = CapturingRunner()
    driver = MagiEngineDriver(runner=runner)
    prior = [
        {"role": "user", "content": "remember the magic word ZEBRA"},
        {"role": "assistant", "content": "Got it, the magic word is ZEBRA."},
    ]
    asyncio.run(
        drain(
            driver.run_turn_stream(
                None,
                _turn_input("s-resume", initial_messages=prior),
                cancel=asyncio.Event(),
            )
        )
    )
    assert runner.seen_text is not None
    # Both prior turns must appear in the content the model sees.
    assert "remember the magic word ZEBRA" in runner.seen_text
    assert "Got it, the magic word is ZEBRA." in runner.seen_text
    # The current prompt is still present and comes AFTER the prior transcript.
    assert "what did I just ask?" in runner.seen_text
    assert runner.seen_text.index(
        "remember the magic word ZEBRA"
    ) < runner.seen_text.index("what did I just ask?")


# ---------------------------------------------------------------------------
# 2. No initial_messages -> byte-identical opening content (no-op invariant).
# ---------------------------------------------------------------------------
def test_no_initial_messages_is_byte_identical() -> None:
    runner = CapturingRunner()
    driver = MagiEngineDriver(runner=runner)
    asyncio.run(
        drain(
            driver.run_turn_stream(
                None,
                _turn_input("s-fresh", prompt="hello"),
                cancel=asyncio.Event(),
            )
        )
    )
    assert runner.seen_text == "hello"

    # Explicit empty list behaves the same as the absent key.
    runner2 = CapturingRunner()
    driver2 = MagiEngineDriver(runner=runner2)
    asyncio.run(
        drain(
            driver2.run_turn_stream(
                None,
                _turn_input("s-fresh2", prompt="hello", initial_messages=[]),
                cancel=asyncio.Event(),
            )
        )
    )
    assert runner2.seen_text == "hello"


# ---------------------------------------------------------------------------
# 3. End-to-end round trip: write a session log -> prepare_resume -> engine.
# ---------------------------------------------------------------------------
def test_resume_roundtrip_from_written_log(tmp_path) -> None:
    # Write a prior turn's transcript exactly like the PR1 drain tap does.
    log = SessionLog(session_id="round-trip", cwd=str(tmp_path))
    log.append(
        RuntimeEvent(
            type="status",
            payload={"type": "user_message", "content": "what is 2 plus 2?"},
            turn_id="t0",
        )
    )
    log.append(
        RuntimeEvent(
            type="token",
            payload={"type": "text_delta", "delta": "The answer is 4."},
            turn_id="t0",
        )
    )
    log.close()

    class _Args:
        resume = "round-trip"
        continue_ = False
        bot_id = ""
        cwd = str(tmp_path)

    ctx = prepare_resume(_Args())
    assert ctx.initial_messages, "resume must reconstruct the prior turn"

    runner = CapturingRunner()
    driver = MagiEngineDriver(runner=runner)
    asyncio.run(
        drain(
            driver.run_turn_stream(
                None,
                _turn_input("round-trip", initial_messages=ctx.initial_messages),
                cancel=asyncio.Event(),
            )
        )
    )
    assert runner.seen_text is not None
    assert "what is 2 plus 2?" in runner.seen_text
    assert "The answer is 4." in runner.seen_text


# ---------------------------------------------------------------------------
# 4. --continue picks the latest session; no log -> graceful empty context.
# ---------------------------------------------------------------------------
def test_continue_latest_selects_newest_session(tmp_path) -> None:
    older = SessionLog(session_id="older", cwd=str(tmp_path))
    older.append(
        RuntimeEvent(
            type="status",
            payload={"type": "user_message", "content": "old question"},
            turn_id="t0",
        )
    )
    older.close()
    newer = SessionLog(session_id="newer", cwd=str(tmp_path))
    newer.append(
        RuntimeEvent(
            type="status",
            payload={"type": "user_message", "content": "new question"},
            turn_id="t0",
        )
    )
    newer.close()

    class _Args:
        resume = None
        continue_ = True
        bot_id = ""
        cwd = str(tmp_path)

    ctx = prepare_resume(_Args())
    assert ctx.session_id == "newer"
    joined = " ".join(m["content"] for m in ctx.initial_messages)
    assert "new question" in joined


def test_no_session_log_is_graceful_empty(tmp_path) -> None:
    class _Args:
        resume = None
        continue_ = True
        bot_id = ""
        cwd = str(tmp_path)

    ctx = prepare_resume(_Args())
    assert ctx.initial_messages == []
    assert ctx.reason == "no_session_to_continue"

    # Feeding the empty context to the engine is a no-op (prompt only).
    runner = CapturingRunner()
    driver = MagiEngineDriver(runner=runner)
    asyncio.run(
        drain(
            driver.run_turn_stream(
                None,
                _turn_input(
                    "s-empty",
                    prompt="brand new",
                    initial_messages=ctx.initial_messages,
                ),
                cancel=asyncio.Event(),
            )
        )
    )
    assert runner.seen_text == "brand new"


# ---------------------------------------------------------------------------
# 5. app.py wiring: --resume threads ResumeContext.initial_messages into
#    run_headless when the gate is ON, and stays empty when OFF.
# ---------------------------------------------------------------------------
def _write_fixture_log(session_id: str) -> None:
    """Write a one-turn transcript at the CLI's resolved (cwd, session_id)."""
    import os as _os

    log = SessionLog(session_id=session_id, cwd=_os.getcwd())
    log.append(
        RuntimeEvent(
            type="status",
            payload={"type": "user_message", "content": "earlier question"},
            turn_id="t0",
        )
    )
    log.append(
        RuntimeEvent(
            type="token",
            payload={"type": "text_delta", "delta": "earlier answer"},
            turn_id="t0",
        )
    )
    log.close()


def test_app_resume_gate_on_threads_initial_messages(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    from unittest.mock import patch

    from typer.testing import CliRunner

    from magi_agent.cli.app import app

    monkeypatch.setenv("MAGI_CLI_ENABLED", "1")
    monkeypatch.setenv("MAGI_CLI_RESUME_ENABLED", "1")
    monkeypatch.setenv("MAGI_CLI_SESSION_DIR", str(tmp_path))

    _write_fixture_log("appsess")

    captured: dict[str, object] = {}

    async def fake_headless(prompt, *, initial_messages=None, **_kw):
        captured["initial_messages"] = initial_messages
        return 0

    cli = CliRunner()
    with patch("magi_agent.cli.app.run_headless", fake_headless):
        cli.invoke(
            app,
            ["-p", "follow up", "--resume", "appsess"],
            catch_exceptions=False,
        )

    msgs = captured.get("initial_messages")
    assert msgs, f"resume gate ON must thread initial_messages, got {msgs!r}"
    joined = " ".join(m["content"] for m in msgs)  # type: ignore[union-attr]
    assert "earlier question" in joined
    assert "earlier answer" in joined


def test_app_resume_explicit_opt_out_is_empty(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    from unittest.mock import patch

    from typer.testing import CliRunner

    from magi_agent.cli.app import app

    monkeypatch.setenv("MAGI_CLI_ENABLED", "1")
    # C2 (N-15): the local-full overlay now setdefaults RESUME=1, so an explicit
    # opt-out (not a delenv) is what keeps --resume in the legacy id-only mode.
    monkeypatch.setenv("MAGI_CLI_RESUME_ENABLED", "0")
    monkeypatch.setenv("MAGI_CLI_SESSION_DIR", str(tmp_path))

    _write_fixture_log("appsess2")

    captured: dict[str, object] = {}

    async def fake_headless(prompt, *, initial_messages=None, **_kw):
        captured["initial_messages"] = initial_messages
        return 0

    cli = CliRunner()
    with patch("magi_agent.cli.app.run_headless", fake_headless):
        cli.invoke(
            app,
            ["-p", "follow up", "--resume", "appsess2"],
            catch_exceptions=False,
        )

    # Explicit =0: --resume threads the id only (legacy), no prior messages.
    assert captured.get("initial_messages") == []


def test_app_resume_default_on_under_local_full_profile(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """ON-path e2e: fresh local-full install (no resume env set) rehydrates.

    Drives overlay -> gate -> prepare_resume -> initial_messages with only
    run_headless faked, so the resume default-ON path is proven end to end.
    """
    from unittest.mock import patch

    from typer.testing import CliRunner

    from magi_agent.cli.app import app

    monkeypatch.setenv("MAGI_CLI_ENABLED", "1")
    monkeypatch.setenv("MAGI_CLI_SESSION_DIR", str(tmp_path))
    # Fresh install: nothing about resume or the profile is set in the env.
    monkeypatch.delenv("MAGI_CLI_RESUME_ENABLED", raising=False)
    monkeypatch.delenv("MAGI_RUNTIME_PROFILE", raising=False)
    monkeypatch.delenv("MAGI_AGENT_LOCAL_FULL_RUNTIME_DEFAULTS", raising=False)

    _write_fixture_log("appsess3")

    captured: dict[str, object] = {}

    async def fake_headless(prompt, *, initial_messages=None, **_kw):
        captured["initial_messages"] = initial_messages
        return 0

    cli = CliRunner()
    with patch("magi_agent.cli.app.run_headless", fake_headless):
        cli.invoke(
            app,
            ["-p", "follow up", "--resume", "appsess3"],
            catch_exceptions=False,
        )

    msgs = captured.get("initial_messages")
    assert msgs, f"local-full default ON must rehydrate initial_messages, got {msgs!r}"
    joined = " ".join(m["content"] for m in msgs)  # type: ignore[union-attr]
    assert "earlier question" in joined
    assert "earlier answer" in joined
