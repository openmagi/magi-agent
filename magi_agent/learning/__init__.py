"""Learning KB — foundational storage layer (PR1).

This package is intentionally decoupled from the agent runtime.
It may only import pydantic, stdlib, and other learning sub-modules.
Do NOT import from magi_agent.runtime, magi_agent.harness,
magi_agent.adk_bridge, or any live-agent module.
"""

from __future__ import annotations

from magi_agent.learning.models import (
    LearningItem,
    LearningKind,
    LearningScope,
    LearningStats,
    LearningStatus,
    Provenance,
)
from magi_agent.learning.policy import (
    POLICY_EVAL_OBSERVATION_REQUIRED,
    POLICY_NO_DIRECT_MUTATION,
    PolicyViolation,
    assert_activation_allowed,
)
from magi_agent.learning.store import (
    DEFAULT_LEARNING_DB_PATH,
    LearningStore,
    Page,
    SqliteLearningStore,
)
from magi_agent.learning.vector import (
    BruteForceVectorIndex,
    LearningVectorIndex,
)

__all__ = [
    # models
    "LearningItem",
    "LearningKind",
    "LearningScope",
    "LearningStats",
    "LearningStatus",
    "Provenance",
    # policy
    "POLICY_EVAL_OBSERVATION_REQUIRED",
    "POLICY_NO_DIRECT_MUTATION",
    "PolicyViolation",
    "assert_activation_allowed",
    # store
    "DEFAULT_LEARNING_DB_PATH",
    "LearningStore",
    "Page",
    "SqliteLearningStore",
    # vector
    "BruteForceVectorIndex",
    "LearningVectorIndex",
]
