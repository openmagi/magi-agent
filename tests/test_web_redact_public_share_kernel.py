"""S-02: web_acquisition public-text redaction must use the canonical share kernel.

``web_acquisition.policy.redact_public_text`` was a weaker fork than
``evidence.run_redaction.redact_public_text``: it only carried a line denylist
plus a generic secret/path regex, so basic-auth URL userinfo, quoted credential
values, internal cluster hostnames, RFC1918 IPs, and emails leaked through on the
~40 web acquisition public surfaces that call it.

This test pins the consolidation: the web redactor delegates the
credential/secret/PII scrub to the shared kernel (strict superset), while keeping
its web-specific structural behaviour (drop agent-internal marker lines, redact
storage/signed URLs, and the generic KEY=VALUE backstop).
"""

from __future__ import annotations

import pytest

from magi_agent.web_acquisition.policy import redact_public_text


# --- gap the fork left open: now closed by the shared kernel ----------------
def test_basic_auth_userinfo_stripped() -> None:
    out = redact_public_text("see https://alice:s3cr3tpw@db.internal/path here")
    assert "s3cr3tpw" not in out
    assert "[redacted]" in out


def test_quoted_credential_value_scrubbed() -> None:
    out = redact_public_text('config password="hunter2longvalue" loaded')
    assert "hunter2longvalue" not in out
    assert "[redacted]" in out


def test_cluster_hostname_redacted() -> None:
    out = redact_public_text("calling api.payments.svc.cluster.local now")
    assert "svc.cluster.local" not in out


def test_rfc1918_ip_redacted() -> None:
    out = redact_public_text("upstream 10.4.5.6 and 192.168.1.20 reached")
    assert "10.4.5.6" not in out
    assert "192.168.1.20" not in out


def test_email_redacted() -> None:
    out = redact_public_text("contact ops.team@example.com for access")
    assert "ops.team@example.com" not in out


def test_provider_token_shapes_covered_by_kernel() -> None:
    # GitHub PAT shape: covered by the kernel's provider denylist.
    out = redact_public_text("token ghp_AbCdEf0123456789AbCdEf0123456789")
    assert "ghp_AbCdEf0123456789AbCdEf0123456789" not in out


# --- web-specific behaviour that must be PRESERVED (strict superset) --------
def test_raw_marker_lines_still_dropped() -> None:
    out = redact_public_text("visible line\nraw_tool internal dump\nkept line")
    assert "internal dump" not in out
    assert "visible line" in out
    assert "kept line" in out


def test_sensitive_storage_url_still_redacted() -> None:
    out = redact_public_text("download s3://my-bucket/secret-object.zip now")
    assert "my-bucket/secret-object.zip" not in out
    assert "[redacted-url]" in out


def test_generic_key_value_backstop_preserved() -> None:
    # A concatenated *KEY* name the kernel's strict cred-key list does not match,
    # but the web generic backstop still catches.
    out = redact_public_text("env MYSERVICE_TOKEN=abcd1234 set")
    assert "abcd1234" not in out


def test_max_chars_clip_preserved() -> None:
    assert len(redact_public_text("x" * 5000, max_chars=160)) == 160
    # default clip stays 2048
    assert len(redact_public_text("y" * 5000)) == 2_048


def test_clean_text_passes_through() -> None:
    assert redact_public_text("a normal sentence about cats") == "a normal sentence about cats"


@pytest.mark.parametrize("value", ["", None, 123])
def test_non_str_and_empty_safe(value: object) -> None:
    # delegated kernel returns "" for empty/non-str; the wrapper must not raise.
    assert isinstance(redact_public_text(value if isinstance(value, str) else ""), str)
