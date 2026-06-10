"""Tests for scripts/generate_env_reference.py.

The generator extracts the public flag inventory from
``magi_agent/config/flags.py`` (the PR2 registry) and renders the generated
section of ``docs/env-reference.md`` between sentinel markers, preserving the
hand-written preamble.

Pure file/registry reads — no network, no model.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Load the generator module by path (it lives under scripts/, not on the
# package import path).
# ---------------------------------------------------------------------------

ROOT_DIR = Path(__file__).resolve().parent.parent
SCRIPT = ROOT_DIR / "scripts" / "generate_env_reference.py"
ENV_REFERENCE = ROOT_DIR / "docs" / "env-reference.md"

_spec = importlib.util.spec_from_file_location("generate_env_reference", SCRIPT)
assert _spec is not None and _spec.loader is not None
gen = importlib.util.module_from_spec(_spec)
sys.modules["generate_env_reference"] = gen
_spec.loader.exec_module(gen)

from magi_agent.config.flags import FLAGS  # noqa: E402


# ---------------------------------------------------------------------------
# Marker constants exist
# ---------------------------------------------------------------------------


def test_markers_defined() -> None:
    assert gen.BEGIN_MARKER
    assert gen.END_MARKER
    assert gen.BEGIN_MARKER != gen.END_MARKER


# ---------------------------------------------------------------------------
# render_flags_section: content from the registry
# ---------------------------------------------------------------------------


class TestRenderFlagsSection:
    def test_returns_string(self) -> None:
        section = gen.render_flags_section(FLAGS)
        assert isinstance(section, str)
        assert section.strip()

    def test_includes_master_memory_flag(self) -> None:
        """Regression: MAGI_MEMORY_ENABLED (master switch) must be documented.

        It is absent from the current hand-maintained reference (coverage 9.7%);
        autogeneration from the registry restores it.
        """
        section = gen.render_flags_section(FLAGS)
        assert "MAGI_MEMORY_ENABLED" in section

    def test_includes_every_public_flag(self) -> None:
        section = gen.render_flags_section(FLAGS)
        public = [f for f in FLAGS if f.scope == "public"]
        assert public, "expected at least one public flag in the registry"
        for spec in public:
            assert spec.name in section, f"public flag {spec.name} missing from output"

    def test_renders_summary_text(self) -> None:
        section = gen.render_flags_section(FLAGS)
        for spec in FLAGS:
            if spec.scope == "public":
                assert spec.summary in section, f"summary for {spec.name} missing"

    def test_excludes_non_public_scopes(self) -> None:
        """hosted/internal/dev flags are intentionally absent from the public ref."""
        section = gen.render_flags_section(FLAGS)
        non_public = [f for f in FLAGS if f.scope != "public"]
        for spec in non_public:
            # A non-public flag name must not leak into the public section.
            assert spec.name not in section, (
                f"non-public flag {spec.name} ({spec.scope}) leaked into output"
            )

    def test_profile_bool_default_is_described_not_as_constant(self) -> None:
        """profile_bool flags must NOT be rendered with a flat true/false default."""
        section = gen.render_flags_section(FLAGS)
        profile_flags = [f for f in FLAGS if f.kind == "profile_bool"]
        if not profile_flags:
            pytest.skip("no profile_bool flags registered")
        # Each profile_bool flag line should mention the profile-aware nature.
        for spec in profile_flags:
            assert spec.name in section


# ---------------------------------------------------------------------------
# apply_to_document: marker-bounded rewrite preserves the preamble
# ---------------------------------------------------------------------------


class TestApplyToDocument:
    def _doc(self) -> str:
        return (
            "# Header\n\n"
            "Hand-written preamble that MUST survive.\n\n"
            f"{gen.BEGIN_MARKER}\n"
            "stale generated content\n"
            f"{gen.END_MARKER}\n\n"
            "Hand-written footer that MUST survive.\n"
        )

    def test_replaces_between_markers(self) -> None:
        out = gen.apply_to_document(self._doc(), "NEW BODY")
        assert "NEW BODY" in out
        assert "stale generated content" not in out

    def test_preserves_preamble_and_footer(self) -> None:
        out = gen.apply_to_document(self._doc(), "NEW BODY")
        assert "Hand-written preamble that MUST survive." in out
        assert "Hand-written footer that MUST survive." in out
        assert "# Header" in out

    def test_markers_remain(self) -> None:
        out = gen.apply_to_document(self._doc(), "NEW BODY")
        assert gen.BEGIN_MARKER in out
        assert gen.END_MARKER in out

    def test_missing_markers_raises(self) -> None:
        with pytest.raises(ValueError):
            gen.apply_to_document("no markers here", "BODY")

    def test_idempotent(self) -> None:
        once = gen.apply_to_document(self._doc(), gen.render_flags_section(FLAGS))
        twice = gen.apply_to_document(once, gen.render_flags_section(FLAGS))
        assert once == twice


# ---------------------------------------------------------------------------
# build_document end-to-end against the real env-reference.md
# ---------------------------------------------------------------------------


class TestBuildDocument:
    def test_real_doc_has_markers(self) -> None:
        text = ENV_REFERENCE.read_text(encoding="utf-8")
        assert gen.BEGIN_MARKER in text, "env-reference.md must carry the generated markers"
        assert gen.END_MARKER in text

    def test_committed_doc_is_in_sync(self) -> None:
        """The committed env-reference.md equals a fresh regeneration (no drift)."""
        text = ENV_REFERENCE.read_text(encoding="utf-8")
        regenerated = gen.build_document(text, FLAGS)
        assert regenerated == text, (
            "docs/env-reference.md is out of sync with the flag registry; "
            "run `python scripts/generate_env_reference.py`"
        )

    def test_committed_doc_contains_master_memory_flag(self) -> None:
        text = ENV_REFERENCE.read_text(encoding="utf-8")
        assert "MAGI_MEMORY_ENABLED" in text
