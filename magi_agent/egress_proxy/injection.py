from __future__ import annotations

import base64

from magi_agent.egress_proxy.config import EgressProxyConfig

_CA_ENV_KEYS = (
    "SSL_CERT_FILE", "CURL_CA_BUNDLE", "REQUESTS_CA_BUNDLE",
    "NODE_EXTRA_CA_CERTS", "GIT_SSL_CAINFO",
)


def _validate_enabled(cfg: EgressProxyConfig) -> bool:
    if not cfg.enabled:
        return False
    cfg.validate()
    return True


def subprocess_env_overlay(cfg: EgressProxyConfig) -> dict[str, str]:
    if not _validate_enabled(cfg):
        return {}
    # Keep proxy auth out of subprocess env. Bash can print its environment, so
    # credentials must stay off arbitrary tool-visible stdout/stderr surfaces.
    assert cfg.proxy_url is not None
    proxy = cfg.proxy_url
    overlay = {"HTTPS_PROXY": proxy, "HTTP_PROXY": proxy, "ALL_PROXY": proxy}
    if cfg.ca_cert_path:
        for key in _CA_ENV_KEYS:
            overlay[key] = cfg.ca_cert_path
    return overlay


def httpx_client_kwargs(cfg: EgressProxyConfig) -> dict[str, object]:
    if not _validate_enabled(cfg):
        return {}
    import httpx

    assert cfg.proxy_url is not None
    headers = {}
    if cfg.proxy_auth:
        token = base64.b64encode(cfg.proxy_auth.encode()).decode()
        headers["Proxy-Authorization"] = f"Basic {token}"
    return {
        "proxy": httpx.Proxy(cfg.proxy_url, headers=headers or None),
        # ca_cert_path is guaranteed non-None when enabled because app startup
        # calls EgressProxyConfig.validate() (fail-closed). Do not call this
        # builder on an enabled-but-unvalidated config.
        "verify": cfg.ca_cert_path,
    }
