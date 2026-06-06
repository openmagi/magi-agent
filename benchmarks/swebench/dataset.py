from __future__ import annotations

from dataclasses import dataclass

DATASET_NAME = "princeton-nlp/SWE-bench_Verified"


@dataclass(frozen=True)
class Instance:
    instance_id: str
    repo: str
    base_commit: str
    problem_statement: str
    version: str


def load_verified() -> list[Instance]:
    """Load the SWE-bench Verified test split from Hugging Face.

    Imported lazily so the pure subset logic is testable without `datasets`.
    """
    from datasets import load_dataset  # noqa: PLC0415

    rows = load_dataset(DATASET_NAME, split="test")
    return [
        Instance(
            instance_id=row["instance_id"],
            repo=row["repo"],
            base_commit=row["base_commit"],
            problem_statement=row["problem_statement"],
            version=str(row["version"]),
        )
        for row in rows
    ]


def select_subset(
    instances: list[Instance],
    *,
    limit: int | None,
    only_ids: list[str] | None,
) -> list[Instance]:
    if only_ids is not None:
        by_id = {inst.instance_id: inst for inst in instances}
        return [by_id[i] for i in only_ids if i in by_id]
    if limit is not None:
        return instances[:limit]
    return list(instances)
