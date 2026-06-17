from magi_agent.runtime.child_derive import derive, _child_memory_mode
from magi_agent.runtime.child_runner_boundary import ChildTaskRequest


def _req(**kw):
    base = dict(parentExecutionId="p", turnId="t", taskId="k", objective="do x")
    base.update(kw)
    return ChildTaskRequest(**base)


def test_memory_inherit_off_keeps_incognito():
    assert _child_memory_mode("normal", memory_inherit_enabled=False) == "incognito"


def test_memory_inherit_on_maps_normal_to_read_only():
    assert _child_memory_mode("normal", memory_inherit_enabled=True) == "read_only"
    # read_only / incognito parents propagate as-is; never 'normal'
    assert _child_memory_mode("read_only", memory_inherit_enabled=True) == "read_only"
    assert _child_memory_mode("incognito", memory_inherit_enabled=True) == "incognito"


def test_derive_sets_child_session_depth_and_no_cap():
    ctx = derive(_req(provider="anthropic", model="claude-sonnet-4-6", budgetMs=1234),
                 parent_memory_mode="normal", parent_depth=1,
                 memory_inherit_enabled=False, child_session_id="child-abc")
    assert ctx.session_id == "child-abc"
    assert ctx.depth == 2
    assert ctx.provider == "anthropic" and ctx.model == "claude-sonnet-4-6"
    assert ctx.budget_ms == 1234
    assert ctx.permission_cap is None and ctx.recipe is None
    assert ctx.memory_mode == "incognito"  # flag off
