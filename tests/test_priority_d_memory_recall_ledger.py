from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def test_memory_recall_ledger_dedupes_provider_and_adk_records_without_projection() -> None:
    from openmagi_core_agent.memory.recall_ledger import (
        MemoryRecallLedgerConfig,
        MemoryRecallRecordInput,
        build_memory_recall_ledger,
    )

    provider_record = MemoryRecallRecordInput(
        recordId="mem-1",
        providerId="hipocampus-qmd-readonly",
        source="provider",
        sourceRef="memory/ROOT.md",
        evidenceRef="evidence:memory-1",
        snippet="Visible launch note.",
    )
    adk_duplicate = MemoryRecallRecordInput(
        recordId="mem-1",
        providerId="hipocampus-qmd-readonly",
        source="adk_memory_service",
        sourceRef="memory/ROOT.md",
        evidenceRef="evidence:memory-1",
        snippet="Visible launch note duplicate.",
    )

    ledger = build_memory_recall_ledger(
        (provider_record, adk_duplicate),
        config=MemoryRecallLedgerConfig(enabled=True),
    )

    assert ledger.status == "recorded"
    assert len(ledger.decisions) == 2
    assert ledger.decisions[0].decision == "allowed"
    assert ledger.decisions[1].decision == "suppressed"
    assert ledger.decisions[1].reason_code == "duplicate_recall_record"
    assert ledger.public_refs == ("memory-ref:mem-1", "evidence:memory-1")
    assert ledger.authority_flags.memory_provider_called is False
    assert ledger.authority_flags.prompt_injection_allowed is False


def test_memory_recall_ledger_records_source_authority_and_private_suppression() -> None:
    from openmagi_core_agent.memory.recall_ledger import (
        MemoryRecallLedgerConfig,
        MemoryRecallRecordInput,
        build_memory_recall_ledger,
    )

    ledger = build_memory_recall_ledger(
        (
            MemoryRecallRecordInput(
                recordId="mem-private",
                providerId="hipocampus-qmd-readonly",
                source="provider",
                sourceRef="/Users/kevin/private/memory.md",
                evidenceRef="s3://private/evidence",
                snippet="raw_tool_log: Cookie: session=unsafe",
                visibility="private",
            ),
            MemoryRecallRecordInput(
                recordId="mem-old",
                providerId="hipocampus-qmd-readonly",
                source="provider",
                sourceRef="memory/old.md",
                evidenceRef="evidence:memory-old",
                snippet="Old plan says option A.",
            ),
        ),
        config=MemoryRecallLedgerConfig(
            enabled=True,
            currentSourceAuthoritative=True,
        ),
    )
    encoded = ledger.model_dump_json(by_alias=True)

    assert ledger.decisions[0].decision == "suppressed"
    assert ledger.decisions[0].reason_code == "private_memory_ref_only"
    assert ledger.decisions[1].decision == "suppressed"
    assert ledger.decisions[1].reason_code == "current_source_outranks_memory"
    assert "/Users/kevin" not in encoded
    assert "s3://private" not in encoded
    assert "Cookie:" not in encoded
    assert "session=unsafe" not in encoded
    assert "raw_tool_log" not in encoded
    assert ledger.public_refs == ()


def test_memory_recall_ledger_redacts_bearer_snippets_and_secret_record_ids() -> None:
    from openmagi_core_agent.memory.recall_ledger import (
        MemoryRecallLedgerConfig,
        MemoryRecallRecordInput,
        build_memory_recall_ledger,
    )

    ledger = build_memory_recall_ledger(
        (
            MemoryRecallRecordInput(
                recordId="sk-live-secret",
                providerId="hipocampus-qmd-readonly",
                source="provider",
                sourceRef="memory/public.md",
                evidenceRef="evidence:memory-safe",
                snippet="Visible line\nBearer live-token-abc12345",
            ),
        ),
        config=MemoryRecallLedgerConfig(enabled=True),
    )
    encoded = ledger.model_dump_json(by_alias=True)

    assert ledger.status == "recorded"
    assert ledger.decisions[0].decision == "allowed"
    assert "Visible line" in ledger.decisions[0].snippet_preview
    assert "Bearer" not in encoded
    assert "live-token" not in encoded
    assert "sk-live-secret" not in encoded
    assert ledger.public_refs[0].startswith("memory-ref:")
    assert ledger.public_refs[0] != "memory-ref:sk-live-secret"


def test_memory_recall_ledger_redacts_provider_tokens_in_ids_and_snippets() -> None:
    from openmagi_core_agent.memory.recall_ledger import (
        MemoryRecallLedgerConfig,
        MemoryRecallRecordInput,
        build_memory_recall_ledger,
    )

    ledger = build_memory_recall_ledger(
        (
            MemoryRecallRecordInput(
                recordId="github_pat_unsafeToken12345",
                providerId="hipocampus-qmd-readonly",
                source="provider",
                sourceRef="memory/public.md",
                snippet=(
                    "Visible line\n"
                    "github_pat_unsafeToken12345\n"
                    "xoxb-unsafeToken12345\n"
                    "AKIAUNSAFEKEY12345\n"
                    "AIzaUnsafeGoogleToken12345"
                ),
            ),
        ),
        config=MemoryRecallLedgerConfig(enabled=True),
    )
    encoded = ledger.model_dump_json(by_alias=True)

    assert "Visible line" in encoded
    for forbidden in (
        "github_pat_unsafe",
        "xoxb-unsafe",
        "AKIAUNSAFE",
        "AIzaUnsafe",
    ):
        assert forbidden not in encoded
    assert ledger.public_refs[0].startswith("memory-ref:")
    assert ledger.public_refs[0] != "memory-ref:github_pat_unsafeToken12345"


def test_memory_recall_ledger_redacts_raw_child_and_tool_snippet_blocks() -> None:
    from openmagi_core_agent.memory.recall_ledger import (
        MemoryRecallLedgerConfig,
        MemoryRecallRecordInput,
        build_memory_recall_ledger,
    )

    ledger = build_memory_recall_ledger(
        (
            MemoryRecallRecordInput(
                recordId="mem-raw-child-tool",
                providerId="hipocampus-qmd-readonly",
                source="provider",
                sourceRef="memory/public.md",
                snippet=(
                    "Visible safe summary.\n"
                    "raw_child_transcript data\n"
                    "raw_subagent_transcript_secret\n"
                    "<tool_log>secret</tool_log>\n"
                    "<child_prompt>private prompt</child_prompt>\n"
                    "raw_tool_args data\n"
                    "tool log: internal command output\n"
                    "tool args: private arguments\n"
                    "child prompt: private instruction\n"
                    "hidden reasoning: private trace\n"
                    "private_reasoning: secret rationale\n"
                    "raw_subagent_transcript_secret:\n"
                    "TRANSCRIPT_PAYLOAD_DO_NOT_LEAK\n"
                    "private_reasoning:\n"
                    "COT_PAYLOAD_DO_NOT_LEAK\n"
                    "private_reasoning:\n"
                    "\n"
                    "BLANK_LINE_COT_PAYLOAD_DO_NOT_LEAK\n"
                    "raw_subagent_transcript_secret:\n"
                    "MULTILINE_PAYLOAD_LINE_ONE_DO_NOT_LEAK\n"
                    "MULTILINE_PAYLOAD_LINE_TWO_DO_NOT_LEAK"
                ),
            ),
        ),
        config=MemoryRecallLedgerConfig(enabled=True),
    )
    encoded = ledger.model_dump_json(by_alias=True)

    assert "Visible safe summary" in encoded
    assert "raw_child_transcript" not in encoded
    assert "raw_subagent_transcript" not in encoded
    assert "raw_tool_args" not in encoded
    assert "<tool_log>" not in encoded
    assert "<child_prompt>" not in encoded
    assert "secret" not in encoded
    assert "private prompt" not in encoded
    assert "tool log" not in encoded
    assert "tool args" not in encoded
    assert "child prompt" not in encoded
    assert "hidden reasoning" not in encoded
    assert "private_reasoning" not in encoded
    assert "secret rationale" not in encoded
    assert "TRANSCRIPT_PAYLOAD_DO_NOT_LEAK" not in encoded
    assert "COT_PAYLOAD_DO_NOT_LEAK" not in encoded
    assert "BLANK_LINE_COT_PAYLOAD_DO_NOT_LEAK" not in encoded
    assert "MULTILINE_PAYLOAD_LINE_ONE_DO_NOT_LEAK" not in encoded
    assert "MULTILINE_PAYLOAD_LINE_TWO_DO_NOT_LEAK" not in encoded


def test_memory_recall_ledger_redacts_generic_api_key_ids_and_snippets() -> None:
    from openmagi_core_agent.memory.recall_ledger import (
        MemoryRecallLedgerConfig,
        MemoryRecallRecordInput,
        build_memory_recall_ledger,
    )

    ledger = build_memory_recall_ledger(
        (
            MemoryRecallRecordInput(
                recordId="api_key:supersecret123",
                providerId="hipocampus-qmd-readonly",
                source="provider",
                sourceRef="memory/public.md",
                snippet="Visible line\nAPI_KEY=supersecret123\napi_key: anothersecret456",
            ),
        ),
        config=MemoryRecallLedgerConfig(enabled=True),
    )
    encoded = ledger.model_dump_json(by_alias=True)

    assert "Visible line" in encoded
    assert "api_key" not in encoded.casefold()
    assert "supersecret" not in encoded
    assert "anothersecret" not in encoded
    assert ledger.public_refs[0].startswith("memory-ref:")
    assert ledger.public_refs[0] != "memory-ref:api_key:supersecret123"


def test_memory_recall_ledger_redacts_telegram_bot_url_snippets_and_payload_blocks() -> None:
    from openmagi_core_agent.memory.recall_ledger import (
        MemoryRecallLedgerConfig,
        MemoryRecallRecordInput,
        build_memory_recall_ledger,
    )

    ledger = build_memory_recall_ledger(
        (
            MemoryRecallRecordInput(
                recordId="telegram-source",
                providerId="hipocampus-qmd-readonly",
                source="provider",
                sourceRef="memory/public.md",
                snippet=(
                    "Visible safe summary.\n"
                    "https://api.telegram.org/bot123:ABC/sendMessage\n"
                    "TELEGRAM_NEXT_LINE_PAYLOAD_DO_NOT_LEAK\n"
                    "TELEGRAM_SECOND_LINE_PAYLOAD_DO_NOT_LEAK"
                ),
            ),
        ),
        config=MemoryRecallLedgerConfig(enabled=True),
    )
    encoded = ledger.model_dump_json(by_alias=True)

    assert "Visible safe summary" in encoded
    assert "api.telegram.org" not in encoded
    assert "bot123:ABC" not in encoded
    assert "TELEGRAM_NEXT_LINE_PAYLOAD_DO_NOT_LEAK" not in encoded
    assert "TELEGRAM_SECOND_LINE_PAYLOAD_DO_NOT_LEAK" not in encoded


def test_memory_recall_ledger_redacts_plain_object_storage_urls_and_payload_blocks() -> None:
    from openmagi_core_agent.memory.recall_ledger import (
        MemoryRecallLedgerConfig,
        MemoryRecallRecordInput,
        build_memory_recall_ledger,
    )

    ledger = build_memory_recall_ledger(
        (
            MemoryRecallRecordInput(
                recordId="object-source",
                providerId="hipocampus-qmd-readonly",
                source="provider",
                sourceRef="memory/public.md",
                snippet=(
                    "Visible safe summary.\n"
                    "https://storage.googleapis.com/private-bucket/object\n"
                    "OBJECT_PAYLOAD_DO_NOT_LEAK\n"
                    "OBJECT_SECOND_LINE_PAYLOAD_DO_NOT_LEAK"
                ),
            ),
        ),
        config=MemoryRecallLedgerConfig(enabled=True),
    )
    encoded = ledger.model_dump_json(by_alias=True)

    assert "Visible safe summary" in encoded
    assert "storage.googleapis.com" not in encoded
    assert "private-bucket" not in encoded
    assert "OBJECT_PAYLOAD_DO_NOT_LEAK" not in encoded
    assert "OBJECT_SECOND_LINE_PAYLOAD_DO_NOT_LEAK" not in encoded


def test_memory_recall_ledger_redacts_json_shaped_api_key_snippets() -> None:
    from openmagi_core_agent.memory.recall_ledger import (
        MemoryRecallLedgerConfig,
        MemoryRecallRecordInput,
        build_memory_recall_ledger,
    )

    ledger = build_memory_recall_ledger(
        (
            MemoryRecallRecordInput(
                recordId="mem-json",
                providerId="hipocampus-qmd-readonly",
                source="provider",
                sourceRef="memory/public.md",
                snippet='Visible line\n{"api_key": "supersecret123"}',
            ),
        ),
        config=MemoryRecallLedgerConfig(enabled=True),
    )
    encoded = ledger.model_dump_json(by_alias=True)

    assert "Visible line" in encoded
    assert "api_key" not in encoded.casefold()
    assert "supersecret" not in encoded


def test_memory_recall_ledger_redacts_home_and_exact_kubelet_paths() -> None:
    from openmagi_core_agent.memory.recall_ledger import (
        MemoryRecallLedgerConfig,
        MemoryRecallRecordInput,
        build_memory_recall_ledger,
    )

    ledger = build_memory_recall_ledger(
        (
            MemoryRecallRecordInput(
                recordId="mem-paths",
                providerId="hipocampus-qmd-readonly",
                source="provider",
                sourceRef="memory/public.md",
                snippet=(
                    "Visible line\n"
                    "/home/kevin/.ssh/id_rsa\n"
                    "/var/lib/kubelet\n"
                    "/var/lib/kubelet/pods/x/token"
                ),
            ),
        ),
        config=MemoryRecallLedgerConfig(enabled=True),
    )
    encoded = ledger.model_dump_json(by_alias=True)

    assert "Visible line" in encoded
    assert "/home/kevin" not in encoded
    assert "/var/lib/kubelet" not in encoded


def test_memory_recall_ledger_import_boundary_avoids_live_memory_providers() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import importlib
import sys

before = set(sys.modules)
module = importlib.import_module("openmagi_core_agent.memory.recall_ledger")
assert hasattr(module, "build_memory_recall_ledger")

forbidden_exact = (
    "google.adk.memory",
    "google.adk.sessions",
    "openmagi_core_agent.memory.adk_bridge",
    "openmagi_core_agent.memory.adapters.hipocampus_readonly",
    "httpx",
    "requests",
    "supabase",
    "psycopg",
)
loaded = [
    name
    for name in set(sys.modules) - before
    if name in forbidden_exact
    or any(name.startswith(f"{prefix}.") for prefix in forbidden_exact)
]
if loaded:
    raise AssertionError(f"memory recall ledger loaded forbidden modules: {loaded}")
""",
        ],
        cwd=Path(__file__).parents[1],
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
