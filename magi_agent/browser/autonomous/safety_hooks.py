from __future__ import annotations

from magi_agent.web_acquisition.policy import url_policy_error

_SENSITIVE_MARKERS = ("/login", "/signin", "/auth/", "oauth", "payment", "checkout")


def navigation_block_reason(url: str) -> str | None:
    """Return an SSRF/egress reason code if ``url`` must be blocked, else None.

    Thin reuse of the shared web_acquisition firewall so the browser tool and
    the fetch tools cannot drift.
    """
    return url_policy_error(url)


def is_sensitive_url(url: str) -> bool:
    """True if navigating here should pause for human re-approval."""
    lowered = url.casefold()
    return any(marker in lowered for marker in _SENSITIVE_MARKERS)
