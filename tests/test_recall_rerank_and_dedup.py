"""WS2 PR2c - recall rerank activation + in-context dedup + 5-gate env thread.

This is the only behavior-changing PR in WS2. It:

  * turns ``MAGI_MEMORY_RECALL_RERANK_ENABLED`` ON in the full local profile
    (overlay) and the dogfood env (parity),
  * threads an injectable ``env`` into ALL gate reads of that flag so the
    staleness scan + rerank reorder honour the THREADED env, not ``os.environ``
    (SC-9 hermeticity, the previously-unenumerated fifth gate at
    ``_stale_recall_paths``), and
  * adds in-context dedup: a recall hit whose content already appears in the
    assembled memory snapshot block is omitted from ``<memory-recall>`` (finding
    14: dedup against the FULL combined snapshot - projection + learning recall).

Design: WS2 memory-continuity design, section "PR2c".

Hermeticity: a module-scoped autouse fixture strips inherited ``MAGI_MEMORY_*``
so a developer shell exporting ``MAGI_MEMORY_*=1`` cannot perturb the
injected-env cases. The flag-inventory cases inject ``env`` explicitly; the e2e
recall cases activate the master via teardown-restored ``monkeypatch.setenv``.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from magi_agent.cli.memory_recall_block import (
    _DEDUP_MIN_MATCH_CHARS,
    _dedup_against_projection,
    _stale_recall_paths,
    build_cli_memory_recall_block,
)
from magi_agent.cli.memory_recall_rerank import (
    MAGI_MEMORY_RECALL_RERANK_ENABLED_ENV,
    _rerank_gate_open,
    rerank_hits,
)
from magi_agent.memory.config import resolve_memory_config
from magi_agent.memory.search.base import SearchHit
from magi_agent.runtime.local_defaults import apply_local_full_runtime_defaults

_PROFILE_PATH = (
    Path(__file__).resolve().parent.parent / "scripts" / "dogfood-full-on.env"
)


@pytest.fixture(autouse=True)
def clear_memory_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Module-scoped hermeticity: strip any inherited ``MAGI_MEMORY_*`` and
    ``MAGI_RUNTIME_PROFILE`` from ``os.environ`` so a developer shell that exports
    ``MAGI_MEMORY_*=1`` (Kevin's does) cannot perturb these cases.

    A prefix GLOB (not an allow-list) is mandatory: the family includes
    ``MAGI_MEMORY_RECALL_RERANK_ENABLED`` and ``MAGI_MEMORY_LOCAL_DEV``. Scoped to
    THIS module only (never a root-conftest autouse) to avoid the #641-class blast
    radius across the other ``MAGI_MEMORY``-referencing test files.
    """
    for key in list(os.environ):
        if key.startswith("MAGI_MEMORY"):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.delenv("MAGI_RUNTIME_PROFILE", raising=False)


def _on_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Activate the recall master + local-search gates via teardown-restored env."""
    monkeypatch.setenv("MAGI_MEMORY_ENABLED", "1")
    monkeypatch.setenv("MAGI_MEMORY_RECALL_ENABLED", "1")
    monkeypatch.setenv("MAGI_MEMORY_PREFER_LOCAL_SEARCH", "1")
    monkeypatch.setenv("MAGI_MEMORY_PREFER_QMD", "0")


def _write(root: Path, rel: str, text: str, *, mtime: float | None = None) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


def _load_dogfood_profile(path: Path = _PROFILE_PATH) -> dict[str, str]:
    """Parse ``export KEY=VALUE`` lines (mirror test_dogfood_full_on_profile)."""
    env: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :]
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            env[key] = value
    return env


# ---------------------------------------------------------------------------
# Activation: the full profile + dogfood env set the rerank flag.
# ---------------------------------------------------------------------------
def test_full_profile_enables_rerank_flag() -> None:
    env: dict[str, str] = {}
    apply_local_full_runtime_defaults(env)
    assert env.get(MAGI_MEMORY_RECALL_RERANK_ENABLED_ENV) == "1"
    # Exercise the THREADED-env seam hermetically: the gate honours the injected
    # env, not os.environ.
    assert _rerank_gate_open(env) is True


def test_dogfood_env_enables_rerank_flag() -> None:
    profile = _load_dogfood_profile()
    assert profile.get(MAGI_MEMORY_RECALL_RERANK_ENABLED_ENV) == "1"
    assert _rerank_gate_open(profile) is True


# ---------------------------------------------------------------------------
# THE FIFTH GATE: _stale_recall_paths must read the injected env (SC-9).
# ---------------------------------------------------------------------------
def test_stale_recall_gate_honors_injected_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    memory_dir = tmp_path / "memory"
    old = time.time() - 5 * 24 * 3600
    _write(
        memory_dir,
        "daily/old.md",
        "---\ndescription: stale decision\ntype: decision\n---\nold body",
        mtime=old,
    )

    # os.environ HAS the flag (simulating Kevin's exported shell) but the INJECTED
    # env LACKS it -> the gate must read the injected env and append NO notes.
    monkeypatch.setenv(MAGI_MEMORY_RECALL_RERANK_ENABLED_ENV, "1")
    assert _stale_recall_paths(memory_dir, env={}) == set()

    # Inverse: os.environ LACKS the flag but the injected env HAS it -> scan runs.
    monkeypatch.delenv(MAGI_MEMORY_RECALL_RERANK_ENABLED_ENV, raising=False)
    stale = _stale_recall_paths(
        memory_dir, env={MAGI_MEMORY_RECALL_RERANK_ENABLED_ENV: "1"}
    )
    assert stale == {"memory/daily/old.md"}


def test_rerank_identity_when_flag_off(tmp_path: Path) -> None:
    # Gate OFF via the INJECTED env (hermetic under Kevin's exported flags):
    # rerank returns the input order unchanged.
    hits = [
        SearchHit(path="memory/daily/a.md", content="a", score=3.0),
        SearchHit(path="memory/daily/b.md", content="b", score=2.0),
    ]
    out = rerank_hits(
        hits=hits,
        query="anything",
        memory_dir=tmp_path / "memory",
        config=resolve_memory_config(env={}),
        env={},
    )
    assert [h.path for h in out] == [h.path for h in hits]


# ---------------------------------------------------------------------------
# In-context dedup against the assembled snapshot (finding 14).
# ---------------------------------------------------------------------------
_ALPHA = "zebraquux alphasentinelxyz unique alpha content " + ("pad " * 6)
_BETA = "zebraquux betasentinelxyz unique beta content " + ("pad " * 6)


def test_recall_dedups_against_projection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _on_env(monkeypatch)
    _write(tmp_path, "memory/daily/a.md", _ALPHA)
    _write(tmp_path, "memory/daily/b.md", _BETA)

    # Baseline: no projection -> both hits surface.
    baseline = build_cli_memory_recall_block(
        workspace_root=str(tmp_path), query="zebraquux", memory_mode="normal"
    )
    assert "alphasentinelxyz" in baseline
    assert "betasentinelxyz" in baseline

    # Projection already carries the alpha content -> alpha is deduped out, beta
    # (a distinct hit) is kept.
    deduped = build_cli_memory_recall_block(
        workspace_root=str(tmp_path),
        query="zebraquux",
        memory_mode="normal",
        projection_text=_ALPHA,
    )
    assert "alphasentinelxyz" not in deduped
    assert "betasentinelxyz" in deduped


def test_recall_dedups_against_learning_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _on_env(monkeypatch)
    gamma = "zebraquux gammasentinelxyz learned fact content " + ("pad " * 6)
    delta = "zebraquux deltasentinelxyz distinct content " + ("pad " * 6)
    _write(tmp_path, "memory/daily/g.md", gamma)
    _write(tmp_path, "memory/daily/d.md", delta)

    # The caller passes the COMBINED snapshot (projection + learning recall) as
    # projection_text; a hit duplicating a LEARNING line is ALSO dropped.
    combined_snapshot = (
        "<memory-context>frozen projection body</memory-context>\n\n"
        "<learning>\n"
        f"{gamma}\n"
        "</learning>"
    )
    deduped = build_cli_memory_recall_block(
        workspace_root=str(tmp_path),
        query="zebraquux",
        memory_mode="normal",
        projection_text=combined_snapshot,
    )
    assert "gammasentinelxyz" not in deduped
    assert "deltasentinelxyz" in deduped


@pytest.mark.parametrize("projection", [None, ""])
def test_dedup_fail_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, projection: str | None
) -> None:
    _on_env(monkeypatch)
    _write(tmp_path, "memory/daily/a.md", _ALPHA)
    _write(tmp_path, "memory/daily/b.md", _BETA)

    block = build_cli_memory_recall_block(
        workspace_root=str(tmp_path),
        query="zebraquux",
        memory_mode="normal",
        projection_text=projection,
    )
    # None/"" projection -> full reranked set; never crashes, never drops all.
    assert "alphasentinelxyz" in block
    assert "betasentinelxyz" in block


def test_dedup_keeps_short_content_below_min_match() -> None:
    """A pathologically short hit is kept even if its token is a substring of the
    projection (the min-match guard prevents coincidental drops)."""
    from types import SimpleNamespace

    short = SimpleNamespace(content="note")  # collapses to 4 chars < the guard
    assert len("note") < _DEDUP_MIN_MATCH_CHARS
    projection = "a long projection line that mentions note among other words"
    assert _dedup_against_projection([short], projection) == [short]

    # Control: a substantial duplicate IS still deduped.
    long_dup = SimpleNamespace(content="this is a substantial durable memory line")
    proj2 = "prefix this is a substantial durable memory line suffix"
    assert _dedup_against_projection([long_dup], proj2) == []
