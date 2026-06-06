from benchmarks.swebench.report import summarize


def test_summarize_basic():
    s = summarize(resolved_ids={"a", "b"}, attempted_ids={"a", "b", "c", "d"})
    assert s.resolved == 2
    assert s.attempted == 4
    assert s.resolved_pct == 50.0


def test_summarize_delta():
    s = summarize(
        resolved_ids={"a", "b", "c"},
        attempted_ids={"a", "b", "c", "d"},
        baseline_resolved_ids={"a"},
    )
    assert s.delta_resolved == 2
    assert sorted(s.newly_resolved) == ["b", "c"]
    assert s.regressed == []
