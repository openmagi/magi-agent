from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from openmagi_core_agent.memory.conformance import (
    MemoryProviderConformanceFixture,
    load_memory_provider_conformance_fixture,
    project_memory_provider_conformance_fixture,
)


FIXTURES = Path(__file__).parent / "fixtures" / "memory_contract"

EXPECTED_FALSE_ONLY_FLAGS = (
    "adkMemoryServiceReplaced",
    "adkMemoryServiceAttached",
    "liveProviderCalls",
    "providerSdkImports",
    "agentMemoryCalls",
    "hipocampusQmdCalls",
    "promptProjection",
    "memoryWrites",
    "routesAttached",
    "productionStorage",
)

EXPECTED_OPERATION_ORDER = (
    "remember",
    "search",
    "compact",
    "decay",
    "delete",
    "export",
    "conflict_resolve",
)


def test_provider_conformance_fixture_declares_metadata_only_phase0_phase1_contract() -> None:
    fixture = load_memory_provider_conformance_fixture(
        "provider_conformance_matrix.json",
        fixture_root=FIXTURES,
    )
    projection = project_memory_provider_conformance_fixture(fixture)

    assert fixture.schema_version == "memoryProviderConformanceMatrix.v1"
    assert fixture.fixture_id == "memory_provider_conformance_matrix_0001"
    assert fixture.phase == "phase_0_1_metadata_only"
    assert fixture.adk_first.model_dump(by_alias=True) == {
        "adkOwns": ["MemoryService", "provider_lifecycle_attachment"],
        "openMagiOwns": [
            "provider_neutral_policy",
            "tenant_scope",
            "source_authority",
            "redaction",
            "receipt_semantics",
            "lifecycle_metadata",
            "audit_evidence_refs",
            "provider_conformance_gates",
        ],
        "memoryServiceReplacementAllowed": False,
        "providerAttachmentAllowed": False,
    }
    assert tuple(fixture.import_boundary.model_dump(by_alias=True)) == EXPECTED_FALSE_ONLY_FLAGS
    assert set(fixture.import_boundary.model_dump(by_alias=True).values()) == {False}

    assert projection.fixture_id == "memory_provider_conformance_matrix_0001"
    assert projection.provider_ids == (
        "hipocampus-qmd-readonly",
        "agentmemory-metadata-gate",
        "external-vector-metadata-gate",
    )
    assert projection.operation_order == EXPECTED_OPERATION_ORDER
    assert projection.provider_count_by_phase == {"phase_0": 1, "phase_1": 2}
    assert projection.metadata_only is True
    assert projection.no_live_runtime is True

    providers = {provider.provider_id: provider for provider in fixture.providers}

    hipocampus = providers["hipocampus-qmd-readonly"]
    assert hipocampus.phase == "phase_0"
    assert hipocampus.storage_model == "file_snapshot"
    assert hipocampus.fact_contract.bitemporal is True
    assert hipocampus.fact_contract.valid_time == "declared"
    assert hipocampus.fact_contract.transaction_time == "declared"
    assert hipocampus.lifecycle_tiers == ("hot", "warm", "cold", "tombstone")
    assert hipocampus.dry_run_maintenance == {
        "compaction": "supported_dry_run_only",
        "decay": "supported_dry_run_only",
        "delete": "supported_dry_run_only",
    }

    agentmemory = providers["agentmemory-metadata-gate"]
    assert agentmemory.phase == "phase_1"
    assert agentmemory.storage_model == "external"
    assert agentmemory.import_or_sdk_allowed is False
    assert agentmemory.fact_contract.audit_evidence_ref == "evidence:agentmemory:metadata-only"

    vector = providers["external-vector-metadata-gate"]
    assert vector.phase == "phase_1"
    assert vector.storage_model == "vector"
    assert vector.import_or_sdk_allowed is False
    assert vector.fact_contract.redaction_status == "required_before_projection"

    for provider in fixture.providers:
        assert provider.provider_call_allowed is False
        assert provider.prompt_projection_allowed is False
        assert provider.memory_write_allowed is False
        assert provider.operations == EXPECTED_OPERATION_ORDER
        for envelope in provider.operation_envelopes:
            assert envelope.operation in EXPECTED_OPERATION_ORDER
            assert envelope.executes_provider is False
            assert envelope.mutates_memory is False
            assert envelope.support in {"metadata_only", "unsupported"}
            assert envelope.failure_code.startswith("memory_provider_")

    assert projection.support_matrix["hipocampus-qmd-readonly"]["remember"] == {
        "support": "unsupported",
        "failureCode": "memory_provider_write_disabled",
        "dryRun": False,
    }
    assert projection.support_matrix["hipocampus-qmd-readonly"]["compact"] == {
        "support": "metadata_only",
        "failureCode": "memory_provider_compaction_dry_run_only",
        "dryRun": True,
    }
    assert projection.support_matrix["agentmemory-metadata-gate"]["search"] == {
        "support": "metadata_only",
        "failureCode": "memory_provider_live_calls_disabled",
        "dryRun": True,
    }


def test_provider_conformance_fixture_contains_no_live_or_mutation_payloads() -> None:
    payload = json.loads((FIXTURES / "provider_conformance_matrix.json").read_text())
    fixture = MemoryProviderConformanceFixture.model_validate(payload)
    projection = project_memory_provider_conformance_fixture(fixture)

    fixture_json = json.dumps(fixture.model_dump(by_alias=True), sort_keys=True)
    projection_json = json.dumps(projection.model_dump(by_alias=True), sort_keys=True)
    unsafe_fragments = (
        "google.adk.memory.MemoryService",
        "AgentMemory(",
        "qmd search",
        "hipocampus search",
        "remember(",
        "delete(",
        "compact(",
        "Bearer unsafe",
        "ghp_memorysecret",
        "sk-memory-secret",
        "SUPABASE_SERVICE_ROLE_KEY",
        "/data/bots",
        "/workspace",
        "postgres://",
        "s3://",
        "adkMemoryServiceReplaced\": true",
        "adkMemoryServiceAttached\": true",
        "liveProviderCalls\": true",
        "providerSdkImports\": true",
        "agentMemoryCalls\": true",
        "hipocampusQmdCalls\": true",
        "promptProjection\": true",
        "memoryWrites\": true",
        "routesAttached\": true",
        "productionStorage\": true",
        "executesProvider\": true",
        "mutatesMemory\": true",
    )
    for fragment in unsafe_fragments:
        assert fragment not in fixture_json
        assert fragment not in projection_json


def test_provider_conformance_rejects_live_provider_calls_and_mutation_envelopes() -> None:
    payload = json.loads((FIXTURES / "provider_conformance_matrix.json").read_text())
    payload["providers"][0]["providerCallAllowed"] = True

    with pytest.raises(ValidationError, match="provider calls"):
        MemoryProviderConformanceFixture.model_validate(payload)

    payload = json.loads((FIXTURES / "provider_conformance_matrix.json").read_text())
    payload["providers"][0]["operationEnvelopes"][0]["mutatesMemory"] = True

    with pytest.raises(ValidationError, match="mutation"):
        MemoryProviderConformanceFixture.model_validate(payload)


def test_provider_conformance_import_boundary_stays_provider_and_adk_service_free() -> None:
    code = """
import importlib
import json
import sys
from pathlib import Path

conformance = importlib.import_module('openmagi_core_agent.memory.conformance')
fixture = conformance.load_memory_provider_conformance_fixture(
    'provider_conformance_matrix.json',
    fixture_root=Path('tests/fixtures/memory_contract'),
)
assert fixture.fixture_id == 'memory_provider_conformance_matrix_0001'

forbidden_prefixes = (
    'google.adk.memory',
    'google.adk.runners',
    'openmagi_core_agent.adk_bridge.local_runner',
    'openmagi_core_agent.adk_bridge.runner_adapter',
    'openmagi_core_agent.routes',
    'openmagi_core_agent.proxy',
    'openmagi_core_agent.transport.chat',
    'openmagi_core_agent.providers',
    'openmagi_core_agent.plugins.agentmemory',
    'openmagi_core_agent.memory.providers',
    'openmagi_core_agent.services.memory',
    'openmagi_core_agent.hipocampus',
    'openmagi_core_agent.qmd',
    'agentmemory',
    'openai',
    'anthropic',
    'google.genai',
    'pinecone',
    'qdrant_client',
    'weaviate',
    'chromadb',
    'requests',
    'httpx',
)
loaded = [
    name
    for name in sys.modules
    if any(name == prefix or name.startswith(f'{prefix}.') for prefix in forbidden_prefixes)
]
if loaded:
    raise AssertionError(f'forbidden modules loaded: {loaded}')
"""
    completed = subprocess.run(
        [sys.executable, "-c", code],
        check=False,
        cwd=Path(__file__).parents[1],
        text=True,
        capture_output=True,
    )

    assert completed.returncode == 0, completed.stderr
