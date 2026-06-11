"""CC-style ``settings.json`` hooks loader (cluster doc 11 PR1).

Reads a Claude-Code-style ``settings.json`` whose top-level ``hooks`` key is a
mapping of CC *event names* (``PreToolUse``, ``PostToolUse``, ``Stop``,
``UserPromptSubmit`` …) to a list of hook entries, and returns a list of
``RegisteredHook`` instances.

This module is a **pure loader** — it performs no execution wiring. Production
boot paths (engine HookBus construction) are wired separately in doc 11 PR2/PR3.

Two entry shapes are accepted under each event key for usability:

CC canonical (matcher-group with a nested ``hooks`` command list)::

    {
      "hooks": {
        "PreToolUse": [
          {
            "matcher": "Edit|Write",
            "hooks": [{ "type": "command", "command": "/usr/local/bin/guard.sh" }]
          }
        ]
      }
    }

Flat (one entry == one hook; ``command``/``url``/``prompt_template`` inline)::

    {
      "hooks": {
        "PostToolUse": [
          { "command": "/usr/local/bin/lint.sh", "matcher": "Edit" }
        ]
      }
    }

Each normalised entry is converted to a ``HookManifest`` via
``external_config._build_manifest_from_yaml_entry`` so that env-var
substitution (``MAGI_HOOK_*`` allowlist), SSRF protection, and manifest
validation are shared with the existing ``agent.hooks.yaml`` loader rather than
duplicated.
"""
from __future__ import annotations

import json
import logging
import os
from typing import TYPE_CHECKING, Any

from magi_agent.hooks.external_config import (
    ExternalHookConfig,
    _build_manifest_from_yaml_entry,
    _external_hook_noop,
)
from magi_agent.hooks.manifest import HookPoint

if TYPE_CHECKING:
    from magi_agent.hooks.bus import RegisteredHook

__all__ = ["load_settings_hooks", "_CC_EVENT_TO_POINT"]

logger = logging.getLogger(__name__)

# CC event-name → internal HookPoint mapping. Only the first-class points that
# the engine wires (command-first, doc 11 §1 scope) plus a few obvious extras
# are mapped; unmapped CC events are warned-and-skipped.
_CC_EVENT_TO_POINT: dict[str, HookPoint] = {
    "PreToolUse": HookPoint.BEFORE_TOOL_USE,
    "PostToolUse": HookPoint.AFTER_TOOL_USE,
    "Stop": HookPoint.AFTER_TURN_END,
    "SubagentStop": HookPoint.AFTER_TURN_END,
    "UserPromptSubmit": HookPoint.BEFORE_SYSTEM_PROMPT,
    "SessionStart": HookPoint.BEFORE_TURN_START,
    "PreCompact": HookPoint.BEFORE_COMPACTION,
}


def _normalise_entry(
    entry: dict[str, Any],
    *,
    point: HookPoint,
    group_matcher: str | None,
) -> dict[str, Any]:
    """Shape a single hook spec into the dict accepted by
    ``_build_manifest_from_yaml_entry``.

    - ``type`` (CC) is mapped to ``execution_type``.
    - ``timeout`` (CC, seconds) is mapped to ``timeoutMs`` (milliseconds).
    - ``matcher`` is carried from the enclosing group when the inner spec
      omits its own.
    - ``point`` is injected from the event-name mapping.
    """
    shaped: dict[str, Any] = dict(entry)

    # CC uses "type" for the executor kind; YAML loader expects execution_type.
    if "type" in shaped and "execution_type" not in shaped and "executionType" not in shaped:
        shaped["execution_type"] = shaped.pop("type")

    # CC "timeout" is seconds; manifest expects timeoutMs.
    if "timeout" in shaped and "timeoutMs" not in shaped and "timeout_ms" not in shaped:
        try:
            shaped["timeoutMs"] = int(float(shaped.pop("timeout")) * 1000)
        except (TypeError, ValueError):
            shaped.pop("timeout", None)

    # Carry the group-level matcher down when the entry has none.
    if group_matcher is not None and "matcher" not in shaped:
        shaped["matcher"] = group_matcher

    # Default execution_type to command when a bare command string is present.
    if (
        "execution_type" not in shaped
        and "executionType" not in shaped
        and "command" in shaped
    ):
        shaped["execution_type"] = "command"

    # Name: derive a stable-ish name when none supplied.
    if "name" not in shaped:
        matcher = shaped.get("matcher")
        shaped["name"] = f"settings:{point.value}:{matcher}" if matcher else f"settings:{point.value}"

    shaped["point"] = point
    return shaped


def _iter_specs(
    raw_entries: list[Any],
    *,
    point: HookPoint,
) -> list[dict[str, Any]]:
    """Flatten a CC event's entry list into individual hook spec dicts.

    Supports both the CC canonical nested form (matcher-group wrapping a
    ``hooks`` list) and the flat per-entry form.
    """
    specs: list[dict[str, Any]] = []
    for entry in raw_entries:
        if not isinstance(entry, dict):
            logger.warning(
                "settings hooks: entry under %s is not a mapping; skipping", point.value
            )
            continue
        nested = entry.get("hooks")
        if isinstance(nested, list):
            group_matcher = entry.get("matcher")
            for inner in nested:
                if not isinstance(inner, dict):
                    logger.warning(
                        "settings hooks: nested entry under %s is not a mapping; skipping",
                        point.value,
                    )
                    continue
                specs.append(
                    _normalise_entry(inner, point=point, group_matcher=group_matcher)
                )
        else:
            specs.append(
                _normalise_entry(entry, point=point, group_matcher=entry.get("matcher"))
            )
    return specs


def load_settings_hooks(
    settings_path: str,
    config: ExternalHookConfig | None = None,
) -> "list[RegisteredHook]":
    """Read a CC-style ``settings.json`` at *settings_path* and return a list of
    ``RegisteredHook`` instances.

    Returns an empty list when:
    - the file does not exist,
    - the file is not valid JSON / not a mapping,
    - the ``hooks`` key is absent, empty, or not a mapping,
    - every entry fails to parse (each failure is logged and skipped).

    Unsupported CC event keys are warned-and-skipped. SSRF-rejected (internal
    URL) http hooks raise inside the shared normaliser and are skipped here.

    When *config* is provided, LLM hooks are filtered out if
    ``config.llm_hooks_enabled`` is False — mirroring ``load_external_hooks_from_yaml``.
    """
    # Lazy import to avoid a circular dependency with bus.py.
    from magi_agent.hooks.bus import RegisteredHook

    if not os.path.isfile(settings_path):
        logger.debug("settings hooks: file not found: %s", settings_path)
        return []

    try:
        with open(settings_path, encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:
        logger.warning("settings hooks: failed to parse JSON: %s", settings_path)
        return []

    if not isinstance(data, dict):
        logger.warning("settings hooks: %s is not a JSON object", settings_path)
        return []

    hooks_block = data.get("hooks")
    if not hooks_block:
        return []
    if not isinstance(hooks_block, dict):
        logger.warning(
            "settings hooks: 'hooks' in %s must be an object keyed by event name",
            settings_path,
        )
        return []

    llm_enabled = config.llm_hooks_enabled if config is not None else True

    registered: list[RegisteredHook] = []
    for event_name, raw_entries in hooks_block.items():
        point = _CC_EVENT_TO_POINT.get(event_name)
        if point is None:
            logger.warning(
                "settings hooks: unsupported event '%s' in %s; skipping",
                event_name,
                settings_path,
            )
            continue
        if not isinstance(raw_entries, list):
            logger.warning(
                "settings hooks: '%s' in %s must be a list; skipping",
                event_name,
                settings_path,
            )
            continue

        for spec in _iter_specs(raw_entries, point=point):
            try:
                manifest = _build_manifest_from_yaml_entry(spec)
            except Exception as exc:
                logger.warning(
                    "settings hooks: entry under '%s' failed to parse (%s); skipping",
                    event_name,
                    exc,
                )
                continue
            if manifest.execution_type == "llm" and not llm_enabled:
                logger.info(
                    "settings hooks: '%s' skipped: LLM hooks disabled (MAGI_LLM_HOOKS_ENABLED)",
                    manifest.name,
                )
                continue
            registered.append(
                RegisteredHook(manifest=manifest, handler=_external_hook_noop)
            )

    logger.info(
        "settings hooks: loaded %d hook(s) from %s", len(registered), settings_path
    )
    return registered
