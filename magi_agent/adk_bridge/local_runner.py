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

from magi_agent.adk_bridge.edit_retry_reflection import (
    build_edit_retry_reflection_plugin,
)
from magi_agent.adk_bridge.local_toolhost import (
    LocalToolHostAdkBundle,
    is_local_fake_receipt_adk_tool,
)
from magi_agent.adk_bridge.resilience_plugin import build_resilience_plugin
from magi_agent.adk_bridge.session_service import WorkspaceSessionService
from magi_agent.config.env import (
    parse_edit_retry_reflection_env,
    parse_error_recovery_env,
    parse_loop_guard_env,
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
    # Flag-gated edit-failure reflection: when enabled, attach the shared
    # RetryController-backed plugin so a failed FileEdit re-injects a corrective
    # hidden message into the next model turn (fail-closed at max_attempts).
    edit_retry_env = parse_edit_retry_reflection_env(os.environ)
    edit_retry_plugin = build_edit_retry_reflection_plugin(
        enabled=edit_retry_env.enabled,
        max_attempts=edit_retry_env.max_attempts,
    )
    # PR12: flag-gated loop guard + multi-strategy error recovery. The common
    # MagiResiliencePlugin shim activates the existing ToolCallLoopDetector
    # (after_tool) and RecoveryEngine (on_model_error). Returns None when both
    # MAGI_LOOP_GUARD_ENABLED and MAGI_ERROR_RECOVERY_ENABLED are OFF, so the
    # disabled path attaches no resilience callbacks (zero regression).
    loop_guard_env = parse_loop_guard_env(os.environ)
    error_recovery_env = parse_error_recovery_env(os.environ)
    resilience_plugin = build_resilience_plugin(
        loop_guard_enabled=loop_guard_env.enabled,
        loop_guard_soft_threshold=loop_guard_env.soft_threshold,
        loop_guard_hard_threshold=loop_guard_env.hard_threshold,
        loop_guard_frequency_soft_threshold=loop_guard_env.frequency_soft_threshold,
        loop_guard_frequency_hard_threshold=loop_guard_env.frequency_hard_threshold,
        error_recovery_enabled=error_recovery_env.enabled,
        recovery_max_attempts=error_recovery_env.max_recovery_attempts,
    )
    runner_plugins = [
        plugin
        for plugin in (edit_retry_plugin, resilience_plugin)
        if plugin is not None
    ]
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
        plugins=runner_plugins,
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
