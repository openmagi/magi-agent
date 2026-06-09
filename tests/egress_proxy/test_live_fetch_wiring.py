# tests/egress_proxy/test_live_fetch_wiring.py
from magi_agent.egress_proxy.config import EgressProxyConfig
from magi_agent.web_acquisition import live_fetch_provider as lf


def test_client_kwargs_empty_when_disabled():
    assert lf._egress_client_kwargs(EgressProxyConfig.from_env({})) == {}


def test_client_kwargs_present_when_enabled(tmp_path):
    ca = tmp_path / "ca.pem"; ca.write_text("x")
    cfg = EgressProxyConfig(True, "http://127.0.0.1:8888", None, str(ca))
    kwargs = lf._egress_client_kwargs(cfg)
    assert kwargs["verify"].endswith("ca.pem")
    assert "proxy" in kwargs
