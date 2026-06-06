"""Guards the schema forms used in the published docs against drift.

Two doc bugs motivated these checks:
- `docs/build-a-recipe.md` used `SpawnDepthRange(min=..., max=...)`, which the
  model rejects (`extra="forbid"`); the canonical fields are `min_depth`/`max_depth`.
- `docs/quickstart.md` used `triggers=('after_tool_use', 'before_commit')`, but
  `EvidenceTrigger` only accepts the camelCase tokens `afterToolUse`/`beforeCommit`.
"""

from __future__ import annotations

import typing

import pytest
from pydantic import ValidationError

from magi_agent.evidence.types import EvidenceTrigger
from magi_agent.harness.evidence_scope import SpawnDepthRange


def test_spawn_depth_range_canonical_fields_construct() -> None:
    rng = SpawnDepthRange(min_depth=0, max_depth=0)
    assert rng.min_depth == 0
    assert rng.max_depth == 0


def test_spawn_depth_range_rejects_min_max_aliases() -> None:
    # The documented-but-wrong `min=/max=` form must fail loudly.
    with pytest.raises(ValidationError):
        SpawnDepthRange(min=0, max=0)  # type: ignore[call-arg]


def test_evidence_trigger_tokens_are_camelcase() -> None:
    valid = set(typing.get_args(EvidenceTrigger))
    assert {"afterToolUse", "beforeCommit"} == valid
    # The snake_case spellings that previously appeared in docs are NOT valid.
    assert "after_tool_use" not in valid
    assert "before_commit" not in valid
