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
    },
    "tools": {},
    "user_rules": "",
}

_USER_RULES_MAX = 20_000


def customize_path() -> Path:
    """Locate customize.json beside the runtime config (env-overridable)."""
    override = os.environ.get("MAGI_CUSTOMIZE")
    if override:
        return Path(override)
    config = os.environ.get("MAGI_CONFIG")
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
    tools = data.get("tools")
    if isinstance(tools, dict):
        merged["tools"] = tools
    user_rules = data.get("user_rules")
    if isinstance(user_rules, str):
        merged["user_rules"] = user_rules[:_USER_RULES_MAX]
    return merged


def load_overrides(path: Path | None = None) -> dict[str, Any]:
    """Load + shape-normalize the overrides file. Never raises; falls back to defaults."""
    target = path or customize_path()
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
    return _normalize(data)


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
