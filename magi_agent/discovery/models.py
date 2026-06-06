"""Pydantic models for the TIDE-style iterative-discovery orchestrator.

These models are intentionally minimal and immutable (``frozen=True``,
``extra="forbid"``) to match the repo's existing style (see
``magi_agent/benchmarks/gaia/dataset.py``).

Vocabulary (TIDE paper):
    * a *prediction* is the triple ``(b, D̂, a)`` — a problem *description*, the
      *evidence* that grounds it, and the recommended *action* — plus the name
      of the discovery *template* (problem class) it matched.
    * a *corpus* is the searchable context the agent reasons over.
    * a *template* is a reusable problem schema supplied to the model each round.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field

_MODEL_CONFIG = ConfigDict(frozen=True, extra="forbid")


class DiscoveryPrediction(BaseModel):
    """A single discovered problem — the TIDE triple plus its matched class.

    Fields
    ------
    description:
        Natural-language statement of the problem (``b``).
    evidence_ids:
        Corpus evidence ids that ground the problem (``D̂``). A tuple so the
        model stays hashable/immutable.
    action:
        Recommended remediation/action (``a``).
    problem_class:
        Name of the matched :class:`DiscoveryTemplate`, or ``None`` when the
        model did not (or could not) label the prediction.
    """

    model_config = _MODEL_CONFIG

    description: str
    evidence_ids: tuple[str, ...] = ()
    action: str = ""
    problem_class: str | None = None


class DiscoveryCorpus(BaseModel):
    """A minimal abstraction of the context the agent searches.

    ``items`` maps an evidence id to its text content. The corpus exposes only
    what the orchestrator needs: the set of valid ids and a compact index string
    for prompting.
    """

    model_config = _MODEL_CONFIG

    items: Mapping[str, str]

    def ids(self) -> frozenset[str]:
        """Return the frozen set of evidence ids in the corpus."""
        return frozenset(self.items.keys())

    def render_index(self, *, max_chars: int = 160) -> str:
        """Render a compact, deterministic ``id: snippet`` index for prompting.

        Each line is ``- <id>: <single-line snippet>``. Snippets are collapsed to
        a single line and truncated to ``max_chars`` so a large corpus cannot
        blow up the prompt.
        """
        lines: list[str] = []
        for evidence_id in sorted(self.items):
            raw = self.items[evidence_id]
            snippet = " ".join(raw.split())
            if len(snippet) > max_chars:
                snippet = snippet[: max_chars - 1].rstrip() + "…"
            lines.append(f"- {evidence_id}: {snippet}")
        return "\n".join(lines)


class DiscoveryTemplate(BaseModel):
    """A reusable problem schema supplied to the model each round (TIDE schema).

    Fields
    ------
    name:
        The problem class label (e.g. ``"Missing Deadline"``).
    pattern:
        1-3 sentence description of the recurring problem pattern.
    evidence_flow:
        1-3 sentence description of the evidence tuple / flow that signals it.
    """

    model_config = _MODEL_CONFIG

    name: str
    pattern: str
    evidence_flow: str


class DiscoveryConfig(BaseModel):
    """Knobs for a discovery run.

    ``rounds_T`` is the maximum number of cumulative rounds; ``batch_k`` is the
    target number of NEW problems to surface per round. Two named defaults match
    the paper's two settings.
    """

    model_config = _MODEL_CONFIG

    rounds_T: int = Field(default=10, ge=1)
    batch_k: int = Field(default=3, ge=1)

    #: Default name for the workspace setting (T=10, small k).
    WORKSPACE: ClassVar[str] = "workspace"
    #: Default name for the repository setting (T=3).
    REPOSITORY: ClassVar[str] = "repository"

    @classmethod
    def workspace(cls) -> DiscoveryConfig:
        """Default config for the workspace setting (T=10, k=3)."""
        return cls(rounds_T=10, batch_k=3)

    @classmethod
    def repository(cls) -> DiscoveryConfig:
        """Default config for the repository setting (T=3, k=3)."""
        return cls(rounds_T=3, batch_k=3)


class DiscoveryState(BaseModel):
    """Cumulative discovery state carried across rounds.

    ``predictions`` is every NEW prediction kept so far (in discovery order);
    ``rounds_used`` is the number of rounds that produced at least one new
    prediction.
    """

    model_config = _MODEL_CONFIG

    predictions: tuple[DiscoveryPrediction, ...] = ()
    rounds_used: int = 0

    @classmethod
    def empty(cls) -> DiscoveryState:
        """Return the empty initial state."""
        return cls(predictions=(), rounds_used=0)

    def extend(self, new_preds: Iterable[DiscoveryPrediction]) -> DiscoveryState:
        """Return a new state with ``new_preds`` appended and ``rounds_used`` +1.

        The original state is left unchanged (the model is frozen).
        """
        appended = tuple(new_preds)
        return DiscoveryState(
            predictions=self.predictions + appended,
            rounds_used=self.rounds_used + 1,
        )


class DiscoveryReport(BaseModel):
    """Final result of a discovery run."""

    model_config = _MODEL_CONFIG

    predictions: tuple[DiscoveryPrediction, ...] = ()
    rounds_used: int = 0

    @property
    def total(self) -> int:
        """Total number of distinct problems discovered."""
        return len(self.predictions)

    def counts_by_class(self) -> Mapping[str, int]:
        """Return a ``problem_class -> count`` summary (``"(unclassified)"`` for None)."""
        counts: dict[str, int] = {}
        for pred in self.predictions:
            key = pred.problem_class or "(unclassified)"
            counts[key] = counts.get(key, 0) + 1
        return counts


__all__ = [
    "DiscoveryConfig",
    "DiscoveryCorpus",
    "DiscoveryPrediction",
    "DiscoveryReport",
    "DiscoveryState",
    "DiscoveryTemplate",
]
