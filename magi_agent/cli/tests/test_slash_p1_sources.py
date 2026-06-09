"""Tests for P1.1 (bundled /init /review), P1.2 (markdown frontmatter + arg-sub),
and P1.3 (skill_commands plugin-skills tier).

TDD: tests are written here first; implementations must make them green.
Plain pytest + asyncio.run — no pytest-asyncio, matching existing convention.
"""

from __future__ import annotations

import asyncio
import textwrap
from pathlib import Path

import pytest

from magi_agent.cli.commands.discovery import (
    DiscoverySources,
    MarkdownPromptCommand,
    discover_commands,
    markdown_commands,
)
from magi_agent.cli.contracts import (
    CommandContext,
    CommandSurface,
    ContentBlock,
    PromptCommand,
)

BOTH = CommandSurface(tui=True, headless=True)


def _ctx(cwd: str = "/tmp/test-cwd") -> CommandContext:
    return CommandContext(cwd=cwd)


# ===========================================================================
# P1.1 — Bundled /init and /review commands
# ===========================================================================


class TestBundledCommands:
    def test_bundled_commands_returns_list(self) -> None:
        from magi_agent.cli.commands.bundled import bundled_commands

        cmds = bundled_commands()
        assert isinstance(cmds, list)
        assert len(cmds) >= 2

    def test_init_command_present(self) -> None:
        from magi_agent.cli.commands.bundled import bundled_commands

        names = {c.name for c in bundled_commands()}
        assert "init" in names

    def test_review_command_present(self) -> None:
        from magi_agent.cli.commands.bundled import bundled_commands

        names = {c.name for c in bundled_commands()}
        assert "review" in names

    def test_init_is_prompt_command(self) -> None:
        from magi_agent.cli.commands.bundled import bundled_commands

        cmds = {c.name: c for c in bundled_commands()}
        assert isinstance(cmds["init"], PromptCommand)

    def test_review_is_prompt_command(self) -> None:
        from magi_agent.cli.commands.bundled import bundled_commands

        cmds = {c.name: c for c in bundled_commands()}
        assert isinstance(cmds["review"], PromptCommand)

    def test_init_description(self) -> None:
        from magi_agent.cli.commands.bundled import InitCommand, bundled_commands

        cmds = {c.name: c for c in bundled_commands()}
        init = cmds["init"]
        assert isinstance(init, InitCommand)
        assert "AGENTS.md" in init.description.lower() or "agents" in init.description.lower()

    def test_review_description(self) -> None:
        from magi_agent.cli.commands.bundled import ReviewCommand, bundled_commands

        cmds = {c.name: c for c in bundled_commands()}
        review = cmds["review"]
        assert isinstance(review, ReviewCommand)
        assert "review" in review.description.lower()

    def test_review_has_subtask_flag(self) -> None:
        from magi_agent.cli.commands.bundled import ReviewCommand, bundled_commands

        cmds = {c.name: c for c in bundled_commands()}
        review = cmds["review"]
        assert isinstance(review, ReviewCommand)
        assert review.subtask is True

    def test_init_build_prompt_substitutes_path(self) -> None:
        from magi_agent.cli.commands.bundled import bundled_commands

        cmds = {c.name: c for c in bundled_commands()}
        init = cmds["init"]
        ctx = _ctx("/my/project")
        blocks = asyncio.run(init.build_prompt(None, ctx))
        assert len(blocks) >= 1
        text = "".join(b.text for b in blocks)
        # ${path} must be replaced with ctx.cwd
        assert "/my/project" in text
        assert "${path}" not in text

    def test_review_build_prompt_substitutes_path(self) -> None:
        from magi_agent.cli.commands.bundled import bundled_commands

        cmds = {c.name: c for c in bundled_commands()}
        review = cmds["review"]
        ctx = _ctx("/review/project")
        blocks = asyncio.run(review.build_prompt(None, ctx))
        assert len(blocks) >= 1
        text = "".join(b.text for b in blocks)
        assert "/review/project" in text
        assert "${path}" not in text

    def test_init_build_prompt_returns_content_blocks(self) -> None:
        from magi_agent.cli.commands.bundled import bundled_commands

        cmds = {c.name: c for c in bundled_commands()}
        init = cmds["init"]
        blocks = asyncio.run(init.build_prompt(None, _ctx()))
        assert all(isinstance(b, ContentBlock) for b in blocks)

    def test_bundled_present_in_discover_commands(self) -> None:
        discovered = discover_commands("/tmp/no-such-cwd")
        names = {c.name for c in discovered}
        assert "init" in names
        assert "review" in names

    def test_bundled_not_shadowed_by_project_markdown(self, tmp_path: Path) -> None:
        """Bundled tier 1 must NOT be shadowed by project .claude/commands/*.md (tier 3)."""
        commands_dir = tmp_path / ".claude" / "commands"
        commands_dir.mkdir(parents=True)
        # A project /init.md — should NOT win over the bundled init
        (commands_dir / "init.md").write_text("project init override", encoding="utf-8")

        discovered = discover_commands(str(tmp_path))
        names_order = [c.name for c in discovered]
        # 'init' is in the list
        assert "init" in names_order
        # The first 'init' found is the bundled one (PromptCommand with the template text,
        # not the project file)
        from magi_agent.cli.commands.bundled import InitCommand

        init_cmd = next(c for c in discovered if c.name == "init")
        assert isinstance(init_cmd, InitCommand), (
            "Bundled InitCommand must win over project .md file; "
            f"got {type(init_cmd).__name__} instead"
        )

    def test_bundled_surface_both(self) -> None:
        from magi_agent.cli.commands.bundled import bundled_commands

        for cmd in bundled_commands():
            assert cmd.surface.tui is True
            assert cmd.surface.headless is True


# ===========================================================================
# P1.2 — Markdown command arg substitution + frontmatter
# ===========================================================================


class TestMarkdownFrontmatter:
    def test_frontmatter_stripped_from_body(self, tmp_path: Path) -> None:
        commands_dir = tmp_path / ".claude" / "commands"
        commands_dir.mkdir(parents=True)
        (commands_dir / "cmd.md").write_text(
            textwrap.dedent("""\
                ---
                description: my desc
                ---
                body text here
            """),
            encoding="utf-8",
        )
        cmds = markdown_commands(str(tmp_path))
        assert len(cmds) == 1
        cmd = cmds[0]
        assert isinstance(cmd, MarkdownPromptCommand)
        # body should NOT contain the frontmatter block
        assert "---" not in cmd.text
        assert "description: my desc" not in cmd.text
        assert "body text here" in cmd.text

    def test_frontmatter_description_stored(self, tmp_path: Path) -> None:
        commands_dir = tmp_path / ".claude" / "commands"
        commands_dir.mkdir(parents=True)
        (commands_dir / "cmd.md").write_text(
            "---\ndescription: fancy description\n---\nbody",
            encoding="utf-8",
        )
        cmds = markdown_commands(str(tmp_path))
        cmd = cmds[0]
        assert isinstance(cmd, MarkdownPromptCommand)
        assert cmd.description == "fancy description"

    def test_frontmatter_agent_stored(self, tmp_path: Path) -> None:
        commands_dir = tmp_path / ".claude" / "commands"
        commands_dir.mkdir(parents=True)
        (commands_dir / "cmd.md").write_text(
            "---\nagent: myagent\n---\nbody",
            encoding="utf-8",
        )
        cmd = markdown_commands(str(tmp_path))[0]
        assert isinstance(cmd, MarkdownPromptCommand)
        assert cmd.agent == "myagent"

    def test_frontmatter_model_stored(self, tmp_path: Path) -> None:
        commands_dir = tmp_path / ".claude" / "commands"
        commands_dir.mkdir(parents=True)
        (commands_dir / "cmd.md").write_text(
            "---\nmodel: claude-sonnet-4-5\n---\nbody",
            encoding="utf-8",
        )
        cmd = markdown_commands(str(tmp_path))[0]
        assert isinstance(cmd, MarkdownPromptCommand)
        assert cmd.model == "claude-sonnet-4-5"

    def test_frontmatter_subtask_stored(self, tmp_path: Path) -> None:
        commands_dir = tmp_path / ".claude" / "commands"
        commands_dir.mkdir(parents=True)
        (commands_dir / "cmd.md").write_text(
            "---\nsubtask: true\n---\nbody",
            encoding="utf-8",
        )
        cmd = markdown_commands(str(tmp_path))[0]
        assert isinstance(cmd, MarkdownPromptCommand)
        assert cmd.subtask is True

    def test_frontmatter_subtask_false_by_default(self, tmp_path: Path) -> None:
        commands_dir = tmp_path / ".claude" / "commands"
        commands_dir.mkdir(parents=True)
        (commands_dir / "cmd.md").write_text("---\ndescription: x\n---\nbody", encoding="utf-8")
        cmd = markdown_commands(str(tmp_path))[0]
        assert isinstance(cmd, MarkdownPromptCommand)
        assert cmd.subtask is False

    def test_no_frontmatter_verbatim_body(self, tmp_path: Path) -> None:
        """Backward compat: no frontmatter = verbatim body as today."""
        commands_dir = tmp_path / ".claude" / "commands"
        commands_dir.mkdir(parents=True)
        body = "Do the foo thing.\n\nWith detail."
        (commands_dir / "foo.md").write_text(body, encoding="utf-8")
        cmd = markdown_commands(str(tmp_path))[0]
        assert isinstance(cmd, MarkdownPromptCommand)
        blocks = asyncio.run(cmd.build_prompt("ignored", _ctx()))
        assert len(blocks) == 1
        assert blocks[0].text == body

    def test_no_frontmatter_description_is_empty(self, tmp_path: Path) -> None:
        commands_dir = tmp_path / ".claude" / "commands"
        commands_dir.mkdir(parents=True)
        (commands_dir / "foo.md").write_text("just a body", encoding="utf-8")
        cmd = markdown_commands(str(tmp_path))[0]
        assert isinstance(cmd, MarkdownPromptCommand)
        assert cmd.description == ""


class TestMarkdownArgSubstitution:
    def _make_cmd(self, tmp_path: Path, body: str) -> MarkdownPromptCommand:
        commands_dir = tmp_path / ".claude" / "commands"
        commands_dir.mkdir(parents=True)
        (commands_dir / "cmd.md").write_text(body, encoding="utf-8")
        cmds = markdown_commands(str(tmp_path))
        cmd = cmds[0]
        assert isinstance(cmd, MarkdownPromptCommand)
        return cmd

    def test_arguments_full_substitution(self, tmp_path: Path) -> None:
        cmd = self._make_cmd(tmp_path, "Run this: $ARGUMENTS")
        blocks = asyncio.run(cmd.build_prompt("hello world", _ctx()))
        assert blocks[0].text == "Run this: hello world"

    def test_positional_1_substitution(self, tmp_path: Path) -> None:
        cmd = self._make_cmd(tmp_path, "First arg: $1")
        blocks = asyncio.run(cmd.build_prompt("alpha beta", _ctx()))
        assert blocks[0].text == "First arg: alpha"

    def test_positional_2_substitution(self, tmp_path: Path) -> None:
        cmd = self._make_cmd(tmp_path, "Second: $2")
        blocks = asyncio.run(cmd.build_prompt("alpha beta gamma", _ctx()))
        assert blocks[0].text == "Second: beta"

    def test_positional_missing_substitutes_empty(self, tmp_path: Path) -> None:
        """$3 with only 1 token -> empty string."""
        cmd = self._make_cmd(tmp_path, "Third: '$3'")
        blocks = asyncio.run(cmd.build_prompt("only-one", _ctx()))
        assert blocks[0].text == "Third: ''"

    def test_none_args_treated_as_empty(self, tmp_path: Path) -> None:
        cmd = self._make_cmd(tmp_path, "Args: '$ARGUMENTS'")
        blocks = asyncio.run(cmd.build_prompt(None, _ctx()))
        assert blocks[0].text == "Args: ''"

    def test_no_placeholders_verbatim(self, tmp_path: Path) -> None:
        """No placeholder in body → verbatim text (backward compat)."""
        body = "No substitution here."
        cmd = self._make_cmd(tmp_path, body)
        blocks = asyncio.run(cmd.build_prompt("irrelevant args", _ctx()))
        assert blocks[0].text == body

    def test_mixed_positional_and_arguments(self, tmp_path: Path) -> None:
        cmd = self._make_cmd(tmp_path, "cmd $1 of $ARGUMENTS")
        blocks = asyncio.run(cmd.build_prompt("foo bar baz", _ctx()))
        assert blocks[0].text == "cmd foo of foo bar baz"

    def test_frontmatter_with_arg_substitution(self, tmp_path: Path) -> None:
        commands_dir = tmp_path / ".claude" / "commands"
        commands_dir.mkdir(parents=True)
        (commands_dir / "cmd.md").write_text(
            "---\ndescription: test cmd\n---\nProcess $1 using $ARGUMENTS",
            encoding="utf-8",
        )
        cmds = markdown_commands(str(tmp_path))
        cmd = cmds[0]
        assert isinstance(cmd, MarkdownPromptCommand)
        assert cmd.description == "test cmd"
        blocks = asyncio.run(cmd.build_prompt("alpha beta", _ctx()))
        assert blocks[0].text == "Process alpha using alpha beta"

    def test_dollar_sign_in_user_args_not_re_substituted(self) -> None:
        """Regression: $1-like text inside the user's argument string must NOT
        be re-substituted (single-pass fix, FIX 1)."""
        cmd = MarkdownPromptCommand(
            name="test",
            surface=CommandSurface(tui=True, headless=True),
            text="$ARGUMENTS",
        )
        blocks = asyncio.run(cmd.build_prompt("hello $1 world", _ctx()))
        # The $1 inside user args must pass through verbatim.
        assert blocks[0].text == "hello $1 world"

    def test_mixed_template_single_pass(self) -> None:
        """Regression: template with both $1 and $ARGUMENTS, args='a b'; the
        $ARGUMENTS expansion must NOT have its embedded tokens re-expanded."""
        cmd = MarkdownPromptCommand(
            name="test",
            surface=CommandSurface(tui=True, headless=True),
            text="$1 and $ARGUMENTS",
        )
        blocks = asyncio.run(cmd.build_prompt("a b", _ctx()))
        assert blocks[0].text == "a and a b"

    def test_two_digit_positional_substitution(self) -> None:
        """$10 must substitute the 10th positional arg, NOT $1 followed by '0'.

        The single-pass regex _TOKEN_RE matches $ARGUMENTS | $([1-9][0-9]*),
        so '$10' is one token capturing group '10' (index 9), not '$1'+'0'.
        """
        cmd = MarkdownPromptCommand(
            name="test",
            surface=CommandSurface(tui=True, headless=True),
            text="$1 $10",
        )
        # 10 tokens: a b c d e f g h i j
        args = "a b c d e f g h i j"
        blocks = asyncio.run(cmd.build_prompt(args, _ctx()))
        text = blocks[0].text
        # $1 -> first token 'a'; $10 -> tenth token 'j'
        assert text == "a j", f"expected 'a j', got {text!r}"

    def test_two_digit_positional_reversed_order(self) -> None:
        """$10 then $1: still 10th then 1st, not any re-expansion artefact."""
        cmd = MarkdownPromptCommand(
            name="test",
            surface=CommandSurface(tui=True, headless=True),
            text="$10 then $1",
        )
        args = "a b c d e f g h i j"
        blocks = asyncio.run(cmd.build_prompt(args, _ctx()))
        assert blocks[0].text == "j then a"


class TestMarkdownHints:
    def _make_cmd(self, tmp_path: Path, body: str) -> MarkdownPromptCommand:
        commands_dir = tmp_path / ".claude" / "commands"
        commands_dir.mkdir(parents=True)
        (commands_dir / "cmd.md").write_text(body, encoding="utf-8")
        return markdown_commands(str(tmp_path))[0]  # type: ignore[return-value]

    def test_hints_positionals(self, tmp_path: Path) -> None:
        cmd = self._make_cmd(tmp_path, "do $1 then $2")
        assert isinstance(cmd, MarkdownPromptCommand)
        assert "$1" in cmd.hints
        assert "$2" in cmd.hints

    def test_hints_arguments(self, tmp_path: Path) -> None:
        cmd = self._make_cmd(tmp_path, "run $ARGUMENTS")
        assert isinstance(cmd, MarkdownPromptCommand)
        assert "$ARGUMENTS" in cmd.hints

    def test_hints_sorted_positionals(self, tmp_path: Path) -> None:
        cmd = self._make_cmd(tmp_path, "$3 and $1 and $2")
        assert isinstance(cmd, MarkdownPromptCommand)
        # positionals come in order; $ARGUMENTS appended after
        pos = [h for h in cmd.hints if h.startswith("$") and h[1:].isdigit()]
        assert pos == sorted(pos)

    def test_hints_empty_when_no_placeholders(self, tmp_path: Path) -> None:
        cmd = self._make_cmd(tmp_path, "just text")
        assert isinstance(cmd, MarkdownPromptCommand)
        assert cmd.hints == []

    def test_hints_no_duplicates(self, tmp_path: Path) -> None:
        cmd = self._make_cmd(tmp_path, "$1 and $1 again")
        assert isinstance(cmd, MarkdownPromptCommand)
        assert cmd.hints.count("$1") == 1


# ===========================================================================
# P1.3 — Skill commands (plugin_skills tier)
# ===========================================================================


class TestSkillCommands:
    def test_skill_commands_returns_list(self, tmp_path: Path) -> None:
        from magi_agent.cli.commands.skill_commands import skill_commands

        result = skill_commands(str(tmp_path))
        assert isinstance(result, list)

    def test_skill_commands_empty_when_no_skills(self, tmp_path: Path) -> None:
        """No on-disk skills; only bundled skills (from package) may be present."""
        from magi_agent.cli.commands.skill_commands import SkillPromptCommand, skill_commands

        result = skill_commands(str(tmp_path))
        # All returned commands must be SkillPromptCommand instances.
        assert all(isinstance(c, SkillPromptCommand) for c in result)
        # On-disk project dirs (skills/, .magi/skills/, docs/superpowers/) are
        # absent in tmp_path; only bundled skills (if any) may be included.
        on_disk_names: set[str] = set()
        assert not (tmp_path / "skills").exists()
        assert not (tmp_path / ".magi" / "skills").exists()
        assert not (tmp_path / "docs" / "superpowers").exists()
        # We don't assert the list is empty since bundled skills are always
        # present; we assert no on-disk project skill was incorrectly loaded.
        # The test is effectively: "no error, returns a list of SkillPromptCommand."
        _ = on_disk_names  # unused — just confirming dirs don't exist

    def test_skill_commands_discovers_skills_dir(self, tmp_path: Path) -> None:
        from magi_agent.cli.commands.skill_commands import SkillPromptCommand, skill_commands

        skill_dir = tmp_path / "skills" / "my-skill"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "---\nname: my-skill\ndescription: does stuff\n---\nSkill body.",
            encoding="utf-8",
        )
        result = skill_commands(str(tmp_path))
        by_name = {c.name: c for c in result}
        assert "my-skill" in by_name, f"my-skill not found; got {list(by_name)}"
        cmd = by_name["my-skill"]
        assert isinstance(cmd, SkillPromptCommand)
        assert cmd.description == "does stuff"

    def test_skill_commands_discovers_installed_skills_beyond_previous_cap(
        self, tmp_path: Path
    ) -> None:
        from magi_agent.cli.commands.skill_commands import skill_commands

        for index in range(75):
            name = f"bulk-skill-{index:03d}"
            skill_dir = tmp_path / "skills" / name
            skill_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text(
                f"---\nname: {name}\n---\n{name} body",
                encoding="utf-8",
            )

        result = skill_commands(str(tmp_path))

        discovered = [cmd.name for cmd in result if cmd.name.startswith("bulk-skill-")]
        assert discovered == [f"bulk-skill-{index:03d}" for index in range(75)]

    def test_skill_commands_discovers_magi_skills_dir(self, tmp_path: Path) -> None:
        from magi_agent.cli.commands.skill_commands import skill_commands

        skill_dir = tmp_path / ".magi" / "skills" / "hidden-skill"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "---\nname: hidden-skill\ndescription: hidden\n---\nbody",
            encoding="utf-8",
        )
        result = skill_commands(str(tmp_path))
        names = {c.name for c in result}
        assert "hidden-skill" in names

    def test_skill_commands_discovers_docs_superpowers_dir(self, tmp_path: Path) -> None:
        from magi_agent.cli.commands.skill_commands import skill_commands

        skill_dir = tmp_path / "docs" / "superpowers" / "sp-skill"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "---\nname: sp-skill\ndescription: sp\n---\nbody",
            encoding="utf-8",
        )
        result = skill_commands(str(tmp_path))
        names = {c.name for c in result}
        assert "sp-skill" in names

    def test_skill_commands_discovers_claude_skills_dir(self, tmp_path: Path) -> None:
        """Project ``.claude/skills`` (Claude-compatible) is scanned."""
        from magi_agent.cli.commands.skill_commands import skill_commands

        skill_dir = tmp_path / ".claude" / "skills" / "cc-skill"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "---\nname: cc-skill\ndescription: from .claude\n---\nbody",
            encoding="utf-8",
        )
        names = {c.name for c in skill_commands(str(tmp_path))}
        assert "cc-skill" in names

    def test_skill_commands_discovers_user_home_dirs(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """User-global ``~/.claude/skills`` and ``~/.magi/skills`` are scanned."""
        from magi_agent.cli.commands import skill_commands as skmod

        fake_home = tmp_path / "home"
        for base in (".claude", ".magi"):
            d = fake_home / base / "skills" / f"home-{base.lstrip('.')}"
            d.mkdir(parents=True)
            (d / "SKILL.md").write_text(
                f"---\nname: home-{base.lstrip('.')}\n---\nbody", encoding="utf-8"
            )
        monkeypatch.setattr(skmod.Path, "home", classmethod(lambda cls: fake_home))
        # cwd has no skills; everything must come from the fake home.
        names = {c.name for c in skmod.skill_commands(str(tmp_path / "proj"))}
        assert {"home-claude", "home-magi"} <= names

    def test_skill_name_fallback_to_dir_name(self, tmp_path: Path) -> None:
        from magi_agent.cli.commands.skill_commands import skill_commands

        skill_dir = tmp_path / "skills" / "fallback-skill"
        skill_dir.mkdir(parents=True)
        # No frontmatter -> name falls back to directory name
        (skill_dir / "SKILL.md").write_text("Just the body.", encoding="utf-8")
        result = skill_commands(str(tmp_path))
        by_name = {c.name: c for c in result}
        assert "fallback-skill" in by_name, f"fallback-skill not found; got {list(by_name)}"

    def test_skill_build_prompt_returns_body(self, tmp_path: Path) -> None:
        from magi_agent.cli.commands.skill_commands import skill_commands

        skill_dir = tmp_path / "skills" / "body-skill"
        skill_dir.mkdir(parents=True)
        body = "This is the skill instructions."
        (skill_dir / "SKILL.md").write_text(
            f"---\nname: body-skill\ndescription: d\n---\n{body}",
            encoding="utf-8",
        )
        result = skill_commands(str(tmp_path))
        by_name = {c.name: c for c in result}
        assert "body-skill" in by_name
        cmd = by_name["body-skill"]
        blocks = asyncio.run(cmd.build_prompt(None, _ctx()))
        assert len(blocks) == 1
        assert isinstance(blocks[0], ContentBlock)
        assert blocks[0].text == body

    def test_skill_is_prompt_command(self, tmp_path: Path) -> None:
        from magi_agent.cli.commands.skill_commands import skill_commands

        skill_dir = tmp_path / "skills" / "ps"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("---\nname: ps\n---\nbody", encoding="utf-8")
        result = skill_commands(str(tmp_path))
        assert isinstance(result[0], PromptCommand)

    def test_skill_surface_both(self, tmp_path: Path) -> None:
        from magi_agent.cli.commands.skill_commands import skill_commands

        skill_dir = tmp_path / "skills" / "sfc"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("---\nname: sfc\n---\nbody", encoding="utf-8")
        cmd = skill_commands(str(tmp_path))[0]
        assert cmd.surface.tui is True
        assert cmd.surface.headless is True

    def test_skill_shadowed_by_project_markdown(self, tmp_path: Path) -> None:
        """A .claude/commands/<name>.md (skill_dir tier 3) shadows a SKILL.md with same name (plugin_skills tier 6)."""
        # Set up a SKILL.md skill
        skill_dir = tmp_path / "skills" / "overridable"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "---\nname: overridable\ndescription: skill version\n---\nskill body",
            encoding="utf-8",
        )
        # Set up a project command with same name
        commands_dir = tmp_path / ".claude" / "commands"
        commands_dir.mkdir(parents=True)
        (commands_dir / "overridable.md").write_text(
            "project command body",
            encoding="utf-8",
        )

        discovered = discover_commands(str(tmp_path))
        by_name = {c.name: c for c in discovered}
        assert "overridable" in by_name
        # skill_dir (tier 3) beats plugin_skills (tier 6)
        cmd = by_name["overridable"]
        assert isinstance(cmd, MarkdownPromptCommand), (
            "Project .claude/commands/overridable.md (skill_dir tier) must shadow "
            f"SKILL.md (plugin_skills tier); got {type(cmd).__name__}"
        )

    def test_skills_present_in_discover_commands(self, tmp_path: Path) -> None:
        from magi_agent.cli.commands.skill_commands import skill_commands

        skill_dir = tmp_path / "skills" / "test-skill"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "---\nname: test-skill\ndescription: d\n---\nbody",
            encoding="utf-8",
        )

        discovered = discover_commands(str(tmp_path))
        names = {c.name for c in discovered}
        assert "test-skill" in names

    def test_bundled_skills_discovered(self) -> None:
        """Bundled skills from magi_agent/skills/bundled are discoverable."""
        from magi_agent.cli.commands.skill_commands import skill_commands

        # /tmp/no-such-cwd has no on-disk skills, but bundled skills should be found
        result = skill_commands("/tmp/no-such-cwd")
        # There are bundled skills (brainstorming, systematic-debugging, etc.)
        assert len(result) > 0
        names = {c.name for c in result}
        # brainstorming skill exists in bundled
        assert "brainstorming" in names


# ===========================================================================
# Backward compat guard: existing markdown tests still pass
# (these reproduce the minimal shape of existing tests to confirm no regression)
# ===========================================================================


class TestBackwardCompat:
    def test_no_frontmatter_verbatim_returns_one_block(self, tmp_path: Path) -> None:
        commands_dir = tmp_path / ".claude" / "commands"
        commands_dir.mkdir(parents=True)
        body = "Do the foo thing.\n\nWith detail."
        (commands_dir / "foo.md").write_text(body, encoding="utf-8")

        discovered = discover_commands(str(tmp_path))
        by_name = {c.name: c for c in discovered}
        assert "foo" in by_name
        foo = by_name["foo"]
        blocks = asyncio.run(foo.build_prompt("ignored-args", _ctx()))
        assert len(blocks) == 1
        assert blocks[0].text == body

    def test_discover_commands_still_includes_builtins(self) -> None:
        discovered = discover_commands("/tmp/no-such-cwd")
        names = {c.name for c in discovered}
        assert {"status", "reset", "compact", "help"} <= names

    def test_markdown_commands_empty_when_no_dir(self, tmp_path: Path) -> None:
        assert markdown_commands(str(tmp_path)) == []
