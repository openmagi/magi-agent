# tests/egress_proxy/test_gate5b_wiring.py
from magi_agent.egress_proxy.config import EgressProxyConfig
from magi_agent.gates import gate5b_full_toolhost as g5


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
