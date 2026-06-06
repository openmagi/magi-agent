"""Multi-problem discovery harness — drives one instance through ``run_discovery``.

This is a DRIVER over the existing discovery orchestrator. It builds the corpus
from a :class:`MultiProblemInstance`, then invokes the REAL
``magi_agent.discovery.orchestrator.run_discovery`` seam (the same path the GAIA
harness uses, with the ``model_factory`` test injection point). It edits nothing
in the ``discovery`` package or the core agent loop.

Three modes implement the TIDE paper's measured systems:

* ``tide`` — the full iterative-discovery mechanism (cumulative-state
  conditioning across ``T`` rounds with the repository template pack and
  grounding verifier).
* ``single_agent`` — the single-shot baseline: one pass (``rounds_T=1``).
* ``multi_agent`` — the parallel baseline: ``N`` independent single-shot passes
  (``N = config.rounds_T``) whose predictions are unioned WITHOUT cumulative
  conditioning, then deduped so coverage is comparable to ``tide``.

The discovery orchestrator is itself default-OFF behind ``MAGI_DISCOVERY_ENABLED``;
this harness forwards an env that enables it for the duration of the measured run
(the harness's own opt-in gate lives in ``cli.py``).
"""
from __future__ import annotations

from collections.abc import Callable, Mapping

from magi_agent.benchmarks.multibug.dataset import MultiProblemInstance
from magi_agent.discovery.gate import DISCOVERY_ENABLED_ENV
from magi_agent.discovery.grounding import GroundingMode, make_grounding_verifier
from magi_agent.discovery.models import DiscoveryConfig, DiscoveryPrediction
from magi_agent.discovery.orchestrator import dedup_against, run_discovery
from magi_agent.discovery.templates import load_template_pack, static_template_provider

Mode = str  # one of: "tide", "single_agent", "multi_agent"

#: Env that enables the discovery orchestrator for the measured run. The harness
#: has its own opt-in gate (``cli.ensure_enabled``); once past it, the inner
#: discovery gate is satisfied so the orchestrator runs.
_DISCOVERY_ENABLED_ENV: Mapping[str, str] = {DISCOVERY_ENABLED_ENV: "1"}


def run_multiproblem(
    instance: MultiProblemInstance,
    *,
    mode: Mode = "tide",
    grounding: GroundingMode = "audit",
    model_factory: Callable[[object], object] | None = None,
    config: DiscoveryConfig | None = None,
    runner_factory: Callable[..., str] | None = None,
    model: str = "claude-opus-4-7",
) -> tuple[DiscoveryPrediction, ...]:
    """Run *instance* through the discovery harness and return its predictions.

    Parameters
    ----------
    instance:
        The multi-bug instance to evaluate; its candidates become the corpus.
    mode:
        ``"tide"`` (full mechanism), ``"single_agent"`` (single pass), or
        ``"multi_agent"`` (N independent passes unioned).
    grounding:
        Grounding-verifier mode forwarded to ``make_grounding_verifier``.
    model_factory:
        Injectable ``(ProviderConfig) -> BaseLlm`` test seam (GAIA pattern),
        forwarded through ``run_discovery``.
    config:
        Discovery config. Defaults to :meth:`DiscoveryConfig.repository` (T=3).
    runner_factory:
        Optional single-turn driver stub forwarded to ``run_discovery`` (tests
        may inject one to avoid the heavy ADK import path).
    model:
        Model identifier forwarded to the driver.

    Raises
    ------
    ValueError
        If ``mode`` is not one of the three supported modes.
    """
    corpus = instance.to_corpus()
    base_config = config or DiscoveryConfig.repository()
    template_provider = static_template_provider(load_template_pack("repository"))
    grounding_verifier = make_grounding_verifier(mode=grounding)

    def _one_pass(pass_config: DiscoveryConfig) -> tuple[DiscoveryPrediction, ...]:
        report = run_discovery(
            corpus,
            config=pass_config,
            template_provider=template_provider,
            grounding_verifier=grounding_verifier,
            model_factory=model_factory,
            runner_factory=runner_factory,
            model=model,
            env=_DISCOVERY_ENABLED_ENV,
        )
        return report.predictions

    if mode == "tide":
        return _one_pass(base_config)

    if mode == "single_agent":
        single = base_config.model_copy(update={"rounds_T": 1})
        return _one_pass(single)

    if mode == "multi_agent":
        single = base_config.model_copy(update={"rounds_T": 1})
        union: tuple[DiscoveryPrediction, ...] = ()
        for _ in range(base_config.rounds_T):
            preds = _one_pass(single)
            # Concatenate then dedup the cumulative union (parallel baseline:
            # no cross-pass state conditioning, only a final dedup so coverage
            # is comparable to the tide path).
            union = union + dedup_against(preds, union)
        return union

    raise ValueError(
        f"unknown mode: {mode!r} (expected 'tide', 'single_agent', or 'multi_agent')"
    )


__all__ = ["Mode", "run_multiproblem"]
