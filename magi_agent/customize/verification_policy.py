from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

_DEFAULT_MODE = "deterministic"


def _rule_scope_matches(rule: dict[str, Any], current_scope: str) -> bool:
    """Does a custom rule's declared ``scope`` cover ``current_scope``?

    Defensive: a rule with a missing or non-vocabulary scope is treated as
    ``always`` (universal) so a corrupt persisted rule does not silently vanish
    on a scope-aware call. ``always`` always matches every current scope.
    """
    from magi_agent.customize.scope import ALWAYS_SCOPE, SCOPES  # local import — cycle guard

    raw = rule.get("scope")
    if not isinstance(raw, str) or raw not in SCOPES:
        return True  # legacy / corrupt / unknown ⇒ universal fallback
    if raw == ALWAYS_SCOPE:
        return True
    return raw == current_scope


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
    # PR-F7 (2026-06-23): per-bot cost budgets. Same dict shape as
    # ``verification.budgets`` on disk: ``{budget_name: positive_int}``. Only
    # str→positive-int pairs survive the load. Consumed by
    # :func:`magi_agent.customize.budgets_apply.apply_budgets_if_enabled`.
    budgets: dict[str, int] = field(default_factory=dict)

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
        budgets = {
            key: int(value)
            for key, value in (v.get("budgets", {}) or {}).items()
            if isinstance(key, str)
            and isinstance(value, int)
            and not isinstance(value, bool)
            and value > 0
        }
        return cls(
            presets,
            recipes,
            hooks,
            modes,
            rules,
            preset_overrides,
            custom_rules,
            budgets,
        )

    def budget(self, name: str) -> int | None:
        """Resolved positive-int budget for ``name`` (PR-F7), or ``None`` when
        the operator has not authored an override.

        ``name`` is one of ``maxToolCallsPerTurn`` / ``maxStepsBrakeHard`` /
        ``loopGuardHardThreshold``; unknown names return ``None`` so a caller
        can probe new keys defensively without raising.
        """
        value = self.budgets.get(name)
        if isinstance(value, int) and not isinstance(value, bool) and value > 0:
            return value
        return None

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

    def enabled_tool_perm_rules(
        self, *, current_scope: str | None = None
    ) -> list[dict[str, Any]]:
        """Enabled ``tool_perm`` custom rules (P2 before-tool-use deny/ask).

        When ``current_scope`` is supplied, the returned list is filtered to
        rules whose ``scope`` matches the current turn (per
        :func:`customize.scope.preset_scope_matches`: ``always`` is universal;
        multi-scope match-any). When ``current_scope`` is ``None`` the historic
        scope-blind list is returned so legacy call sites keep working.

        Defensive: a rule with a missing or non-vocabulary ``scope`` is treated
        as ``always`` so a corrupt persisted rule does not silently vanish.
        """
        candidates = [
            rule
            for rule in self.custom_rules
            if rule.get("enabled", False)
            and isinstance(rule.get("what"), dict)
            and rule["what"].get("kind") == "tool_perm"
        ]
        if current_scope is None:
            return candidates
        return [rule for rule in candidates if _rule_scope_matches(rule, current_scope)]

    def enabled_shacl_rules(self) -> list[dict[str, Any]]:
        """Enabled ``shacl_constraint`` custom rules (pre-final SHACL gate).

        For each enabled rule whose ``what.kind`` is ``"shacl_constraint"``,
        returns ``{"ruleId": ..., "shapeTtl": ...}``. ``ruleId`` falls back to
        the rule's top-level ``id`` when ``payload.ruleId`` is absent. Preserves
        stored order. Malformed rules are silently skipped — never raises.
        """
        results: list[dict[str, Any]] = []
        for rule in self.custom_rules:
            if not rule.get("enabled", False):
                continue
            what = rule.get("what")
            if not isinstance(what, dict) or what.get("kind") != "shacl_constraint":
                continue
            payload = what.get("payload")
            if not isinstance(payload, dict):
                continue
            shape_ttl = payload.get("shapeTtl")
            if not isinstance(shape_ttl, str) or not shape_ttl:
                continue
            rule_id = payload.get("ruleId") or rule.get("id")
            results.append({"ruleId": rule_id, "shapeTtl": shape_ttl})
        return results

    def enabled_custom_rules_grouped(
        self,
    ) -> dict[str | None, list[dict[str, Any]]]:
        """PR-F-UX6: bucket ENABLED custom rules by ``groupId``.

        Rules sharing a non-empty ``groupId`` string are surfaced in the
        dashboard as one logical policy (hybrid composition: e.g. regex
        pre-filter + LLM critic). The ``None`` key holds ungrouped rules.

        Defensive: a malformed ``groupId`` (non-str / empty / whitespace-only)
        is bucketed as ungrouped — matches the precedent set by
        :func:`_rule_scope_matches` (corrupt persisted values never raise,
        they degrade to the safe fallback). Preserves stored order within each
        group.
        """
        grouped: dict[str | None, list[dict[str, Any]]] = {}
        for rule in self.custom_rules:
            if not rule.get("enabled", False):
                continue
            raw = rule.get("groupId")
            if isinstance(raw, str) and raw.strip():
                key: str | None = raw
            else:
                key = None
            grouped.setdefault(key, []).append(rule)
        return grouped

    def enabled_capability_scope_rules(self) -> list[dict[str, Any]]:
        """Enabled ``capability_scope`` custom rules (F4 spawn-time toolset cap).

        Returns the raw rule dicts (in stored order) whose ``what.kind`` is
        ``"capability_scope"`` and whose ``firesAt`` is ``"spawn"``. The runtime
        passes the result directly to
        :func:`magi_agent.customize.capability_scope.apply_capability_scope`,
        which reads ``rule["what"]["payload"]`` (denyTools / maxPermissionClass)
        on each entry.

        Malformed rules are silently skipped — never raises. Filtering is
        scope-blind (capability_scope always fires at the single ``spawn`` slot;
        per-role narrowing is deferred).
        """
        results: list[dict[str, Any]] = []
        for rule in self.custom_rules:
            if not rule.get("enabled", False):
                continue
            what = rule.get("what")
            if not isinstance(what, dict) or what.get("kind") != "capability_scope":
                continue
            if rule.get("firesAt") != "spawn":
                continue
            results.append(rule)
        return results

    def enabled_llm_criterion_rules(
        self, *, fires_at: str, current_scope: str | None = None
    ) -> list[dict[str, Any]]:
        """Enabled ``llm_criterion`` custom rules for a fire-at point (P3/P4).

        When ``current_scope`` is supplied, the returned list is additionally
        filtered to rules whose ``scope`` matches the current turn. The
        ``fires_at`` filter still applies (composed with scope, not replaced).
        ``current_scope=None`` preserves the historic scope-blind behavior.
        """
        candidates = [
            rule
            for rule in self.custom_rules
            if rule.get("enabled", False)
            and rule.get("firesAt") == fires_at
            and isinstance(rule.get("what"), dict)
            and rule["what"].get("kind") == "llm_criterion"
        ]
        if current_scope is None:
            return candidates
        return [rule for rule in candidates if _rule_scope_matches(rule, current_scope)]

    def enabled_prompt_injection_rules(
        self, *, fires_at: str, current_scope: str | None = None
    ) -> list[dict[str, Any]]:
        """Enabled ``prompt_injection`` custom rules for a fire-at point (F-MUT1).

        Mirrors :meth:`enabled_llm_criterion_rules` exactly: filter on
        ``enabled`` + ``firesAt == fires_at`` + ``what.kind ==
        "prompt_injection"``. When ``current_scope`` is supplied the result
        is additionally narrowed by :func:`_rule_scope_matches`; otherwise
        the scope-blind list is returned. Consumed by:

        * :mod:`magi_agent.facades` — ``fires_at="before_tool_use"`` to
          gather tool-arg mutators.
        * :mod:`magi_agent.runtime.governed_turn` — ``fires_at=
          "on_user_prompt_submit"`` to gather system-prompt section
          mutators.

        The runtime apply helpers
        (:func:`magi_agent.customize.prompt_injection.apply_prompt_injection_to_tool_args`
        + :func:`...apply_prompt_injection_to_prompt_sections`) accept the
        raw rule dicts returned here and fail-safe-drop any individual rule
        that is malformed.
        """
        candidates = [
            rule
            for rule in self.custom_rules
            if rule.get("enabled", False)
            and rule.get("firesAt") == fires_at
            and isinstance(rule.get("what"), dict)
            and rule["what"].get("kind") == "prompt_injection"
        ]
        if current_scope is None:
            return candidates
        return [rule for rule in candidates if _rule_scope_matches(rule, current_scope)]

    def enabled_output_rewrite_rules(
        self, *, fires_at: str, current_scope: str | None = None
    ) -> list[dict[str, Any]]:
        """Enabled ``output_rewrite`` custom rules for a fire-at point (F-MUT2).

        Mirrors :meth:`enabled_prompt_injection_rules` exactly: filter on
        ``enabled`` + ``firesAt == fires_at`` + ``what.kind ==
        "output_rewrite"``. When ``current_scope`` is supplied the result
        is additionally narrowed by :func:`_rule_scope_matches`; otherwise
        the scope-blind list is returned. Consumed by:

        * :mod:`magi_agent.facades` — ``fires_at="after_tool_use"`` to
          gather tool-output mutators applied after the AFTER_TOOL_USE
          hook's ``replace`` consumer.

        The runtime apply helper
        (:func:`magi_agent.customize.output_rewrite.apply_output_rewrite_to_tool_result`)
        accepts the raw rule dicts returned here and fail-safe-drops any
        individual rule that is malformed.
        """
        candidates = [
            rule
            for rule in self.custom_rules
            if rule.get("enabled", False)
            and rule.get("firesAt") == fires_at
            and isinstance(rule.get("what"), dict)
            and rule["what"].get("kind") == "output_rewrite"
        ]
        if current_scope is None:
            return candidates
        return [rule for rule in candidates if _rule_scope_matches(rule, current_scope)]

    def explicit_preset(self, preset_id: str) -> bool | None:
        """Explicit per-preset enable state, or None if the user never set it."""
        return self.preset_overrides.get(preset_id)

    def resolve_enabled(self, preset_id: str, *, default: bool) -> bool:
        """Resolved enable state: explicit override if set, else ``default``."""
        explicit = self.preset_overrides.get(preset_id)
        return explicit if explicit is not None else default

    def mode(self, preset_id: str) -> str:
        return self.modes.get(preset_id, _DEFAULT_MODE)

    def user_rules_advisory_text(self) -> str:
        """Trimmed operator-supplied advisory text from the Customize Guidance field.

        Returns the value of ``user_rules`` with leading/trailing whitespace
        stripped, or ``""`` when the field is absent, empty, or whitespace-only.
        Canonical read seam used by the F1 ``<user_advisory_rules>`` system
        prompt envelope in ``runtime.message_builder._user_rules_block``; the
        envelope is omitted entirely when this accessor returns ``""``, so a
        blank Guidance field never produces a stray header.

        Non-prompt consumers (dashboard renderers, audit logs) can read the
        same canonical view without re-implementing trim/empty semantics.
        """
        text = self.user_rules
        if not isinstance(text, str):
            return ""
        return text.strip()
