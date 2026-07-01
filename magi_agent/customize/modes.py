"""Agent MODES (postures) — typed model + customize.json CRUD.

A *mode* is an explicit, user-selected, session-sticky posture. It defines:
- a system prompt (soft, capability/posture axis);
- a tool allow/deny DELTA from the bot default (NOT a snapshot — new tools/packs
  installed later auto-appear; only deliberate overrides persist);
- the ids of scoped policies (user-authored components) active in this mode.

DISTINCT from ``verification.modes`` (a per-preset enforcement mode:
deterministic/audit). This module owns the posture concept.

Runtime consumption: the active mode's ``system_prompt`` IS injected into the
assembled system prompt (``runtime.message_builder._agent_mode_block``); its
``tool_delta`` IS applied at the local runner-build seam (``cli.wiring``) —
``exclude`` narrows the exposed toolset (inherently safe), and ``include``
re-enables a default-off tool within a property-based hard-safety cap
(``_mode_include_allows_manifest``: never execute/net/computer/dangerous;
``exclude`` wins over ``include``). Still storage-only: ``scoped_policy_ids``
(needs a per-turn policy resolver — no such seam exists yet, so it is a
design-first follow-up, not applied here).
See clawy docs/plans/2026-06-30-magi-mode-pack-component-model.md (mode design).
"""
from __future__ import annotations

import re
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from magi_agent.customize.store import load_overrides, save_overrides

_MODE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_TOOL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}$")
_POLICY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:@-]{0,127}$")
_MAX_PROMPT = 20_000
_MAX_LIST = 256
_MAX_MODES = 256

# Restrictiveness rank (higher = more human approvals required). A mode may only
# raise restrictiveness above the deployment baseline (tighten-only, consistent
# with mode scoping); it can never loosen approvals. Hard-safety denies are
# never bypassed by any permission mode regardless of this field. This map is
# the SINGLE source of the valid permission-mode set (mirror of
# ``cli.permissions.PermissionMode``, hardcoded to keep this module import-light).
_PERMISSION_MODE_RANK = {
    "bypassPermissions": 0,
    "acceptEdits": 1,
    "smartApprove": 2,
    "default": 3,
}
# Permission modes a mode may carry (derived from the rank map so the two can
# never drift). ``None`` = the mode does not override the deployment posture.
_VALID_PERMISSION_MODES = frozenset(_PERMISSION_MODE_RANK)

_MODEL_CONFIG = ConfigDict(
    frozen=True, populate_by_name=True, extra="forbid", validate_default=True
)


def _dedupe_valid(value: tuple[str, ...], pattern: re.Pattern[str], label: str) -> tuple[str, ...]:
    if len(value) > _MAX_LIST:
        raise ValueError(f"{label}: too many entries")
    seen: list[str] = []
    for item in value:
        if not isinstance(item, str) or pattern.fullmatch(item) is None:
            raise ValueError(f"{label}: invalid entry {item!r}")
        if item not in seen:
            seen.append(item)
    return tuple(seen)


class ToolDelta(BaseModel):
    """Allow/deny delta from the bot-default toolset. Exclusions turn a
    default-ON tool off in this mode; inclusions turn a default-OFF tool on
    (subject to universal hard-safety, enforced at apply-time, not here)."""

    model_config = _MODEL_CONFIG

    exclude: tuple[str, ...] = ()
    include: tuple[str, ...] = ()

    @field_validator("exclude", "include")
    @classmethod
    def _validate_tools(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _dedupe_valid(value, _TOOL_RE, "toolDelta")


class AgentMode(BaseModel):
    model_config = _MODEL_CONFIG

    mode_id: str = Field(alias="id")
    display_name: str = Field(alias="displayName")
    system_prompt: str = Field(default="", alias="systemPrompt")
    tool_delta: ToolDelta = Field(default_factory=ToolDelta, alias="toolDelta")
    scoped_policy_ids: tuple[str, ...] = Field(default=(), alias="scopedPolicyIds")
    permission_mode: str | None = Field(default=None, alias="permissionMode")

    @field_validator("mode_id")
    @classmethod
    def _validate_id(cls, value: str) -> str:
        if _MODE_ID_RE.fullmatch(value) is None:
            raise ValueError("mode id must be a lowercase safe token [a-z0-9][a-z0-9_-]*")
        return value

    @field_validator("display_name")
    @classmethod
    def _validate_display(cls, value: str) -> str:
        # Drop control / non-printable chars (defense-in-depth: mode names are a
        # UI rendering + spoofing surface; do not rely on frontend escaping alone).
        text = "".join(ch for ch in value if ch.isprintable()).strip()
        if not text:
            raise ValueError("mode displayName must be non-empty")
        return text[:120]

    @field_validator("system_prompt")
    @classmethod
    def _cap_prompt(cls, value: str) -> str:
        return value[:_MAX_PROMPT]

    @field_validator("scoped_policy_ids")
    @classmethod
    def _validate_policies(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _dedupe_valid(value, _POLICY_RE, "scopedPolicyIds")

    @field_validator("permission_mode")
    @classmethod
    def _validate_permission_mode(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if value not in _VALID_PERMISSION_MODES:
            raise ValueError(
                f"permissionMode must be one of {sorted(_VALID_PERMISSION_MODES)} or null"
            )
        return value

    def to_payload(self) -> dict:
        return self.model_dump(by_alias=True, mode="json")


def _modes_raw(path: Path | None) -> dict:
    raw = load_overrides(path).get("agent_modes", {})
    return raw if isinstance(raw, dict) else {}


def list_modes(path: Path | None = None) -> tuple[AgentMode, ...]:
    """All valid stored modes, sorted by id. Malformed entries are skipped."""
    out: list[AgentMode] = []
    for key, raw in _modes_raw(path).items():
        if not isinstance(raw, dict):
            continue
        try:
            mode = AgentMode.model_validate(raw)
        except ValidationError:
            continue
        if mode.mode_id != key:
            continue  # dict key must match payload id (hand-edit guard)
        out.append(mode)
    return tuple(sorted(out, key=lambda mode: mode.mode_id))


def get_mode(mode_id: str, path: Path | None = None) -> AgentMode | None:
    raw = _modes_raw(path).get(mode_id)
    if not isinstance(raw, dict):
        return None
    try:
        mode = AgentMode.model_validate(raw)
    except ValidationError:
        return None
    if mode.mode_id != mode_id:
        return None  # dict key must match payload id (hand-edit guard)
    return mode


def upsert_mode(mode: AgentMode, path: Path | None = None) -> None:
    overrides = load_overrides(path)
    modes = dict(overrides.get("agent_modes", {}) if isinstance(overrides.get("agent_modes"), dict) else {})
    if mode.mode_id not in modes and len(modes) >= _MAX_MODES:
        raise ValueError(f"too many modes (max {_MAX_MODES})")
    modes[mode.mode_id] = mode.to_payload()
    overrides["agent_modes"] = modes
    save_overrides(overrides, path)


def delete_mode(mode_id: str, path: Path | None = None) -> None:
    overrides = load_overrides(path)
    modes = dict(overrides.get("agent_modes", {}) if isinstance(overrides.get("agent_modes"), dict) else {})
    if mode_id not in modes:
        return
    del modes[mode_id]
    overrides["agent_modes"] = modes
    # Deleting the active mode clears the sticky selection (falls back to default).
    if overrides.get("active_agent_mode") == mode_id:
        overrides["active_agent_mode"] = None
    save_overrides(overrides, path)


def active_mode_id(path: Path | None = None) -> str | None:
    value = load_overrides(path).get("active_agent_mode")
    return value if isinstance(value, str) and value else None


def set_active_mode(mode_id: str | None, path: Path | None = None) -> None:
    """Set the session-sticky active mode. ``None`` clears it (bot default).
    A non-None id must reference an existing stored mode."""
    if mode_id is not None and get_mode(mode_id, path) is None:
        raise ValueError(f"unknown mode: {mode_id!r}")
    overrides = load_overrides(path)
    overrides["active_agent_mode"] = mode_id
    save_overrides(overrides, path)


def active_permission_mode(path: Path | None = None) -> str | None:
    """The active mode's ``permission_mode`` for this turn, resolved like the
    other mode seams (per-turn selection wins over the sticky default). ``None``
    when no mode is active, the mode sets no permission_mode, or on any error
    (fail-soft ⇒ the caller keeps its deployment baseline)."""
    try:
        from magi_agent.runtime.per_turn_agent_mode_context import (
            current_per_turn_agent_mode,
        )

        mode_id = current_per_turn_agent_mode() or active_mode_id(path)
        if not mode_id:
            return None
        mode = get_mode(mode_id, path)
        return mode.permission_mode if mode else None
    except Exception:
        return None


def capped_permission_mode(mode_value: str | None, baseline: str) -> str:
    """Effective per-turn permission mode: a mode may only make approvals MORE
    restrictive than ``baseline`` (tighten-only, consistent with mode scoping),
    never looser. Returns ``baseline`` when ``mode_value`` is unset, invalid, or
    would loosen. Hard-safety denies are unaffected by any permission mode."""
    if not mode_value or mode_value not in _PERMISSION_MODE_RANK:
        return baseline
    base_rank = _PERMISSION_MODE_RANK.get(baseline)
    if base_rank is None:
        return baseline  # unknown baseline → never override
    if _PERMISSION_MODE_RANK[mode_value] > base_rank:
        return mode_value
    return baseline


__all__ = [
    "AgentMode",
    "ToolDelta",
    "active_mode_id",
    "active_permission_mode",
    "capped_permission_mode",
    "delete_mode",
    "get_mode",
    "list_modes",
    "set_active_mode",
    "upsert_mode",
]
