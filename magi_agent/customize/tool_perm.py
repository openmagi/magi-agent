"""Custom tool-permission rule matching (P2).

A ``tool_perm`` custom rule fires at ``before_tool_use`` to deny or ask-approval
for a tool call. Matching is purely deterministic on the tool name and/or the
fetch target domain:

- ``{"tool": "web_fetch"}``               — matches calls to that tool.
- ``{"domain": "evil.com"}``              — denylist: matches calls whose URL host
                                            is (a subdomain of) that domain.
- ``{"domainAllowlist": ["sec.gov"]}``    — allowlist: matches URL-bearing calls
                                            whose host is NOT in the list (e.g. the
                                            SEC source-allowlist use case).

Flag-gated by ``MAGI_CUSTOMIZE_VERIFICATION_ENABLED`` + ``MAGI_CUSTOMIZE_CUSTOM_RULES_ENABLED``
→ returns ``None`` (no decision) when off, so the permission policy is
byte-identical to today.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

_URL_ARG_KEYS = ("url", "uri", "href", "link", "address", "endpoint")


def _host_from_arguments(arguments: dict[str, Any]) -> str | None:
    candidates: list[str] = []
    for key in _URL_ARG_KEYS:
        v = arguments.get(key)
        if isinstance(v, str):
            candidates.append(v)
    # Fall back: any string value that looks like an http(s) URL.
    for v in arguments.values():
        if isinstance(v, str) and v.startswith(("http://", "https://")):
            candidates.append(v)
    for raw in candidates:
        host = urlparse(raw if "://" in raw else f"//{raw}").hostname
        if host:
            return host.lower()
    return None


def _domain_matches(host: str, domain: str) -> bool:
    domain = domain.lower().lstrip(".")
    return host == domain or host.endswith(f".{domain}")


def _rule_matches(match: dict[str, Any], *, tool_name: str, arguments: dict[str, Any]) -> bool:
    tool = match.get("tool")
    if isinstance(tool, str) and tool == tool_name:
        return True
    domain = match.get("domain")
    if isinstance(domain, str):
        host = _host_from_arguments(arguments)
        if host and _domain_matches(host, domain):
            return True
    allowlist = match.get("domainAllowlist")
    if isinstance(allowlist, list) and allowlist:
        host = _host_from_arguments(arguments)
        if host and not any(
            isinstance(d, str) and _domain_matches(host, d) for d in allowlist
        ):
            return True
    return False


def matched_decision(
    *, tool_name: str, arguments: dict[str, Any]
) -> tuple[str, str] | None:
    """Return ``(action, rule_id)`` for the first matching enabled tool_perm rule.

    ``action`` is ``"deny"`` or ``"ask"``. Returns ``None`` when the flags are off,
    no rule matches, or on any error (fail-open — never wedges a tool call).
    """
    from magi_agent.config.flags import flag_profile_bool

    if not (
        flag_profile_bool("MAGI_CUSTOMIZE_VERIFICATION_ENABLED")
        and flag_profile_bool("MAGI_CUSTOMIZE_CUSTOM_RULES_ENABLED")
    ):
        return None
    try:
        from magi_agent.customize.store import load_overrides
        from magi_agent.customize.verification_policy import CustomizeVerificationPolicy

        policy = CustomizeVerificationPolicy.from_overrides(load_overrides())
        for rule in policy.enabled_tool_perm_rules():
            payload = rule.get("what", {}).get("payload", {})
            match = payload.get("match")
            if not isinstance(match, dict):
                continue
            if _rule_matches(match, tool_name=tool_name, arguments=arguments):
                decision = "ask" if payload.get("decision") == "ask" else "deny"
                rid = rule.get("id")
                return (decision, rid if isinstance(rid, str) else "custom")
        return None
    except Exception:
        return None
