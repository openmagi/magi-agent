"""External hook configuration loading.

Reads ``agent.hooks.yaml`` (or a user-supplied path) and registers external
hooks (``execution_type: command``, ``execution_type: http``, or
``execution_type: llm``) into the HookBus.  The feature is gated behind
``MAGI_EXTERNAL_HOOKS_ENABLED=true``.  LLM hooks can additionally be gated
via ``MAGI_LLM_HOOKS_ENABLED=true`` (when unset, LLM hooks are allowed as
long as the general external hooks gate is enabled).

YAML schema (subset)::

    hooks:
      - name: "ci-lint-check"
        point: "afterToolUse"
        matcher: "Edit"        # currently stored as description (future: matcher scope)
        execution_type: "command"
        command: "/usr/local/bin/lint-check.sh"
        timeoutMs: 10000
        failOpen: true
      - name: "security-webhook"
        point: "beforeToolUse"
        execution_type: "http"
        url: "https://security.internal/hooks/magi"
        http_headers:
          Authorization: "Bearer ${SECURITY_HOOK_TOKEN}"
        timeoutMs: 5000
        failOpen: false
      - name: "custom-safety-check"
        point: "beforeToolUse"
        execution_type: "llm"
        prompt_template: "Evaluate if this is safe: {tool_input}"
        max_prompt_tokens: 1500
        timeoutMs: 3000
        failOpen: true

Environment-variable substitution
----------------------------------
Values in ``http_headers`` (and ``command``, ``url``) that contain
``${VAR_NAME}`` patterns are expanded from ``os.environ`` at load time.
Missing variables resolve to an empty string with a warning log.
"""
from __future__ import annotations

import ipaddress
import logging
import os
import re
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

import yaml

if TYPE_CHECKING:
    from magi_agent.hooks.bus import RegisteredHook
from pydantic import BaseModel, ConfigDict, Field

from magi_agent.hooks.context import HookContext
from magi_agent.hooks.manifest import HookManifest, HookPoint
from magi_agent.hooks.result import HookResult
from magi_agent.tools.manifest import ToolSource

__all__ = [
    "ExternalHookConfig",
    "load_external_hooks_from_yaml",
    "_is_internal_url",
]

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Env-var substitution
# ---------------------------------------------------------------------------

# Matches ${VAR_NAME} where VAR_NAME is a valid POSIX variable name
# (starts with letter or underscore, followed by letters, digits, or underscores).
_ENV_VAR_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
# Detects any ${...} pattern (including malformed ones) so we can warn about them.
_ENV_VAR_ANY_RE = re.compile(r"\$\{([^}]*)\}")

# Only env vars with this prefix may be substituted into hook config values.
# This prevents operators from accidentally (or maliciously) embedding
# platform secrets such as ANTHROPIC_API_KEY or SUPABASE_SERVICE_ROLE_KEY
# into hook commands, URLs, or headers.
_SAFE_EXPAND_PREFIX = "MAGI_HOOK_"


# ---------------------------------------------------------------------------
# SSRF protection
# ---------------------------------------------------------------------------

# RFC 1918 private ranges, loopback, and cloud metadata endpoint.
_INTERNAL_NETWORKS: tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...] = (
    ipaddress.IPv4Network("127.0.0.0/8"),       # loopback
    ipaddress.IPv4Network("10.0.0.0/8"),        # RFC 1918
    ipaddress.IPv4Network("172.16.0.0/12"),     # RFC 1918
    ipaddress.IPv4Network("192.168.0.0/16"),    # RFC 1918
    ipaddress.IPv4Network("169.254.0.0/16"),    # link-local / cloud metadata
    ipaddress.IPv6Network("::1/128"),           # IPv6 loopback
    ipaddress.IPv6Network("fc00::/7"),          # IPv6 unique-local (RFC 4193)
)


def _is_internal_url(url: str) -> bool:
    """Return True if *url* resolves to an internal/private address that should
    not be reachable from external hook configurations.

    Blocks:
    - ``localhost`` hostname (case-insensitive)
    - Hostnames matching ``*.svc.cluster.local`` (Kubernetes internal DNS)
    - Literal IP addresses in RFC 1918, loopback, or cloud-metadata ranges
      (169.254.0.0/16)

    Does NOT perform DNS resolution — only literal IPs and blocked hostnames
    are rejected.  DNS-rebinding is a separate, runtime-level concern.

    Returns False (allow) when the URL is unparseable, so callers can still
    attempt construction and let Pydantic/httpx reject malformed URLs.
    """
    try:
        parsed = urlparse(url)
        host = parsed.hostname or ""
    except Exception:
        return False  # fail-open: let manifest validation handle it

    # Blocked hostnames
    if host.lower() == "localhost":
        return True
    if host.lower().endswith(".svc.cluster.local"):
        return True

    # Literal IP address check
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        return False  # not a literal IP — DNS not resolved here, allow through

    return any(addr in net for net in _INTERNAL_NETWORKS)


def _resolve_env_vars(value: str) -> str:
    """Replace ``${VAR_NAME}`` patterns with values from ``os.environ``.

    **Security:** Only variables whose names start with ``MAGI_HOOK_`` are
    substituted.  References to other variables are left as-is and a warning
    is logged at load time so operators notice the restriction immediately.
    This prevents hook config from reading platform secrets (API keys,
    Supabase credentials, etc.) even if an operator accidentally references
    them.

    Patterns with invalid POSIX names are left as-is and a warning is logged.
    Unknown ``MAGI_HOOK_*`` variables are replaced with an empty string and
    a warning is logged so operators notice missing secrets at startup time.
    """
    # Warn about any ${...} patterns that don't match the valid-name regex.
    for bad_match in _ENV_VAR_ANY_RE.finditer(value):
        if not _ENV_VAR_RE.match(bad_match.group(0)):
            logger.warning(
                "external hook config: '${%s}' is not a valid POSIX variable name; skipping substitution",
                bad_match.group(1),
            )

    def _replace(match: re.Match[str]) -> str:
        var_name = match.group(1)
        if not var_name.startswith(_SAFE_EXPAND_PREFIX):
            logger.warning(
                "external hook config: env var '%s' is not allowed for substitution "
                "(only MAGI_HOOK_* variables may be expanded to prevent secret leakage); "
                "leaving as-is",
                var_name,
            )
            return match.group(0)  # leave the ${VAR_NAME} token unchanged
        resolved = os.environ.get(var_name)
        if resolved is None:
            logger.warning(
                "external hook config: env var '%s' is not set; substituting empty string",
                var_name,
            )
            return ""
        return resolved

    return _ENV_VAR_RE.sub(_replace, value)


# ---------------------------------------------------------------------------
# Config model
# ---------------------------------------------------------------------------

class ExternalHookConfig(BaseModel):
    """Top-level configuration for the external hooks feature.

    Read from environment variables at construction time.

    ``MAGI_EXTERNAL_HOOKS_ENABLED`` gates all external hooks (command, http, llm).
    ``MAGI_LLM_HOOKS_ENABLED`` is an additional gate specifically for LLM hooks.
    When ``MAGI_LLM_HOOKS_ENABLED`` is not set, LLM hooks are allowed as long as
    the general external hooks gate is enabled.
    """
    model_config = ConfigDict(frozen=True)

    enabled: bool = Field(default=False)
    llm_hooks_enabled: bool = Field(default=True)

    @classmethod
    def from_env(cls) -> "ExternalHookConfig":
        """Build config by reading ``MAGI_EXTERNAL_HOOKS_ENABLED`` and
        ``MAGI_LLM_HOOKS_ENABLED`` from the environment."""
        # I-4: routed through the typed flag registry.
        from magi_agent.config.flags import flag_bool, flag_str  # noqa: PLC0415

        enabled = flag_bool("MAGI_EXTERNAL_HOOKS_ENABLED")
        # LLM hooks default to enabled (True) unless explicitly disabled.
        # ``MAGI_LLM_HOOKS_ENABLED`` is registered as ``str`` so the
        # default-ON-when-unset semantics are preserved (``flag_bool``
        # would default-OFF and silently flip live behavior).
        llm_raw = (flag_str("MAGI_LLM_HOOKS_ENABLED") or "").strip().lower()
        llm_enabled = llm_raw in ("1", "true", "yes") if llm_raw else True
        return cls(enabled=enabled, llm_hooks_enabled=llm_enabled)


# ---------------------------------------------------------------------------
# Placeholder async handler for external hooks
# ---------------------------------------------------------------------------

async def _external_hook_noop(ctx: HookContext) -> HookResult:  # noqa: ARG001
    """No-op async handler used as a placeholder for command/http hooks.

    The real execution is performed by the executor in HookBus, not by this
    handler.  It exists only to satisfy the ``RegisteredHook.handler`` typing.
    """
    return HookResult(action="continue")


# ---------------------------------------------------------------------------
# YAML → RegisteredHook list
# ---------------------------------------------------------------------------

_EXTERNAL_SOURCE = ToolSource(kind="external", package="agent.hooks.yaml")

# Maps snake_case YAML keys to the camelCase aliases expected by HookManifest.
# Defined at module level so it is constructed once rather than on every call.
_SNAKE_TO_ALIAS: dict[str, str] = {
    "execution_type": "executionType",
    "fail_open": "failOpen",
    "timeout_ms": "timeoutMs",
    "http_method": "httpMethod",
    "http_headers": "httpHeaders",
    "security_critical": "securityCritical",
    "if_condition": "if",
    "opt_out": "optOut",
    "prompt_template": "promptTemplate",
    "max_prompt_tokens": "maxPromptTokens",
}


def _build_manifest_from_yaml_entry(entry: dict[str, Any]) -> HookManifest:
    """Convert a single YAML hook entry dict into a ``HookManifest``.

    Applies env-var substitution to string values that may contain
    ``${VAR_NAME}`` patterns before the manifest is constructed.
    """
    # Normalise snake_case keys that the YAML might use for Pydantic alias fields.
    # Pydantic aliases take camelCase; we accept both for YAML usability.
    normalised: dict[str, Any] = {}
    for k, v in entry.items():
        normalised[_SNAKE_TO_ALIAS.get(k, k)] = v

    # Apply env-var substitution to all string leaf values.
    if "command" in normalised and isinstance(normalised["command"], str):
        normalised["command"] = _resolve_env_vars(normalised["command"])
    if "url" in normalised and isinstance(normalised["url"], str):
        resolved_url = _resolve_env_vars(normalised["url"])
        # SSRF protection: reject URLs targeting internal/private networks unless
        # the operator explicitly opts out via MAGI_HOOK_ALLOW_INTERNAL_URLS.
        # I-4: routed through the typed flag registry (canonical truthy
        # set widens trivially from ``{1, true, yes}`` to add ``on``).
        from magi_agent.config.flags import flag_bool  # noqa: PLC0415

        allow_internal = flag_bool("MAGI_HOOK_ALLOW_INTERNAL_URLS")
        if not allow_internal and _is_internal_url(resolved_url):
            raise ValueError(
                f"hook url '{resolved_url}' resolves to an internal/private address; "
                "refusing to register hook (set MAGI_HOOK_ALLOW_INTERNAL_URLS=true to override)"
            )
        normalised["url"] = resolved_url
    if "httpHeaders" in normalised and isinstance(normalised["httpHeaders"], dict):
        normalised["httpHeaders"] = {
            k: _resolve_env_vars(v) if isinstance(v, str) else v
            for k, v in normalised["httpHeaders"].items()
        }

    # description: if not provided, derive from name or matcher.
    if "description" not in normalised:
        normalised["description"] = normalised.get("matcher", normalised.get("name", "external hook"))

    # source is always the config file.
    normalised["source"] = _EXTERNAL_SOURCE

    # point: accept string or enum; convert string to enum member.
    if "point" in normalised and isinstance(normalised["point"], str):
        normalised["point"] = HookPoint(normalised["point"])

    # Remove 'matcher' key — it's not a HookManifest field (future scope feature).
    normalised.pop("matcher", None)

    return HookManifest(**normalised)


def load_external_hooks_from_yaml(
    path: str,
    config: ExternalHookConfig | None = None,
) -> "list[RegisteredHook]":
    """Read *path* and return a list of ``RegisteredHook`` instances.

    Returns an empty list when:
    - The file does not exist.
    - The ``hooks`` key is absent or empty.
    - Any individual entry fails to parse (logged as a warning; others continue).

    If *config* is provided, LLM hooks are filtered out when
    ``config.llm_hooks_enabled`` is False.  The caller is responsible for
    enabling the feature gate before calling this (i.e. checking
    ``ExternalHookConfig.from_env().enabled``).
    """
    # Lazy import to avoid circular dependency with bus.py which imports this.
    from magi_agent.hooks.bus import RegisteredHook

    if not os.path.isfile(path):
        logger.debug("external hooks config file not found: %s", path)
        return []

    try:
        with open(path, encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
    except Exception:
        logger.exception("failed to read external hooks config file: %s", path)
        return []

    if not isinstance(data, dict):
        logger.warning("external hooks config file is not a YAML mapping: %s", path)
        return []

    raw_hooks = data.get("hooks")
    if not raw_hooks:
        return []

    if not isinstance(raw_hooks, list):
        logger.warning("'hooks' key in %s must be a list", path)
        return []

    llm_enabled = config.llm_hooks_enabled if config is not None else True

    registered: list[RegisteredHook] = []
    for i, entry in enumerate(raw_hooks):
        if not isinstance(entry, dict):
            logger.warning("hooks[%d] in %s is not a mapping; skipping", i, path)
            continue
        try:
            manifest = _build_manifest_from_yaml_entry(entry)
        except Exception as exc:
            logger.warning(
                "hooks[%d] in %s failed to parse (%s); skipping",
                i,
                path,
                exc,
            )
            continue
        if manifest.execution_type == "llm" and not llm_enabled:
            logger.info(
                "hooks[%d] '%s' skipped: LLM hooks disabled (MAGI_LLM_HOOKS_ENABLED)",
                i,
                manifest.name,
            )
            continue
        registered.append(RegisteredHook(manifest=manifest, handler=_external_hook_noop))

    logger.info(
        "loaded %d external hook(s) from %s",
        len(registered),
        path,
    )
    return registered
