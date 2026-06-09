import os

import pytest

from magi_agent.egress_proxy.config import EgressProxyConfig
from magi_agent.egress_proxy.injection import (
    subprocess_env_overlay,
    httpx_client_kwargs,
)


def _enabled(tmp_path, auth=None):
    ca = tmp_path / "ca.pem"; ca.write_text("x")
    return EgressProxyConfig(
        enabled=True,
        proxy_url="http://127.0.0.1:8888",
        proxy_auth=auth,
        ca_cert_path=str(ca),
    )


def test_overlay_empty_when_disabled():
    cfg = EgressProxyConfig(False, None, None, None)
    assert subprocess_env_overlay(cfg) == {}


def test_overlay_sets_proxy_and_ca(tmp_path):
    overlay = subprocess_env_overlay(_enabled(tmp_path))
    assert overlay["HTTPS_PROXY"] == "http://127.0.0.1:8888"
    assert overlay["HTTP_PROXY"] == "http://127.0.0.1:8888"
    for k in ("SSL_CERT_FILE", "CURL_CA_BUNDLE", "REQUESTS_CA_BUNDLE",
              "NODE_EXTRA_CA_CERTS", "GIT_SSL_CAINFO"):
        assert overlay[k].endswith("ca.pem")


def test_overlay_keeps_auth_out_of_subprocess_proxy_urls(tmp_path):
    overlay = subprocess_env_overlay(_enabled(tmp_path, auth="agent:tok"))
    for key in ("HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY"):
        assert overlay[key] == "http://127.0.0.1:8888"
        assert "agent" not in overlay[key]
        assert "tok" not in overlay[key]


@pytest.mark.parametrize("builder", [subprocess_env_overlay, httpx_client_kwargs])
def test_enabled_builders_fail_closed_when_proxy_url_missing(tmp_path, builder):
    ca = tmp_path / "ca.pem"; ca.write_text("x")
    cfg = EgressProxyConfig(True, None, None, str(ca))
    with pytest.raises(ValueError, match="proxy URL missing"):
        builder(cfg)


@pytest.mark.parametrize("builder", [subprocess_env_overlay, httpx_client_kwargs])
def test_enabled_builders_fail_closed_when_proxy_url_invalid(tmp_path, builder):
    ca = tmp_path / "ca.pem"; ca.write_text("x")
    cfg = EgressProxyConfig(True, "http://127.0.0.1:8888/path", None, str(ca))
    with pytest.raises(ValueError, match="path/query/fragment"):
        builder(cfg)


def test_httpx_kwargs_empty_when_disabled():
    cfg = EgressProxyConfig(False, None, None, None)
    assert httpx_client_kwargs(cfg) == {}


def test_httpx_kwargs_sets_proxy_and_verify(tmp_path):
    kwargs = httpx_client_kwargs(_enabled(tmp_path, auth="agent:tok"))
    assert kwargs["verify"].endswith("ca.pem")
    proxy = kwargs["proxy"]
    # httpx.Proxy carries url + Proxy-Authorization header
    assert "127.0.0.1:8888" in str(proxy.url)
    assert any(h.lower() == b"proxy-authorization" for h, _ in proxy.headers.raw)
    # §4 loggable-URL guarantee: auth stays in the header, never in the httpx URL
    assert "tok" not in str(proxy.url)


def test_https_proxy_origin_accepted(tmp_path):
    ca = tmp_path / "ca.pem"; ca.write_text("x")
    cfg = EgressProxyConfig(True, "https://127.0.0.1:8888", None, str(ca))
    cfg.validate()  # must not raise
    assert subprocess_env_overlay(cfg)["HTTPS_PROXY"] == "https://127.0.0.1:8888"


def test_builders_never_mutate_os_environ(tmp_path):
    before = dict(os.environ)
    cfg = _enabled(tmp_path, auth="agent:tok")
    subprocess_env_overlay(cfg)
    httpx_client_kwargs(cfg)
    assert dict(os.environ) == before
