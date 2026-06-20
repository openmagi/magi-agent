"""SeamSpec — declarative PresetSeam mutation IR for the NL rule builder.

The Customize tab today exposes 36 preset toggles. Each preset is bound to a
``PresetSeam`` (``magi_agent/customize/preset_map.py``) whose 5 fields decide
how the toggle wires into the pre-final gate: which refs the seam controls,
whether enabling adds (``opt_in``) or removing subtracts (``opt_out``), what
the runtime default is, which controlled list (validator vs evidence) is
affected, and which gate modes the seam supports.

Power users want to express richer policy in natural language — control-plane
already does this for NL → Policy IR. The SeamSpec module is the **declarative
intermediate representation** the NL → IR compiler emits. A spec is a tuple of
``SeamAction`` records, each either:

* ``add_seam``: introduce a brand-new ``preset_id`` with full seam fields, or
* ``modify_seam``: take an existing seam and override one or more fields.

Stage A (this module + ``seam_compiler.py``) produces and structurally validates
the spec. Stage B (PR-C2) translates it back into concrete ``PresetSeam``
instances and persists them in a user-override store; Stage C wires the
runtime ``seam_for(preset_id, user_id=...)`` to merge user overrides on top of
the static catalog. Stage D (this module's :func:`validate_spec`) is the
deterministic build-time guard that catches malformed specs before any LLM
critic has a chance to wave them through.

DESIGN NOTES
============

* **Module is dormant.** No runtime code path imports ``seam_spec``. The
  module ships purely as a registration-time helper (compiler + endpoint) and
  is gated behind a default-OFF endpoint flag in PR-C2.
* **Validate-only here.** ``apply_spec_to_seams`` (the actual mutation
  function) lives in PR-C2 to keep this PR's blast radius small. PR-C1 only
  has to prove the spec model + validation are correct.
* **Builtin allow-list authority.** ``MODIFIABLE_PRESET_IDS`` is sourced from
  :data:`magi_agent.customize.preset_map.PRESET_SEAMS` so we cannot drift —
  if the catalog drops a preset, ``modify_seam`` for that id automatically
  surfaces as a schema issue rather than silently no-op'ing later.
* **No new env flag.** PR-C2 introduces ``MAGI_CUSTOMIZE_SEAM_SPEC_ENABLED``
  to gate the endpoint; this module stays callable from tests/other code.

Spec: docs/notes/2026-06-20-magi-agent-customize-tab-handoff-from-control-plane.md §5
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Final


#: Spec wire-format version. Bump whenever ``SeamAction`` gains/changes a
#: required field so older specs in the override store can be migrated rather
#: than silently mis-parsed.
SPEC_VERSION: Final[str] = "0.1"

#: Legal values for :attr:`SeamAction.wiring`. The same allow-list lives on
#: :class:`magi_agent.customize.preset_map.PresetSeam` but is enforced here so
#: ``validate_spec`` can flag a malformed string before any runtime apply.
LEGAL_WIRINGS: Final[frozenset[str]] = frozenset({"opt_in", "opt_out"})

#: Legal values for :attr:`SeamAction.controls_kind`.
LEGAL_CONTROLS_KINDS: Final[frozenset[str]] = frozenset({"validator", "evidence"})

#: Legal values for :attr:`SeamAction.supported_modes` entries. Mirrors the
#: ``supported_modes`` strings carried by builtin seams.
LEGAL_SUPPORTED_MODES: Final[frozenset[str]] = frozenset({"deterministic", "llm"})

#: Legal :attr:`SeamAction.op` values.
LEGAL_OPS: Final[frozenset[str]] = frozenset({"add_seam", "modify_seam"})


def modifiable_preset_ids() -> frozenset[str]:
    """The preset ids a ``modify_seam`` action is allowed to target.

    Sourced live from ``PRESET_SEAMS`` so the allow-list cannot drift when
    the static catalog gains or drops a preset.
    """
    from magi_agent.customize.preset_map import PRESET_SEAMS  # local — circular guard

    return frozenset(PRESET_SEAMS.keys())


@dataclass(frozen=True)
class SeamAction:
    """One atomic mutation against the static ``PRESET_SEAMS`` catalog.

    For ``op="add_seam"``: every seam field (``preset_id``, ``controls_refs``,
    ``runtime_default_on``, ``wiring``, ``controls_kind``,
    ``supported_modes``) must be supplied — the new seam is built verbatim.

    For ``op="modify_seam"``: ``preset_id`` MUST refer to an existing builtin
    seam; every other field is optional. ``None`` means "leave the builtin's
    value untouched"; a non-None value overrides that field. Field-level
    override (not whole-seam replace) is intentional — users typically want
    to flip ``wiring`` or change ``runtime_default_on`` without re-stating the
    full ref list.
    """

    op: str
    preset_id: str
    controls_refs: tuple[str, ...] | None = None
    runtime_default_on: bool | None = None
    wiring: str | None = None
    controls_kind: str | None = None
    supported_modes: tuple[str, ...] | None = None


@dataclass(frozen=True)
class SeamSpec:
    """A whole NL→IR spec: an ordered tuple of seam mutations + version tag."""

    spec_version: str
    actions: tuple[SeamAction, ...] = field(default_factory=tuple)


def parse_spec(data: object) -> SeamSpec:
    """Build a :class:`SeamSpec` from a JSON-decoded dict.

    Tolerant of missing fields per the ``modify_seam`` rule (every override
    field is optional). Raises ``ValueError`` ONLY on type/shape errors that
    would prevent constructing a :class:`SeamAction` at all (wrong root type,
    missing ``op`` / ``preset_id``, non-list ``controls_refs``). Semantic
    issues (unknown op, illegal wiring, unknown preset_id for modify) surface
    through :func:`validate_spec` so the human reviewer sees the full set at
    once, not just the first parse failure.
    """
    if not isinstance(data, dict):
        raise ValueError("spec must be a JSON object")

    version = str(data.get("spec_version") or SPEC_VERSION)
    raw_actions = data.get("actions")
    if raw_actions is None:
        raw_actions = ()
    if not isinstance(raw_actions, (list, tuple)):
        raise ValueError("'actions' must be a list")

    actions: list[SeamAction] = []
    for idx, raw in enumerate(raw_actions):
        if not isinstance(raw, dict):
            raise ValueError(f"action[{idx}] must be an object")
        op = raw.get("op")
        preset_id = raw.get("preset_id")
        if not isinstance(op, str) or not op:
            raise ValueError(f"action[{idx}].op is required")
        if not isinstance(preset_id, str) or not preset_id:
            raise ValueError(f"action[{idx}].preset_id is required")
        controls_refs = raw.get("controls_refs")
        if controls_refs is not None:
            if not isinstance(controls_refs, (list, tuple)):
                raise ValueError(f"action[{idx}].controls_refs must be a list")
            controls_refs = tuple(str(r) for r in controls_refs)
        runtime_default_on = raw.get("runtime_default_on")
        if runtime_default_on is not None and not isinstance(runtime_default_on, bool):
            raise ValueError(f"action[{idx}].runtime_default_on must be a bool")
        wiring = raw.get("wiring")
        if wiring is not None and not isinstance(wiring, str):
            raise ValueError(f"action[{idx}].wiring must be a string")
        controls_kind = raw.get("controls_kind")
        if controls_kind is not None and not isinstance(controls_kind, str):
            raise ValueError(f"action[{idx}].controls_kind must be a string")
        supported_modes = raw.get("supported_modes")
        if supported_modes is not None:
            if not isinstance(supported_modes, (list, tuple)):
                raise ValueError(f"action[{idx}].supported_modes must be a list")
            supported_modes = tuple(str(m) for m in supported_modes)
        actions.append(
            SeamAction(
                op=op,
                preset_id=preset_id,
                controls_refs=controls_refs,
                runtime_default_on=runtime_default_on,
                wiring=wiring,
                controls_kind=controls_kind,
                supported_modes=supported_modes,
            )
        )

    return SeamSpec(spec_version=version, actions=tuple(actions))


def validate_spec(spec: SeamSpec) -> list[str]:
    """Deterministic structural checks on a parsed spec.

    Returns ``[]`` when the spec is structurally clean; otherwise a list of
    human-readable issue strings the reviewer dashboard surfaces alongside
    the LLM critic verdict. This complements (does NOT replace) the LLM
    reviewer — the schema check is deterministic; the reviewer is semantic.

    Checks (each runs independently — every issue is reported, not just the
    first):

    * ``op`` is one of :data:`LEGAL_OPS`.
    * ``preset_id`` is non-empty.
    * ``modify_seam`` targets an existing builtin (``MODIFIABLE_PRESET_IDS``).
    * ``add_seam`` does NOT target an existing builtin (would shadow it).
    * ``add_seam`` supplies every required field (controls_refs,
      runtime_default_on, wiring, controls_kind).
    * ``wiring`` (when set) is one of :data:`LEGAL_WIRINGS`.
    * ``controls_kind`` (when set) is one of :data:`LEGAL_CONTROLS_KINDS`.
    * ``supported_modes`` (when set) is non-empty and every entry is one of
      :data:`LEGAL_SUPPORTED_MODES`.
    * No two actions in the spec target the same ``preset_id`` (collision —
      one would silently win on apply).
    """
    issues: list[str] = []
    modifiable = modifiable_preset_ids()
    seen_preset_ids: dict[str, int] = {}

    for idx, action in enumerate(spec.actions):
        prefix = f"action[{idx}]"
        if action.op not in LEGAL_OPS:
            issues.append(f"{prefix}.op={action.op!r} not in {sorted(LEGAL_OPS)}")
        if not action.preset_id:
            issues.append(f"{prefix}.preset_id is empty")
        else:
            if action.preset_id in seen_preset_ids:
                first = seen_preset_ids[action.preset_id]
                issues.append(
                    f"{prefix}.preset_id={action.preset_id!r} duplicates action[{first}] — "
                    "one would silently win on apply"
                )
            else:
                seen_preset_ids[action.preset_id] = idx

        if action.op == "modify_seam" and action.preset_id and action.preset_id not in modifiable:
            issues.append(
                f"{prefix}.preset_id={action.preset_id!r} is not a builtin seam — "
                "modify_seam targets an existing PRESET_SEAMS entry"
            )
        if action.op == "add_seam":
            if action.preset_id and action.preset_id in modifiable:
                issues.append(
                    f"{prefix}.preset_id={action.preset_id!r} already exists in "
                    "PRESET_SEAMS — use op=modify_seam to override"
                )
            required = {
                "controls_refs": action.controls_refs,
                "runtime_default_on": action.runtime_default_on,
                "wiring": action.wiring,
                "controls_kind": action.controls_kind,
            }
            for name, value in required.items():
                if value is None:
                    issues.append(f"{prefix} add_seam requires '{name}'")

        if action.wiring is not None and action.wiring not in LEGAL_WIRINGS:
            issues.append(
                f"{prefix}.wiring={action.wiring!r} not in {sorted(LEGAL_WIRINGS)}"
            )
        if (
            action.controls_kind is not None
            and action.controls_kind not in LEGAL_CONTROLS_KINDS
        ):
            issues.append(
                f"{prefix}.controls_kind={action.controls_kind!r} "
                f"not in {sorted(LEGAL_CONTROLS_KINDS)}"
            )
        if action.supported_modes is not None:
            if len(action.supported_modes) == 0:
                issues.append(f"{prefix}.supported_modes must be non-empty when set")
            for mode in action.supported_modes:
                if mode not in LEGAL_SUPPORTED_MODES:
                    issues.append(
                        f"{prefix}.supported_modes contains {mode!r} "
                        f"not in {sorted(LEGAL_SUPPORTED_MODES)}"
                    )

    return issues


__all__ = [
    "LEGAL_CONTROLS_KINDS",
    "LEGAL_OPS",
    "LEGAL_SUPPORTED_MODES",
    "LEGAL_WIRINGS",
    "SPEC_VERSION",
    "SeamAction",
    "SeamSpec",
    "modifiable_preset_ids",
    "parse_spec",
    "validate_spec",
]
