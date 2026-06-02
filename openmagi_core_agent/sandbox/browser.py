from __future__ import annotations

from urllib.parse import urlparse
from urllib.parse import unquote

from .network import evaluate_network_access
from .policy import SandboxDecision, SandboxPolicy, build_decision


def evaluate_browser_request(policy: SandboxPolicy, *, url: str) -> SandboxDecision:
    network_decision = evaluate_network_access(policy, url=url)
    parsed = urlparse(url)
    inspected = " ".join(
        _decoded_fixed_point(part).lower()
        for part in (
            parsed.hostname or "",
            parsed.path or "",
            parsed.query or "",
            parsed.fragment or "",
        )
    )
    reason_codes = list(network_decision.reason_codes)
    if any(marker in inspected for marker in ("oauth", "login", "authorize", "auth")):
        reason_codes.append("auth_flow_blocked")
    if "captcha" in inspected:
        reason_codes.append("captcha_flow_blocked")

    return build_decision(
        allowed=not reason_codes,
        operation="browser",
        reason_codes=tuple(reason_codes),
        target_digest=network_decision.target_digest,
        target_kind="browser_url",
        host=network_decision.host,
        policy=policy,
    )


def _decoded_fixed_point(value: str, *, rounds: int = 4) -> str:
    current = value
    seen = {current}
    for _ in range(rounds):
        decoded = unquote(current)
        if decoded in seen:
            return decoded
        seen.add(decoded)
        current = decoded
    return current


__all__ = ["evaluate_browser_request"]
