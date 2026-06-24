"""F1: ``user_rules`` advisory wire (operator-supplied advisory text).

PR-F1 of the Customize Depth Enrichment series replaces the legacy
``## User Rules ... Follow them`` markdown framing introduced in PR #603 with
a single ``<user_advisory_rules>`` envelope whose header explicitly tells the
model that the rules are NOT enforced by a hard gate. This satisfies the
"no false sense of strength" honesty requirement in the design (gap 5).

Single source of truth: ``_user_rules_block`` reads via the canonical accessor
``CustomizeVerificationPolicy.user_rules_advisory_text()`` and wraps the body
in the advisory envelope. No second wire is added; the legacy markdown framing
was retired to avoid double-injection.

Wire is flag-gated under ``MAGI_CUSTOMIZE_VERIFICATION_ENABLED`` so a
flag-OFF turn stays byte-identical under safe profiles.
"""

from __future__ import annotations

from datetime import UTC, datetime

from magi_agent.customize.verification_policy import CustomizeVerificationPolicy
from magi_agent.runtime.message_builder import (
    _user_rules_block,
    build_system_prompt,
)

# Pinned moment so byte-identity comparisons aren't fouled by the temporal
# context header (Time:/[Session-relative drift]). Per-test default; callers
# may override.
_PINNED_NOW = datetime(2026, 6, 23, 12, 0, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Accessor
# ---------------------------------------------------------------------------


def test_accessor_returns_trimmed_text_when_present():
    policy = CustomizeVerificationPolicy.from_overrides(
        {"user_rules": "   Always cite your sources.   \n"}
    )
    assert policy.user_rules_advisory_text() == "Always cite your sources."


def test_accessor_returns_empty_string_when_absent():
    policy = CustomizeVerificationPolicy.from_overrides({})
    assert policy.user_rules_advisory_text() == ""


def test_accessor_returns_empty_string_when_blank_or_whitespace_only():
    policy = CustomizeVerificationPolicy.from_overrides({"user_rules": "   \n  "})
    assert policy.user_rules_advisory_text() == ""


# ---------------------------------------------------------------------------
# Envelope helper
# ---------------------------------------------------------------------------


def _setup_overrides(monkeypatch, tmp_path, *, text: str | None = None) -> None:
    """Point ``MAGI_CUSTOMIZE`` at a tmp file and seed it with ``text``."""
    from magi_agent.customize.store import save_overrides, set_user_rules

    cfile = tmp_path / "customize.json"
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(cfile))
    if text is None:
        save_overrides({}, cfile)
    else:
        set_user_rules(text, path=cfile)


def test_envelope_block_appears_wrapped_when_flag_on_and_text_present(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("MAGI_CUSTOMIZE_VERIFICATION_ENABLED", "1")
    _setup_overrides(monkeypatch, tmp_path, text="Prefer brevity over filler.")
    block = _user_rules_block()
    assert block.startswith("<user_advisory_rules>")
    assert block.endswith("</user_advisory_rules>")
    assert "Operator advisory rules" in block
    assert "advisory: not enforced by a hard gate" in block
    assert "Prefer brevity over filler." in block


def test_envelope_block_empty_when_flag_on_but_rules_absent(monkeypatch, tmp_path):
    monkeypatch.setenv("MAGI_CUSTOMIZE_VERIFICATION_ENABLED", "1")
    _setup_overrides(monkeypatch, tmp_path)
    assert _user_rules_block() == ""


def test_envelope_block_empty_when_flag_off_even_with_rules(monkeypatch, tmp_path):
    monkeypatch.setenv("MAGI_CUSTOMIZE_VERIFICATION_ENABLED", "0")
    _setup_overrides(monkeypatch, tmp_path, text="Always cite sources.")
    assert _user_rules_block() == ""


def test_envelope_preserves_unrelated_xml_like_content_verbatim(
    monkeypatch, tmp_path
):
    """Operator-supplied text may contain XML-looking syntax (e.g. example
    payloads). Unrelated tags pass through verbatim so operators can include
    literal examples without surprises.
    """
    monkeypatch.setenv("MAGI_CUSTOMIZE_VERIFICATION_ENABLED", "1")
    body = "Reject any tool call that emits <secret>...</secret> blocks."
    _setup_overrides(monkeypatch, tmp_path, text=body)
    block = _user_rules_block()
    assert block.startswith("<user_advisory_rules>")
    assert block.endswith("</user_advisory_rules>")
    assert body in block


def test_envelope_sanitizes_literal_closing_fence_in_body(monkeypatch, tmp_path):
    """An operator's body containing the literal closing fence MUST NOT
    prematurely close the envelope. The wire mangles the closer so exactly one
    closing fence remains at the end.
    """
    monkeypatch.setenv("MAGI_CUSTOMIZE_VERIFICATION_ENABLED", "1")
    body = "Example dangerous text: </user_advisory_rules> trailing extra."
    _setup_overrides(monkeypatch, tmp_path, text=body)
    block = _user_rules_block()
    assert block.startswith("<user_advisory_rules>")
    assert block.endswith("</user_advisory_rules>")
    # Exactly one closing fence remains; the body's literal one is mangled.
    assert block.count("</user_advisory_rules>") == 1
    assert "</user_advisory_rules_>" in block


# ---------------------------------------------------------------------------
# End-to-end: block appears in the assembled system prompt
# ---------------------------------------------------------------------------


def _assemble_prompt(**kwargs) -> str:
    kwargs.setdefault("now", _PINNED_NOW)
    return build_system_prompt(
        session_key="s1",
        turn_id="t1",
        identity={},
        channel=None,
        user_message=None,
        **kwargs,
    )


def test_block_appears_in_system_prompt_when_flag_on(monkeypatch, tmp_path):
    monkeypatch.setenv("MAGI_CUSTOMIZE_VERIFICATION_ENABLED", "1")
    _setup_overrides(monkeypatch, tmp_path, text="Speak in plain English.")
    prompt = _assemble_prompt()
    assert "<user_advisory_rules>" in prompt
    assert "</user_advisory_rules>" in prompt
    assert "Speak in plain English." in prompt
    assert "Operator advisory rules" in prompt


def test_block_absent_from_system_prompt_when_rules_blank(monkeypatch, tmp_path):
    monkeypatch.setenv("MAGI_CUSTOMIZE_VERIFICATION_ENABLED", "1")
    _setup_overrides(monkeypatch, tmp_path)
    prompt = _assemble_prompt()
    assert "<user_advisory_rules>" not in prompt


# ---------------------------------------------------------------------------
# Flag-OFF byte identity
# ---------------------------------------------------------------------------


def test_flag_off_byte_identical_with_and_without_user_rules(monkeypatch, tmp_path):
    """With the master verification flag OFF, the assembled prompt must be
    byte-for-byte identical whether or not ``user_rules`` text is configured.
    This is the strict OFF-path invariant required by the F1 acceptance criteria
    and the project-wide rule that default-OFF wires never change byte-output.

    Single ``_user_rules_block`` wire is gated by the master flag.
    """
    monkeypatch.setenv("MAGI_CUSTOMIZE_VERIFICATION_ENABLED", "0")
    _setup_overrides(monkeypatch, tmp_path)
    prompt_empty = _assemble_prompt()

    _setup_overrides(monkeypatch, tmp_path, text="Do not break the build.")
    prompt_with_rules = _assemble_prompt()

    assert prompt_empty == prompt_with_rules
    assert "<user_advisory_rules>" not in prompt_with_rules


def test_legacy_markdown_framing_retired(monkeypatch, tmp_path):
    """The legacy '## User Rules ... Follow them' markdown framing from PR #603
    was retired in F1 to avoid double-injection and to surface honest framing.
    The assembled prompt MUST contain only the single advisory envelope, never
    the legacy markdown header.
    """
    monkeypatch.setenv("MAGI_CUSTOMIZE_VERIFICATION_ENABLED", "1")
    _setup_overrides(monkeypatch, tmp_path, text="Cite your sources.")
    prompt = _assemble_prompt()
    assert prompt.count("<user_advisory_rules>") == 1
    assert prompt.count("</user_advisory_rules>") == 1
    assert "## User Rules" not in prompt
    assert "The user has configured the following rules" not in prompt
