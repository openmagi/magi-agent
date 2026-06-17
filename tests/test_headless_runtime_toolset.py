"""Tests for optional tools/memory_mode passthrough in build_headless_runtime.

Task 2A.3: build_headless_runtime and _build_default_runner now accept an
optional ``tools`` parameter.  When ``tools is None`` (the default) behavior is
byte-identical to before the patch — the full first-party toolset is built
normally.  When a caller explicitly passes ``tools`` (including ``[]``) that
list reaches ``build_cli_model_runner`` unchanged, enabling child-agent
privilege containment without a full custom runner.

``memory_mode`` was already threaded all the way through; these tests also
verify the forwarding remains intact.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_FAKE_PROVIDER = "anthropic"
_FAKE_MODEL = "claude-sonnet-4-5"
_FAKE_API_KEY = "sk-test-headless-toolset"


def _fake_provider_config() -> object:
    """Minimal ProviderConfig stand-in sufficient for the monkeypatch."""
    from magi_agent.cli.providers import ProviderConfig  # noqa: PLC0415

    return ProviderConfig(
        provider=_FAKE_PROVIDER,
        model=_FAKE_MODEL,
        api_key=_FAKE_API_KEY,
    )


class _FakeRunner:
    """Minimal runner stand-in returned by the monkeypatched build_cli_model_runner."""

    model_provider: str = _FAKE_PROVIDER
    model_label: str = _FAKE_MODEL
    general_automation_receipts: object = None
    runner_policy_assembly: object = None

    def __init__(self, *, tools: list[object] | None, memory_mode: str) -> None:
        self.tools_received = tools
        self.memory_mode_received = memory_mode

    # Composio attach reads runner.agent — provide a minimal stub.
    @property
    def agent(self) -> MagicMock:
        return MagicMock()


# ---------------------------------------------------------------------------
# Core forwarding tests
# ---------------------------------------------------------------------------


def test_tools_none_default_does_not_skip_first_party_build(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """When tools=None (default), _build_first_party_adk_tools is called normally."""
    built: dict[str, bool] = {"called": False}
    real_build = None

    from magi_agent.cli import wiring as wiring_mod  # noqa: PLC0415

    real_build = wiring_mod._build_first_party_adk_tools

    def spy_build(**kwargs: object) -> list[object]:  # type: ignore[misc]
        built["called"] = True
        return real_build(**kwargs)  # type: ignore[misc]

    monkeypatch.setattr(wiring_mod, "_build_first_party_adk_tools", spy_build)

    # Patch resolve_provider_config to return a fake config, and
    # build_cli_model_runner to capture its kwargs without needing google-adk.
    captured: dict[str, object] = {}

    def fake_resolve(*, model_override: object = None) -> object:
        return _fake_provider_config()

    def fake_build_runner(config: object, **kwargs: object) -> _FakeRunner:
        captured.update(kwargs)
        return _FakeRunner(
            tools=kwargs.get("tools"),  # type: ignore[arg-type]
            memory_mode=str(kwargs.get("memory_mode", "normal")),
        )

    monkeypatch.setattr("magi_agent.cli.wiring.build_headless_runtime.__module__", None, raising=False)
    monkeypatch.setattr(
        "magi_agent.cli.providers.resolve_provider_config",
        fake_resolve,
    )
    # Patch the name as imported inside _build_default_runner (lazy import).
    import magi_agent.cli.real_runner as rr  # noqa: PLC0415

    monkeypatch.setattr(rr, "build_cli_model_runner", fake_build_runner)

    # Also patch lazy-imported resolve inside _build_default_runner.
    import magi_agent.cli.providers as prov  # noqa: PLC0415

    monkeypatch.setattr(prov, "resolve_provider_config", fake_resolve)

    from magi_agent.cli.wiring import build_headless_runtime  # noqa: PLC0415

    # tools=None (default): spy should be called.
    build_headless_runtime(cwd=str(tmp_path))
    assert built["called"], "_build_first_party_adk_tools was not called when tools=None"


def test_explicit_empty_tools_skips_first_party_build(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """When tools=[], _build_first_party_adk_tools is NOT called and [] reaches build_cli_model_runner."""
    from magi_agent.cli import wiring as wiring_mod  # noqa: PLC0415

    spy_called: dict[str, bool] = {"called": False}

    def spy_build(**kwargs: object) -> list[object]:  # type: ignore[misc]
        spy_called["called"] = True
        return []

    monkeypatch.setattr(wiring_mod, "_build_first_party_adk_tools", spy_build)

    captured: dict[str, object] = {}

    def fake_resolve(*, model_override: object = None) -> object:
        return _fake_provider_config()

    def fake_build_runner(config: object, **kwargs: object) -> _FakeRunner:
        captured.update(kwargs)
        return _FakeRunner(
            tools=kwargs.get("tools"),  # type: ignore[arg-type]
            memory_mode=str(kwargs.get("memory_mode", "normal")),
        )

    import magi_agent.cli.real_runner as rr  # noqa: PLC0415
    import magi_agent.cli.providers as prov  # noqa: PLC0415

    monkeypatch.setattr(rr, "build_cli_model_runner", fake_build_runner)
    monkeypatch.setattr(prov, "resolve_provider_config", fake_resolve)

    from magi_agent.cli.wiring import build_headless_runtime  # noqa: PLC0415

    build_headless_runtime(cwd=str(tmp_path), tools=[])

    # The spy must NOT have been called — caller-supplied tools bypass the build.
    assert not spy_called["called"], "_build_first_party_adk_tools was called despite tools=[]"
    # The empty list must have reached build_cli_model_runner.
    assert captured.get("tools") == [], f"tools forwarded incorrectly: {captured.get('tools')!r}"


def test_explicit_tool_list_forwarded_to_build_cli_model_runner(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """A non-empty explicit tools list reaches build_cli_model_runner unchanged."""
    from magi_agent.cli import wiring as wiring_mod  # noqa: PLC0415

    class _FakeTool:
        name = "FakeChildTool"

    explicit_tools = [_FakeTool()]

    def spy_build(**kwargs: object) -> list[object]:  # type: ignore[misc]
        raise AssertionError("_build_first_party_adk_tools must NOT be called when tools is provided")

    monkeypatch.setattr(wiring_mod, "_build_first_party_adk_tools", spy_build)

    captured: dict[str, object] = {}

    def fake_resolve(*, model_override: object = None) -> object:
        return _fake_provider_config()

    def fake_build_runner(config: object, **kwargs: object) -> _FakeRunner:
        captured.update(kwargs)
        return _FakeRunner(
            tools=kwargs.get("tools"),  # type: ignore[arg-type]
            memory_mode=str(kwargs.get("memory_mode", "normal")),
        )

    import magi_agent.cli.real_runner as rr  # noqa: PLC0415
    import magi_agent.cli.providers as prov  # noqa: PLC0415

    monkeypatch.setattr(rr, "build_cli_model_runner", fake_build_runner)
    monkeypatch.setattr(prov, "resolve_provider_config", fake_resolve)

    from magi_agent.cli.wiring import build_headless_runtime  # noqa: PLC0415

    build_headless_runtime(cwd=str(tmp_path), tools=explicit_tools)

    assert captured.get("tools") is explicit_tools, (
        f"tools forwarded incorrectly: {captured.get('tools')!r}"
    )


def test_memory_mode_forwarded_to_build_cli_model_runner(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """memory_mode is forwarded through build_headless_runtime -> _build_default_runner -> build_cli_model_runner."""
    from magi_agent.cli import wiring as wiring_mod  # noqa: PLC0415

    captured: dict[str, object] = {}

    def fake_first_party(**kwargs: object) -> list[object]:  # type: ignore[misc]
        return []

    def fake_resolve(*, model_override: object = None) -> object:
        return _fake_provider_config()

    def fake_build_runner(config: object, **kwargs: object) -> _FakeRunner:
        captured.update(kwargs)
        return _FakeRunner(
            tools=kwargs.get("tools"),  # type: ignore[arg-type]
            memory_mode=str(kwargs.get("memory_mode", "normal")),
        )

    import magi_agent.cli.real_runner as rr  # noqa: PLC0415
    import magi_agent.cli.providers as prov  # noqa: PLC0415

    monkeypatch.setattr(wiring_mod, "_build_first_party_adk_tools", fake_first_party)
    monkeypatch.setattr(rr, "build_cli_model_runner", fake_build_runner)
    monkeypatch.setattr(prov, "resolve_provider_config", fake_resolve)

    from magi_agent.cli.wiring import build_headless_runtime  # noqa: PLC0415

    build_headless_runtime(cwd=str(tmp_path), memory_mode="incognito")

    assert captured.get("memory_mode") == "incognito", (
        f"memory_mode forwarded incorrectly: {captured.get('memory_mode')!r}"
    )


def test_tools_and_memory_mode_forwarded_together(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """Both tools=[] and memory_mode='incognito' reach build_cli_model_runner together."""
    from magi_agent.cli import wiring as wiring_mod  # noqa: PLC0415

    captured: dict[str, object] = {}

    def spy_build(**kwargs: object) -> list[object]:  # type: ignore[misc]
        raise AssertionError("_build_first_party_adk_tools must NOT be called when tools=[]")

    def fake_resolve(*, model_override: object = None) -> object:
        return _fake_provider_config()

    def fake_build_runner(config: object, **kwargs: object) -> _FakeRunner:
        captured.update(kwargs)
        return _FakeRunner(
            tools=kwargs.get("tools"),  # type: ignore[arg-type]
            memory_mode=str(kwargs.get("memory_mode", "normal")),
        )

    import magi_agent.cli.real_runner as rr  # noqa: PLC0415
    import magi_agent.cli.providers as prov  # noqa: PLC0415

    monkeypatch.setattr(wiring_mod, "_build_first_party_adk_tools", spy_build)
    monkeypatch.setattr(rr, "build_cli_model_runner", fake_build_runner)
    monkeypatch.setattr(prov, "resolve_provider_config", fake_resolve)

    from magi_agent.cli.wiring import build_headless_runtime  # noqa: PLC0415

    build_headless_runtime(cwd=str(tmp_path), tools=[], memory_mode="incognito")

    assert captured.get("tools") == [], f"tools: {captured.get('tools')!r}"
    assert captured.get("memory_mode") == "incognito", f"memory_mode: {captured.get('memory_mode')!r}"


def test_explicit_runner_param_bypasses_tools_param(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """When runner= is supplied directly, tools= is ignored (runner wins)."""
    from magi_agent.cli import wiring as wiring_mod  # noqa: PLC0415

    def spy_build(**kwargs: object) -> list[object]:  # type: ignore[misc]
        raise AssertionError("_build_first_party_adk_tools must NOT be called when runner= is supplied")

    monkeypatch.setattr(wiring_mod, "_build_first_party_adk_tools", spy_build)

    from magi_agent.cli.wiring import build_headless_runtime  # noqa: PLC0415

    # Pass an explicit runner — tools= should never reach _build_default_runner.
    runtime = build_headless_runtime(
        cwd=str(tmp_path),
        runner=object(),
        mode="plan",  # plan mode suppresses composio build
        tools=[],
    )
    # Just confirm we got a HeadlessRuntime back without errors.
    assert runtime is not None
