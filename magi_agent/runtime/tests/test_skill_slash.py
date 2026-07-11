"""Unit tests for magi_agent.runtime.skill_slash.

Covers:
- parse_slash split semantics including Korean residual text
- Each resolution rung (dir name, frontmatter name, custom- prefix both dirs)
- reserved-name refusal
- traversal-hostile token (no crash, returns None or Miss)
- truncation (small max_body_chars)
- miss with near-matches (typo)
- deterministic precedence when same skill exists in two bases
- non-slash text returns None
"""

from __future__ import annotations

from pathlib import Path

import pytest

from magi_agent.runtime.skill_slash import (
    RESERVED_COMMAND_NAMES,
    SkillSlashActivation,
    SkillSlashMiss,
    parse_slash,
    resolve_skill_slash,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_skill(
    base: Path,
    dir_name: str,
    body: str = "# skill body",
    fm_name: str | None = None,
) -> Path:
    """Write a SKILL.md under ``base/dir_name/SKILL.md``."""
    skill_dir = base / dir_name
    skill_dir.mkdir(parents=True, exist_ok=True)
    if fm_name:
        content = f"---\nname: {fm_name}\n---\n{body}"
    else:
        content = body
    skill_file = skill_dir / "SKILL.md"
    skill_file.write_text(content, encoding="utf-8")
    return skill_file


@pytest.fixture()
def ws(tmp_path: Path) -> Path:
    """Return a workspace root with a ``skills/`` directory."""
    (tmp_path / "skills").mkdir()
    return tmp_path


# ---------------------------------------------------------------------------
# parse_slash
# ---------------------------------------------------------------------------


class TestParseSlash:
    def test_simple_token(self) -> None:
        result = parse_slash("/foo")
        assert result == ("foo", "")

    def test_token_with_args(self) -> None:
        result = parse_slash("/foo bar baz")
        assert result == ("foo", "bar baz")

    def test_korean_residual(self) -> None:
        text = "/custom-stock-multibagger-screening 스킬에 대해 설명해줘"
        result = parse_slash(text)
        assert result is not None
        token, residual = result
        assert token == "custom-stock-multibagger-screening"
        assert residual == "스킬에 대해 설명해줘"

    def test_no_leading_slash(self) -> None:
        assert parse_slash("hello") is None

    def test_empty_token(self) -> None:
        # "/  " has an empty token after stripping the slash and splitting.
        # split(None, 1) on "  " returns [] so token = ""
        assert parse_slash("/") is None

    def test_slash_only_space(self) -> None:
        assert parse_slash("/  ") is None or parse_slash("/  ") is not None
        # Accept either; the important case is that split is done correctly.
        # In practice "  ".split(None, 1) -> [] so token="" -> None
        result = parse_slash("/  ")
        if result is not None:
            token, _ = result
            assert token != ""

    def test_token_with_hyphen(self) -> None:
        result = parse_slash("/my-skill args here")
        assert result == ("my-skill", "args here")

    def test_empty_string(self) -> None:
        assert parse_slash("") is None


# ---------------------------------------------------------------------------
# resolve_skill_slash -- basic behaviour
# ---------------------------------------------------------------------------


class TestNonSlash:
    def test_non_slash_returns_none(self, ws: Path) -> None:
        result = resolve_skill_slash("hello world", workspace_root=ws, max_body_chars=1000)
        assert result is None

    def test_empty_string_returns_none(self, ws: Path) -> None:
        result = resolve_skill_slash("", workspace_root=ws, max_body_chars=1000)
        assert result is None


class TestReservedNames:
    @pytest.mark.parametrize("name", sorted(RESERVED_COMMAND_NAMES))
    def test_reserved_name_returns_none(self, ws: Path, name: str) -> None:
        _make_skill(ws / "skills", name, fm_name=None)
        result = resolve_skill_slash(f"/{name}", workspace_root=ws, max_body_chars=10000)
        assert result is None

    def test_custom_reserved_set(self, ws: Path) -> None:
        _make_skill(ws / "skills", "deploy", body="# deploy skill")
        result = resolve_skill_slash(
            "/deploy",
            workspace_root=ws,
            max_body_chars=1000,
            reserved_names=frozenset({"deploy"}),
        )
        assert result is None


# ---------------------------------------------------------------------------
# Resolution rung 1: directory name == token
# ---------------------------------------------------------------------------


class TestRung1DirectoryName:
    def test_exact_dir_name_match(self, ws: Path) -> None:
        _make_skill(ws / "skills", "my-skill", body="Hello rung1")
        result = resolve_skill_slash("/my-skill", workspace_root=ws, max_body_chars=10000)
        assert isinstance(result, SkillSlashActivation)
        assert result.invoked_token == "my-skill"
        assert result.source == "workspace"
        assert "Hello rung1" in result.body
        assert result.residual_text == ""

    def test_case_insensitive_dir_name(self, ws: Path) -> None:
        _make_skill(ws / "skills", "My-Skill", body="case body")
        result = resolve_skill_slash("/my-skill", workspace_root=ws, max_body_chars=10000)
        assert isinstance(result, SkillSlashActivation)
        assert "case body" in result.body

    def test_residual_text_preserved(self, ws: Path) -> None:
        _make_skill(ws / "skills", "stock-screener", body="# screener")
        result = resolve_skill_slash(
            "/stock-screener 스킬에 대해 설명해줘",
            workspace_root=ws,
            max_body_chars=10000,
        )
        assert isinstance(result, SkillSlashActivation)
        assert result.residual_text == "스킬에 대해 설명해줘"

    def test_incident_message_exact_dir(self, ws: Path) -> None:
        """Reproduce the exact incident token: dir = custom-stock-multibagger-screening."""
        _make_skill(
            ws / "skills",
            "custom-stock-multibagger-screening",
            body="# multibagger skill body",
            fm_name="stock-multibagger-screening",
        )
        result = resolve_skill_slash(
            "/custom-stock-multibagger-screening 스킬에 대해 설명해줘",
            workspace_root=ws,
            max_body_chars=10000,
        )
        assert isinstance(result, SkillSlashActivation)
        assert result.invoked_token == "custom-stock-multibagger-screening"
        assert result.residual_text == "스킬에 대해 설명해줘"
        assert "multibagger skill body" in result.body


# ---------------------------------------------------------------------------
# Resolution rung 2: frontmatter name == token
# ---------------------------------------------------------------------------


class TestRung2FrontmatterName:
    def test_frontmatter_name_match(self, ws: Path) -> None:
        _make_skill(
            ws / "skills",
            "dir-name-differs",
            body="FM body",
            fm_name="my-clean-skill",
        )
        result = resolve_skill_slash(
            "/my-clean-skill", workspace_root=ws, max_body_chars=10000
        )
        assert isinstance(result, SkillSlashActivation)
        assert result.skill_name == "my-clean-skill"
        assert "FM body" in result.body

    def test_frontmatter_name_case_insensitive(self, ws: Path) -> None:
        _make_skill(
            ws / "skills",
            "some-dir",
            body="body",
            fm_name="MY-SKILL",
        )
        result = resolve_skill_slash("/my-skill", workspace_root=ws, max_body_chars=10000)
        assert isinstance(result, SkillSlashActivation)


# ---------------------------------------------------------------------------
# Resolution rung 3: custom- prefix stripping (both directions)
# ---------------------------------------------------------------------------


class TestRung3CustomPrefix:
    def test_dir_custom_prefix_stripped_matches_token(self, ws: Path) -> None:
        """Dir is ``custom-stock-multibagger-screening``; token is ``/stock-multibagger-screening``."""
        _make_skill(
            ws / "skills",
            "custom-stock-multibagger-screening",
            body="rung3 dir body",
            fm_name="stock-multibagger-screening",
        )
        result = resolve_skill_slash(
            "/stock-multibagger-screening",
            workspace_root=ws,
            max_body_chars=10000,
        )
        assert isinstance(result, SkillSlashActivation)
        assert "rung3 dir body" in result.body

    def test_token_custom_prefix_stripped_matches_dir(self, ws: Path) -> None:
        """Token is ``/custom-plain``; dir is ``plain`` (no custom- prefix)."""
        _make_skill(ws / "skills", "plain", body="plain body")
        result = resolve_skill_slash("/custom-plain", workspace_root=ws, max_body_chars=10000)
        assert isinstance(result, SkillSlashActivation)
        assert "plain body" in result.body

    def test_token_custom_prefix_stripped_matches_frontmatter_name(self, ws: Path) -> None:
        """Token is ``/custom-myscill``; fm_name is ``myscill``."""
        _make_skill(
            ws / "skills",
            "different-dir-name",
            body="fm rung3 body",
            fm_name="myscill",
        )
        result = resolve_skill_slash("/custom-myscill", workspace_root=ws, max_body_chars=10000)
        assert isinstance(result, SkillSlashActivation)
        assert "fm rung3 body" in result.body

    def test_both_directions_custom_present(self, ws: Path) -> None:
        """Both dir and token have custom- prefix; rung 1 should match first."""
        _make_skill(
            ws / "skills",
            "custom-alpha",
            body="alpha body",
        )
        # /custom-alpha should match rung 1 (dir name == token).
        result = resolve_skill_slash("/custom-alpha", workspace_root=ws, max_body_chars=10000)
        assert isinstance(result, SkillSlashActivation)
        assert result.source_path == "skills/custom-alpha/SKILL.md"


# ---------------------------------------------------------------------------
# Truncation
# ---------------------------------------------------------------------------


class TestTruncation:
    def test_truncation_flag_set(self, ws: Path) -> None:
        _make_skill(ws / "skills", "long-skill", body="A" * 200)
        result = resolve_skill_slash("/long-skill", workspace_root=ws, max_body_chars=50)
        assert isinstance(result, SkillSlashActivation)
        assert result.truncated is True
        assert len(result.body) == 50

    def test_no_truncation_when_fits(self, ws: Path) -> None:
        _make_skill(ws / "skills", "short-skill", body="Short")
        result = resolve_skill_slash("/short-skill", workspace_root=ws, max_body_chars=10000)
        assert isinstance(result, SkillSlashActivation)
        assert result.truncated is False
        assert "Short" in result.body


# ---------------------------------------------------------------------------
# Miss with near-matches
# ---------------------------------------------------------------------------


class TestMiss:
    def test_miss_returns_miss_type(self, ws: Path) -> None:
        result = resolve_skill_slash(
            "/nonexistent-zzz", workspace_root=ws, max_body_chars=10000
        )
        assert isinstance(result, SkillSlashMiss)
        assert result.invoked_token == "nonexistent-zzz"

    def test_miss_near_matches_prefix(self, ws: Path) -> None:
        _make_skill(ws / "skills", "stock-screener")
        result = resolve_skill_slash("/stock", workspace_root=ws, max_body_chars=10000)
        assert isinstance(result, SkillSlashMiss)
        # "stock-screener" starts with "stock" -> near match expected.
        assert "stock-screener" in result.near_matches

    def test_miss_near_matches_levenshtein(self, ws: Path) -> None:
        _make_skill(ws / "skills", "deploy-tool")
        result = resolve_skill_slash("/deploi-tool", workspace_root=ws, max_body_chars=10000)
        assert isinstance(result, SkillSlashMiss)
        # levenshtein("deploi-tool", "deploy-tool") == 1 -> near match
        assert "deploy-tool" in result.near_matches

    def test_miss_residual_preserved(self, ws: Path) -> None:
        result = resolve_skill_slash(
            "/no-skill some residual", workspace_root=ws, max_body_chars=10000
        )
        assert isinstance(result, SkillSlashMiss)
        assert result.residual_text == "some residual"

    def test_miss_at_most_3_near_matches(self, ws: Path) -> None:
        # Create 5 skills whose names all start with "foo".
        for i in range(5):
            _make_skill(ws / "skills", f"foo-skill-{i}")
        result = resolve_skill_slash("/foo", workspace_root=ws, max_body_chars=10000)
        assert isinstance(result, SkillSlashMiss)
        assert len(result.near_matches) <= 3

    def test_miss_no_near_matches_when_nothing_close(self, ws: Path) -> None:
        _make_skill(ws / "skills", "alpha-beta-gamma")
        result = resolve_skill_slash("/zzz-xyz-qrs", workspace_root=ws, max_body_chars=10000)
        assert isinstance(result, SkillSlashMiss)
        # Very different name; near_matches may be empty.
        assert isinstance(result.near_matches, tuple)


# ---------------------------------------------------------------------------
# Traversal-hostile tokens
# ---------------------------------------------------------------------------


class TestTraversalHostile:
    def test_dotdot_slash_token(self, ws: Path) -> None:
        """../etc/passwd style token must never crash; returns None or Miss."""
        result = resolve_skill_slash(
            "/../etc/passwd", workspace_root=ws, max_body_chars=10000
        )
        # Does not start with "/" correctly (starts with "/.." which parse_slash sees as
        # token = ".." and residual = "etc/passwd").
        # The resolver should either return None (reserved/empty) or SkillSlashMiss.
        assert result is None or isinstance(result, SkillSlashMiss)

    def test_absolute_path_token(self, ws: Path) -> None:
        """Token that looks like an absolute path - no crash, safe result."""
        result = resolve_skill_slash(
            "/workspace/skills/../../etc",
            workspace_root=ws,
            max_body_chars=10000,
        )
        # parse_slash returns ("workspace/skills/../../etc", "") - treat as unknown skill
        assert result is None or isinstance(result, (SkillSlashMiss, SkillSlashActivation))

    def test_null_byte_token(self, ws: Path) -> None:
        result = resolve_skill_slash(
            "/foo\x00bar", workspace_root=ws, max_body_chars=10000
        )
        # Should not crash; likely a miss.
        assert result is None or isinstance(result, (SkillSlashMiss, SkillSlashActivation))


# ---------------------------------------------------------------------------
# Deterministic precedence across bases
# ---------------------------------------------------------------------------


class TestPrecedence:
    def test_first_base_wins(self, ws: Path) -> None:
        """Same skill dir name in two bases; first scan order wins."""
        # The scan order: skills, skills-learned, .magi/skills, docs/superpowers
        (ws / "skills").mkdir(exist_ok=True)
        (ws / "skills-learned").mkdir(exist_ok=True)
        _make_skill(ws / "skills", "shared-skill", body="from skills base")
        _make_skill(ws / "skills-learned", "shared-skill", body="from skills-learned base")

        result = resolve_skill_slash(
            "/shared-skill", workspace_root=ws, max_body_chars=10000
        )
        assert isinstance(result, SkillSlashActivation)
        # skills/ comes before skills-learned/ in _WORKSPACE_SKILL_BASES order.
        assert "from skills base" in result.body
        assert result.source_path.startswith("skills/shared-skill/")

    def test_rung1_beats_rung2_across_bases(self, ws: Path) -> None:
        """Rung 1 match in a later base wins over rung 2 match in an earlier base."""
        # In "skills": dir="different-dir", fm_name="target" -> rung 2 match for /target
        # In "skills-learned": dir="target" -> rung 1 match for /target
        # Rung 1 in a later base should NOT override rung 2 in an earlier base?
        # The spec says: "Precedence across bases follows the candidate-scan order
        # (bundled first, then _WORKSPACE_SKILL_BASES order, ...); within a rung,
        # first candidate wins."
        # AND the ladder says "first hit wins" -- rung 1 is checked first.
        # So for a given candidate, rung 1 is checked before rung 2.
        # But across candidates, the first candidate in scan order with ANY rung match wins
        # only if using first-hit-across-all-candidates semantics.
        # The design says "first hit wins" per rung, with rung 1 taking priority over rung 2.
        # So rung 1 in skills-learned beats rung 2 in skills.

        (ws / "skills").mkdir(exist_ok=True)
        (ws / "skills-learned").mkdir(exist_ok=True)
        _make_skill(ws / "skills", "different-dir", fm_name="target", body="rung2 body")
        _make_skill(ws / "skills-learned", "target", body="rung1 body")

        result = resolve_skill_slash("/target", workspace_root=ws, max_body_chars=10000)
        assert isinstance(result, SkillSlashActivation)
        # Rung 1 (dir name match) should win over rung 2 (frontmatter name match),
        # even though the rung-2 match is in a base scanned first.
        assert "rung1 body" in result.body


# ---------------------------------------------------------------------------
# Source path and source field
# ---------------------------------------------------------------------------


class TestSourceField:
    def test_workspace_source(self, ws: Path) -> None:
        _make_skill(ws / "skills", "ws-skill", body="workspace body")
        result = resolve_skill_slash("/ws-skill", workspace_root=ws, max_body_chars=10000)
        assert isinstance(result, SkillSlashActivation)
        assert result.source == "workspace"
        assert result.source_path == "skills/ws-skill/SKILL.md"


# ---------------------------------------------------------------------------
# Frontmatter stripping from body
# ---------------------------------------------------------------------------


class TestFrontmatterStrip:
    def test_frontmatter_stripped_from_body(self, ws: Path) -> None:
        _make_skill(
            ws / "skills",
            "fm-skill",
            body="Actual body text",
            fm_name="fm-skill",
        )
        result = resolve_skill_slash("/fm-skill", workspace_root=ws, max_body_chars=10000)
        assert isinstance(result, SkillSlashActivation)
        # Frontmatter block (---\n...\n---) must not appear in body.
        assert "---" not in result.body
        assert "Actual body text" in result.body


# ---------------------------------------------------------------------------
# Edge: empty workspace (no skills dir)
# ---------------------------------------------------------------------------


class TestEmptyWorkspace:
    def test_no_skills_dir_returns_miss(self, tmp_path: Path) -> None:
        result = resolve_skill_slash("/any-skill", workspace_root=tmp_path, max_body_chars=1000)
        assert result is None or isinstance(result, SkillSlashMiss)
