# tests/egress_proxy/test_gate5b_wiring.py
import hashlib

import pytest

from magi_agent.egress_proxy.config import EgressProxyConfig
from magi_agent.gates import gate5b_full_toolhost as g5


def _sha256(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def test_bash_env_byte_identical_when_disabled(monkeypatch):
    monkeypatch.delenv("MAGI_EGRESS_PROXY_ENABLED", raising=False)
    env = g5._build_bash_env(EgressProxyConfig.from_env({}))
    assert set(env.keys()) == {"PATH"}


def test_bash_env_has_overlay_when_enabled(tmp_path):
    ca = tmp_path / "ca.pem"; ca.write_text("x")
    cfg = EgressProxyConfig(True, "http://127.0.0.1:8888", None, str(ca))
    env = g5._build_bash_env(cfg)
    assert env["HTTPS_PROXY"] == "http://127.0.0.1:8888"
    assert env["PATH"]


def test_bash_env_fails_closed_when_enabled_proxy_url_missing(tmp_path):
    ca = tmp_path / "ca.pem"; ca.write_text("x")
    cfg = EgressProxyConfig(True, None, None, str(ca))
    with pytest.raises(ValueError, match="proxy URL missing"):
        g5._build_bash_env(cfg)


@pytest.mark.asyncio
async def test_gate5b_bash_printenv_https_proxy_does_not_expose_proxy_auth(
    monkeypatch,
    tmp_path,
):
    ca = tmp_path / "ca.pem"; ca.write_text("x")
    monkeypatch.setenv("MAGI_EGRESS_PROXY_ENABLED", "1")
    monkeypatch.setenv("MAGI_EGRESS_PROXY_URL", "http://127.0.0.1:8888")
    monkeypatch.setenv("MAGI_EGRESS_PROXY_AUTH", "synthetic-agent:synth-secret-123")
    monkeypatch.setenv("MAGI_EGRESS_PROXY_CA_CERT_PATH", str(ca))
    bundle = g5.build_gate5b_full_toolhost_bundle(
        config=g5.Gate5BFullToolHostConfig.model_validate(
            {
                "enabled": True,
                "killSwitchEnabled": False,
                "routeAttachmentEnabled": True,
                "selectedBotDigest": _sha256("bot-test"),
                "selectedOwnerDigest": _sha256("user-test"),
                "environment": "production",
                "environmentAllowlist": ("production",),
                "allowedToolNames": ("Bash",),
                "maxToolCallsPerTurn": 1,
            }
        ),
        scope={
            "selectedBotDigest": _sha256("bot-test"),
            "selectedOwnerDigest": _sha256("user-test"),
            "environment": "production",
        },
        workspace_root=tmp_path,
    )

    outcome = await bundle.host.dispatch(
        "Bash",
        {"command": "printenv HTTPS_PROXY"},
        request_digest=_sha256("request-printenv-proxy"),
        tool_call_id="call-printenv-proxy",
    )

    assert outcome.status == "ok"
    assert isinstance(outcome.output_preview, dict)
    stdout = str(outcome.output_preview["stdout"])
    assert stdout == "http://127.0.0.1:8888\n"
    assert "synthetic-agent" not in stdout
    assert "synth-secret-123" not in stdout
    assert "@" not in stdout


@pytest.mark.asyncio
async def test_gate5b_bash_output_redacts_synthetic_proxy_credential_urls(tmp_path):
    bundle = g5.build_gate5b_full_toolhost_bundle(
        config=g5.Gate5BFullToolHostConfig.model_validate(
            {
                "enabled": True,
                "killSwitchEnabled": False,
                "routeAttachmentEnabled": True,
                "selectedBotDigest": _sha256("bot-test"),
                "selectedOwnerDigest": _sha256("user-test"),
                "environment": "production",
                "environmentAllowlist": ("production",),
                "allowedToolNames": ("Bash",),
                "maxToolCallsPerTurn": 1,
            }
        ),
        scope={
            "selectedBotDigest": _sha256("bot-test"),
            "selectedOwnerDigest": _sha256("user-test"),
            "environment": "production",
        },
        workspace_root=tmp_path,
    )

    outcome = await bundle.host.dispatch(
        "Bash",
        {
            "command": (
                "printf 'http://synthetic-agent:synth-secret-123@proxy.local:8080'"
            ),
        },
        request_digest=_sha256("request-redact-proxy-url"),
        tool_call_id="call-redact-proxy-url",
    )

    assert outcome.status == "ok"
    assert isinstance(outcome.output_preview, dict)
    stdout = str(outcome.output_preview["stdout"])
    assert "synthetic-agent" not in stdout
    assert "synth-secret-123" not in stdout
    assert "proxy.local:8080" in stdout
