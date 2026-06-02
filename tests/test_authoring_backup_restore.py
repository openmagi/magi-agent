from __future__ import annotations

from urllib.parse import quote

import pytest
from pydantic import BaseModel, ValidationError

from openmagi_core_agent import authoring as authoring_module
from openmagi_core_agent.authoring.backup_restore import (
    RecipeBackupArtifactRef,
    RecipeBackupLedgerRef,
    RecipeBackupManifest,
    RecipeBackupScope,
    RecipeRestoreValidationBlocker,
    RecipeRestoreValidationRequest,
    digest_recipe_backup_manifest,
    validate_recipe_restore_request,
)


DIGEST_A = "sha256:" + "a" * 64
DIGEST_B = "sha256:" + "b" * 64
DIGEST_C = "sha256:" + "c" * 64
DIGEST_D = "sha256:" + "d" * 64
DIGEST_E = "sha256:" + "e" * 64
DIGEST_F = "sha256:" + "f" * 64


def _encoded(value: str, rounds: int) -> str:
    for _ in range(rounds):
        value = quote(value, safe="")
    return value


def _valid_manifest_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "schemaVersion": "recipe_backup_manifest.v1",
        "backupId": "backup.finance-research.001",
        "scope": {
            "ownerId": "owner_public_001",
            "botId": "bot_public_001",
            "sessionId": "session_public_001",
        },
        "durableStoreRef": "durable-store.recipe-builder.metadata.001",
        "artifactIndexDigest": DIGEST_A,
        "ledger": {
            "ledgerRef": "ledger.recipe-builder.backup.001",
            "ledgerHeadDigest": DIGEST_B,
            "summary": "Append-only authoring metadata ledger; grants no live authority.",
        },
        "exportPackageRefs": ("export-package.finance-research.001",),
        "exportPackageDigests": (DIGEST_C,),
        "backupArtifacts": (
            {
                "artifactType": "sqlite_backup",
                "path": "backups/finance-research/core-agent.sqlite.backup",
                "digest": DIGEST_D,
                "byteSize": 8192,
                "mediaType": "application/vnd.sqlite3",
                "redactionStatus": "redacted",
                "summary": "Metadata backup blob reference only; no connector credentials.",
            },
            {
                "artifactType": "artifact_index",
                "path": "backups/finance-research/artifact-index.json",
                "digest": DIGEST_A,
                "byteSize": 1024,
                "mediaType": "application/json",
                "redactionStatus": "public_safe",
                "summary": "Digest index for authoring metadata artifacts.",
            },
            {
                "artifactType": "ledger_manifest",
                "path": "backups/finance-research/ledger-manifest.json",
                "digest": DIGEST_B,
                "byteSize": 2048,
                "mediaType": "application/json",
                "redactionStatus": "public_safe",
                "summary": "Ledger head digest manifest for dry-run restore validation.",
            },
            {
                "artifactType": "export_bundle",
                "path": "backups/finance-research/export-package.json",
                "digest": DIGEST_C,
                "byteSize": 4096,
                "mediaType": "application/json",
                "redactionStatus": "public_safe",
                "summary": "Portable recipe export package metadata reference.",
            },
        ),
        "createdByRef": "recipe-builder-mode.backup.validate-only",
        "backupMode": "metadata_refs_only",
        "integrityChecks": ("artifact-index-digest", "ledger-head-digest"),
        "checkMetadata": (
            {
                "ref": "check.artifact-index-digest",
                "digest": DIGEST_E,
                "summary": "Verifies referenced artifact digests only; performs no storage writes.",
            },
            {
                "ref": "check.ledger-head-digest",
                "digest": DIGEST_F,
                "summary": "Verifies ledger head digest only; performs no runtime activation.",
            },
        ),
    }
    payload.update(overrides)
    return payload


def _valid_restore_request_payload(**overrides: object) -> dict[str, object]:
    manifest = RecipeBackupManifest.model_validate(_valid_manifest_payload())
    payload: dict[str, object] = {
        "targetScope": {
            "ownerId": manifest.scope.owner_id,
            "botId": manifest.scope.bot_id,
            "sessionId": manifest.scope.session_id,
        },
        "backupManifest": manifest.model_dump(by_alias=True),
        "backupDigest": digest_recipe_backup_manifest(manifest),
        "validationMode": "dry_run",
    }
    payload.update(overrides)
    return payload


def test_backup_manifest_is_publicly_importable_and_digest_is_deterministic() -> None:
    manifest = RecipeBackupManifest.model_validate(_valid_manifest_payload())
    payload_with_different_key_order = {
        "checkMetadata": manifest.model_dump(by_alias=True)["checkMetadata"],
        "backupArtifacts": manifest.model_dump(by_alias=True)["backupArtifacts"],
        **_valid_manifest_payload(),
    }

    assert authoring_module.RecipeBackupManifest is RecipeBackupManifest
    assert authoring_module.RecipeRestoreValidationRequest is RecipeRestoreValidationRequest
    assert manifest.schema_version == "recipe_backup_manifest.v1"
    assert manifest.backup_mode == "metadata_refs_only"
    assert manifest.ledger.ledger_head_digest == DIGEST_B
    assert manifest.backup_artifacts[0].redaction_status == "redacted"

    assert digest_recipe_backup_manifest(manifest) == (
        digest_recipe_backup_manifest(payload_with_different_key_order)
    )
    assert digest_recipe_backup_manifest(manifest).startswith("sha256:")


def test_valid_restore_validation_is_dry_run_and_default_off() -> None:
    request = RecipeRestoreValidationRequest.model_validate(_valid_restore_request_payload())

    result = validate_recipe_restore_request(request)

    assert result.status == "valid"
    assert result.blockers == ()
    assert result.backup_digest == request.backup_digest
    assert result.accepted_artifact_refs == tuple(
        artifact.path for artifact in request.backup_manifest.backup_artifacts
    )
    assert result.target_scope == request.target_scope
    assert result.restore_writes_enabled is False
    assert result.activation_enabled is False
    assert result.runtime_activation_eligible is False
    assert result.connector_credentials_restored is False
    assert result.schedules_restored is False
    assert result.memory_writes_enabled is False
    assert result.workspace_mutation_enabled is False


def test_backup_digest_mismatch_blocks_without_artifact_acceptance_or_authority() -> None:
    request = RecipeRestoreValidationRequest.model_validate(
        _valid_restore_request_payload(backupDigest=DIGEST_F)
    )

    result = validate_recipe_restore_request(request)

    assert result.status == "blocked"
    assert result.backup_digest == DIGEST_F
    assert result.accepted_artifact_refs == ()
    assert result.restore_writes_enabled is False
    assert result.activation_enabled is False
    assert result.runtime_activation_eligible is False
    assert result.connector_credentials_restored is False
    assert result.schedules_restored is False
    assert result.memory_writes_enabled is False
    assert result.workspace_mutation_enabled is False
    assert result.blockers[0].code == "backup_digest_mismatch"


def test_cross_scope_restore_validation_blocks_without_artifact_acceptance_or_authority() -> None:
    request = RecipeRestoreValidationRequest.model_validate(
        _valid_restore_request_payload(
            targetScope={
                "ownerId": "owner_target_001",
                "botId": "bot_target_001",
                "sessionId": "session_target_001",
            }
        )
    )

    result = validate_recipe_restore_request(request)

    assert result.status == "blocked"
    assert result.accepted_artifact_refs == ()
    assert result.restore_writes_enabled is False
    assert result.activation_enabled is False
    assert result.runtime_activation_eligible is False
    assert result.connector_credentials_restored is False
    assert result.schedules_restored is False
    assert result.memory_writes_enabled is False
    assert result.workspace_mutation_enabled is False
    assert result.blockers[0].code == "backup_scope_mismatch"


@pytest.mark.parametrize(
    "path",
    (
        "../core-agent.sqlite",
        "backups/../core-agent.sqlite",
        "/workspace/backups/core-agent.sqlite",
        "~/.openmagi/backups/core-agent.sqlite",
        "backups\\core-agent.sqlite",
        "file:///tmp/core-agent.sqlite",
        "HTTPS:example.com/core-agent.sqlite",
        "s3://bucket/core-agent.sqlite",
        "https://example.com/core-agent.sqlite?X-Amz-Signature=abc123",
        "backups/finance-research/core-agent.sqlite.backup?private_key=abc123456789",
        "backups/finance-research/core-agent.sqlite.backup?secret-key=abc123456789",
        "vault://secret/data/core-agent.sqlite",
        "backups/%2e%2e/core-agent.sqlite",
        "file%3A%2F%2F%2Ftmp%2Fcore-agent.sqlite",
        "HTTPS%3Aexample.com%2Fcore-agent.sqlite",
        "https%3A%2F%2Fexample.com%2Fbackup%3FX-Amz-Signature%3Dabc",
        "C:Users/core-agent.sqlite",
        "C%3A%5CUsers%5Ccore-agent.sqlite",
    ),
)
def test_backup_artifact_refs_reject_unsafe_paths(path: str) -> None:
    with pytest.raises(ValidationError, match="path"):
        RecipeBackupArtifactRef(
            artifactType="sqlite_backup",
            path=path,
            digest=DIGEST_A,
            byteSize=1,
            mediaType="application/vnd.sqlite3",
            redactionStatus="redacted",
            summary="Metadata backup blob reference only.",
        )


@pytest.mark.parametrize(
    ("field_name", "value", "match"),
    (
        ("rawCode", "print('unsafe')", "raw generated code"),
        ("sourceCode", "def run(): pass", "raw generated code"),
        ("rawPrompt", "hidden prompt", "raw prompt/output"),
        ("rawModelOutput", "generated tool body", "raw prompt/output"),
        ("apiKey", "placeholder-secret-value", "raw credential"),
        ("connectorCredentials", "secret", "connector credential"),
        ("foreignApprovalRefs", ("approval.other-owner",), "approval"),
        ("externalApprovalAuthorityState", True, "approval"),
        ("liveSessionCursors", ("cursor.live",), "live session"),
        ("inFlightSandboxProcesses", ("pid.123",), "sandbox"),
        ("hostedBillingQuotaState", "quota.available", "billing"),
        ("missingObjectStoreBlobs", (), "object-store"),
        ("builderAgentId", "builder-agent.backup", "Builder Agent"),
        ("restoreWritesEnabled", True, "restore"),
        ("activationEnabled", True, "activation"),
        ("runtimeActivationEligible", True, "activation"),
        ("connectorCredentialsRestored", True, "connector credential"),
        ("schedulesRestored", True, "schedule"),
        ("memoryWritesEnabled", True, "memory"),
        ("workspaceMutationEnabled", True, "workspace"),
        ("liveMode", True, "live"),
    ),
)
def test_backup_manifest_rejects_sensitive_runtime_authority_and_state_fields(
    field_name: str,
    value: object,
    match: str,
) -> None:
    with pytest.raises(ValidationError, match=match):
        RecipeBackupManifest.model_validate(_valid_manifest_payload(**{field_name: value}))


@pytest.mark.parametrize(
    "field_name",
    (
        "exportPackageRefs",
        "exportPackageDigests",
        "createdByRef",
        "integrityChecks",
        "checkMetadata",
    ),
)
def test_backup_manifest_requires_provenance_and_integrity_fields(field_name: str) -> None:
    payload = _valid_manifest_payload()
    payload.pop(field_name)

    with pytest.raises(ValidationError):
        RecipeBackupManifest.model_validate(payload)


@pytest.mark.parametrize(
    ("field_name", "value"),
    (
        ("exportPackageRefs", ()),
        ("exportPackageDigests", ()),
        ("createdByRef", ""),
        ("integrityChecks", ()),
        ("checkMetadata", ()),
    ),
)
def test_backup_manifest_rejects_empty_provenance_and_integrity_fields(
    field_name: str,
    value: object,
) -> None:
    with pytest.raises(ValidationError):
        RecipeBackupManifest.model_validate(_valid_manifest_payload(**{field_name: value}))


def test_backup_manifest_rejects_unsafe_refs_summaries_and_status_tokens() -> None:
    with pytest.raises(ValidationError, match="private"):
        RecipeBackupScope(
            ownerId="../owner-secret",
            botId="bot_public_001",
            sessionId="session_public_001",
        )

    with pytest.raises(ValidationError, match="raw secrets"):
        RecipeBackupLedgerRef(
            ledgerRef="ledger.safe",
            ledgerHeadDigest=DIGEST_A,
            summary="Needs api_key=secret-value for live connector access.",
        )

    for credential_spelling in ("private_key=abc123456789", "secret-key=abc123456789"):
        with pytest.raises(ValidationError, match="raw secrets"):
            RecipeBackupLedgerRef(
                ledgerRef="ledger.safe",
                ledgerHeadDigest=DIGEST_A,
                summary=f"Contains {credential_spelling} in exported metadata.",
            )

    with pytest.raises(ValidationError, match="private paths"):
        RecipeBackupArtifactRef(
            artifactType="artifact_index",
            path="backups/finance-research/artifact-index.json",
            digest=DIGEST_A,
            byteSize=1,
            mediaType="application/json",
            redactionStatus="public_safe",
            summary="References infra/docker/clawy-core-agent-python/secret.py",
        )

    with pytest.raises(ValidationError, match="raw source code"):
        RecipeBackupArtifactRef(
            artifactType="artifact_index",
            path="backups/finance-research/artifact-index.json",
            digest=DIGEST_A,
            byteSize=1,
            mediaType="application/json",
            redactionStatus="public_safe",
            summary="```python\ndef run():\n    return 'unsafe'\n```",
        )

    with pytest.raises(ValidationError, match="Builder Agent"):
        RecipeBackupManifest.model_validate(
            _valid_manifest_payload(createdByRef="builder-agent.backup.validate-only")
        )

    with pytest.raises(ValidationError, match="status tokens"):
        RecipeBackupManifest.model_validate(_valid_manifest_payload(backupId="backup.live"))

    for allowed in (
        "Allows no live connector credentials.",
        "Requests no memory write authority.",
        "Grants no workspace mutation authority.",
        "No schedule mutation or cron authority is requested.",
    ):
        assert RecipeBackupLedgerRef(
            ledgerRef=f"ledger.safe.{len(allowed)}",
            ledgerHeadDigest=DIGEST_A,
            summary=allowed,
        )

    for rejected in (
        "mutate workspace files during review.",
        "write memory entries after approval.",
        "create schedules for nightly runs.",
        "deliver externally by email.",
        "Can access live connector credentials.",
        "Can restore foreign approval refs.",
        "Will restore external approval authority state.",
        "Restores schedules, cron jobs, and webhooks.",
        "Live session cursors are restored.",
        "In-flight sandbox processes are resumed.",
        "Object-store blobs missing from backup are restored.",
        "Hosted billing and quota state is restored.",
    ):
        with pytest.raises(ValidationError):
            RecipeBackupLedgerRef(
                ledgerRef="ledger.safe",
                ledgerHeadDigest=DIGEST_A,
                summary=rejected,
            )


def test_backup_manifest_rejects_deeply_encoded_hostile_values() -> None:
    for path in (
        _encoded("../secret.sqlite", 10),
        _encoded("file:///tmp/secret.sqlite", 10),
        _encoded("https://example.com/backup?X-Amz-Signature=abc", 10),
        _encoded("api_key=secret-value", 10),
        _encoded("```python\nprint('unsafe')\n```", 10),
    ):
        with pytest.raises(ValidationError):
            RecipeBackupArtifactRef(
                artifactType="sqlite_backup",
                path=path,
                digest=DIGEST_A,
                byteSize=1,
                mediaType="application/vnd.sqlite3",
                redactionStatus="redacted",
                summary="Metadata backup blob reference only.",
            )

    for summary in (
        _encoded("api_key=secret-value", 10),
        _encoded("```python\nprint('unsafe')\n```", 10),
        _encoded("Will create scheduled jobs for nightly runs.", 10),
        _encoded("Builder Agent backup manifest.", 10),
    ):
        with pytest.raises(ValidationError):
            RecipeBackupLedgerRef(
                ledgerRef="ledger.safe",
                ledgerHeadDigest=DIGEST_A,
                summary=summary,
            )


def test_backup_artifact_refs_require_strict_byte_size() -> None:
    for byte_size in (True, "1"):
        with pytest.raises(ValidationError):
            RecipeBackupArtifactRef(
                artifactType="sqlite_backup",
                path="backups/finance-research/core-agent.sqlite.backup",
                digest=DIGEST_A,
                byteSize=byte_size,
                mediaType="application/vnd.sqlite3",
                redactionStatus="redacted",
                summary="Metadata backup blob reference only.",
            )


@pytest.mark.parametrize(
    ("field_name", "value"),
    (
        ("validationMode", "apply"),
        ("validationMode", "restore"),
        ("validationMode", "promote"),
        ("validationMode", "activate"),
        ("restoreWritesEnabled", True),
        ("activationEnabled", True),
        ("runtimeActivationEligible", True),
        ("connectorCredentialsRestored", True),
        ("schedulesRestored", True),
        ("memoryWritesEnabled", True),
        ("workspaceMutationEnabled", True),
        ("liveMode", True),
    ),
)
def test_restore_validation_rejects_write_apply_promote_activate_or_live_requests(
    field_name: str,
    value: object,
) -> None:
    with pytest.raises(ValidationError):
        RecipeRestoreValidationRequest.model_validate(
            _valid_restore_request_payload(**{field_name: value})
        )


@pytest.mark.parametrize(
    "code",
    (
        "activate_live",
        "../secret",
        "api_key=secret-value",
        "```python\nprint('unsafe')\n```",
        "file%3A%2F%2F%2Ftmp%2Fsecret",
    ),
)
def test_restore_validation_blocker_code_rejects_unsafe_values(code: str) -> None:
    with pytest.raises(ValidationError):
        RecipeRestoreValidationBlocker(
            code=code,
            message="Validation blocked.",
        )


def test_constructed_invalid_instances_are_revalidated_before_digest_and_restore() -> None:
    manifest = RecipeBackupManifest.model_validate(_valid_manifest_payload())
    safe_digest = digest_recipe_backup_manifest(manifest)
    object.__setattr__(manifest, "restoreWritesEnabled", True)

    with pytest.raises(ValidationError):
        digest_recipe_backup_manifest(manifest)

    unsafe_request = BaseModel.model_construct.__func__(
        RecipeRestoreValidationRequest,
        target_scope=manifest.scope,
        backup_manifest=manifest,
        backup_digest=safe_digest,
        validation_mode="dry_run",
        restore_writes_enabled=False,
        activation_enabled=False,
        runtime_activation_eligible=False,
        connector_credentials_restored=False,
        schedules_restored=False,
        memory_writes_enabled=False,
        workspace_mutation_enabled=False,
        live_mode=False,
    )

    with pytest.raises(ValidationError):
        validate_recipe_restore_request(unsafe_request)


def test_constructed_instances_with_pydantic_extra_are_revalidated_before_digest() -> None:
    manifest = RecipeBackupManifest.model_validate(_valid_manifest_payload())
    object.__setattr__(manifest, "__pydantic_extra__", {"activationEnabled": True})

    with pytest.raises(ValidationError):
        digest_recipe_backup_manifest(manifest)


def test_backup_restore_contracts_disable_model_construct_and_model_copy_revalidates() -> None:
    manifest = RecipeBackupManifest.model_validate(_valid_manifest_payload())

    with pytest.raises(TypeError, match="model_construct is disabled"):
        RecipeBackupManifest.model_construct(**_valid_manifest_payload())

    with pytest.raises(ValidationError):
        manifest.model_copy(update={"createdByRef": "builder-agent.backup"})


def test_backup_restore_boundary_has_no_runtime_tool_model_network_deploy_or_storage_imports() -> None:
    import ast
    from pathlib import Path

    source = Path("openmagi_core_agent/authoring/backup_restore.py").read_text()
    tree = ast.parse(source)
    forbidden = (
        "tool_host",
        "ToolHost",
        "adk",
        "requests",
        "httpx",
        "openai",
        "supabase",
        "kubernetes",
        "storage",
        "runtime",
        "deploy",
        "sqlite",
        "shutil",
    )

    imported_names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_names.extend(alias.name for alias in node.names)
        if isinstance(node, ast.ImportFrom) and node.module is not None:
            imported_names.append(node.module)

    assert not [
        name for name in imported_names if any(token in name for token in forbidden)
    ]
