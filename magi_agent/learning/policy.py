"""Learning KB — policy invariants.

Codifies the two policy refs declared in
magi_agent.recipes.first_party.self_improvement:
  policy:self-improvement.eval-observation-required@1
  policy:self-improvement.no-direct-mutation@1
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from magi_agent.learning.models import LearningItem


POLICY_EVAL_OBSERVATION_REQUIRED = "policy:self-improvement.eval-observation-required@1"
POLICY_NO_DIRECT_MUTATION = "policy:self-improvement.no-direct-mutation@1"


class PolicyViolation(Exception):
    """Raised when a learning activation violates a declared policy."""


def assert_activation_allowed(
    item: "LearningItem",
    *,
    eval_observation_ref: str | None,
    approval_ref: str | None = None,
) -> None:
    """Assert that activating *item* is permitted under the learning policies.

    Raises:
        PolicyViolation: if ``eval_observation_ref`` is absent
            (policy:self-improvement.eval-observation-required@1), or if
            *item* is a ``rule`` and ``approval_ref`` is absent
            (policy:self-improvement.no-direct-mutation@1).
    """
    if not eval_observation_ref:
        raise PolicyViolation(
            f"{POLICY_EVAL_OBSERVATION_REQUIRED}: "
            "an eval_observation_ref is required before activating a learning item"
        )

    if item.kind == "rule" and not approval_ref:
        raise PolicyViolation(
            f"{POLICY_NO_DIRECT_MUTATION}: "
            "rule activation requires a human approval_ref"
        )


__all__ = [
    "POLICY_EVAL_OBSERVATION_REQUIRED",
    "POLICY_NO_DIRECT_MUTATION",
    "PolicyViolation",
    "assert_activation_allowed",
]
