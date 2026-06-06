from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Prediction:
    instance_id: str
    model_name_or_path: str
    model_patch: str


def append_prediction(path: Path, prediction: Prediction) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(
        {
            "instance_id": prediction.instance_id,
            "model_name_or_path": prediction.model_name_or_path,
            "model_patch": prediction.model_patch,
        }
    )
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def load_completed_ids(path: Path) -> set[str]:
    if not path.exists():
        return set()
    ids: set[str] = set()
    for raw in path.read_text(encoding="utf-8").splitlines():
        raw = raw.strip()
        if not raw:
            continue
        ids.add(json.loads(raw)["instance_id"])
    return ids
