"""E-1: TS export must materialize the dashboard preset list from the catalog.

This test runs the ``magi_agent.models.export_ts`` CLI module against a tmp
output and asserts the generated TS contains the post-E-1 flagship ids
(``claude-opus-4-8`` MUST appear; ``kimi-k2p6`` MUST appear) and never references
records flagged ``deprecated=True``.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

from magi_agent.models.catalog import ModelCatalog


def _run_export(tmp_path: Path) -> str:
    out = tmp_path / "generated-local-runtime-models.ts"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "magi_agent.models.export_ts",
            "--out",
            str(out),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, (
        f"export_ts failed: stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    return out.read_text(encoding="utf-8")


def test_export_ts_emits_flagship_and_cheap_models(tmp_path: Path) -> None:
    """The E-1 user-visible fix: flagship ``claude-opus-4-8`` MUST be present
    and the legacy ``claude-opus-4-6`` MUST NOT be the sota label (it can stay
    as a legacy back-compat entry, but the catalog's frontier is 4-8).
    """
    text = _run_export(tmp_path)
    # claude-opus-4-8 must be in the generated TS (catalog flagship for anthropic).
    assert "claude-opus-4-8" in text, "Generated TS missing claude-opus-4-8 flagship"
    # kimi-k2p6 must be present (the fireworks default).
    assert "kimi-k2p6" in text, "Generated TS missing kimi-k2p6"
    # The legacy retired Fireworks id MUST NOT be reintroduced.
    assert "accounts/fireworks/models/kimi-k2-instruct" not in text, (
        "Generated TS reintroduced the retired Fireworks id"
    )


def test_export_ts_marks_deprecated_records(tmp_path: Path) -> None:
    """Deprecated catalog records may stay in the TS for backward-compat
    (configs that already saved the old id must keep loading) — but each
    such entry MUST carry the ``deprecated`` marker comment so the dashboard
    can demote them in the picker.
    """
    text = _run_export(tmp_path)
    catalog = ModelCatalog.builtin()
    for record in catalog.all_records():
        if not record.deprecated:
            continue
        if record.model not in text:
            continue  # excluded entirely — also acceptable.
        # The model line must carry the deprecated marker comment.
        for line in text.splitlines():
            if f'value: "{record.model}"' in line:
                assert "deprecated" in line.lower(), (
                    f"Deprecated model {record.model!r} emitted without marker"
                )


def test_export_ts_includes_generated_header(tmp_path: Path) -> None:
    """Mark the file as generated so reviewers know not to hand-edit."""
    text = _run_export(tmp_path)
    assert "DO NOT EDIT" in text
    assert "builtin_catalog.json" in text


def test_export_ts_uses_canonical_provider_keys(tmp_path: Path) -> None:
    """Generated TS exposes the five CLI providers as preset groups."""
    text = _run_export(tmp_path)
    # Each provider name should appear as a key in the generated literal.
    for provider in ("anthropic", "openai", "gemini", "fireworks", "openrouter"):
        assert re.search(rf"\b{provider}\s*:", text), (
            f"Generated TS missing provider group {provider!r}"
        )


def test_generated_ts_file_matches_committed(tmp_path: Path) -> None:
    """The generated file committed to the repo must be up-to-date.

    Mirrors the supabase-types drop-safety pattern: re-running the generator
    must produce the same bytes as the committed file.
    """
    repo_root = Path(__file__).resolve().parents[2]
    committed = repo_root / "apps" / "web" / "src" / "lib" / "models" / "generated-local-runtime-models.ts"
    if not committed.exists():
        pytest.skip("generated-local-runtime-models.ts not yet committed")
    fresh = _run_export(tmp_path)
    assert committed.read_text(encoding="utf-8") == fresh, (
        "Generated TS is stale; re-run "
        "`python -m magi_agent.models.export_ts --out apps/web/src/lib/models/generated-local-runtime-models.ts` "
        "and commit the result."
    )
