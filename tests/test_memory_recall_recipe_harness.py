from __future__ import annotations

import asyncio
import ast
import json
import subprocess
import sys
from pathlib import Path

from magi_agent.memory.contracts import MemoryRecord, RecallRequest, RecallResult
from magi_agent.memory.namespaces import MemoryNamespacePolicy


NAMESPACE_A = "memory-ns:tenant-a.bot-a"
NAMESPACE_B = "memory-ns:tenant-b.bot-b"
DUMMY_SESSION_KEY_SUFFIX = "abc" + "123456789"


class FakeMemoryRecallAdapter:
    openmagi_local_fake_provider = True

    def __init__(
        self,
        *records: MemoryRecord,
        reason_codes: tuple[str, ...] = ("local_fake_memory_fixture",),
    ) -> None:
        self.records = records
        self.reason_codes = reason_codes
        self.calls = 0

    async def recall(self, request: RecallRequest, *, policy: object) -> RecallResult:
        self.calls += 1
        assert policy is not None
        return RecallResult(
            providerId="local-fake-memory",
            records=self.records,
            recallAllowed=True,
            writeAllowed=False,
            promptProjectionAllowed=False,
            publicProjectionAllowed=True,
            reasonCodes=self.reason_codes,
        )


def _request(query: str = "How should we continue the launch plan?") -> RecallRequest:
    return RecallRequest(
        scope={"tenantId": "tenant-a", "botId": "bot-a", "sessionKey": "session-a"},
        query=query,
        purpose="answer_user",
    )


def _record(
    record_id: str,
    *,
    namespace: str = NAMESPACE_A,
    visibility: str = "public-safe",
    body: str = "Launch plan: keep memory recall read-only and require receipts.",
    source_ref: str | None = None,
    metadata: dict[str, object] | None = None,
) -> MemoryRecord:
    return MemoryRecord(
        id=record_id,
        scope="bot",
        kind="note",
        body=body,
        sourceRef=source_ref or f"memory:fixture.{record_id}",
        providerId="local-fake-memory",
        confidence="observed",
        visibility=visibility,
        score=0.95,
        customMetadata={"namespaceRef": namespace, **(metadata or {})},
    )


def _projection_policy(latest_user_text: str = "continue the launch plan") -> object:
    from magi_agent.recipes.first_party.memory_recall import (
        MemoryRecallProjectionPolicy,
    )

    return MemoryRecallProjectionPolicy(
        latestUserText=latest_user_text,
        maxBytes=2048,
        policySnapshotRef="policy-snapshot:memory-pr5",
    )


def _enabled_harness(adapter: object) -> object:
    from magi_agent.harness.memory_recall import (
        MemoryRecallHarness,
        MemoryRecallHarnessConfig,
    )

    return MemoryRecallHarness(
        MemoryRecallHarnessConfig(enabled=True, localFakeAdapterEnabled=True),
        adapter=adapter,
    )


def test_memory_recall_recipe_is_disabled_by_default_and_does_not_call_adapter() -> None:
    from magi_agent.harness.memory_recall import MemoryRecallHarness

    adapter = FakeMemoryRecallAdapter(_record("allowed"))
    result = asyncio.run(
        MemoryRecallHarness(adapter=adapter).recall(
            request=_request(),
            namespace_policy=MemoryNamespacePolicy(namespaceRef=NAMESPACE_A),
            projection_policy=_projection_policy(),
        )
    )

    assert adapter.calls == 0
    assert result.status == "disabled"
    assert result.projection.references == ()
    assert result.receipt.decision_counts == {"allowed": 0, "blocked": 0, "background_only": 0}
    assert result.receipt.authority_flags.live_provider_called is False
    assert result.receipt.authority_flags.prompt_projection_allowed is False
    assert result.receipt.authority_flags.memory_write_allowed is False
    assert result.receipt.authority_flags.production_write_allowed is False
    assert result.receipt.authority_flags.traffic_attached is False
    assert result.receipt.authority_flags.user_visible_output_allowed is False


def test_memory_recall_requires_explicit_namespace_and_projection_policy() -> None:
    adapter = FakeMemoryRecallAdapter(_record("allowed"))
    harness = _enabled_harness(adapter)

    missing_namespace = asyncio.run(
        harness.recall(
            request=_request(),
            namespace_policy=None,
            projection_policy=_projection_policy(),
        )
    )
    missing_projection = asyncio.run(
        harness.recall(
            request=_request(),
            namespace_policy=MemoryNamespacePolicy(namespaceRef=NAMESPACE_A),
            projection_policy=None,
        )
    )

    assert adapter.calls == 0
    assert missing_namespace.status == "blocked"
    assert missing_projection.status == "blocked"
    assert "missing_memory_namespace_policy" in missing_namespace.reason_codes
    assert "missing_memory_projection_policy" in missing_projection.reason_codes


def test_memory_recall_allowed_path_uses_injected_local_fake_and_digest_only_receipt() -> None:
    adapter = FakeMemoryRecallAdapter(_record("allowed"))
    result = asyncio.run(
        _enabled_harness(adapter).recall(
            request=_request("continue the launch plan with receipt checks"),
            namespace_policy=MemoryNamespacePolicy(namespaceRef=NAMESPACE_A),
            projection_policy=_projection_policy("continue launch plan"),
        )
    )
    projection = result.public_projection()
    rendered = json.dumps(projection, sort_keys=True)

    assert adapter.calls == 1
    assert result.status == "allowed"
    assert len(result.projection.references) == 1
    assert result.projection.prompt_projection_allowed is False
    assert result.projection.prompt_text == ""
    assert result.projection.session_injection_allowed is False
    assert result.receipt.input_digest.startswith("sha256:")
    assert result.receipt.output_digest.startswith("sha256:")
    assert result.receipt.redaction_status == "verified"
    assert result.receipt.source_authority == "long_term_allowed"
    assert result.receipt.namespace_ref == NAMESPACE_A
    assert result.receipt.decision_counts == {"allowed": 1, "blocked": 0, "background_only": 0}
    assert result.receipt.policy_snapshot_digest.startswith("sha256:")
    assert result.receipt.policy_snapshot_ref == "policy-snapshot:memory-pr5"
    assert "continue the launch plan" not in rendered
    assert "Launch plan: keep memory recall" in rendered
    assert "promptProjectionAllowed" in rendered
    assert "memoryWriteAllowed" in rendered


def test_memory_recall_blocks_private_stale_unredacted_and_unrelated_records() -> None:
    cases = (
        (_record("private", visibility="private", body="Private preference."), "private_memory_excluded"),
        (_record("shared", visibility="shared", body="Shared workspace note."), "private_memory_excluded"),
        (_record("stale", metadata={"stale": True}), "stale_memory_ref_denied"),
        (
            _record("unredacted", metadata={"redactionState": "failed"}),
            "memory_redaction_not_verified",
        ),
        (_record("unrelated", namespace=NAMESPACE_B), "memory_namespace_mismatch"),
        (_record("erased", metadata={"eraseState": "erased"}), "memory_erase_state_blocks_projection"),
        (
            _record("retention-expired", metadata={"retentionState": "expired"}),
            "memory_retention_not_active",
        ),
    )

    for record, reason in cases:
        result = asyncio.run(
            _enabled_harness(FakeMemoryRecallAdapter(record)).recall(
                request=_request(),
                namespace_policy=MemoryNamespacePolicy(namespaceRef=NAMESPACE_A),
                projection_policy=_projection_policy(),
            )
        )
        rendered = json.dumps(result.public_projection(), sort_keys=True)

        assert result.status == "blocked", reason
        assert result.projection.references == ()
        assert reason in result.reason_codes
        assert "Private preference" not in rendered
        assert "Shared workspace note" not in rendered


def test_memory_recall_blocks_mixed_safe_and_rejected_records_fail_closed() -> None:
    result = asyncio.run(
        _enabled_harness(
            FakeMemoryRecallAdapter(
                _record("allowed"),
                _record("private", visibility="private", body="Private preference."),
            )
        ).recall(
            request=_request(),
            namespace_policy=MemoryNamespacePolicy(namespaceRef=NAMESPACE_A),
            projection_policy=_projection_policy(),
        )
    )
    rendered = json.dumps(result.public_projection(), sort_keys=True)

    assert result.status == "blocked"
    assert result.projection.references == ()
    assert result.receipt.decision_counts == {
        "allowed": 1,
        "blocked": 1,
        "background_only": 0,
    }
    assert "private_memory_excluded" in result.reason_codes
    assert "Launch plan: keep memory recall" not in rendered
    assert "Private preference" not in rendered


def test_memory_recall_blocks_mixed_safe_and_projection_rejected_records() -> None:
    result = asyncio.run(
        _enabled_harness(
            FakeMemoryRecallAdapter(
                _record("allowed"),
                _record("raw-child", metadata={"childMemoryRaw": True}),
            )
        ).recall(
            request=_request(),
            namespace_policy=MemoryNamespacePolicy(namespaceRef=NAMESPACE_A),
            projection_policy=_projection_policy(),
        )
    )
    rendered = json.dumps(result.public_projection(), sort_keys=True)

    assert result.status == "blocked"
    assert result.projection.references == ()
    assert "child_raw_memory_rejected" in result.reason_codes
    assert "memory_projection_rejected_records" in result.reason_codes
    assert "Launch plan: keep memory recall" not in rendered


def test_memory_recall_blocks_source_authority_and_empty_public_projection() -> None:
    disabled = asyncio.run(
        _enabled_harness(FakeMemoryRecallAdapter(_record("allowed"))).recall(
            request=_request(),
            namespace_policy=MemoryNamespacePolicy(
                namespaceRef=NAMESPACE_A,
                sourceAuthority="long_term_disabled",
            ),
            projection_policy=_projection_policy(),
        )
    )
    child_isolated = asyncio.run(
        _enabled_harness(FakeMemoryRecallAdapter(_record("allowed"))).recall(
            request=_request(),
            namespace_policy=MemoryNamespacePolicy(
                namespaceRef=NAMESPACE_A,
                sourceAuthority="child_isolated",
            ),
            projection_policy=_projection_policy(),
        )
    )
    redact_authority = asyncio.run(
        _enabled_harness(FakeMemoryRecallAdapter(_record("allowed"))).recall(
            request=_request(),
            namespace_policy=MemoryNamespacePolicy(
                namespaceRef=NAMESPACE_A,
                sourceAuthority="memory_redact_authority",
            ),
            projection_policy=_projection_policy(),
        )
    )
    empty = asyncio.run(
        _enabled_harness(FakeMemoryRecallAdapter()).recall(
            request=_request(),
            namespace_policy=MemoryNamespacePolicy(namespaceRef=NAMESPACE_A),
            projection_policy=_projection_policy(),
        )
    )

    assert disabled.status == "blocked"
    assert "source_authority_disables_long_term_memory" in disabled.reason_codes
    assert child_isolated.status == "blocked"
    assert "child_memory_scope_isolated" in child_isolated.reason_codes
    assert redact_authority.status == "blocked"
    assert "memory_redact_authority_supersedes_provider" in redact_authority.reason_codes
    assert empty.status == "blocked"
    assert "empty_public_memory_projection" in empty.reason_codes


def test_memory_recall_receipt_and_projection_redact_raw_prompt_output_auth_secret_and_paths() -> None:
    unsafe = _record(
        "sk-live-unsafe-record",
        body=(
            "Visible safe summary.\n"
            "raw_prompt: hidden prompt must not leak\n"
            "raw_output: hidden output must not leak\n"
            "Authorization: Bearer unsafe-token\n"
            "Cookie: session=unsafe\n"
            "API_KEY=supersecret\n"
            "/Users/kevin/private/path.txt"
        ),
        source_ref="/Users/kevin/private/memory.md",
    )
    result = asyncio.run(
        _enabled_harness(FakeMemoryRecallAdapter(unsafe)).recall(
            request=_request("Authorization: Bearer request-secret"),
            namespace_policy=MemoryNamespacePolicy(namespaceRef=NAMESPACE_A),
            projection_policy=_projection_policy("/Users/kevin/private/latest prompt"),
        )
    )
    rendered = json.dumps(result.public_projection(), sort_keys=True)

    assert result.status == "allowed"
    assert "Visible safe summary" in rendered
    for forbidden in (
        "raw_prompt",
        "raw_output",
        "hidden prompt",
        "hidden output",
        "Authorization",
        "Bearer",
        "Cookie",
        "session=unsafe",
        "API_KEY",
        "supersecret",
        "/Users/kevin",
        "sk-live-unsafe-record",
        "request-secret",
        "latest prompt",
    ):
        assert forbidden not in rendered


def test_memory_recall_public_output_sanitizes_sensitive_reason_and_reference_strings() -> None:
    record = _record(
        "users_kevin_private_path_file",
        source_ref="memory:data/bots_private_path_file",
        metadata={"evidenceRef": "authorization_bearer_sk-live-12345678"},
    )
    result = asyncio.run(
        _enabled_harness(
            FakeMemoryRecallAdapter(
                record,
                reason_codes=(
                    "authorization_bearer_sk-live-12345678",
                    f"session_key_{DUMMY_SESSION_KEY_SUFFIX}",
                ),
            )
        ).recall(
            request=_request(),
            namespace_policy=MemoryNamespacePolicy(namespaceRef=NAMESPACE_A),
            projection_policy=_projection_policy(),
        )
    )
    rendered = json.dumps(result.public_projection(), sort_keys=True)
    raw_model_dump = json.dumps(
        result.model_dump(by_alias=True, mode="json"),
        sort_keys=True,
    )

    assert result.status == "allowed"
    for forbidden in (
        "authorization_bearer",
        "sk-live",
        "session_key",
        DUMMY_SESSION_KEY_SUFFIX,
        "users_kevin_private_path_file",
        "data/bots_private_path_file",
    ):
        assert forbidden not in rendered
        assert forbidden not in raw_model_dump
    assert "reason:" in rendered


def test_memory_recall_public_output_sanitizes_non_token_sensitive_reason_strings() -> None:
    result = asyncio.run(
        _enabled_harness(
            FakeMemoryRecallAdapter(
                _record("allowed"),
                reason_codes=(
                    "raw_prompt: user said do-not-leak",
                    "hidden_reasoning: chain detail",
                    "/Users/kevin/private/path.txt",
                    "/private/var/folders/leaked/path.txt",
                    "workspace_private_path_file",
                    "data_bots_private_path_file",
                    "var_lib_kubelet_private_path_file",
                    "raw_transcript_tool_log: child_prompt text",
                    "private_memory_payload: do-not-leak",
                ),
            )
        ).recall(
            request=_request(),
            namespace_policy=MemoryNamespacePolicy(namespaceRef=NAMESPACE_A),
            projection_policy=_projection_policy(),
        )
    )
    rendered = json.dumps(result.public_projection(), sort_keys=True)

    assert result.status == "allowed"
    for forbidden in (
        "raw_prompt",
        "do-not-leak",
        "hidden_reasoning",
        "chain_detail",
        "/Users",
        "/private",
        "users_kevin",
        "workspace_private_path",
        "data_bots_private_path",
        "var_lib_kubelet_private_path",
        "private_path",
        "var_folders",
        "leaked_path",
        "raw_transcript",
        "tool_log",
        "child_prompt",
        "private_memory_payload",
    ):
        assert forbidden not in rendered
    assert rendered.count("reason:") >= 4


def test_memory_recall_authority_flags_are_forge_hardened() -> None:
    from magi_agent.recipes.first_party.memory_recall import (
        MemoryRecallAuthorityFlags,
    )

    flags = MemoryRecallAuthorityFlags.model_validate(
        {
            "localAdapterCalled": True,
            "liveProviderCalled": True,
            "adkRunnerCalled": True,
            "adkMemoryServiceCalled": True,
            "modelCalled": True,
            "networkCalled": True,
            "promptProjectionAllowed": True,
            "memoryWriteAllowed": True,
            "productionWriteAllowed": True,
            "trafficAttached": True,
            "userVisibleOutputAllowed": True,
        }
    )

    assert flags.local_adapter_called is True
    assert flags.live_provider_called is False
    assert flags.adk_runner_called is False
    assert flags.adk_memory_service_called is False
    assert flags.model_called is False
    assert flags.network_called is False
    assert flags.prompt_projection_allowed is False
    assert flags.memory_write_allowed is False
    assert flags.production_write_allowed is False
    assert flags.traffic_attached is False
    assert flags.user_visible_output_allowed is False


def test_memory_recall_import_boundary_has_no_live_adk_model_provider_or_network_imports() -> None:
    python_root = Path(__file__).resolve().parents[1]
    module_paths = [
        python_root / "magi_agent/recipes/first_party/memory_recall.py",
        python_root / "magi_agent/harness/memory_recall.py",
    ]
    banned_roots = {
        "google",
        "openai",
        "anthropic",
        "httpx",
        "requests",
        "urllib",
        "socket",
        "supabase",
        "psycopg",
        "asyncpg",
    }

    for module_path in module_paths:
        tree = ast.parse(module_path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            assert not (banned_roots & {name.split(".")[0] for name in names}), (
                module_path,
                names,
            )

    code = """
import sys
import magi_agent.recipes.first_party.memory_recall
import magi_agent.harness.memory_recall
for name in (
    'google.adk',
    'google.genai',
    'openai',
    'anthropic',
    'httpx',
    'requests',
    'socket',
    'supabase',
    'psycopg',
    'asyncpg',
    'magi_agent.runtime.adk_turn_runner',
    'magi_agent.runtime.provider_execution',
    'magi_agent.app',
):
    if name in sys.modules:
        raise SystemExit(name)
"""
    completed = subprocess.run(
        [sys.executable, "-c", code],
        check=False,
        text=True,
        capture_output=True,
        cwd=python_root,
    )

    assert completed.returncode == 0, completed.stderr or completed.stdout
