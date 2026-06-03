from magi_agent.channels.workflow_classifier import (
    WorkflowEligibility,
    classify_workflow_eligibility,
)


class _FakeClassifier:
    def __init__(self, kind: str) -> None:
        self._kind = kind

    def classify(self, message_text: str) -> str:
        return self._kind


def test_eligible_kinds_route():
    for kind in ("source_sensitive_research", "complex_synthesis", "ambiguous_architecture"):
        out = classify_workflow_eligibility("anything", classifier=_FakeClassifier(kind))
        assert out.eligible is True
        assert out.task_kind == kind


def test_ineligible_kinds_do_not_route():
    for kind in ("simple_arithmetic", "general", "coding_change"):
        out = classify_workflow_eligibility("anything", classifier=_FakeClassifier(kind))
        assert out.eligible is False


def test_unknown_kind_defaults_ineligible():
    out = classify_workflow_eligibility("x", classifier=_FakeClassifier("not_a_real_kind"))
    assert out.eligible is False
    assert out.task_kind == "general"


def test_model_construct_disabled():
    import pytest
    with pytest.raises(TypeError):
        WorkflowEligibility.model_construct()
