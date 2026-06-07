"""Prompt construction + tolerant parsing for the discovery orchestrator.

``build_discovery_prompt`` injects (i) the corpus index, (ii) the template
library, (iii) the already-found ``prior`` predictions (the cumulative-state
conditioning that is the core TIDE mechanism), and (iv) an instruction to
surface up to ``k`` NEW problems as JSON triples.

``parse_predictions`` defensively extracts the JSON array even when wrapped in
prose and skips malformed entries. It deliberately does NOT filter by corpus
membership — grounding verification is a separate, later feature.
"""
from __future__ import annotations

import json
from collections.abc import Sequence

from magi_agent.discovery.models import DiscoveryCorpus, DiscoveryPrediction, DiscoveryTemplate


def build_discovery_prompt(
    corpus: DiscoveryCorpus,
    templates: Sequence[DiscoveryTemplate],
    prior: Sequence[DiscoveryPrediction],
    k: int,
) -> str:
    """Build the discovery prompt for one round.

    Parameters
    ----------
    corpus:
        The context to search; its compact index is injected.
    templates:
        The full template library to match against (the paper supplies all
        templates every round).
    prior:
        Predictions already discovered in earlier rounds. The model is told NOT
        to re-surface anything in this list — this is the cumulative-state
        conditioning that drives breadth.
    k:
        The maximum number of NEW problems to surface this round.
    """
    corpus_index = corpus.render_index() or "(empty corpus)"
    template_block = _render_templates(templates)
    prior_block = _render_prior(prior)
    valid_ids = ", ".join(sorted(corpus.ids())) or "(none)"

    return (
        "You are a proactive problem-discovery agent. Search the CONTEXT below "
        "and surface concrete problems worth acting on.\n\n"
        "## CONTEXT INDEX (evidence_id: snippet)\n"
        f"{corpus_index}\n\n"
        "## PROBLEM TEMPLATES (match each problem to one template by name)\n"
        f"{template_block}\n\n"
        "## ALREADY DISCOVERED (do NOT re-surface any of these; find NEW ones)\n"
        f"{prior_block}\n\n"
        "## TASK\n"
        f"Surface up to {k} NEW problems not already listed above. For each "
        "problem emit one JSON object with keys:\n"
        '  - "description": a concise statement of the problem (string)\n'
        '  - "evidence_ids": the corpus evidence ids that ground it, a subset '
        f"of [{valid_ids}] (array of strings)\n"
        '  - "action": the recommended action (string)\n'
        '  - "problem_class": the matching template name above (string)\n\n'
        "Respond with ONLY a JSON array of these objects (it may be empty if no "
        "NEW problems remain). Do not repeat anything under ALREADY DISCOVERED."
    )


def parse_predictions(
    text: str, corpus_ids: frozenset[str]
) -> tuple[DiscoveryPrediction, ...]:
    """Tolerantly parse a model response into predictions.

    The model is told to emit a JSON array of objects with keys
    ``description`` / ``evidence_ids`` / ``action`` / ``problem_class``. This
    parser extracts the first JSON array even if surrounded by prose and skips
    malformed entries.

    ``corpus_ids`` is accepted for signature stability but NOT used to filter:
    grounding verification is a separate, later feature. Parsed
    ``evidence_ids`` are passed through verbatim.
    """
    array_text = _extract_json_array(text)
    if array_text is None:
        return ()
    try:
        decoded = json.loads(array_text)
    except (json.JSONDecodeError, ValueError):
        return ()
    if not isinstance(decoded, list):
        return ()

    out: list[DiscoveryPrediction] = []
    for entry in decoded:
        pred = _coerce_prediction(entry)
        if pred is not None:
            out.append(pred)
    return tuple(out)


def _render_templates(templates: Sequence[DiscoveryTemplate]) -> str:
    if not templates:
        return "(no templates supplied)"
    lines: list[str] = []
    for tpl in templates:
        lines.append(
            f"- {tpl.name}: pattern={tpl.pattern} | evidence_flow={tpl.evidence_flow}"
        )
    return "\n".join(lines)


def _render_prior(prior: Sequence[DiscoveryPrediction]) -> str:
    if not prior:
        return "(none yet)"
    lines: list[str] = []
    for i, pred in enumerate(prior, start=1):
        cls = pred.problem_class or "(unclassified)"
        ev = ", ".join(pred.evidence_ids) or "(none)"
        lines.append(f"{i}. [{cls}] {pred.description} (evidence: {ev})")
    return "\n".join(lines)


def _extract_json_array(text: str) -> str | None:
    """Extract the first balanced top-level JSON array substring from ``text``.

    Scans for the first ``[`` and returns through its matching ``]``, respecting
    string literals and escapes so brackets inside strings don't fool the
    matcher. Returns ``None`` if no balanced array is found.
    """
    start = text.find("[")
    if start == -1:
        return None
    depth = 0
    in_string = False
    escaped = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None


def _coerce_prediction(entry: object) -> DiscoveryPrediction | None:
    """Build a :class:`DiscoveryPrediction` from one decoded entry, or ``None``.

    Skips entries that are not objects or lack a usable ``description``.
    """
    if not isinstance(entry, dict):
        return None
    description = entry.get("description")
    if not isinstance(description, str) or not description.strip():
        return None

    raw_ids = entry.get("evidence_ids", [])
    evidence_ids: tuple[str, ...]
    if isinstance(raw_ids, (list, tuple)):
        evidence_ids = tuple(str(x) for x in raw_ids if isinstance(x, (str, int)))
    elif isinstance(raw_ids, str):
        evidence_ids = (raw_ids,)
    else:
        evidence_ids = ()

    raw_action = entry.get("action", "")
    action = raw_action if isinstance(raw_action, str) else ""

    raw_class = entry.get("problem_class")
    problem_class = raw_class if isinstance(raw_class, str) and raw_class else None

    try:
        return DiscoveryPrediction(
            description=description,
            evidence_ids=evidence_ids,
            action=action,
            problem_class=problem_class,
        )
    except Exception:
        return None


__all__ = ["build_discovery_prompt", "parse_predictions"]
