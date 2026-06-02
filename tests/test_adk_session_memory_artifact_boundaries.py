from __future__ import annotations

import asyncio

import pytest
from google.adk.events import Event

from openmagi_core_agent.adk_bridge.artifact_service import (
    ArtifactAuthorityFlags,
    ArtifactBoundaryConfig,
    ArtifactServiceBoundary,
)
from openmagi_core_agent.adk_bridge.memory_service import (
    MemoryAuthorityFlags,
    MemoryBoundaryConfig,
    MemoryServiceBoundary,
)
from openmagi_core_agent.adk_bridge.session_service import (
    WorkspaceSessionService,
    project_session_for_durable_store,
)


def test_session_projection_exposes_only_approved_refs_not_raw_events() -> None:
    service = WorkspaceSessionService(app_name="openmagi")

    async def exercise():
        session = await service.create_session(
            app_name="openmagi",
            user_id="user-1",
            session_id="session-1",
            state={
                "openmagi.memoryMode": "normal",
                "openmagi.note": "User asked for private tax issue",
                "openmagi.safeLabel": "Authorization: Bearer unsafe",
                "rawPrompt": "Authorization: Bearer unsafe",
            },
        )
        await service.append_event(
            session,
            Event(author="model", invocation_id="turn-1"),
        )
        return project_session_for_durable_store(session)

    projection = asyncio.run(exercise())
    encoded = str(projection.public_projection())

    assert projection.session_ref.startswith("session:sha256:")
    assert projection.user_ref.startswith("user:sha256:")
    assert projection.event_count == 1
    assert projection.state_digest.startswith("sha256:")
    assert projection.approved_state_refs
    assert all(str(value).startswith("sha256:") for value in projection.approved_state_refs.values())
    assert "Authorization" not in encoded
    assert "Bearer" not in encoded
    assert "rawPrompt" not in encoded
    assert "safeLabel" not in encoded
    assert "private tax issue" not in encoded


def test_session_projection_hashes_adk_session_and_user_identifiers() -> None:
    service = WorkspaceSessionService(app_name="openmagi")

    async def exercise():
        session = await service.create_session(
            app_name="openmagi",
            user_id="user@example.com",
            session_id="sessionKeySECRET",
            state={"openmagi.memoryMode": "normal"},
        )
        return project_session_for_durable_store(session)

    projection = asyncio.run(exercise())
    encoded = str(projection.public_projection())

    assert projection.session_ref.startswith("session:sha256:")
    assert projection.user_ref.startswith("user:sha256:")
    assert "sessionKeySECRET" not in encoded
    assert "user@example.com" not in encoded


def test_session_projection_hashes_approved_state_keys() -> None:
    service = WorkspaceSessionService(app_name="openmagi")

    async def exercise():
        session = await service.create_session(
            app_name="openmagi",
            user_id="user-1",
            session_id="session-1",
            state={
                "openmagi.memoryMode": "normal",
                "openmagi./Users/kevin/.ssh/id_rsa": "digest-ref",
                "openmagi." + "auth" + "Key": "digest-ref",
            },
        )
        return project_session_for_durable_store(session)

    projection = asyncio.run(exercise())
    encoded = str(projection.public_projection())

    assert projection.approved_state_refs
    assert all(key.startswith("state:sha256:") for key in projection.approved_state_refs)
    assert "openmagi.memoryMode" not in encoded
    assert "/Users/kevin" not in encoded
    assert "authKey" not in encoded


def test_memory_service_boundary_defaults_off_and_denies_writes() -> None:
    boundary = MemoryServiceBoundary(MemoryBoundaryConfig())
    projection = boundary.public_projection()

    assert projection["enabled"] is False
    assert projection["adkMemoryServiceAttached"] is False
    assert projection["writeAllowed"] is False
    assert projection["reasonCodes"] == ["memory_service_boundary_disabled"]

    with pytest.raises(ValueError, match="write"):
        MemoryBoundaryConfig(enabled=True, writeAllowed=True)


def test_memory_boundary_config_cannot_be_forged_with_construct_or_copy() -> None:
    forged = MemoryBoundaryConfig.model_construct(
        enabled=True,
        write_allowed=True,
        prompt_projection_allowed=True,
    )
    copied = MemoryBoundaryConfig().model_copy(
        update={"writeAllowed": True, "promptProjectionAllowed": True}
    )

    assert forged.write_allowed is False
    assert forged.prompt_projection_allowed is False
    assert copied.write_allowed is False
    assert copied.prompt_projection_allowed is False


def test_memory_authority_flags_cannot_be_forged_with_construct_or_copy() -> None:
    forged = MemoryAuthorityFlags.model_construct(
        adk_memory_service_attached=True,
        memory_write_allowed=True,
        prompt_projection_allowed=True,
    )
    copied = MemoryAuthorityFlags().model_copy(
        update={
            "adkMemoryServiceAttached": True,
            "memoryWriteAllowed": True,
            "promptProjectionAllowed": True,
        }
    )

    assert forged.adk_memory_service_attached is False
    assert forged.memory_write_allowed is False
    assert forged.prompt_projection_allowed is False
    assert copied.adk_memory_service_attached is False
    assert copied.memory_write_allowed is False
    assert copied.prompt_projection_allowed is False


def test_artifact_service_boundary_defaults_off_and_denies_blob_writes() -> None:
    boundary = ArtifactServiceBoundary(ArtifactBoundaryConfig())
    projection = boundary.public_projection()

    assert projection["enabled"] is False
    assert projection["adkArtifactServiceAttached"] is False
    assert projection["artifactWriteAllowed"] is False
    assert projection["blobStorageLocation"] == "external_ref_only"

    with pytest.raises(ValueError, match="artifact write"):
        ArtifactBoundaryConfig(enabled=True, artifactWriteAllowed=True)


def test_artifact_boundary_config_cannot_be_forged_with_construct_or_copy() -> None:
    forged = ArtifactBoundaryConfig.model_construct(
        enabled=True,
        artifact_write_allowed=True,
        adk_artifact_service_attached=True,
    )
    copied = ArtifactBoundaryConfig().model_copy(
        update={"artifactWriteAllowed": True, "adkArtifactServiceAttached": True}
    )

    assert forged.artifact_write_allowed is False
    assert forged.adk_artifact_service_attached is False
    assert copied.artifact_write_allowed is False
    assert copied.adk_artifact_service_attached is False


def test_artifact_authority_flags_cannot_be_forged_with_construct_or_copy() -> None:
    forged = ArtifactAuthorityFlags.model_construct(
        adk_artifact_service_attached=True,
        artifact_write_allowed=True,
        production_storage_written=True,
    )
    copied = ArtifactAuthorityFlags().model_copy(
        update={
            "adkArtifactServiceAttached": True,
            "artifactWriteAllowed": True,
            "productionStorageWritten": True,
        }
    )

    assert forged.adk_artifact_service_attached is False
    assert forged.artifact_write_allowed is False
    assert forged.production_storage_written is False
    assert copied.adk_artifact_service_attached is False
    assert copied.artifact_write_allowed is False
    assert copied.production_storage_written is False


def test_boundaries_do_not_eagerly_import_provider_sdks() -> None:
    import sys

    forbidden = {
        "supabase",
        "psycopg",
        "psycopg2",
        "boto3",
        "google.cloud.storage",
    }

    assert forbidden.isdisjoint(sys.modules)
