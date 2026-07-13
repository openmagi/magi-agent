"""User-facing opt-out for first-party (builtin) *policies*.

First-party policies (``verify_before_replying``, ``source_citation``) are
registered in :mod:`magi_agent.customize.policies` as read-only display mirrors:
they appear in ``list_policies()`` so the Customize surface tells the truth, but
their runtime gates fire purely on ``MAGI_*_ENABLED`` env flags, never on policy
scope. So a user could *see* a first-party policy in the dashboard but had no way
to turn it *off*.

This module closes that gap, mirroring :mod:`control_plane_overrides`. It defines
the curated catalog of *user-disableable* builtin policies and an apply step that
projects the persisted ``customize.json`` ``builtin_policies`` section onto the
environment **as an explicit overwrite** (not ``setdefault``). Wired at startup
right after ``apply_control_plane_overrides_to_env`` AND re-projected by the
PATCH endpoint, an explicit user toggle wins over the profile seed / a prior
shell export, and (because the projection is an overwrite) a disabled policy
can be cleanly re-enabled without a restart (which a ``setdefault`` applier could
not do: once ``…=0`` is set, ``setdefault`` can never flip it back to ``1``).

Tri-state, like ``control_plane``: a policy id absent from the section leaves its
env flag untouched (OFF/empty is byte-identical to before this module existed).
Only an explicit ``True`` / ``False`` projects.

Security / floors: this BOOLEAN opt-out catalog is deliberately limited to
*non-blocking* first-party policies. ``source_citation`` (whose gate can BLOCK
in ``repair`` mode) is intentionally NOT listed, so a user cannot turn that
enforcement fully OFF through this seam (mirroring ``control_plane_overrides``'
refusal to expose hard safety flags). Adding a new boolean opt-out is a one-line
catalog entry; the projection NEVER touches a flag whose id is not in the curated
catalog.

A gate-mode opt-DOWN seam sits alongside the boolean catalog and is the opt-DOWN
half of the story for ``source_citation``:

* GATE-MODE OPT-DOWN (``source_citation`` only, via
  ``apply_citation_gate_mode_override_to_env``): boolean-disable of the citation
  policy stays floored, but a 3-way MODE step-down (``repair`` -> ``audit`` ->
  ``off``) is an acceptable opt-DOWN lever, because capture, inline citations,
  and the Sources panel (``MAGI_SOURCE_CITATION_ENABLED``) stay ON in all three
  modes. The persisted override projects onto ``MAGI_SOURCE_CITATION_GATE_MODE``
  using the same overwrite-both-ways discipline as the boolean catalog. The
  floored policy itself is disclosed to the Customize surface by the unified
  ``policies`` catalog array (``catalog._policy_entries``); this module only owns
  the gate-mode step-down attached to that disclosure, not a separate floor list.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping, MutableMapping
from dataclasses import dataclass

__all__ = [
    "BuiltinPolicyToggle",
    "BUILTIN_POLICY_TOGGLES",
    "builtin_policy_toggle_catalog",
    "apply_builtin_policy_overrides_to_env",
    "CITATION_GATE_MODE_VALUES",
    "citation_gate_mode_effective",
    "apply_citation_gate_mode_override_to_env",
]


@dataclass(frozen=True)
class BuiltinPolicyToggle:
    """A user-disableable first-party policy.

    ``effective`` is a *profile-aware* resolver for the policy's current on/off
    state: unlike the control-plane behaviors (seeded ON only by lab/dogfood),
    a first-party policy can be default-ON via a ``profile_bool`` with the env
    var UNSET, so a raw ``is_true(env.get(var))`` would misreport it as "off".
    """

    id: str
    env_var: str
    label: str
    description: str
    effective: Callable[[Mapping[str, str]], bool]


def _verify_effective(env: Mapping[str, str]) -> bool:
    from magi_agent.config.env import (  # noqa: PLC0415
        parse_verify_before_replying_enabled,
    )

    return parse_verify_before_replying_enabled(env)


def _injection_guard_effective(env: Mapping[str, str]) -> bool:
    from magi_agent.config.env import (  # noqa: PLC0415
        parse_injection_guard_enabled,
    )

    return parse_injection_guard_enabled(env)


def _egress_guard_effective(env: Mapping[str, str]) -> bool:
    from magi_agent.config.env import (  # noqa: PLC0415
        parse_egress_guard_enabled,
    )

    return parse_egress_guard_enabled(env)


# Curated, conservative catalog. Each entry maps a builtin *policy* id (the same
# id used in ``policies.BUILTIN_POLICIES`` and returned by ``list_policies()``)
# to the single master ``MAGI_*_ENABLED`` flag its runtime gate reads.
#
# ``source_citation`` is deliberately ABSENT: its gate can BLOCK (repair mode),
# so it is a floor, not a boolean user toggle. It is disclosed as an always-on
# card by the unified ``policies`` catalog array, and its opt-DOWN lever is the
# 3-way gate MODE selector (``apply_citation_gate_mode_override_to_env``), not a
# boolean off switch. Flip it to fully disableable later by adding a
# BuiltinPolicyToggle entry here (and setting its Policy.user_disableable True).
BUILTIN_POLICY_TOGGLES: tuple[BuiltinPolicyToggle, ...] = (
    BuiltinPolicyToggle(
        id="egress_guard",
        env_var="MAGI_EGRESS_GUARD_ENABLED",
        label="Egress guard",
        description=(
            "Records the first-hop network destination of web tools and shell "
            "network commands so an exfiltration attempt leaves a trail. In the "
            "default audit mode nothing is blocked. Turn it off to stop "
            "recording outbound destinations entirely."
        ),
        effective=_egress_guard_effective,
    ),
    BuiltinPolicyToggle(
        id="verify_before_replying",
        env_var="MAGI_VERIFY_BEFORE_REPLYING_ENABLED",
        label="Verify before replying",
        description=(
            "Before the final answer, audits the candidate reply against this "
            "turn's tool activity and evidence ledger, then hands specific "
            "findings back to the model to ship, revise, or keep working. It "
            "never blocks — the model decides. Turn it off to skip the pre-final "
            "self-audit entirely."
        ),
        effective=_verify_effective,
    ),
    BuiltinPolicyToggle(
        id="injection_guard",
        env_var="MAGI_INJECTION_GUARD_ENABLED",
        label="Injection guard",
        description=(
            "Scans external tool-result content (web, browser, knowledge-base "
            "reads) for prompt-injection heuristics, records findings as audit "
            "evidence, and on a high-severity match prepends a static advisory "
            "header so the model treats the content as untrusted data. It never "
            "blocks and never rewrites the fetched content. Turn it off to skip "
            "the scan and annotation entirely."
        ),
        effective=_injection_guard_effective,
    ),
)

_BY_ID: dict[str, BuiltinPolicyToggle] = {t.id: t for t in BUILTIN_POLICY_TOGGLES}


def builtin_policy_toggle_catalog(
    env: Mapping[str, str] | None = None,
) -> list[dict[str, object]]:
    """Serializable catalog for the dashboard Customize surface.

    ``enabled`` is the policy's *current effective* state via its profile-aware
    ``effective`` resolver, so the toggle reflects reality (including the
    unset-but-default-ON case) when no explicit override is recorded yet.
    """

    source = env if env is not None else os.environ
    out: list[dict[str, object]] = []
    for t in BUILTIN_POLICY_TOGGLES:
        try:
            enabled = bool(t.effective(source))
        except Exception:  # noqa: BLE001 - a broken resolver must not break the surface
            enabled = False
        out.append(
            {
                "id": t.id,
                "env_var": t.env_var,
                "label": t.label,
                "description": t.description,
                "enabled": enabled,
            }
        )
    return out


def _coerce_section(overrides: Mapping[str, object] | None) -> Mapping[str, object]:
    """Extract the ``builtin_policies`` mapping, fail-soft to empty."""

    if not isinstance(overrides, Mapping):
        return {}
    section = overrides.get("builtin_policies")
    if not isinstance(section, Mapping):
        return {}
    return section


def apply_builtin_policy_overrides_to_env(
    env: MutableMapping[str, str],
    overrides: Mapping[str, object] | None,
) -> None:
    """Project ``overrides['builtin_policies']`` onto ``env`` as an overwrite.

    For every catalog policy whose id maps to an explicit ``bool`` in the
    section, set its master env flag to ``"1"`` / ``"0"`` (overwrite, so the user
    toggle beats the profile seed and re-enable works cleanly). Absent ids,
    non-bool values, and (critically) ids that are NOT in the curated catalog
    (a floor policy id, a flag-shaped id, a hand-edited typo) are ignored, so
    this seam can only ever move a flag it explicitly owns. Never raises: a
    malformed overrides document degrades to a no-op.
    """

    try:
        section = _coerce_section(overrides)
        if not section:
            return
        for policy_id, value in section.items():
            if not isinstance(value, bool):
                # Tri-state: only explicit booleans project.
                continue
            toggle = _BY_ID.get(policy_id)
            if toggle is None:
                # Unknown / floor / flag-shaped id. Never touch a flag we do not
                # own -- in particular never a floor policy's flag.
                continue
            env[toggle.env_var] = "1" if value else "0"
    except Exception:  # noqa: BLE001 - fail-soft; a bad file must not break startup
        return


# --------------------------------------------------------------------------- #
# source_citation gate-mode opt-down (3-way step, NOT a boolean off switch)      #
# --------------------------------------------------------------------------- #

#: The three source_citation gate modes, most to least enforcing. ``repair`` is
#: the fleet default (see ``config.flags.MAGI_SOURCE_CITATION_GATE_MODE``);
#: ``audit`` and ``off`` are the opt-DOWN steps.
CITATION_GATE_MODE_VALUES: tuple[str, ...] = ("repair", "audit", "off")

_CITATION_GATE_MODE_ENV = "MAGI_SOURCE_CITATION_GATE_MODE"


def citation_gate_mode_effective(env: Mapping[str, str] | None = None) -> str:
    """Current source_citation gate mode (``repair`` / ``audit`` / ``off``).

    Reads through the same parser the driver uses, so an unset / unparseable
    flag reports the fleet default (``repair``).
    """

    from magi_agent.config.env import (  # noqa: PLC0415
        parse_source_citation_gate_mode,
    )

    source = env if env is not None else os.environ
    return parse_source_citation_gate_mode(source)


def _coerce_gate_mode_section(
    overrides: Mapping[str, object] | None,
) -> str | None:
    """Extract a valid persisted gate-mode string, or ``None`` when absent."""

    if not isinstance(overrides, Mapping):
        return None
    value = overrides.get("citation_gate_mode")
    if isinstance(value, str) and value in CITATION_GATE_MODE_VALUES:
        return value
    return None


def apply_citation_gate_mode_override_to_env(
    env: MutableMapping[str, str],
    overrides: Mapping[str, object] | None,
) -> None:
    """Project ``overrides['citation_gate_mode']`` onto the gate-mode env flag.

    When the section holds one of ``repair`` / ``audit`` / ``off``, overwrite
    ``MAGI_SOURCE_CITATION_GATE_MODE`` with it (overwrite-both-ways, so the user
    choice beats the profile seed and can be stepped back up cleanly). Absent /
    invalid values are ignored, so an unset override is byte-identical to today
    (default ``repair``). NEVER touches ``MAGI_SOURCE_CITATION_ENABLED``: capture,
    inline citations, and the Sources panel stay on in every mode. Never raises.
    """

    try:
        mode = _coerce_gate_mode_section(overrides)
        if mode is None:
            return
        env[_CITATION_GATE_MODE_ENV] = mode
    except Exception:  # noqa: BLE001 - fail-soft; a bad file must not break startup
        return
