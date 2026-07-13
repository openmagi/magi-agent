from __future__ import annotations

import copy
import json
import os
import tempfile
from pathlib import Path
from typing import Any

DEFAULT_OVERRIDES: dict[str, Any] = {
    "verification": {
        "recipes": [],
        "harness_presets": [],
        # Explicit per-preset enable state (tri-state: present True/False, or
        # absent → use the preset's runtime default). Drives opt-out of
        # default-on verification gates. Distinct from the legacy
        # ``harness_presets`` enabled-list (kept for back-compat / recipes-style).
        "preset_overrides": {},
        "hooks": {},
        "modes": {},
        "custom_rules": [],
        # PR-C2: approved SeamSpec documents. Each entry is the JSON shape of
        # a :class:`magi_agent.customize.seam_spec.SeamSpec` plus a per-spec
        # ``id`` for upsert/delete. The runtime ``seam_for_user`` loads this
        # list and layers it on top of ``PRESET_SEAMS``. Empty by default so
        # OFF is byte-identical to before.
        "seam_specs": [],
        # PR-F7 (2026-06-23): per-bot cost budgets. Surfaced via the
        # Customize "Budgets" sub-tab and applied at turn entry by
        # :func:`magi_agent.customize.budgets_apply.apply_budgets_if_enabled`
        # as ``setdefault`` overrides for the live MAGI_* env (operator env
        # always wins). Empty dict by default so OFF is byte-identical.
        # Supported keys (all optional, positive int):
        #   - "maxToolCallsPerTurn"     -> MAGI_TOOL_MAX_CALLS_PER_TURN
        #   - "maxStepsBrakeHard"       -> MAGI_MAX_STEPS_BRAKE_HARD (sentinel; no
        #                                  numeric flag is registered today)
        #   - "loopGuardHardThreshold"  -> MAGI_LOOP_GUARD_HARD_THRESHOLD
        "budgets": {},
    },
    "tools": {},
    "user_rules": "",
    # Explicit per-behavior enable state for the in-context control-plane loop
    # controls (facts-survey replan, goal nudge, tool-synthesis nudge,
    # empty-response recovery). Tri-state like ``preset_overrides``: a behavior
    # id present with True/False projects onto its ``MAGI_*_ENABLED`` flag as an
    # overwrite (user toggle beats the lab/dogfood profile seed); an absent id
    # leaves the env flag untouched. Empty by default so OFF is byte-identical.
    # Catalog + projection live in ``customize.control_plane_overrides``.
    "control_plane": {},
    # Agent MODES (postures): explicit, user-selected, session-sticky. Each mode =
    # system prompt + tool allow/deny DELTA from bot-default + scoped policy ids.
    # DISTINCT from ``verification.modes`` (per-preset enforcement mode). Typed model
    # + CRUD live in ``customize.modes``; NOT consumed by the runtime yet (storage
    # only). Empty by default so OFF is byte-identical. Keyed ``agent_modes`` (NOT
    # ``modes``) to avoid lexical collision with ``verification.modes`` above.
    "agent_modes": {},
    # Last-selected mode id (session-sticky). None ⇒ the bot default mode.
    "active_agent_mode": None,
    # POLICIES: named user-intent units, each a composition of 1..N custom
    # rules (a policy is the authoring/grouping unit; a rule is the atomic
    # executable unit). Keyed id→policy dict, mirroring ``agent_modes``. Typed
    # model + CRUD live in ``customize.policies``. A policy carries no
    # precedence of its own (all custom_rule-composed policies are soft);
    # activation is per member rule. Empty by default so OFF is byte-identical.
    # See clawy docs/plans/2026-07-03-policy-abstraction-and-organic-multi-rule-authoring-design.md.
    "policies": {},
    # Per-BUILTIN-policy enable override (tri-state like ``control_plane``). A
    # first-party policy id (``verify_before_replying``) present with True/False
    # projects onto its master ``MAGI_*_ENABLED`` flag as an overwrite (user
    # toggle beats the profile seed; re-enable works cleanly); an absent id
    # leaves the env flag untouched. Only ids in the curated
    # ``builtin_policy_overrides.BUILTIN_POLICY_TOGGLES`` catalog project — a
    # floor policy (``source_citation``) is never in the catalog, so it cannot
    # be disabled through this seam. Empty by default so OFF is byte-identical.
    "builtin_policies": {},
    # source_citation gate-mode opt-DOWN (``repair`` / ``audit`` / ``off``). The
    # citation policy's BOOLEAN disable stays floored (absent from
    # ``builtin_policies``); this MODE step-down is the acceptable opt-DOWN lever
    # because capture / inline citations / Sources stay on in all three modes. An
    # explicit value projects onto ``MAGI_SOURCE_CITATION_GATE_MODE`` as an
    # overwrite; ``None`` (the default) leaves the flag untouched so it resolves
    # to the fleet default (``repair``) -- byte-identical to before this seam.
    "citation_gate_mode": None,
    # Mode selections for the generalized gate-mode first-party policies
    # (answer_verifier / research_governance / edit_match). Maps a policy id to
    # its chosen mode string; an absent id leaves that gate's env var untouched
    # (byte-identical to before this seam). Projected by
    # ``apply_gate_mode_overrides_to_env``.
    "gate_modes": {},
}

_USER_RULES_MAX = 20_000


def customize_path() -> Path:
    """Locate customize.json beside the runtime config (env-overridable)."""
    # I-4: routed through the typed flag registry.
    from magi_agent.config.flags import flag_str  # noqa: PLC0415

    override = flag_str("MAGI_CUSTOMIZE") or None
    if override:
        return Path(override)
    config = flag_str("MAGI_CONFIG") or None
    if config:
        return Path(config).parent / "customize.json"
    return Path.home() / ".magi" / "customize.json"


def _clone_default() -> dict[str, Any]:
    return copy.deepcopy(DEFAULT_OVERRIDES)


def _normalize(data: dict[str, Any]) -> dict[str, Any]:
    merged = _clone_default()
    verification = data.get("verification")
    if isinstance(verification, dict):
        for key in merged["verification"]:
            if key in verification and isinstance(
                verification[key], type(merged["verification"][key])
            ):
                merged["verification"][key] = verification[key]
        # PR-F7: budgets is a typed-int dict. Keep only str→positive-int pairs
        # so malformed entries never reach the runtime applier (mirrors the
        # control_plane defensive filter below).
        budgets_raw = verification.get("budgets")
        if isinstance(budgets_raw, dict):
            merged["verification"]["budgets"] = {
                key: int(value)
                for key, value in budgets_raw.items()
                if isinstance(key, str)
                and isinstance(value, int)
                and not isinstance(value, bool)
                and value > 0
            }
        else:
            merged["verification"]["budgets"] = {}
    tools = data.get("tools")
    if isinstance(tools, dict):
        merged["tools"] = tools
    user_rules = data.get("user_rules")
    if isinstance(user_rules, str):
        merged["user_rules"] = user_rules[:_USER_RULES_MAX]
    control_plane = data.get("control_plane")
    if isinstance(control_plane, dict):
        # Keep only explicit booleans (tri-state). Non-bool values are dropped
        # so the projection step never has to guess at a malformed entry.
        merged["control_plane"] = {
            key: value
            for key, value in control_plane.items()
            if isinstance(key, str) and isinstance(value, bool)
        }
    # Agent modes: light structural filter here (id->dict); the typed AgentMode
    # validation lives in ``customize.modes`` (get/list skip malformed entries).
    modes_raw = data.get("agent_modes")
    if isinstance(modes_raw, dict):
        merged["agent_modes"] = {
            key: value
            for key, value in modes_raw.items()
            if isinstance(key, str) and isinstance(value, dict)
        }
    active_mode = data.get("active_agent_mode")
    if isinstance(active_mode, str) and active_mode.strip():
        merged["active_agent_mode"] = active_mode
    # Policies: light structural filter here (id->dict); the typed Policy
    # validation lives in ``customize.policies`` (get/list skip malformed
    # entries). Mirrors the ``agent_modes`` branch above.
    policies_raw = data.get("policies")
    if isinstance(policies_raw, dict):
        merged["policies"] = {
            key: value
            for key, value in policies_raw.items()
            if isinstance(key, str) and isinstance(value, dict)
        }
    # Builtin-policy opt-out overrides: tri-state bool filter, mirroring the
    # ``control_plane`` branch above. Only explicit booleans survive; the
    # projection step (builtin_policy_overrides) additionally ignores any id
    # outside the curated catalog.
    builtin_policies_raw = data.get("builtin_policies")
    if isinstance(builtin_policies_raw, dict):
        merged["builtin_policies"] = {
            key: value
            for key, value in builtin_policies_raw.items()
            if isinstance(key, str) and isinstance(value, bool)
        }
    # source_citation gate-mode opt-down: keep only a valid enum string; anything
    # else (absent, wrong type, out-of-set) falls back to None so the projection
    # leaves the env flag at its fleet default (``repair``).
    gate_mode_raw = data.get("citation_gate_mode")
    if isinstance(gate_mode_raw, str) and gate_mode_raw in ("repair", "audit", "off"):
        merged["citation_gate_mode"] = gate_mode_raw
    # Generalized gate-mode selections: keep a shallow str->str mapping. The
    # projection (``apply_gate_mode_overrides_to_env``) validates each value
    # against its gate's ordered enum, so we only shape-check the container here.
    gate_modes_raw = data.get("gate_modes")
    if isinstance(gate_modes_raw, dict):
        merged["gate_modes"] = {
            str(k): v for k, v in gate_modes_raw.items() if isinstance(v, str)
        }
    return merged


#: Parse cache keyed by (st_mtime_ns, st_size, st_ino) per target path (N-39).
#: The freshness contract is preserved because every call still stat()s the
#: file; only the read+json.loads+_normalize work is skipped on a cache hit.
#: save_overrides() writes via os.replace (new inode + mtime_ns), so a write
#: automatically invalidates the entry.
_PARSE_CACHE: dict[str, tuple[tuple[int, int, int], dict[str, Any]]] = {}


def load_overrides(path: Path | None = None) -> dict[str, Any]:
    """Load + shape-normalize the overrides file. Never raises; falls back to defaults."""
    target = path or customize_path()
    try:
        stat = target.stat()  # freshness contract: stat EVERY call
    except OSError:
        return _clone_default()
    sig = (stat.st_mtime_ns, stat.st_size, stat.st_ino)
    cache_key = str(target)
    cached = _PARSE_CACHE.get(cache_key)
    if cached is not None and cached[0] == sig:
        # Callers mutate the result (e.g. set_tool_override), so hand back a
        # deep copy and keep the cached value pristine.
        return copy.deepcopy(cached[1])
    try:
        raw = target.read_text(encoding="utf-8")
    except (FileNotFoundError, NotADirectoryError, IsADirectoryError, OSError):
        return _clone_default()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return _clone_default()
    if not isinstance(data, dict):
        return _clone_default()
    normalized = _normalize(data)
    _PARSE_CACHE[cache_key] = (sig, copy.deepcopy(normalized))
    return normalized


def save_overrides(overrides: dict[str, Any], path: Path | None = None) -> None:
    """Atomically write the overrides file (normalized). Creates parent dirs."""
    target = path or customize_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    normalized = _normalize(overrides if isinstance(overrides, dict) else {})
    payload = json.dumps(normalized, indent=2, sort_keys=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(target.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
        os.replace(tmp_name, target)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)


def set_tool_override(name: str, enabled: bool, path: Path | None = None) -> dict[str, Any]:
    """Load, set one tool's enabled override, save atomically, return the new overrides."""
    target = path or customize_path()
    overrides = load_overrides(target)
    overrides["tools"][name] = bool(enabled)
    save_overrides(overrides, target)
    return overrides


def set_control_plane_override(
    behavior_id: str, enabled: bool, path: Path | None = None
) -> dict[str, Any]:
    """Set one control-plane behavior's enable override, save, return overrides.

    The bool is RETAINED on disable (tri-state) so an opt-out of a profile-
    seeded behavior persists across restarts. Validation that ``behavior_id`` is
    a real catalog entry is the API layer's job; the store records what it is
    given (the projection step ignores unknown ids anyway).
    """
    target = path or customize_path()
    overrides = load_overrides(target)
    overrides.setdefault("control_plane", {})[behavior_id] = bool(enabled)
    save_overrides(overrides, target)
    return overrides


def set_builtin_policy_override(
    policy_id: str, enabled: bool, path: Path | None = None
) -> dict[str, Any]:
    """Set one builtin-policy enable override, save, return the new overrides.

    The bool is RETAINED on disable (tri-state) so an opt-out of a default-ON
    first-party policy persists across restarts. Validation that ``policy_id`` is
    a real catalog entry is the API layer's job; the store records what it is
    given (the projection step ignores ids outside the curated catalog anyway).
    """
    target = path or customize_path()
    overrides = load_overrides(target)
    overrides.setdefault("builtin_policies", {})[policy_id] = bool(enabled)
    save_overrides(overrides, target)
    return overrides


def set_citation_gate_mode_override(
    mode: str, path: Path | None = None
) -> dict[str, Any]:
    """Persist the source_citation gate-mode opt-down, save, return overrides.

    ``mode`` must be one of ``repair`` / ``audit`` / ``off``. Validation that the
    value is a real mode is the API layer's job; the store records what it is
    given (the projection step ignores anything outside the enum anyway). This is
    a MODE step-down, NOT a boolean off switch: it never touches
    ``MAGI_SOURCE_CITATION_ENABLED``.
    """
    target = path or customize_path()
    overrides = load_overrides(target)
    overrides["citation_gate_mode"] = str(mode)
    save_overrides(overrides, target)
    return overrides


def set_gate_mode_override(
    policy_id: str, mode: str, path: Path | None = None
) -> dict[str, Any]:
    """Persist a mode-gated first-party policy's mode selection, save, return.

    Records ``overrides['gate_modes'][policy_id] = mode`` for the generalized
    gate-mode policies (answer_verifier / research_governance / edit_match). The
    API layer validates that ``policy_id`` is a registered gate-mode policy and
    ``mode`` is in that gate's ordered values; the projection step
    (``apply_gate_mode_overrides_to_env``) ignores anything outside each gate's
    enum, so a stray value is inert.
    """
    target = path or customize_path()
    overrides = load_overrides(target)
    section = overrides.get("gate_modes")
    if not isinstance(section, dict):
        section = {}
    section[str(policy_id)] = str(mode)
    overrides["gate_modes"] = section
    save_overrides(overrides, target)
    return overrides


def set_verification_override(
    kind: str,
    item_id: str,
    enabled: bool,
    mode: str | None = None,
    path: Path | None = None,
) -> dict[str, Any]:
    """Enable/disable one verification item and record its mode.

    ``kind``:
    - ``harness_presets`` — explicit tri-state in ``preset_overrides`` (the bool
      is RETAINED on disable so an opt-out of a default-on gate persists).
    - ``recipes`` — list-backed (append on enable, remove on disable).
    - ``hooks`` — dict-backed.

    Never raises on bad input; returns the new overrides.
    """
    target = path or customize_path()
    overrides = load_overrides(target)
    verification = overrides["verification"]
    if kind == "harness_presets":
        verification["preset_overrides"][item_id] = bool(enabled)
    elif kind == "hooks":
        verification["hooks"][item_id] = bool(enabled)
    elif kind == "recipes":
        bucket = verification["recipes"]
        if enabled and item_id not in bucket:
            bucket.append(item_id)
        if not enabled and item_id in bucket:
            bucket.remove(item_id)
    if enabled and mode:
        verification["modes"][item_id] = mode
    elif not enabled:
        verification["modes"].pop(item_id, None)
    save_overrides(overrides, target)
    return overrides


def set_user_rules(text: str, path: Path | None = None) -> dict[str, Any]:
    """Persist the free-text USER-RULES.md body (length-capped). Returns overrides."""
    target = path or customize_path()
    overrides = load_overrides(target)
    overrides["user_rules"] = (text or "")[:_USER_RULES_MAX]
    save_overrides(overrides, target)
    return overrides


def set_custom_rule(rule: dict[str, Any], path: Path | None = None) -> dict[str, Any]:
    """Upsert one custom verification rule into ``verification.custom_rules[]``.

    Matches on ``id`` (replace) or appends. Caller is responsible for validating
    the rule first (``custom_rules.validate_custom_rule``). Returns the overrides.
    """
    target = path or customize_path()
    overrides = load_overrides(target)
    rules = overrides["verification"]["custom_rules"]
    rid = rule.get("id")
    for i, existing in enumerate(rules):
        if isinstance(existing, dict) and existing.get("id") == rid:
            rules[i] = rule
            break
    else:
        rules.append(rule)
    save_overrides(overrides, target)
    return overrides


def delete_custom_rule(rule_id: str, path: Path | None = None) -> dict[str, Any]:
    """Remove a custom rule by id. Returns the overrides (no-op if absent)."""
    target = path or customize_path()
    overrides = load_overrides(target)
    verification = overrides["verification"]
    verification["custom_rules"] = [
        r for r in verification["custom_rules"]
        if not (isinstance(r, dict) and r.get("id") == rule_id)
    ]
    save_overrides(overrides, target)
    return overrides


def set_custom_rules_group(
    rules: list[dict[str, Any]],
    group_id: str,
    path: Path | None = None,
) -> dict[str, Any]:
    """PR-F-UX6: persist N rules sharing the same ``groupId``.

    Each rule in ``rules`` is stamped with ``groupId=group_id`` (overwriting any
    pre-existing groupId on the entry) and then upserted via the same id-match
    logic as :func:`set_custom_rule`. Callers are responsible for validating
    each rule first (``custom_rules.validate_custom_rule``). Returns the
    post-save overrides.

    ``group_id`` MUST be a non-empty string. A short ValueError is raised on
    bad input (matches the explicit-validation contract — silent acceptance
    would surface as a malformed hybrid row in the dashboard).
    """
    if not isinstance(group_id, str) or not group_id.strip():
        raise ValueError("group_id must be a non-empty string")
    if not isinstance(rules, list) or not rules:
        raise ValueError("rules must be a non-empty list")

    target = path or customize_path()
    overrides = load_overrides(target)
    existing = overrides["verification"]["custom_rules"]
    for rule in rules:
        if not isinstance(rule, dict):
            raise ValueError("each rule must be a dict")
        stamped = {**rule, "groupId": group_id}
        rid = stamped.get("id")
        for i, prior in enumerate(existing):
            if isinstance(prior, dict) and prior.get("id") == rid:
                existing[i] = stamped
                break
        else:
            existing.append(stamped)
    save_overrides(overrides, target)
    return overrides


def delete_custom_rule_group(
    group_id: str, path: Path | None = None
) -> dict[str, Any]:
    """PR-F-UX6: remove every custom rule whose ``groupId`` matches.

    Sibling to :func:`delete_custom_rule` but groupId-keyed. No-op when no
    rule carries the groupId. Returns the post-save overrides.
    """
    target = path or customize_path()
    overrides = load_overrides(target)
    verification = overrides["verification"]
    verification["custom_rules"] = [
        r for r in verification["custom_rules"]
        if not (isinstance(r, dict) and r.get("groupId") == group_id)
    ]
    save_overrides(overrides, target)
    return overrides


def set_verification_budgets(
    budgets: dict[str, Any], path: Path | None = None
) -> dict[str, Any]:
    """Replace the persisted ``verification.budgets`` map (PR-F7).

    The caller is responsible for shape-validating the values; non-positive
    ints, booleans, and non-str keys are silently dropped by ``_normalize`` on
    load so a malformed write can never poison the live runtime applier.
    Returns the post-save overrides view.
    """
    target = path or customize_path()
    overrides = load_overrides(target)
    sanitized = {
        key: int(value)
        for key, value in (budgets or {}).items()
        if isinstance(key, str)
        and isinstance(value, int)
        and not isinstance(value, bool)
        and value > 0
    }
    overrides["verification"]["budgets"] = sanitized
    save_overrides(overrides, target)
    return overrides


def set_seam_spec(spec_doc: dict[str, Any], path: Path | None = None) -> dict[str, Any]:
    """Upsert one approved SeamSpec into ``verification.seam_specs[]``.

    ``spec_doc`` is the JSON shape of a :class:`SeamSpec` (``spec_version`` +
    ``actions``) augmented with a per-doc ``id`` for upsert/delete. Matches
    on ``id`` (replace) or appends. Caller is responsible for validating
    the spec via :func:`magi_agent.customize.seam_spec.validate_spec` first.
    """
    target = path or customize_path()
    overrides = load_overrides(target)
    specs = overrides["verification"]["seam_specs"]
    spec_id = spec_doc.get("id")
    for i, existing in enumerate(specs):
        if isinstance(existing, dict) and existing.get("id") == spec_id:
            specs[i] = spec_doc
            break
    else:
        specs.append(spec_doc)
    save_overrides(overrides, target)
    return overrides


def delete_seam_spec(spec_id: str, path: Path | None = None) -> dict[str, Any]:
    """Remove a SeamSpec doc by id. Returns the overrides (no-op if absent)."""
    target = path or customize_path()
    overrides = load_overrides(target)
    verification = overrides["verification"]
    verification["seam_specs"] = [
        s for s in verification["seam_specs"]
        if not (isinstance(s, dict) and s.get("id") == spec_id)
    ]
    save_overrides(overrides, target)
    return overrides
