"""Custom tool-permission rule matching (P2).

A ``tool_perm`` custom rule fires at ``before_tool_use`` to deny or ask-approval
for a tool call. Matching is purely deterministic on the tool name and/or the
fetch target domain and/or the file/path argument:

- ``{"tool": "web_fetch"}``               — matches calls to that tool.
- ``{"domain": "evil.com"}``              — denylist: matches calls whose URL host
                                            is (a subdomain of) that domain.
- ``{"domainAllowlist": ["sec.gov"]}``    — allowlist: matches URL-bearing calls
                                            whose host is NOT in the list (e.g. the
                                            SEC source-allowlist use case).
- ``{"path": "/Users/me/secret"}``        — denylist: matches calls whose file/path
                                            argument is at OR under that path
                                            prefix (workspace-lock denylist).
- ``{"pathAllowlist": ["/Users/me/x"]}``  — allowlist: matches path-bearing calls
                                            whose file/path argument is NOT under
                                            any listed prefix (workspace-lock
                                            allowlist, the C11 use case).

# scope: coding  (Intended scope per H4 OUR-SIDE rule — Phase 1 wires this.)

Flag-gated by ``MAGI_CUSTOMIZE_VERIFICATION_ENABLED`` + ``MAGI_CUSTOMIZE_CUSTOM_RULES_ENABLED``
→ returns ``None`` (no decision) when off, so the permission policy is
byte-identical to today.
"""

from __future__ import annotations

import os.path as _ospath
from typing import Any
from urllib.parse import urlparse

_URL_ARG_KEYS = ("url", "uri", "href", "link", "address", "endpoint")

# Argument keys file/edit/read tools surface for the target path. ``path`` is the
# overwhelming default (see ``tools/file_tool_manifests.py``); the rest are
# common aliases other tools (or user-defined ones) might use.
_PATH_ARG_KEYS = ("path", "file", "filename", "filepath", "filePath", "pathRef")


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


def _path_from_arguments(arguments: dict[str, Any]) -> str | None:
    """Extract a canonical absolute path from a tool's arguments.

    Returns ``None`` when no path-shaped argument is present. The match is
    intentionally narrow: a value must look like a path (absolute or relative
    starting with ``./``/``../``, or a string containing ``/``) AND not look like
    a URL (so a tool that surfaces a URL under ``path`` is not misclassified).
    Normalised via ``os.path.normpath`` so ``..`` segments are resolved before
    prefix matching.
    """
    for key in _PATH_ARG_KEYS:
        raw = arguments.get(key)
        if not isinstance(raw, str) or not raw:
            continue
        if raw.startswith(("http://", "https://", "file://")):
            continue
        if not (raw.startswith(("/", "./", "../")) or "/" in raw or "\\" in raw):
            continue
        return _ospath.normpath(raw)
    return None


def _domain_matches(host: str, domain: str) -> bool:
    domain = domain.lower().lstrip(".")
    return host == domain or host.endswith(f".{domain}")


def _path_under_prefix(path: str, prefix: str) -> bool:
    """True if ``path`` is at, or strictly under, ``prefix``.

    Both are normalised; trailing slashes on the prefix are ignored. A prefix
    match must hit a path-segment boundary (so ``/a/bar`` does NOT match
    ``/a/b``).
    """
    norm_prefix = _ospath.normpath(prefix)
    norm_path = _ospath.normpath(path)
    if norm_path == norm_prefix:
        return True
    sep = "/" if "/" in norm_prefix or "/" in norm_path else "\\"
    boundary = norm_prefix if norm_prefix.endswith(sep) else norm_prefix + sep
    return norm_path.startswith(boundary)


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
    path = match.get("path")
    if isinstance(path, str):
        target = _path_from_arguments(arguments)
        if target and _path_under_prefix(target, path):
            return True
    path_allowlist = match.get("pathAllowlist")
    if isinstance(path_allowlist, list) and path_allowlist:
        target = _path_from_arguments(arguments)
        if target and not any(
            isinstance(p, str) and _path_under_prefix(target, p) for p in path_allowlist
        ):
            return True
    return False


def matched_decision(
    *,
    tool_name: str,
    arguments: dict[str, Any],
    current_scope: str | None = None,
) -> tuple[str, str] | None:
    """Return ``(action, rule_id)`` for the first matching enabled tool_perm rule.

    ``action`` is ``"deny"`` or ``"ask"``. Returns ``None`` when the flags are off,
    no rule matches, or on any error (fail-open — never wedges a tool call).

    ``current_scope`` (Phase 2): when supplied, only rules whose ``scope`` covers
    the current turn are considered. Backwards-compat: ``None`` preserves the
    historic scope-blind behavior so legacy call sites keep working.
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
        # PR-D3: an active mode may force-activate a tool_perm rule for this turn
        # via its scoped_policy_ids (even if the rule is globally disabled). Same
        # flag gate as above, so force-include never bypasses an operator flag.
        from magi_agent.customize.scoped_policy import (  # noqa: PLC0415
            active_scoped_policy_ids,
            resolve_scoped_policy_overlay,
        )

        _scoped = active_scoped_policy_ids()
        _force_ids = (
            resolve_scoped_policy_overlay(
                _scoped, custom_rules=policy.custom_rules, dashboard_check_ids=()
            ).tool_perm_rule_ids
            if _scoped
            else ()
        )
        for rule in policy.enabled_tool_perm_rules(
            current_scope=current_scope, force_include_ids=_force_ids
        ):
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
