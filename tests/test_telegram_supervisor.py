from __future__ import annotations


class _FakeProvider:
    def __init__(self, token: str) -> None:
        self.token = token
        self.closed = False

    def close(self) -> None:
        self.closed = True


def _make_supervisor(tokens: list[str | None]):
    """Build a supervisor whose resolve_token yields successive values."""
    from magi_agent.gateway.channel_watchers import TelegramSupervisor

    box = {"i": 0}

    def resolve_token() -> str | None:
        i = box["i"]
        value = tokens[min(i, len(tokens) - 1)]
        box["i"] = i + 1
        return value

    built: list[_FakeProvider] = []

    def provider_factory(token: str) -> _FakeProvider:
        provider = _FakeProvider(token)
        built.append(provider)
        return provider

    polls: list[str] = []

    def poll_once_factory(provider: _FakeProvider):
        def poll_once() -> None:
            polls.append(provider.token)

        return poll_once

    sup = TelegramSupervisor(
        resolve_token=resolve_token,
        provider_factory=provider_factory,
        poll_once_factory=poll_once_factory,
    )
    return sup, built, polls


def test_idle_when_no_token() -> None:
    sup, built, polls = _make_supervisor([None])

    assert sup.tick() == "idle"
    assert built == []
    assert polls == []


def test_builds_provider_and_polls_when_token_appears() -> None:
    sup, built, polls = _make_supervisor([None, "tok-1"])

    assert sup.tick() == "idle"
    assert sup.tick() == "polled"
    assert [p.token for p in built] == ["tok-1"]
    assert polls == ["tok-1"]


def test_reuses_provider_when_token_unchanged() -> None:
    sup, built, polls = _make_supervisor(["tok-1", "tok-1"])

    sup.tick()
    sup.tick()

    assert len(built) == 1
    assert polls == ["tok-1", "tok-1"]


def test_rebuilds_and_closes_old_provider_when_token_changes() -> None:
    sup, built, polls = _make_supervisor(["tok-1", "tok-2"])

    sup.tick()
    sup.tick()

    assert [p.token for p in built] == ["tok-1", "tok-2"]
    assert built[0].closed is True
    assert polls == ["tok-1", "tok-2"]


def test_idles_and_closes_provider_when_token_removed() -> None:
    sup, built, polls = _make_supervisor(["tok-1", None])

    assert sup.tick() == "polled"
    assert sup.tick() == "idle"
    assert built[0].closed is True
    assert polls == ["tok-1"]


def test_dashboard_telegram_gate_default_off(monkeypatch) -> None:
    from magi_agent.gateway import channel_watchers

    monkeypatch.delenv("MAGI_DASHBOARD_TELEGRAM_ENABLED", raising=False)
    assert channel_watchers.is_dashboard_telegram_enabled() is False

    monkeypatch.setenv("MAGI_DASHBOARD_TELEGRAM_ENABLED", "1")
    assert channel_watchers.is_dashboard_telegram_enabled() is True


def test_default_watchers_adds_supervisor_when_dashboard_gate_on(monkeypatch) -> None:
    from magi_agent.gateway import watchers

    monkeypatch.setenv("MAGI_DASHBOARD_TELEGRAM_ENABLED", "1")
    monkeypatch.setenv("MAGI_SCHEDULER_EXECUTOR_ENABLED", "0")

    names = {w.name for w in watchers.build_default_watchers()}
    assert "channel_telegram" in names
