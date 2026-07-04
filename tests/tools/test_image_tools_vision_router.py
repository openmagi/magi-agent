"""Tests for the vision-sidecar routing override (MAGI_VISION_MODEL/PROVIDER).

HAL-style vision sidecar: ``image_understand`` can route its vision calls to a
dedicated model that is independent of the main orchestration model, so vision
quality does not degrade when the operator picks a cheap/text-tier main model.

Key contracts proven here:
1. Default-OFF: with both flags unset, behavior is byte-identical to before
   (same litellm call args, same metadata/output/transcriptOutput key sets).
2. Routed: ``MAGI_VISION_MODEL`` (+ optional ``MAGI_VISION_PROVIDER``) switches
   the litellm model/key; receipts (``visionRouted``/``visionModel``) appear.
3. Fail-soft ladder: routed-call failure retries on the main path
   (``visionFallback``); unresolvable overrides skip with a reason
   (``visionRouteSkipped``); the tool path never crashes.
4. ``resolve_vision_provider_config`` never raises and is env/config-injectable.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from magi_agent.tools.context import ToolContext

# Key-shaped fixtures assembled at runtime (GitHub push protection).
_FAKE_ANTHROPIC_KEY = "sk-ant-" + "test-" + "router-fixture"
_FAKE_GEMINI_KEY = "AIza" + "-test-" + "router-fixture"
_FAKE_OPENAI_KEY = "sk-" + "test-" + "openai-router-fixture"

_PROVIDER_KEY_ENV_VARS = (
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
    "FIREWORKS_API_KEY",
)


# ---------------------------------------------------------------------------
# Helpers (conventions from tests/tools/test_image_tools_vision.py)
# ---------------------------------------------------------------------------


def _make_litellm_response(text: str) -> object:
    resp = MagicMock()
    resp.choices = [MagicMock()]
    resp.choices[0].message.content = text
    return resp


def _ctx(tmp_path: Path, *, adk_ctx: object = None) -> ToolContext:
    return ToolContext(
        botId="test-bot",
        sessionId="test-session",
        turnId="test-turn",
        workspaceRoot=str(tmp_path),
        adk_tool_context=adk_ctx,
    )


def _write_png(tmp_path: Path, name: str = "img.png") -> Path:
    p = tmp_path / name
    p.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 60)
    return p


@pytest.fixture()
def clean_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> pytest.MonkeyPatch:
    """Hermetic env: no provider keys, no overrides, no real config file."""
    for name in _PROVIDER_KEY_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    for name in ("MAGI_VISION_MODEL", "MAGI_VISION_PROVIDER", "MAGI_PROVIDER", "MAGI_MODEL"):
        monkeypatch.delenv(name, raising=False)
    # Point the config file at a nonexistent path so ~/.magi/config.toml never leaks in.
    monkeypatch.setenv("MAGI_CONFIG", str(tmp_path / "nonexistent-config.toml"))
    return monkeypatch


def _capture_litellm(
    monkeypatch: pytest.MonkeyPatch, text: str = "vision says hi"
) -> list[dict[str, Any]]:
    captured: list[dict[str, Any]] = []

    def fake_completion(**kwargs: Any) -> object:
        captured.append(kwargs)
        return _make_litellm_response(text)

    monkeypatch.setattr("litellm.completion", fake_completion)
    return captured


# ---------------------------------------------------------------------------
# 1. Default-OFF — zero behavior change (the flag-unset guarantee)
# ---------------------------------------------------------------------------


class TestDefaultOffZeroBehaviorChange:
    def test_unset_flags_produce_todays_exact_call_and_key_sets(
        self, clean_env: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """With MAGI_VISION_MODEL/PROVIDER unset, the litellm call args and the
        result key sets are byte-identical to the pre-change behavior."""
        from magi_agent.tools.image_tools import image_understand
        from magi_agent.tools.spreadsheet_tools import _base_metadata

        clean_env.setenv("ANTHROPIC_API_KEY", _FAKE_ANTHROPIC_KEY)
        _write_png(tmp_path)
        captured = _capture_litellm(clean_env, "A plain image.")

        ctx = _ctx(tmp_path)
        result = image_understand({"path": "img.png"}, ctx)

        assert result.status == "ok"
        # (a) exact call as today: main provider model + key, same caps.
        assert len(captured) == 1
        call = captured[0]
        assert call["model"] == "anthropic/claude-sonnet-5"
        assert call["api_key"] == _FAKE_ANTHROPIC_KEY
        assert call["timeout"] == 60
        assert call["max_tokens"] == 2048
        # (b) key sets are exactly today's — no vision* receipts leak in.
        assert isinstance(result.output, dict)
        assert set(result.output) == {"description", "contentDigest"}
        assert isinstance(result.transcript_output, dict)
        assert set(result.transcript_output) == {"toolName", "contentDigest", "byteCount"}
        assert isinstance(result.metadata, dict)
        expected_metadata_keys = set(
            _base_metadata("image_understand", permission_class="read", mutates_workspace=False)
        ) | {"contentDigest", "byteCount", "mimeType", "pathRef"}
        assert set(result.metadata) == expected_metadata_keys
        assert not any(key.startswith("vision") for key in result.metadata)

    def test_resolver_returns_none_for_empty_env(self) -> None:
        from magi_agent.cli.providers import resolve_vision_provider_config

        assert resolve_vision_provider_config(env={}, config={}) is None


# ---------------------------------------------------------------------------
# 2. Routed — explicit provider + model
# ---------------------------------------------------------------------------


class TestRoutedVisionCall:
    def test_explicit_provider_and_model_route_the_call(
        self, clean_env: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        from magi_agent.tools.image_tools import image_understand

        clean_env.setenv("ANTHROPIC_API_KEY", _FAKE_ANTHROPIC_KEY)  # main provider
        clean_env.setenv("MAGI_VISION_MODEL", "gemini-3.5-pro")
        clean_env.setenv("MAGI_VISION_PROVIDER", "gemini")
        clean_env.setenv("GEMINI_API_KEY", _FAKE_GEMINI_KEY)
        _write_png(tmp_path)
        captured = _capture_litellm(clean_env, "routed description")

        ctx = _ctx(tmp_path)
        result = image_understand({"path": "img.png"}, ctx)

        assert result.status == "ok"
        assert len(captured) == 1
        assert captured[0]["model"] == "gemini/gemini-3.5-pro"
        assert captured[0]["api_key"] == _FAKE_GEMINI_KEY
        assert isinstance(result.output, dict)
        assert result.output["description"] == "routed description"
        assert isinstance(result.metadata, dict)
        assert result.metadata["visionRouted"] is True
        assert result.metadata["visionModel"] == "gemini/gemini-3.5-pro"

    def test_routed_call_keeps_existing_caps(
        self, clean_env: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """The override changes which model, nothing else (timeout/max_tokens)."""
        from magi_agent.tools.image_tools import image_understand

        clean_env.setenv("MAGI_VISION_MODEL", "gemini-3.5-pro")
        clean_env.setenv("MAGI_VISION_PROVIDER", "gemini")
        clean_env.setenv("GEMINI_API_KEY", _FAKE_GEMINI_KEY)
        _write_png(tmp_path)
        captured = _capture_litellm(clean_env)

        image_understand({"path": "img.png"}, _ctx(tmp_path))

        assert captured[0]["timeout"] == 60
        assert captured[0]["max_tokens"] == 2048

    def test_routed_metadata_includes_model_tier(
        self, clean_env: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Tier-registry observability: routed calls attach visionModelTier."""
        from magi_agent.tools.image_tools import image_understand

        clean_env.setenv("MAGI_VISION_MODEL", "gemini-3.5-pro")
        clean_env.setenv("MAGI_VISION_PROVIDER", "gemini")
        clean_env.setenv("GEMINI_API_KEY", _FAKE_GEMINI_KEY)
        _write_png(tmp_path)
        _capture_litellm(clean_env)

        result = image_understand({"path": "img.png"}, _ctx(tmp_path))

        assert isinstance(result.metadata, dict)
        # gemini-3.5-pro is not in the default registry -> "standard".
        assert result.metadata["visionModelTier"] == "standard"


# ---------------------------------------------------------------------------
# 3. Model-only override inherits main provider credentials
# ---------------------------------------------------------------------------


class TestModelOnlyOverride:
    def test_model_only_override_inherits_main_provider(
        self, clean_env: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        from magi_agent.tools.image_tools import image_understand

        clean_env.setenv("ANTHROPIC_API_KEY", _FAKE_ANTHROPIC_KEY)
        clean_env.setenv("MAGI_VISION_MODEL", "claude-opus-4-7")
        _write_png(tmp_path)
        captured = _capture_litellm(clean_env)

        result = image_understand({"path": "img.png"}, _ctx(tmp_path))

        assert len(captured) == 1
        assert captured[0]["model"] == "anthropic/claude-opus-4-7"
        assert captured[0]["api_key"] == _FAKE_ANTHROPIC_KEY
        assert isinstance(result.metadata, dict)
        assert result.metadata["visionRouted"] is True


# ---------------------------------------------------------------------------
# 4. Fail-soft fallback — routed call fails, main path serves
# ---------------------------------------------------------------------------


class TestFailSoftFallback:
    def test_routed_failure_falls_back_to_main_path(
        self, clean_env: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        from magi_agent.tools.image_tools import image_understand

        clean_env.setenv("ANTHROPIC_API_KEY", _FAKE_ANTHROPIC_KEY)
        clean_env.setenv("MAGI_VISION_MODEL", "gemini-3.5-pro")
        clean_env.setenv("MAGI_VISION_PROVIDER", "gemini")
        clean_env.setenv("GEMINI_API_KEY", _FAKE_GEMINI_KEY)
        _write_png(tmp_path)

        calls: list[dict[str, Any]] = []

        def flaky_completion(**kwargs: Any) -> object:
            calls.append(kwargs)
            if kwargs["model"] == "gemini/gemini-3.5-pro":
                raise RuntimeError("routed vision model unavailable")
            return _make_litellm_response("main path served")

        clean_env.setattr("litellm.completion", flaky_completion)

        result = image_understand({"path": "img.png"}, _ctx(tmp_path))

        assert result.status == "ok"
        assert len(calls) == 2
        assert calls[0]["model"] == "gemini/gemini-3.5-pro"
        assert calls[1]["model"] == "anthropic/claude-sonnet-5"
        assert isinstance(result.output, dict)
        assert result.output["description"] == "main path served"
        assert isinstance(result.metadata, dict)
        assert result.metadata["visionFallback"] is True
        assert "routed vision model unavailable" in result.metadata["visionFallbackError"]

    def test_both_paths_failing_returns_existing_error_string(
        self, clean_env: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Override and main path both fail -> existing graceful error string."""
        from magi_agent.tools.image_tools import image_understand

        clean_env.setenv("ANTHROPIC_API_KEY", _FAKE_ANTHROPIC_KEY)
        clean_env.setenv("MAGI_VISION_MODEL", "gemini-3.5-pro")
        clean_env.setenv("MAGI_VISION_PROVIDER", "gemini")
        clean_env.setenv("GEMINI_API_KEY", _FAKE_GEMINI_KEY)
        _write_png(tmp_path)

        def always_fail(**kwargs: Any) -> object:
            raise RuntimeError("everything is down")

        clean_env.setattr("litellm.completion", always_fail)

        result = image_understand({"path": "img.png"}, _ctx(tmp_path))

        assert result.status == "ok"
        assert isinstance(result.output, dict)
        assert "vision call failed" in result.output["description"]


# ---------------------------------------------------------------------------
# 5. Skip reasons — set but unusable override degrades, never crashes
# ---------------------------------------------------------------------------


class TestRouteSkipped:
    def test_unsupported_vision_provider_skips_with_reason(
        self, clean_env: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        from magi_agent.tools.image_tools import image_understand

        clean_env.setenv("ANTHROPIC_API_KEY", _FAKE_ANTHROPIC_KEY)
        clean_env.setenv("MAGI_VISION_MODEL", "some-vision-model")
        clean_env.setenv("MAGI_VISION_PROVIDER", "notreal")
        _write_png(tmp_path)
        captured = _capture_litellm(clean_env)

        result = image_understand({"path": "img.png"}, _ctx(tmp_path))

        assert result.status == "ok"
        assert len(captured) == 1
        assert captured[0]["model"] == "anthropic/claude-sonnet-5"
        assert isinstance(result.metadata, dict)
        assert result.metadata["visionRouteSkipped"] == "vision_provider_unsupported"

    def test_missing_vision_provider_key_skips_with_reason(
        self, clean_env: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        from magi_agent.tools.image_tools import image_understand

        clean_env.setenv("ANTHROPIC_API_KEY", _FAKE_ANTHROPIC_KEY)
        clean_env.setenv("MAGI_VISION_MODEL", "gemini-3.5-pro")
        clean_env.setenv("MAGI_VISION_PROVIDER", "gemini")  # no GEMINI key in env
        _write_png(tmp_path)
        captured = _capture_litellm(clean_env)

        result = image_understand({"path": "img.png"}, _ctx(tmp_path))

        assert result.status == "ok"
        assert len(captured) == 1
        assert captured[0]["model"] == "anthropic/claude-sonnet-5"
        assert isinstance(result.metadata, dict)
        assert result.metadata["visionRouteSkipped"] == "no_api_key"

    def test_no_main_provider_to_inherit_skips_with_reason(
        self, clean_env: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Model-only override but no main provider at all -> skip + the
        existing hardcoded-fallback main path behavior."""
        from magi_agent.tools.image_tools import image_understand

        clean_env.setenv("MAGI_VISION_MODEL", "some-vision-model")
        _write_png(tmp_path)
        captured = _capture_litellm(clean_env)

        result = image_understand({"path": "img.png"}, _ctx(tmp_path))

        assert result.status == "ok"
        assert len(captured) == 1
        assert captured[0]["model"] == "anthropic/claude-sonnet-5"
        assert captured[0]["api_key"] is None
        assert isinstance(result.metadata, dict)
        assert result.metadata["visionRouteSkipped"] == "no_main_provider"

    def test_provider_set_without_model_is_a_noop(
        self, clean_env: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """MAGI_VISION_PROVIDER alone does not trigger routing (model is the trigger)."""
        from magi_agent.tools.image_tools import image_understand

        clean_env.setenv("ANTHROPIC_API_KEY", _FAKE_ANTHROPIC_KEY)
        clean_env.setenv("MAGI_VISION_PROVIDER", "gemini")
        clean_env.setenv("GEMINI_API_KEY", _FAKE_GEMINI_KEY)
        _write_png(tmp_path)
        captured = _capture_litellm(clean_env)

        result = image_understand({"path": "img.png"}, _ctx(tmp_path))

        assert result.status == "ok"
        assert len(captured) == 1
        assert captured[0]["model"] == "anthropic/claude-sonnet-5"
        assert isinstance(result.metadata, dict)
        assert not any(key.startswith("vision") for key in result.metadata)


# ---------------------------------------------------------------------------
# 6. Verify pass routing — structured + verify both hit the routed model
# ---------------------------------------------------------------------------


class TestVerifyPassRouting:
    def test_structured_verify_uses_routed_model_for_both_calls(
        self, clean_env: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        from magi_agent.tools.image_tools import image_understand

        clean_env.setenv("MAGI_VISION_MODEL", "gemini-3.5-pro")
        clean_env.setenv("MAGI_VISION_PROVIDER", "gemini")
        clean_env.setenv("GEMINI_API_KEY", _FAKE_GEMINI_KEY)
        _write_png(tmp_path)
        captured = _capture_litellm(clean_env, '{"values": [1, 2]}')

        result = image_understand(
            {"path": "img.png", "mode": "structured", "verify": True}, _ctx(tmp_path)
        )

        assert result.status == "ok"
        assert len(captured) == 2
        assert captured[0]["model"] == "gemini/gemini-3.5-pro"
        assert captured[1]["model"] == "gemini/gemini-3.5-pro"
        assert isinstance(result.metadata, dict)
        assert result.metadata["visionRouted"] is True


# ---------------------------------------------------------------------------
# 7. Resolver unit tests — injectable env/config, never raises
# ---------------------------------------------------------------------------


class TestResolveVisionProviderConfig:
    def test_blank_model_returns_none(self) -> None:
        from magi_agent.cli.providers import resolve_vision_provider_config

        assert resolve_vision_provider_config(env={"MAGI_VISION_MODEL": "  "}, config={}) is None

    def test_sentinel_model_returns_none(self) -> None:
        from magi_agent.cli.providers import resolve_vision_provider_config
        from magi_agent.config.env import LOCAL_DEV_MODEL_SENTINEL

        assert (
            resolve_vision_provider_config(
                env={"MAGI_VISION_MODEL": LOCAL_DEV_MODEL_SENTINEL}, config={}
            )
            is None
        )

    def test_explicit_provider_resolves_key_from_config_block(self) -> None:
        from magi_agent.cli.providers import resolve_vision_provider_config

        cfg = resolve_vision_provider_config(
            env={"MAGI_VISION_MODEL": "gemini-3.5-pro", "MAGI_VISION_PROVIDER": "gemini"},
            config={"providers": {"gemini": {"api_key": _FAKE_GEMINI_KEY}}},
        )
        assert cfg is not None
        assert cfg.provider == "gemini"
        assert cfg.model == "gemini-3.5-pro"
        assert cfg.api_key == _FAKE_GEMINI_KEY
        assert cfg.litellm_model == "gemini/gemini-3.5-pro"

    def test_explicit_provider_resolves_key_from_env(self) -> None:
        from magi_agent.cli.providers import resolve_vision_provider_config

        cfg = resolve_vision_provider_config(
            env={
                "MAGI_VISION_MODEL": "gpt-5.5-vision",
                "MAGI_VISION_PROVIDER": "openai",
                "OPENAI_API_KEY": _FAKE_OPENAI_KEY,
            },
            config={},
        )
        assert cfg is not None
        assert cfg.provider == "openai"
        assert cfg.api_key == _FAKE_OPENAI_KEY

    def test_unsupported_provider_returns_none_not_raises(self) -> None:
        from magi_agent.cli.providers import resolve_vision_provider_config

        cfg = resolve_vision_provider_config(
            env={"MAGI_VISION_MODEL": "x", "MAGI_VISION_PROVIDER": "notreal"},
            config={},
        )
        assert cfg is None

    def test_inherits_main_provider_when_provider_unset(self) -> None:
        from magi_agent.cli.providers import resolve_vision_provider_config

        cfg = resolve_vision_provider_config(
            env={"MAGI_VISION_MODEL": "claude-opus-4-7", "ANTHROPIC_API_KEY": _FAKE_ANTHROPIC_KEY},
            config={},
        )
        assert cfg is not None
        assert cfg.provider == "anthropic"
        assert cfg.model == "claude-opus-4-7"
        assert cfg.api_key == _FAKE_ANTHROPIC_KEY

    def test_bad_main_provider_returns_none_not_raises(self) -> None:
        """A bad MAGI_PROVIDER raises in resolve_provider_config; the vision
        resolver must absorb it (tool path never crashes)."""
        from magi_agent.cli.providers import resolve_vision_provider_config

        cfg = resolve_vision_provider_config(
            env={"MAGI_VISION_MODEL": "x", "MAGI_PROVIDER": "notreal"},
            config={},
        )
        assert cfg is None

    def test_no_main_provider_returns_none(self) -> None:
        from magi_agent.cli.providers import resolve_vision_provider_config

        assert resolve_vision_provider_config(env={"MAGI_VISION_MODEL": "x"}, config={}) is None

    def test_malformed_config_never_raises(self) -> None:
        from magi_agent.cli.providers import resolve_vision_provider_config

        cfg = resolve_vision_provider_config(
            env={"MAGI_VISION_MODEL": "x", "MAGI_VISION_PROVIDER": "gemini"},
            config={"providers": "not-a-dict"},  # type: ignore[dict-item]
        )
        assert cfg is None


# ---------------------------------------------------------------------------
# 8. Flag registration
# ---------------------------------------------------------------------------


class TestFlagRegistration:
    def test_vision_flags_are_registered_str_flags(self) -> None:
        from magi_agent.config.flags import get_flag

        for name in ("MAGI_VISION_MODEL", "MAGI_VISION_PROVIDER"):
            spec = get_flag(name)
            assert spec.kind == "str"
            assert spec.default == ""
            assert spec.scope == "public"
