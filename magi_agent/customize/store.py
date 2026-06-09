from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from typing import Any

DEFAULT_OVERRIDES: dict[str, Any] = {
    "verification": {
        "recipes": [],
        "harness_presets": [],
        "hooks": {},
        "custom_rules": [],
    },
    "tools": {},
}


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
