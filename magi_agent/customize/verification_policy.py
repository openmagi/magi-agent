from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

_DEFAULT_MODE = "deterministic"


@dataclass(frozen=True)
class CustomizeVerificationPolicy:
    """Resolved view of persisted verification overrides.

    The enforcement wiring (Phases 2-4) reads this off
    ``runtime.customize_verification_policy`` to decide which preset gates to
    contribute to the recipe-driven pre-final evidence gate. Phase 1 only
    constructs it; nothing consumes it yet.
    """

    enabled_presets: frozenset[str] = frozenset()
    enabled_recipes: frozenset[str] = frozenset()
    enabled_hooks: frozenset[str] = frozenset()
    modes: dict[str, str] = field(default_factory=dict)
    user_rules: str = ""
    # Explicit per-preset enable state (tri-state: True/False/absent). Source of
    # truth for opt-out of default-on gates.
    preset_overrides: dict[str, bool] = field(default_factory=dict)
    # Structured custom rules (verification.custom_rules[]). Raw dicts as stored;
    # compilation/validation lives in customize.custom_rules + real_runner.
    custom_rules: tuple[dict[str, Any], ...] = ()

    @classmethod
    def from_overrides(cls, overrides: dict[str, Any]) -> "CustomizeVerificationPolicy":
        v = (overrides or {}).get("verification", {}) or {}
        presets = frozenset(
            x for x in v.get("harness_presets", []) if isinstance(x, str)
        )
        recipes = frozenset(x for x in v.get("recipes", []) if isinstance(x, str))
        hooks = frozenset(
            k for k, on in (v.get("hooks", {}) or {}).items() if isinstance(k, str) and on
        )
        modes = {
            k: m
            for k, m in (v.get("modes", {}) or {}).items()
            if isinstance(k, str) and isinstance(m, str)
        }
        preset_overrides = {
            k: bool(on)
            for k, on in (v.get("preset_overrides", {}) or {}).items()
            if isinstance(k, str) and isinstance(on, bool)
        }
        raw_rules = (overrides or {}).get("user_rules", "")
        rules = raw_rules if isinstance(raw_rules, str) else ""
        custom_rules = tuple(
            r for r in v.get("custom_rules", []) if isinstance(r, dict)
        )
        return cls(presets, recipes, hooks, modes, rules, preset_overrides, custom_rules)

    def is_enabled(self, preset_id: str) -> bool:
        return preset_id in self.enabled_presets

    def enabled_deterministic_refs(self) -> list[str]:
        """Refs contributed by ENABLED deterministic_ref custom rules (P1 compile).

        Only ``deterministic_ref`` kind compiles in P1; tool_perm/llm_criterion
        persist but are inert until their phase. Malformed rules are skipped.
        """
        refs: list[str] = []
        for rule in self.custom_rules:
            if not rule.get("enabled", False):
                continue
            what = rule.get("what")
            if not isinstance(what, dict) or what.get("kind") != "deterministic_ref":
                continue
            payload = what.get("payload")
            ref = payload.get("ref") if isinstance(payload, dict) else None
            if isinstance(ref, str) and ref:
                refs.append(ref)
        return refs

    def enabled_tool_perm_rules(self) -> list[dict[str, Any]]:
        """Enabled ``tool_perm`` custom rules (P2 before-tool-use deny/ask)."""
        return [
            rule
            for rule in self.custom_rules
            if rule.get("enabled", False)
            and isinstance(rule.get("what"), dict)
            and rule["what"].get("kind") == "tool_perm"
        ]

    def enabled_llm_criterion_rules(self, *, fires_at: str) -> list[dict[str, Any]]:
        """Enabled ``llm_criterion`` custom rules for a fire-at point (P3/P4)."""
        return [
            rule
            for rule in self.custom_rules
            if rule.get("enabled", False)
            and rule.get("firesAt") == fires_at
            and isinstance(rule.get("what"), dict)
            and rule["what"].get("kind") == "llm_criterion"
        ]

    def explicit_preset(self, preset_id: str) -> bool | None:
        """Explicit per-preset enable state, or None if the user never set it."""
        return self.preset_overrides.get(preset_id)

    def resolve_enabled(self, preset_id: str, *, default: bool) -> bool:
        """Resolved enable state: explicit override if set, else ``default``."""
        explicit = self.preset_overrides.get(preset_id)
        return explicit if explicit is not None else default

    def mode(self, preset_id: str) -> str:
        return self.modes.get(preset_id, _DEFAULT_MODE)
