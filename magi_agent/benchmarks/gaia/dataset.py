"""Load GAIA questions from a local parquet metadata file."""
from __future__ import annotations

import os
from collections.abc import Sequence

import pyarrow.parquet as pq
from pydantic import BaseModel, ConfigDict, Field

_MODEL_CONFIG = ConfigDict(frozen=True, extra="forbid")


class GaiaQuestion(BaseModel):
    model_config = _MODEL_CONFIG

    task_id: str
    question: str
    level: int
    final_answer: str = Field(default="")
    file_name: str = Field(default="")
    attachment_path: str | None = None


def load_gaia_questions(
    metadata_path: str,
    *,
    attachments_dir: str,
    levels: Sequence[int] | None = None,
) -> tuple[GaiaQuestion, ...]:
    table = pq.read_table(metadata_path)
    data = table.to_pydict()
    n = table.num_rows
    wanted = set(levels) if levels is not None else None
    out: list[GaiaQuestion] = []
    for i in range(n):
        level = int(str(data["Level"][i]))
        if wanted is not None and level not in wanted:
            continue
        file_name = str(data.get("file_name", [""] * n)[i] or "")
        attachment = os.path.join(attachments_dir, file_name) if file_name else None
        out.append(
            GaiaQuestion(
                task_id=str(data["task_id"][i]),
                question=str(data["Question"][i]),
                level=level,
                final_answer=str(data.get("Final answer", [""] * n)[i] or ""),
                file_name=file_name,
                attachment_path=attachment,
            )
        )
    return tuple(out)


__all__ = ["GaiaQuestion", "load_gaia_questions"]
