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
        "hooks": {},
        "modes": {},
        "custom_rules": [],
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


_VERIFICATION_LIST_KINDS = ("recipes", "harness_presets")


def set_verification_override(
    kind: str,
    item_id: str,
    enabled: bool,
    mode: str | None = None,
    path: Path | None = None,
) -> dict[str, Any]:
    """Enable/disable one verification item and record its mode.

    ``kind`` is one of ``recipes``, ``harness_presets`` (list-backed) or
    ``hooks`` (dict-backed). Enabling appends/sets; disabling removes the entry
    and clears its mode. Never raises on bad input; returns the new overrides.
    """
    target = path or customize_path()
    overrides = load_overrides(target)
    verification = overrides["verification"]
    if kind == "hooks":
        verification["hooks"][item_id] = bool(enabled)
    elif kind in _VERIFICATION_LIST_KINDS:
        bucket = verification[kind]
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
