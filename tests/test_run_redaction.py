"""Public-link redaction for the run-share path.

``redact_public_text`` composes the canonical kernel scrub
(``ops.safety.redact_private_text``) with the gaps the kernel leaves open on a
PUBLIC surface: quoted credential values, ``scheme://user:pass@`` URL creds, and
public-only PII (internal cluster hostnames, RFC1918 IPs, emails). It must stay
LINEAR (the legacy ledger redactor had catastrophic backtracking).

``build_public_run_view`` is allowlist fail-closed: only known keys survive, and
every free-text value is scrubbed.
"""
from __future__ import annotations

import time

import pytest

from magi_agent.evidence.run_redaction import (
    build_public_run_view,
    redact_public_text,
)


def _leaks(text: str, secret: str) -> bool:
    return secret in redact_public_text(text, max_chars=None)


# --- coverage the kernel already provides (regression guard via this path) ---
@pytest.mark.parametrize(
    "secret",
    [
        "AKIA" + "IOSFODNN7EXAMPLE",  # AWS
        "AIza" + "SyA1234567890abcdefghij",  # Google
        "xoxb-" + "123456789012-abcdef",  # Slack
        "eyJ" + "abcdefgh.eyJpayload01.sigsigsig0",  # JWT
        "ghp_" + "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789",  # GitHub
        "/Users/" + "kevin/.ssh/id_rsa",  # home path
    ],
)
def test_kernel_covered_formats_are_redacted(secret: str) -> None:
    assert not _leaks(f"value {secret} end", secret)


# --- gaps the kernel leaves; this module must close them ---
def test_quoted_credential_value_is_redacted() -> None:
    assert not _leaks('password="SUPERSECRETVALUE0123456789"', "SUPERSECRETVALUE0123456789")


def test_json_quoted_secret_is_redacted() -> None:
    assert not _leaks('{"secret": "abcDEF123456789value"}', "abcDEF123456789value")


def test_single_quoted_credential_value_is_redacted() -> None:
    assert not _leaks("api_key='plain-text-secret-value'", "plain-text-secret-value")


def test_http_basic_auth_url_creds_are_redacted() -> None:
    out = redact_public_text("curl https://admin:hunter2@example.com/api", max_chars=None)
    assert "hunter2" not in out
    assert "admin" not in out


def test_internal_cluster_hostname_is_redacted() -> None:
    assert not _leaks("api-internal.prod.svc.cluster.local", "api-internal.prod.svc.cluster.local")


def test_rfc1918_ip_is_redacted() -> None:
    out = redact_public_text("host 10.1.2.3 and 192.168.0.5 and 172.16.5.5", max_chars=None)
    assert "10.1.2.3" not in out
    assert "192.168.0.5" not in out
    assert "172.16.5.5" not in out


def test_public_ip_is_not_redacted() -> None:
    # 8.8.8.8 is public; over-redacting normal text would gut the trace.
    assert "8.8.8.8" in redact_public_text("dns 8.8.8.8", max_chars=None)


def test_email_is_redacted() -> None:
    assert not _leaks("contact alice@example.com now", "alice@example.com")


# --- regression locks for the adversarial review findings ---
@pytest.mark.parametrize(
    "text,secret",
    [
        ('password="a\\"PROD_DB_PW_LEAKS"', "PROD_DB_PW_LEAKS"),  # escaped quote
        ("secret=\"he said 'topsecretpw' ok\"", "topsecretpw"),  # opposite quote inside
        ("access_key=\"abc'SECRETTAIL12345\"", "SECRETTAIL12345"),  # apostrophe in value
        ("passwd=mypwLEAKS123", "mypwLEAKS123"),  # short key, unquoted
        ("pwd='plainsecretvalue'", "plainsecretvalue"),
        ("service_role_key=eyJsupabaseSERVICErole", "eyJsupabaseSERVICErole"),  # Supabase, unquoted
        ('service_role_key: "supabaseSECRETvalue"', "supabaseSECRETvalue"),  # quoted
        ("passphrase=plainpassphraseval", "plainpassphraseval"),
        ("AccountKey=abcDEF123456789xyzBASE64KEYvalue", "abcDEF123456789xyzBASE64KEYvalue"),  # Azure
        ("auth=0123456789abcdef0123456789abcdef", "0123456789abcdef0123456789abcdef"),  # 32-hex
    ],
)
def test_named_and_opaque_credentials_do_not_leak(text: str, secret: str) -> None:
    assert not _leaks(text, secret)


def test_url_userinfo_with_path_keyword_username_is_redacted() -> None:
    # The kernel's /Users path rule must not strand the password (L3).
    out = redact_public_text("https://users:Sup3rS3cretPW@host/db", max_chars=None)
    assert "Sup3rS3cretPW" not in out


def test_url_userinfo_with_port_is_redacted() -> None:
    out = redact_public_text("postgres://u:RabbitSecretPW@host:5432/db", max_chars=None)
    assert "RabbitSecretPW" not in out


def test_pat_keyword_does_not_overmatch_inside_word() -> None:
    # ``compat=1`` must NOT be redacted by the ``pat`` credential key.
    assert "compat=1" in redact_public_text("compat=1 enabled", max_chars=None)


def test_opaque_token_rule_does_not_eat_paths_or_urls() -> None:
    text = "ran ls /Users/x and curl https://example.com/some/long/path/here"
    out = redact_public_text(text, max_chars=None)
    # The URL path (has slashes) must survive the opaque-token rule (the /Users
    # part is redacted by the kernel, which is fine).
    assert "example.com/some/long/path/here" in out


def test_nested_dict_secret_key_is_redacted() -> None:
    pub = build_public_run_view(
        {
            "trace": [
                {"name": "Env", "status": "ok", "argsSummary": {"ghp_" + "A" * 36: "1"}}
            ],
        }
    )
    assert "ghp_" + "A" * 36 not in str(pub["trace"][0]["argsSummary"])


def test_non_secret_text_survives() -> None:
    text = "Fixed 12 lint errors and opened PR 1234 in the repo"
    assert redact_public_text(text, max_chars=None) == text


def test_clips_to_max_chars() -> None:
    assert len(redact_public_text("x" * 1000)) <= 200


def test_non_string_is_empty() -> None:
    assert redact_public_text(None) == ""  # type: ignore[arg-type]


# --- linearity: the legacy redactor hung >10s at 20k chars ---
def test_redaction_is_linear_on_large_input() -> None:
    start = time.perf_counter()
    redact_public_text("x" * 100_000, max_chars=None)
    assert time.perf_counter() - start < 1.0


@pytest.mark.parametrize(
    "payload",
    [
        "A=" * 60_000,  # interleaved '=' (opaque-token ReDoS regression)
        "password=" * 20_000,
        'secret="' + "a" * 120_000,  # unterminated quote
        "k=" + "A" * 120_000,
    ],
)
def test_no_redos_on_adversarial_structured_input(payload: str) -> None:
    start = time.perf_counter()
    redact_public_text(payload, max_chars=None)
    assert time.perf_counter() - start < 1.0


def test_opaque_rule_does_not_over_redact_common_id_keys() -> None:
    text = (
        "commit=0123456789abcdef0123456789abcdef01234567 "
        "request_id=11111111-2222-3333-4444-555555555555 "
        "id=longlonglonglonglongidentifier01"
    )
    out = redact_public_text(text, max_chars=None)
    assert "0123456789abcdef0123456789abcdef01234567" in out
    assert "11111111-2222-3333-4444-555555555555" in out


def test_opaque_rule_still_redacts_vendor_keys() -> None:
    assert not _leaks(
        "AccountKey=abcDEF123456789xyzBASE64KEYvalue", "abcDEF123456789xyzBASE64KEYvalue"
    )
    assert not _leaks("auth=0123456789abcdef0123456789abcdef", "0123456789abcdef0123456789abcdef")


# --- allowlist fail-closed projection ---
def _view() -> dict:
    return {
        "schemaVersion": "openmagi.runView.v1",
        "sessionId": "s",
        "summary": {
            "goal": "deploy with token ghp_" + "A" * 36,
            "result": "done",
            "status": "ok",
            "model": {"label": "claude-opus-4-8", "provider": "anthropic"},
            "usage": {"inputTokens": 10, "outputTokens": 5},
            "costUsd": 0.01,
            "EVIL": "should be dropped",
        },
        "trace": [
            {
                "turnId": "t1",
                "name": "Bash",
                "status": "ok",
                "reason": "tool_completed",
                "durationMs": 12,
                "argsSummary": {"command": "curl https://u:p@host/x"},
                "resultSummary": {"exitCode": 0},
                "SECRETFIELD": "drop me",
            }
        ],
        "governance": [
            {"turnId": "t1", "name": "FileWrite", "status": "blocked", "reason": "policy", "kind": "policy"}
        ],
        "counts": {"stepCount": 1, "turnCount": 1, "receiptCount": 0, "governanceCount": 1},
    }


def test_projection_drops_unknown_keys() -> None:
    pub = build_public_run_view(_view())
    assert "EVIL" not in pub["summary"]
    assert "SECRETFIELD" not in pub["trace"][0]


def test_projection_redacts_summary_free_text() -> None:
    pub = build_public_run_view(_view())
    assert "ghp_" + "A" * 36 not in pub["summary"]["goal"]


def test_projection_redacts_trace_args() -> None:
    pub = build_public_run_view(_view())
    args = pub["trace"][0]["argsSummary"]
    assert "p@host" not in str(args)  # url creds scrubbed in nested value


def test_projection_preserves_safe_structure() -> None:
    pub = build_public_run_view(_view())
    assert pub["schemaVersion"] == "openmagi.runView.v1"
    assert pub["summary"]["model"] == {"label": "claude-opus-4-8", "provider": "anthropic"}
    assert pub["summary"]["usage"] == {"inputTokens": 10, "outputTokens": 5}
    assert pub["trace"][0]["name"] == "Bash"
    assert pub["governance"][0]["kind"] == "policy"
    assert pub["counts"]["stepCount"] == 1


def test_projection_handles_none_summary() -> None:
    view = _view()
    view["summary"] = None
    pub = build_public_run_view(view)
    assert pub["summary"] is None
