from __future__ import annotations

import json


def test_composio_health_for_default_disabled_is_safe_and_actionable() -> None:
    from magi_agent.composio.config import resolve_composio_config
    from magi_agent.composio.health import composio_health_metadata

    metadata = composio_health_metadata(resolve_composio_config({}), package_available=False)

    assert metadata["configured"] is False
    assert metadata["active"] is False
    assert metadata["enabledMode"] == "off"
    assert metadata["credentialSource"] == "missing"
    assert metadata["disabledReason"] == "disabled_by_config"
    assert metadata["nextAction"] == "set MAGI_COMPOSIO_ENABLED=auto or on"


def test_composio_health_redacts_secret_and_url() -> None:
    from magi_agent.composio.config import resolve_composio_config
    from magi_agent.composio.health import composio_health_metadata
    from magi_agent.composio.mcp import ComposioToolsetBundle

    cfg = resolve_composio_config({"COMPOSIO_API_KEY": "cp_test_secret"})
    metadata = composio_health_metadata(
        cfg,
        ComposioToolsetBundle(
            active=False,
            status="error",
            reason="toolset_build_failed",
            lastErrorClass="RuntimeError",
            lastErrorPreview="https://connect.composio.dev/link/ln_abc cp_test_secret",
        ),
        package_available=True,
    )
    rendered = json.dumps(metadata, sort_keys=True)

    assert "cp_test_secret" not in rendered
    assert "ln_abc" not in rendered
    assert metadata["lastErrorClass"] == "RuntimeError"


def test_composio_health_redacts_composio_account_session_and_connect_url() -> None:
    from magi_agent.composio.config import resolve_composio_config
    from magi_agent.composio.health import composio_health_metadata
    from magi_agent.composio.mcp import ComposioToolsetBundle

    cfg = resolve_composio_config({"COMPOSIO_API_KEY": "cp_test_secret"})
    metadata = composio_health_metadata(
        cfg,
        ComposioToolsetBundle(
            active=False,
            status="error",
            reason="toolset_build_failed",
            lastErrorClass="RuntimeError",
            lastErrorPreview=(
                "connectedAccountId: acct_live_12345 "
                "x-composio-session: sess_123 "
                "https://connect.composio.dev/link/ln_secret"
            ),
        ),
        package_available=True,
    )
    rendered = json.dumps(metadata, sort_keys=True)

    assert "acct_live_12345" not in rendered
    assert "sess_123" not in rendered
    assert "ln_secret" not in rendered
    assert "[redacted-composio-id]" in rendered
    assert "[redacted-composio-secret]" in rendered
    assert "[redacted-composio-connect-url]" in rendered


def test_composio_health_reports_missing_optional_package() -> None:
    from magi_agent.composio.config import resolve_composio_config
    from magi_agent.composio.health import composio_health_metadata

    metadata = composio_health_metadata(
        resolve_composio_config(
            {"COMPOSIO_API_KEY": "cp_test_secret", "MAGI_COMPOSIO_ENABLED": "on"}
        ),
        package_available=False,
    )

    assert metadata["configured"] is True
    assert metadata["active"] is False
    assert metadata["disabledReason"] == "missing_python_package"
    assert metadata["nextAction"] == "install the composio optional extra to enable integrations"
