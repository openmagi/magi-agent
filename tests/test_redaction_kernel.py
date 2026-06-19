"""C-1 redaction-kernel parity + behavior tests.

The single redaction home is ``magi_agent/ops/safety.py``. Before any local
``_PRIVATE_TEXT_RE``/``_SECRET_*_RE`` copy is deleted, the kernel must prove it
catches the *union* of every distinctive token shape the copies caught. This
file enumerates each distinctive token family (AKIA, xox, AIza, gh[opusr]_,
github_pat_, sk-, sk_live_, rk_, JWT, x-amz-signature, signed_url, bearer,
basic-auth, private path) and asserts the kernel both flags it
(``contains_secret_marker``) and scrubs it (``redact_private_text`` leaves no
raw token substring) and raises on it (``reject_private_text``), so the three
forms share one pattern set and cannot drift.
"""

from __future__ import annotations

import pytest

from magi_agent.ops.safety import (
    MAX_PUBLIC_TEXT_CHARS,
    contains_secret_marker,
    redact_private_text,
    reject_private_text,
    safe_public_ref,
)


def _t(*parts: str) -> str:
    """Assemble a secret-shaped TEST fixture from fragments at runtime.

    No contiguous provider-pattern literal lives in source, so GitHub
    push-protection / secret-scanning never flags these *fake* fixtures, while
    the redaction regex still sees the full joined token at test time.
    """
    return "".join(parts)


_SK_LIVE = _t("sk_", "live_", "abcdEFGH1234", "ijklMNOP5678")
_SK_TEST = _t("sk_", "test_", "abcdEFGH1234", "ijklMNOP5678")
_RK_LIVE = _t("rk_", "live_", "abcdEFGH1234", "ijklMNOP")
_AKIA = _t("AKIA", "IOSFODNN7", "EXAMPLE")
_AIZA = _t("AIza", "SyA1234567890", "abcdefghijklmnopqrstuv")
_XOXB = _t("xoxb", "-1234567890-", "abcdefghijklmnop")
_GHP = _t("ghp_", "abcdEFGH1234", "ijklMNOP5678qrstUVWX")
_GH_PAT = _t("github_", "pat_", "abcdEFGH1234ijklMNOP")
_JWT = _t("eyJ", "hbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9", ".eyJzdWIiOiIxMjM0NTY3ODkwIn0", ".dQw4w9WgXcQabcdEFGH12")
_JWT_HEAD = _t("eyJ", "hbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9")


# Each entry is (label, sample_text, raw_token_substr_that_must_not_survive).
_SECRET_SAMPLES = [
    ("stripe_sk_live", f"key {_SK_LIVE}", _SK_LIVE),
    ("stripe_sk_test", f"key {_SK_TEST}", _SK_TEST),
    ("openai_sk", "token sk-abcdEFGH1234ijklMNOP", "sk-abcdEFGH1234ijklMNOP"),
    ("stripe_rk", f"restricted {_RK_LIVE}", _RK_LIVE),
    ("aws_akia", f"creds {_AKIA} here", _AKIA),
    ("google_aiza", f"g {_AIZA}", _AIZA),
    ("slack_xoxb", f"slack {_XOXB}", _XOXB),
    ("github_ghp", f"gh {_GHP}", _GHP),
    ("github_pat", f"gh {_GH_PAT}", _GH_PAT),
    (
        "jwt",
        f"jwt {_JWT}",
        _JWT_HEAD,
    ),
    ("amz_sig", "url x-amz-signature=deadbeefcafef00ddeadbeef", "x-amz-signature=deadbeefcafef00ddeadbeef"),
    ("goog_sig", "url x-goog-signature=deadbeefcafef00ddeadbeef", "x-goog-signature=deadbeefcafef00ddeadbeef"),
    ("signed_url", "see signed_url for the link", "signed_url"),
    ("bearer", "Authorization: Bearer abcdEFGH1234ijklMNOP", "Bearer abcdEFGH1234ijklMNOP"),
    ("basic_auth", "Authorization: Basic dXNlcjpwYXNz", "Basic dXNlcjpwYXNz"),
    ("authorization_header", "authorization: tok-value-here", "authorization:"),
    ("cookie", "cookie: session=deadbeef", "cookie:"),
    ("pem_private_key", "-----BEGIN RSA PRIVATE KEY-----\nMIIabc\n-----END RSA PRIVATE KEY-----", "PRIVATE KEY"),
    ("password_assign", "password=hunter2value", "password=hunter2value"),
    ("api_key_assign", "api_key: superSecretValue123", "superSecretValue123"),
    ("users_path", "see /Users/kevin/secret/file", "/Users/kevin"),
    ("workspace_path", "in /workspace/secret/file", "/workspace/secret"),
]


@pytest.mark.parametrize("label,sample,raw", _SECRET_SAMPLES, ids=[s[0] for s in _SECRET_SAMPLES])
def test_kernel_flags_every_token_family(label: str, sample: str, raw: str) -> None:
    assert contains_secret_marker(sample) is True, f"{label} not flagged by contains_secret_marker"


@pytest.mark.parametrize("label,sample,raw", _SECRET_SAMPLES, ids=[s[0] for s in _SECRET_SAMPLES])
def test_kernel_scrubs_every_token_family(label: str, sample: str, raw: str) -> None:
    scrubbed = redact_private_text(sample)
    assert raw not in scrubbed, f"{label} raw token survived redaction: {scrubbed!r}"


@pytest.mark.parametrize("label,sample,raw", _SECRET_SAMPLES, ids=[s[0] for s in _SECRET_SAMPLES])
def test_kernel_rejects_every_token_family(label: str, sample: str, raw: str) -> None:
    with pytest.raises(ValueError):
        reject_private_text(sample, field_name="x")


def test_redact_private_text_clips_and_scrubs() -> None:
    payload = "Bearer abcdEFGH1234ijklMNOP " + ("a" * 1000)
    out = redact_private_text(payload, max_chars=64)
    assert "Bearer abcdEFGH1234ijklMNOP" not in out
    assert len(out) <= 64


def test_redact_private_text_default_clip_is_kernel_constant() -> None:
    long_clean = "x" * (MAX_PUBLIC_TEXT_CHARS + 500)
    out = redact_private_text(long_clean)
    assert len(out) == MAX_PUBLIC_TEXT_CHARS


def test_redact_private_text_no_clip_when_unset() -> None:
    long_clean = "x" * (MAX_PUBLIC_TEXT_CHARS + 500)
    out = redact_private_text(long_clean, max_chars=None)
    assert len(out) == len(long_clean)


def test_clean_text_is_unflagged_and_unchanged() -> None:
    sample = "the agent summarized the report and produced a chart"
    assert contains_secret_marker(sample) is False
    assert redact_private_text(sample, max_chars=None) == sample


def test_reject_and_redact_share_pattern() -> None:
    # Every sample that contains_secret_marker reports True must also be rejected
    # by the raising form, proving both forms share UNSAFE_TEXT_RE.
    for _label, sample, _raw in _SECRET_SAMPLES:
        assert contains_secret_marker(sample) is True
        with pytest.raises(ValueError):
            reject_private_text(sample, field_name="x")


def test_safe_public_ref_accepts_clean_and_rejects_secret() -> None:
    assert safe_public_ref("ops.metric.value", field_name="ref") == "ops.metric.value"
    with pytest.raises(ValueError):
        safe_public_ref("sk-abcdEFGH1234ijklMNOP", field_name="ref")
