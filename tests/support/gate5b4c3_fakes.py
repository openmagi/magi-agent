"""Shared fake-ADK harness for gate5b4c3 boundary tests.

Provides:
- ``_Fake*`` stub classes replacing real ADK/Google-gen-AI objects.
- Specialized runner subclasses used across the boundary test suite.
- ``text_event(text)``, ``function_call_event(name, args)``,
  ``final_event(text)`` factory helpers that return ``_FakeEvent`` instances.
- ``make_primitives(runner)`` that builds a
  ``Gate5B4C3LiveAdkPrimitives`` whose ``Runner`` forwards to the
  given pre-constructed runner instance.
"""

from __future__ import annotations

from typing import Any

from magi_agent.shadow.gate5b4c3_live_runner_boundary import Gate5B4C3LiveAdkPrimitives

__all__ = [
    # core fakes
    "_FakePart",
    "_FakeContent",
    "_FakeAgent",
    "_FakeSessionService",
    "_FakeGenerateContentConfig",
    "_FakeRunner",
    "_FakeEvent",
    # function-call / response support
    "_FunctionCallOnlyPart",
    "_FunctionCallOnlyEvent",
    "_CandidateFunctionCallOnlyEvent",
    "_MethodFunctionCall",
    "_MethodFunctionCallOnlyEvent",
    "_FunctionResponseOnlyPart",
    "_FunctionResponseOnlyEvent",
    "_TextAndFunctionCallEvent",
    # specialized runners
    "_FunctionCallOnlyRunner",
    "_FunctionCallThenFinalRunner",
    "_DuplicateTextAndFunctionCallRunner",
    "_EventCapTextAndFunctionCallRunner",
    "_AutoToolLoopAgent",
    "_AutoToolLoopRunner",
    "_PromiseOnlyRunner",
    "_ManualCalculationTool",
    "_MappingContentPartsRunner",
    "_CandidateContentPartsRunner",
    "_ModelDumpCandidateContentRunner",
    "_PartialAggregateEvent",
    "_PartialAggregateRunner",
    "_FinishReasonTextEvent",
    "_OutputContinuationRunner",
    "_LongSelectedTextRunner",
    "_ModelDumpFunctionCallOnlyEvent",
    "_ModelDumpFunctionCallOnlyPart",
    "_PartModelDumpFunctionCallOnlyEvent",
    "_ProviderSetupFailRunner",
    "_GenericProxyFailRunner",
    "_FunctionToolSchemaTypeErrorRunner",
    "_RunnerConstructionFail",
    "_ToolHostAttachmentFailAgent",
    # factories + helpers
    "text_event",
    "function_call_event",
    "final_event",
    "make_primitives",
]


# ---------------------------------------------------------------------------
# Core stubs
# ---------------------------------------------------------------------------


class _FakePart:
    def __init__(self, text: str) -> None:
        self.text = text

    @classmethod
    def from_text(cls, *, text: str) -> "_FakePart":
        return cls(text)


class _FakeContent:
    def __init__(self, *, parts: list[_FakePart], role: str | None = None) -> None:
        self.parts = parts
        self.role = role


class _FakeAgent:
    created_kwargs: dict[str, object] = {}

    def __init__(self, **kwargs: object) -> None:
        type(self).created_kwargs = kwargs


class _FakeSessionService:
    pass


class _FakeGenerateContentConfig:
    created_kwargs: dict[str, object] = {}

    def __init__(self, **kwargs: object) -> None:
        type(self).created_kwargs = kwargs


class _FakeRunner:
    """Fake ADK Runner.

    Two construction modes:

    1. **Legacy class-state mode** (used when production code calls
       ``primitives.Runner(**kwargs)``):  ``_FakeRunner(**kwargs)`` stores
       ``kwargs`` on the class and yields a single default event.

    2. **Event-list mode** (used to build a ``make_primitives`` instance):
       ``_FakeRunner([event1, event2, ...])`` stores the provided events and
       yields them in order from ``run_async``.
    """

    created_kwargs: dict[str, object] = {}
    run_kwargs: dict[str, object] = {}
    fail: bool = False

    def __init__(self, events: list[Any] | None = None, **kwargs: object) -> None:
        if events is not None:
            # Event-list mode: store events for iteration.
            self._events: list[Any] = list(events)
        else:
            # Legacy class-state mode: record kwargs on the class.
            self._events = []
            type(self).created_kwargs = kwargs

    async def run_async(self, **kwargs: object) -> object:  # type: ignore[override]
        type(self).run_kwargs = kwargs
        if type(self).fail:
            raise RuntimeError("provider failed with Authorization: Bearer unsafe-token")
        if self._events:
            for event in self._events:
                yield event
        else:
            yield {"text": "local diagnostic event only"}


class _FakeEvent:
    def __init__(self, text: str) -> None:
        self.content = _FakeContent(parts=[_FakePart(text)], role="model")


# ---------------------------------------------------------------------------
# Function-call / response support classes
# ---------------------------------------------------------------------------


class _FunctionCallOnlyPart:
    function_call = {
        "name": "Calculation",
        "args": {"expression": "1 + 1"},
        "id": "calculation-call-001",
    }


class _FunctionCallOnlyEvent:
    class _Content:
        parts = [_FunctionCallOnlyPart()]

    content = _Content()


class _CandidateFunctionCallOnlyEvent:
    candidates = [
        {
            "content": {
                "parts": [
                    {
                        "functionCall": {
                            "name": "Calculation",
                            "args": {"expression": "2 + 3"},
                        }
                    }
                ]
            }
        }
    ]

    @property
    def text(self) -> str:
        return ""


class _MethodFunctionCall:
    name = "Calculation"
    args = {"expression": "3 + 4"}
    id = "call_method"


class _MethodFunctionCallOnlyEvent:
    @property
    def text(self) -> str:
        return ""

    def get_function_calls(self) -> list[object]:
        return [_MethodFunctionCall()]


class _FunctionResponseOnlyPart:
    function_response = {
        "name": "Calculation",
        "id": "calculation-call-001",
        "response": {"status": "ok"},
    }


class _FunctionResponseOnlyEvent:
    class _Content:
        parts = [_FunctionResponseOnlyPart()]

    content = _Content()


class _TextAndFunctionCallEvent:
    """A single model turn that emits preamble text AND a pending tool call.

    This is the shape that produced "promise without delivery": the model says
    it will do the work and emits the function call in the same turn.
    """

    def __init__(self) -> None:
        self.content = _FakeContent(
            parts=[
                _FakePart("재무제표 분석을 진행하겠습니다."),
                _FunctionCallOnlyPart(),
            ],
            role="model",
        )


# ---------------------------------------------------------------------------
# Specialized runners
# ---------------------------------------------------------------------------


class _FunctionCallOnlyRunner(_FakeRunner):
    async def run_async(self, **kwargs: object) -> object:  # type: ignore[override]
        type(self).run_kwargs = kwargs
        yield _FunctionCallOnlyEvent()


class _FunctionCallThenFinalRunner(_FakeRunner):
    calls: list[dict[str, object]] = []
    event_factory: object = _FunctionCallOnlyEvent

    async def run_async(self, **kwargs: object) -> object:  # type: ignore[override]
        type(self).run_kwargs = kwargs
        type(self).calls.append(kwargs)
        if len(type(self).calls) == 1:
            factory = type(self).event_factory
            yield factory() if callable(factory) else factory
            return
        message = kwargs["new_message"]
        assert isinstance(message, _FakeContent)
        assert "Tool execution results" in message.parts[0].text
        yield _FakeEvent("final answer after manual tool execution")


class _DuplicateTextAndFunctionCallRunner(_FakeRunner):
    calls: list[dict[str, object]] = []

    async def run_async(self, **kwargs: object) -> object:  # type: ignore[override]
        type(self).run_kwargs = kwargs
        type(self).calls.append(kwargs)
        if len(type(self).calls) == 1:
            yield _TextAndFunctionCallEvent()
            yield _TextAndFunctionCallEvent()
            return
        message = kwargs["new_message"]
        assert isinstance(message, _FakeContent)
        assert "Tool execution results" in message.parts[0].text
        yield _FakeEvent("final answer after one manual tool execution")


class _EventCapTextAndFunctionCallRunner(_FakeRunner):
    async def run_async(self, **kwargs: object) -> object:  # type: ignore[override]
        type(self).run_kwargs = kwargs
        for _ in range(63):
            yield _FakeEvent("")
        yield _TextAndFunctionCallEvent()


class _AutoToolLoopAgent:
    created_kwargs: list[dict[str, object]] = []

    def __init__(self, **kwargs: object) -> None:
        self.tools = tuple(kwargs.get("tools", ()))
        type(self).created_kwargs.append(kwargs)


class _AutoToolLoopRunner(_FakeRunner):
    calls: list[dict[str, object]] = []
    after_function_call_observer: object = None

    def __init__(self, **kwargs: object) -> None:
        self.agent = kwargs["agent"]
        type(self).created_kwargs = kwargs

    async def run_async(self, **kwargs: object) -> object:  # type: ignore[override]
        type(self).run_kwargs = kwargs
        type(self).calls.append(
            {
                "toolsAttached": bool(getattr(self.agent, "tools", ())),
                "newMessage": kwargs.get("new_message"),
                "runConfigPresent": kwargs.get("run_config") is not None,
            }
        )
        if getattr(self.agent, "tools", ()):
            yield _FunctionCallOnlyEvent()
            if callable(type(self).after_function_call_observer):
                type(self).after_function_call_observer()
            yield _FunctionResponseOnlyEvent()
            return
        yield _FakeEvent("final answer after no-tool finalizer")


class _PromiseOnlyRunner(_FakeRunner):
    async def run_async(self, **kwargs: object) -> object:  # type: ignore[override]
        type(self).run_kwargs = kwargs
        yield _FakeEvent(
            "선정된 종목들에 대해 /multibagger-full-report 분석을 병렬로 실행하겠습니다. "
            "잠시만 기다려 주세요."
        )


class _ManualCalculationTool:
    name = "Calculation"
    calls: list[dict[str, object]] = []

    @classmethod
    async def run_async(
        cls,
        *,
        args: dict[str, object],
        tool_context: object,
    ) -> dict[str, object]:
        del tool_context
        cls.calls.append(args)
        return {
            "status": "ok",
            "reason": "tool_completed",
            "outputPreview": {"value": 2},
        }


class _MappingContentPartsRunner(_FakeRunner):
    async def run_async(self, **kwargs: object) -> object:  # type: ignore[override]
        type(self).run_kwargs = kwargs
        yield {"content": {"parts": ({"text": "live ADK text from mapping parts"},)}}


class _CandidateContentPartsRunner(_FakeRunner):
    async def run_async(self, **kwargs: object) -> object:  # type: ignore[override]
        type(self).run_kwargs = kwargs
        yield {
            "candidates": (
                {
                    "content": {
                        "parts": (
                            {"text": "live ADK text from candidate parts"},
                        )
                    }
                },
            )
        }


class _ModelDumpCandidateContentRunner(_FakeRunner):
    class _Event:
        def model_dump(self, **_kwargs: object) -> dict[str, object]:
            return {
                "candidates": (
                    {
                        "content": {
                            "parts": (
                                {"text": "live ADK text from model dump"},
                            )
                        }
                    },
                )
            }

    async def run_async(self, **kwargs: object) -> object:  # type: ignore[override]
        type(self).run_kwargs = kwargs
        yield self._Event()


class _PartialAggregateEvent:
    def __init__(self, text: str, *, partial: bool) -> None:
        self.partial = partial
        self.content = _FakeContent(parts=[_FakePart(text)], role="model")


class _PartialAggregateRunner(_FakeRunner):
    async def run_async(self, **kwargs: object) -> object:  # type: ignore[override]
        type(self).run_kwargs = kwargs
        yield _PartialAggregateEvent("EX", partial=True)
        yield _PartialAggregateEvent("ACTLY_ONCE_SENTINEL_9Q4Z", partial=True)
        yield _PartialAggregateEvent("EXACTLY_ONCE_SENTINEL_9Q4Z", partial=False)


class _FinishReasonTextEvent(_FakeEvent):
    def __init__(self, text: str, finish_reason: str) -> None:
        super().__init__(text)
        self.finish_reason = finish_reason


class _OutputContinuationRunner(_FakeRunner):
    calls: list[dict[str, object]] = []

    async def run_async(self, **kwargs: object) -> object:  # type: ignore[override]
        type(self).run_kwargs = kwargs
        type(self).calls.append(kwargs)
        if len(type(self).calls) == 1:
            yield _FinishReasonTextEvent("section one is cut", "length")
            return
        message = kwargs["new_message"]
        assert isinstance(message, _FakeContent)
        assert "Continue exactly where you left off" in message.parts[0].text
        yield _FakeEvent(" off and then finishes. END_LONG_SMOKE")


class _LongSelectedTextRunner(_FakeRunner):
    async def run_async(self, **kwargs: object) -> object:  # type: ignore[override]
        type(self).run_kwargs = kwargs
        for index in range(560):
            suffix = " END_LONG_SMOKE" if index == 559 else ""
            yield _FakeEvent(f"{index:03d}-segment{suffix} ")


class _ModelDumpFunctionCallOnlyEvent:
    @property
    def text(self) -> str:
        return ""

    def model_dump(self, **_kwargs: object) -> dict[str, object]:
        return {
            "functionCalls": [
                {
                    "name": "Calculation",
                    "args": {"expression": "5 + 6"},
                    "id": "dump_call",
                }
            ]
        }


class _ModelDumpFunctionCallOnlyPart:
    def model_dump(self, **_kwargs: object) -> dict[str, object]:
        return {
            "function_call": {
                "name": "Calculation",
                "args": {"expression": "7 + 8"},
                "id": "part_dump_call",
            }
        }


class _PartModelDumpFunctionCallOnlyEvent:
    @property
    def text(self) -> str:
        return ""

    @property
    def content(self) -> object:
        return type(
            "_Content",
            (),
            {"parts": [_ModelDumpFunctionCallOnlyPart()]},
        )()


class _ProviderSetupFailRunner(_FakeRunner):
    async def run_async(self, **kwargs: object) -> object:  # type: ignore[override]
        type(self).run_kwargs = kwargs
        raise RuntimeError(
            "No API key configured at /Users/kevin/private with "
            "Authorization: Bearer raw-token prompt=secret-output"
        )
        yield {"text": "must not happen"}  # noqa: unreachable


class _GenericProxyFailRunner(_FakeRunner):
    async def run_async(self, **kwargs: object) -> object:  # type: ignore[override]
        type(self).run_kwargs = kwargs
        raise RuntimeError("ProxyError: upstream tunnel reset after CONNECT")
        yield {"text": "must not happen"}  # noqa: unreachable


class _FunctionToolSchemaTypeErrorRunner(_FakeRunner):
    async def run_async(self, **kwargs: object) -> object:  # type: ignore[override]
        type(self).run_kwargs = kwargs
        raise TypeError(
            "FunctionTool schema signature mismatch at /Users/kevin/private "
            "Authorization: Bearer raw-token prompt=secret-output"
        )
        yield {"text": "must not happen"}  # noqa: unreachable


class _RunnerConstructionFail:
    def __init__(self, **_kwargs: object) -> None:
        raise RuntimeError(
            "Runner construction failed at /Users/kevin/private with token=secret"
        )


class _ToolHostAttachmentFailAgent:
    created_kwargs: dict[str, object] = {}

    def __init__(self, **kwargs: object) -> None:
        type(self).created_kwargs = kwargs
        raise RuntimeError(
            "ToolHost attachment failed with Cookie: session=secret and /private/path"
        )


# ---------------------------------------------------------------------------
# Event factory helpers
# ---------------------------------------------------------------------------


def text_event(text: str) -> _FakeEvent:
    """Return a ``_FakeEvent`` carrying ``text`` as its model output."""
    return _FakeEvent(text)


def function_call_event(name: str, args: dict[str, object]) -> _FakeEvent:
    """Return a ``_FakeEvent`` shaped like a function-call turn.

    The event's ``content`` carries a single part whose ``function_call``
    attribute mirrors the ``_FunctionCallOnlyPart`` shape used throughout the
    boundary suite.
    """

    class _Part:
        function_call = {"name": name, "args": args, "id": f"{name}-call-001"}

    class _CallContent:
        parts = [_Part()]
        role = "model"

    event = object.__new__(_FakeEvent)
    event.content = _CallContent()  # type: ignore[attr-defined]
    return event  # type: ignore[return-value]


def final_event(text: str) -> _FakeEvent:
    """Return a ``_FakeEvent`` intended as a final-turn text response."""
    return _FakeEvent(text)


# ---------------------------------------------------------------------------
# make_primitives
# ---------------------------------------------------------------------------


def make_primitives(runner: object) -> Gate5B4C3LiveAdkPrimitives:
    """Build a ``Gate5B4C3LiveAdkPrimitives`` that uses the given runner instance.

    When production code calls ``primitives.Runner(**kwargs)``, the wrapper
    class ignores ``**kwargs`` and returns ``runner`` unchanged, so callers
    can pre-build the runner (e.g. ``_FakeRunner([text_event("hi")])``) and
    have it wired into the boundary under test.
    """
    _runner = runner

    class _RunnerWrapper:
        """Thin wrapper so ``primitives.Runner(**kwargs)`` returns ``_runner``."""

        def __new__(cls, **kwargs: object) -> object:  # type: ignore[misc]
            del kwargs
            return _runner

    return Gate5B4C3LiveAdkPrimitives(
        Agent=_FakeAgent,
        Runner=_RunnerWrapper,
        InMemorySessionService=_FakeSessionService,
        Content=_FakeContent,
        Part=_FakePart,
        GenerateContentConfig=_FakeGenerateContentConfig,
    )
