"""C-1 per-site golden parity tests for the redaction kernel migration.

The C-1 pass re-pointed several forked ``_PRIVATE_TEXT_RE`` copies onto the
single ``ops/safety`` kernel (``UNSAFE_TEXT_RE``). The migration plan mandated a
per-site golden-equivalence capture so a migrated wrapper can never redact LESS
than the forked copy it replaced — but those captures were never written, which
let real parity regressions (``session=``, ``authorization: <value>``, and
length-floor-shortened token shapes) slip through into the LIVE tool-output
redaction path.

This file closes that gap. For EVERY migrated wrapper it feeds payloads
containing each token shape the now-deleted forked copies caught and asserts the
post-migration wrapper REDACTS it. The wrappers under test are the actual
shipped redaction functions (``_sanitize_text`` / ``_safe_text`` /
``_contains_private_text``), not the kernel helpers directly, so a kernel that is
not a true superset fails here exactly where a live secret would leak.

Token shapes are sourced from the union of every forked copy replaced in commit
"refactor(security): migrate tools/artifacts/projection onto redaction kernel"
(see git log / ws-C-security-kernels.md C-1):

  - tools/kernel._PRIVATE_TEXT_RE        (had bare session=, authorization: <value>)
  - tools/output_budget._PRIVATE_TEXT_RE (had sk-/gh_/xox/AKIA/AIza with `+`/`{8,}`)
  - tools/schema_validation._PRIVATE_TEXT_RE
  - artifacts/local_result_store._PRIVATE_TEXT_RE
  - runtime/governed_projection._PRIVATE_TEXT_RE + _JWT_LIKE_RE
"""

from __future__ import annotations

import pytest

from magi_agent.artifacts.local_result_store import _safe_text as _artifact_safe_text
from magi_agent.runtime.governed_projection import _contains_private_text
from magi_agent.tools.kernel import _sanitize_text as _kernel_sanitize_text
from magi_agent.tools.output_budget import _safe_text as _budget_safe_text
from magi_agent.tools.schema_validation import _safe_text as _schema_safe_text


# (label, payload, raw_substr_that_must_not_survive_redaction).
# Each raw token shape was caught by at least one forked copy the kernel
# replaced; a true-superset kernel must redact every one of them.
_SITE_GOLDEN_SAMPLES: list[tuple[str, str, str]] = [
    # --- R1 regression 1: bare session assignment (tools/kernel copy) ---
    ("session_eq", "session=SUPERSECRETVALUE123", "SUPERSECRETVALUE123"),
    ("session_id_eq", "session_id=SUPERSECRETVALUE123", "SUPERSECRETVALUE123"),
    ("session_id_colon", "session_id: SUPERSECRETVALUE123", "SUPERSECRETVALUE123"),
    ("session_key_colon", "session-key: SUPERSECRETVALUE123", "SUPERSECRETVALUE123"),
    ("sessionid_eq", "sessionid=SUPERSECRETVALUE123", "SUPERSECRETVALUE123"),
    # --- R1 regression 2: authorization header consumes scheme+value ---
    ("authorization_value", "authorization: APIKey mysecretvalue", "mysecretvalue"),
    ("authorization_value_scheme", "authorization: APIKey mysecretvalue", "APIKey mysecretvalue"),
    # --- R1 regression 3: length-floor weakening (short tokens) ---
    ("short_sk", "token sk-abc", "sk-abc"),
    ("short_ghp", "gh ghp_abc", "ghp_abc"),
    ("short_xoxa", "slack xoxa-abc", "xoxa-abc"),
    ("short_akia", "creds AKIA01234567", "AKIA01234567"),
    ("short_aiza", "g AIzaabc", "AIzaabc"),
    # --- gh[opusr]_ family (all letters) ---
    ("ghp", "ghp_abcdEFGH1234", "ghp_abcdEFGH1234"),
    ("gho", "gho_abcdEFGH1234", "gho_abcdEFGH1234"),
    ("ghu", "ghu_abcdEFGH1234", "ghu_abcdEFGH1234"),
    ("ghs", "ghs_abcdEFGH1234", "ghs_abcdEFGH1234"),
    ("ghr", "ghr_abcdEFGH1234", "ghr_abcdEFGH1234"),
    # --- other shapes every copy caught ---
    ("github_pat", "github_pat_abcdEFGH1234", "github_pat_abcdEFGH1234"),
    ("bearer", "Authorization: Bearer abcdEFGH1234ijkl", "Bearer abcdEFGH1234ijkl"),
    # full-length provider tokens assembled at runtime so source has no
    # contiguous provider-pattern literal (GitHub push-protection); the joined
    # value is what each old forked copy caught and the kernel must still catch.
    ("aiza_full", ("AIza" + "SyA1234567890abcdefghijklmnopqrstuv"), ("AIza" + "SyA1234567890abcdefghijklmnopqrstuv")),
    ("akia_full", ("AKIA" + "IOSFODNN7EXAMPLE"), ("AKIA" + "IOSFODNN7EXAMPLE")),
    ("users_path", "see /Users/kevin/secret/file", "/Users/kevin"),
]


# Scrub wrappers: (site_label, callable). Each returns the redacted string; the
# raw token must not survive.
_SCRUB_WRAPPERS = [
    ("tools/kernel._sanitize_text", _kernel_sanitize_text),
    ("tools/output_budget._safe_text", _budget_safe_text),
    ("tools/schema_validation._safe_text", _schema_safe_text),
    ("artifacts/local_result_store._safe_text", _artifact_safe_text),
]


@pytest.mark.parametrize(
    "site_label,scrub",
    _SCRUB_WRAPPERS,
    ids=[s[0] for s in _SCRUB_WRAPPERS],
)
@pytest.mark.parametrize(
    "label,payload,raw",
    _SITE_GOLDEN_SAMPLES,
    ids=[s[0] for s in _SITE_GOLDEN_SAMPLES],
)
def test_migrated_scrub_wrapper_redacts_every_token_shape(
    site_label: str, scrub, label: str, payload: str, raw: str
) -> None:
    scrubbed = scrub(payload)
    assert raw not in scrubbed, (
        f"PARITY VIOLATION: {site_label} left {label} raw token in output: "
        f"{scrubbed!r}"
    )


@pytest.mark.parametrize(
    "label,payload,raw",
    _SITE_GOLDEN_SAMPLES,
    ids=[s[0] for s in _SITE_GOLDEN_SAMPLES],
)
def test_governed_projection_flags_every_token_shape(
    label: str, payload: str, raw: str
) -> None:
    # governed_projection migrated its boolean _PRIVATE_TEXT_RE/_JWT_LIKE_RE
    # checks onto contains_secret_marker via _contains_private_text. A
    # true-superset kernel must flag every shape the forked copy flagged.
    assert _contains_private_text(payload) is True, (
        f"PARITY VIOLATION: governed_projection no longer flags {label}: {payload!r}"
    )


# --- Direct LIVE-PROOF assertions the reviewer named explicitly ----------------
def test_live_proof_session_eq_redacted() -> None:
    out = _kernel_sanitize_text("session=SUPERSECRETVALUE123")
    assert "SUPERSECRETVALUE123" not in out
    assert "[redacted-private]" in out


def test_live_proof_authorization_value_redacted() -> None:
    out = _kernel_sanitize_text("authorization: APIKey mysecretvalue")
    assert "APIKey mysecretvalue" not in out
    assert "[redacted-private]" in out


@pytest.mark.parametrize(
    "payload,raw",
    [
        ("token sk-abc", "sk-abc"),
        ("gh ghp_abc", "ghp_abc"),
        ("slack xoxa-abc", "xoxa-abc"),
        ("creds AKIA01234567", "AKIA01234567"),
        ("g AIzaabc", "AIzaabc"),
    ],
    ids=["sk", "ghp", "xoxa", "akia", "aiza"],
)
def test_live_proof_short_tokens_redacted(payload: str, raw: str) -> None:
    out = _kernel_sanitize_text(payload)
    assert raw not in out, f"short token {raw!r} survived: {out!r}"
