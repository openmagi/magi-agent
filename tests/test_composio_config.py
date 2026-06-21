from __future__ import annotations

import json
import subprocess
import sys
import textwrap

import pytest


def test_composio_config_import_is_sdk_and_adk_clean() -> None:
    probe = textwrap.dedent(
        """
        from __future__ import annotations

        import json
        import sys

        import magi_agent.composio.config as config_module

        print(json.dumps({
            "config_imported": config_module is not None,
            "composio": sorted(
                key
                for key in sys.modules
                if key == "composio" or key.startswith("composio.")
            ),
            "adk_mcp_tool": sorted(
                key
                for key in sys.modules
                if key == "google.adk.tools.mcp_tool"
                or key.startswith("google.adk.tools.mcp_tool.")
            ),
        }, sort_keys=True))
        """
    )

    completed = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        check=False,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr

    observed = json.loads(completed.stdout)

    assert observed == {
        "config_imported": True,
        "composio": [],
        "adk_mcp_tool": [],
    }


def test_default_without_key_is_inactive_auto_not_configured() -> None:
    from magi_agent.composio.config import resolve_composio_config

    cfg = resolve_composio_config({}, package_available=True)

    assert cfg.enabled_mode == "auto"
    assert cfg.active is False
    assert cfg.configured is False
    assert cfg.disabled_reason == "not_configured"
    assert cfg.credential_source == "missing"


def test_explicit_on_without_key_is_inactive_missing_api_key() -> None:
    from magi_agent.composio.config import resolve_composio_config

    cfg = resolve_composio_config({"MAGI_COMPOSIO_ENABLED": "on"})

    assert cfg.enabled_mode == "on"
    assert cfg.active is False
    assert cfg.configured is False
    assert cfg.required is True
    assert cfg.disabled_reason == "missing_api_key"
    assert cfg.credential_source == "missing"


def test_default_with_key_and_package_available_is_active_auto() -> None:
    from magi_agent.composio.config import resolve_composio_config

    cfg = resolve_composio_config(
        {"COMPOSIO_API_KEY": "cp_test_secret"},
        package_available=True,
    )

    assert cfg.enabled_mode == "auto"
    assert cfg.active is True
    assert cfg.configured is True
    assert cfg.api_key == "cp_test_secret"
    assert cfg.credential_source == "env"
    assert cfg.toolkits == ()
    assert cfg.disabled_reason is None


def test_default_with_key_but_missing_package_is_inactive_auto() -> None:
    from magi_agent.composio.config import resolve_composio_config

    cfg = resolve_composio_config(
        {"COMPOSIO_API_KEY": "cp_test_secret"},
        package_available=False,
    )

    assert cfg.enabled_mode == "auto"
    assert cfg.active is False
    assert cfg.configured is True
    assert cfg.api_key == "cp_test_secret"
    assert cfg.credential_source == "env"
    assert cfg.disabled_reason == "missing_python_package"


@pytest.mark.parametrize("profile", ("safe", "minimal", "off", "conservative", "eval"))
def test_default_auto_with_key_respects_safe_runtime_profiles(profile: str) -> None:
    from magi_agent.composio.config import resolve_composio_config

    cfg = resolve_composio_config(
        {
            "COMPOSIO_API_KEY": "cp_test_secret",
            "MAGI_RUNTIME_PROFILE": profile,
        },
        package_available=True,
    )

    assert cfg.enabled_mode == "auto"
    assert cfg.active is False
    assert cfg.configured is True
    assert cfg.disabled_reason == "disabled_by_config"


def test_explicit_on_with_key_overrides_safe_runtime_profile() -> None:
    from magi_agent.composio.config import resolve_composio_config

    cfg = resolve_composio_config(
        {
            "COMPOSIO_API_KEY": "cp_test_secret",
            "MAGI_COMPOSIO_ENABLED": "on",
            "MAGI_RUNTIME_PROFILE": "safe",
        },
        package_available=True,
    )

    assert cfg.enabled_mode == "on"
    assert cfg.active is True
    assert cfg.disabled_reason is None


def test_explicit_on_with_key_uses_default_oss_entity() -> None:
    from magi_agent.composio.config import resolve_composio_config

    cfg = resolve_composio_config(
        {
            "COMPOSIO_API_KEY": "cp_test_secret",
            "MAGI_COMPOSIO_ENABLED": "on",
        }
    )

    assert cfg.enabled_mode == "on"
    assert cfg.active is True
    assert cfg.configured is True
    assert cfg.api_key == "cp_test_secret"
    assert cfg.entity_id == "default"
    assert cfg.credential_source == "env"
    assert cfg.disabled_reason is None


def test_explicit_auto_with_key_is_active() -> None:
    from magi_agent.composio.config import resolve_composio_config

    cfg = resolve_composio_config(
        {
            "COMPOSIO_API_KEY": "cp_test_secret",
            "MAGI_COMPOSIO_ENABLED": "auto",
        },
        package_available=True,
    )

    assert cfg.enabled_mode == "auto"
    assert cfg.active is True
    assert cfg.disabled_reason is None


def test_explicit_off_wins_over_key() -> None:
    from magi_agent.composio.config import resolve_composio_config

    cfg = resolve_composio_config(
        {"MAGI_COMPOSIO_ENABLED": "off", "COMPOSIO_API_KEY": "cp_test_secret"}
    )

    assert cfg.active is False
    assert cfg.configured is True
    assert cfg.disabled_reason == "disabled_by_config"


def test_explicit_on_without_key_is_required_missing() -> None:
    from magi_agent.composio.config import resolve_composio_config

    cfg = resolve_composio_config({"MAGI_COMPOSIO_ENABLED": "on"})

    assert cfg.enabled_mode == "on"
    assert cfg.active is False
    assert cfg.required is True
    assert cfg.disabled_reason == "missing_api_key"


def test_runtime_identity_derives_entity_id_from_user_and_bot() -> None:
    from magi_agent.composio.config import resolve_composio_config

    cfg = resolve_composio_config(
        {
            "COMPOSIO_API_KEY": "cp_test_secret",
            "MAGI_COMPOSIO_ENABLED": "on",
            "USER_ID": "user-123",
            "BOT_ID": "bot-456",
        }
    )

    assert cfg.entity_id == "openmagi:user:user-123:bot:bot-456"


def test_hosted_source_refuses_default_entity() -> None:
    from magi_agent.composio.config import resolve_composio_config

    cfg = resolve_composio_config(
        {
            "COMPOSIO_API_KEY": "cp_test_secret",
            "MAGI_COMPOSIO_ENABLED": "on",
            "MAGI_COMPOSIO_CREDENTIAL_SOURCE": "hosted",
        }
    )

    assert cfg.active is False
    assert cfg.configured is True
    assert cfg.credential_source == "hosted"
    assert cfg.disabled_reason == "missing_hosted_entity"


def test_toolkits_are_normalized_deduped_and_safe() -> None:
    from magi_agent.composio.config import resolve_composio_config

    cfg = resolve_composio_config(
        {
            "COMPOSIO_API_KEY": "cp_test_secret",
            "MAGI_COMPOSIO_ENABLED": "on",
            "MAGI_COMPOSIO_TOOLKITS": " gmail,googledrive,gmail, bad slug ",
        }
    )

    assert cfg.toolkits == ("gmail", "googledrive")


def test_invalid_enabled_value_with_api_key_is_inactive_invalid_config() -> None:
    from magi_agent.composio.config import resolve_composio_config

    cfg = resolve_composio_config(
        {
            "COMPOSIO_API_KEY": "cp_test_secret",
            "MAGI_COMPOSIO_ENABLED": "enabled",
        }
    )

    assert cfg.active is False
    assert cfg.configured is True
    assert cfg.disabled_reason == "invalid_config"


def test_invalid_credential_source_with_api_key_is_inactive_invalid_config() -> None:
    from magi_agent.composio.config import resolve_composio_config

    cfg = resolve_composio_config(
        {
            "COMPOSIO_API_KEY": "cp_test_secret",
            "MAGI_COMPOSIO_ENABLED": "on",
            "MAGI_COMPOSIO_CREDENTIAL_SOURCE": "vault",
        }
    )

    assert cfg.active is False
    assert cfg.configured is True
    assert cfg.disabled_reason == "invalid_config"


def test_explicit_entity_id_with_spaces_and_newlines_is_sanitized() -> None:
    from magi_agent.composio.config import resolve_composio_config

    cfg = resolve_composio_config(
        {
            "COMPOSIO_API_KEY": "cp_test_secret",
            "MAGI_COMPOSIO_ENABLED": "on",
            "MAGI_COMPOSIO_ENTITY_ID": " team alpha\nbot beta ",
        }
    )

    assert cfg.active is True
    assert cfg.entity_id == "team_alpha_bot_beta"
    assert cfg.entity_configured is True
    assert cfg.disabled_reason is None


def test_explicit_blank_entity_id_is_inactive_invalid_config() -> None:
    from magi_agent.composio.config import resolve_composio_config

    cfg = resolve_composio_config(
        {
            "COMPOSIO_API_KEY": "cp_test_secret",
            "MAGI_COMPOSIO_ENABLED": "on",
            "MAGI_COMPOSIO_ENTITY_ID": " \n\t ",
        }
    )

    assert cfg.active is False
    assert cfg.disabled_reason == "invalid_config"
    assert cfg.entity_id is None


def test_runtime_identity_replaces_colons_inside_user_and_bot_segments() -> None:
    from magi_agent.composio.config import resolve_composio_config

    cfg = resolve_composio_config(
        {
            "COMPOSIO_API_KEY": "cp_test_secret",
            "MAGI_COMPOSIO_ENABLED": "on",
            "USER_ID": "user:123",
            "BOT_ID": "bot:456:prod",
        }
    )

    assert cfg.entity_id == "openmagi:user:user_123:bot:bot_456_prod"


def test_explicit_toolkit_env_with_only_unsafe_tokens_is_inactive_invalid_config() -> None:
    from magi_agent.composio.config import resolve_composio_config

    cfg = resolve_composio_config(
        {
            "COMPOSIO_API_KEY": "cp_test_secret",
            "MAGI_COMPOSIO_ENABLED": "on",
            "MAGI_COMPOSIO_TOOLKITS": " bad slug,123bad,$admin,-oops ",
        }
    )

    assert cfg.active is False
    assert cfg.disabled_reason == "invalid_config"
    assert cfg.toolkits == ()


@pytest.mark.parametrize(
    "override",
    (
        "not-a-url",
        "http://mcp.composio.dev/mcp",
        "https://attacker.example/mcp",
        "https://user:mypass@mcp.composio.dev/mcp",
        "https://mcp.composio.dev/mcp#fragment",
        "https://mcp.composio.dev/mcp?api_key=cp_secret",
        "https://mcp.composio.dev/mcp?token=tok_secret",
        "https://mcp.composio.dev/mcp?access_token=tok_secret",
        "https://mcp.composio.dev/mcp?refresh_token=tok_secret",
        "https://mcp.composio.dev/mcp?secret=tok_secret",
        "https://mcp.composio.dev/mcp?auth=tok_secret",
        "https://mcp.composio.dev/mcp?authorization=tok_secret",
        "https://mcp.composio.dev/mcp?bearer=tok_secret",
        "https://mcp.composio.dev/mcp?client_secret=tok_secret",
    ),
)
def test_mcp_url_override_rejects_invalid_or_credentialed_urls(override: str) -> None:
    from magi_agent.composio.config import resolve_composio_config

    cfg = resolve_composio_config(
        {
            "COMPOSIO_API_KEY": "cp_test_secret",
            "MAGI_COMPOSIO_MCP_URL": override,
        }
    )

    assert cfg.active is False
    assert cfg.disabled_reason == "invalid_config"
    assert cfg.mcp_url_override is None


@pytest.mark.parametrize(
    "query_key",
    (
        "apiKey",
        "accessToken",
        "refreshToken",
        "clientSecret",
    ),
)
def test_mcp_url_override_rejects_camel_case_credential_query_keys(
    query_key: str,
) -> None:
    from magi_agent.composio.config import resolve_composio_config

    cfg = resolve_composio_config(
        {
            "COMPOSIO_API_KEY": "cp_test_secret",
            "MAGI_COMPOSIO_MCP_URL": (
                f"https://mcp.composio.dev/mcp?{query_key}=tok_secret"
            ),
        }
    )

    assert cfg.active is False
    assert cfg.disabled_reason == "invalid_config"
    assert cfg.mcp_url_override is None


@pytest.mark.parametrize(
    "query",
    (
        "x-api-key=tok_secret",
        "xApiKey=tok_secret",
        "composioApiKey=tok_secret",
        "workspace=ok;apiKey=tok_secret",
    ),
)
def test_mcp_url_override_rejects_nested_or_prefixed_credential_query_keys(
    query: str,
) -> None:
    from magi_agent.composio.config import resolve_composio_config

    cfg = resolve_composio_config(
        {
            "COMPOSIO_API_KEY": "cp_test_secret",
            "MAGI_COMPOSIO_MCP_URL": f"https://mcp.composio.dev/mcp?{query}",
        }
    )

    assert cfg.active is False
    assert cfg.disabled_reason == "invalid_config"
    assert cfg.mcp_url_override is None


@pytest.mark.parametrize(
    "query",
    (
        "credential=tok_secret",
        "credentials=tok_secret",
        "password=tok_secret",
        "private_key=tok_secret",
        "x-key=tok_secret",
        "session=tok_secret",
        "workspace=ok%3BapiKey=tok_secret",
        "workspace=ok%253BapiKey=tok_secret",
        "workspace=ok%2526x-key=tok_secret",
    ),
)
def test_mcp_url_override_rejects_generic_credential_query_keys(
    query: str,
) -> None:
    from magi_agent.composio.config import resolve_composio_config

    cfg = resolve_composio_config(
        {
            "COMPOSIO_API_KEY": "cp_test_secret",
            "MAGI_COMPOSIO_MCP_URL": f"https://mcp.composio.dev/mcp?{query}",
        }
    )

    assert cfg.active is False
    assert cfg.disabled_reason == "invalid_config"
    assert cfg.mcp_url_override is None


def test_mcp_url_override_accepts_composio_https_url() -> None:
    from magi_agent.composio.config import resolve_composio_config

    cfg = resolve_composio_config(
        {
            "COMPOSIO_API_KEY": "cp_test_secret",
            "MAGI_COMPOSIO_ENABLED": "on",
            "MAGI_COMPOSIO_MCP_URL": "https://mcp.composio.dev/mcp/session-1",
        }
    )

    assert cfg.active is True
    assert cfg.disabled_reason is None
    assert cfg.mcp_url_override == "https://mcp.composio.dev/mcp/session-1"


def test_mcp_url_override_is_not_serialized_or_represented() -> None:
    from magi_agent.composio.config import resolve_composio_config

    cfg = resolve_composio_config(
        {
            "COMPOSIO_API_KEY": "cp_test_secret",
            "MAGI_COMPOSIO_MCP_URL": (
                "https://mcp.composio.dev/mcp/session-1?workspace=tok_secret"
            ),
        }
    )
    by_alias = cfg.model_dump(by_alias=True)
    repr_text = repr(cfg)
    json_dump = cfg.model_dump_json()
    rendered = repr_text + json_dump + str(by_alias)

    assert cfg.mcp_url_override == (
        "https://mcp.composio.dev/mcp/session-1?workspace=tok_secret"
    )
    assert by_alias["mcpUrlOverridePresent"] is True
    assert "mcpUrlOverride" not in by_alias
    assert "mcpUrlOverride" not in repr_text
    assert '"mcpUrlOverride":' not in json_dump
    assert "tok_secret" not in rendered
    assert "session-1" not in rendered


def test_missing_python_package_disabled_reason_is_preserved() -> None:
    from magi_agent.composio.config import ComposioConfig

    cfg = ComposioConfig(disabledReason="missing_python_package")

    assert cfg.disabled_reason == "missing_python_package"


def test_config_repr_and_json_do_not_expose_api_key() -> None:
    from magi_agent.composio.config import resolve_composio_config

    cfg = resolve_composio_config({"COMPOSIO_API_KEY": "cp_test_secret_123456"})
    rendered = repr(cfg) + cfg.model_dump_json()

    assert "cp_test_secret" not in rendered
    assert "123456" not in rendered
    assert "apiKeyPresent" in cfg.model_dump(by_alias=True)


def test_redaction_removes_keys_bearers_connect_urls_and_connected_account_ids() -> None:
    from magi_agent.composio.redaction import redact_composio_value

    value = {
        "message": (
            "COMPOSIO_API_KEY=cp_test_secret Authorization: Bearer abc.def "
            "https://connect.composio.dev/link/ln_abc123 connected_account_id=ca_12345"
        )
    }
    rendered = json.dumps(redact_composio_value(value), sort_keys=True)

    assert "cp_test_secret" not in rendered
    assert "abc.def" not in rendered
    assert "ln_abc123" not in rendered
    assert "ca_12345" not in rendered
    assert "[redacted-composio-secret]" in rendered


def test_redaction_removes_connected_account_and_session_mapping_keys() -> None:
    from magi_agent.composio.redaction import redact_composio_value

    value = {
        "connectedAccountId": "acct_live_12345",
        "connected_account_id": "ca_live_12345",
        "connectionId": "conn_live_12345",
        "connection_id": "conn_live_67890",
        "headers": {
            "x-composio-session": "sess_header_123",
            "x_composio_session": "sess_header_456",
            "composio-session": "sess_header_789",
            "composio_session": "sess_header_000",
        },
    }
    rendered = json.dumps(redact_composio_value(value), sort_keys=True)

    assert "acct_live_12345" not in rendered
    assert "ca_live_12345" not in rendered
    assert "conn_live_12345" not in rendered
    assert "conn_live_67890" not in rendered
    assert "sess_header_123" not in rendered
    assert "sess_header_456" not in rendered
    assert "sess_header_789" not in rendered
    assert "sess_header_000" not in rendered
    assert "[redacted-composio-id]" in rendered
    assert "[redacted-composio-secret]" in rendered


@pytest.mark.parametrize(
    "source",
    (
        "connectedAccountId: acct_live_12345",
        "connected_account_id='ca_live_12345'",
        '"connectionId": "conn_live_12345"',
        "'connection_id': 'conn_live_67890'",
        "x-composio-session: sess_header_123",
        "x_composio_session='sess_header_456'",
        '"composio-session": "sess_header_789"',
        "'composio_session': 'sess_header_000'",
        "ids ca_live_abcdef acct_live_abcdef",
    ),
)
def test_redaction_removes_connected_account_and_session_text_forms(source: str) -> None:
    from magi_agent.composio.redaction import redact_composio_text

    redacted = redact_composio_text(source)

    assert "acct_live" not in redacted
    assert "ca_live" not in redacted
    assert "conn_live" not in redacted
    assert "sess_header" not in redacted
    assert (
        "[redacted-composio-id]" in redacted
        or "[redacted-composio-secret]" in redacted
    )


@pytest.mark.parametrize(
    "source",
    (
        "[redacted-composio-connect-url]_secret_tail987654321",
        "[redacted-composio-connect-url]abcdefghijklmnopqrstuvwxyz",
        "[redacted-composio-connect-url].abcdefghijklmnopqrstuvwxyz",
        "[redacted-composio-connect-url]-abcdefghijklmnopqrstuvwxyz",
        "[redacted-composio-connect-url]:abcdefghijklmnopqrstuvwxyz",
        "Authorization: Bearer [redacted]defghijklmnopqrstuvwxyz0123456789",
        "Authorization: Bearer [redacted]abcdefghijklmnopqrstuvwxyz",
        "Authorization: Bearer [redacted].abcdefghijklmnopqrstuvwxyz",
        "Authorization: Bearer [redacted]-abcdefghijklmnopqrstuvwxyz",
        "Authorization: Bearer [redacted]:abcdefghijklmnopqrstuvwxyz",
    ),
)
def test_redaction_no_longer_nukes_marker_suffix_inputs(source: str) -> None:
    """C-11: the pre-C-11 ``_REDACTION_MARKER_SUFFIX_RE`` "self-undo" branch
    replaced the ENTIRE output with ``[redacted-composio-output]`` when a
    redaction marker accidentally grew a trailing alphanumeric suffix (a tell
    that the original pattern set was too aggressive / overlapping with itself
    -- a second regex cleaning up after the first regex's mistakes). C-11 deletes
    that branch and relies on the C-1 kernel + Composio-anchored rules to redact
    cleanly. These inputs must now redact (no raw secret-tail survives in the
    output) WITHOUT collapsing to the ``[redacted-composio-output]`` sentinel."""

    from magi_agent.composio.redaction import (
        redact_composio_text,
        redact_composio_value,
    )

    redacted = redact_composio_text(source)
    # Cure: output is no longer the full-nuke sentinel.
    assert redacted != "[redacted-composio-output]"
    # Some redaction marker still appears (input contained a marker; either the
    # original Composio marker prefix or the kernel ``[redacted]`` token).
    assert "[redacted" in redacted
    # Composio markers in the input survive (the kernel does not re-consume
    # them; that was the prior duplicate-redaction artifact this test guards
    # against).
    if "[redacted-composio-connect-url]" in source:
        assert "[redacted-composio-connect-url]" in redacted

    value_redacted = redact_composio_value({"probe": source})
    assert value_redacted == {"probe": redacted}


@pytest.mark.parametrize(
    "source",
    (
        "[redacted-composio-connect-url]. Next sentence",
        "Authorization: Bearer [redacted]. Next sentence",
    ),
)
def test_redaction_preserves_normal_punctuation_after_markers(source: str) -> None:
    """C-11: punctuation/whitespace immediately following a redaction marker
    must not be gobbled by either the Composio-anchored rules or the kernel
    sweep. Pre-redacted ``Authorization: Bearer [redacted]`` prefixes are now
    re-scrubbed by the C-1 kernel (producing ``[redacted][redacted]`` — strictly
    safe over-redaction); the assertion that matters here is that ``. Next
    sentence`` is preserved verbatim AND that no raw secret-shape leaks."""

    from magi_agent.composio.redaction import redact_composio_text

    redacted = redact_composio_text(source)
    # Trailing punctuation + clear-text words after the marker are preserved.
    assert redacted.endswith(". Next sentence")
    # No raw "Bearer <secret>" sequence leaks. Either the input was idempotent
    # (already-clean prefix preserved by the marker-with-prefix split, e.g.
    # ``Authorization: Bearer [redacted]``) or the kernel substituted the
    # entire auth prefix with a single ``[redacted]`` token.
    assert "Bearer " not in redacted or "Bearer [redacted]" in redacted


def test_composio_redaction_import_is_sdk_and_adk_clean() -> None:
    probe = textwrap.dedent(
        """
        from __future__ import annotations

        import json
        import sys

        import magi_agent.composio.redaction as redaction_module

        print(json.dumps({
            "redaction_imported": redaction_module is not None,
            "composio": sorted(
                key
                for key in sys.modules
                if key == "composio" or key.startswith("composio.")
            ),
            "adk_mcp_tool": sorted(
                key
                for key in sys.modules
                if key == "google.adk.tools.mcp_tool"
                or key.startswith("google.adk.tools.mcp_tool.")
            ),
        }, sort_keys=True))
        """
    )

    completed = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        check=False,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr

    observed = json.loads(completed.stdout)

    assert observed == {
        "redaction_imported": True,
        "composio": [],
        "adk_mcp_tool": [],
    }


@pytest.mark.parametrize(
    "source",
    (
        'COMPOSIO_API_KEY="cp_test_secret"',
        "COMPOSIO_API_KEY: cp_test_secret",
        "composio_api_key=cp_test_secret",
    ),
)
def test_redaction_removes_composio_api_key_aliases_quotes_and_colons(source: str) -> None:
    from magi_agent.composio.redaction import redact_composio_text

    redacted = redact_composio_text(source)

    assert "cp_test_secret" not in redacted
    assert "[redacted-composio-secret]" in redacted


@pytest.mark.parametrize(
    "source",
    (
        '"COMPOSIO_API_KEY": "cp_test_secret"',
        "'COMPOSIO_API_KEY': 'cp_test_secret'",
        "'composio_api_key': 'cp_test_secret'",
    ),
)
def test_redaction_removes_quoted_composio_api_key_log_fields(source: str) -> None:
    from magi_agent.composio.redaction import redact_composio_text

    redacted = redact_composio_text(source)

    assert "cp_test_secret" not in redacted
    assert "[redacted-composio-secret]" in redacted


@pytest.mark.parametrize(
    "source",
    (
        'COMPOSIO_API_KEY="abc/def+ghi=~tail" next=value',
        "COMPOSIO_API_KEY=abc/def+ghi=~tail, next=value",
        '"COMPOSIO_API_KEY": "abc/def+ghi=~tail", "next": true',
        "'composio_api_key': 'abc/def+ghi=~tail'}",
    ),
)
def test_redaction_removes_full_api_key_values_with_punctuation(source: str) -> None:
    from magi_agent.composio.redaction import redact_composio_text

    redacted = redact_composio_text(source)

    assert "abc/def+ghi=~tail" not in redacted
    assert "/def+ghi=~tail" not in redacted
    assert "[redacted-composio-secret]" in redacted


def test_redaction_removes_generic_api_key_mapping_values() -> None:
    from magi_agent.composio.redaction import redact_composio_value

    redacted = redact_composio_value({"api_key": "cp_test_secret"})

    assert redacted == {"api_key": "[redacted-composio-secret]"}


# ---------------------------------------------------------------------------
# C-11 secret-shape round-trip (kernel strict-superset proof).
#
# The pre-C-11 ~117-LOC generic alternation in ``composio/redaction.py`` (Bearer
# tokens, sk-/xox-/AIza/gh*_/github_pat_/AKIA/JWT shapes) is now delegated to
# the C-1 redaction kernel (``ops.safety.redact_private_text``). Each row below
# asserts that a representative secret-shape REAL-WORLD pattern is still
# redacted (raw token does NOT survive in the output), proving the kernel
# strictly supersedes the pre-C-11 alternation on every distinctive shape the
# legacy caught.
# ---------------------------------------------------------------------------


def _fake_secret(*parts: str) -> str:
    """Assemble a secret-shaped FAKE fixture from fragments at runtime so
    GitHub push-protection / scanners don't flag this source file."""

    return "".join(parts)


_F_BEARER = _fake_secret("Bearer ", "abcdEFGH1234ijklMNOP5678")
_F_SK = _fake_secret("sk-", "abcdEFGH1234ijklMNOP5678")
_F_GHP = _fake_secret("ghp_", "abcdEFGH1234ijklMNOP5678qrstUVWX")
_F_GH_PAT = _fake_secret("github_pat_", "abcdEFGH1234ijklMNOP")
_F_AIZA = _fake_secret("AIza", "SyA1234567890abcdefghijklmnopqrstuv")
_F_XOXB = _fake_secret("xoxb-", "1234567890-abcdefghijklmnop")
_F_AKIA = _fake_secret("AKIA", "IOSFODNN7EXAMPLE")
_F_JWT = _fake_secret(
    "eyJ",
    "hbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9",
    ".eyJzdWIiOiIxMjM0NTY3ODkwIn0",
    ".dQw4w9WgXcQabcdEFGH12",
)
_F_PRIVATE_PATH = "/Users/operator/.ssh/id_rsa"


@pytest.mark.parametrize(
    "raw_token",
    (
        _F_BEARER,
        _F_SK,
        _F_GHP,
        _F_GH_PAT,
        _F_AIZA,
        _F_XOXB,
        _F_AKIA,
        _F_JWT,
        _F_PRIVATE_PATH,
    ),
    ids=(
        "bearer",
        "openai_sk",
        "github_ghp",
        "github_pat",
        "google_aiza",
        "slack_xoxb",
        "aws_akia",
        "jwt",
        "private_path",
    ),
)
def test_c11_kernel_catches_every_legacy_secret_shape(raw_token: str) -> None:
    """C-11 strict-superset proof: each pre-C-11 generic secret shape (delegated
    to the C-1 kernel) still produces a redacted output with the raw token
    fully removed."""

    from magi_agent.composio.redaction import redact_composio_text

    source = f"context {raw_token} trailing"
    redacted = redact_composio_text(source)
    assert raw_token not in redacted, (
        f"{raw_token!r} survived C-11 kernel-delegated redaction: {redacted!r}"
    )
    assert "[redacted" in redacted


def test_c11_kernel_catches_legacy_bearer_token_in_composio_value() -> None:
    """``redact_composio_value`` also routes string values through the kernel
    via :func:`redact_composio_text`. Bearer tokens in nested mappings are
    redacted (no raw token survives)."""

    from magi_agent.composio.redaction import redact_composio_value

    redacted = redact_composio_value({"message": f"see {_F_BEARER} here"})
    assert isinstance(redacted, dict)
    assert _F_BEARER not in redacted["message"]


def test_c11_composio_url_with_embedded_token_still_redacts() -> None:
    """A Composio webhook URL with an embedded token is still redacted (the
    Composio-anchored URL rule consumes the whole URL including the token)."""

    from magi_agent.composio.redaction import redact_composio_text

    embedded = "https://connect.composio.dev/webhook/" + _fake_secret(
        "tok_", "abcdEFGH1234ijklMNOP"
    )
    redacted = redact_composio_text(f"see {embedded} for the link")
    assert "abcdEFGH1234ijklMNOP" not in redacted
    assert "[redacted-composio-connect-url]" in redacted


def test_c11_generic_bearer_redacted_via_kernel_not_composio_rule() -> None:
    """Generic ``Authorization: Bearer …`` outside any Composio context is
    redacted via the C-1 kernel (the pre-C-11 Composio-local Bearer rule was
    folded into the kernel)."""

    from magi_agent.composio.redaction import redact_composio_text

    redacted = redact_composio_text(f"Authorization: {_F_BEARER}")
    # The raw bearer token MUST NOT survive.
    assert _F_BEARER not in redacted
    # Either the kernel ``[redacted]`` or the composio session-token marker
    # appears — the contract is "no raw token", not a specific marker shape.
    assert "[redacted" in redacted


def test_c11_marker_suffix_machinery_was_deleted() -> None:
    """C-11 removed ``_REDACTION_MARKER_SUFFIX_RE`` and
    ``_has_redaction_marker_suffix_artifact``. They must no longer be
    importable from ``magi_agent.composio.redaction``."""

    from magi_agent.composio import redaction as _r

    assert not hasattr(_r, "_REDACTION_MARKER_SUFFIX_RE"), (
        "C-11: ``_REDACTION_MARKER_SUFFIX_RE`` should be deleted."
    )
    assert not hasattr(_r, "_has_redaction_marker_suffix_artifact"), (
        "C-11: ``_has_redaction_marker_suffix_artifact`` should be deleted."
    )
    assert not hasattr(_r, "has_redaction_marker_suffix"), (
        "C-11: ``has_redaction_marker_suffix`` (any public alias) "
        "should be deleted."
    )
    assert not hasattr(_r, "_OUTPUT_REPLACEMENT"), (
        "C-11: the ``[redacted-composio-output]`` nuke sentinel "
        "``_OUTPUT_REPLACEMENT`` should be deleted."
    )
