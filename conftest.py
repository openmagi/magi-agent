"""Repo-root pytest configuration: CI baseline quarantine.

The first CI run for this repo (PR #406) surfaced 174 pre-existing failures
on a fresh checkout (GitHub Actions ubuntu-latest, ``uv sync --extra dev
--extra cli``), identical on Python 3.11 and 3.12. They are quarantined as
``xfail(strict=False)`` — still executed and reported, but non-blocking —
so CI stays actionable while the underlying categories are fixed:

- openmagi/magi-agent#407 — stale monorepo path assumptions in doc/matrix tests
- openmagi/magi-agent#408 — optional document libs missing from dev+cli extras
- openmagi/magi-agent#409 — env-dependent baseline failures (import-boundary
  probes, tokenizer drift, contract drift, git-sensitive tests)

The quarantined ids live in ``tests/ci_quarantine.txt``; ``#reason:`` header
lines set the xfail reason for the ids that follow. Remove ids from the
manifest as they are fixed (an unexpectedly passing id reports as XPASS and
is safe to delete).
"""

from __future__ import annotations

from pathlib import Path

import pytest

_QUARANTINE_MANIFEST = Path(__file__).resolve().parent / "tests" / "ci_quarantine.txt"
_REASON_PREFIX = "#reason:"


def _load_quarantine(manifest: Path) -> dict[str, str]:
    """Map quarantined test nodeids to their xfail reason."""
    if not manifest.is_file():
        return {}
    entries: dict[str, str] = {}
    reason = "CI baseline quarantine (no reason header found in manifest)"
    for raw_line in manifest.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith(_REASON_PREFIX):
            reason = line[len(_REASON_PREFIX) :].strip() or reason
            continue
        if line.startswith("#"):
            continue
        entries[line] = reason
    return entries


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    quarantined = _load_quarantine(_QUARANTINE_MANIFEST)
    if not quarantined:
        return
    for item in items:
        reason = quarantined.get(item.nodeid)
        if reason is not None:
            item.add_marker(pytest.mark.xfail(reason=reason, strict=False))
