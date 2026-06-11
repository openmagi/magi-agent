"""Tests for the PR3.3 toast helpers in ``magi_agent.cli.tui.notify``.

Style mirrors the other TUI tests: SYNC functions driving the coroutine via
``asyncio.run`` with a nested ``async def _run`` that uses Textual's
``App.run_test()`` harness. No model is ever hit.

The toast helpers are thin, fail-safe wrappers over ``App.notify``: they forward
the message + a fixed severity, and a ``notify`` that itself raises is swallowed
(a failed toast must never crash a turn).
"""

from __future__ import annotations

import asyncio

from textual.app import App, ComposeResult
from textual.widgets import Static

from magi_agent.cli.tui import notify


class _Harness(App[None]):
    def __init__(self) -> None:
        super().__init__()
        self.calls: list[tuple[str, str]] = []

    def compose(self) -> ComposeResult:
        yield Static("x")

    def notify(  # noqa: A003 - shadow App.notify deliberately for the test
        self, message, *, title="", severity="information", timeout=None, markup=True
    ):
        self.calls.append((message, severity))


def test_notify_helpers_forward_message_and_severity() -> None:
    async def _run() -> None:
        app = _Harness()
        async with app.run_test() as pilot:
            await pilot.pause()
            notify.info(app, "all good")
            notify.warning(app, "careful")
            notify.error(app, "broke")
        assert ("all good", "information") in app.calls
        assert ("careful", "warning") in app.calls
        assert ("broke", "error") in app.calls

    asyncio.run(_run())


def test_notify_helpers_never_raise_when_notify_unavailable() -> None:
    async def _run() -> None:
        class _Broken(App[None]):
            def compose(self) -> ComposeResult:
                yield Static("x")

            def notify(self, *a, **k):  # noqa: A003
                raise RuntimeError("no notify here")

        app = _Broken()
        async with app.run_test() as pilot:
            await pilot.pause()
            # Must not propagate — notification failure is never fatal.
            notify.error(app, "still fine")

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# PR3.4 — focus-aware bell / desktop notify (gated by MAGI_TUI_NOTIFY_BELL)
# ---------------------------------------------------------------------------
def test_bell_enabled_respects_env(monkeypatch) -> None:
    monkeypatch.delenv(notify.BELL_ENV, raising=False)
    assert notify.bell_enabled() is False
    monkeypatch.setenv(notify.BELL_ENV, "1")
    assert notify.bell_enabled() is True
    monkeypatch.setenv(notify.BELL_ENV, "0")
    assert notify.bell_enabled() is False


def test_notify_attention_rings_only_when_enabled_and_unfocused(monkeypatch) -> None:
    async def _run() -> None:
        class _BellApp(App[None]):
            def __init__(self) -> None:
                super().__init__()
                self.bells = 0

            def compose(self) -> ComposeResult:
                yield Static("x")

            def bell(self) -> None:  # noqa: A003 - shadow App.bell for the test
                self.bells += 1

        # Enabled + unfocused -> rings.
        monkeypatch.setenv(notify.BELL_ENV, "1")
        app = _BellApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            notify.notify_attention(app, focused=False, reason="turn done")
            assert app.bells == 1
            # Focused -> no ring even when enabled.
            notify.notify_attention(app, focused=True, reason="turn done")
            assert app.bells == 1

        # Disabled (env unset) -> never rings even when unfocused.
        monkeypatch.delenv(notify.BELL_ENV, raising=False)
        app2 = _BellApp()
        async with app2.run_test() as pilot:
            await pilot.pause()
            notify.notify_attention(app2, focused=False, reason="turn done")
            assert app2.bells == 0

    asyncio.run(_run())


def test_notify_attention_never_raises_when_bell_unavailable(monkeypatch) -> None:
    async def _run() -> None:
        class _BrokenBell(App[None]):
            def compose(self) -> ComposeResult:
                yield Static("x")

            def bell(self) -> None:  # noqa: A003
                raise RuntimeError("no bell here")

        monkeypatch.setenv(notify.BELL_ENV, "1")
        app = _BrokenBell()
        async with app.run_test() as pilot:
            await pilot.pause()
            # A bell that raises must never crash a turn (fail-open).
            notify.notify_attention(app, focused=False, reason="turn done")

    asyncio.run(_run())
