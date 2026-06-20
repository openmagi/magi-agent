from __future__ import annotations

from urllib.parse import urlparse

from magi_agent.security.ssrf import classify_url, coerce_ip

from .policy import SandboxDecision, SandboxPolicy, build_decision, digest_payload, require_safe_ref


# NOTE: C-6/C-7 consolidation.
#
# The legacy ``_METADATA_HOSTS`` set, ``_CREDENTIAL_QUERY_KEYS`` set,
# ``_classify_parsed_url`` function, AND its near-duplicate
# ``_classify_nested_url_without_deep_query`` function all moved to
# :mod:`magi_agent.security.ssrf` (single SSRF leaf) and
# :mod:`magi_agent.security.credential_vocab` (single credential vocab leaf).
# This module is now the SANDBOX POLICY layer — it owns the ``allow_network`` /
# ``network_allowlist`` policy wrapping around the shared classifier; it does
# NOT re-implement IP coercion, metadata-host membership, or credential-query
# detection.
#
# C-7 specifically: the in-file ``_classify_parsed_url`` vs.
# ``_classify_nested_url_without_deep_query`` pair collapses into the single
# ``security.ssrf.classify_url(..., recurse_query=bool)`` call.


def evaluate_network_access(policy: SandboxPolicy, *, url: str) -> SandboxDecision:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower().rstrip(".")
    safe_host, ssrf_reasons = classify_url(url)
    reason_codes = list(ssrf_reasons)

    if not policy.allow_network:
        reason_codes.append("network_disabled")
    elif policy.network_allowlist and (safe_host or _safe_host(host)) not in policy.network_allowlist:
        reason_codes.append("network_host_not_allowlisted")

    return build_decision(
        allowed=not reason_codes,
        operation="network",
        reason_codes=tuple(reason_codes),
        target_digest=digest_payload(
            {
                "scheme": parsed.scheme,
                "host": host,
                "pathDigest": digest_payload({"path": parsed.path or "/"}),
                "queryDigest": digest_payload({"query": parsed.query}),
            }
        ),
        target_kind="url",
        host=(safe_host or _safe_host(host)) if host else None,
        policy=policy,
    )


def classify_network_url(url: str) -> tuple[str | None, tuple[str, ...]]:
    """Return ``(safe_host, reason_codes)`` — sandbox-side wrapper around
    :func:`magi_agent.security.ssrf.classify_url`.

    Preserved as a public symbol because ``tools/media_egress.py`` /
    ``sandbox/process.py`` already import it. The body is now a one-line
    delegation so the SSRF classifier stays in one place.
    """
    return classify_url(url)


def _safe_host(host: str) -> str:
    """Sandbox-policy-side safe-host wrapper.

    Returns ``"invalid_host"`` for empty, ``"ip_host"`` for any IP literal
    (including legacy IPv4 forms / NAT64 IPv6), and the snake-cased
    ``require_safe_ref``-validated DNS label otherwise. Kept here because the
    sandbox policy layer wants the validation to flow through its own
    ``require_safe_ref`` so the resulting host string is safe to embed in a
    :class:`SandboxDecision` projection.
    """
    if not host:
        return "invalid_host"
    if coerce_ip(host) is None:
        return require_safe_ref(host.replace("-", "_"), field_name="host")
    return "ip_host"


__all__ = ["classify_network_url", "evaluate_network_access"]
