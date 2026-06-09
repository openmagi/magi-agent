import pytest
from magi_agent.egress_proxy.config import EgressProxyConfig


def _base_env():
    return {
        "MAGI_EGRESS_PROXY_ENABLED": "1",
        "MAGI_EGRESS_PROXY_URL": "http://127.0.0.1:8888",
        "MAGI_EGRESS_PROXY_CA_CERT_PATH": "",  # filled per-test
    }


def test_disabled_when_unset():
    cfg = EgressProxyConfig.from_env({})
    assert cfg.enabled is False
    assert cfg.proxy_url is None


@pytest.mark.parametrize("val,expected", [
    ("1", True), ("true", True), ("on", True), ("YES", True),
    ("0", False), ("false", False), ("", False),
])
def test_tristate_master_switch(val, expected):
    cfg = EgressProxyConfig.from_env({"MAGI_EGRESS_PROXY_ENABLED": val})
    assert cfg.enabled is expected


def test_validate_enabled_requires_url(tmp_path):
    ca = tmp_path / "ca.pem"; ca.write_text("x")
    cfg = EgressProxyConfig.from_env({
        "MAGI_EGRESS_PROXY_ENABLED": "1",
        "MAGI_EGRESS_PROXY_CA_CERT_PATH": str(ca),
    })
    with pytest.raises(ValueError, match="proxy URL"):
        cfg.validate()


def test_validate_enabled_requires_readable_ca():
    cfg = EgressProxyConfig.from_env({
        "MAGI_EGRESS_PROXY_ENABLED": "1",
        "MAGI_EGRESS_PROXY_URL": "http://127.0.0.1:8888",
        "MAGI_EGRESS_PROXY_CA_CERT_PATH": "/nonexistent/ca.pem",
    })
    with pytest.raises(ValueError, match="CA cert"):
        cfg.validate()


def test_validate_disabled_is_noop():
    EgressProxyConfig.from_env({}).validate()  # must not raise


def test_url_rejects_path_and_creds(tmp_path):
    ca = tmp_path / "ca.pem"; ca.write_text("x")
    cfg = EgressProxyConfig.from_env({
        "MAGI_EGRESS_PROXY_ENABLED": "1",
        "MAGI_EGRESS_PROXY_URL": "http://u:p@127.0.0.1:8888/path",
        "MAGI_EGRESS_PROXY_CA_CERT_PATH": str(ca),
    })
    with pytest.raises(ValueError):
        cfg.validate()


def test_auth_carried_separately(tmp_path):
    ca = tmp_path / "ca.pem"; ca.write_text("x")
    cfg = EgressProxyConfig.from_env({
        "MAGI_EGRESS_PROXY_ENABLED": "1",
        "MAGI_EGRESS_PROXY_URL": "http://127.0.0.1:8888",
        "MAGI_EGRESS_PROXY_AUTH": "agent:tok123",
        "MAGI_EGRESS_PROXY_CA_CERT_PATH": str(ca),
    })
    cfg.validate()
    assert cfg.proxy_auth == "agent:tok123"
    assert "tok123" not in (cfg.proxy_url or "")


def test_startup_validate_raises_on_enabled_misconfig():
    cfg = EgressProxyConfig.from_env({"MAGI_EGRESS_PROXY_ENABLED": "1"})
    with pytest.raises(ValueError):
        cfg.validate()
