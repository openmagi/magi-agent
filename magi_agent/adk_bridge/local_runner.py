from __future__ import annotations

import os
from dataclasses import dataclass
from typing import AsyncGenerator

from google.adk.agents import Agent
from google.adk.apps.app import App
from google.adk.artifacts import InMemoryArtifactService
from google.adk.memory import InMemoryMemoryService
from google.adk.models import BaseLlm, LlmRequest, LlmResponse
from google.adk.runners import Runner

from magi_agent.adk_bridge.control_plane import build_default_plugin
from magi_agent.adk_bridge.local_toolhost import (
    LocalToolHostAdkBundle,
    is_local_fake_receipt_adk_tool,
)
from magi_agent.adk_bridge.session_service import WorkspaceSessionService
from magi_agent.harness.general_automation.live_gate import (
    GeneralAutomationReceiptLedgerStore,
)
from magi_agent.harness.general_automation.task_completion import (
    RequiredDeliverableEvidence,
)

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
    # Build the control plane from the same env flags as real_runner so the two
    # construction paths cannot drift. Full local profile enables first-party
    # controls by default; safe/minimal profiles keep the plane behaviorally
    # empty.
    plane_plugin = build_default_plugin(
        general_automation_receipts=GeneralAutomationReceiptLedgerStore(),
        contract_required=RequiredDeliverableEvidence(),
        agent_role="general",
    )
    # ADK 1.33 deprecates ``Runner(plugins=...)``; the supported path wraps the
    # agent and plugins in an ``App``. An App with an empty plugins list behaves
    # identically to the old no-plugin runner (no deprecation warning, no plugin
    # manager callbacks fire), so both the enabled and disabled paths use App.
    # ``App.name`` must be a valid identifier, but ``app_name`` here may contain
    # hyphens; we pass a sanitized identifier to ``App`` and let ``Runner``'s
    # ``app_name`` override preserve the caller-visible application name.
    app = App(
        name=_app_identifier(app_name),
        root_agent=agent,
        plugins=[plane_plugin],
    )
    runner = Runner(
        app=app,
        app_name=app_name,
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


def _app_identifier(app_name: str) -> str:
    """Coerce ``app_name`` into a valid Python identifier for ``App.name``.

    ``App`` validates ``name.isidentifier()`` (rejecting hyphens etc.), while the
    runner's ``app_name`` may contain hyphens. We sanitize for ``App.name`` and
    pass the original ``app_name`` to ``Runner`` to preserve the visible name.
    """
    sanitized = "".join(c if c.isalnum() or c == "_" else "_" for c in app_name)
    if not sanitized or not sanitized[0].isalpha() and sanitized[0] != "_":
        sanitized = f"_{sanitized}"
    return sanitized if sanitized.isidentifier() else "magi_agent_local"


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
