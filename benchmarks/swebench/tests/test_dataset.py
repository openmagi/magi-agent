from benchmarks.swebench.dataset import Instance, select_subset


def _mk(i: int) -> Instance:
    return Instance(
        instance_id=f"repo__proj-{i}",
        repo="repo/proj",
        base_commit="abc",
        problem_statement="fix it",
        version="1.0",
    )


def test_select_first_n():
    items = [_mk(i) for i in range(5)]
    chosen = select_subset(items, limit=2, only_ids=None)
    assert [c.instance_id for c in chosen] == ["repo__proj-0", "repo__proj-1"]


def test_select_only_ids_preserves_request_order():
    items = [_mk(i) for i in range(5)]
    chosen = select_subset(items, limit=None, only_ids=["repo__proj-3", "repo__proj-1"])
    assert [c.instance_id for c in chosen] == ["repo__proj-3", "repo__proj-1"]


def test_select_no_filter_returns_all():
    items = [_mk(i) for i in range(3)]
    assert len(select_subset(items, limit=None, only_ids=None)) == 3
