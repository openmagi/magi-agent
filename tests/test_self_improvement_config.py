"""F-LIFE5 — schema unlock + safety preservation for Self Improvement.

The ``self_improvement_allowed`` flag on :class:`PythonGate8ReadinessConfig`
was previously typed ``Literal[False]`` so the schema rejected any True
construction — the dashboard's Self Improvement recipe toggle was a
UI-only no-op even when the operator wanted it on. F-LIFE5 changes the
field to a flippable ``bool`` (default False, operator opt-in via the
primary ``__init__`` / ``model_validate`` path) while keeping the same
``_UNSAFE_CONSTRUCT_COPY_FIELDS`` force-false discipline as
``user_visible_output_allowed`` / ``canary_routing_allowed`` on
:class:`PythonRuntimeAuthorityConfig`.

These tests pin:

* default remains ``False`` (byte-identical to pre-F-LIFE5 when the flag
  is unset)
* primary ``__init__`` accepts ``True`` (was previously force-falsed)
* the ``model_construct`` / ``model_copy`` escape hatches still
  force-false (an unaudited code path cannot leak True)
* serialisation round-trips the flag through both aliases (snake_case
  field + camelCase alias)
* the two frozen safety policies on the recipe manifest still raise
  ``PolicyViolation`` when their preconditions are missing — the schema
  unlock does NOT bypass them.
"""

from __future__ import annotations

import pytest

from magi_agent.config.models import PythonGate8ReadinessConfig
from magi_agent.learning.policy import (
    POLICY_EVAL_OBSERVATION_REQUIRED,
    POLICY_NO_DIRECT_MUTATION,
    PolicyViolation,
    assert_activation_allowed,
)
from magi_agent.recipes.first_party.self_improvement import (
    build_self_improvement_proposal_recipe_manifest,
)


def _empty_gate8_kwargs() -> dict[str, object]:
    """Construction kwargs that satisfy required ``PythonGate8ReadinessConfig``
    fields without touching ``selfImprovementAllowed`` (so the default applies).
    """
    return {}


def test_default_self_improvement_allowed_is_false() -> None:
    config = PythonGate8ReadinessConfig(**_empty_gate8_kwargs())

    assert config.self_improvement_allowed is False
    # Round-trip both via field name and alias so the dashboard contract is
    # unambiguous.
    dumped_alias = config.model_dump(by_alias=True)
    dumped_snake = config.model_dump(by_alias=False)
    assert dumped_alias["selfImprovementAllowed"] is False
    assert dumped_snake["self_improvement_allowed"] is False


def test_primary_init_accepts_self_improvement_allowed_true() -> None:
    # F-LIFE5: operator opt-in via the primary construction surface
    # (was a ValidationError before because the field was Literal[False]).
    config = PythonGate8ReadinessConfig(selfImprovementAllowed=True)

    assert config.self_improvement_allowed is True
    assert config.model_dump(by_alias=True)["selfImprovementAllowed"] is True


def test_model_construct_force_falses_self_improvement_allowed() -> None:
    # Escape hatch: an unaudited caller bypassing validation MUST NOT leak True.
    config = PythonGate8ReadinessConfig.model_construct(
        selfImprovementAllowed=True,
    )

    assert config.self_improvement_allowed is False
    assert config.model_dump(by_alias=True)["selfImprovementAllowed"] is False


def test_model_copy_force_falses_self_improvement_allowed() -> None:
    # Mirrors the ``PythonRuntimeAuthorityConfig`` discipline for
    # ``user_visible_output_allowed`` / ``canary_routing_allowed``:
    # ``model_copy`` ALWAYS force-falses the unsafe bool fields regardless
    # of whether an ``update`` was supplied. The only authoritative surface
    # for the operator opt-in is the primary ``__init__`` /
    # ``model_validate`` path.
    base = PythonGate8ReadinessConfig(selfImprovementAllowed=True)
    assert base.self_improvement_allowed is True

    same = base.model_copy()
    assert same.self_improvement_allowed is False

    copied = base.model_copy(update={"selfImprovementAllowed": True})
    assert copied.self_improvement_allowed is False


# ---------------------------------------------------------------------------
# Safety policies remain enforced regardless of the schema unlock
# ---------------------------------------------------------------------------


class _RuleItem:
    """Minimal LearningItem-shaped stub for policy.assert_activation_allowed."""

    kind = "rule"


class _ExampleItem:
    kind = "example"


def test_policy_eval_observation_required_still_fires_with_si_allowed_true() -> None:
    # Operator opted in to the recipe — but the safety policy must STILL block
    # activation when no eval_observation_ref is supplied.
    config = PythonGate8ReadinessConfig(selfImprovementAllowed=True)
    assert config.self_improvement_allowed is True

    with pytest.raises(PolicyViolation) as excinfo:
        assert_activation_allowed(
            _ExampleItem(),
            eval_observation_ref=None,
            approval_ref="approval:human:test",
        )
    assert POLICY_EVAL_OBSERVATION_REQUIRED in str(excinfo.value)


def test_policy_no_direct_mutation_still_fires_with_si_allowed_true() -> None:
    # Operator opted in — but a ``rule`` learning item without a human
    # approval_ref must STILL be rejected.
    config = PythonGate8ReadinessConfig(selfImprovementAllowed=True)
    assert config.self_improvement_allowed is True

    with pytest.raises(PolicyViolation) as excinfo:
        assert_activation_allowed(
            _RuleItem(),
            eval_observation_ref="eval:obs:test-1",
            approval_ref=None,
        )
    assert POLICY_NO_DIRECT_MUTATION in str(excinfo.value)


def test_recipe_manifest_still_pins_required_policy_refs() -> None:
    # The recipe manifest itself is immutable: the two policy refs are pinned
    # by ``_force_default_off`` regardless of the operator's schema-level flag.
    manifest = build_self_improvement_proposal_recipe_manifest()

    assert POLICY_EVAL_OBSERVATION_REQUIRED in manifest.required_policy_refs
    assert POLICY_NO_DIRECT_MUTATION in manifest.required_policy_refs
    # And the attachment flags are still all False (no live runner/tool/etc.
    # is bound at materialisation time).
    flags = manifest.attachment_flags
    assert flags.live_tool_attached is False
    assert flags.live_callback_attached is False
    assert flags.production_write_enabled is False
    assert flags.mutation_enabled is False
