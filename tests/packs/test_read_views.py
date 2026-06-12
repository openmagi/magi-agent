import pytest

from magi_agent.packs.context import SessionReadView, EvidenceReadView


def test_session_read_view_is_frozen_and_narrow():
    sv = SessionReadView(
        invocation_id="inv-1",
        agent_name="root",
        turn_index=3,
        state={"k": "v"},
    )
    assert sv.invocation_id == "inv-1"
    assert sv.turn_index == 3
    assert sv.get_state("k") == "v"
    assert sv.get_state("missing") is None
    with pytest.raises(AttributeError):
        sv.turn_index = 4  # frozen


def test_session_state_is_a_copy_not_a_live_handle():
    src = {"k": "v"}
    sv = SessionReadView(invocation_id="i", agent_name="a", turn_index=0, state=src)
    src["k"] = "MUTATED"
    assert sv.get_state("k") == "v"  # snapshot, not live alias


def test_evidence_read_view_lists_owed_and_present():
    ev = EvidenceReadView(present=("file_write",), owed=("test_run",))
    assert ev.has("file_write") is True
    assert ev.has("test_run") is False
    assert ev.owed == ("test_run",)
