from __future__ import annotations

import pyarrow as pa
import pyarrow.parquet as pq

from magi_agent.benchmarks.gaia.dataset import load_gaia_questions


def _write_parquet(path, rows: list[dict]) -> None:
    cols = ["task_id", "Question", "Level", "Final answer", "file_name", "file_path", "Annotator Metadata"]
    table = pa.table({c: [r.get(c, "") for r in rows] for c in cols})
    pq.write_table(table, path)


def test_loads_rows_and_levels(tmp_path) -> None:
    p = tmp_path / "metadata.parquet"
    _write_parquet(p, [
        {"task_id": "a", "Question": "Q1", "Level": "1", "Final answer": "x", "file_name": ""},
        {"task_id": "b", "Question": "Q2", "Level": "2", "Final answer": "y", "file_name": "b.xlsx"},
    ])
    qs = load_gaia_questions(str(p), attachments_dir=str(tmp_path))
    assert [q.task_id for q in qs] == ["a", "b"]
    assert qs[0].level == 1 and qs[1].level == 2
    assert qs[0].attachment_path is None
    assert qs[1].attachment_path == str(tmp_path / "b.xlsx")


def test_level_filter(tmp_path) -> None:
    p = tmp_path / "metadata.parquet"
    _write_parquet(p, [
        {"task_id": "a", "Question": "Q1", "Level": "1", "Final answer": "x", "file_name": ""},
        {"task_id": "b", "Question": "Q2", "Level": "3", "Final answer": "y", "file_name": ""},
    ])
    qs = load_gaia_questions(str(p), attachments_dir=str(tmp_path), levels=(1,))
    assert [q.task_id for q in qs] == ["a"]
