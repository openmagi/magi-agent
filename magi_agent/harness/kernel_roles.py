"""External agent roles as a kernel ``role`` provides type (PR2, contained seam).

A third party adds a new ``ext.<name>`` agent role the same no-fork way every
other primitive is added — a ``pack.toml`` ``[[provides]] type="role"`` entry
pointing at a declarative ``RoleManifest`` spec. A role is a **scope label only**:
it buckets which harness packs / hooks / contracts apply, it does not itself
enforce anything.

This module is the harness-side consumer. It reads role specs through the
kernel's own discovery (``packs.discovery.discover_pack_files`` + the static
``PackManifest``) — NOT a parallel scanner — but stays on the declarative
provides only, so the hot harness-resolution path never imports any pack impl.
``packs/registries.py`` projects the same ``role`` provides into
``registries.roles`` for the full kernel pipeline (``magi pack new`` / acceptance);
both share :class:`RoleManifest` and :func:`parse_role_manifest`.

Contained scope (carried from the abandoned bespoke ``role_registry.py`` design):
only the harness *preset* resolution consults this. Downstream strict-typed
``AgentRole`` Literals (``engine``/``parallel_execution``/``inference_scaling``/
``evidence``) are NOT widened — an external role is a harness-pack + hook scope
label, not an end-to-end engine invocation role.

Flag-ON reachability (traced 2026-06-16): an external role is honoured ONLY as
``ResolvedHarnessPresetState.agent_role`` (a scope label). No live serve or CLI
path routes an external role end-to-end — every live ``agent_role`` source is
first-party (the harness state defaults to ``"general"``; subagent roles are the
first-party ``ChildRole`` literal clamped to general/coding/research), and the
still-``AgentRole``-Literal models (``HarnessResolutionRequest`` /
``ParallelExecutionScope`` / ``InferenceScalingScope``) have no live constructor
while ``EvidenceScopeContext`` is guarded to first-party roles. So turning the
flag ON cannot crash those Literal fields. End-to-end routing of an external role
(its own engine invocation / parallel / inference / evidence scope) is
unsupported until those contracts are deliberately widened.

Trust boundary:

* **default-OFF** — ``MAGI_KERNEL_ROLE_PROVIDES_ENABLED``. With the flag unset,
  ``known_agent_role_ids`` returns exactly the first-party three (byte-identical).
* **namespaced / non-impersonating** — an external role must be ``ext.``-prefixed
  and may not collide with a first-party role id.
* **fail-closed-to-first-party** — any discovery/parse error drops the offending
  role (or the whole external contribution) and keeps the first-party three.
"""

from __future__ import annotations

import logging
import re
import tomllib
from collections.abc import Mapping
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from magi_agent.config.flags import flag_bool

logger = logging.getLogger(__name__)

__all__ = [
    "MAGI_KERNEL_ROLE_PROVIDES_ENABLED_ENV",
    "FIRST_PARTY_AGENT_ROLE_IDS",
    "RoleManifest",
    "is_first_party_agent_role",
    "known_agent_role_ids",
    "parse_role_manifest",
    "validate_external_role_id",
]

MAGI_KERNEL_ROLE_PROVIDES_ENABLED_ENV = "MAGI_KERNEL_ROLE_PROVIDES_ENABLED"

#: The first-party roles. When the flag is OFF, ``known_agent_role_ids`` returns
#: exactly this set — the byte-identical baseline.
FIRST_PARTY_AGENT_ROLE_IDS: tuple[str, ...] = ("general", "coding", "research")

_EXTERNAL_ROLE_NAMESPACE_PREFIX = "ext."
_EXTERNAL_ROLE_ID_RE = re.compile(r"^ext\.[A-Za-z0-9]+(?:[._-][A-Za-z0-9]+)*$")
_RESERVED_ROLE_IDS = frozenset(FIRST_PARTY_AGENT_ROLE_IDS)


class RoleManifest(BaseModel):
    """Declarative ``role`` provides spec: a namespaced agent-role scope label."""

    model_config = ConfigDict(frozen=True, populate_by_name=True, extra="forbid")

    role_id: str = Field(alias="roleId")
    display_name: str = Field(default="", alias="displayName")
    description: str = ""


def is_first_party_agent_role(role_id: str) -> bool:
    return role_id in _RESERVED_ROLE_IDS


def validate_external_role_id(role_id: object) -> str:
    """Return ``""`` when ``role_id`` is an acceptable external role, else a reason."""

    if not isinstance(role_id, str):
        return "role_id_not_a_string"
    if role_id in _RESERVED_ROLE_IDS:
        return "first_party_collision"
    if not role_id.startswith(_EXTERNAL_ROLE_NAMESPACE_PREFIX):
        return "namespace_required"
    if not _EXTERNAL_ROLE_ID_RE.fullmatch(role_id):
        return "malformed_role_id"
    return ""


def parse_role_manifest(spec_path: Path) -> RoleManifest | None:
    """Parse a ``*.role.toml`` spec into a ``RoleManifest``, or ``None`` on error.

    Shared by the harness reader and the ``packs/registries.py`` role projection
    so both agree on the schema. Fail-closed: any read/parse/validation error
    returns ``None`` (the caller drops the role).
    """

    try:
        with open(spec_path, "rb") as handle:
            raw = tomllib.load(handle)
        return RoleManifest.model_validate(raw)
    except Exception:  # noqa: BLE001 - fail-closed: a bad spec drops the role
        return None


def _discover_external_role_ids() -> set[str]:
    """Read every ``type="role"`` provides spec via kernel discovery (no impl import)."""

    from magi_agent.packs.discovery import default_search_bases, discover_pack_files

    found: set[str] = set()
    for disc in discover_pack_files(default_search_bases()):
        for entry in disc.manifest.provides:
            if entry.type != "role" or entry.spec is None:
                continue
            manifest = parse_role_manifest((disc.pack_dir / entry.spec))
            if manifest is None:
                continue
            if validate_external_role_id(manifest.role_id) == "":
                found.add(manifest.role_id)
    return found


def known_agent_role_ids(env: Mapping[str, str] | None = None) -> frozenset[str]:
    """First-party roles, plus validated external ``role`` provides when the flag is ON.

    With ``MAGI_KERNEL_ROLE_PROVIDES_ENABLED`` OFF this is exactly
    ``FIRST_PARTY_AGENT_ROLE_IDS`` — the byte-identical baseline.
    """

    base = frozenset(FIRST_PARTY_AGENT_ROLE_IDS)
    if not flag_bool(MAGI_KERNEL_ROLE_PROVIDES_ENABLED_ENV, env=env):
        return base
    try:
        return base | _discover_external_role_ids()
    except Exception:  # noqa: BLE001 - fail-closed: discovery never halts resolution
        return base
