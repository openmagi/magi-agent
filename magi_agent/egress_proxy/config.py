from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from urllib.parse import urlparse

# I-2 PR A: the per-module ``_truthy`` is removed in favour of the canonical
# leaf so the truthy set lives in exactly one place. Use :func:`is_true`
# directly at call sites.
from magi_agent.config._truthy import is_true as _truthy


def _validate_proxy_origin(value: str) -> str:
    cleaned = str(value or "").strip()
    parsed = urlparse(cleaned)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("egress proxy URL must be an HTTP(S) proxy origin")
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise ValueError("egress proxy URL must not contain path/query/fragment")
    if parsed.username or parsed.password:
        raise ValueError("egress proxy URL must not embed credentials; use MAGI_EGRESS_PROXY_AUTH")
    if any(c.isspace() for c in cleaned):
        raise ValueError("egress proxy URL must not contain whitespace")
    return cleaned


@dataclass(frozen=True)
class EgressProxyConfig:
    enabled: bool
    proxy_url: str | None
    proxy_auth: str | None = field(repr=False)  # secret: kept out of repr/tracebacks
    ca_cert_path: str | None = None

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "EgressProxyConfig":
        env = os.environ if env is None else env
        return cls(
            enabled=_truthy(env.get("MAGI_EGRESS_PROXY_ENABLED")),
            proxy_url=(env.get("MAGI_EGRESS_PROXY_URL") or "").strip() or None,
            proxy_auth=(env.get("MAGI_EGRESS_PROXY_AUTH") or "").strip() or None,
            ca_cert_path=(env.get("MAGI_EGRESS_PROXY_CA_CERT_PATH") or "").strip() or None,
        )

    def validate(self) -> None:
        if not self.enabled:
            return
        if not self.proxy_url:
            raise ValueError("MAGI_EGRESS_PROXY_ENABLED set but proxy URL missing")
        _validate_proxy_origin(self.proxy_url)
        if not self.ca_cert_path or not os.path.isfile(self.ca_cert_path):
            raise ValueError("MAGI_EGRESS_PROXY_ENABLED set but CA cert path missing/unreadable")
        try:
            with open(self.ca_cert_path, "r"):
                pass
        except OSError as exc:
            raise ValueError(f"CA cert path unreadable: {exc}") from exc
