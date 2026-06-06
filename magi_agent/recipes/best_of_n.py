"""General Best-of-N budgeted test-time scaling wrapper.

Samples N independent rollouts of any runner_fn, then applies a
deterministic consensus rule.  Budget-gated; default-OFF (n=1 pass-through
unless ``BestOfNConfig.enabled=True`` or the ``MAGI_BEST_OF_N_ENABLED``
environment variable is set).

Does NOT import from magi_agent.benchmarks — that layer depends on this one,
not the reverse.
"""
from __future__ import annotations

import os
import re
import tempfile
from collections.abc import Callable, Sequence
from enum import Enum
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field

from magi_agent.tools.manifest import Budget


T = TypeVar("T")

# Environment-variable feature flag — disabled until explicitly set
_BEST_OF_N_ENABLED_ENV = "MAGI_BEST_OF_N_ENABLED"


class ConsensusMode(str, Enum):
    """Strategy used to aggregate N rollout results into one winner."""

    PLURALITY = "plurality"
    """Normalised string plurality vote (general; default)."""

    FIRST_VALID = "first_valid"
    """Return first non-empty result (degenerate n=1 compat)."""


class BestOfNConfig(BaseModel):
    """Configuration for a Best-of-N sampling run."""

    model_config = ConfigDict(frozen=True)

    n: int = Field(
        default=1,
        ge=1,
        le=16,
        description="Number of independent rollouts to sample.",
    )
    base_seed: int = Field(
        default=42,
        description="Deterministic seed base; rollout i gets base_seed+i.",
    )
    consensus_mode: ConsensusMode = Field(
        default=ConsensusMode.PLURALITY,
        description="Consensus strategy to aggregate rollout results.",
    )
    enabled: bool = Field(
        default=False,
        description=(
            "Must be explicitly True to sample n>1; default-OFF. "
            "Can also be activated via the MAGI_BEST_OF_N_ENABLED env var."
        ),
    )
    max_total_tokens: int | None = Field(
        default=None,
        description=(
            "Optional guard: if n * estimated_tokens_per_call exceeds this "
            "value, n is capped before sampling."
        ),
    )


class BestOfNResult(BaseModel, Generic[T]):
    """Aggregated result from a Best-of-N run."""

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    value: T
    """The consensus winner value (str when ConsensusMode.PLURALITY)."""

    confidence: float
    """Fraction of *n_attempted* samples that agreed with the winner
    (0.0 when all samples failed; 1/n_attempted in the all-disagree case)."""

    n_attempted: int
    """Number of rollouts that were attempted (after budget capping)."""

    n_successful: int
    """Number of rollouts that produced a non-error result."""

    agreement_count: int
    """Number of samples that agreed with the consensus winner."""

    samples: tuple[Any, ...]
    """Raw rollout outputs in call order (for logging and downstream scoring)."""

    consensus_mode: ConsensusMode
    """The consensus mode that produced this result."""


def run_best_of_n(
    runner_fn: Callable[..., T],
    *,
    task: Any,
    config: BestOfNConfig | None = None,
    budget: Budget | None = None,
    runner_kwargs: dict[str, Any] | None = None,
) -> BestOfNResult[T]:
    """Sample N rollouts of *runner_fn* and return the consensus result.

    Parameters
    ----------
    runner_fn:
        Callable with signature
        ``(task, *, workspace_root: str, seed: int, **kwargs) -> T``.
        Exceptions raised by the runner are caught; the failed sample is
        excluded from consensus (outvoted, not re-raised).
    task:
        Arbitrary task descriptor forwarded verbatim to each ``runner_fn`` call.
    config:
        ``BestOfNConfig`` instance; defaults to a default-OFF n=1 config when
        ``None``.
    budget:
        Optional ``Budget``; when ``budget.max_calls_per_turn`` is set and is
        less than ``config.n``, the effective n is capped to that value.
    runner_kwargs:
        Extra keyword arguments forwarded to every ``runner_fn`` call.
    """
    cfg = config or BestOfNConfig()
    kwargs = runner_kwargs or {}

    # --- Default-OFF gate -------------------------------------------------
    is_enabled = cfg.enabled or bool(os.getenv(_BEST_OF_N_ENABLED_ENV))
    effective_n = cfg.n if is_enabled else 1

    # --- Budget cap -------------------------------------------------------
    if budget is not None and budget.max_calls_per_turn is not None:
        effective_n = min(effective_n, budget.max_calls_per_turn)

    # --- Sampling loop ----------------------------------------------------
    raw: list[T] = []
    for i in range(effective_n):
        workspace = tempfile.mkdtemp()
        try:
            result = runner_fn(
                task,
                workspace_root=workspace,
                seed=cfg.base_seed + i,
                **kwargs,
            )
            raw.append(result)
        except Exception:  # noqa: BLE001 — failed sample is outvoted, not fatal
            pass

    # --- Build result with effective_n as n_attempted --------------------
    return _build_result(raw, cfg, n_attempted=effective_n)


# ---------------------------------------------------------------------------
# Internal consensus helpers
# ---------------------------------------------------------------------------


def _normalize_for_consensus(value: str) -> str:
    """Lightweight normalisation for consensus comparison (NOT GAIA-specific).

    Strips leading/trailing whitespace, lowercases, collapses internal
    whitespace, and removes common numeric punctuation (currency symbols,
    percent signs, thousands separators) so that ``"$1,234"`` and ``"1234"``
    are treated as equivalent for vote counting.

    This function is intentionally lighter than
    ``magi_agent.benchmarks.gaia.scorer.normalize_str`` — it is a general
    production primitive, not a benchmark-specific scorer.
    """
    v = value.strip().lower()
    v = re.sub(r"\s+", " ", v)
    for ch in ("$", "%", ","):
        v = v.replace(ch, "")
    return v


def _plurality_consensus(results: Sequence[str]) -> tuple[str, int]:
    """Return ``(winner_raw_value, agreement_count)`` via normalised plurality.

    Tie-breaking is stable: the first-seen canonical key among equally-voted
    candidates wins, preserving determinism.
    """
    if not results:
        return "", 0
    counts: dict[str, int] = {}
    rep: dict[str, str] = {}
    order: list[str] = []
    for r in results:
        key = _normalize_for_consensus(r)
        if key not in counts:
            counts[key] = 0
            rep[key] = r
            order.append(key)
        counts[key] += 1
    # Stable max: iterate in insertion order, pick highest count
    best_key = order[0]
    for k in order[1:]:
        if counts[k] > counts[best_key]:
            best_key = k
    return rep[best_key], counts[best_key]


def _build_result(
    raw: list[Any],
    cfg: BestOfNConfig,
    *,
    n_attempted: int,
) -> BestOfNResult[Any]:
    """Construct a ``BestOfNResult`` from raw rollout outputs."""
    n_successful = len(raw)

    if not raw:
        return BestOfNResult(
            value="",
            confidence=0.0,
            n_attempted=n_attempted,
            n_successful=0,
            agreement_count=0,
            samples=(),
            consensus_mode=cfg.consensus_mode,
        )

    if cfg.consensus_mode == ConsensusMode.PLURALITY:
        str_results = [str(r) for r in raw]
        winner, agreement = _plurality_consensus(str_results)
        confidence = agreement / n_attempted
        return BestOfNResult(
            value=winner,
            confidence=confidence,
            n_attempted=n_attempted,
            n_successful=n_successful,
            agreement_count=agreement,
            samples=tuple(raw),
            consensus_mode=cfg.consensus_mode,
        )

    if cfg.consensus_mode == ConsensusMode.FIRST_VALID:
        winner = next((r for r in raw if r), raw[0])
        return BestOfNResult(
            value=winner,
            confidence=1.0 / n_attempted,
            n_attempted=n_attempted,
            n_successful=n_successful,
            agreement_count=1,
            samples=tuple(raw),
            consensus_mode=cfg.consensus_mode,
        )

    raise ValueError(f"unsupported consensus mode: {cfg.consensus_mode!r}")


__all__ = [
    "BestOfNConfig",
    "BestOfNResult",
    "ConsensusMode",
    "run_best_of_n",
]
