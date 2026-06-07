"""Static discovery-template library (feature B1).

Each setting ("workspace" / "repository") ships a JSON pack of template objects
(``{name, pattern, evidence_flow}``) authored from the TIDE paper's examples.
``load_template_pack`` reads a pack into :class:`DiscoveryTemplate` objects, and
``static_template_provider`` adapts a fixed template tuple into the
``template_provider`` callable the orchestrator expects (the paper supplies the
full library every round, so the provider ignores the running state).
"""
from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from importlib import resources
from typing import Literal

from magi_agent.discovery.models import DiscoveryState, DiscoveryTemplate

PackName = Literal["workspace", "repository"]

_PACKAGE = "magi_agent.discovery.templates"


def load_template_pack(name: PackName) -> tuple[DiscoveryTemplate, ...]:
    """Load the static template pack ``name`` as a tuple of templates.

    Raises
    ------
    ValueError
        If ``name`` is not a known pack.
    """
    if name not in ("workspace", "repository"):
        raise ValueError(
            f"unknown template pack: {name!r} (expected 'workspace' or 'repository')"
        )
    raw = resources.files(_PACKAGE).joinpath(f"{name}.json").read_text(encoding="utf-8")
    decoded = json.loads(raw)
    if not isinstance(decoded, list):
        raise ValueError(f"template pack {name!r} is not a JSON array")
    return tuple(
        DiscoveryTemplate(
            name=entry["name"],
            pattern=entry["pattern"],
            evidence_flow=entry["evidence_flow"],
        )
        for entry in decoded
    )


def static_template_provider(
    templates: Sequence[DiscoveryTemplate],
) -> Callable[[DiscoveryState], tuple[DiscoveryTemplate, ...]]:
    """Return a ``template_provider`` that yields the full library every round.

    The returned callable matches the orchestrator's ``template_provider``
    contract — ``(DiscoveryState) -> Sequence[DiscoveryTemplate]`` — but ignores
    the state, supplying the entire fixed library each round (per the paper).
    """
    frozen = tuple(templates)

    def _provider(_state: DiscoveryState) -> tuple[DiscoveryTemplate, ...]:
        return frozen

    return _provider


__all__ = [
    "PackName",
    "load_template_pack",
    "static_template_provider",
]
