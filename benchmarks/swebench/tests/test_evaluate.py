import sys

import benchmarks.swebench.evaluate as ev


def test_run_evaluation_uses_current_interpreter(tmp_path, monkeypatch):
    """The eval subprocess must use the running interpreter, not a bare
    ``python`` (hosts that ship only ``python3`` would FileNotFoundError)."""
    captured: dict[str, object] = {}

    def fake_run(cmd, check, cwd):  # noqa: ANN001, ARG001
        captured["cmd"] = cmd

    monkeypatch.setattr(ev.subprocess, "run", fake_run)
    (tmp_path / "magi.run-x.json").write_text(
        '{"resolved_ids": ["a__b-1"], "total_instances": 1}', encoding="utf-8"
    )
    preds = tmp_path / "predictions.jsonl"
    preds.write_text("", encoding="utf-8")

    out = ev.run_evaluation(preds, run_id="run-x", max_workers=2)

    assert captured["cmd"][0] == sys.executable
    assert "swebench.harness.run_evaluation" in captured["cmd"]
    assert out.resolved_ids == {"a__b-1"}
