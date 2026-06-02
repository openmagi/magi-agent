from __future__ import annotations

from magi_agent.config.models import (
    PythonMemoryAdapterConfig,
    PythonRuntimeAuthorityConfig,
    PythonToolHostAttachmentConfig,
)
from magi_agent.harness.mission_runtime_boundary import MissionChildTaskIntent
from magi_agent.memory.policy import MemoryPolicy, MemoryPolicyDecision
from magi_agent.memory.projection import TurnMemorySummaryProjection
from magi_agent.memory.write_boundary import (
    MemoryBackendCapabilities,
    MemoryBackendDescriptor,
)
from magi_agent.workspace.adoption_boundary import WorkspaceMutationConfig


DIGEST = "sha256:" + "a" * 64


def _dumped_false_values(model: object, keys: tuple[str, ...]) -> tuple[bool, ...]:
    dumped = model.model_dump(by_alias=True)  # type: ignore[attr-defined]
    return tuple(dumped[key] for key in keys)


def test_runtime_config_false_only_fields_cannot_be_constructed_or_copied_true() -> None:
    memory = PythonMemoryAdapterConfig.model_construct(
        adapter="agentmemory_readonly",
        mode="readonly_local",
        enabled=True,
        promptProjectionEnabled=True,
        liveProviderCallsEnabled=True,
        adkMemoryServiceAttachmentEnabled=True,
    )
    toolhost = PythonToolHostAttachmentConfig.model_construct(
        enabled=True,
        mode="shadow_readonly",
        productionAttachmentEnabled=True,
        liveToolMutationEnabled=True,
    )
    authority = PythonRuntimeAuthorityConfig.model_construct(
        userVisibleOutputAllowed=True,
        canaryRoutingAllowed=True,
        transcriptWriteAllowed=True,
        sseWriteAllowed=True,
        channelWriteAllowed=True,
        dbWriteAllowed=True,
        workspaceMutationAllowed=True,
        childExecutionAllowed=True,
        missionRuntimeAllowed=True,
        evidenceBlockModeAllowed=True,
    )

    assert _dumped_false_values(
        memory,
        (
            "promptProjectionEnabled",
            "liveProviderCallsEnabled",
            "adkMemoryServiceAttachmentEnabled",
        ),
    ) == (False, False, False)
    assert _dumped_false_values(
        toolhost,
        ("productionAttachmentEnabled", "liveToolMutationEnabled"),
    ) == (False, False)
    assert _dumped_false_values(
        authority.model_copy(update={"missionRuntimeAllowed": True}),
        (
            "userVisibleOutputAllowed",
            "canaryRoutingAllowed",
            "transcriptWriteAllowed",
            "sseWriteAllowed",
            "channelWriteAllowed",
            "dbWriteAllowed",
            "workspaceMutationAllowed",
            "childExecutionAllowed",
            "missionRuntimeAllowed",
            "evidenceBlockModeAllowed",
        ),
    ) == (False, False, False, False, False, False, False, False, False, False)


def test_memory_boundary_false_only_fields_cannot_be_constructed_or_copied_true() -> None:
    policy = MemoryPolicy.model_construct(
        memoryMode="normal",
        sourceAuthority="long_term_allowed",
        promptProjectionEnabled=True,
        writesEnabled=True,
    )
    decision = MemoryPolicyDecision.model_construct(
        recallAllowed=True,
        writeAllowed=True,
        promptProjectionAllowed=True,
        publicProjectionAllowed=True,
        reasonCodes=(),
    )
    projection = TurnMemorySummaryProjection.model_construct(
        memoryWritesEnabled=True,
        productionWritesEnabled=True,
        turnDigest=DIGEST,
        eventDigest=DIGEST,
        transcriptDigest=DIGEST,
        eventTypes=("text_delta",),
    )
    backend = MemoryBackendDescriptor.model_construct(
        providerId="provider:agentmemory",
        kind="agent_memory",
        displayName="AgentMemory",
        optionalCandidate=True,
        enabled=True,
        providerCallsEnabled=True,
        providerSdkImportAllowed=True,
        memoryWriteAllowed=True,
        productionWriteEnabled=True,
        capabilities=MemoryBackendCapabilities(supportsSearch=True),
        activationBlockers=("provider disabled",),
    )

    assert _dumped_false_values(policy, ("promptProjectionEnabled", "writesEnabled")) == (
        False,
        False,
    )
    assert _dumped_false_values(
        decision.copy(update={"writeAllowed": True}),
        ("writeAllowed", "promptProjectionAllowed"),
    ) == (False, False)
    assert _dumped_false_values(
        projection.model_copy(update={"memoryWritesEnabled": True}),
        ("memoryWritesEnabled", "productionWritesEnabled"),
    ) == (False, False)
    assert _dumped_false_values(
        backend.copy(update={"providerCallsEnabled": True}),
        (
            "enabled",
            "providerCallsEnabled",
            "providerSdkImportAllowed",
            "memoryWriteAllowed",
            "productionWriteEnabled",
        ),
    ) == (False, False, False, False, False)


def test_mission_child_and_workspace_configs_cannot_construct_live_execution() -> None:
    child = MissionChildTaskIntent.model_construct(
        taskId="task:one",
        goalRef="goal:one",
        role="implementer",
        promptPreview="summarize only",
        executionAllowed=True,
    )
    workspace = WorkspaceMutationConfig.model_construct(
        enabled=True,
        localFakeApplyEnabled=True,
        productionWorkspaceMutationEnabled=True,
        productionWritesEnabled=True,
    )

    assert child.model_dump(by_alias=True)["executionAllowed"] is False
    assert _dumped_false_values(
        workspace.copy(update={"productionWritesEnabled": True}),
        ("productionWorkspaceMutationEnabled", "productionWritesEnabled"),
    ) == (False, False)
