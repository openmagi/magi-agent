"""Integration tests for the fuzzy-edit cascade wired into gate5b FileEdit.

Four cases (per spec):
  (a) old_text with wrong indentation succeeds when flag is ON.
  (b) Genuinely absent old_text → old_text_not_found.
  (c) Ambiguous duplicate region → old_text_not_unique.
  (d) Flag OFF preserves exact-only behaviour (indentation mismatch fails).

PR1 extensions:
  (e) FileEdit attaches an EditMatch receipt with the right tier.
  (f) Low-tier edit under block_final_answer enforcement triggers requirement.
  (g) Under flag "off" enforcement a low-tier edit does NOT block.
"""
from __future__ import annotations

import os
import importlib
import pytest

from magi_agent.gates.gate5b_full_toolhost import (
    GATE5B_FULL_TOOLHOST_TOOL_NAMES,
    Gate5BFullToolHostConfig,
    build_gate5b_full_toolhost_bundle,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sha256(value: str) -> str:
    import hashlib
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _ready_bundle(tmp_path, *, extra_config: dict | None = None):
    """Build a ready gate5b bundle targeting tmp_path."""
    config_data = {
        "enabled": True,
        "killSwitchEnabled": False,
        "routeAttachmentEnabled": True,
        "selectedBotDigest": _sha256("bot-fuzzy-test"),
        "selectedOwnerDigest": _sha256("user-fuzzy-test"),
        "environment": "production",
        "environmentAllowlist": ("production",),
        "allowedToolNames": GATE5B_FULL_TOOLHOST_TOOL_NAMES,
        "maxToolCallsPerTurn": 16,
    }
    if extra_config:
        config_data.update(extra_config)
    config = Gate5BFullToolHostConfig.model_validate(config_data)
    scope = {
        "selectedBotDigest": _sha256("bot-fuzzy-test"),
        "selectedOwnerDigest": _sha256("user-fuzzy-test"),
        "environment": "production",
    }
    return build_gate5b_full_toolhost_bundle(
        config=config,
        scope=scope,
        workspace_root=tmp_path,
        read_ledger_enabled=False,
    )


def _write_file(tmp_path, name: str, content: str) -> None:
    target = tmp_path / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


# ---------------------------------------------------------------------------
# (a) Flag ON — indentation mismatch is absorbed by fuzzy match
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fuzzy_edit_flag_on_indentation_mismatch_succeeds(tmp_path, monkeypatch):
    """FileEdit with wrong indentation in old_text succeeds when flag is ON."""
    monkeypatch.setenv("MAGI_EDIT_FUZZY_MATCH_ENABLED", "1")

    # gate5b now reads the flag from the env at call time.
    monkeypatch.setenv("MAGI_EDIT_FUZZY_MATCH_ENABLED", "1")

    bundle = _ready_bundle(tmp_path)
    assert bundle.status == "ready"

    content = (
        "def greet(name):\n"
        "    message = 'Hello, ' + name\n"
        "    return message\n"
    )
    _write_file(tmp_path, "greet.py", content)

    # old_text uses 2-space indentation instead of 4-space (intentional mismatch)
    old_text_wrong_indent = (
        "def greet(name):\n"
        "  message = 'Hello, ' + name\n"
        "  return message\n"
    )
    new_text = (
        "def greet(name):\n"
        "    message = 'Hi, ' + name\n"
        "    return message\n"
    )

    outcome = await bundle.host.dispatch(
        "FileEdit",
        {"path": "greet.py", "oldText": old_text_wrong_indent, "newText": new_text},
        request_digest=_sha256("req-a-1"),
        tool_call_id="call-a-1",
    )

    assert outcome.status == "ok", f"Expected ok, got {outcome.status}: {outcome.reason}"
    result = (tmp_path / "greet.py").read_text(encoding="utf-8")
    assert "Hi, " in result, "Replacement should have been applied"
    assert "Hello, " not in result, "Original should have been replaced"


# ---------------------------------------------------------------------------
# (b) Flag ON — genuinely absent old_text → old_text_not_found
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fuzzy_edit_flag_on_absent_old_text_returns_not_found(tmp_path, monkeypatch):
    """FileEdit with genuinely absent old_text → error status (old_text_not_found)."""
    monkeypatch.setenv("MAGI_EDIT_FUZZY_MATCH_ENABLED", "1")

    bundle = _ready_bundle(tmp_path)
    _write_file(tmp_path, "data.py", "x = 1\ny = 2\n")

    outcome = await bundle.host.dispatch(
        "FileEdit",
        {
            "path": "data.py",
            "oldText": "this_text_does_not_exist_anywhere_in_the_file\n",
            "newText": "replaced\n",
        },
        request_digest=_sha256("req-b-1"),
        tool_call_id="call-b-1",
    )

    # gate5b maps ValueError to status="error"
    assert outcome.status == "error", f"Expected error, got {outcome.status}"
    # File must be unchanged — confirms the error was a match failure, not a
    # partial write followed by an error (which would be a worse outcome).
    assert (tmp_path / "data.py").read_text(encoding="utf-8") == "x = 1\ny = 2\n"


@pytest.mark.asyncio
async def test_fuzzy_edit_handle_absent_old_text_raises_old_text_not_found(tmp_path, monkeypatch):
    """Direct unit test: _handle raises ValueError('old_text_not_found') on NoMatchError.

    This supplements the integration test above by asserting the *specific*
    error code rather than just a generic status=="error", which any ValueError
    would produce.  If the wiring were broken (e.g. NoMatchError swallowed
    silently, or mapped to a different code), this test fails.
    """
    monkeypatch.setenv("MAGI_EDIT_FUZZY_MATCH_ENABLED", "1")

    bundle = _ready_bundle(tmp_path)
    _write_file(tmp_path, "data2.py", "x = 1\ny = 2\n")

    with pytest.raises(ValueError, match="old_text_not_found"):
        await bundle.host._handle(
            "FileEdit",
            {
                "path": "data2.py",
                "oldText": "this_text_does_not_exist_anywhere_in_the_file\n",
                "newText": "replaced\n",
            },
            tool_call_id="call-b-direct",
        )


@pytest.mark.asyncio
async def test_fuzzy_edit_handle_ambiguous_raises_old_text_not_unique(tmp_path, monkeypatch):
    """Direct unit test: _handle raises ValueError('old_text_not_unique') on MultipleMatchesError.

    Complements test (c) — asserts the specific error code, which would fail if
    MultipleMatchesError were mapped to 'old_text_not_found' or silently ignored.
    """
    monkeypatch.setenv("MAGI_EDIT_FUZZY_MATCH_ENABLED", "1")

    bundle = _ready_bundle(tmp_path)

    repeated_block = "    def process(self):\n        pass\n"
    content = repeated_block + "\n" + repeated_block
    _write_file(tmp_path, "service2.py", content)

    with pytest.raises(ValueError, match="old_text_not_unique"):
        await bundle.host._handle(
            "FileEdit",
            {
                "path": "service2.py",
                "oldText": "def process(self):\n    pass\n",
                "newText": "def process(self):\n    return True\n",
            },
            tool_call_id="call-c-direct",
        )


# ---------------------------------------------------------------------------
# (c) Flag ON — ambiguous duplicate region → old_text_not_unique
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fuzzy_edit_flag_on_ambiguous_duplicate_returns_not_unique(tmp_path, monkeypatch):
    """FileEdit with ambiguous duplicate → error status (old_text_not_unique)."""
    monkeypatch.setenv("MAGI_EDIT_FUZZY_MATCH_ENABLED", "1")

    bundle = _ready_bundle(tmp_path)

    # File has two identical blocks
    repeated_block = "    def process(self):\n        pass\n"
    content = repeated_block + "\n" + repeated_block
    _write_file(tmp_path, "service.py", content)

    # old_text with stripped indentation — will fuzzy-match both occurrences
    old_text_stripped = "def process(self):\n    pass\n"

    outcome = await bundle.host.dispatch(
        "FileEdit",
        {
            "path": "service.py",
            "oldText": old_text_stripped,
            "newText": "def process(self):\n    return True\n",
        },
        request_digest=_sha256("req-c-1"),
        tool_call_id="call-c-1",
    )

    assert outcome.status == "error", f"Expected error, got {outcome.status}"
    # File unchanged
    assert (tmp_path / "service.py").read_text(encoding="utf-8") == content


# ---------------------------------------------------------------------------
# (d) Flag OFF — exact-only behaviour, indentation mismatch fails
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fuzzy_edit_flag_off_preserves_exact_only_behavior(tmp_path, monkeypatch):
    """Flag OFF: indentation-mismatched old_text fails as before."""
    monkeypatch.setenv("MAGI_EDIT_FUZZY_MATCH_ENABLED", "0")

    bundle = _ready_bundle(tmp_path)
    content = "def hello():\n    return 'world'\n"
    _write_file(tmp_path, "hello.py", content)

    # old_text has wrong indentation (2-space vs 4-space)
    wrong_indent = "def hello():\n  return 'world'\n"

    outcome = await bundle.host.dispatch(
        "FileEdit",
        {
            "path": "hello.py",
            "oldText": wrong_indent,
            "newText": "def hello():\n    return 'earth'\n",
        },
        request_digest=_sha256("req-d-1"),
        tool_call_id="call-d-1",
    )

    assert outcome.status == "error", (
        f"Flag OFF should fail on indentation mismatch, got {outcome.status}"
    )
    # File unchanged
    assert (tmp_path / "hello.py").read_text(encoding="utf-8") == content


# ---------------------------------------------------------------------------
# (d-extra) Flag OFF — exact match still works
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fuzzy_edit_flag_off_exact_match_still_works(tmp_path, monkeypatch):
    """Flag OFF: exact old_text succeeds (no regression on happy path)."""
    monkeypatch.setenv("MAGI_EDIT_FUZZY_MATCH_ENABLED", "0")

    bundle = _ready_bundle(tmp_path)
    content = "def hello():\n    return 'world'\n"
    _write_file(tmp_path, "hello2.py", content)

    outcome = await bundle.host.dispatch(
        "FileEdit",
        {
            "path": "hello2.py",
            "oldText": "return 'world'",
            "newText": "return 'earth'",
        },
        request_digest=_sha256("req-d-2"),
        tool_call_id="call-d-2",
    )

    assert outcome.status == "ok", f"Exact match should succeed, got {outcome.status}"
    assert "earth" in (tmp_path / "hello2.py").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# (e) PR1: FileEdit with fuzzy match attaches EditMatch receipt
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fuzzy_edit_attaches_edit_match_receipt(tmp_path, monkeypatch):
    """PR1: A successful fuzzy FileEdit attaches an EditMatchReceiptRecord."""
    monkeypatch.setenv("MAGI_EDIT_FUZZY_MATCH_ENABLED", "1")

    bundle = _ready_bundle(tmp_path)
    content = (
        "def greet(name):\n"
        "    message = 'Hello, ' + name\n"
        "    return message\n"
    )
    _write_file(tmp_path, "greet2.py", content)

    old_text_wrong_indent = (
        "def greet(name):\n"
        "  message = 'Hello, ' + name\n"
        "  return message\n"
    )
    new_text = (
        "def greet(name):\n"
        "    message = 'Hi, ' + name\n"
        "    return message\n"
    )

    outcome = await bundle.host.dispatch(
        "FileEdit",
        {"path": "greet2.py", "oldText": old_text_wrong_indent, "newText": new_text},
        request_digest=_sha256("req-e-1"),
        tool_call_id="call-e-1",
    )

    assert outcome.status == "ok", f"Expected ok, got {outcome.status}: {outcome.reason}"
    assert outcome.edit_match_receipt is not None, "EditMatch receipt should be attached"
    receipt = outcome.edit_match_receipt
    assert receipt.type == "EditMatch"
    # line_trimmed fires before indentation_flexible for pure indentation diffs
    assert receipt.tier in ("line_trimmed", "indentation_flexible")
    assert receipt.file_digest.startswith("sha256:")
    assert receipt.span_digest.startswith("sha256:")


@pytest.mark.asyncio
async def test_exact_fuzzy_edit_attaches_receipt_with_simple_tier(tmp_path, monkeypatch):
    """PR1: Exact match via simple tier also produces a receipt (tier=simple)."""
    monkeypatch.setenv("MAGI_EDIT_FUZZY_MATCH_ENABLED", "1")

    bundle = _ready_bundle(tmp_path)
    _write_file(tmp_path, "simple.py", "x = 1\ny = 2\n")

    outcome = await bundle.host.dispatch(
        "FileEdit",
        {"path": "simple.py", "oldText": "x = 1", "newText": "x = 10"},
        request_digest=_sha256("req-e-2"),
        tool_call_id="call-e-2",
    )

    assert outcome.status == "ok"
    assert outcome.edit_match_receipt is not None
    assert outcome.edit_match_receipt.tier == "simple"
    assert outcome.edit_match_receipt.confidence == 1.0


@pytest.mark.asyncio
async def test_flag_off_no_edit_match_receipt(tmp_path, monkeypatch):
    """PR1: With flag OFF the edit_match_receipt is None (exact-only path)."""
    monkeypatch.setenv("MAGI_EDIT_FUZZY_MATCH_ENABLED", "0")

    bundle = _ready_bundle(tmp_path)
    _write_file(tmp_path, "exact.py", "a = 1\n")

    outcome = await bundle.host.dispatch(
        "FileEdit",
        {"path": "exact.py", "oldText": "a = 1", "newText": "a = 2"},
        request_digest=_sha256("req-e-3"),
        tool_call_id="call-e-3",
    )

    assert outcome.status == "ok"
    assert outcome.edit_match_receipt is None, (
        "Flag OFF must not produce an EditMatch receipt"
    )


# ---------------------------------------------------------------------------
# (f/g) PR1: build_edit_confidence_contract enforcement tests
# ---------------------------------------------------------------------------


class TestBuildEditConfidenceContract:
    """Tests for build_edit_confidence_contract() in coding_verification.py."""

    def _make_low_tier_match(self):
        """Return an EditMatchResult from a context_aware match (low-confidence tier)."""
        # Build a context_aware match
        content = (
            "class Foo:\n"
            "    def bar(self):\n"
            "        x = 1\n"
            "        y = 2\n"
            "        return x\n"
        )
        find = (
            "class Foo:\n"
            "    def baz(self):\n"
            "        x = 1\n"
            "        y = 2\n"
            "        return x\n"
        )
        new = (
            "class Foo:\n"
            "    def bar(self):\n"
            "        x = 10\n"
            "        y = 20\n"
            "        return x\n"
        )
        from magi_agent.coding.edit_matching import replace
        return replace(content, find, new)

    def _make_high_tier_match(self):
        """Return an EditMatchResult from a simple (high-confidence) match."""
        from magi_agent.coding.edit_matching import replace
        return replace("hello world\n", "hello", "goodbye")

    def test_high_confidence_tier_uses_audit(self):
        from magi_agent.evidence.coding_verification import build_edit_confidence_contract
        match = self._make_high_tier_match()
        contract = build_edit_confidence_contract(
            match,
            last_code_mutation_at=1000.0,
            enforcement="block_final_answer",
        )
        # High-confidence tier (simple) must NOT block even with block_final_answer
        assert contract.on_missing == "audit"

    def test_low_confidence_tier_block_mode_uses_block_final_answer(self):
        from magi_agent.evidence.coding_verification import build_edit_confidence_contract
        match = self._make_low_tier_match()
        # block_anchor fires before context_aware for this content; both are
        # low-confidence tiers that trigger block_final_answer when enforcement is on.
        assert match.tier in ("context_aware", "block_anchor"), (
            f"Expected a low-confidence tier, got {match.tier}"
        )
        contract = build_edit_confidence_contract(
            match,
            last_code_mutation_at=1000.0,
            enforcement="block_final_answer",
        )
        assert contract.on_missing == "block_final_answer"

    def test_low_confidence_tier_off_mode_uses_audit(self):
        from magi_agent.evidence.coding_verification import build_edit_confidence_contract
        match = self._make_low_tier_match()
        contract = build_edit_confidence_contract(
            match,
            last_code_mutation_at=1000.0,
            enforcement="off",
        )
        # With enforcement="off" nothing should block
        assert contract.on_missing == "audit"

    def test_contract_includes_edit_match_requirement(self):
        from magi_agent.evidence.coding_verification import build_edit_confidence_contract
        match = self._make_high_tier_match()
        contract = build_edit_confidence_contract(
            match,
            last_code_mutation_at=1000.0,
        )
        req_types = {req.type for req in contract.requirements}
        assert "EditMatch" in req_types

    def test_contract_includes_git_diff_and_test_run(self):
        from magi_agent.evidence.coding_verification import build_edit_confidence_contract
        match = self._make_low_tier_match()
        contract = build_edit_confidence_contract(
            match,
            last_code_mutation_at=1000.0,
            enforcement="block_final_answer",
        )
        req_types = {req.type for req in contract.requirements}
        assert "GitDiff" in req_types
        assert "TestRun" in req_types

    def test_default_enforcement_is_off(self):
        """Default enforcement="off" means audit only, never block."""
        from magi_agent.evidence.coding_verification import build_edit_confidence_contract
        match = self._make_low_tier_match()
        # No enforcement kwarg — defaults to "off"
        contract = build_edit_confidence_contract(match, last_code_mutation_at=1000.0)
        assert contract.on_missing == "audit"
