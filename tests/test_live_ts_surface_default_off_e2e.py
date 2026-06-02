from __future__ import annotations

import json
from pathlib import Path

from openmagi_core_agent.channels.contract import ChannelRef
from openmagi_core_agent.recipes.compiler import (
    AgentRecipeCompiler,
    PackRegistry,
    ProfileResolutionRequest,
)
from openmagi_core_agent.recipes.materializer import RecipeMaterializer
from openmagi_core_agent.runtime.request_shape import RequestShapeLedger


FIXTURE = Path(__file__).parent / "fixtures" / "live_ts_surface_parity" / "full_surface_matrix.json"


class _FakeProvider:
    openmagi_local_fake_provider = True

    def __init__(self) -> None:
        self.calls: list[object] = []

    def execute(self, request: object) -> dict[str, object]:
        self.calls.append(request)
        return {"status": "sent", "providerMessageId": "msg-1"}

    def search(self, request: object) -> dict[str, object]:
        self.calls.append(request)
        return {"results": []}

    def open(self, request: object) -> dict[str, object]:
        self.calls.append(request)
        return {"visibleText": "safe"}

    def deliver(self, request: object) -> object:
        self.calls.append(request)
        return {"status": "sent"}

    def save_task(self, task: object) -> object:
        self.calls.append(task)
        return task

    def get_task(self, task_id: str) -> None:
        self.calls.append(task_id)
        return None

    def list_tasks(self) -> tuple[object, ...]:
        self.calls.append("list")
        return ()


def _materialize(row: dict[str, object]) -> object:
    snapshot = AgentRecipeCompiler(PackRegistry.with_first_party_packs()).compile(
        ProfileResolutionRequest(
            taskProfile=row.get("taskProfile", {}),
            runtimeContext=row.get("runtimeContext", {}),
        )
    )
    return RecipeMaterializer.with_reliability_defaults().materialize(
        snapshot,
        modelProvider="google",
        modelLabel="gemini-3.5-flash",
    )


def _exercise_default_off_boundary(boundary: str) -> tuple[object, _FakeProvider]:
    provider = _FakeProvider()
    if boundary == "web_acquisition":
        from openmagi_core_agent.web_acquisition.live_provider_pack import (
            WebAcquisitionProviderPack,
            WebAcquisitionProviderPackConfig,
            WebAcquisitionProviderRequest,
        )

        result = WebAcquisitionProviderPack(WebAcquisitionProviderPackConfig()).run(
            WebAcquisitionProviderRequest(
                operation="search",
                requestId="e2e-web-1",
                providerName="fake-web",
                botIdDigest="bot:abc",
                ownerIdDigest="owner:def",
                sessionKeyDigest="session:ghi",
                query="current docs",
            ),
            provider=provider,
        )
        return result, provider
    if boundary == "browser":
        from openmagi_core_agent.browser.live_provider_pack import (
            BrowserProviderPack,
            BrowserProviderPackConfig,
            BrowserProviderPackRequest,
        )

        result = BrowserProviderPack(BrowserProviderPackConfig()).run(
            BrowserProviderPackRequest(
                action="browser.open",
                requestId="e2e-browser-1",
                providerName="fake-browser",
                botIdDigest="bot:abc",
                ownerIdDigest="owner:def",
                sessionKeyDigest="session:ghi",
                url="https://docs.example.com",
            ),
            provider=provider,
        )
        return result, provider
    if boundary == "file_delivery":
        from openmagi_core_agent.artifacts.file_delivery import (
            FileDeliveryBoundary,
            FileDeliveryConfig,
            FileDeliveryRequest,
        )

        result = FileDeliveryBoundary(FileDeliveryConfig()).execute(
            FileDeliveryRequest(
                operation="file.deliver",
                requestId="e2e-file-1",
                sessionKey="session:ghi",
                channel=ChannelRef(type="telegram", channelId="chat-1"),
                artifactRefs=("artifact:e2e",),
                filename="report.md",
                mimeType="text/markdown",
                contentDigest="sha256:" + "0" * 64,
            ),
            artifact_provider=provider,
            channel_provider=provider,
        )
        return result, provider
    if boundary == "scheduler":
        from openmagi_core_agent.harness.scheduler_runtime import (
            SchedulerRuntimeBoundary,
            SchedulerRuntimeConfig,
            SchedulerTickRequest,
        )

        result = SchedulerRuntimeBoundary(SchedulerRuntimeConfig()).tick(
            SchedulerTickRequest(
                requestId="e2e-scheduler-1",
                now=1_000,
                ownerDigest="owner:abc",
                dueRefs=("cron:reminder",),
            )
        )
        return result, provider
    if boundary == "background_task":
        from openmagi_core_agent.harness.background_tasks import (
            BackgroundTaskBoundary,
            BackgroundTaskConfig,
            BackgroundTaskRequest,
        )

        result = BackgroundTaskBoundary(BackgroundTaskConfig()).execute(
            BackgroundTaskRequest(
                operation="TaskCreate",
                requestId="e2e-task-1",
                ownerDigest="owner:abc",
                taskId="task:e2e",
                promptPreview="safe long running goal",
                channel=ChannelRef(type="web", channelId="web-session"),
            ),
            store=provider,
        )
        return result, provider
    if boundary == "channel_dispatch":
        from openmagi_core_agent.channels.dispatcher import (
            ChannelDispatchConfig,
            ChannelDispatchRequest,
            ChannelDispatcher,
        )

        result = ChannelDispatcher(ChannelDispatchConfig()).dispatch(
            ChannelDispatchRequest(
                operation="dispatch.message",
                requestId="e2e-discord-1",
                channel=ChannelRef(type="discord", channelId="discord-channel"),
                providerName="discord-provider",
                botIdDigest="bot:abc",
                userIdDigest="user:def",
                sessionKeyDigest="session:ghi",
                text="safe reply",
            ),
            provider=provider,
        )
        return result, provider
    raise AssertionError(f"unknown boundary fixture: {boundary}")


def test_live_ts_surface_default_off_matrix_is_packaged() -> None:
    fixture = json.loads(FIXTURE.read_text())

    assert fixture["fixtureId"] == "live_ts_surface_default_off_matrix_0001"
    assert tuple(row["caseId"] for row in fixture["rows"]) == (
        "research_citations_web_search",
        "browse_website_summarize",
        "send_generated_file_to_telegram",
        "scheduled_reminder",
        "long_running_goal_subtask_notification",
        "coding_test_run_file_delivery",
        "discord_reply_with_attachment",
    )


def test_live_ts_surface_default_off_e2e_matrix() -> None:
    fixture = json.loads(FIXTURE.read_text())

    for row in fixture["rows"]:
        plan = _materialize(row)
        result, provider = _exercise_default_off_boundary(row["boundary"])
        projection = result.public_projection() if hasattr(result, "public_projection") else result.model_dump(by_alias=True)
        ledger_record = RequestShapeLedger().record_model_phase(
            turnId=f"turn:{row['caseId']}",
            phase="final_verification",
            provider="google",
            model="gemini-3.5-flash",
            modelTier="cheap",
            recipeSnapshotId=plan.recipe_snapshot_id,
            inputRefs=("summary:e2e", "/workspace/private/raw.txt"),
            evidenceRefs=tuple(plan.evidence_requirements[:3]) + ("Bearer unsafe",),
            rawInput="Authorization: Bearer live-token /Users/kevin/private",
            validatorRefs=tuple(plan.final_gate_policy.required_validators[:3]),
        )
        encoded = json.dumps(
            {
                "case": row["caseId"],
                "plan": plan.model_dump(by_alias=True),
                "projection": projection,
                "requestShape": ledger_record.public_projection(),
            },
            sort_keys=True,
            default=str,
        )

        for pack_id in row.get("expectedPacks", ()):
            assert pack_id in plan.selected_pack_ids
        for intent in row.get("expectedProviderIntents", ()):
            assert intent in plan.provider_intents
        for intent in row.get("expectedToolIntents", ()):
            assert intent in plan.tool_intents
        for intent in row.get("expectedChannelIntents", ()):
            assert intent in plan.channel_intents
        for intent in row.get("expectedArtifactIntents", ()):
            assert intent in plan.artifact_intents
        for intent in row.get("expectedSchedulerIntents", ()):
            assert intent in plan.scheduler_intents
        for approval in row.get("expectedApprovals", ()):
            assert approval in plan.approval_gates

        assert provider.calls == []
        assert set(plan.attachment_flags.values()) == {False}
        assert plan.live_attachment_refs == ()
        assert "authorityFlags" in projection
        assert set(projection["authorityFlags"].values()) == {False}
        assert "GATEWAY_TOKEN" not in encoded
        assert "FIRECRAWL_API_KEY" not in encoded
        assert "Bearer live-token" not in encoded
        assert "Bearer unsafe" not in encoded
        assert "/Users/kevin" not in encoded
        assert "/workspace/private" not in encoded
        assert "raw_browser_snapshot" not in encoded
        assert "adkRunnerInvoked\": true" not in encoded
        assert "providerCalled\": true" not in encoded
        assert "routeAttached\": true" not in encoded
