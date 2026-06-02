"""PR3: built-in beforeSystemPrompt prompt-transform presets.

Each transform is a ``handler`` hook that reads ``context.prompt_sections`` (an
immutable tuple) and returns either a ``replace`` HookResult with a NEW list
(existing sections + one new section) or ``continue`` when the relevant signal
is absent. None of the transforms ever mutate ``context.prompt_sections``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from magi_agent.hooks.builtin.prompt_transforms import (
    language_preference_transform,
    language_preference_transform_manifest,
    model_capability_transform,
    model_capability_transform_manifest,
    project_context_transform,
    project_context_transform_manifest,
)
from magi_agent.hooks.context import HookContext
from magi_agent.hooks.manifest import HookPoint


_BASE_SECTIONS = ("identity body", "safety body")


def _ctx(**overrides: object) -> HookContext:
    kwargs: dict[str, object] = {
        "bot_id": "bot-1",
        "prompt_sections": _BASE_SECTIONS,
    }
    kwargs.update(overrides)
    return HookContext(**kwargs)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Manifests: all disabled, fail-open, opt-out, beforeSystemPrompt, handler
# ---------------------------------------------------------------------------

class TestManifests:
    @pytest.mark.parametrize(
        "factory",
        (
            language_preference_transform_manifest,
            project_context_transform_manifest,
            model_capability_transform_manifest,
        ),
    )
    def test_manifest_defaults(self, factory) -> None:
        m = factory()
        assert m.enabled is False
        assert m.fail_open is True
        assert m.opt_out is True
        assert m.point is HookPoint.BEFORE_SYSTEM_PROMPT
        assert m.execution_type == "handler"
        assert m.source.kind == "builtin"


# ---------------------------------------------------------------------------
# language_preference_transform
# ---------------------------------------------------------------------------

class TestLanguagePreference:
    def test_injects_section_from_locale(self) -> None:
        ctx = _ctx(locale="ko-KR")
        result = language_preference_transform(ctx)
        assert result is not None
        assert result.action == "replace"
        assert isinstance(result.value, list)
        # original sections preserved, new section appended
        assert list(ctx.prompt_sections) == list(_BASE_SECTIONS)  # not mutated
        assert result.value[: len(_BASE_SECTIONS)] == list(_BASE_SECTIONS)
        assert "Korean" in result.value[-1]
        assert "Respond" in result.value[-1]

    def test_english_locale(self) -> None:
        ctx = _ctx(locale="en")
        result = language_preference_transform(ctx)
        assert result is not None
        assert result.action == "replace"
        assert "English" in result.value[-1]

    def test_no_locale_returns_continue(self) -> None:
        ctx = _ctx(locale=None)
        result = language_preference_transform(ctx)
        assert result is None or result.action == "continue"

    def test_no_prompt_sections_returns_continue(self) -> None:
        ctx = HookContext(bot_id="b", locale="ko")
        result = language_preference_transform(ctx)
        assert result is None or result.action == "continue"

    def test_does_not_mutate_input_tuple(self) -> None:
        ctx = _ctx(locale="ja")
        before = tuple(ctx.prompt_sections)
        language_preference_transform(ctx)
        assert tuple(ctx.prompt_sections) == before


# ---------------------------------------------------------------------------
# project_context_transform
# ---------------------------------------------------------------------------

class TestProjectContext:
    def test_injects_when_context_file_present(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        opencode = tmp_path / ".opencode"
        opencode.mkdir()
        (opencode / "context.md").write_text("Project: Magi runtime\nUse TDD.")
        monkeypatch.setenv(
            "CORE_AGENT_PYTHON_GATE1A_READONLY_TOOLS_WORKSPACE_ROOT", str(tmp_path)
        )
        ctx = _ctx()
        result = project_context_transform(ctx)
        assert result is not None
        assert result.action == "replace"
        assert result.value[: len(_BASE_SECTIONS)] == list(_BASE_SECTIONS)
        assert "Magi runtime" in result.value[-1]

    def test_no_file_returns_continue(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(
            "CORE_AGENT_PYTHON_GATE1A_READONLY_TOOLS_WORKSPACE_ROOT", str(tmp_path)
        )
        ctx = _ctx()
        result = project_context_transform(ctx)
        assert result is None or result.action == "continue"

    def test_oversized_file_truncated(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        opencode = tmp_path / ".opencode"
        opencode.mkdir()
        (opencode / "context.md").write_text("x" * 100_000)
        monkeypatch.setenv(
            "CORE_AGENT_PYTHON_GATE1A_READONLY_TOOLS_WORKSPACE_ROOT", str(tmp_path)
        )
        ctx = _ctx()
        result = project_context_transform(ctx)
        assert result is not None
        assert result.action == "replace"
        # capped well below 100k, and notes truncation
        assert len(result.value[-1]) < 50_000
        assert "truncat" in result.value[-1].lower()

    def test_no_prompt_sections_returns_continue(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        opencode = tmp_path / ".opencode"
        opencode.mkdir()
        (opencode / "context.md").write_text("ctx")
        monkeypatch.setenv(
            "CORE_AGENT_PYTHON_GATE1A_READONLY_TOOLS_WORKSPACE_ROOT", str(tmp_path)
        )
        ctx = HookContext(bot_id="b")
        result = project_context_transform(ctx)
        assert result is None or result.action == "continue"


# ---------------------------------------------------------------------------
# model_capability_transform
# ---------------------------------------------------------------------------

class TestModelCapability:
    def test_injects_for_claude_model(self) -> None:
        ctx = _ctx(agent_model="claude-opus-4-8")
        result = model_capability_transform(ctx)
        assert result is not None
        assert result.action == "replace"
        assert result.value[: len(_BASE_SECTIONS)] == list(_BASE_SECTIONS)
        assert "thinking" in result.value[-1].lower()

    def test_non_claude_model_returns_continue(self) -> None:
        ctx = _ctx(agent_model="gpt-5")
        result = model_capability_transform(ctx)
        assert result is None or result.action == "continue"

    def test_no_model_returns_continue(self) -> None:
        ctx = _ctx(agent_model=None)
        result = model_capability_transform(ctx)
        assert result is None or result.action == "continue"

    def test_no_prompt_sections_returns_continue(self) -> None:
        ctx = HookContext(bot_id="b", agent_model="claude-opus-4-8")
        result = model_capability_transform(ctx)
        assert result is None or result.action == "continue"
