"""F-LIFE5 — learning runtime master-flag + safety policy preservation.

Cross-cuts the schema unlock in :mod:`magi_agent.config.models` (Gate 8
``self_improvement_allowed`` flipped from ``Literal[False]`` to a flippable
``bool``) and the recipe-mapping unlock in
:mod:`magi_agent.customize.catalog`.

Pins:

* the master ``MAGI_LEARNING_ENABLED`` env var still resolves to ON when
  unset (existing PR9a opt-out semantics — unchanged by F-LIFE5);
* setting ``MAGI_LEARNING_ENABLED=0`` AND ``selfImprovementAllowed=True``
  on the gate config keeps the runtime ``LearningBootstrap`` inert (the
  schema unlock does not bypass the master gate);
* the two frozen safety policies ALWAYS fire when their precondition is
  absent, regardless of any flag combination.
"""

from __future__ import annotations

import asyncio

import pytest

from magi_agent.config.models import PythonGate8ReadinessConfig
from magi_agent.learning.bootstrap import LearningBootstrap
from magi_agent.learning.config import (
    ENV_MASTER,
    resolve_learning_config,
)
from magi_agent.learning.policy import (
    POLICY_EVAL_OBSERVATION_REQUIRED,
    POLICY_NO_DIRECT_MUTATION,
    PolicyViolation,
    assert_activation_allowed,
)


# ---------------------------------------------------------------------------
# Stubs
# ---------------------------------------------------------------------------


class _ExampleItem:
    kind = "example"


class _RuleItem:
    kind = "rule"


# ---------------------------------------------------------------------------
# Master flag resolution
# ---------------------------------------------------------------------------


def test_master_flag_defaults_on_when_env_unset() -> None:
    # Existing PR9a opt-out behaviour — F-LIFE5 must not change it.
    config = resolve_learning_config(env={})
    assert config.enabled is True


def test_master_flag_off_disables_everything() -> None:
    config = resolve_learning_config(env={ENV_MASTER: "0"})

    assert config.enabled is False
    assert config.reflection_effective is False
    assert config.injection_effective is False
    assert config.live_effective is False
    assert config.telemetry_effective is False


def test_master_flag_off_keeps_bootstrap_inert_even_when_si_flag_set_true() -> None:
    # Even with the Gate 8 schema-level self_improvement flag flipped ON,
    # the master MAGI_LEARNING_ENABLED=0 gate must keep the live loop inert.
    config = resolve_learning_config(env={ENV_MASTER: "0"})
    gate8 = PythonGate8ReadinessConfig(selfImprovementAllowed=True)
    assert gate8.self_improvement_allowed is True
    assert config.enabled is False

    bootstrap = LearningBootstrap(config=config)

    # ``start()`` is fail-open and async; a master-off config must leave the
    # bootstrap inert (.active == False) without raising.
    asyncio.run(bootstrap.start())
    try:
        assert bootstrap.active is False
    finally:
        asyncio.run(bootstrap.stop())


# ---------------------------------------------------------------------------
# Safety policies — always-on, never bypassed
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("self_improvement_allowed", [False, True])
@pytest.mark.parametrize("master_enabled", [False, True])
def test_eval_observation_required_fires_under_every_flag_combo(
    self_improvement_allowed: bool,
    master_enabled: bool,
) -> None:
    # The schema-level operator opt-in and the master runtime gate are
    # ORTHOGONAL to the policy: ``eval-observation-required`` MUST raise
    # whenever ``eval_observation_ref`` is missing, regardless of flags.
    config = resolve_learning_config(
        env={ENV_MASTER: "1" if master_enabled else "0"},
    )
    gate8 = PythonGate8ReadinessConfig(
        selfImprovementAllowed=self_improvement_allowed,
    )
    assert gate8.self_improvement_allowed is self_improvement_allowed
    assert config.enabled is master_enabled

    with pytest.raises(PolicyViolation) as excinfo:
        assert_activation_allowed(
            _ExampleItem(),
            eval_observation_ref=None,
            approval_ref="approval:human:test",
        )
    assert POLICY_EVAL_OBSERVATION_REQUIRED in str(excinfo.value)


@pytest.mark.parametrize("self_improvement_allowed", [False, True])
@pytest.mark.parametrize("master_enabled", [False, True])
def test_no_direct_mutation_fires_for_rule_items_under_every_flag_combo(
    self_improvement_allowed: bool,
    master_enabled: bool,
) -> None:
    # ``no-direct-mutation`` requires a human ``approval_ref`` for any
    # ``rule``-kind learning item, regardless of operator opt-in or master.
    config = resolve_learning_config(
        env={ENV_MASTER: "1" if master_enabled else "0"},
    )
    gate8 = PythonGate8ReadinessConfig(
        selfImprovementAllowed=self_improvement_allowed,
    )
    assert gate8.self_improvement_allowed is self_improvement_allowed
    assert config.enabled is master_enabled

    with pytest.raises(PolicyViolation) as excinfo:
        assert_activation_allowed(
            _RuleItem(),
            eval_observation_ref="eval:obs:test-1",
            approval_ref=None,
        )
    assert POLICY_NO_DIRECT_MUTATION in str(excinfo.value)


def test_policy_passes_when_both_preconditions_present() -> None:
    # Positive path — the policy does not over-fire. With both refs supplied
    # an example item activates cleanly, and a rule item activates cleanly
    # when a human approval_ref is also supplied.
    assert_activation_allowed(
        _ExampleItem(),
        eval_observation_ref="eval:obs:test-1",
        approval_ref=None,  # not required for non-rule
    )
    assert_activation_allowed(
        _RuleItem(),
        eval_observation_ref="eval:obs:test-1",
        approval_ref="approval:human:test",
    )
