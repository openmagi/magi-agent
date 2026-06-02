from __future__ import annotations

from urllib.parse import quote

import pytest
from pydantic import BaseModel, ValidationError

from magi_agent import authoring as authoring_module
from magi_agent.authoring.generated_proposals import (
    GeneratedProposalArtifactFileRef,
    GeneratedProposalDigestSummaryRef,
    GeneratedProposalManifest,
    GeneratedProposalSourceRef,
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


def _valid_manifest_payload(**overrides: object) -> dict[str, object]:
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


def test_generated_proposal_manifest_is_publicly_importable_and_non_executable() -> None:
    manifest = GeneratedProposalManifest.model_validate(_valid_manifest_payload())

    assert authoring_module.GeneratedProposalManifest is GeneratedProposalManifest
    assert authoring_module.GeneratedProposalArtifactFileRef is GeneratedProposalArtifactFileRef
    assert manifest.schema_version == "generated_proposal_manifest.v1"
    assert manifest.proposal_id == "proposal.generated.research-helper"
    assert manifest.execution_default == "denied"
    assert manifest.artifacts[0].executable is False

    dumped = manifest.model_dump(by_alias=True)
    assert dumped["executionDefault"] == "denied"
    assert dumped["artifacts"][0] == {
        "path": "tools/source_digest_validator.py",
        "digest": DIGEST_D,
        "byteSize": 4096,
        "mediaType": "text/x-python",
        "executable": False,
    }
    assert "rawCode" not in str(dumped)
    assert "runtimeEntrypoint" not in str(dumped)


def test_generated_proposal_manifest_digest_is_deterministic() -> None:
    manifest = GeneratedProposalManifest.model_validate(_valid_manifest_payload())
    payload_with_different_key_order = {
        "approvalRefs": ("approval.owner-human.required",),
        "reviewRefs": ("review.proposal.generated.research-helper.required",),
        **_valid_manifest_payload(),
    }

    assert digest_generated_proposal_manifest(manifest) == (
        digest_generated_proposal_manifest(payload_with_different_key_order)
    )
    assert digest_generated_proposal_manifest(manifest).startswith("sha256:")


@pytest.mark.parametrize(
    "path",
    (
        "../secrets.py",
        "tools/../secrets.py",
        "/workspace/tools/plugin.py",
        "~/.openmagi/plugin.py",
        "tools\\plugin.py",
        "file:///tmp/plugin.py",
        "HTTPS:example.com/plugin.py",
        "https://example.com/plugin.py?X-Amz-Signature=abc123",
        "vault://secret/data/plugin.py",
        "tools/%2e%2e/secrets.py",
        "file%3A%2F%2F%2Ftmp%2Fplugin.py",
        "HTTPS%3Aexample.com%2Fplugin.py",
        "https%3A%2F%2Fexample.com%2Fplugin.py%3FX-Amz-Signature%3Dabc",
        "C:Users/plugin.py",
        "C%3A%5CUsers%5Csecret.py",
    ),
)
def test_generated_proposal_file_refs_reject_private_or_credential_paths(path: str) -> None:
    payload = _valid_manifest_payload(
        artifacts=(
            {
                "path": path,
                "digest": DIGEST_D,
                "byteSize": 12,
                "mediaType": "text/plain",
            },
        )
    )

    with pytest.raises(ValidationError, match="path"):
        GeneratedProposalManifest.model_validate(payload)


@pytest.mark.parametrize(
    ("field_name", "value", "match"),
    (
        ("rawCode", "print('unsafe')", "raw generated code"),
        ("rawPrompt", "hidden prompt", "raw prompt/output"),
        ("rawModelOutput", "generated tool body", "raw prompt/output"),
        ("apiKey", "placeholder-secret-value", "raw credential"),
        ("runtimeEntrypoint", "main:run", "runtimeEntrypoint"),
        ("activationEnabled", True, "activation"),
        ("allowMemoryWrite", True, "memory"),
        ("allowWorkspaceMutation", True, "workspace"),
        ("allowExternalDelivery", True, "external"),
        ("allowScheduleMutation", True, "schedule"),
        ("liveConnectorCredentials", "secret", "connector credential"),
        ("builderAgentId", "builder-agent.generated", "Builder Agent"),
    ),
)
def test_generated_proposal_manifest_rejects_executable_and_sensitive_fields(
    field_name: str,
    value: object,
    match: str,
) -> None:
    with pytest.raises(ValidationError, match=match):
        GeneratedProposalManifest.model_validate(
            _valid_manifest_payload(**{field_name: value})
        )


def test_generated_proposal_manifest_rejects_raw_source_in_file_metadata() -> None:
    payload = _valid_manifest_payload(
        artifacts=(
            {
                "path": "tools/source_digest_validator.py",
                "digest": DIGEST_D,
                "byteSize": 4096,
                "mediaType": "text/x-python",
                "rawCode": "print('not allowed')",
            },
        )
    )

    with pytest.raises(ValidationError, match="raw generated code"):
        GeneratedProposalManifest.model_validate(payload)


def test_generated_proposal_manifest_requires_denied_execution_default() -> None:
    with pytest.raises(ValidationError, match="executionDefault"):
        GeneratedProposalManifest.model_validate(
            _valid_manifest_payload(executionDefault="review")
        )

    with pytest.raises(ValidationError, match="executable"):
        GeneratedProposalArtifactFileRef(
            path="tools/source_digest_validator.py",
            digest=DIGEST_D,
            byteSize=4096,
            mediaType="text/x-python",
            executable=True,
        )


def test_generated_proposal_manifest_rejects_unsafe_refs_and_summaries() -> None:
    with pytest.raises(ValidationError, match="private"):
        GeneratedProposalSourceRef(
            ref="../drafts/secret",
            digest=DIGEST_A,
        )

    with pytest.raises(ValidationError, match="raw secrets"):
        GeneratedProposalDigestSummaryRef(
            digest=DIGEST_B,
            summary="Needs api_key=secret-value for live connector access.",
        )

    with pytest.raises(ValidationError, match="private paths"):
        GeneratedProposalDigestSummaryRef(
            digest=DIGEST_B,
            summary=(
                "Review notes reference "
                "magi-agent/magi_agent/secret.py"
            ),
        )

    with pytest.raises(ValidationError, match="raw source code"):
        GeneratedProposalDigestSummaryRef(
            digest=DIGEST_B,
            summary="```python\ndef run():\n    return 'unsafe'\n```",
        )

    with pytest.raises(ValidationError, match="raw source code"):
        GeneratedProposalDigestSummaryRef(
            digest=DIGEST_B,
            summary="#!/bin/sh\necho unsafe",
        )

    with pytest.raises(ValidationError, match="authoring authority"):
        GeneratedProposalDigestSummaryRef(
            digest=DIGEST_B,
            summary="Grants workspace mutation and memory write authority.",
        )

    with pytest.raises(ValidationError, match="authoring authority"):
        GeneratedProposalDigestSummaryRef(
            digest=DIGEST_B,
            summary="Allows live connector credentials for external delivery.",
        )

    with pytest.raises(ValidationError, match="authoring authority"):
        GeneratedProposalDigestSummaryRef(
            digest=DIGEST_B,
            summary="Requests workspace_mutation and memory_write authority.",
        )

    with pytest.raises(ValidationError, match="authoring authority"):
        GeneratedProposalDigestSummaryRef(
            digest=DIGEST_B,
            summary="May create cron schedules and external delivery.",
        )

    with pytest.raises(ValidationError, match="Builder Agent"):
        GeneratedProposalDigestSummaryRef(
            digest=DIGEST_B,
            summary="Builder agent review metadata only.",
        )

    for allowed in (
        "Allows no live connector credentials.",
        "Requests no memory write authority.",
        "Grants no workspace mutation authority.",
        "No schedule mutation or cron authority is requested.",
    ):
        assert GeneratedProposalDigestSummaryRef(digest=DIGEST_B, summary=allowed)

    for rejected in (
        "mutate workspace files during review.",
        "write memory entries after approval.",
        "create schedules for nightly runs.",
        "deliver externally by email.",
        "Can mutate the workspace during review.",
        "Can mutate workspace files during review.",
        "Can write to memory after approval.",
        "Can write memory entries after approval.",
        "Will create scheduled jobs for nightly runs.",
        "Will create schedules for nightly runs.",
        "Will externally deliver results by email.",
        "Will deliver results externally by email.",
    ):
        with pytest.raises(ValidationError, match="authoring authority"):
            GeneratedProposalDigestSummaryRef(digest=DIGEST_B, summary=rejected)


def test_generated_proposal_manifest_rejects_missing_source_refs() -> None:
    payload = _valid_manifest_payload()
    payload.pop("sourceVersionRef")

    with pytest.raises(ValidationError, match="sourceVersionRef"):
        GeneratedProposalManifest.model_validate(payload)

    payload = _valid_manifest_payload()
    payload.pop("sourceSnapshotRef")

    with pytest.raises(ValidationError, match="sourceSnapshotRef"):
        GeneratedProposalManifest.model_validate(payload)


def test_generated_proposal_manifest_rejects_runtime_status_ref_tokens() -> None:
    for key, value in (
        ("proposalId", "proposal.live.runtime.enabled"),
        ("reviewRefs", ("review.promote.to.runtime",)),
        ("approvalRefs", ("approval.live.active",)),
        ("reviewRefs", ("review/..",)),
        ("sourceDraftRef", {"ref": "drafts/%2e%2e/secret", "digest": DIGEST_A}),
        ("sandboxPlan", {"ref": "sandbox.runtime.live", "digest": DIGEST_A, "summary": "No execution requested."}),
    ):
        with pytest.raises(ValidationError):
            GeneratedProposalManifest.model_validate(_valid_manifest_payload(**{key: value}))


def test_generated_proposal_manifest_rejects_deeply_encoded_hostile_values() -> None:
    for path in (
        _encoded("../secret.py", 10),
        _encoded("file:///tmp/plugin.py", 10),
        _encoded("https://example.com/plugin.py?X-Amz-Signature=abc", 10),
        _encoded("api_key=secret-value", 10),
        _encoded("```python\nprint('unsafe')\n```", 10),
    ):
        with pytest.raises(ValidationError):
            GeneratedProposalManifest.model_validate(
                _valid_manifest_payload(
                    artifacts=(
                        {
                            "path": path,
                            "digest": DIGEST_D,
                            "byteSize": 4096,
                            "mediaType": "text/x-python",
                        },
                    )
                )
            )

    for summary in (
        _encoded("api_key=secret-value", 10),
        _encoded("```python\nprint('unsafe')\n```", 10),
    ):
        with pytest.raises(ValidationError):
            GeneratedProposalDigestSummaryRef(digest=DIGEST_B, summary=summary)


def test_generated_proposal_manifest_requires_strict_byte_size() -> None:
    for byte_size in (True, "1"):
        payload = _valid_manifest_payload(
            artifacts=(
                {
                    "path": "tools/source_digest_validator.py",
                    "digest": DIGEST_D,
                    "byteSize": byte_size,
                    "mediaType": "text/x-python",
                },
            )
        )
        with pytest.raises(ValidationError):
            GeneratedProposalManifest.model_validate(payload)


def test_generated_proposal_digest_revalidates_constructed_instances() -> None:
    unsafe = BaseModel.model_construct.__func__(
        GeneratedProposalManifest,
        **_valid_manifest_payload(
            artifacts=(
                {
                    "path": "tools/%2e%2e/secrets.py",
                    "digest": DIGEST_D,
                    "byteSize": 4096,
                    "mediaType": "text/x-python",
                    "executable": True,
                },
            )
        ),
    )

    with pytest.raises(ValidationError):
        GeneratedProposalManifest.model_validate(unsafe)

    with pytest.raises(ValidationError):
        digest_generated_proposal_manifest(unsafe)


def test_generated_proposal_contracts_disable_model_construct() -> None:
    with pytest.raises(TypeError, match="model_construct is disabled"):
        GeneratedProposalManifest.model_construct(**_valid_manifest_payload())
