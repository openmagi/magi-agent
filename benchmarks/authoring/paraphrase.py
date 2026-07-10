"""Stage-4 paraphrase expander (offline, design section 8).

Breadth without label drift: a paraphraser rewrites the TEMPLATE utterances of a
generated scenario while the oracle stays derived from slots, never from text.
This module ships the INTERFACE plus a scripted fake paraphraser for tests; the
live-LLM wiring belongs to Wave C's CLI (``run.py``), which passes a real
model-backed callable to :func:`expand_scenario`.

Guardrails (design stage 4), enforced as POST-checks so a bad paraphrase is
DROPPED, never repaired:

- The paraphraser receives ONLY the utterance text and the language — never the
  slots, never the oracle. (Enforced by the ``Paraphraser`` signature.)
- Literal preservation: every slot-critical literal (tool name, domain, script
  snippet, pattern, and — for a corrective turn — both the stale and the target
  markers) must survive verbatim in the paraphrase.
- Language-tag preservation: the paraphrase must stay in the scenario's language
  (a cheap script-family heuristic; Hangul stays Hangul, ASCII stays ASCII).
- Length cap: a paraphrase may not exceed ``_MAX_LEN_RATIO`` x the original (a
  runaway rewrite is dropped).
- Only ``turns[*].say`` changes; ``llm_script``, slots, and oracle are copied
  untouched (the caller in :func:`expand_scenario` rebuilds the Scenario with
  only ``say`` replaced).
- Dedup: a paraphrase whose normalized text equals the original (or an already
  accepted paraphrase) is dropped.

Provenance is recorded in ``generated.paraphrase_model`` by the caller.
"""
from __future__ import annotations

import re
from dataclasses import replace
from typing import Callable, Protocol

from benchmarks.authoring.scenario import Scenario, Turn

#: A paraphrase longer than this ratio of the original is rejected.
_MAX_LEN_RATIO = 3.0

#: Hangul syllables + jamo; used only for the language-family heuristic.
_HANGUL_RE = re.compile(r"[가-힣ᄀ-ᇿ㄰-㆏]")


class Paraphraser(Protocol):
    """The narrow surface a paraphraser must implement.

    It sees ONLY ``text`` and ``language`` — deliberately not the slots or the
    oracle — so a paraphrase can never (be tempted to) encode a label.
    """

    def __call__(self, text: str, language: str) -> str: ...


class ScriptedParaphraser:
    """A deterministic fake paraphraser for tests (no network).

    Maps exact input utterances to canned paraphrases via ``mapping``; falls back
    to ``default`` (an identity-ish transform) for unmapped inputs. A test can
    seed a mapping that VIOLATES a guardrail to prove the post-check rejects it.
    """

    def __init__(
        self,
        mapping: dict[str, str] | None = None,
        *,
        default: Callable[[str, str], str] | None = None,
    ) -> None:
        self._mapping = dict(mapping or {})
        self._default = default or (lambda text, _lang: text)

    def __call__(self, text: str, language: str) -> str:
        if text in self._mapping:
            return self._mapping[text]
        return self._default(text, language)


# ---------------------------------------------------------------------------
# Guardrail post-checks
# ---------------------------------------------------------------------------


def _same_language(original: str, candidate: str) -> bool:
    """Cheap script-family check: the candidate stays in the original's family."""
    orig_has_hangul = bool(_HANGUL_RE.search(original))
    cand_has_hangul = bool(_HANGUL_RE.search(candidate))
    # ko utterances must keep Hangul; en utterances must not introduce Hangul.
    return orig_has_hangul == cand_has_hangul


def _within_length(original: str, candidate: str) -> bool:
    if not candidate.strip():
        return False
    return len(candidate) <= max(1, int(len(original) * _MAX_LEN_RATIO))


def slot_literals(scenario: Scenario) -> list[str]:
    """Slot-critical literals that MUST survive paraphrase, drawn from slots.

    For flow B: the gated tool + the allowlist domain (from the generated slot
    block). For flow A: the payload's identifying literal (tool name, pattern,
    denied tool) when present. These come from the SLOTS, not the utterance, so
    the check is oracle-grounded.
    """
    literals: list[str] = []
    gen = scenario.generated or {}
    slots = gen.get("slots") or {}
    if scenario.flow == "linked_policy":
        for key in ("gatedTool", "domain"):
            val = slots.get(key)
            if isinstance(val, str) and val:
                literals.append(val)
    else:
        # Flow A: pull the identifying literal out of the seed payload.
        payload = (scenario.seed_draft.get("what") or {}).get("payload") or {}
        for probe in (
            payload.get("match", {}).get("tool"),
            payload.get("pattern"),
        ):
            if isinstance(probe, str) and probe:
                literals.append(probe)
        deny = payload.get("denyTools")
        if isinstance(deny, list):
            literals.extend(d for d in deny if isinstance(d, str) and d)
    return literals


def paraphrase_ok(original: str, candidate: str, *, language: str, literals: list[str]) -> bool:
    """Every guardrail as one boolean. A False => the paraphrase is DROPPED."""
    if not _within_length(original, candidate):
        return False
    if not _same_language(original, candidate):
        return False
    for lit in literals:
        # A slot literal present in the original MUST survive verbatim.
        if lit in original and lit not in candidate:
            return False
    return True


# ---------------------------------------------------------------------------
# Expansion
# ---------------------------------------------------------------------------


def _normalize(text: str) -> str:
    return " ".join((text or "").casefold().split())


def expand_scenario(
    scenario: Scenario,
    paraphraser: Paraphraser,
    *,
    model_id: str = "scripted-fake",
) -> Scenario | None:
    """Return a paraphrased COPY of ``scenario`` or None if any turn is rejected.

    Only ``turns[*].say`` is rewritten (answers / llm_script / oracle / slots are
    copied). If ANY turn's paraphrase fails a guardrail OR the whole turn script
    dedups to the original, the scenario is dropped (returns None) rather than
    shipping a half-paraphrased or duplicate entry.
    """
    literals = slot_literals(scenario)
    new_turns: list[Turn] = []
    any_changed = False
    for turn in scenario.turns:
        if turn.say is None:
            new_turns.append(Turn(say=None, answers=dict(turn.answers),
                                   answers_from_slots=turn.answers_from_slots))
            continue
        candidate = paraphraser(turn.say, scenario.language)
        if not paraphrase_ok(turn.say, candidate, language=scenario.language, literals=literals):
            return None  # guardrail violation -> drop, never repair
        if _normalize(candidate) != _normalize(turn.say):
            any_changed = True
        new_turns.append(
            Turn(say=candidate, answers=dict(turn.answers),
                 answers_from_slots=turn.answers_from_slots)
        )
    if not any_changed:
        return None  # a no-op paraphrase adds no breadth -> dedup to nothing

    gen = dict(scenario.generated or {})
    gen["paraphrase_model"] = model_id
    return replace(
        scenario,
        id=f"{scenario.id}_pp",
        turns=new_turns,
        generated=gen,
    )


def expand_corpus(
    scenarios: list[Scenario],
    paraphraser: Paraphraser,
    *,
    model_id: str = "scripted-fake",
) -> list[Scenario]:
    """Paraphrase-expand a corpus, dropping guardrail failures and dedup-empties.

    Deduplicates across the accepted paraphrases by normalized turn signature so
    two templates that paraphrase to the same text yield one entry.
    """
    seen: set[tuple] = set()
    out: list[Scenario] = []
    for scenario in scenarios:
        expanded = expand_scenario(scenario, paraphraser, model_id=model_id)
        if expanded is None:
            continue
        sig = (expanded.flow, tuple(
            (_normalize(t.say or ""), tuple(sorted(t.answers.items())))
            for t in expanded.turns
        ))
        if sig in seen:
            continue
        seen.add(sig)
        out.append(expanded)
    return out


__all__ = [
    "Paraphraser",
    "ScriptedParaphraser",
    "expand_corpus",
    "expand_scenario",
    "paraphrase_ok",
    "slot_literals",
]
