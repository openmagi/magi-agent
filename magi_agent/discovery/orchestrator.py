"""Stateful iterative-discovery orchestrator (TIDE mechanism).

``run_discovery`` drives the agent runtime over up to ``T`` rounds, conditioning
each round's prompt on the cumulative set of already-discovered predictions and
stopping early once a round yields no NEW problems.

This module reuses — but does NOT modify — the runner seam used by the GAIA
harness (``build_cli_model_runner`` from ``cli/real_runner.py``,
``runner.run_async``). The single-turn drive function mirrors
``benchmarks/gaia/harness.py:run_gaia_question`` exactly, including the
``model_factory`` injection point that lets tests pass a fake ``BaseLlm``.

A ``grounding_verifier`` hook is accepted now (default ``None``) so the
later grounding-verification feature plugs in WITHOUT editing this file.
"""
from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping, Sequence

from magi_agent.discovery.gate import ensure_discovery_enabled
from magi_agent.discovery.models import (
    DiscoveryConfig,
    DiscoveryCorpus,
    DiscoveryPrediction,
    DiscoveryReport,
    DiscoveryState,
    DiscoveryTemplate,
)
from magi_agent.discovery.prompt import build_discovery_prompt, parse_predictions

#: ``(DiscoveryState) -> Sequence[DiscoveryTemplate]`` — supplies the template
#: library for a round (see ``templates.static_template_provider``).
TemplateProvider = Callable[[DiscoveryState], Sequence[DiscoveryTemplate]]

#: Optional post-pass: ``(batch, corpus) -> batch`` filtering / repairing the
#: parsed predictions. Default ``None`` (a later feature plugs in here).
GroundingVerifier = Callable[
    [Sequence[DiscoveryPrediction], DiscoveryCorpus],
    Sequence[DiscoveryPrediction],
]

#: Injectable single-turn driver: ``(prompt, *, model_factory, model) -> text``.
RunnerFactory = Callable[..., str]


def run_discovery(
    corpus: DiscoveryCorpus,
    *,
    config: DiscoveryConfig,
    template_provider: TemplateProvider,
    runner_factory: RunnerFactory | None = None,
    grounding_verifier: GroundingVerifier | None = None,
    model_factory: Callable[[object], object] | None = None,
    model: str = "claude-opus-4-7",
    env: Mapping[str, str] | None = None,
) -> DiscoveryReport:
    """Run the iterative-discovery loop and return a :class:`DiscoveryReport`.

    Parameters
    ----------
    corpus:
        The context to search.
    config:
        Round/batch knobs (see :class:`DiscoveryConfig`).
    template_provider:
        Callable returning the template library for a round, given the running
        state. ``static_template_provider`` returns the full library each round.
    runner_factory:
        Injectable single-turn driver ``(prompt, *, model_factory, model) -> str``.
        Defaults to :func:`drive_runner_once`. Tests can inject a stub to avoid
        the heavy ADK import; production callers leave it ``None``.
    grounding_verifier:
        Optional post-pass over each round's parsed batch. Default ``None``.
    model_factory:
        Optional injectable model factory forwarded to the driver so tests can
        supply a fake ``BaseLlm`` (mirrors the GAIA harness).
    model:
        Model identifier forwarded to the driver.
    env:
        Optional environment mapping for the default-OFF gate (tests override).
    """
    ensure_discovery_enabled(env)

    drive = runner_factory if runner_factory is not None else drive_runner_once

    state = DiscoveryState.empty()
    corpus_ids = corpus.ids()

    for _ in range(config.rounds_T):
        templates = template_provider(state)
        prompt = build_discovery_prompt(
            corpus, list(templates), state.predictions, config.batch_k
        )
        raw = drive(prompt, model_factory=model_factory, model=model)
        batch = parse_predictions(raw, corpus_ids)

        if grounding_verifier is not None:
            batch = tuple(grounding_verifier(batch, corpus))

        new = dedup_against(batch, state.predictions)
        if not new:
            break
        state = state.extend(new)

    return DiscoveryReport(
        predictions=state.predictions, rounds_used=state.rounds_used
    )


def dedup_against(
    batch: Sequence[DiscoveryPrediction],
    prior: Sequence[DiscoveryPrediction],
) -> tuple[DiscoveryPrediction, ...]:
    """Drop predictions already present in ``prior`` (or repeated within ``batch``).

    Duplicate key is ``(problem_class, frozenset(evidence_ids))`` — two
    predictions that match the same template on the same evidence are treated as
    the same discovery regardless of wording.
    """
    seen: set[tuple[str | None, frozenset[str]]] = {
        _dedup_key(pred) for pred in prior
    }
    out: list[DiscoveryPrediction] = []
    for pred in batch:
        key = _dedup_key(pred)
        if key in seen:
            continue
        seen.add(key)
        out.append(pred)
    return tuple(out)


def drive_runner_once(
    prompt: str,
    *,
    model_factory: Callable[[object], object] | None = None,
    model: str = "claude-opus-4-7",
    api_key: str = "unused-in-tests",
    workspace_root: str | None = None,
) -> str:
    """Drive a single full agent turn over ``prompt`` and return the joined text.

    Mirrors ``benchmarks/gaia/harness.py:run_gaia_question`` — builds a
    ``CliModelRunner`` via ``build_cli_model_runner`` (with the injectable
    ``model_factory`` test seam), sends one user message, and joins all model
    text parts. Heavy ADK imports are local so callers that inject
    ``runner_factory`` never trigger them.
    """
    import tempfile  # noqa: PLC0415

    from google.genai import types  # noqa: PLC0415

    from magi_agent.engine.providers import ProviderConfig  # noqa: PLC0415
    from magi_agent.cli.real_runner import (  # noqa: PLC0415
        CliModelRunner,
        build_cli_model_runner,
    )

    effective_workspace = workspace_root or tempfile.mkdtemp()
    config = ProviderConfig(provider="anthropic", model=model, api_key=api_key)
    runner: CliModelRunner = build_cli_model_runner(
        config,
        instruction=prompt,
        model_factory=model_factory,
        workspace_root=effective_workspace,
    )

    async def _drive() -> list[str]:
        new_message = types.Content(role="user", parts=[types.Part(text=prompt)])
        texts: list[str] = []
        async for event in runner.run_async(
            user_id="discovery-harness",
            session_id="discovery-session",
            new_message=new_message,
        ):
            content = getattr(event, "content", None)
            for part in getattr(content, "parts", None) or []:
                text = getattr(part, "text", None)
                if isinstance(text, str) and text:
                    texts.append(text)
        return texts

    texts = asyncio.run(_drive())
    return "\n".join(texts)


def _dedup_key(pred: DiscoveryPrediction) -> tuple[str | None, frozenset[str]]:
    return (pred.problem_class, frozenset(pred.evidence_ids))


__all__ = [
    "GroundingVerifier",
    "RunnerFactory",
    "TemplateProvider",
    "dedup_against",
    "drive_runner_once",
    "run_discovery",
]
