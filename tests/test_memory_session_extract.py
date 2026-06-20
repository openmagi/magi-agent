"""PR4 — Session-end auto-extraction of declarative facts (Hermes timing).

TDD tests written BEFORE implementation (RED first).

The session-extract harness adopts the Hermes ``on_session_end`` timing: it
re-reads the conversation transcript ONCE at a session boundary (``/reset`` or
transport session close), proposes declarative fact candidates via a cheap
model, filters them through the EXISTING D2 declarative filter, and writes only
the accepted ones through the EXISTING gated ``LocalFileMemoryProvider.remember``
path (redaction + path-safety + byte caps + MEMORY.md/USER.md allowlist).

Safety posture mirrored from the rest of the memory stack:
  * DEFAULT OFF. ``MAGI_MEMORY_SESSION_EXTRACT_ENABLED`` gates the whole feature;
    writes ADDITIONALLY require ``MAGI_MEMORY_WRITE_ENABLED``.
  * When the feature gate is OFF: NO transcript model call, NO write (no-op).
  * Fail-soft: an extractor model error never raises into the caller; it yields
    a receipt with zero writes.
  * The agent must never reach SOUL.md — writes are pinned to MEMORY.md.

Sections:
  A. Declarative filtering — imperative/code candidates rejected, only
     declarative facts written.
  B. Flags OFF → no model call, no write.
  C. Real write lands in MEMORY.md (never SOUL.md) and goes through redaction.
  D. Fail-soft when the extractor model errors.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _transcript() -> list[dict]:
    return [
        {"role": "user", "content": "I prefer concise answers and I'm based in Seoul."},
        {"role": "assistant", "content": "Got it, I'll keep answers concise."},
        {"role": "user", "content": "Also go ahead and run the deploy now."},
    ]


def _make_provider(tmp_path: Path):
    from magi_agent.memory.adapters.local_file_writable import (
        LocalFileMemoryConfig,
        LocalFileMemoryProvider,
    )

    config = LocalFileMemoryConfig(
        workspace_root=tmp_path, enabled=True, write_enabled=True
    )
    return LocalFileMemoryProvider(config)


# ---------------------------------------------------------------------------
# A. Declarative filtering — only declarative facts get written
# ---------------------------------------------------------------------------


def test_imperative_and_code_candidates_rejected_only_declarative_written(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A mix of declarative + task-state/imperative candidates → only the
    declarative facts are persisted; task-state is dropped by the D2 filter."""
    from magi_agent.memory.adapters.local_file_writable import (
        MAGI_MEMORY_WRITE_ENABLED_ENV,
    )
    from magi_agent.harness.memory_session_extract import (
        MAGI_MEMORY_SESSION_EXTRACT_ENABLED_ENV,
        on_session_end,
    )

    monkeypatch.setenv(MAGI_MEMORY_SESSION_EXTRACT_ENABLED_ENV, "1")
    monkeypatch.setenv(MAGI_MEMORY_WRITE_ENABLED_ENV, "1")

    provider = _make_provider(tmp_path)

    candidates = [
        "User prefers concise answers",   # declarative — accepted
        "User is based in Seoul",         # declarative — accepted
        "PR #123 merged successfully",    # task-state — rejected
        "deployed the build to production",  # task-state verb — rejected
        "fix landed in commit a1b2c3d4e5",  # commit-SHA task-state — rejected
    ]

    receipt = asyncio.run(
        on_session_end(
            _transcript(),
            provider=provider,
            extractor=lambda _messages: list(candidates),
        )
    )

    assert receipt.status == "extracted"
    assert receipt.candidates == 5
    assert receipt.written == 2
    assert receipt.dropped_declarative == 3

    content = (tmp_path / "MEMORY.md").read_text(encoding="utf-8")
    assert "concise answers" in content
    assert "based in Seoul" in content
    # Rejected task-state must NOT appear
    assert "PR #123" not in content
    assert "deployed the build" not in content
    assert "a1b2c3d4e5" not in content


# ---------------------------------------------------------------------------
# B. Flags OFF → no model call, no write
# ---------------------------------------------------------------------------


def test_feature_flag_off_no_model_call_no_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Feature gate OFF → extractor is never called and nothing is written."""
    from magi_agent.memory.adapters.local_file_writable import (
        MAGI_MEMORY_WRITE_ENABLED_ENV,
    )
    from magi_agent.harness.memory_session_extract import (
        MAGI_MEMORY_SESSION_EXTRACT_ENABLED_ENV,
        on_session_end,
    )

    monkeypatch.delenv(MAGI_MEMORY_SESSION_EXTRACT_ENABLED_ENV, raising=False)
    monkeypatch.setenv(MAGI_MEMORY_WRITE_ENABLED_ENV, "1")

    provider = _make_provider(tmp_path)
    called = {"extractor": False}

    def _boom(_messages: list[dict]) -> list[str]:
        called["extractor"] = True
        return ["User prefers dark mode"]

    receipt = asyncio.run(
        on_session_end(_transcript(), provider=provider, extractor=_boom)
    )

    assert receipt.status == "disabled"
    assert called["extractor"] is False
    assert receipt.written == 0
    assert not (tmp_path / "MEMORY.md").exists()


def test_write_flag_off_extracts_but_does_not_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Feature gate ON but MAGI_MEMORY_WRITE_ENABLED OFF → declarative candidates
    are surfaced (status 'extracted') but NO file is written (write gate closed)."""
    from magi_agent.memory.adapters.local_file_writable import (
        MAGI_MEMORY_WRITE_ENABLED_ENV,
    )
    from magi_agent.harness.memory_session_extract import (
        MAGI_MEMORY_SESSION_EXTRACT_ENABLED_ENV,
        on_session_end,
    )

    monkeypatch.setenv(MAGI_MEMORY_SESSION_EXTRACT_ENABLED_ENV, "1")
    monkeypatch.delenv(MAGI_MEMORY_WRITE_ENABLED_ENV, raising=False)

    # Provider with explicit write_enabled=False so the write gate is closed.
    from magi_agent.memory.adapters.local_file_writable import (
        LocalFileMemoryConfig,
        LocalFileMemoryProvider,
    )

    provider = LocalFileMemoryProvider(
        LocalFileMemoryConfig(
            workspace_root=tmp_path, enabled=True, write_enabled=False
        )
    )

    receipt = asyncio.run(
        on_session_end(
            _transcript(),
            provider=provider,
            extractor=lambda _m: ["User prefers dark mode"],
        )
    )

    assert receipt.status == "extracted"
    # The declarative candidate was accepted but the provider write gate is off,
    # so it is blocked (no real write) and the file is never created.
    assert receipt.written == 0
    assert receipt.blocked >= 1
    assert not (tmp_path / "MEMORY.md").exists()


# ---------------------------------------------------------------------------
# C. Real write lands in MEMORY.md (never SOUL.md) + goes through redaction
# ---------------------------------------------------------------------------


def test_write_lands_in_memory_md_not_soul_and_is_redacted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Accepted facts are written to MEMORY.md, NEVER SOUL.md, and secrets in the
    fact body are redacted before persisting."""
    from magi_agent.memory.adapters.local_file_writable import (
        MAGI_MEMORY_WRITE_ENABLED_ENV,
    )
    from magi_agent.harness.memory_session_extract import (
        MAGI_MEMORY_SESSION_EXTRACT_ENABLED_ENV,
        on_session_end,
    )

    monkeypatch.setenv(MAGI_MEMORY_SESSION_EXTRACT_ENABLED_ENV, "1")
    monkeypatch.setenv(MAGI_MEMORY_WRITE_ENABLED_ENV, "1")

    provider = _make_provider(tmp_path)

    # One clean declarative fact + one declarative fact carrying a secret.
    secret_fact = "User's deploy key is ghp_ABCDEFGHIJ0123456789 for the repo"

    receipt = asyncio.run(
        on_session_end(
            _transcript(),
            provider=provider,
            extractor=lambda _m: [
                "User prefers ripgrep over grep",
                secret_fact,
            ],
        )
    )

    assert receipt.status == "extracted"

    memory_path = tmp_path / "MEMORY.md"
    assert memory_path.exists()
    content = memory_path.read_text(encoding="utf-8")
    assert "ripgrep over grep" in content
    # The kind label must be the session_extract tag.
    assert "session_extract" in content
    # Secret must be redacted, not persisted verbatim.
    assert "ghp_ABCDEFGHIJ0123456789" not in content

    # SOUL.md must NEVER be created or touched by the extractor.
    assert not (tmp_path / "SOUL.md").exists()


def test_extractor_cannot_write_to_soul_even_if_it_proposes_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Even a (declarative) candidate cannot be redirected to SOUL.md: the
    harness pins target_file=MEMORY.md, so SOUL.md is never written."""
    from magi_agent.memory.adapters.local_file_writable import (
        MAGI_MEMORY_WRITE_ENABLED_ENV,
    )
    from magi_agent.harness.memory_session_extract import (
        MAGI_MEMORY_SESSION_EXTRACT_ENABLED_ENV,
        on_session_end,
    )

    monkeypatch.setenv(MAGI_MEMORY_SESSION_EXTRACT_ENABLED_ENV, "1")
    monkeypatch.setenv(MAGI_MEMORY_WRITE_ENABLED_ENV, "1")

    provider = _make_provider(tmp_path)

    asyncio.run(
        on_session_end(
            _transcript(),
            provider=provider,
            extractor=lambda _m: ["User prefers tabs over spaces"],
        )
    )

    assert (tmp_path / "MEMORY.md").exists()
    assert not (tmp_path / "SOUL.md").exists()


# ---------------------------------------------------------------------------
# D. Fail-soft when the extractor model errors
# ---------------------------------------------------------------------------


def test_extractor_exception_is_fail_soft(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A raising extractor must not propagate; receipt is 'extracted' w/ 0 writes."""
    from magi_agent.memory.adapters.local_file_writable import (
        MAGI_MEMORY_WRITE_ENABLED_ENV,
    )
    from magi_agent.harness.memory_session_extract import (
        MAGI_MEMORY_SESSION_EXTRACT_ENABLED_ENV,
        on_session_end,
    )

    monkeypatch.setenv(MAGI_MEMORY_SESSION_EXTRACT_ENABLED_ENV, "1")
    monkeypatch.setenv(MAGI_MEMORY_WRITE_ENABLED_ENV, "1")

    provider = _make_provider(tmp_path)

    def _raises(_messages: list[dict]) -> list[str]:
        raise RuntimeError("model exploded")

    # Must not raise.
    receipt = asyncio.run(
        on_session_end(_transcript(), provider=provider, extractor=_raises)
    )

    assert receipt.status == "extracted"
    assert receipt.candidates == 0
    assert receipt.written == 0
    assert "extractor_exception" in receipt.reason_codes
    assert not (tmp_path / "MEMORY.md").exists()


def test_provider_write_exception_is_fail_soft(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A provider that raises on remember() must not crash the session-end hook;
    the offending fact is counted as blocked, others still attempted."""
    from magi_agent.memory.adapters.local_file_writable import (
        MAGI_MEMORY_WRITE_ENABLED_ENV,
    )
    from magi_agent.harness.memory_session_extract import (
        MAGI_MEMORY_SESSION_EXTRACT_ENABLED_ENV,
        on_session_end,
    )

    monkeypatch.setenv(MAGI_MEMORY_SESSION_EXTRACT_ENABLED_ENV, "1")
    monkeypatch.setenv(MAGI_MEMORY_WRITE_ENABLED_ENV, "1")

    class _ExplodingProvider:
        async def remember(self, _payload: object) -> None:
            raise RuntimeError("disk on fire")

    receipt = asyncio.run(
        on_session_end(
            _transcript(),
            provider=_ExplodingProvider(),
            extractor=lambda _m: ["User prefers dark mode"],
        )
    )

    assert receipt.status == "extracted"
    assert receipt.written == 0
    assert receipt.blocked >= 1


# ---------------------------------------------------------------------------
# E. extract_session_facts — real cheap-model extractor seam
# ---------------------------------------------------------------------------


def test_extract_session_facts_uses_injected_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``extract_session_facts`` calls the injected model via the ADK
    async-generator contract and parses a JSON array of fact strings."""
    from magi_agent.harness.memory_session_extract import extract_session_facts

    class _FakeResp:
        def __init__(self, text: str) -> None:
            self.content = type("C", (), {"parts": [type("P", (), {"text": text})()]})()

    class _FakeModel:
        model = "fake/cheap-model"

        async def generate_content_async(self, _req, stream=False):  # noqa: ANN001
            yield _FakeResp(
                '{"facts": ["User prefers concise answers", "User is based in Seoul"]}'
            )

    facts = asyncio.run(
        extract_session_facts(_transcript(), model=_FakeModel())
    )

    assert facts == ["User prefers concise answers", "User is based in Seoul"]


def test_extract_session_facts_model_error_returns_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A model that raises → extract_session_facts returns [] (fail-soft)."""
    from magi_agent.harness.memory_session_extract import extract_session_facts

    class _BoomModel:
        model = "fake/cheap-model"

        async def generate_content_async(self, _req, stream=False):  # noqa: ANN001
            raise RuntimeError("model down")
            yield  # pragma: no cover

    facts = asyncio.run(extract_session_facts(_transcript(), model=_BoomModel()))
    assert facts == []


def test_extract_session_facts_no_model_returns_empty() -> None:
    """No model resolvable → returns [] (no crash)."""
    from magi_agent.harness.memory_session_extract import extract_session_facts

    facts = asyncio.run(extract_session_facts(_transcript(), model=None))
    assert facts == []
