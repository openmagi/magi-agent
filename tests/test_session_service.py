import asyncio

from google.adk.events import Event

from openmagi_core_agent.adk_bridge.session_service import (
    SessionDeletionRequiresReviewError,
    WorkspaceSessionService,
)


def test_workspace_session_service_create_get_append_and_list() -> None:
    seen: list[Event] = []
    service = WorkspaceSessionService(app_name="openmagi", event_sink=seen.append)

    async def exercise():
        session = await service.create_session(
            app_name="openmagi",
            user_id="user-1",
            session_id="agent:main:app:default",
            state={"openmagi.memoryMode": "normal"},
        )
        event = Event(author="model", invocation_id="turn-1")
        appended = await service.append_event(session, event)
        loaded = await service.get_session(
            app_name="openmagi",
            user_id="user-1",
            session_id="agent:main:app:default",
        )
        listed = await service.list_sessions(app_name="openmagi", user_id="user-1")
        return session, event, appended, loaded, listed

    _session, event, appended, loaded, listed = asyncio.run(exercise())

    assert appended is event
    assert loaded is not None
    assert loaded.id == "agent:main:app:default"
    assert loaded.events == [event]
    assert seen == [event]
    assert [item.id for item in listed.sessions] == ["agent:main:app:default"]


def test_workspace_session_service_delete_is_unsupported_for_phase_2() -> None:
    service = WorkspaceSessionService(app_name="openmagi")

    async def exercise() -> None:
        await service.delete_session(app_name="openmagi", user_id="user-1", session_id="s")

    try:
        asyncio.run(exercise())
    except SessionDeletionRequiresReviewError as exc:
        assert "requires product review" in str(exc)
        return
    raise AssertionError("delete_session should be unsupported")
