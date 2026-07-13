"""U2 -- egress destination extraction (pure module, no wiring).

Destination awareness for the ``egress_guard`` policy (design section 5.3).
This module answers ONE question, in three honest layers: "for this outbound
tool call or shell command, what network host is it about to talk to?" It does
NOT decide, record, or enforce anything -- U3 (audit emission) and U4 (block
mode) wire it.

Three layers, each honest about its precision:

1. **Tool-argument layer** (:func:`extract_tool_destination`). Reuses the
   existing best-effort URL/host arg extractor
   :func:`magi_agent.credentials_admin.approval_resolver.extract_egress_host`
   as the base and extends it with the per-tool rules that generic key scan
   cannot know (the ``del tool_name`` at approval_resolver.py:68 reserved this
   seam): ``web_fetch`` / ``browser_*`` -> the URL host; ``web_search`` -> the
   search PROVIDER host (Brave or SerpAPI), never the query text.

2. **Shell layer** (:func:`extract_shell_destinations`). Parses host tokens
   from curl / wget / ssh / scp / rsync / nc / ftp / sftp argument vectors,
   including pipes and compounds. Best-effort: Bash obfuscation (variable
   expansion, command substitution, unbalanced quoting) is DETECTED and
   recorded as an extraction failure -- never guessed into a wrong host.

3. **Proxy layer** -- authoritative, existing, and out of scope here; the
   egress proxy sees true destinations when the operator opts in.

THE ONE LOAD-BEARING GUARANTEE: an obfuscated, ambiguous, oversized, or
injection-shaped destination lands in ``extraction == "failed"`` with
``host is None``. This module NEVER surfaces a plausible-but-wrong host.
Extracted hosts are attacker-controlled input (a URL argument can be crafted),
so every host is passed through :func:`validate_host` -- RFC-1123 syntactic
check, length bounding, lowercasing, no port / userinfo / path residue --
before it is placed on a result. Anything that fails validation becomes a
failed extraction.

Stdlib-only, plus the in-tree pure seam ``approval_resolver.extract_egress_host``.
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlsplit

ExtractionStatus = Literal["args", "shell", "failed"]


@dataclass(frozen=True)
class EgressDestination:
    """A single extracted outbound destination.

    ``host`` is the validated, lowercased, no-port hostname (or bracket-free
    IPv6 literal), or ``None`` when extraction failed. ``port`` is the parsed
    port when the source carried one, else ``None``. ``extraction`` records
    which layer produced the result: ``"args"`` (tool argument layer),
    ``"shell"`` (shell command layer), or ``"failed"`` (no host could be
    safely extracted).
    """

    host: str | None
    port: int | None
    extraction: ExtractionStatus


_FAILED = EgressDestination(host=None, port=None, extraction="failed")


# --------------------------------------------------------------------------- #
# Host validation + bounding (design 5.3 / 11 / N-1).
# --------------------------------------------------------------------------- #

# RFC-1123 label: 1-63 chars, alphanumeric, internal hyphens allowed, no leading
# or trailing hyphen. A hostname is one or more such labels joined by dots.
_LABEL_RE = re.compile(r"^(?!-)[A-Za-z0-9-]{1,63}(?<!-)$")

_MAX_HOSTNAME_LEN = 253


def validate_host(raw: object) -> str | None:
    """Validate and bound an attacker-controlled host string.

    Returns the lowercased, no-port host on success (a bracketed IPv6 literal
    is returned de-bracketed and lowercased), else ``None``. Rejects: empty /
    whitespace, port / userinfo / path / scheme residue, control characters,
    wildcard patterns, oversized (> 253) or over-long-label (> 63) names, and
    any injection-shaped string. NEVER raises.
    """
    if not isinstance(raw, str):
        return None
    value = raw.strip()
    if not value:
        return None

    # Bracketed IPv6 literal: validate the inner address, return de-bracketed.
    if value.startswith("[") and value.endswith("]"):
        return _validate_ipv6(value[1:-1])

    # A bare IPv6 literal (contains ':') is valid only as a real IPv6 address;
    # a stray ':' otherwise is a port/authority residue and must be rejected.
    if ":" in value:
        return _validate_ipv6(value)

    if len(value) > _MAX_HOSTNAME_LEN:
        return None

    lowered = value.lower()
    labels = lowered.split(".")
    if any(label == "" for label in labels):  # empty label: leading/trailing/double dot
        return None
    if not all(_LABEL_RE.match(label) for label in labels):
        return None
    return lowered


def _validate_ipv6(value: str) -> str | None:
    """Validate a bare IPv6 address string; return it lowercased, else None."""
    import ipaddress

    try:
        addr = ipaddress.IPv6Address(value)
    except ValueError:
        return None
    return str(addr).lower()


# --------------------------------------------------------------------------- #
# web_search provider mapping (design 5.3: destination is the provider host).
# --------------------------------------------------------------------------- #

# Kept in lockstep with tools/web_search_tools.py provider endpoints. The
# destination of a web_search call is the SEARCH PROVIDER, never the query.
_BRAVE_HOST = "api.search.brave.com"
_SERPAPI_HOST = "serpapi.com"

_WEB_SEARCH_TOOLS = frozenset({"web_search"})


def _resolve_web_search_host(env: dict[str, str] | None) -> str:
    """Provider host for a web_search call, mirroring _resolve_search_provider.

    Returns SerpAPI's host iff ``MAGI_WEB_SEARCH_PROVIDER`` (stripped,
    lowercased) equals ``"serpapi"`` AND ``SERPAPI_API_KEY`` is non-empty, else
    Brave's host. Never raises.
    """
    source = env if isinstance(env, dict) else {}
    selected = str(source.get("MAGI_WEB_SEARCH_PROVIDER", "")).strip().lower()
    if selected == "serpapi" and str(source.get("SERPAPI_API_KEY", "")).strip():
        return _SERPAPI_HOST
    return _BRAVE_HOST


# --------------------------------------------------------------------------- #
# Tool-argument layer (design 5.3 layer 1).
# --------------------------------------------------------------------------- #


def extract_tool_destination(
    tool_name: str,
    arguments: object,
    *,
    env: dict[str, str] | None = None,
) -> EgressDestination:
    """Extract the first-hop destination for an outbound tool call.

    ``web_search`` maps to the provider host (never the query). All other net
    tools extract the host (and port) from their URL/host-shaped argument via
    the shared :func:`extract_egress_host` seam, then validate/bound it. A
    call with no extractable, valid destination returns a failed extraction.
    Never raises.
    """
    if tool_name in _WEB_SEARCH_TOOLS:
        return EgressDestination(
            host=_resolve_web_search_host(env), port=None, extraction="args"
        )

    if not isinstance(arguments, dict):
        return _FAILED

    # Reuse the shared best-effort extractor for the host; re-parse locally for
    # the port so the two never disagree about the authority. Lazy-imported to
    # avoid a security->credentials_admin top-level layering edge.
    from magi_agent.credentials_admin.approval_resolver import extract_egress_host

    raw_host = extract_egress_host(tool_name, arguments)
    if raw_host is None:
        return _FAILED

    host = validate_host(raw_host)
    if host is None:
        return _FAILED

    port = _port_from_arguments(arguments)
    return EgressDestination(host=host, port=port, extraction="args")


_HOST_ARG_KEYS = ("url", "uri", "endpoint", "target", "host")


def _port_from_arguments(arguments: dict[str, object]) -> int | None:
    """Best-effort port from the same URL/host arg extract_egress_host used."""
    for key in _HOST_ARG_KEYS:
        raw = arguments.get(key)
        if not isinstance(raw, str) or not raw.strip():
            continue
        value = raw.strip()
        candidate = value if "//" in value else f"//{value}"
        try:
            port = urlsplit(candidate, scheme="https").port
        except ValueError:
            return None
        return port
    return None


# --------------------------------------------------------------------------- #
# Shell layer (design 5.3 layer 2).
# --------------------------------------------------------------------------- #

# Network executables whose argument vectors carry a destination host. Kept in
# lockstep with tools/safety.py:_NETWORK_EXECUTABLES.
_NETWORK_EXECUTABLES = frozenset(
    {"curl", "ftp", "nc", "rsync", "scp", "sftp", "ssh", "wget"}
)

# Shell metacharacters that make a token's host un-extractable: any presence in
# a host-bearing token means the real destination is hidden and must fail.
_OBFUSCATION_RE = re.compile(r"[$`]")


def extract_shell_destinations(command: str) -> tuple[EgressDestination, ...]:
    """Extract destinations from a shell command, one per network segment.

    Splits the command into pipe/compound segments and, for each segment whose
    executable is a network command, extracts the destination host from its
    argument vector. A segment whose host hides behind variable expansion or
    command substitution, or a command that cannot be parsed at all, yields a
    failed extraction rather than a guessed host. A command with no network
    command yields an empty tuple. Never raises.
    """
    if not isinstance(command, str) or not command.strip():
        return ()

    segments = _parsed_command_segments(command)
    if not segments:
        # Unparseable (e.g. unbalanced quote) but a network executable is named:
        # record a failed extraction so the blind spot is visible; otherwise
        # nothing outbound to report.
        if re.search(r"\b(?:curl|ftp|nc|rsync|scp|sftp|ssh|wget)\b", command):
            return (_FAILED,)
        return ()

    results: list[EgressDestination] = []
    for parts in segments:
        if not parts:
            continue
        exe = parts[0].rsplit("/", 1)[-1]
        if exe not in _NETWORK_EXECUTABLES:
            continue
        results.append(_destination_from_segment(exe, parts[1:]))
    return tuple(results)


# curl/wget flags that take a following value token, so that value is not a
# destination candidate (e.g. ``curl -X POST url`` -> POST is not a host).
_VALUE_FLAGS = frozenset(
    {
        "-X",
        "--request",
        "-d",
        "--data",
        "--data-binary",
        "--data-raw",
        "-H",
        "--header",
        "-o",
        "--output",
        "-u",
        "--user",
        "-e",
        "--referer",
        "-A",
        "--user-agent",
        "-b",
        "--cookie",
        "-p",
        "-l",
        "-s",
        "-i",
        "-O",  # ssh/scp/rsync option-with-value forms
    }
)


def _destination_from_segment(exe: str, args: tuple[str, ...]) -> EgressDestination:
    """Extract the destination host from one network command's argument vector.

    Strategy that never surfaces a wrong host: any obfuscated host-bearing token
    fails the whole segment; a URL token (``scheme://``) is the strongest signal
    and wins for any executable; otherwise per-executable fallbacks pick only
    tokens whose SHAPE identifies a destination (ssh/scp/rsync remote targets,
    the nc host token), skipping flags and their values.
    """
    candidates = _candidate_tokens(args)
    if candidates is None:
        return _FAILED

    # 1. A URL token is unambiguous for any network command.
    for token in candidates:
        if "://" in token:
            host, port = _host_port_from_token(exe, token)
            return _finalize(host, port)

    # 2. ssh/scp/rsync: pick the remote-target token (user@host or host:/path).
    if exe in {"ssh", "scp", "rsync", "sftp"}:
        for token in candidates:
            if "@" in token or ":" in token:
                host, port = _host_port_from_token(exe, token)
                return _finalize(host, port)
        # ssh to a bare hostname (no user@, no colon): a single bare token that
        # validates as a host is the destination.
        for token in candidates:
            if validate_host(token) is not None:
                return EgressDestination(
                    host=validate_host(token), port=None, extraction="shell"
                )
        return _FAILED

    # 3. curl/wget/nc/ftp: no URL found -> the first bare token that validates
    # as a host is the destination (nc host port; curl bare-host form).
    for token in candidates:
        host = validate_host(token)
        if host is not None:
            return EgressDestination(host=host, port=None, extraction="shell")
    return _FAILED


def _candidate_tokens(args: tuple[str, ...]) -> list[str] | None:
    """Non-flag, non-flag-value tokens. Returns None if any host token is obfuscated.

    Any ``$`` / backtick anywhere in a non-flag token means a hidden host, so
    the whole segment fails (the caller returns a failed extraction).
    """
    candidates: list[str] = []
    skip_next = False
    for arg in args:
        if skip_next:
            skip_next = False
            continue
        if arg in _VALUE_FLAGS:
            skip_next = True
            continue
        if arg.startswith("-"):
            continue  # a flag without a separately-tokenized value
        if _OBFUSCATION_RE.search(arg):
            return None
        candidates.append(arg)
    return candidates


def _finalize(host: str | None, port: int | None) -> EgressDestination:
    if host is None:
        return _FAILED
    validated = validate_host(host)
    if validated is None:
        return _FAILED
    return EgressDestination(host=validated, port=port, extraction="shell")


def _host_port_from_token(exe: str, token: str) -> tuple[str | None, int | None]:
    """Parse (host, port) from a single argument token for a network command.

    Handles ``scheme://[user@]host[:port]/...`` URLs, ``user@host`` ssh/scp
    forms, ``user@host:/path`` scp/rsync remote targets, and bare hostnames.
    Returns ``(None, None)`` when the token carries no host.
    """
    if _OBFUSCATION_RE.search(token):
        return (None, None)

    # Full URL (curl/wget): let urlsplit find the authority host + port.
    if "://" in token:
        try:
            parsed = urlsplit(token)
        except ValueError:
            return (None, None)
        return (parsed.hostname, _safe_port(parsed))

    # scp/rsync remote target: user@host:/path or host:/path (colon precedes a
    # path, not a numeric port). ssh: user@host (no colon).
    userinfo_host = token
    if "@" in token:
        userinfo_host = token.rsplit("@", 1)[1]

    if ":" in userinfo_host:
        head, _, tail = userinfo_host.partition(":")
        # nc host port is space-separated (two tokens), so a colon here is a
        # remote path separator (scp/rsync) -> host is the head, no numeric port.
        if tail == "" or not tail.split("/", 1)[0].isdigit():
            return (head, None)
        # A numeric port after the colon (rare in these forms) -> parse it.
        return (head, int(tail.split("/", 1)[0]))

    return (userinfo_host or None, None)


def _safe_port(parsed: object) -> int | None:
    try:
        return getattr(parsed, "port", None)
    except ValueError:
        return None


# --------------------------------------------------------------------------- #
# Shell segmentation (duplicated from tools/safety.py:_parsed_command_segments).
#
# Duplicated rather than imported to keep this module a pure, low-coupling leaf
# (design 5.3 / conflict-watch U2: prefer duplicating the small parse helper
# over importing safety, which is a G-unit decomposition target). The two must
# stay behaviorally equivalent; this is the same posix shlex segmentation.
# --------------------------------------------------------------------------- #


def _parsed_command_segments(command: str) -> tuple[tuple[str, ...], ...]:
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
        lexer.whitespace_split = True
        lexer.commenters = ""
        tokens = tuple(lexer)
    except ValueError:
        return ()

    parsed: list[tuple[str, ...]] = []
    segment: list[str] = []
    for token in tokens:
        if token in {"|", ";", "&&", "||"}:
            if segment:
                parsed.append(tuple(segment))
                segment = []
            continue
        segment.append(token)
    if segment:
        parsed.append(tuple(segment))
    return tuple(parsed)
