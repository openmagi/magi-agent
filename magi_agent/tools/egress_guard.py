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

# NOTE: magi_agent.security is lazy-imported inside each function that uses it
# to avoid a tools->security layering edge. See ``extract_egress_destinations``.

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
) -> tuple[object, ...]:
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
        from magi_agent.security.egress_destinations import (  # noqa: PLC0415
            extract_shell_destinations,
            extract_tool_destination,
        )

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
    dest: object,
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
