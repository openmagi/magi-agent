"""A real, model-backed runner for the local ``magi`` CLI.

:class:`CliModelRunner` wraps a genuine ADK ``Runner`` so it drops into the same
seam the stub :class:`~magi_agent.cli.local_runner.LocalCliRunner` occupies:

* it exposes ``.agent`` (the permission gate attaches a ``before_tool_callback``
  to ``runner.agent``), and
* its ``run_async(**kwargs)`` accepts the adapter's
  ``user_id / session_id / invocation_id / new_message`` kwargs.

Unlike a bare ADK ``Runner``, the wrapper lazily creates the session before the
first turn (``Runner.run_async`` requires an existing session), so the engine and
adapter need no change.

The model is built via ADK's ``LiteLlm`` so all four supported providers
(``openai`` / ``anthropic`` / ``gemini`` / ``fireworks``) share one path. ``LiteLlm``
needs the optional ``litellm`` dependency; if it is missing we raise
:class:`CliProviderDependencyError` with an actionable install hint.
"""

from __future__ import annotations

from typing import AsyncGenerator, Callable

from magi_agent.cli.providers import ProviderConfig

DEFAULT_INSTRUCTION = (
    "You are Magi, an autonomous AI agent running locally through the magi CLI. "
    "Help the user with their request directly and concisely. When you do not "
    "have a tool for something, explain what you would do."
)

# Type of the model-construction hook (injectable for tests).
ModelFactory = Callable[[ProviderConfig], object]


class CliProviderDependencyError(RuntimeError):
    """A provider is configured but its runtime dependency is not installed."""


class CliModelRunner:
    """Adapter exposing a real ADK ``Runner`` through the CLI runner contract."""

    def __init__(
        self,
        *,
        runner: object,
        agent: object,
        session_service: object,
        app_name: str,
        user_id: str = "cli-user",
        session_id: str = "cli-session",
    ) -> None:
        self._runner = runner
        self._agent = agent
        self._session_service = session_service
        self._app_name = app_name
        self._default_user_id = user_id
        self._default_session_id = session_id

    @property
    def agent(self) -> object:
        return self._agent

    async def run_async(self, **kwargs: object) -> AsyncGenerator[object, None]:
        user_id = _as_str(kwargs.get("user_id"), self._default_user_id)
        session_id = _as_str(kwargs.get("session_id"), self._default_session_id)
        await self._ensure_session(user_id=user_id, session_id=session_id)
        async for event in self._runner.run_async(**kwargs):  # type: ignore[attr-defined]
            yield event

    async def _ensure_session(self, *, user_id: str, session_id: str) -> None:
        existing = await self._session_service.get_session(  # type: ignore[attr-defined]
            app_name=self._app_name, user_id=user_id, session_id=session_id
        )
        if existing is None:
            await self._session_service.create_session(  # type: ignore[attr-defined]
                app_name=self._app_name, user_id=user_id, session_id=session_id
            )


def build_cli_model_runner(
    config: ProviderConfig,
    *,
    app_name: str = "magi-cli",
    agent_name: str = "magi_cli_agent",
    instruction: str = DEFAULT_INSTRUCTION,
    tools: list[object] | None = None,
    model_factory: ModelFactory | None = None,
    user_id: str = "cli-user",
) -> CliModelRunner:
    """Build a real, model-backed CLI runner from a resolved provider config."""

    from google.adk.agents import Agent  # noqa: PLC0415
    from google.adk.apps.app import App  # noqa: PLC0415
    from google.adk.artifacts import InMemoryArtifactService  # noqa: PLC0415
    from google.adk.memory import InMemoryMemoryService  # noqa: PLC0415
    from google.adk.runners import Runner  # noqa: PLC0415

    from magi_agent.adk_bridge.session_service import (  # noqa: PLC0415
        WorkspaceSessionService,
    )

    build_model = model_factory or _build_litellm_model
    model = build_model(config)

    agent = Agent(
        name=agent_name,
        model=model,
        instruction=instruction,
        tools=list(tools or []),
    )
    session_service = WorkspaceSessionService(app_name=app_name)
    app = App(name=_app_identifier(app_name), root_agent=agent, plugins=[])
    runner = Runner(
        app=app,
        app_name=app_name,
        session_service=session_service,
        memory_service=InMemoryMemoryService(),
        artifact_service=InMemoryArtifactService(),
    )
    return CliModelRunner(
        runner=runner,
        agent=agent,
        session_service=session_service,
        app_name=app_name,
        user_id=user_id,
    )


def _build_litellm_model(config: ProviderConfig) -> object:
    try:
        from google.adk.models.lite_llm import LiteLlm  # noqa: PLC0415
    except Exception as exc:  # ImportError or downstream litellm import errors.
        raise CliProviderDependencyError(
            f"Provider '{config.provider}' is configured but the 'litellm' "
            "dependency is not installed. Install it with: "
            "pip install 'magi-agent[providers]'"
        ) from exc
    return LiteLlm(model=config.litellm_model, api_key=config.api_key)


def _app_identifier(app_name: str) -> str:
    """Coerce ``app_name`` into a valid identifier for ``App.name``.

    ``App`` validates ``name.isidentifier()`` (rejecting hyphens), while the
    runner's visible ``app_name`` may contain them.
    """

    sanitized = "".join(c if c.isalnum() or c == "_" else "_" for c in app_name)
    if not sanitized or (not sanitized[0].isalpha() and sanitized[0] != "_"):
        sanitized = f"_{sanitized}"
    return sanitized if sanitized.isidentifier() else "magi_cli_agent"


def _as_str(value: object, default: str) -> str:
    return value if isinstance(value, str) and value else default


__all__ = [
    "CliModelRunner",
    "CliProviderDependencyError",
    "DEFAULT_INSTRUCTION",
    "build_cli_model_runner",
]
