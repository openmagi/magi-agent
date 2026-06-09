from __future__ import annotations

import base64
from urllib.parse import urlsplit, urlunsplit

from magi_agent.egress_proxy.config import EgressProxyConfig

_CA_ENV_KEYS = (
    "SSL_CERT_FILE", "CURL_CA_BUNDLE", "REQUESTS_CA_BUNDLE",
    "NODE_EXTRA_CA_CERTS", "GIT_SSL_CAINFO",
)


def _url_with_auth(url: str, auth: str | None) -> str:
    if not auth:
        return url
    parts = urlsplit(url)
    netloc = f"{auth}@{parts.netloc}"
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


def subprocess_env_overlay(cfg: EgressProxyConfig) -> dict[str, str]:
    if not cfg.enabled or not cfg.proxy_url:
        return {}
    # Auth is embedded in the proxy URL here by necessity: CLI tools (curl, git)
    # read proxy creds from the *_PROXY URL, not a header. The httpx path below
    # keeps auth in a Proxy-Authorization header instead. Do not "unify" these.
    proxy = _url_with_auth(cfg.proxy_url, cfg.proxy_auth)
    overlay = {"HTTPS_PROXY": proxy, "HTTP_PROXY": proxy, "ALL_PROXY": proxy}
    if cfg.ca_cert_path:
        for key in _CA_ENV_KEYS:
            overlay[key] = cfg.ca_cert_path
    return overlay


def httpx_client_kwargs(cfg: EgressProxyConfig) -> dict[str, object]:
    if not cfg.enabled or not cfg.proxy_url:
        return {}
    import httpx

    headers = {}
    if cfg.proxy_auth:
        token = base64.b64encode(cfg.proxy_auth.encode()).decode()
        headers["Proxy-Authorization"] = f"Basic {token}"
    return {
        "proxy": httpx.Proxy(cfg.proxy_url, headers=headers or None),
        "verify": cfg.ca_cert_path,
    }
