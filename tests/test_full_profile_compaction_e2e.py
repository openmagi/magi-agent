"""WS2 PR2b - compaction verification + machine-region hardening.

Design: WS2 memory continuity, section "PR2b - compaction verification +
machine-region hardening".

The 5-level compaction tree (``compaction_tree.CompactionTree``) is ALREADY
implemented, wired at turn-end (``runtime/memory_turn_hook.record_turn``), backed
by a lazy cheap-model summarizer, and ALREADY ON in shipped full installs (the
compaction sub-flag cascades from the bootstrap-set memory master). PR2b does NOT
build or activate it. It (1) proves the already-live compaction end-to-end under
the full profile, (2) hardens the machine-vs-user region boundary with a
machine-owned ``ROOT.md`` banner (the only net-new production code), (3) asserts
write/read redactor parity for secret shapes, and (4) locks the crash-recovery
semantics as characterization tests.

Hermetic per SC-9: every resolver call injects an explicit ``env=`` dict and a
module-scoped autouse fixture clears any inherited ``MAGI_MEMORY_*`` /
``MAGI_RUNTIME_PROFILE`` from the developer shell via a prefix glob. The
once-per-session compaction guard is reset around every test.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from magi_agent.memory.compaction_tree import CompactionTree
from magi_agent.memory.config import MemoryRuntimeConfig, resolve_memory_config
from magi_agent.runtime.local_defaults import apply_local_full_runtime_defaults
from magi_agent.runtime.memory_turn_hook import (
    record_turn,
    reset_session_compaction_state,
)

#: Stable substring of the machine-owned ROOT banner (PR2b GREEN). Asserted as a
#: marker so a future wording tweak that keeps the marker does not break the test.
_BANNER_MARKER = "machine-generated"

#: Fixed reference clock for hermetic cooldown bookkeeping.
_FIXED = datetime(2026, 6, 23, 12, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def clear_memory_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Module-scoped hermeticity: strip inherited ``MAGI_MEMORY_*`` and
    ``MAGI_RUNTIME_PROFILE`` so a shell exporting ``MAGI_MEMORY_*=1`` (Kevin's
    does) cannot perturb these injected-env cases.

    A prefix GLOB (not an allow-list) is mandatory: the family includes
    ``MAGI_MEMORY_RECALL_RERANK_ENABLED`` and ``MAGI_MEMORY_LOCAL_DEV`` which an
    allow-list could silently miss. Scoped to THIS module only (never a
    root-conftest autouse) to avoid the #641-class blast radius.
    """
    import os

    for key in list(os.environ):
        if key.startswith("MAGI_MEMORY"):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.delenv("MAGI_RUNTIME_PROFILE", raising=False)


@pytest.fixture(autouse=True)
def _clear_session_state() -> None:
    reset_session_compaction_state()
    yield
    reset_session_compaction_state()


class _FakeSummarizer:
    """Deterministic, model-free summarizer (prefixes a stable marker)."""

    def summarize(self, text: str) -> str:
        first = next((ln for ln in text.splitlines() if ln.strip()), "")
        return f"SUMMARY:: {first.strip()}"


class _RaisingSummarizer:
    """A summarizer that always fails, to exercise the fail-open truncation."""

    def summarize(self, text: str) -> str:
        raise RuntimeError("summarizer model unavailable")


def _full_config() -> MemoryRuntimeConfig:
    """Resolve the real full-profile memory config via the local-full overlay."""
    env: dict[str, str] = {}
    apply_local_full_runtime_defaults(env)
    return resolve_memory_config(env=env)


def _seed_daily(memory_dir: Path, name: str, *, lines: int) -> Path:
    """Seed a raw (not-yet-summarized) daily file with ``lines`` entry lines."""
    daily = memory_dir / "daily"
    daily.mkdir(parents=True, exist_ok=True)
    body = "\n".join(
        f"- [turn t{i}] work item {i} on the deploy pipeline rollout"
        for i in range(lines)
    )
    target = daily / f"{name}.md"
    target.write_text(body + "\n", encoding="utf-8")
    return target


# ---------------------------------------------------------------------------
# 1. Full-profile end-to-end build.
# ---------------------------------------------------------------------------
def test_full_profile_builds_tree(tmp_path: Path) -> None:
    cfg = _full_config()
    assert cfg.compaction_enabled is True

    memory_dir = tmp_path / "memory"
    # Seed daily files in a PRIOR COMPLETED ISO week (2026-W25, Mon 06-15 + Tue
    # 06-16) relative to the fixed today (2026-06-23, ISO week 2026-W26). Because
    # W25 is strictly before W26, ``_roll_weekly`` produces weekly/2026-W25.md.
    _seed_daily(memory_dir, "2026-06-15", lines=cfg.daily_threshold + 5)
    _seed_daily(memory_dir, "2026-06-16", lines=cfg.daily_threshold + 5)

    record_turn(
        workspace_root=tmp_path,
        session_id="s-build",
        turn_id="t1",
        user_text="continue the deploy pipeline rollout",
        assistant_text="Rolled out stage 2 and updated the canary cohort.",
        used_tool=True,
        config=cfg,
        today=date(2026, 6, 23),
        summarizer=_FakeSummarizer(),
    )

    weekly = memory_dir / "weekly" / "2026-W25.md"
    root = memory_dir / "ROOT.md"
    assert weekly.is_file(), "completed prior ISO week must roll up to weekly/"
    assert root.is_file(), "ROOT.md must be synthesized"
    assert len(root.read_text(encoding="utf-8")) <= cfg.root_max_tokens * 4


# ---------------------------------------------------------------------------
# 2. Machine-owned ROOT banner (the only net-new production behavior).
# ---------------------------------------------------------------------------
def test_root_md_is_machine_owned_banner(tmp_path: Path) -> None:
    cfg = _full_config()
    memory_dir = tmp_path / "memory"
    _seed_daily(memory_dir, "2026-06-22", lines=2)

    CompactionTree(memory_dir, cfg, summarizer=_FakeSummarizer()).run(
        today=date(2026, 6, 23), force=True
    )

    root_text = (memory_dir / "ROOT.md").read_text(encoding="utf-8")
    lines = root_text.splitlines()
    # H1 stays line 1 (load-bearing for other parsers); banner is genuinely line 2.
    assert lines[0] == "# Memory Root (synthesized)"
    assert _BANNER_MARKER in lines[1]
    # Extra robustness against a future join change.
    assert _BANNER_MARKER in "\n".join(lines[:3])
    # The banner must NOT have been mistaken for the H1 (no startswith-banner).
    assert not root_text.startswith(_BANNER_MARKER)


# ---------------------------------------------------------------------------
# 3. SC-5 user-region safety.
# ---------------------------------------------------------------------------
def test_compaction_never_touches_user_files(tmp_path: Path) -> None:
    cfg = _full_config()
    memory_dir = tmp_path / "memory"
    memory_dir.mkdir(parents=True, exist_ok=True)

    user_memory = tmp_path / "MEMORY.md"
    user_profile = tmp_path / "USER.md"
    user_memory.write_text(
        "# Durable notes\n- prefer concise answers\n- ship default-OFF\n",
        encoding="utf-8",
    )
    user_profile.write_text("# Kevin\n- founder, sole developer\n", encoding="utf-8")
    mem_before = user_memory.read_bytes()
    prof_before = user_profile.read_bytes()

    _seed_daily(memory_dir, "2026-06-15", lines=3)
    _seed_daily(memory_dir, "2026-06-22", lines=3)
    CompactionTree(memory_dir, cfg, summarizer=_FakeSummarizer()).run(
        today=date(2026, 6, 23), force=True
    )

    # Self-contained: prove compaction actually did work (so the byte-unchanged
    # assertions below cannot pass vacuously if a future change silently no-ops
    # the run).
    assert (memory_dir / "ROOT.md").is_file(), "compaction must have synthesized ROOT.md"
    assert user_memory.read_bytes() == mem_before, "MEMORY.md must be byte-unchanged"
    assert user_profile.read_bytes() == prof_before, "USER.md must be byte-unchanged"


# ---------------------------------------------------------------------------
# 4. Summarizer fail-open (archive-only tiers still shrink; no raise).
# ---------------------------------------------------------------------------
def test_summarizer_failure_falls_open(tmp_path: Path) -> None:
    cfg = _full_config()
    memory_dir = tmp_path / "memory"
    _seed_daily(memory_dir, "2026-06-22", lines=cfg.daily_threshold + 10)

    # record_turn is fail-soft; a raising summarizer must not surface into the turn.
    record_turn(
        workspace_root=tmp_path,
        session_id="s-fail",
        turn_id="t1",
        user_text="keep going on the rollout",
        assistant_text="Completed the next rollout stage and verified the canary.",
        used_tool=True,
        config=cfg,
        today=date(2026, 6, 23),
        summarizer=_RaisingSummarizer(),
    )

    daily_after = (memory_dir / "daily" / "2026-06-22.md").read_text(encoding="utf-8")
    kept = [ln for ln in daily_after.splitlines() if ln.strip()]
    # The over-threshold daily tier still shrank via deterministic truncation.
    assert len(kept) <= cfg.daily_threshold


# ---------------------------------------------------------------------------
# 5. F2(b): in-process tier exception stamps the cooldown (recovery semantics).
# ---------------------------------------------------------------------------
def _raise_roll_weekly(self: CompactionTree, today: date):  # noqa: ANN202, ARG001
    raise RuntimeError("injected mid-tier failure")


def test_compaction_inproc_failure_stamps_cooldown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = _full_config()
    memory_dir = tmp_path / "memory"
    _seed_daily(memory_dir, "2026-06-15", lines=3)

    user_memory = tmp_path / "MEMORY.md"
    user_memory.write_text("- durable user note\n", encoding="utf-8")
    mem_before = user_memory.read_bytes()

    # Force the weekly tier to raise so the run()'s except block (which
    # deliberately stamps the cooldown) executes.
    monkeypatch.setattr(CompactionTree, "_roll_weekly", _raise_roll_weekly)

    tree = CompactionTree(
        memory_dir, cfg, summarizer=_FakeSummarizer(), clock=lambda: _FIXED
    )
    result = tree.run(today=date(2026, 6, 23), force=True)

    # (2) the except returns ran=True (partial result).
    assert result.ran is True
    # (1) the cooldown stamp IS written by the except.
    assert (memory_dir / ".compaction-state.json").is_file()
    # (3) no user-file damage.
    assert user_memory.read_bytes() == mem_before
    # (4) tiers written before the raise (the in-place daily compaction) remain.
    assert (memory_dir / "daily" / "2026-06-15.md").is_file()

    # (5) a second immediate run is throttled by the just-stamped cooldown.
    second = CompactionTree(
        memory_dir, cfg, summarizer=_FakeSummarizer(), clock=lambda: _FIXED
    ).run(today=date(2026, 6, 23), force=False)
    assert second.ran is False
    assert second.skipped_reason == "cooldown"


# ---------------------------------------------------------------------------
# 6. F2(a): process-kill (no stamp) converges on the next run.
# ---------------------------------------------------------------------------
def test_compaction_processkill_completes_next_run(tmp_path: Path) -> None:
    cfg = _full_config()
    memory_dir = tmp_path / "memory"
    # Consistent PARTIAL post-kill state: RAW (not-yet-summarized) daily files in
    # a prior completed ISO week, and NO .compaction-state.json (the SIGKILL never
    # reached the except stamp).
    _seed_daily(memory_dir, "2026-06-15", lines=3)
    _seed_daily(memory_dir, "2026-06-16", lines=3)
    assert not (memory_dir / ".compaction-state.json").exists()

    result = CompactionTree(
        memory_dir, cfg, summarizer=_FakeSummarizer(), clock=lambda: _FIXED
    ).run(today=date(2026, 6, 23), force=False)
    assert result.ran is True

    # Converges to a complete, consistent tree (weekly + ROOT produced).
    assert (memory_dir / "weekly" / "2026-W25.md").is_file()
    assert (memory_dir / "ROOT.md").is_file()

    # A second run is now guarded by the now-present cooldown; the tree stays
    # internally consistent (NOT asserting byte-identical re-derivation).
    second = CompactionTree(
        memory_dir, cfg, summarizer=_FakeSummarizer(), clock=lambda: _FIXED
    ).run(today=date(2026, 6, 23), force=False)
    assert second.ran is False
    assert second.skipped_reason == "cooldown"
    assert (memory_dir / "weekly" / "2026-W25.md").is_file()
    assert (memory_dir / "ROOT.md").is_file()


# ---------------------------------------------------------------------------
# 7. SC-6 redactor parity (write side at least as strong for secret shapes).
# ---------------------------------------------------------------------------
def test_redactor_parity() -> None:
    from magi_agent.memory.adapters.local_file_writable import _redact_for_write
    from magi_agent.memory.prompt_projection import _redact_snapshot_content

    # Secret-shaped fixtures assembled from FRAGMENTS (magi-agent push-protection
    # rule: never embed a contiguous provider literal). Each pair is
    # (sensitive_core_that_must_not_survive, full_input_carrying_that_core).
    bearer = "Bearer " + "abcDEF0123" + "456789ghij" + "klmnopQRST"
    github = "ghp_" + "A1b2C3d4E5" + "f6G7h8I9j0" + "K1l2M3n4O5"
    openai = "sk-" + "live1234567890" + "ABCDabcdwxyz9876"
    jwt = (
        "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
        + "."
        + "eyJzdWIiOiIxMjM0NTY3ODkwIn0"
        + "."
        + "SflKxwRJSMeKKF2QT4fwpMeJf36"
    )
    pem = (
        "-----BEGIN RSA PRIVATE KEY-----\n"
        + "MIIBOgIBAAJBAKj34GkxFh"
        + "\n-----END RSA PRIVATE KEY-----"
    )
    named = "API_KEY=" + "supersecret0123" + "456789ABCDEF"

    cases: list[tuple[str, str]] = [
        ("abcDEF0123456789ghijklmnopQRST", bearer),
        (github, github),
        (openai, openai),
        (jwt, jwt),
        ("MIIBOgIBAAJBAKj34GkxFh", pem),
        ("supersecret0123456789ABCDEF", named),
    ]

    for core, payload in cases:
        write_side = _redact_for_write(payload)
        read_side = _redact_snapshot_content(payload)
        # Write side must redact the secret-shaped token (SC-6: no secret-shaped
        # token survives the write side).
        assert core not in write_side, f"write side leaked secret: {payload!r}"
        # "At least as strong" for secret shapes: anything the read side hides,
        # the write side hides too (implied by the absolute write-side assertion).
        if core not in read_side:
            assert core not in write_side


# ---------------------------------------------------------------------------
# 8. SC-7 hermetic OFF parity (master OFF => no write, no tree).
# ---------------------------------------------------------------------------
def test_off_path_byte_identical(tmp_path: Path) -> None:
    cfg = MemoryRuntimeConfig(masterEnabled=False)
    assert cfg.write_enabled is False
    assert cfg.compaction_enabled is False

    record_turn(
        workspace_root=tmp_path,
        session_id="s-off",
        turn_id="t1",
        user_text="a substantial prompt about the deploy pipeline rollout",
        assistant_text="a long substantial reply about the rollout " * 3,
        used_tool=True,
        config=cfg,
        today=date(2026, 6, 23),
        summarizer=_FakeSummarizer(),
    )

    # No daily file, no tree: memory/ is never created on the OFF path.
    assert not (tmp_path / "memory").exists()
