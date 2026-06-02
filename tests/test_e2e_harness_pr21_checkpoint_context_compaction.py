from __future__ import annotations

import asyncio
import importlib
import inspect
import json
import subprocess
import sys

import pytest

from openmagi_core_agent.adk_bridge.session_service import WorkspaceSessionService
from openmagi_core_agent.runtime.cache_safe_params import CacheSafeParams
from openmagi_core_agent.runtime.context_lifecycle import (
    ContextLifecycleBoundary,
    ContextLifecycleConfig,
    ContextLifecycleEvent,
    RestoreContextRequest,
)
from openmagi_core_agent.runtime.content_replacement import replace_content_with_ref
from openmagi_core_agent.runtime.query_state import QueryState


DIGEST_A = "sha256:" + "a" * 64
DIGEST_B = "sha256:" + "b" * 64


def _run(coro):
    return asyncio.run(coro)


async def _session(label: str) -> tuple[WorkspaceSessionService, object]:
    service = WorkspaceSessionService(app_name="openmagi")
    session = await service.create_session(
        app_name="openmagi",
        user_id=f"user-{label}",
        session_id=f"session-{label}",
    )
    return service, session


@pytest.mark.parametrize("label", ("coding", "research", "general_automation"))
def test_pr21_shared_generic_compaction_harness_preserves_refs_for_all_labels(
    label: str,
) -> None:
    service, session = _run(_session(label))
    cache_params = CacheSafeParams(
        modelRef=f"model-config:{label}:standard",
        runtimeConfigRef="runtime-config:local-fake",
        params={"temperature": 0.1, "maxOutputTokens": 256},
    )
    tool_projection = replace_content_with_ref(
        content_kind="tool_result",
        raw_content=f"{label} local fake result " * 100,
        ref_namespace=f"tool-result:{label}",
    )
    state = QueryState(
        currentTurnId=f"turn-{label}-current",
        sessionId=f"session-{label}",
        outstandingControlRequestRefs=(f"control:{label}:approval-1",),
        latestReadLedgerDigests=(DIGEST_B,),
        pendingToolResultRefs=(tool_projection.content_ref,),
        childAgentSummaryRefs=(f"summary:child:{label}:1",),
        childAgentEvidenceRefs=(f"evidence:child:{label}:1",),
        verificationEvidenceRefs=(f"evidence:verification:{label}:1",),
        modelContextConfigRefs=(f"model-config:{label}:standard",),
        cacheSafeParamRefs=(f"cache-params:{label}:1",),
        cacheSafeParamDigests=(cache_params.digest,),
    )
    events = tuple(
        ContextLifecycleEvent(
            eventRef=f"event:{label}:{index}",
            tokenEstimate=200,
            contentRef=f"content:{label}:{index}",
        )
        for index in range(4)
    )
    boundary = ContextLifecycleBoundary()

    compacted = _run(
        boundary.compact_if_needed(
            session_service=service,
            session=session,
            state=state,
            events=events,
            approvedSummaryRef=f"summary:compact:{label}:1",
            approvedSummaryDigest=DIGEST_A,
            config=ContextLifecycleConfig(
                enabled=True,
                localFakeCompactionEnabled=True,
                tokenEstimateThreshold=300,
                eventCountThreshold=2,
                recentEventCount=2,
            ),
        )
    )
    restored = _run(
        boundary.restore_context(
            session_service=service,
            session=session,
            request=RestoreContextRequest(
                state=compacted.state,
                approvedSummaryRef=f"summary:compact:{label}:1",
                approvedSummaryDigest=DIGEST_A,
                recentEventRefs=compacted.state.recent_event_refs,
            ),
        )
    )

    assert compacted.status == "compacted"
    assert restored.status == "restored"
    assert restored.context_refs[:3] == (
        f"summary:compact:{label}:1",
        f"event:{label}:2",
        f"event:{label}:3",
    )
    assert restored.state.outstanding_control_request_refs == (
        f"control:{label}:approval-1",
    )
    assert restored.state.pending_tool_result_refs == (tool_projection.content_ref,)
    assert restored.state.child_agent_evidence_refs == (f"evidence:child:{label}:1",)
    assert restored.state.cache_safe_param_digests == (cache_params.digest,)
    assert set(restored.authority_flags.model_dump(by_alias=True).values()) == {False}
    encoded = json.dumps(restored.public_projection(), sort_keys=True)
    assert "local fake result" not in encoded
    assert "raw" not in encoded.lower()


def test_pr21_core_contains_no_domain_specific_policy_branches() -> None:
    source = "\n".join(
        inspect.getsource(importlib.import_module(module))
        for module in (
            "openmagi_core_agent.runtime.query_state",
            "openmagi_core_agent.runtime.context_lifecycle",
            "openmagi_core_agent.runtime.content_replacement",
            "openmagi_core_agent.runtime.cache_safe_params",
        )
    )

    assert "if label" not in source
    assert "if domain" not in source
    assert "coding" not in source
    assert "research" not in source
    assert "general_automation" not in source


def test_pr21_import_boundary_avoids_eager_live_runner_tool_memory_and_transport_modules() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import importlib
import json
import sys

before = set(sys.modules)
for name in (
    "openmagi_core_agent.runtime.query_state",
    "openmagi_core_agent.runtime.context_lifecycle",
    "openmagi_core_agent.runtime.content_replacement",
    "openmagi_core_agent.runtime.cache_safe_params",
):
    importlib.import_module(name)
after = set(sys.modules) - before
forbidden = [
    "google.adk.runners",
    "openmagi_core_agent.tools",
    "openmagi_core_agent.memory",
    "openmagi_core_agent.transport",
    "openmagi_core_agent.routing",
    "openmagi_core_agent.main",
    "kubernetes",
    "supabase",
]
print(json.dumps(sorted(name for name in after if any(name == prefix or name.startswith(prefix + ".") for prefix in forbidden))))
""",
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    loaded = json.loads(completed.stdout)
    assert loaded == []
