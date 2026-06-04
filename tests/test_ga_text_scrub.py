"""Tests for the shared GA secret/path scrubber (PR12).

TDD: this file was written FIRST (RED). The shared scrubber module
magi_agent.harness.general_automation.text_scrub must provide scrub_text()
that implements the superset of all patterns from shell_policy, output_budget_policy,
and the guardrail_matrix path-like markers.

Superset patterns covered:
  Path prefixes:   /Users, /home, /workspace, /data/bots, /var/lib/kubelet
  New prefixes:    /etc/, /proc/, /sys/, /root/
  Auth headers:    Authorization: Bearer <token>, bearer <token>
  Cookie header:   cookie: <value>
  SID:             sid=<value>
  API key tokens:  sk-<value>, sk_<value>
  GitHub tokens:   gho_, ghp_, ghu_, ghs_, ghr_, github_pat_
  Slack tokens:    xox?-<value>
  AWS key:         AKIA<8+ uppercase alphanum>
  Google API key:  AIza<alphanum>
  Cloud URIs:      s3://, gs://, supabase://
  Raw markers:     raw_tool_log, raw tool, raw-prompt, hidden_reasoning, chain_of_thought
  Also the output_budget raw sub-types: raw_child, raw_transcript, raw_output, raw_result,
                                         raw_log, raw_args, raw_browser, raw_dom
"""
from __future__ import annotations

import pytest

from magi_agent.harness.general_automation.text_scrub import scrub_text


REDACTED = "[redacted-private]"


# ---------------------------------------------------------------------------
# Path prefixes — original set
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("path", [
    "/Users/acme/secret.txt",
    "/Users",
    "/home/ubuntu/key.pem",
    "/home",
    "/workspace/project/config.json",
    "/workspace",
    "/data/bots/1234/token.txt",
    "/data/bots",
    "/var/lib/kubelet/pods/abc/volumes/secret",
])
def test_original_path_prefixes_are_redacted(path: str) -> None:
    result = scrub_text(path)
    assert REDACTED in result
    assert path not in result


# ---------------------------------------------------------------------------
# NEW path prefixes (PR12 extension)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("path", [
    "/etc/passwd",
    "/etc/ssh/sshd_config",
    "/etc/",
    "/proc/self/environ",
    "/proc/1/cmdline",
    "/proc/",
    "/sys/kernel/debug/foo",
    "/sys/",
    "/root/.ssh/id_rsa",
    "/root/",
])
def test_new_system_path_prefixes_are_redacted(path: str) -> None:
    result = scrub_text(path)
    assert REDACTED in result
    assert path not in result


# ---------------------------------------------------------------------------
# Auth tokens
# ---------------------------------------------------------------------------

def test_bearer_header_is_redacted() -> None:
    s = "Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.abc.def"
    result = scrub_text(s)
    assert REDACTED in result
    assert "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9" not in result


def test_bearer_token_inline_is_redacted() -> None:
    s = "bearer eyJtoken123/abc+def="
    result = scrub_text(s)
    assert REDACTED in result
    assert "eyJtoken123" not in result


def test_cookie_header_is_redacted() -> None:
    s = "cookie: session=abc123; user=kevin"
    result = scrub_text(s)
    assert REDACTED in result
    assert "session=abc123" not in result


def test_sid_cookie_is_redacted() -> None:
    s = "sid=s3cr3t-session-id-value"
    result = scrub_text(s)
    assert REDACTED in result
    assert "s3cr3t-session-id-value" not in result


# ---------------------------------------------------------------------------
# API key patterns
# ---------------------------------------------------------------------------

def test_sk_dash_token_is_redacted() -> None:
    s = "sk-proj-abc123DEF456"
    result = scrub_text(s)
    assert REDACTED in result
    assert "abc123DEF456" not in result


def test_sk_underscore_token_is_redacted() -> None:
    s = "sk_live_abcDEFxyz789"
    result = scrub_text(s)
    assert REDACTED in result
    assert "sk_live_abcDEFxyz789" not in result


@pytest.mark.parametrize("prefix", ["gho_", "ghp_", "ghu_", "ghs_", "ghr_"])
def test_github_token_prefixes_are_redacted(prefix: str) -> None:
    s = f"{prefix}Abc123XYZ"
    result = scrub_text(s)
    assert REDACTED in result
    assert "Abc123XYZ" not in result


def test_github_pat_token_is_redacted() -> None:
    s = "github_pat_11ABCDEF_longTokenValue123"
    result = scrub_text(s)
    assert REDACTED in result
    assert "longTokenValue123" not in result


@pytest.mark.parametrize("token", [
    "xoxb-123456789012-abc",
    "xoxp-111222333-abc",
    "xoxa-2-abc123",
    "xoxr-abc.123-def",
])
def test_slack_xox_tokens_are_redacted(token: str) -> None:
    result = scrub_text(token)
    assert REDACTED in result
    assert token not in result


def test_aws_akia_key_is_redacted() -> None:
    s = "AKIAIOSFODNN7EXAMPLE"
    result = scrub_text(s)
    assert REDACTED in result
    assert "AKIAIOSFODNN7EXAMPLE" not in result


def test_google_aiza_key_is_redacted() -> None:
    s = "AIzaSyDHsampleKeyValue1234"
    result = scrub_text(s)
    assert REDACTED in result
    assert "AIzaSyDHsampleKeyValue1234" not in result


# ---------------------------------------------------------------------------
# Cloud storage URIs
# ---------------------------------------------------------------------------

def test_s3_uri_is_redacted() -> None:
    s = "s3://my-bucket/path/to/object.json"
    result = scrub_text(s)
    assert REDACTED in result
    assert "my-bucket" not in result


def test_gs_uri_is_redacted() -> None:
    s = "gs://gcp-bucket/some/path"
    result = scrub_text(s)
    assert REDACTED in result
    assert "gcp-bucket" not in result


def test_supabase_uri_is_redacted() -> None:
    s = "supabase://project.supabase.co/rest/v1/table"
    result = scrub_text(s)
    assert REDACTED in result
    assert "project.supabase.co" not in result


# ---------------------------------------------------------------------------
# Raw content markers (both shell_policy and output_budget variants)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("marker", [
    "raw_tool_log",
    "raw tool",
    "raw-tool",
    "raw_prompt",
    "raw-prompt",
    "raw_output",
    "raw-output",
    "raw_result",
    "raw-result",
    "raw_log",
    "raw-log",
    "raw_args",
    "raw-args",
    # output_budget extras
    "raw_child",
    "raw-child",
    "raw_transcript",
    "raw-transcript",
    "raw_browser",
    "raw-browser",
    "raw_dom",
    "raw-dom",
])
def test_raw_content_markers_are_redacted(marker: str) -> None:
    result = scrub_text(marker)
    assert REDACTED in result
    assert marker not in result


@pytest.mark.parametrize("marker", [
    "hidden_reasoning",
    "hidden reasoning",
    "hidden-reasoning",
    "chain_of_thought",
    "chain of thought",
    "chain-of-thought",
])
def test_hidden_reasoning_chain_of_thought_markers_are_redacted(marker: str) -> None:
    result = scrub_text(marker)
    assert REDACTED in result
    assert marker not in result


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------

def test_scrub_is_idempotent() -> None:
    s = "bearer token123 /etc/passwd /Users/kevin/data"
    once = scrub_text(s)
    twice = scrub_text(once)
    assert once == twice


def test_scrub_idempotent_on_already_clean_text() -> None:
    s = "Hello, this is ordinary text without secrets."
    result = scrub_text(s)
    assert result == scrub_text(result)


# ---------------------------------------------------------------------------
# No over-redaction of ordinary text
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("safe_text", [
    "Hello world",
    "Running: npm run build",
    "Error: file not found",
    "GET /api/v1/status HTTP/1.1",
    "200 OK",
    "user@example.com",
    "the value is 42",
    "https://api.example.com/endpoint",
])
def test_ordinary_text_is_not_over_redacted(safe_text: str) -> None:
    result = scrub_text(safe_text)
    assert REDACTED not in result


@pytest.mark.parametrize("safe_text", [
    "GET /api/v1/processing HTTP/1.1",
    "system startup complete",
    "/etc-tools/build",
    "/rootdir/data",
    "processing files in /processing",
    "system update required",
])
def test_new_system_paths_do_not_over_redact_url_words(safe_text: str) -> None:
    """Verify that /etc/, /proc/, /sys/, /root/ patterns require the trailing /
    and don't match common URL/log words like '/processing', 'system', '/etc-tools', '/rootdir'."""
    result = scrub_text(safe_text)
    assert REDACTED not in result, f"Over-redacted safe text: {safe_text!r}"


# ---------------------------------------------------------------------------
# Integration: mixed sensitive + safe text
# ---------------------------------------------------------------------------

def test_mixed_content_redacts_sensitive_parts_preserves_safe_parts() -> None:
    s = (
        "Processing file /Users/acme/data.csv — status OK. "
        "Token: bearer super-secret-token. "
        "Normal output: 42 rows processed."
    )
    result = scrub_text(s)
    assert REDACTED in result
    assert "/Users/acme" not in result
    assert "super-secret-token" not in result
    assert "42 rows processed" in result
    assert "status OK" in result


def test_new_path_prefixes_in_mixed_content() -> None:
    s = (
        "Reading /etc/passwd for user info and /proc/self/environ for env. "
        "Normal log: all good."
    )
    result = scrub_text(s)
    assert REDACTED in result
    assert "/etc/passwd" not in result
    assert "/proc/self/environ" not in result
    assert "Normal log" in result
    assert "all good" in result


# ---------------------------------------------------------------------------
# Call-site migration: shell_policy._safe_text and output_budget._safe_text
# must now be superset-scrubbers, not the old narrower sets.
# ---------------------------------------------------------------------------

def test_shell_policy_safe_text_redacts_aws_key() -> None:
    """shell_policy._safe_text must redact AWS AKIA keys (was missing before migration)."""
    from magi_agent.harness.general_automation import shell_policy
    # AKIA pattern was NOT in shell_policy before migration — post-migration it must be.
    sensitive = "AKIAIOSFODNN7EXAMPLE"
    result = shell_policy._safe_text(sensitive)
    assert REDACTED in result
    assert "AKIAIOSFODNN7EXAMPLE" not in result


def test_shell_policy_safe_text_redacts_hidden_reasoning() -> None:
    """shell_policy._safe_text must redact hidden_reasoning (was missing before migration)."""
    from magi_agent.harness.general_automation import shell_policy
    sensitive = "hidden_reasoning chain_of_thought xoxb-123-abc"
    result = shell_policy._safe_text(sensitive)
    assert REDACTED in result
    assert "hidden_reasoning" not in result


def test_shell_policy_safe_text_redacts_new_system_paths() -> None:
    """shell_policy._safe_text must redact /etc/, /proc/, /sys/, /root/ (PR12 additions)."""
    from magi_agent.harness.general_automation import shell_policy
    for path in ("/etc/passwd", "/proc/self/environ", "/sys/kernel", "/root/.ssh/id_rsa"):
        result = shell_policy._safe_text(path)
        assert REDACTED in result, f"Expected {path!r} to be redacted by shell_policy._safe_text"


def test_output_budget_safe_text_redacts_new_system_paths() -> None:
    """output_budget_policy._safe_text must redact /etc/, /proc/, /sys/, /root/ (PR12 additions)."""
    from magi_agent.harness.general_automation import output_budget_policy
    for path in ("/etc/shadow", "/proc/1/maps", "/sys/class/net", "/root/credentials"):
        result = output_budget_policy._safe_text(path)
        assert REDACTED in result, f"Expected {path!r} to be redacted by output_budget._safe_text"


def test_output_budget_safe_text_delegates_to_scrub_text_superset() -> None:
    """output_budget_policy._safe_text must produce same result as scrub_text on all patterns."""
    from magi_agent.harness.general_automation import output_budget_policy
    sensitive = "hidden_reasoning AIzaSyDtest /home/user/key /etc/passwd"
    assert output_budget_policy._safe_text(sensitive) == scrub_text(sensitive)


def test_shell_policy_safe_text_delegates_to_scrub_text_superset() -> None:
    """shell_policy._safe_text must produce same result as scrub_text on all patterns."""
    from magi_agent.harness.general_automation import shell_policy
    sensitive = "/Users/kevin/secret bearer tok123 AKIAIOSFODNN7EXAMPLE /proc/self/environ"
    assert shell_policy._safe_text(sensitive) == scrub_text(sensitive)
