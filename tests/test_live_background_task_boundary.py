from __future__ import annotations

import json
import subprocess
import sys

import pytest

from magi_agent.channels.contract import ChannelRef


class FakeTaskStore:
    openmagi_local_fake_provider = True

    def __init__(self) -> None:
        self.records: dict[str, object] = {}
        self.cancelled: list[str] = []

    def save_task(self, task: object) -> object:
        self.records[getattr(task, "task_id")] = task
        return task

    def get_task(self, task_id: str) -> object | None:
        return self.records.get(task_id)

    def list_tasks(self) -> tuple[object, ...]:
        return tuple(self.records.values())

    def stop_task(self, task_id: str, reason: str | None = None) -> bool:
        _ = reason
        self.cancelled.append(task_id)
        task = self.records.get(task_id)
        if task is None or getattr(task, "status") != "running":
            return False
        self.records[task_id] = task.model_copy(update={"status": "aborted", "updated_at": 200})
        return True


class ThrowingTaskStore(FakeTaskStore):
    def __init__(self, throwing_method: str) -> None:
        super().__init__()
        self.throwing_method = throwing_method

    def _raise_if_throwing(self, method: str) -> None:
        if self.throwing_method == method:
            raise RuntimeError("fake store leaked /Users/kevin/.openmagi Bearer sk-test-private-token")

    def save_task(self, task: object) -> object:
        self._raise_if_throwing("save_task")
        return super().save_task(task)

    def get_task(self, task_id: str) -> object | None:
        self._raise_if_throwing("get_task")
        return super().get_task(task_id)

    def list_tasks(self) -> tuple[object, ...]:
        self._raise_if_throwing("list_tasks")
        return super().list_tasks()

    def stop_task(self, task_id: str, reason: str | None = None) -> bool:
        self._raise_if_throwing("stop_task")
        return super().stop_task(task_id, reason)


def _request(**overrides: object) -> object:
    from magi_agent.harness.background_tasks import BackgroundTaskRequest

    payload = {
        "operation": "TaskList",
        "requestId": "task-req",
        "ownerDigest": "owner:abc",
    }
    payload.update(overrides)
    return BackgroundTaskRequest(**payload)


def _seed_task(store: FakeTaskStore) -> None:
    from magi_agent.harness.background_tasks import BackgroundTaskRecord

    store.records["task-throw"] = BackgroundTaskRecord(
        taskId="task-throw",
        ownerDigest="owner:abc",
        status="running",
        promptPreview="public",
        cancelTokenRef="cancel-token:throw",
        idempotencyDigest="task:throw",
        createdAt=100,
        updatedAt=100,
    )


def test_background_task_disabled_never_calls_store() -> None:
    from magi_agent.harness.background_tasks import (
        BackgroundTaskBoundary,
        BackgroundTaskConfig,
    )

    store = FakeTaskStore()
    decision = BackgroundTaskBoundary(BackgroundTaskConfig()).execute(
        _request(operation="TaskList"),
        store=store,
    )

    assert decision.status == "disabled"
    assert store.records == {}
    assert decision.reason_codes == ("background_task_runtime_disabled",)
    assert set(decision.authority_flags.model_dump(by_alias=True).values()) == {False}


def test_background_task_create_records_metadata_cancel_token_and_digest() -> None:
    from magi_agent.harness.background_tasks import (
        BackgroundTaskBoundary,
        BackgroundTaskConfig,
    )

    store = FakeTaskStore()
    decision = BackgroundTaskBoundary(
        BackgroundTaskConfig(enabled=True, localFakeTaskStoreEnabled=True),
    ).execute(
        _request(
            operation="TaskCreate",
            taskId="task-1",
            parentTurnId="turn-1",
            sessionKeyDigest="session:abc",
            channel=ChannelRef(type="web", channelId="web-session"),
            promptPreview="run tests",
            progress=("queued",),
            outputRefs=("artifact:plan",),
        ),
        store=store,
    )

    assert decision.status == "recorded_local_fake"
    assert decision.task is not None
    assert decision.task.task_id == "task-1"
    assert decision.task.cancel_token_ref.startswith("cancel-token:")
    assert decision.task.idempotency_digest.startswith("task:")
    assert decision.task.output_refs == ("artifact:plan",)
    assert decision.public_projection()["authorityFlags"]["backgroundTaskStarted"] is False


def test_background_task_config_and_authority_flags_cannot_be_forged_with_model_copy() -> None:
    from magi_agent.harness.background_tasks import (
        BackgroundTaskAuthorityFlags,
        BackgroundTaskConfig,
    )

    config = BackgroundTaskConfig().model_copy(
        update={
            "backgroundTaskRunnerAttached": True,
            "background_task_runner_attached": True,
            "productionWritesEnabled": True,
            "production_writes_enabled": True,
            "routeAttached": True,
            "route_attached": True,
        }
    )
    flags = BackgroundTaskAuthorityFlags().model_copy(
        update={
            "backgroundTaskStarted": True,
            "background_task_started": True,
            "realChildRunnerInvoked": True,
            "real_child_runner_invoked": True,
            "productionWritesEnabled": True,
            "production_writes_enabled": True,
            "routeAttached": True,
            "route_attached": True,
        }
    )

    assert config.background_task_runner_attached is False
    assert config.production_writes_enabled is False
    assert config.route_attached is False
    assert set(flags.model_dump(by_alias=True).values()) == {False}


def test_task_list_get_wait_output_stop_match_ts_compatible_shapes() -> None:
    from magi_agent.harness.background_tasks import (
        BackgroundTaskBoundary,
        BackgroundTaskConfig,
    )

    store = FakeTaskStore()
    boundary = BackgroundTaskBoundary(
        BackgroundTaskConfig(enabled=True, localFakeTaskStoreEnabled=True),
    )
    boundary.execute(
        _request(operation="TaskCreate", taskId="task-a", promptPreview="first", now=100),
        store=store,
    )
    boundary.execute(
        _request(
            operation="TaskCreate",
            taskId="task-b",
            promptPreview="second",
            sessionKeyDigest="session:abc",
            now=101,
        ),
        store=store,
    )
    got = boundary.execute(_request(operation="TaskGet", taskId="task-a"), store=store)
    listed = boundary.execute(_request(operation="TaskList", sessionKeyDigest="session:abc"), store=store)
    waited = boundary.execute(_request(operation="TaskWait", taskIds=("task-a", "task-b"), waitTimeoutMs=5), store=store)
    output = boundary.execute(_request(operation="TaskOutput", taskId="task-a"), store=store)
    stopped = boundary.execute(_request(operation="TaskStop", taskId="task-a", stopReason="user asked"), store=store)

    assert got.public_projection()["task"]["taskId"].startswith("task:")
    assert tuple(task.task_id for task in listed.tasks) == ("task-b",)
    assert waited.public_projection()["timedOut"] is True
    assert len(waited.public_projection()["results"]) == 2
    assert output.public_projection()["output"]["status"] == "running"
    assert stopped.public_projection()["stopped"] is True
    assert stopped.task is not None and stopped.task.status == "aborted"


def test_background_task_reads_and_stops_are_owner_scoped() -> None:
    from magi_agent.harness.background_tasks import (
        BackgroundTaskBoundary,
        BackgroundTaskConfig,
    )

    store = FakeTaskStore()
    boundary = BackgroundTaskBoundary(
        BackgroundTaskConfig(enabled=True, localFakeTaskStoreEnabled=True),
    )
    boundary.execute(
        _request(operation="TaskCreate", taskId="task-owned", promptPreview="owned"),
        store=store,
    )
    boundary.execute(
        _request(
            operation="TaskCreate",
            requestId="task_req_other",
            taskId="task-other",
            ownerDigest="owner:other",
            promptPreview="other",
        ),
        store=store,
    )

    listed = boundary.execute(_request(operation="TaskList"), store=store)
    waited = boundary.execute(
        _request(operation="TaskWait", taskIds=("task-owned", "task-other")),
        store=store,
    )
    get_other = boundary.execute(_request(operation="TaskGet", taskId="task-other"), store=store)
    output_other = boundary.execute(_request(operation="TaskOutput", taskId="task-other"), store=store)
    stop_other = boundary.execute(_request(operation="TaskStop", taskId="task-other"), store=store)

    assert tuple(task.task_id for task in listed.tasks) == ("task-owned",)
    assert tuple(task.task_id for task in waited.tasks) == ("task-owned",)
    assert get_other.status == "blocked"
    assert get_other.reason_codes == ("task_not_found_or_not_owned",)
    assert output_other.status == "blocked"
    assert output_other.reason_codes == ("task_not_found_or_not_owned",)
    assert stop_other.status == "blocked"
    assert stop_other.reason_codes == ("task_not_found_or_not_owned",)
    assert store.cancelled == []
    assert getattr(store.get_task("task-other"), "status") == "running"


@pytest.mark.parametrize(
    ("operation", "throwing_method", "overrides"),
    [
        ("TaskCreate", "save_task", {"taskId": "task-throw", "promptPreview": "public"}),
        ("TaskList", "list_tasks", {}),
        ("TaskGet", "get_task", {"taskId": "task-throw"}),
        ("TaskWait", "get_task", {"taskIds": ("task-throw",)}),
        ("TaskOutput", "get_task", {"taskId": "task-throw"}),
        ("TaskStop", "stop_task", {"taskId": "task-throw", "stopReason": "user asked"}),
    ],
)
def test_background_task_store_errors_fail_closed_with_redacted_metadata(
    operation: str,
    throwing_method: str,
    overrides: dict[str, object],
) -> None:
    from magi_agent.harness.background_tasks import (
        BackgroundTaskBoundary,
        BackgroundTaskConfig,
    )

    store = ThrowingTaskStore(throwing_method)
    if operation != "TaskCreate":
        _seed_task(store)

    decision = BackgroundTaskBoundary(
        BackgroundTaskConfig(enabled=True, localFakeTaskStoreEnabled=True),
    ).execute(
        _request(operation=operation, **overrides),
        store=store,
    )
    projection = decision.public_projection()
    rendered = json.dumps(projection, sort_keys=True)

    assert decision.status == "blocked"
    assert decision.reason_codes == ("local_fake_task_store_error",)
    assert decision.task is None
    assert decision.tasks == ()
    assert decision.results == ()
    assert decision.output is None
    assert projection["diagnosticMetadata"]["storeErrorCode"] == "fake_store_error"
    assert projection["authorityFlags"]["backgroundTaskStarted"] is False
    assert "/Users/kevin" not in rendered
    assert "Bearer" not in rendered
    assert "sk-test-private-token" not in rendered
    assert "providerError" not in rendered


def test_background_task_rejects_untrusted_store_and_redacts_prompt_output() -> None:
    from magi_agent.harness.background_tasks import (
        BackgroundTaskBoundary,
        BackgroundTaskConfig,
    )

    class UntrustedStore(FakeTaskStore):
        openmagi_local_fake_provider = False

    blocked = BackgroundTaskBoundary(
        BackgroundTaskConfig(enabled=True, localFakeTaskStoreEnabled=True),
    ).execute(
        _request(operation="TaskCreate", taskId="task-x", promptPreview="run"),
        store=UntrustedStore(),
    )
    assert blocked.status == "blocked"
    assert blocked.reason_codes == ("local_fake_task_store_untrusted",)

    store = FakeTaskStore()
    decision = BackgroundTaskBoundary(
        BackgroundTaskConfig(enabled=True, localFakeTaskStoreEnabled=True),
    ).execute(
        _request(
            operation="TaskCreate",
            taskId="task-secret",
            promptPreview="raw transcript /Users/kevin/private ghp_taskSecret",
            outputRefs=("artifact:ok",),
        ),
        store=store,
    )
    rendered = str(decision.public_projection())
    assert "/Users/kevin" not in rendered
    assert "ghp_taskSecret" not in rendered
    assert "raw transcript" not in rendered


def test_background_task_diagnostic_metadata_cannot_forge_authority_claims() -> None:
    from magi_agent.harness.background_tasks import (
        BackgroundTaskBoundary,
        BackgroundTaskConfig,
    )

    decision = BackgroundTaskBoundary(BackgroundTaskConfig()).execute(
        _request(
            operation="TaskList",
            metadata={
                "enabled": True,
                "backgroundTaskRunnerAttached": True,
                "productionWritesEnabled": True,
                "routeAttached": True,
                "authorityFlags": "fake",
                "safeNote": "public",
            },
        ),
        store=FakeTaskStore(),
    )
    projection = decision.public_projection()
    rendered = json.dumps(projection["diagnosticMetadata"], sort_keys=True)

    assert projection["authorityFlags"]["backgroundTaskStarted"] is False
    assert projection["authorityFlags"]["productionWritesEnabled"] is False
    assert projection["diagnosticMetadata"] == {"safeNote": "public"}
    assert "enabled" not in rendered
    assert "Attached" not in rendered
    assert "authority" not in rendered


@pytest.mark.xfail(
    strict=False,
    reason=(
        "Subprocess-based import-boundary probe flakes on some hosts where the "
        "interpreter eagerly loads socket/subprocess/urllib at startup. Tracked "
        "in openmagi/magi-agent CI-baseline quarantine; do not fix in the CI "
        "bootstrap PR."
    ),
)
def test_background_task_boundary_has_no_live_imports() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import importlib
import sys

importlib.import_module("magi_agent.harness.background_tasks")
forbidden_prefixes = (
    "google.adk",
    "google.genai",
    "magi_agent.adk_bridge",
    "magi_agent.transport",
    "magi_agent.routing",
    "magi_agent.deploy",
    "magi_agent.chat_proxy",
    "magi_agent.runtime_selector",
    "magi_agent.k8s",
    "subprocess",
    "kubernetes",
    "telegram",
    "discord",
    "requests",
    "httpx",
    "aiohttp",
    "socket",
    "urllib",
    "playwright",
    "selenium",
)
loaded = [
    name
    for name in sys.modules
    if any(name == prefix or name.startswith(f"{prefix}.") for prefix in forbidden_prefixes)
]
if loaded:
    raise AssertionError(f"forbidden modules loaded: {loaded}")
""",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
