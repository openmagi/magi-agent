import asyncio
from tests.support.engine_fakes import MockRunner, text_event, call_event, response_event


def test_mockrunner_yields_real_adk_events():
    runner = MockRunner([text_event("hi"), call_event("Bash", {"cmd": "ls"}, "c1"),
                         response_event("Bash", {"out": "x"}, "c1")])

    async def _collect():
        return [e async for e in runner.run_async(user_id="u", session_id="s", new_message=None)]

    events = asyncio.run(_collect())
    assert len(events) == 3
    # real ADK Event carries author/content
    assert events[0].content.parts[0].text == "hi"
