"""Parity tests for the I-1 batches 1+2 flag migration (15 ``is_*_enabled`` flags).

Each ``is_*_enabled`` function under test had its inline body
``_is_true(source.get(NAME))`` replaced with a one-line delegation to the
canonical ``config.flags`` registry helper ``flag_bool``. Because both code
paths route through the same shared truthy parser (``config._truthy.is_true``)
and the new ``FlagSpec`` entries register ``default=False``, the behaviour MUST
be byte-identical for every input the legacy form produced.

The 13-value parametrized table below covers:

* unset (``None`` key in the env mapping),
* the canonical lower-case true literals (``1``/``true``/``on``/``yes``),
* mixed-case true literals (``TRUE``/``Yes``),
* the explicit falsey literals (``0``/``false``/``off``),
* the empty string,
* an unknown word (``garbage``) — strict opt-in: must remain ``False``,
* a whitespace-padded true literal (``  true  ``) — the truthy parser trims
  and case-folds, so this must round-trip to ``True``.

If any case fails the migration is NOT byte-identical and the regression must
be rolled back before the batch ships. The plan recommends batches and the
inventory meta-test ratchets the remaining inline ``is_*_enabled`` count down,
so this file grows by one row per migrated flag in each subsequent batch.

Reference: ``docs/plans/2026-06-18-magi-agent-oss-main-remediation/
ws-I-config-quality.md`` §I-1 (Tests / Backward-compat / migration strategy).
"""

from __future__ import annotations

import pytest

from magi_agent.config.env import (
    MAGI_AUTOMATION_METHODOLOGY_ENABLED_ENV,
    MAGI_CODING_CONTEXT_ENABLED_ENV,
    MAGI_DASHBOARD_PACK_AUTHORING_ENABLED_ENV,
    MAGI_FACTS_REPLAN_ENABLED_ENV,
    MAGI_GOAL_NUDGE_ENABLED_ENV,
    MAGI_GROUNDED_ANSWER_GUARD_ENABLED_ENV,
    MAGI_KEY_AWARE_MODEL_ROUTES_ENABLED_ENV,
    MAGI_PROMPT_EXAMPLES_ENABLED_ENV,
    MAGI_PROMPT_REDFLAGS_ENABLED_ENV,
    MAGI_PROMPT_SEARCH_RULES_ENABLED_ENV,
    MAGI_RESEARCH_FACT_GUIDANCE_ENABLED_ENV,
    MAGI_RESEARCH_METHODOLOGY_ENABLED_ENV,
    MAGI_TOOL_SYNTHESIS_NUDGE_ENABLED_ENV,
    MAGI_TOOL_USAGE_GUIDANCE_ENABLED_ENV,
    MAGI_USER_HOOKS_ENABLED_ENV,
    is_automation_methodology_enabled,
    is_coding_context_enabled,
    is_dashboard_pack_authoring_enabled,
    is_facts_replan_enabled,
    is_goal_nudge_enabled,
    is_grounded_answer_guard_enabled,
    is_key_aware_model_routes_enabled,
    is_prompt_examples_enabled,
    is_prompt_redflags_enabled,
    is_prompt_search_rules_enabled,
    is_research_fact_guidance_enabled,
    is_research_methodology_enabled,
    is_tool_synthesis_nudge_enabled,
    is_tool_usage_guidance_enabled,
    is_user_hooks_enabled,
)


# Single source of truth for the 13 inputs and their expected legacy semantics
# (strict-truthy opt-in: only ``1``/``true``/``on``/``yes`` after trim+lower
# resolve to ``True``).
_PARITY_CASES: tuple[tuple[str | None, bool], ...] = (
    # (env_value_or_None, expected_bool)
    (None, False),  # unset
    ("1", True),
    ("true", True),
    ("on", True),
    ("yes", True),
    ("TRUE", True),
    ("Yes", True),
    ("0", False),
    ("false", False),
    ("off", False),
    ("", False),
    ("garbage", False),
    ("  true  ", True),  # whitespace-trim + case-fold
)


# Reader fn + env-name pair for each migrated flag. Adding a row here in a
# future batch automatically extends the parametrized parity suite.
_MIGRATED_FLAGS = (
    # I-1 batch 1 (8 flags):
    (is_automation_methodology_enabled, MAGI_AUTOMATION_METHODOLOGY_ENABLED_ENV),
    (is_coding_context_enabled, MAGI_CODING_CONTEXT_ENABLED_ENV),
    (is_key_aware_model_routes_enabled, MAGI_KEY_AWARE_MODEL_ROUTES_ENABLED_ENV),
    (is_prompt_examples_enabled, MAGI_PROMPT_EXAMPLES_ENABLED_ENV),
    (is_prompt_redflags_enabled, MAGI_PROMPT_REDFLAGS_ENABLED_ENV),
    (is_prompt_search_rules_enabled, MAGI_PROMPT_SEARCH_RULES_ENABLED_ENV),
    (is_research_methodology_enabled, MAGI_RESEARCH_METHODOLOGY_ENABLED_ENV),
    (is_tool_usage_guidance_enabled, MAGI_TOOL_USAGE_GUIDANCE_ENABLED_ENV),
    # I-1 batch 2 (7 strict default-OFF master-switch flags):
    (is_dashboard_pack_authoring_enabled, MAGI_DASHBOARD_PACK_AUTHORING_ENABLED_ENV),
    (is_facts_replan_enabled, MAGI_FACTS_REPLAN_ENABLED_ENV),
    (is_goal_nudge_enabled, MAGI_GOAL_NUDGE_ENABLED_ENV),
    (is_grounded_answer_guard_enabled, MAGI_GROUNDED_ANSWER_GUARD_ENABLED_ENV),
    (is_research_fact_guidance_enabled, MAGI_RESEARCH_FACT_GUIDANCE_ENABLED_ENV),
    (is_tool_synthesis_nudge_enabled, MAGI_TOOL_SYNTHESIS_NUDGE_ENABLED_ENV),
    (is_user_hooks_enabled, MAGI_USER_HOOKS_ENABLED_ENV),
)


@pytest.mark.parametrize(
    ("reader", "env_name"),
    _MIGRATED_FLAGS,
    ids=[fn.__name__ for fn, _ in _MIGRATED_FLAGS],
)
@pytest.mark.parametrize(("raw", "expected"), _PARITY_CASES)
def test_flag_bool_parity_with_legacy_inline_body(
    reader, env_name: str, raw: str | None, expected: bool
) -> None:
    """Each migrated ``is_*_enabled`` matches the legacy ``_is_true(get(NAME))``.

    The new body delegates to ``flag_bool(NAME, env=source)`` — semantically
    identical because ``FlagSpec(..., kind="bool", default=False)`` plus the
    shared ``_truthy.is_true`` parser collapses to the same decision tree as
    the inline ``return _is_true(source.get(NAME))`` form for every input above.
    """

    env: dict[str, str] = {} if raw is None else {env_name: raw}
    assert reader(env) is expected


def test_default_env_resolves_to_false(monkeypatch: pytest.MonkeyPatch) -> None:
    """Sanity: passing ``env=None`` (≡ ``os.environ`` with the flag unset) is False.

    Each migrated flag is registered with a ``False`` default, so reading via
    ``env=None`` (the convenience form) on a hermetic env without the flag set
    must yield ``False``. Uses ``monkeypatch.delenv`` to scrub any operator env
    that may have leaked into the test process.
    """

    for reader, env_name in _MIGRATED_FLAGS:
        monkeypatch.delenv(env_name, raising=False)
        assert reader() is False, reader.__name__
        assert reader(None) is False, reader.__name__


def test_migrated_flags_are_registered_as_bool() -> None:
    """Each migrated env-name is registered in ``FLAGS`` with ``kind='bool'``.

    The migration relies on the registry contract: ``flag_bool`` raises a
    ``TypeError`` for non-``bool`` kinds and ``KeyError`` for unregistered
    names, so this test pins both the membership and the kind so a future
    rename can never silently downgrade a flag onto the profile-aware path.
    """

    from magi_agent.config.flags import FLAGS_BY_NAME

    for _, env_name in _MIGRATED_FLAGS:
        spec = FLAGS_BY_NAME[env_name]
        assert spec.kind == "bool", spec
        assert spec.default is False, spec
