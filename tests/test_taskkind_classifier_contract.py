import asyncio

from magi_agent.channels.taskkind_classifier import FixedClassifier, TaskKindClassifier


class _FakePart:
    def __init__(self, text: str) -> None:
        self.text = text


class _FakeContent:
    def __init__(self, text: str) -> None:
        self.parts = [_FakePart(text)]


class _FakeResp:
    def __init__(self, text: str) -> None:
        self.content = _FakeContent(text)


class _FakeModel:
    """Mimics ADK model.generate_content_async(req, stream=False) → async iter."""

    def __init__(self, text: str) -> None:
        self._text = text

    async def generate_content_async(self, request, stream=False):  # noqa: ANN001
        yield _FakeResp(self._text)


def _factory(text: str):
    return lambda: _FakeModel(text)


def test_valid_label_returned():
    c = TaskKindClassifier(model_factory=_factory("source_sensitive_research"))
    assert asyncio.run(c.aclassify("compare A vs B")) == "source_sensitive_research"


def test_junk_label_falls_back_to_general():
    c = TaskKindClassifier(model_factory=_factory("definitely-not-a-kind"))
    assert asyncio.run(c.aclassify("x")) == "general"


def test_no_model_factory_is_general():
    c = TaskKindClassifier()
    assert asyncio.run(c.aclassify("x")) == "general"


def test_model_factory_raises_is_general():
    def boom():
        raise RuntimeError("no model")

    c = TaskKindClassifier(model_factory=boom)
    assert asyncio.run(c.aclassify("x")) == "general"


def test_llm_raises_is_general():
    class _BoomModel:
        async def generate_content_async(self, request, stream=False):  # noqa: ANN001
            raise RuntimeError("llm down")
            yield  # pragma: no cover

    c = TaskKindClassifier(model_factory=lambda: _BoomModel())
    assert asyncio.run(c.aclassify("x")) == "general"


def test_label_with_surrounding_text_is_parsed():
    c = TaskKindClassifier(model_factory=_factory("complex_synthesis\n"))
    assert asyncio.run(c.aclassify("x")) == "complex_synthesis"


def test_fixed_classifier_returns_label():
    assert FixedClassifier("ambiguous_architecture").classify("anything") == "ambiguous_architecture"


def test_fixed_classifier_invalid_label_general():
    assert FixedClassifier("bogus").classify("anything") == "general"
