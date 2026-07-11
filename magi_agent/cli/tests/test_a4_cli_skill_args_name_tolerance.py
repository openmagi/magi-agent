"""Tests for Unit A4: CLI skill args drop fix (C3) + custom- name tolerance (C11).

C3: ``SkillPromptCommand.build_prompt`` was silently dropping trailing CLI args.
C11: ``_dispatch_headless_command`` (and TUI classify) failed to resolve skills
     stored as ``custom-<slug>`` dirs because the registry key is the clean
     ``<slug>`` name from frontmatter.

Style: no pytest-asyncio; async code is driven via ``asyncio.run``.
"""

from __future__ import annotations

import asyncio
import io
import json
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from magi_agent.cli.commands.skill_commands import SkillPromptCommand

from magi_agent.cli.contracts import (
    Command,
    CommandContext,
    CommandSurface,
    ContentBlock,
    EngineResult,
    PromptCommand,
    RuntimeEvent,
    Terminal,
)

BOTH = CommandSurface(tui=True, headless=True)

# ---------------------------------------------------------------------------
# Minimal fake registry (mirrors the one in test_headless_projection.py)
# ---------------------------------------------------------------------------


class _Registry:
    def __init__(self, commands: list[Command]) -> None:
        self._commands = commands

    def lookup(self, name: str) -> Command | None:
        for c in self._commands:
            if getattr(c, "name", None) == name:
                return c
        return None

    def list_for(self, surface: CommandSurface) -> list[Command]:
        _ = surface
        return list(self._commands)


# Minimal scripted driver that records the turn input it was called with.
class _ScriptedDriver:
    def __init__(self) -> None:
        self.seen_input: object | None = None

    async def run_turn_stream(self, runtime, turn_input, *, cancel, gate=None):
        self.seen_input = turn_input
        yield RuntimeEvent(type="token", payload={"delta": "ok"}, turn_id="t")
        yield EngineResult(
            terminal=Terminal.completed,
            usage={"input_tokens": 1, "output_tokens": 1},
            cost_usd=0.0,
            error=None,
        )


# ---------------------------------------------------------------------------
# Helper: build a CommandContext for unit tests
# ---------------------------------------------------------------------------


def _ctx(tmp_path: Path | None = None) -> CommandContext:
    return CommandContext(cwd=str(tmp_path) if tmp_path else "/tmp")


# ===========================================================================
# C3 -- SkillPromptCommand.build_prompt args handling
# ===========================================================================


class TestSkillBuildPrompt:
    """Unit tests for the fixed ``SkillPromptCommand.build_prompt``."""

    def _make_skill(self, body: str) -> "SkillPromptCommand":  # type: ignore[name-defined]
        from magi_agent.cli.commands.skill_commands import SkillPromptCommand

        return SkillPromptCommand(
            name="test-skill",
            surface=BOTH,
            body=body,
        )

    def test_no_args_returns_body_byte_identical(self) -> None:
        """No-args call must return the body string unchanged (byte-identical)."""
        body = "Do something useful.\nLine 2."
        cmd = self._make_skill(body)
        blocks = asyncio.run(cmd.build_prompt("", _ctx()))
        assert len(blocks) == 1
        assert blocks[0].text == body

    def test_none_args_returns_body_byte_identical(self) -> None:
        """``None`` args must also return the body byte-identical."""
        body = "Skill body text."
        cmd = self._make_skill(body)
        blocks = asyncio.run(cmd.build_prompt(None, _ctx()))
        assert len(blocks) == 1
        assert blocks[0].text == body

    def test_trailing_args_appended_when_no_placeholder(self) -> None:
        """When body has no $ARGUMENTS/$N tokens, trailing args are appended."""
        body = "Search the web."
        cmd = self._make_skill(body)
        blocks = asyncio.run(cmd.build_prompt("query text here", _ctx()))
        assert len(blocks) == 1
        text = blocks[0].text
        assert text.startswith(body)
        assert "query text here" in text
        assert "User request:" in text

    def test_arguments_token_substituted(self) -> None:
        """``$ARGUMENTS`` in body is replaced by the full args string."""
        body = "Please answer: $ARGUMENTS"
        cmd = self._make_skill(body)
        blocks = asyncio.run(cmd.build_prompt("what is 2+2", _ctx()))
        assert len(blocks) == 1
        assert blocks[0].text == "Please answer: what is 2+2"

    def test_positional_token_substituted(self) -> None:
        """``$1`` in body is replaced by the first whitespace-split token."""
        body = "Fetch data for ticker $1."
        cmd = self._make_skill(body)
        blocks = asyncio.run(cmd.build_prompt("AAPL", _ctx()))
        assert len(blocks) == 1
        assert blocks[0].text == "Fetch data for ticker AAPL."

    def test_arguments_token_with_no_args_yields_empty_substitution(self) -> None:
        """$ARGUMENTS with empty args substitutes to empty string (not dropped)."""
        body = "Topic: $ARGUMENTS"
        cmd = self._make_skill(body)
        blocks = asyncio.run(cmd.build_prompt("", _ctx()))
        assert len(blocks) == 1
        assert blocks[0].text == "Topic: "

    def test_korean_residual_appended(self) -> None:
        """Korean trailing text is preserved when appended (no placeholder)."""
        body = "Screening skill body."
        cmd = self._make_skill(body)
        trailing = "스킬에 대해 설명해줘"
        blocks = asyncio.run(cmd.build_prompt(trailing, _ctx()))
        assert len(blocks) == 1
        assert trailing in blocks[0].text


# ===========================================================================
# C11 -- resolve_command / _strip_custom_prefix
# ===========================================================================


class TestStripCustomPrefix:
    """Unit tests for the local ``_strip_custom_prefix`` helper in headless.py."""

    def test_strips_custom_prefix(self) -> None:
        from magi_agent.cli.headless import _strip_custom_prefix

        assert _strip_custom_prefix("custom-stock-multibagger-screening") == "stock-multibagger-screening"

    def test_strips_custom_prefix_case_insensitive(self) -> None:
        from magi_agent.cli.headless import _strip_custom_prefix

        assert _strip_custom_prefix("Custom-my-skill") == "my-skill"

    def test_no_prefix_returns_unchanged(self) -> None:
        from magi_agent.cli.headless import _strip_custom_prefix

        assert _strip_custom_prefix("my-skill") == "my-skill"

    def test_custom_only_no_suffix(self) -> None:
        from magi_agent.cli.headless import _strip_custom_prefix

        # "custom-" with nothing after -> empty string (stripped prefix only)
        assert _strip_custom_prefix("custom-") == ""

    def test_strip_only_one_prefix(self) -> None:
        from magi_agent.cli.headless import _strip_custom_prefix

        # Only one leading prefix is stripped.
        assert _strip_custom_prefix("custom-custom-nested") == "custom-nested"


class TestResolveCommand:
    """Unit tests for the shared ``resolve_command`` helper."""

    def _make_registry(self, names: list[str]) -> _Registry:
        @dataclass
        class _Stub(PromptCommand):
            async def build_prompt(self, args, ctx):  # type: ignore[override]
                return [ContentBlock(type="text", text=f"stub:{self.name}")]

        return _Registry([_Stub(name=n, surface=BOTH) for n in names])

    def test_direct_name_found(self) -> None:
        from magi_agent.cli.headless import resolve_command

        reg = self._make_registry(["my-skill"])
        cmd = resolve_command(reg, "my-skill")
        assert cmd is not None
        assert cmd.name == "my-skill"

    def test_custom_prefix_name_resolves_to_clean_name(self) -> None:
        """``custom-stock-screening`` should resolve to ``stock-screening``."""
        from magi_agent.cli.headless import resolve_command

        reg = self._make_registry(["stock-screening"])
        cmd = resolve_command(reg, "custom-stock-screening")
        assert cmd is not None
        assert cmd.name == "stock-screening"

    def test_unknown_name_returns_none(self) -> None:
        from magi_agent.cli.headless import resolve_command

        reg = self._make_registry(["some-skill"])
        assert resolve_command(reg, "does-not-exist") is None

    def test_custom_prefix_unknown_returns_none(self) -> None:
        from magi_agent.cli.headless import resolve_command

        reg = self._make_registry(["some-skill"])
        assert resolve_command(reg, "custom-does-not-exist") is None


# ===========================================================================
# C11 -- headless dispatch with /custom-<slug> spelling
# ===========================================================================


class TestHeadlessCustomPrefixDispatch:
    """Integration test: headless dispatch resolves /custom-<slug> skills."""

    def _make_skill_registry(self, slug: str, body: str) -> _Registry:
        from magi_agent.cli.commands.skill_commands import SkillPromptCommand

        # Registry key is the CLEAN slug (as stored in frontmatter ``name``).
        cmd = SkillPromptCommand(name=slug, surface=BOTH, body=body)
        return _Registry([cmd])

    def test_custom_prefix_headless_dispatch_resolves_and_expands(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``/custom-stock-multibagger-screening ARGS`` resolves and feeds engine."""
        from magi_agent.cli.headless import run_headless

        monkeypatch.setenv("MAGI_CLI_ENABLED", "1")
        slug = "stock-multibagger-screening"
        body = "Run multibagger screening."
        registry = self._make_skill_registry(slug, body)
        driver = _ScriptedDriver()
        buffer = io.StringIO()
        trailing = "스킬에 대해 설명해줘"
        prompt = f"/custom-{slug} {trailing}"

        code = asyncio.run(
            run_headless(
                prompt,
                output="stream-json",
                driver=driver,
                commands=registry,
                stream=buffer,
            )
        )

        assert code == 0, f"expected exit 0, got {code}; output: {buffer.getvalue()}"
        # The engine must have been called (PromptCommand feeds a turn).
        assert driver.seen_input is not None, "engine was not called"
        turn = driver.seen_input
        assert isinstance(turn, dict)
        turn_prompt = turn.get("prompt", "")
        # The skill body must be present.
        assert body in turn_prompt, f"body not in turn input: {turn_prompt!r}"
        # The Korean trailing text must appear in the expanded prompt.
        assert trailing in turn_prompt, f"trailing text not in turn input: {turn_prompt!r}"

    def test_custom_prefix_dispatch_error_on_truly_missing_skill(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``/custom-nonexistent`` must produce an error frame, not resolve."""
        from magi_agent.cli.headless import run_headless

        monkeypatch.setenv("MAGI_CLI_ENABLED", "1")
        registry = self._make_skill_registry("some-other-skill", "body")
        driver = _ScriptedDriver()
        buffer = io.StringIO()

        code = asyncio.run(
            run_headless(
                "/custom-nonexistent arg",
                output="stream-json",
                driver=driver,
                commands=registry,
                stream=buffer,
            )
        )

        assert code != 0 or any(
            "unknown" in json.dumps(o).lower() or o.get("type") == "error"
            for o in [json.loads(l) for l in buffer.getvalue().splitlines() if l]
        ), "expected error for truly missing skill"
        # Engine must NOT have been driven.
        assert driver.seen_input is None

    def test_direct_slug_dispatch_still_works(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The un-prefixed ``/slug`` path must remain unaffected."""
        from magi_agent.cli.headless import run_headless

        monkeypatch.setenv("MAGI_CLI_ENABLED", "1")
        slug = "stock-multibagger-screening"
        body = "Run multibagger screening."
        registry = self._make_skill_registry(slug, body)
        driver = _ScriptedDriver()
        buffer = io.StringIO()

        code = asyncio.run(
            run_headless(
                f"/{slug} some query",
                output="stream-json",
                driver=driver,
                commands=registry,
                stream=buffer,
            )
        )

        assert code == 0
        assert driver.seen_input is not None
        turn_prompt = driver.seen_input.get("prompt", "")  # type: ignore[union-attr]
        assert body in turn_prompt
        assert "some query" in turn_prompt


# ===========================================================================
# C11 -- TUI/registry parity via classify_line
# ===========================================================================


class TestClassifyLineCustomPrefix:
    """The TUI ``classify_line`` should resolve custom- prefix the same way."""

    def _make_registry(self, slug: str) -> _Registry:
        from magi_agent.cli.commands.skill_commands import SkillPromptCommand

        cmd = SkillPromptCommand(name=slug, surface=BOTH, body="body")
        return _Registry([cmd])

    def test_classify_line_resolves_custom_prefix(self) -> None:
        from magi_agent.cli.tui.input import classify_line

        slug = "stock-multibagger-screening"
        registry = self._make_registry(slug)
        sub = classify_line(f"/custom-{slug} args here", registry)
        assert sub.kind == "command"
        assert sub.command_name == f"custom-{slug}"
        # Command must have been resolved to the clean-slug skill.
        assert sub.command is not None, "command should be resolved via custom- fallback"
        assert sub.command.name == slug

    def test_classify_line_direct_slug_still_resolves(self) -> None:
        from magi_agent.cli.tui.input import classify_line

        slug = "stock-multibagger-screening"
        registry = self._make_registry(slug)
        sub = classify_line(f"/{slug} args here", registry)
        assert sub.kind == "command"
        assert sub.command is not None
        assert sub.command.name == slug

    def test_classify_line_unknown_custom_prefix_leaves_command_none(self) -> None:
        from magi_agent.cli.tui.input import classify_line

        registry = self._make_registry("some-skill")
        sub = classify_line("/custom-does-not-exist", registry)
        assert sub.kind == "command"
        assert sub.command is None


# ===========================================================================
# C11 -- disk-backed integration: build_registry with custom- dir name
# ===========================================================================


class TestBuildRegistryCustomPrefixSkill:
    """End-to-end: skill dir named ``custom-<slug>`` with frontmatter ``name: <slug>``
    is discoverable via both ``/<slug>`` and ``/custom-<slug>``."""

    def _write_skill(self, base: Path, dir_name: str, frontmatter_name: str, body: str) -> None:
        skill_dir = base / ".claude" / "skills" / dir_name
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            f"---\nname: {frontmatter_name}\ndescription: test skill\n---\n{body}",
            encoding="utf-8",
        )

    def test_custom_dir_skill_resolved_via_custom_prefix(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        from magi_agent.cli.commands.discovery import build_registry
        from magi_agent.cli.headless import resolve_command

        self._write_skill(
            tmp_path,
            dir_name="custom-stock-multibagger-screening",
            frontmatter_name="stock-multibagger-screening",
            body="Multibagger screening skill body.",
        )
        registry = build_registry(str(tmp_path))

        # Direct slug lookup must work.
        cmd = registry.lookup("stock-multibagger-screening")
        assert cmd is not None, "direct slug lookup failed"

        # custom- prefix lookup via resolve_command must work.
        cmd2 = resolve_command(registry, "custom-stock-multibagger-screening")
        assert cmd2 is not None, "custom- prefix lookup via resolve_command failed"
        assert cmd2.name == "stock-multibagger-screening"

    def test_custom_dir_skill_headless_dispatch(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Full pipeline: SKILL.md in custom-<slug> dir, dispatched as /custom-<slug>."""
        from magi_agent.cli.commands.discovery import build_registry
        from magi_agent.cli.headless import run_headless

        monkeypatch.setenv("MAGI_CLI_ENABLED", "1")
        body = "Multibagger screening skill body."
        self._write_skill(
            tmp_path,
            dir_name="custom-stock-multibagger-screening",
            frontmatter_name="stock-multibagger-screening",
            body=body,
        )
        registry = build_registry(str(tmp_path))
        driver = _ScriptedDriver()
        buffer = io.StringIO()
        trailing = "스킬에 대해 설명해줘"

        code = asyncio.run(
            run_headless(
                f"/custom-stock-multibagger-screening {trailing}",
                output="stream-json",
                driver=driver,
                commands=registry,
                stream=buffer,
            )
        )

        assert code == 0, f"exit {code}: {buffer.getvalue()}"
        assert driver.seen_input is not None, "engine was not called"
        turn_prompt = driver.seen_input.get("prompt", "")  # type: ignore[union-attr]
        assert body in turn_prompt
        assert trailing in turn_prompt
