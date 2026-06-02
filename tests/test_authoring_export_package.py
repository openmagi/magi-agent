from __future__ import annotations

from urllib.parse import quote

import pytest
from pydantic import BaseModel, ValidationError

from openmagi_core_agent import authoring as authoring_module
from openmagi_core_agent.authoring.export_package import (
    RecipeExportGeneratedProposalRef,
    RecipeExportPackageArtifactRef,
    RecipeExportPackageManifest,
    RecipeExportPackageScope,
    RecipeExportPackageSubjectRef,
    RecipeImportValidationBlocker,
    RecipeImportValidationRequest,
    digest_recipe_export_package_manifest,
    validate_recipe_export_package_import,
)
from openmagi_core_agent.authoring.generated_proposals import (
    GeneratedProposalManifest,
    digest_generated_proposal_manifest,
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


def _valid_generated_proposal_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "schemaVersion": "generated_proposal_manifest.v1",
        "proposalId": "proposal.generated.research-helper",
        "sourceDraftRef": {
            "ref": "draft.finance-research.001",
            "digest": DIGEST_A,
        },
        "sourceVersionRef": {
            "ref": "version.finance-research.v1",
            "digest": DIGEST_B,
        },
        "sourceSnapshotRef": {
            "ref": "snapshot.finance-research.compiler-output",
            "digest": DIGEST_C,
        },
        "artifacts": (
            {
                "path": "tools/source_digest_validator.py",
                "digest": DIGEST_D,
                "byteSize": 4096,
                "mediaType": "text/x-python",
            },
        ),
        "permissionManifest": {
            "digest": DIGEST_E,
            "summary": "Requests review-only tool metadata; grants no live authority.",
        },
        "dependencyManifest": {
            "digest": DIGEST_F,
            "summary": "Uses existing runtime libraries only.",
        },
        "sandboxPlan": {
            "ref": "sandbox-plan.proposal.generated.research-helper",
            "digest": DIGEST_A,
            "summary": "Static review packet only; no execution is requested.",
        },
        "reviewRefs": ("review.proposal.generated.research-helper.required",),
        "approvalRefs": ("approval.owner-human.required",),
        "executionDefault": "denied",
    }
    payload.update(overrides)
    return payload


def _valid_package_payload(**overrides: object) -> dict[str, object]:
    proposal_manifest = GeneratedProposalManifest.model_validate(
        _valid_generated_proposal_payload()
    )
    payload: dict[str, object] = {
        "schemaVersion": "recipe_export_package.v1",
        "packageId": "recipe-package.finance-research.001",
        "sourceScope": {
            "ownerId": "owner_public_001",
            "botId": "bot_public_001",
            "sessionId": "session_public_001",
        },
        "subjects": (
            {
                "subjectType": "recipe_pack_draft",
                "ref": "draft.finance-research.001",
                "digest": DIGEST_A,
                "summary": "Review-only finance research recipe metadata; no live authority.",
            },
        ),
        "artifacts": (
            {
                "artifactType": "manifest",
                "path": "recipes/finance-research/manifest.json",
                "digest": DIGEST_B,
                "byteSize": 2048,
                "mediaType": "application/json",
                "summary": "Portable authoring metadata only; no transcript payloads.",
            },
        ),
        "generatedProposals": (
            {
                "ref": "proposal.generated.research-helper",
                "manifest": proposal_manifest.model_dump(by_alias=True),
                "manifestDigest": digest_generated_proposal_manifest(proposal_manifest),
            },
        ),
        "createdByRef": "export-tool.recipe-builder.validate-only",
    }
    payload.update(overrides)
    return payload


def _valid_import_request_payload(**overrides: object) -> dict[str, object]:
    package = RecipeExportPackageManifest.model_validate(_valid_package_payload())
    payload: dict[str, object] = {
        "targetScope": {
            "ownerId": "owner_target_001",
            "botId": "bot_target_001",
            "sessionId": "session_target_001",
        },
        "package": package.model_dump(by_alias=True),
        "packageDigest": digest_recipe_export_package_manifest(package),
        "validationMode": "validate_only",
    }
    payload.update(overrides)
    return payload


def test_export_package_is_publicly_importable_and_digest_is_deterministic() -> None:
    package = RecipeExportPackageManifest.model_validate(_valid_package_payload())
    payload_with_different_key_order = {
        "createdByRef": "export-tool.recipe-builder.validate-only",
        "generatedProposals": package.model_dump(by_alias=True)["generatedProposals"],
        **_valid_package_payload(),
    }

    assert authoring_module.RecipeExportPackageManifest is RecipeExportPackageManifest
    assert authoring_module.RecipeImportValidationRequest is RecipeImportValidationRequest
    assert package.schema_version == "recipe_export_package.v1"
    assert package.activation_enabled is False
    assert package.runtime_activation_eligible is False
    assert package.contains_credentials is False
    assert package.contains_raw_model_output is False
    assert package.generated_proposals[0].manifest.execution_default == "denied"

    assert digest_recipe_export_package_manifest(package) == (
        digest_recipe_export_package_manifest(payload_with_different_key_order)
    )
    assert digest_recipe_export_package_manifest(package).startswith("sha256:")


def test_valid_import_validation_is_validate_only_and_default_off() -> None:
    request = RecipeImportValidationRequest.model_validate(_valid_import_request_payload())

    result = validate_recipe_export_package_import(request)

    assert result.status == "valid"
    assert result.blockers == ()
    assert result.package_digest == request.package_digest
    assert result.accepted_subject_refs == ("draft.finance-research.001",)
    assert result.target_scope == request.target_scope
    assert result.activation_enabled is False
    assert result.runtime_activation_eligible is False
    assert result.import_writes_enabled is False


def test_package_digest_mismatch_blocks_without_write_or_activation_flags() -> None:
    request = RecipeImportValidationRequest.model_validate(
        _valid_import_request_payload(packageDigest=DIGEST_F)
    )

    result = validate_recipe_export_package_import(request)

    assert result.status == "blocked"
    assert result.package_digest == DIGEST_F
    assert result.accepted_subject_refs == ()
    assert result.activation_enabled is False
    assert result.runtime_activation_eligible is False
    assert result.import_writes_enabled is False
    assert result.blockers[0].code == "package_digest_mismatch"


def test_export_package_requires_at_least_one_artifact_ref() -> None:
    with pytest.raises(ValidationError, match="artifacts"):
        RecipeExportPackageManifest.model_validate(_valid_package_payload(artifacts=()))


@pytest.mark.parametrize(
    "value",
    (
        "../secrets.json",
        "recipes/../secrets.json",
        "/workspace/export.json",
        "~/.openmagi/export.json",
        "recipes\\export.json",
        "file:///tmp/export.json",
        "HTTPS:example.com/export.json",
        "s3://bucket/export.json",
        "https://example.com/export.json?X-Amz-Signature=abc123",
        "vault://secret/data/export.json",
        "recipes/%2e%2e/secrets.json",
        "file%3A%2F%2F%2Ftmp%2Fexport.json",
        "HTTPS%3Aexample.com%2Fexport.json",
        "https%3A%2F%2Fexample.com%2Fexport.json%3FX-Amz-Signature%3Dabc",
        "C:Users/export.json",
        "C%3A%5CUsers%5Csecret.json",
    ),
)
def test_export_package_rejects_private_or_credential_artifact_paths(value: str) -> None:
    with pytest.raises(ValidationError, match="path"):
        RecipeExportPackageManifest.model_validate(
            _valid_package_payload(
                artifacts=(
                    {
                        "artifactType": "manifest",
                        "path": value,
                        "digest": DIGEST_B,
                        "byteSize": 2048,
                        "mediaType": "application/json",
                        "summary": "Portable authoring metadata only.",
                    },
                )
            )
        )


@pytest.mark.parametrize(
    ("field_name", "value", "match"),
    (
        ("rawCode", "print('unsafe')", "raw generated code"),
        ("sourceCode", "def run(): pass", "raw generated code"),
        ("rawPrompt", "hidden prompt", "raw prompt/output"),
        ("rawModelOutput", "generated tool body", "raw prompt/output"),
        ("apiKey", "placeholder-secret-value", "raw credential"),
        ("runtimeEntrypoint", "main:run", "runtimeEntrypoint"),
        ("activationEnabled", True, "activation"),
        ("runtimeActivationEligible", True, "activation"),
        ("containsCredentials", True, "credential"),
        ("containsRawModelOutput", True, "raw model"),
        ("allowMemoryWrite", True, "memory"),
        ("allowWorkspaceMutation", True, "workspace"),
        ("allowExternalDelivery", True, "external"),
        ("allowScheduleMutation", True, "schedule"),
        ("liveConnectorCredentials", "secret", "connector credential"),
    ),
)
def test_export_package_rejects_executable_and_sensitive_fields(
    field_name: str,
    value: object,
    match: str,
) -> None:
    with pytest.raises(ValidationError, match=match):
        RecipeExportPackageManifest.model_validate(_valid_package_payload(**{field_name: value}))


def test_export_package_rejects_unsafe_refs_and_summaries_but_allows_no_authority() -> None:
    with pytest.raises(ValidationError, match="private"):
        RecipeExportPackageSubjectRef(
            subjectType="recipe_pack_draft",
            ref="../drafts/secret",
            digest=DIGEST_A,
            summary="Review-only metadata.",
        )

    with pytest.raises(ValidationError, match="raw secrets"):
        RecipeExportPackageSubjectRef(
            subjectType="recipe_pack_draft",
            ref="draft.safe",
            digest=DIGEST_A,
            summary="Needs api_key=secret-value for live connector access.",
        )

    with pytest.raises(ValidationError, match="private paths"):
        RecipeExportPackageSubjectRef(
            subjectType="recipe_pack_draft",
            ref="draft.safe",
            digest=DIGEST_A,
            summary="References infra/docker/clawy-core-agent-python/secret.py",
        )

    with pytest.raises(ValidationError, match="raw source code"):
        RecipeExportPackageArtifactRef(
            artifactType="manifest",
            path="recipes/finance-research/manifest.json",
            digest=DIGEST_B,
            byteSize=2048,
            mediaType="application/json",
            summary="```python\ndef run():\n    return 'unsafe'\n```",
        )

    for allowed in (
        "Allows no live connector credentials.",
        "Requests no memory write authority.",
        "Grants no workspace mutation authority.",
        "No schedule mutation or cron authority is requested.",
    ):
        assert RecipeExportPackageSubjectRef(
            subjectType="recipe_pack_draft",
            ref=f"draft.safe.{len(allowed)}",
            digest=DIGEST_A,
            summary=allowed,
        )

    for rejected in (
        "mutate workspace files during review.",
        "write memory entries after approval.",
        "create schedules for nightly runs.",
        "deliver externally by email.",
        "Can mutate workspace files during review.",
        "Can write memory entries after approval.",
        "Will create scheduled jobs for nightly runs.",
        "Will deliver results externally by email.",
    ):
        with pytest.raises(ValidationError, match="authoring authority"):
            RecipeExportPackageSubjectRef(
                subjectType="recipe_pack_draft",
                ref="draft.safe",
                digest=DIGEST_A,
                summary=rejected,
            )


def test_export_package_rejects_activation_live_runtime_status_tokens() -> None:
    for key, value in (
        ("packageId", "package.live.runtime.enabled"),
        ("createdByRef", "export-tool.runtime.promote"),
        ("createdByRef", "builder-agent.recipe-builder.validate-only"),
        ("subjects", ({"subjectType": "recipe_pack_version", "ref": "version.active", "digest": DIGEST_A, "summary": "Review-only metadata."},)),
        ("artifacts", ({"artifactType": "status", "path": "recipes/runtime/status.json", "digest": DIGEST_B, "byteSize": 12, "mediaType": "application/json", "summary": "Review-only metadata."},)),
    ):
        with pytest.raises(ValidationError):
            RecipeExportPackageManifest.model_validate(_valid_package_payload(**{key: value}))


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
def test_import_validation_blocker_code_rejects_unsafe_values(code: str) -> None:
    with pytest.raises(ValidationError):
        RecipeImportValidationBlocker(
            code=code,
            message="Validation blocked.",
        )


def test_export_package_rejects_deeply_encoded_hostile_values() -> None:
    for path in (
        _encoded("../secret.json", 10),
        _encoded("file:///tmp/export.json", 10),
        _encoded("https://example.com/export.json?X-Amz-Signature=abc", 10),
        _encoded("api_key=secret-value", 10),
        _encoded("```python\nprint('unsafe')\n```", 10),
    ):
        with pytest.raises(ValidationError):
            RecipeExportPackageManifest.model_validate(
                _valid_package_payload(
                    artifacts=(
                        {
                            "artifactType": "manifest",
                            "path": path,
                            "digest": DIGEST_B,
                            "byteSize": 2048,
                            "mediaType": "application/json",
                            "summary": "Portable authoring metadata only.",
                        },
                    )
                )
            )

    for summary in (
        _encoded("api_key=secret-value", 10),
        _encoded("```python\nprint('unsafe')\n```", 10),
        _encoded("Will create scheduled jobs for nightly runs.", 10),
    ):
        with pytest.raises(ValidationError):
            RecipeExportPackageSubjectRef(
                subjectType="recipe_pack_draft",
                ref="draft.safe",
                digest=DIGEST_A,
                summary=summary,
            )


def test_generated_proposal_manifest_digest_mismatch_rejects_fail_closed() -> None:
    with pytest.raises(ValidationError, match="manifestDigest"):
        RecipeExportGeneratedProposalRef(
            ref="proposal.generated.research-helper",
            manifest=GeneratedProposalManifest.model_validate(
                _valid_generated_proposal_payload()
            ),
            manifestDigest=DIGEST_F,
        )

    unsafe = BaseModel.model_construct.__func__(
        RecipeExportGeneratedProposalRef,
        ref="proposal.generated.research-helper",
        manifest=GeneratedProposalManifest.model_validate(_valid_generated_proposal_payload()),
        manifest_digest=DIGEST_F,
    )
    valid_package = RecipeExportPackageManifest.model_validate(_valid_package_payload())
    package = BaseModel.model_construct.__func__(
        RecipeExportPackageManifest,
        schema_version=valid_package.schema_version,
        package_id=valid_package.package_id,
        source_scope=valid_package.source_scope,
        subjects=valid_package.subjects,
        artifacts=valid_package.artifacts,
        generated_proposals=(unsafe,),
        created_by_ref=valid_package.created_by_ref,
        activation_enabled=False,
        runtime_activation_eligible=False,
        contains_credentials=False,
        contains_raw_model_output=False,
    )
    target_scope = RecipeExportPackageScope(
        ownerId="owner_target_001",
        botId="bot_target_001",
        sessionId="session_target_001",
    )
    request = BaseModel.model_construct.__func__(
        RecipeImportValidationRequest,
        target_scope=target_scope,
        package=package,
        package_digest=digest_recipe_export_package_manifest(valid_package),
        validation_mode="validate_only",
        activation_enabled=False,
        runtime_activation_eligible=False,
        import_writes_enabled=False,
        live_mode=False,
    )

    with pytest.raises(ValidationError):
        validate_recipe_export_package_import(request)


@pytest.mark.parametrize(
    "proposal_override",
    (
        {
            "artifacts": (
                {
                    "path": "HTTPS:example.com/export.py",
                    "digest": DIGEST_D,
                    "byteSize": 4096,
                    "mediaType": "text/x-python",
                },
            )
        },
        {
            "artifacts": (
                {
                    "path": "HTTPS%3Aexample.com%2Fexport.py",
                    "digest": DIGEST_D,
                    "byteSize": 4096,
                    "mediaType": "text/x-python",
                },
            )
        },
        {
            "permissionManifest": {
                "digest": DIGEST_E,
                "summary": "Builder agent review metadata only.",
            }
        },
    ),
)
def test_export_package_revalidates_nested_generated_proposal_public_safety(
    proposal_override: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        RecipeExportPackageManifest.model_validate(
            _valid_package_payload(
                generatedProposals=(
                    {
                        "ref": "proposal.generated.research-helper",
                        "manifest": _valid_generated_proposal_payload(**proposal_override),
                        "manifestDigest": DIGEST_A,
                    },
                )
            )
        )


def test_export_package_requires_strict_byte_size() -> None:
    for byte_size in (True, "1"):
        with pytest.raises(ValidationError):
            RecipeExportPackageArtifactRef(
                artifactType="manifest",
                path="recipes/finance-research/manifest.json",
                digest=DIGEST_B,
                byteSize=byte_size,
                mediaType="application/json",
                summary="Portable authoring metadata only.",
            )


def test_constructed_invalid_instances_are_revalidated_before_digest_and_import() -> None:
    unsafe = BaseModel.model_construct.__func__(
        RecipeExportPackageManifest,
        **_valid_package_payload(
            packageId="package.live.runtime.enabled",
            activationEnabled=True,
        ),
    )

    with pytest.raises(ValidationError):
        RecipeExportPackageManifest.model_validate(unsafe)

    with pytest.raises(ValidationError):
        digest_recipe_export_package_manifest(unsafe)

    with pytest.raises(ValidationError):
        RecipeImportValidationRequest.model_validate(
            _valid_import_request_payload(package=unsafe, packageDigest=DIGEST_A)
        )


def test_export_package_contracts_disable_model_construct_and_model_copy_revalidates() -> None:
    package = RecipeExportPackageManifest.model_validate(_valid_package_payload())

    with pytest.raises(TypeError, match="model_construct is disabled"):
        RecipeExportPackageManifest.model_construct(**_valid_package_payload())

    with pytest.raises(ValidationError):
        package.model_copy(update={"activationEnabled": True})


@pytest.mark.parametrize(
    ("field_name", "value"),
    (
        ("validationMode", "apply"),
        ("validationMode", "promote"),
        ("validationMode", "activate"),
        ("importWritesEnabled", True),
        ("activationEnabled", True),
        ("runtimeActivationEligible", True),
        ("liveMode", True),
    ),
)
def test_import_validation_rejects_write_apply_promote_activate_or_live_requests(
    field_name: str,
    value: object,
) -> None:
    with pytest.raises(ValidationError):
        RecipeImportValidationRequest.model_validate(
            _valid_import_request_payload(**{field_name: value})
        )


def test_export_package_boundary_has_no_runtime_tool_model_network_or_deploy_imports() -> None:
    import ast
    from pathlib import Path

    source = Path("openmagi_core_agent/authoring/export_package.py").read_text()
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
