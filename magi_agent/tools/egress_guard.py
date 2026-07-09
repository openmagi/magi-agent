"""U3 -- egress_guard AUDIT mode wiring (observe-only, NEVER denies).

The pure destination extraction lives in :mod:`magi_agent.security.egress_destinations`
(U2). This module is the thin, flag-gated AUDIT surface that both emission sites
share (design 5.4):

* the permission boundary stashes the extracted destination onto the safety
  decision metadata so DENIED and ASKED egress attempts carry it, and
* the local evidence collector emits an ``EgressDestination`` evidence record
  for EXECUTED outbound calls.

Both sites agree on ONE definition of "is this an outbound call and where to":
net tools extract from their URL/host argument; shell tools extract from their
command string. When the master flag is OFF (or the runtime profile is
safe/eval), every entry point returns "nothing", so the paths are byte-identical
to before this policy existed. Never raises: a bad argument shape or a broken
extractor degrades to "no destination", never to a turn-breaking error.
"""

from __future__ import annotations

import os
from collections.abc import Mapping

from magi_agent.security.egress_destinations import (
    EgressDestination,
    extract_shell_destinations,
    extract_tool_destination,
    host_in_allowlist,
)

# Shell tool names whose ``command`` argument may carry a network destination.
# Kept small and explicit (the core execute tools); an unlisted tool is treated
# as non-outbound rather than guessed.
_SHELL_TOOL_NAMES: frozenset[str] = frozenset({"Bash", "TestRun"})

# Net tool names whose arguments carry an outbound URL/host. Mirrors the
# citation-capture external-read classification (web + browser), the surface
# that reaches ``allow`` today with zero friction and zero recording.
_NET_TOOL_NAMES: frozenset[str] = frozenset(
    {
        "web_fetch",
        "WebFetch",
        "fetch_url",
        "FetchUrl",
        "research_fact",
        "ResearchFact",
        "web_search",
        "WebSearch",
        "search_web",
        "SearchWeb",
        "browser_navigate",
        "browser_read",
        "BrowserNavigate",
        "BrowserRead",
        "browser_screenshot",
        "BrowserScreenshot",
    }
)

_SHELL_COMMAND_KEYS = ("command", "cmd", "script")


def egress_guard_enabled(env: Mapping[str, str] | None = None) -> bool:
    """Whether the egress_guard master switch resolves ON (profile-aware)."""
    from magi_agent.config.env import parse_egress_guard_enabled  # noqa: PLC0415

    source = env if env is not None else os.environ
    try:
        return parse_egress_guard_enabled(source)
    except Exception:  # noqa: BLE001 - a resolver problem must never break a turn
        return False


def egress_guard_mode(env: Mapping[str, str] | None = None) -> str:
    """The egress_guard enforcement mode (``audit`` default, or ``block``)."""
    from magi_agent.config.env import parse_egress_guard_mode  # noqa: PLC0415

    source = env if env is not None else os.environ
    try:
        return parse_egress_guard_mode(source)
    except Exception:  # noqa: BLE001
        return "audit"


def _first_command_string(arguments: object) -> str | None:
    if not isinstance(arguments, Mapping):
        return None
    for key in _SHELL_COMMAND_KEYS:
        raw = arguments.get(key)
        if isinstance(raw, str) and raw.strip():
            return raw
    return None


def extract_egress_destinations(
    tool_name: str,
    arguments: object,
    *,
    permission: str | None = None,
) -> tuple[EgressDestination, ...]:
    """Extracted outbound destinations for a tool call, or an empty tuple.

    Net tools yield the single first-hop destination (``extraction == "args"``);
    shell tools yield one destination per network segment (``"shell"``), which
    may include ``extraction == "failed"`` blind-spot entries for obfuscated
    hosts. A non-outbound tool yields ``()``. Never raises.

    A tool is treated as a net tool when its name is in the web/browser
    allowlist OR its manifest ``permission`` is ``"net"`` (the permission layer
    passes it; the collector, which has no manifest, relies on the name list).
    """
    try:
        if tool_name in _NET_TOOL_NAMES or permission == "net":
            return (extract_tool_destination(tool_name, arguments),)
        if tool_name in _SHELL_TOOL_NAMES:
            command = _first_command_string(arguments)
            if command is None:
                return ()
            return extract_shell_destinations(command)
    except Exception:  # noqa: BLE001 - extraction must never break the tool path
        return ()
    return ()


def _destination_payload(
    dest: EgressDestination,
    *,
    tool_name: str,
    mode: str,
) -> dict[str, object]:
    """The evidence/metadata shape (design 11): host, tool, extraction, mode."""
    return {
        "host": dest.host,
        "port": dest.port,
        "tool": tool_name,
        "extraction": dest.extraction,
        # ``allowlisted``/``decision`` are populated by U4's block mode; in audit
        # mode there is no allowlist verdict, so they are null.
        "allowlisted": None,
        "mode": mode,
        "decision": None,
    }


def maybe_stash_egress_destination(
    manifest: object,
    arguments: object,
    metadata: dict[str, object],
) -> None:
    """Stash the extracted egress destination onto a safety decision's metadata.

    F-2 single-site call: invoked immediately after the arbiter call in
    ``ToolPermissionPolicy.decide`` so all return paths (deny/ask/allow) carry
    ``egressDestination``. No-op when the master flag is OFF or the call is not
    outbound. Records the FIRST extracted destination (denied/asked attempts are
    single-destination in practice). Never raises, never changes the action.
    """
    try:
        if not egress_guard_enabled():
            return
        tool_name = getattr(manifest, "name", None)
        if not isinstance(tool_name, str):
            return
        permission = getattr(manifest, "permission", None)
        destinations = extract_egress_destinations(
            tool_name, arguments, permission=permission if isinstance(permission, str) else None
        )
        if not destinations:
            return
        metadata["egressDestination"] = _destination_payload(
            destinations[0], tool_name=tool_name, mode=egress_guard_mode()
        )
    except Exception:  # noqa: BLE001 - audit stash must never break a decision
        return


def egress_destination_records(
    tool_name: str,
    arguments: object,
) -> list[object]:
    """Build ``custom:EgressDestination`` evidence records for an executed call.

    One record per extracted destination (including ``extraction == "failed"``
    blind spots, so the audit ledger shows the gap count). Returns ``[]`` when
    the master flag is OFF or the call is not outbound. The records carry the
    default untrusted ``tool_declared`` origin and NO producing rule id, so they
    can never satisfy a ``requireEvidence`` unlock (design 11 note 3). Never
    raises.
    """
    try:
        if not egress_guard_enabled():
            return []
        destinations = extract_egress_destinations(tool_name, arguments)
        if not destinations:
            return []
        mode = egress_guard_mode()
        from magi_agent.evidence.types import EvidenceRecord  # noqa: PLC0415
        import time  # noqa: PLC0415

        records: list[object] = []
        for dest in destinations:
            records.append(
                EvidenceRecord.model_validate(
                    {
                        "type": "custom:EgressDestination",
                        "status": "ok",
                        "observedAt": time.time(),
                        # "tool_trace" is the only local-origin source kind; carry
                        # the tool name so the audit surface can attribute it.
                        "source": {"kind": "tool_trace", "toolName": tool_name},
                        "fields": _destination_payload(
                            dest, tool_name=tool_name, mode=mode
                        ),
                    }
                )
            )
        return records
    except Exception:  # noqa: BLE001 - record synthesis must never break a turn
        return []


# --------------------------------------------------------------------------- #
# U4 -- BLOCK mode: allowlist resolution + block evaluation                    #
# --------------------------------------------------------------------------- #


def resolve_allowlist(env: Mapping[str, str] | None = None) -> tuple[str, ...]:
    """The effective egress allowlist: persisted customize.json UNIONED with env.

    Env adds, never removes (a shell export cannot silently SHRINK a
    user-authored list, design 5.5). Patterns are lowercased and de-duplicated,
    persisted-first order preserved. Never raises: a store/read problem degrades
    to whatever the env provides (or an empty list).
    """
    source = env if env is not None else os.environ
    patterns: list[str] = []
    seen: set[str] = set()

    def _add(raw: object) -> None:
        if isinstance(raw, str):
            token = raw.strip().lower()
            if token and token not in seen:
                seen.add(token)
                patterns.append(token)

    # Persisted customize.json egress_guard.allowlist.
    try:
        from magi_agent.customize.store import load_overrides  # noqa: PLC0415

        section = load_overrides().get("egress_guard")
        if isinstance(section, Mapping):
            persisted = section.get("allowlist")
            if isinstance(persisted, (list, tuple)):
                for item in persisted:
                    _add(item)
    except Exception:  # noqa: BLE001 - a store problem must never break a decision
        pass

    # Env union.
    try:
        from magi_agent.config.env import parse_egress_guard_allowlist  # noqa: PLC0415

        for item in parse_egress_guard_allowlist(source):
            _add(item)
    except Exception:  # noqa: BLE001
        pass

    return tuple(patterns)


def evaluate_block(
    tool_name: str,
    arguments: object,
    *,
    permission: str | None = None,
    env: Mapping[str, str] | None = None,
) -> str | None:
    """Return the blocked host when block mode should DENY this call, else None.

    Returns ``None`` (fall through, no deny) when:
      * the master flag is OFF, or the mode is not ``block``;
      * the call is not outbound / no destination could be extracted; or
      * extraction FAILED for every destination (OQ-2: v1 falls through on a
        parser blind spot rather than turn-breaking; the audit trail keeps the
        gap visible); or
      * every extracted host matches the effective allowlist.

    Returns the FIRST non-allowlisted, successfully-extracted host string when a
    deny is warranted. Never raises: any internal error degrades to ``None``
    (fail-open for block evaluation, the same posture as the audit stash -- a
    broken extractor must not deny a legitimate call).
    """
    try:
        if not egress_guard_enabled(env):
            return None
        if egress_guard_mode(env) != "block":
            return None
        destinations = extract_egress_destinations(
            tool_name, arguments, permission=permission
        )
        if not destinations:
            return None
        allowlist = resolve_allowlist(env)
        for dest in destinations:
            if dest.extraction == "failed" or dest.host is None:
                continue  # OQ-2: unextractable falls through, recorded elsewhere
            if not host_in_allowlist(dest.host, allowlist):
                return dest.host
        return None
    except Exception:  # noqa: BLE001 - block evaluation must never break a turn
        return None
