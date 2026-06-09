"""D1 — Gated writable memory contract + LocalFileMemoryProvider.

TDD tests written before implementation.  All tests must pass after D1 lands.
Tests are grouped into four categories:

A. Invariant: read-only default still raises on supports_write=True.
B. Gated tier: allows bounded write when explicitly authorized.
C. LocalFileMemoryProvider: read path works; gated write appends to disk.
D. Gate-off inertness: provider defaults to read-only when write is not enabled.
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

from magi_agent.memory.contracts import (
    MemoryProviderCapabilities,
    RecallRequest,
    UnsupportedMemoryOperationError,
)
from magi_agent.memory.policy import MemoryPolicy


# ---------------------------------------------------------------------------
# A. Invariant: read-only default still raises on supports_write=True
# ---------------------------------------------------------------------------


def test_default_capabilities_raise_on_supports_write_true() -> None:
    """The existing invariant must be byte-identical after D1."""
    with pytest.raises(ValueError, match="read-only"):
        MemoryProviderCapabilities(
            provider_id="test-provider",
            storage_model="file",
            supports_write=True,
        )


def test_default_capabilities_raise_on_nonzero_max_write_bytes() -> None:
    """max_write_bytes != 0 with default tier raises unchanged."""
    with pytest.raises(ValueError):
        MemoryProviderCapabilities(
            provider_id="test-provider",
            storage_model="file",
            max_write_bytes=1024,
        )


def test_default_capabilities_raise_on_delete_support() -> None:
    """supports_delete != 'none' still raises."""
    with pytest.raises(ValueError, match="delete"):
        MemoryProviderCapabilities(
            provider_id="test-provider",
            storage_model="file",
            supports_delete="soft",
        )


def test_default_tier_capabilities_are_read_only() -> None:
    """Default construction — no write_tier kwarg — stays fully read-only."""
    caps = MemoryProviderCapabilities(
        provider_id="hipocampus-qmd-readonly",
        storage_model="file",
        supports_search=True,
        supports_export=True,
    )
    assert caps.supports_write is False
    assert caps.max_write_bytes == 0
    assert caps.supports_delete == "none"


# ---------------------------------------------------------------------------
# B. Gated tier: allows bounded write when explicitly authorized
# ---------------------------------------------------------------------------


def test_gated_write_tier_allows_supports_write_true_with_bounded_bytes() -> None:
    """write_tier='gated_write' unlocks supports_write + positive max_write_bytes."""
    caps = MemoryProviderCapabilities(
        provider_id="local-file-writable",
        storage_model="file",
        supports_write=True,
        max_write_bytes=65_536,
        write_tier="gated_write",
    )
    assert caps.supports_write is True
    assert caps.max_write_bytes == 65_536
    assert caps.write_tier == "gated_write"


def test_gated_write_tier_still_forbids_delete() -> None:
    """Even with gated_write, supports_delete must remain 'none'."""
    with pytest.raises(ValueError, match="delete"):
        MemoryProviderCapabilities(
            provider_id="local-file-writable",
            storage_model="file",
            supports_write=True,
            max_write_bytes=4_096,
            write_tier="gated_write",
            supports_delete="soft",
        )


def test_gated_write_tier_requires_positive_max_write_bytes() -> None:
    """gated_write with max_write_bytes=0 is incoherent and should raise."""
    with pytest.raises(ValueError, match="max_write_bytes"):
        MemoryProviderCapabilities(
            provider_id="local-file-writable",
            storage_model="file",
            supports_write=True,
            max_write_bytes=0,
            write_tier="gated_write",
        )


def test_read_only_tier_still_rejects_supports_write_true_even_if_explicitly_set() -> None:
    """write_tier='read_only' is still the default; supports_write=True must raise."""
    with pytest.raises(ValueError, match="read-only"):
        MemoryProviderCapabilities(
            provider_id="test-provider",
            storage_model="file",
            supports_write=True,
            write_tier="read_only",
        )


# ---------------------------------------------------------------------------
# C. LocalFileMemoryProvider — read path
# ---------------------------------------------------------------------------


def _write_local_memory_fixtures(root: Path) -> None:
    (root / "MEMORY.md").write_text(
        "# Memory\n\nUser prefers dark mode. Budget is 5000.\n",
        encoding="utf-8",
    )
    (root / "USER.md").write_text(
        "# User Profile\n\nName: Alice. Role: developer.\n",
        encoding="utf-8",
    )


def test_local_file_provider_read_path_loads_memory_md(tmp_path: Path) -> None:
    """recall() on gate-off provider reads MEMORY.md without writing."""
    from magi_agent.memory.adapters.local_file_writable import (
        LocalFileMemoryProvider,
        LocalFileMemoryConfig,
    )

    _write_local_memory_fixtures(tmp_path)
    config = LocalFileMemoryConfig(workspace_root=tmp_path, enabled=True)
    provider = LocalFileMemoryProvider(config)

    result = asyncio.run(
        provider.recall(
            RecallRequest(
                scope={"tenantId": "t1", "botId": "b1"},
                query="dark mode",
                purpose="answer_user",
            ),
            policy=MemoryPolicy(memory_mode="normal", source_authority="long_term_allowed"),
        )
    )

    assert result.recall_allowed is True
    assert result.write_allowed is False
    assert result.prompt_projection_allowed is False
    assert any("dark mode" in record.body for record in result.records)


def test_local_file_provider_read_path_loads_user_md(tmp_path: Path) -> None:
    """recall() also surfaces USER.md entries."""
    from magi_agent.memory.adapters.local_file_writable import (
        LocalFileMemoryProvider,
        LocalFileMemoryConfig,
    )

    _write_local_memory_fixtures(tmp_path)
    config = LocalFileMemoryConfig(workspace_root=tmp_path, enabled=True)
    provider = LocalFileMemoryProvider(config)

    result = asyncio.run(
        provider.recall(
            RecallRequest(
                scope={"tenantId": "t1", "botId": "b1"},
                query="Alice developer",
                purpose="answer_user",
            ),
            policy=MemoryPolicy(memory_mode="normal", source_authority="long_term_allowed"),
        )
    )

    assert any("Alice" in record.body or "developer" in record.body for record in result.records)


def test_local_file_provider_returns_empty_when_disabled(tmp_path: Path) -> None:
    """When enabled=False the provider returns an empty recall result."""
    from magi_agent.memory.adapters.local_file_writable import (
        LocalFileMemoryProvider,
        LocalFileMemoryConfig,
    )

    _write_local_memory_fixtures(tmp_path)
    config = LocalFileMemoryConfig(workspace_root=tmp_path, enabled=False)
    provider = LocalFileMemoryProvider(config)

    result = asyncio.run(
        provider.recall(
            RecallRequest(
                scope={"tenantId": "t1", "botId": "b1"},
                query="anything",
                purpose="answer_user",
            ),
            policy=MemoryPolicy(memory_mode="normal", source_authority="long_term_allowed"),
        )
    )

    assert result.records == ()
    assert "adapter_disabled" in result.reason_codes


# ---------------------------------------------------------------------------
# C. LocalFileMemoryProvider — gated write path (append / update)
# ---------------------------------------------------------------------------


def test_local_file_provider_gate_off_remember_raises(tmp_path: Path) -> None:
    """When write is NOT enabled, remember() raises UnsupportedMemoryOperationError."""
    from magi_agent.memory.adapters.local_file_writable import (
        LocalFileMemoryProvider,
        LocalFileMemoryConfig,
    )

    config = LocalFileMemoryConfig(workspace_root=tmp_path, enabled=True, write_enabled=False)
    provider = LocalFileMemoryProvider(config)

    with pytest.raises(UnsupportedMemoryOperationError):
        asyncio.run(provider.remember({"body": "should not be written"}))


def test_local_file_provider_gated_write_appends_to_memory_md(tmp_path: Path) -> None:
    """With write_enabled=True and MAGI_MEMORY_WRITE_ENABLED, remember() appends."""
    from magi_agent.memory.adapters.local_file_writable import (
        LocalFileMemoryProvider,
        LocalFileMemoryConfig,
    )

    config = LocalFileMemoryConfig(workspace_root=tmp_path, enabled=True, write_enabled=True)
    provider = LocalFileMemoryProvider(config)

    asyncio.run(provider.remember({
        "body": "User prefers Vim keybindings.",
        "kind": "preference",
        "scope": "user",
        "target_file": "MEMORY.md",
    }))

    memory_path = tmp_path / "MEMORY.md"
    assert memory_path.exists()
    content = memory_path.read_text(encoding="utf-8")
    assert "Vim keybindings" in content


def test_local_file_provider_gated_write_appends_to_user_md(tmp_path: Path) -> None:
    """With write_enabled=True, remember() with target_file=USER.md writes USER.md."""
    from magi_agent.memory.adapters.local_file_writable import (
        LocalFileMemoryProvider,
        LocalFileMemoryConfig,
    )

    config = LocalFileMemoryConfig(workspace_root=tmp_path, enabled=True, write_enabled=True)
    provider = LocalFileMemoryProvider(config)

    asyncio.run(provider.remember({
        "body": "User is based in Seoul.",
        "kind": "fact",
        "scope": "user",
        "target_file": "USER.md",
    }))

    user_path = tmp_path / "USER.md"
    assert user_path.exists()
    content = user_path.read_text(encoding="utf-8")
    assert "Seoul" in content


def test_local_file_provider_gated_write_is_retrievable(tmp_path: Path) -> None:
    """After a gated write, recall() returns the appended entry."""
    from magi_agent.memory.adapters.local_file_writable import (
        LocalFileMemoryProvider,
        LocalFileMemoryConfig,
    )

    config = LocalFileMemoryConfig(workspace_root=tmp_path, enabled=True, write_enabled=True)
    provider = LocalFileMemoryProvider(config)

    asyncio.run(provider.remember({
        "body": "Favorite tool is ripgrep.",
        "kind": "preference",
        "target_file": "MEMORY.md",
    }))

    result = asyncio.run(
        provider.recall(
            RecallRequest(
                scope={"tenantId": "t1", "botId": "b1"},
                query="ripgrep",
                purpose="answer_user",
            ),
            policy=MemoryPolicy(memory_mode="normal", source_authority="long_term_allowed"),
        )
    )

    assert any("ripgrep" in record.body for record in result.records)


def test_local_file_provider_write_enforces_max_write_bytes(tmp_path: Path) -> None:
    """remember() rejects payloads exceeding max_write_bytes."""
    from magi_agent.memory.adapters.local_file_writable import (
        LocalFileMemoryProvider,
        LocalFileMemoryConfig,
    )

    config = LocalFileMemoryConfig(
        workspace_root=tmp_path,
        enabled=True,
        write_enabled=True,
        max_write_bytes=10,  # tiny cap
    )
    provider = LocalFileMemoryProvider(config)

    with pytest.raises((ValueError, UnsupportedMemoryOperationError)):
        asyncio.run(provider.remember({
            "body": "This body is definitely longer than ten bytes.",
            "kind": "note",
        }))


def test_local_file_provider_write_redacts_secrets_before_persisting(tmp_path: Path) -> None:
    """Secrets in the body must be redacted before writing to disk."""
    from magi_agent.memory.adapters.local_file_writable import (
        LocalFileMemoryProvider,
        LocalFileMemoryConfig,
    )

    config = LocalFileMemoryConfig(workspace_root=tmp_path, enabled=True, write_enabled=True)
    provider = LocalFileMemoryProvider(config)

    asyncio.run(provider.remember({
        "body": "API key is sk-live-supersecretkey12345 and token is ghp_ABCDEFGHIJ0123456789",
        "kind": "note",
        "target_file": "MEMORY.md",
    }))

    content = (tmp_path / "MEMORY.md").read_text(encoding="utf-8")
    assert "sk-live-supersecretkey12345" not in content
    assert "ghp_ABCDEFGHIJ0123456789" not in content


# ---------------------------------------------------------------------------
# C2 — write-side redaction parity with read-side (_redact_for_write must be
# at least as strong as projection's _redact_snapshot_content).
# ---------------------------------------------------------------------------


def test_redact_for_write_scrubs_pem_private_key_block() -> None:
    """A PEM-encoded private key block must be redacted before disk write."""
    from magi_agent.memory.adapters.local_file_writable import _redact_for_write

    body = (
        "Here is the key:\n"
        "-----BEGIN RSA PRIVATE KEY-----\n"
        "MIIEpAIBAAKCAQEA1234567890abcdefGHIJKLMNOPQRSTUV\n"
        "Q29uZ3JhdHVsYXRpb25zIHlvdSBmb3VuZCBhIHNlY3JldA==\n"
        "-----END RSA PRIVATE KEY-----"
    )
    out = _redact_for_write(body)
    assert "MIIEpAIBAAKCAQEA1234567890abcdefGHIJKLMNOPQRSTUV" not in out
    assert "Q29uZ3JhdHVsYXRpb25zIHlvdSBmb3VuZCBhIHNlY3JldA==" not in out
    assert "[redacted" in out


def test_redact_for_write_scrubs_jwt() -> None:
    """A JWT (three base64url segments) must be redacted before disk write."""
    from magi_agent.memory.adapters.local_file_writable import _redact_for_write

    jwt = (
        "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
        ".eyJzdWIiOiIxMjM0NTY3ODkwIiwibmFtZSI6IkpvaG4ifQ"
        ".SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
    )
    out = _redact_for_write(f"session token is {jwt} keep it safe")
    assert jwt not in out
    assert "[redacted" in out


def test_redact_for_write_scrubs_slack_webhook_url() -> None:
    """A Slack incoming-webhook URL must be redacted before disk write."""
    from magi_agent.memory.adapters.local_file_writable import _redact_for_write

    # Assembled at runtime so the source file never contains the contiguous
    # webhook literal (which would trip secret-scanning push protection).
    url = "https://hooks." + "slack.com/services/" + "T00000000/B00000000/" + "X" * 24
    out = _redact_for_write(f"post alerts to {url} thanks")
    assert url not in out
    assert ("B00000000/" + "X" * 24) not in out
    assert "[redacted" in out


def test_redact_for_write_scrubs_dsn_with_inline_password() -> None:
    """A connection DSN with inline user:pass@host must be redacted on write."""
    from magi_agent.memory.adapters.local_file_writable import _redact_for_write

    # An arbitrary scheme not in the existing _SENSITIVE_URL_RE allowlist.
    out = _redact_for_write("connect to amqp://admin:hunter2pass@broker.internal:5672/vhost")
    assert "hunter2pass" not in out
    assert "[redacted" in out


def test_redact_for_write_still_scrubs_existing_vendor_tokens() -> None:
    """Regression: the existing vendor-token shapes are still redacted."""
    from magi_agent.memory.adapters.local_file_writable import _redact_for_write

    out = _redact_for_write(
        "key sk-live-supersecretkey12345 and tok ghp_ABCDEFGHIJ0123456789 "
        "and aws AKIAIOSFODNN7EXAMPLE"
    )
    assert "sk-live-supersecretkey12345" not in out
    assert "ghp_ABCDEFGHIJ0123456789" not in out
    assert "AKIAIOSFODNN7EXAMPLE" not in out


def test_redact_for_write_preserves_ordinary_prose() -> None:
    """Conservative: ordinary prose must survive redaction unchanged."""
    from magi_agent.memory.adapters.local_file_writable import _redact_for_write

    body = "User prefers Korean discussion and English code. Deploy ran fine today."
    out = _redact_for_write(body)
    assert out == body.strip()


def test_redact_for_write_is_redos_safe_on_long_lines() -> None:
    """A pathological long uniform line must not hang the redactor (ReDoS guard).

    The compaction-tree path feeds unbounded tier text through
    ``_redact_for_write``; the per-line token redactors backtrack badly on long
    uniform input, so they must only ever see bounded windows.  The non-secret
    tail must survive (no truncation → no persisted-memory loss).
    """
    import time

    from magi_agent.memory.adapters.local_file_writable import _redact_for_write

    line = "Bearer secret=" + "A" * 200_000 + ":B@" + "x" * 200_000
    start = time.perf_counter()
    out = _redact_for_write(line)
    elapsed = time.perf_counter() - start
    assert elapsed < 2.0, f"redaction took {elapsed:.2f}s — ReDoS guard missing"
    # Tail content is preserved (not truncated).
    assert "x" * 1000 in out


# ---------------------------------------------------------------------------
# PR-D review — write-side must redact the FULL line (no verbatim tail past the
# ReDoS window).  Token-format secrets appearing past column 600, or straddling
# a window boundary, must NOT leak to disk / to the summarizer input.
# ---------------------------------------------------------------------------


def _assert_secret_redacted_past_col(secret: str, col: int = 750) -> None:
    """Place ``secret`` past ``col`` on one long line and assert it is scrubbed."""
    from magi_agent.memory.adapters.local_file_writable import _redact_for_write

    # Non-secret filler that itself contains no token shapes.
    filler = "log line filler text without any credentials here " * 30
    assert len(filler) > col, "filler must push the secret past the target column"
    line = filler[:col] + " " + secret + " trailing context after the secret value"
    assert line.index(secret) > col
    out = _redact_for_write(line)
    assert secret not in out, f"secret leaked past col {col}: {secret!r}"
    assert "[redacted" in out
    # The non-secret remainder must survive (no truncation / data loss).
    assert "trailing context after the secret value" in out


def test_redact_for_write_scrubs_github_token_past_col_700() -> None:
    _assert_secret_redacted_past_col("ghp_ABCDEFGHIJ0123456789KLMNOPQRSTUV")


def test_redact_for_write_scrubs_openai_token_past_col_700() -> None:
    _assert_secret_redacted_past_col("sk-proj-Abc123Def456Ghi789Jkl012Mno345")


def test_redact_for_write_scrubs_aws_key_past_col_700() -> None:
    _assert_secret_redacted_past_col("AKIAIOSFODNN7EXAMPLE")


def test_redact_for_write_scrubs_name_value_secret_past_col_700() -> None:
    _assert_secret_redacted_past_col("api_key=secret123SUPERSECRETvalue456")


def test_redact_for_write_scrubs_bearer_token_past_col_700() -> None:
    from magi_agent.memory.adapters.local_file_writable import _redact_for_write

    token = "AbCdEf0123456789GhIjKlMnOpQrStUvWxYz"
    filler = "log line filler text without any credentials here " * 30
    line = filler[:750] + " Bearer " + token + " trailing context here"
    assert line.index(token) > 750
    out = _redact_for_write(line)
    assert token not in out, "Bearer token leaked past col 750"
    assert "[redacted" in out
    assert "trailing context here" in out


def test_redact_for_write_scrubs_secret_straddling_window_boundary() -> None:
    """A secret starting ~col 580 and extending past the 600 window boundary."""
    from magi_agent.memory.adapters.local_file_writable import (
        _TOKEN_REDACT_WINDOW,
        _redact_for_write,
    )

    boundary = _TOKEN_REDACT_WINDOW  # 600
    secret = "ghp_" + "Z" * 60  # 64 chars, spans from before to after the boundary
    pad = boundary - 20  # secret starts ~20 chars before the window boundary
    filler = "x" * pad
    line = filler + secret + " tail content after straddling secret"
    start = line.index(secret)
    assert start < boundary < start + len(secret), "secret must straddle boundary"
    out = _redact_for_write(line)
    assert secret not in out, "straddling secret leaked"
    assert "[redacted" in out
    assert "tail content after straddling secret" in out


def test_redact_for_write_200k_line_preserves_content_length() -> None:
    """200K uniform line redacts fast AND keeps the non-secret content (no loss)."""
    import time

    from magi_agent.memory.adapters.local_file_writable import _redact_for_write

    # Uniform non-secret content; no token shapes, so nothing should be removed.
    line = "x" * 200_000
    start = time.perf_counter()
    out = _redact_for_write(line)
    elapsed = time.perf_counter() - start
    assert elapsed < 2.0, f"redaction took {elapsed:.2f}s — ReDoS guard missing"
    # No silent truncation / data loss: a non-secret uniform line is preserved.
    assert out == line


# ---------------------------------------------------------------------------
# PR-D review (C1 / I-1) — the FOUR structural regexes (PEM / JWT / WEBHOOK /
# DSN) run on the FULL body (NOT windowed), so each must be ReDoS-safe; and the
# Tier-1 write-side vendor-token coverage must be ≥ the read-side projection.
# ---------------------------------------------------------------------------


def test_redact_for_write_dsn_scheme_is_redos_safe() -> None:
    """C1: an unbounded DSN scheme class backtracked catastrophically.

    ``"QUJjRGVm." * 8000`` (~64KB dotted line, no ``@``) took ~16.7s with the
    unbounded ``[a-z0-9+.-]*://`` scheme.  Bounding the scheme to ``{0,30}`` makes
    the match fail linearly.  RED before the fix (≫ 2s), GREEN after (~0.04s).
    """
    import time

    from magi_agent.memory.adapters.local_file_writable import _redact_for_write

    line = "x=" + "QUJjRGVm." * 8000
    start = time.perf_counter()
    _redact_for_write(line)
    elapsed = time.perf_counter() - start
    assert elapsed < 2.0, f"DSN redaction took {elapsed:.2f}s — ReDoS in scheme class"


def test_redact_for_write_pem_is_redos_safe_on_200k() -> None:
    """C1: a bare ``.*?`` PEM body restarts at every ``-----BEGIN`` marker.

    ``"-----BEGIN PRIVATE KEY-----" * 8000`` (no END marker, ~200KB) cost ~45s
    (O(n^2)) before the tempered + bounded body fix.  RED before, GREEN (~0.001s)
    after.
    """
    import time

    from magi_agent.memory.adapters.local_file_writable import _redact_for_write

    line = ("-----BEGIN PRIVATE KEY-----" * 8000)[:200_000]
    start = time.perf_counter()
    _redact_for_write(line)
    elapsed = time.perf_counter() - start
    assert elapsed < 2.0, f"PEM redaction took {elapsed:.2f}s — ReDoS in body"


def test_redact_for_write_jwt_is_redos_safe_on_200k() -> None:
    """C1 audit: a 200KB ``eyJ…`` run that never completes a 3-segment JWT."""
    import time

    from magi_agent.memory.adapters.local_file_writable import _redact_for_write

    line = ("eyJ" + ("A" * 8 + "." + "A" * 8 + ".") * 8000)[:200_000]
    start = time.perf_counter()
    _redact_for_write(line)
    elapsed = time.perf_counter() - start
    assert elapsed < 2.0, f"JWT redaction took {elapsed:.2f}s — ReDoS"


def test_redact_for_write_webhook_is_redos_safe_on_200k() -> None:
    """C1 audit: a 200KB slack-webhook tail."""
    import time

    from magi_agent.memory.adapters.local_file_writable import _redact_for_write

    line = ("https://hooks.slack.com/services/" + "A" * 200_000)[:200_000]
    start = time.perf_counter()
    _redact_for_write(line)
    elapsed = time.perf_counter() - start
    assert elapsed < 2.0, f"webhook redaction took {elapsed:.2f}s — ReDoS"


def test_redact_for_write_redacts_dotted_openai_token_delimiter_free() -> None:
    """I-1: write<read parity leak — a dotted ``sk-`` token on a delimiter-free line.

    The read-side ``_OPENAI_TOKEN_RE`` (``sk-[A-Za-z0-9._-]+``) allows dots, but the
    write-side Tier-1 vendor pattern excluded ``.``.  On a line with no ``:``/``=``
    delimiter (Tier 2 windowing is skipped), the dotted token passed through
    VERBATIM.  RED before the fix, GREEN after.
    """
    from magi_agent.memory.adapters.local_file_writable import _redact_for_write

    out = _redact_for_write("sk-abc.def.ghi.jkl.mnopqr")
    assert "sk-abc.def.ghi.jkl.mnopqr" not in out
    assert "[redacted" in out


def test_redact_for_write_dotted_openai_token_matches_read_side() -> None:
    """I-1: write-side coverage of dotted ``sk-`` must be ≥ read-side projection."""
    from magi_agent.memory.adapters.local_file_writable import _redact_for_write
    from magi_agent.transport.tool_preview import redact_secret_tokens

    token = "sk-abc.def.ghi.jkl.mnopqr"
    # Read-side redacts it; write-side must too (delimiter-free line).
    assert token not in redact_secret_tokens(token)
    assert token not in _redact_for_write(token)


def test_redact_for_write_dotted_token_does_not_mangle_prose() -> None:
    """I-1 guard: the ``\\bsk-`` anchor must not fire inside hyphenated prose."""
    from magi_agent.memory.adapters.local_file_writable import _redact_for_write

    body = "the task-list and disk-usage and risk-averse review went fine"
    out = _redact_for_write(body)
    assert out == body.strip()


def test_local_file_provider_delete_always_raises(tmp_path: Path) -> None:
    """delete() raises regardless of write_enabled — no destructive operations."""
    from magi_agent.memory.adapters.local_file_writable import (
        LocalFileMemoryProvider,
        LocalFileMemoryConfig,
    )

    config = LocalFileMemoryConfig(workspace_root=tmp_path, enabled=True, write_enabled=True)
    provider = LocalFileMemoryProvider(config)

    with pytest.raises(UnsupportedMemoryOperationError):
        asyncio.run(provider.delete("some-record-id"))


def test_local_file_provider_capabilities_declare_gated_write_when_enabled(
    tmp_path: Path,
) -> None:
    """capabilities() returns write_tier='gated_write' when write is enabled."""
    from magi_agent.memory.adapters.local_file_writable import (
        LocalFileMemoryProvider,
        LocalFileMemoryConfig,
    )

    config = LocalFileMemoryConfig(workspace_root=tmp_path, enabled=True, write_enabled=True)
    provider = LocalFileMemoryProvider(config)

    caps = provider.capabilities()
    assert caps.supports_write is True
    assert caps.write_tier == "gated_write"
    assert caps.max_write_bytes > 0


def test_local_file_provider_capabilities_are_read_only_when_write_disabled(
    tmp_path: Path,
) -> None:
    """When write_enabled=False, capabilities() reports supports_write=False."""
    from magi_agent.memory.adapters.local_file_writable import (
        LocalFileMemoryProvider,
        LocalFileMemoryConfig,
    )

    config = LocalFileMemoryConfig(workspace_root=tmp_path, enabled=True, write_enabled=False)
    provider = LocalFileMemoryProvider(config)

    caps = provider.capabilities()
    assert caps.supports_write is False
    assert caps.write_tier == "read_only"
    assert caps.max_write_bytes == 0


# ---------------------------------------------------------------------------
# D. Gate-off via env: MAGI_MEMORY_WRITE_ENABLED not set → write inert
# ---------------------------------------------------------------------------


def test_local_file_provider_env_gate_off_by_default(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without MAGI_MEMORY_WRITE_ENABLED=1, write is inert even if not explicitly set."""
    from magi_agent.memory.adapters.local_file_writable import (
        LocalFileMemoryProvider,
        LocalFileMemoryConfig,
        MAGI_MEMORY_WRITE_ENABLED_ENV,
    )

    monkeypatch.delenv(MAGI_MEMORY_WRITE_ENABLED_ENV, raising=False)
    # Config does not set write_enabled — default should be False
    config = LocalFileMemoryConfig(workspace_root=tmp_path, enabled=True)
    provider = LocalFileMemoryProvider(config)

    with pytest.raises(UnsupportedMemoryOperationError):
        asyncio.run(provider.remember({"body": "should be blocked"}))


def test_local_file_provider_env_gate_on_enables_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With MAGI_MEMORY_WRITE_ENABLED=1, write is live (env-driven gate)."""
    from magi_agent.memory.adapters.local_file_writable import (
        LocalFileMemoryProvider,
        LocalFileMemoryConfig,
        MAGI_MEMORY_WRITE_ENABLED_ENV,
    )

    monkeypatch.setenv(MAGI_MEMORY_WRITE_ENABLED_ENV, "1")
    config = LocalFileMemoryConfig(workspace_root=tmp_path, enabled=True)
    provider = LocalFileMemoryProvider(config)

    asyncio.run(provider.remember({
        "body": "Env-gated write should land.",
        "target_file": "MEMORY.md",
    }))

    content = (tmp_path / "MEMORY.md").read_text(encoding="utf-8")
    assert "Env-gated write should land" in content


# ---------------------------------------------------------------------------
# E. New quality-review tests (D1 code-quality pass)
# ---------------------------------------------------------------------------


def test_cumulative_file_cap_raises_when_exceeded(tmp_path: Path) -> None:
    """Repeated small writes that exceed max_file_bytes must raise ValueError."""
    from magi_agent.memory.adapters.local_file_writable import (
        LocalFileMemoryProvider,
        LocalFileMemoryConfig,
    )

    # Set a very small cumulative cap (256 bytes)
    config = LocalFileMemoryConfig(
        workspace_root=tmp_path,
        enabled=True,
        write_enabled=True,
        max_file_bytes=256,
    )
    provider = LocalFileMemoryProvider(config)

    # Write entries until we hit the cap
    hit_cap = False
    for i in range(50):
        try:
            asyncio.run(provider.remember({
                "body": f"Entry number {i} with some padding text to fill bytes.",
                "kind": "note",
                "target_file": "MEMORY.md",
            }))
        except ValueError as exc:
            assert "max_file_bytes" in str(exc)
            hit_cap = True
            break

    assert hit_cap, "Expected ValueError for exceeding cumulative file cap"
    # Verify the file is not grown past the cap
    actual_size = (tmp_path / "MEMORY.md").stat().st_size
    assert actual_size <= 256


def test_cumulative_file_cap_default_is_4mib(tmp_path: Path) -> None:
    """Default max_file_bytes is 4 MiB (4_194_304)."""
    from magi_agent.memory.adapters.local_file_writable import LocalFileMemoryConfig

    config = LocalFileMemoryConfig(workspace_root=tmp_path)
    assert config.max_file_bytes == 4_194_304


def test_extract_target_file_raises_on_unknown_target(tmp_path: Path) -> None:
    """remember() with an unknown target_file raises ValueError (not silently redirects)."""
    from magi_agent.memory.adapters.local_file_writable import (
        LocalFileMemoryProvider,
        LocalFileMemoryConfig,
    )

    config = LocalFileMemoryConfig(workspace_root=tmp_path, enabled=True, write_enabled=True)
    provider = LocalFileMemoryProvider(config)

    with pytest.raises(ValueError, match="unknown write target"):
        asyncio.run(provider.remember({
            "body": "should not be written",
            "target_file": "notes.md",
        }))


def test_extract_target_file_raises_on_path_traversal(tmp_path: Path) -> None:
    """Path-traversal attempts produce a basename not in allowlist → raises ValueError."""
    from magi_agent.memory.adapters.local_file_writable import (
        LocalFileMemoryProvider,
        LocalFileMemoryConfig,
    )

    config = LocalFileMemoryConfig(workspace_root=tmp_path, enabled=True, write_enabled=True)
    provider = LocalFileMemoryProvider(config)

    with pytest.raises(ValueError, match="unknown write target"):
        asyncio.run(provider.remember({
            "body": "should not be written",
            "target_file": "../../etc/passwd",
        }))


def test_extract_target_file_allows_memory_md(tmp_path: Path) -> None:
    """MEMORY.md remains a valid write target after the allowlist change."""
    from magi_agent.memory.adapters.local_file_writable import (
        LocalFileMemoryProvider,
        LocalFileMemoryConfig,
    )

    config = LocalFileMemoryConfig(workspace_root=tmp_path, enabled=True, write_enabled=True)
    provider = LocalFileMemoryProvider(config)

    asyncio.run(provider.remember({"body": "valid write", "target_file": "MEMORY.md"}))
    assert (tmp_path / "MEMORY.md").exists()


def test_extract_target_file_allows_user_md(tmp_path: Path) -> None:
    """USER.md remains a valid write target after the allowlist change."""
    from magi_agent.memory.adapters.local_file_writable import (
        LocalFileMemoryProvider,
        LocalFileMemoryConfig,
    )

    config = LocalFileMemoryConfig(workspace_root=tmp_path, enabled=True, write_enabled=True)
    provider = LocalFileMemoryProvider(config)

    asyncio.run(provider.remember({"body": "valid write", "target_file": "USER.md"}))
    assert (tmp_path / "USER.md").exists()


def test_secret_looking_kind_is_redacted_on_disk(tmp_path: Path) -> None:
    """A secret-looking kind value (e.g. sk-live-...) is redacted in the [label] on disk."""
    from magi_agent.memory.adapters.local_file_writable import (
        LocalFileMemoryProvider,
        LocalFileMemoryConfig,
    )

    config = LocalFileMemoryConfig(workspace_root=tmp_path, enabled=True, write_enabled=True)
    provider = LocalFileMemoryProvider(config)

    asyncio.run(provider.remember({
        "body": "some innocuous content",
        "kind": "sk-live-supersecretkey12345",
        "target_file": "MEMORY.md",
    }))

    content = (tmp_path / "MEMORY.md").read_text(encoding="utf-8")
    # The secret pattern should not appear verbatim in the [kind] label
    assert "sk-live-supersecretkey12345" not in content
    assert "[redacted]" in content
