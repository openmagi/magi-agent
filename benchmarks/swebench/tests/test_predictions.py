from pathlib import Path

from benchmarks.swebench.predictions import (
    Prediction,
    append_prediction,
    load_completed_ids,
)


def test_append_and_reload(tmp_path: Path):
    p = tmp_path / "preds.jsonl"
    append_prediction(p, Prediction("astropy__astropy-1", "magi", "diff --git a b"))
    append_prediction(p, Prediction("astropy__astropy-2", "magi", ""))
    ids = load_completed_ids(p)
    assert ids == {"astropy__astropy-1", "astropy__astropy-2"}


def test_load_completed_ids_missing_file(tmp_path: Path):
    assert load_completed_ids(tmp_path / "nope.jsonl") == set()


def test_jsonl_line_shape(tmp_path: Path):
    import json

    p = tmp_path / "preds.jsonl"
    append_prediction(p, Prediction("x__y-3", "magi", "PATCH"))
    obj = json.loads(p.read_text().splitlines()[0])
    assert obj == {
        "instance_id": "x__y-3",
        "model_name_or_path": "magi",
        "model_patch": "PATCH",
    }
