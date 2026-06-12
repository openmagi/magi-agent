"""Multi-problem (multi-bug) benchmark instances for the discovery harness.

An *instance* is one repository snapshot at a fixed anchor commit whose
``candidates`` form the discovery corpus (buggy functions plus distractors) and
whose ``gold_problems`` enumerate the >=2 coexisting bugs the harness is meant to
uncover. Models are frozen pydantic to match the repo style (see
``benchmarks/gaia/dataset.py`` and ``magi_agent/discovery/models.py``).

``load_instances`` reads a local JSON/JSONL file and is what tests use.
``build_instances_from_swebench`` is a thin grouping utility (mirrors
``gaia/download.py``): its pure grouping/filtering logic IS unit-tested; only the
upstream HuggingFace/SWE-bench fetch that produces its input is live-only
(network-gated) and therefore exercised outside the unit tests.
"""
from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from magi_agent.discovery.models import DiscoveryCorpus

_MODEL_CONFIG = ConfigDict(frozen=True, extra="forbid")


class GoldProblem(BaseModel):
    """One ground-truth bug in a multi-problem instance.

    Fields
    ------
    problem_id:
        Stable identifier for the bug (e.g. the SWE-bench issue id).
    evidence_ids:
        Candidate ids implicated in this bug (the gold ``D̂`` against which a
        prediction's evidence overlap is scored).
    gold_patch:
        The reference fix (resolution gold) — consumed by an LLM judge later,
        not by the deterministic retrieval scorer.
    description:
        Optional natural-language statement of the bug.
    """

    model_config = _MODEL_CONFIG

    problem_id: str
    evidence_ids: tuple[str, ...] = ()
    gold_patch: str = ""
    description: str = ""


class MultiProblemInstance(BaseModel):
    """A repository snapshot carrying >=2 coexisting bugs.

    Fields
    ------
    instance_id:
        Stable identifier for this grouped instance.
    repo:
        ``owner/name`` of the source repository.
    anchor_commit:
        The common commit at which every grouped bug is unfixed.
    candidates:
        ``candidate_id -> source text`` — the corpus, including distractors.
    gold_problems:
        The >=2 ground-truth bugs (validated at construction).
    """

    model_config = _MODEL_CONFIG

    instance_id: str
    repo: str
    anchor_commit: str
    candidates: Mapping[str, str]
    gold_problems: tuple[GoldProblem, ...] = Field(min_length=2)

    def to_corpus(self) -> DiscoveryCorpus:
        """Return the discovery corpus built from this instance's candidates."""
        return DiscoveryCorpus(items=dict(self.candidates))


def load_instances(path: str | Path) -> tuple[MultiProblemInstance, ...]:
    """Load instances from a local JSON or JSONL file.

    Accepts either a JSONL file (one instance object per non-blank line) or a
    JSON file containing a single top-level array of instance objects. This is
    the format tests round-trip.

    Raises
    ------
    ValueError
        If the file content is neither a JSON array nor valid JSONL, or if any
        instance fails validation (notably the >=2 ``gold_problems`` rule).
    """
    text = Path(path).read_text(encoding="utf-8")
    stripped = text.lstrip()
    raw_objects: list[object]
    if stripped.startswith("["):
        decoded = json.loads(text)
        if not isinstance(decoded, list):
            raise ValueError(f"{path}: top-level JSON must be an array of instances")
        raw_objects = decoded
    else:
        raw_objects = []
        for line_no, line in enumerate(text.splitlines(), start=1):
            line = line.strip()
            if not line:
                continue
            try:
                raw_objects.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_no}: invalid JSONL line") from exc

    return tuple(MultiProblemInstance.model_validate(obj) for obj in raw_objects)


def build_instances_from_swebench(
    issues: Sequence[Mapping[str, object]],
    *,
    min_problems: int = 2,
    min_functions: int = 2,
) -> tuple[MultiProblemInstance, ...]:
    """Group same-repo SWE-bench / TestExplora issues into multi-bug instances.

    Per the TIDE paper §3.1, valid multi-problem instances group issues from the
    SAME repository at a COMMON anchor commit where every grouped buggy function
    is still unfixed, keeping only groups with ``>=min_problems`` coexisting bugs
    that span ``>=min_functions`` distinct functions.

    Each ``issue`` mapping is expected to expose at least::

        {"repo": str, "anchor_commit": str, "problem_id": str,
         "evidence_ids": [str, ...], "gold_patch": str, "candidates": {id: src},
         "functions": [str, ...]}

    This function depends only on already-grouped issue records, so its pure
    grouping/filtering logic IS unit-tested (see
    ``tests/benchmarks/multibug/test_dataset.py``). The upstream
    HuggingFace/``datasets`` fetch that produces those records is a separate,
    live-only concern (mirrors ``gaia/download.py``) and is deliberately NOT
    performed here so tests never require network.
    """
    grouped: dict[tuple[str, str], list[Mapping[str, object]]] = {}
    for issue in issues:
        key = (str(issue["repo"]), str(issue["anchor_commit"]))
        grouped.setdefault(key, []).append(issue)

    instances: list[MultiProblemInstance] = []
    for (repo, anchor_commit), group in grouped.items():
        if len(group) < min_problems:
            continue
        functions: set[str] = set()
        for issue in group:
            functions.update(str(fn) for fn in (issue.get("functions") or ()))
        if len(functions) < min_functions:
            continue

        candidates: dict[str, str] = {}
        gold_problems: list[GoldProblem] = []
        for issue in group:
            candidates.update(
                {str(k): str(v) for k, v in dict(issue.get("candidates") or {}).items()}
            )
            gold_problems.append(
                GoldProblem(
                    problem_id=str(issue["problem_id"]),
                    evidence_ids=tuple(
                        str(e) for e in (issue.get("evidence_ids") or ())
                    ),
                    gold_patch=str(issue.get("gold_patch") or ""),
                    description=str(issue.get("description") or ""),
                )
            )

        instances.append(
            MultiProblemInstance(
                instance_id=f"{repo}@{anchor_commit}",
                repo=repo,
                anchor_commit=anchor_commit,
                candidates=candidates,
                gold_problems=tuple(gold_problems),
            )
        )

    return tuple(instances)


__all__ = [
    "GoldProblem",
    "MultiProblemInstance",
    "build_instances_from_swebench",
    "load_instances",
]
