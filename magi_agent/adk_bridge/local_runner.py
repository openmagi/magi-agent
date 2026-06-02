from __future__ import annotations

import os
from dataclasses import dataclass
from typing import AsyncGenerator

from google.adk.agents import Agent
from google.adk.artifacts import InMemoryArtifactService
from google.adk.memory import InMemoryMemoryService
from google.adk.models import BaseLlm, LlmRequest, LlmResponse
from google.adk.runners import Runner

from magi_agent.adk_bridge.local_toolhost import (
    LocalToolHostAdkBundle,
    is_local_fake_receipt_adk_tool,
)
from magi_agent.adk_bridge.session_service import WorkspaceSessionService

LOCAL_ADK_RUNNER_FLAG = "CORE_AGENT_PYTHON_LOCAL_ADK_RUNNER"
LOCAL_INERT_MODEL_NAME = "openmagi-local-inert"
_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})


class LocalAdkRunnerDisabled(RuntimeError):
    pass


class LocalAdkRunnerExecutionBlocked(RuntimeError):
    pass


class LocalAdkRunnerToolAttachmentRejected(TypeError):
    pass


class LocalInertLlm(BaseLlm):
    async def generate_content_async(
        self,
        llm_request: LlmRequest,
        stream: bool = False,
    ) -> AsyncGenerator[LlmResponse, None]:
        raise LocalAdkRunnerExecutionBlocked(
            "The local-only inert ADK model blocks generation to prevent provider traffic"
        )
        if False:
            yield LlmResponse()


@dataclass(frozen=True)
class LocalAdkRunnerBundle:
    agent: Agent
    runner: Runner
    session_service: WorkspaceSessionService
    memory_service: InMemoryMemoryService
    artifact_service: InMemoryArtifactService
    local_only: bool = True
    traffic_attached: bool = False
    production_attached: bool = False
    canary_attached: bool = False
    route_attached: bool = False
    deploy_attached: bool = False
    telegram_attached: bool = False
    user_visible_output_attached: bool = False
    transcript_write_attached: bool = False
    sse_write_attached: bool = False
    control_write_attached: bool = False
    db_write_attached: bool = False
    workspace_mutation_attached: bool = False


def is_local_adk_runner_enabled() -> bool:
    return os.environ.get(LOCAL_ADK_RUNNER_FLAG, "").strip().lower() in _TRUE_VALUES


def build_local_adk_runner(
    *,
    app_name: str = "magi-agent-local",
    agent_name: str = "magi_agent_local",
    instruction: str = "Local-only OpenMagi ADK runner attachment test agent.",
    tools: LocalToolHostAdkBundle | None = None,
) -> LocalAdkRunnerBundle:
    if not is_local_adk_runner_enabled():
        raise LocalAdkRunnerDisabled(
            f"{LOCAL_ADK_RUNNER_FLAG} must be set to a true-ish value for local ADK runner construction"
        )

    adk_tools = _local_toolhost_bundle_tools(tools)
    agent = Agent(
        name=agent_name,
        model=LocalInertLlm(model=LOCAL_INERT_MODEL_NAME),
        instruction=instruction,
        tools=list(adk_tools),
    )
    session_service = WorkspaceSessionService(app_name=app_name)
    memory_service = InMemoryMemoryService()
    artifact_service = InMemoryArtifactService()
    runner = Runner(
        app_name=app_name,
        agent=agent,
        session_service=session_service,
        memory_service=memory_service,
        artifact_service=artifact_service,
    )
    return LocalAdkRunnerBundle(
        agent=agent,
        runner=runner,
        session_service=session_service,
        memory_service=memory_service,
        artifact_service=artifact_service,
    )


def _local_toolhost_bundle_tools(
    tools: LocalToolHostAdkBundle | None,
) -> tuple[object, ...]:
    if tools is None:
        return ()
    if not isinstance(tools, LocalToolHostAdkBundle):
        raise LocalAdkRunnerToolAttachmentRejected(
            "build_local_adk_runner tools must be a LocalToolHostAdkBundle from local_toolhost"
        )
    if not tools.local_only:
        raise LocalAdkRunnerToolAttachmentRejected("local ADK runner only accepts local-only tools")
    rejected = [tool for tool in tools.tools if not is_local_fake_receipt_adk_tool(tool)]
    if rejected:
        names = ", ".join(getattr(tool, "name", type(tool).__name__) for tool in rejected)
        raise LocalAdkRunnerToolAttachmentRejected(
            f"local ADK runner only accepts local fake receipt tools; rejected: {names}"
        )
    return tools.tools
