from __future__ import annotations

import ast
import base64
from pathlib import Path
from urllib.parse import quote

import pytest
from pydantic import BaseModel, ValidationError

from openmagi_core_agent import authoring as authoring_module
from openmagi_core_agent.authoring.audit_events import (
    RecipeBuilderAuditBatch,
    RecipeBuilderAuditEvent,
    RecipeBuilderAuditEventRef,
    RecipeBuilderAuditScope,
    digest_recipe_builder_audit_batch,
    digest_recipe_builder_audit_event,
    validate_recipe_builder_audit_batch,
)


DIGEST_A = "sha256:" + "a" * 64
DIGEST_B = "sha256:" + "b" * 64
DIGEST_C = "sha256:" + "c" * 64
DIGEST_D = "sha256:" + "d" * 64


def _encoded(value: str, rounds: int) -> str:
    for _ in range(rounds):
        value = quote(value, safe="")
    return value


def _base64(value: str, *, urlsafe: bool = False) -> str:
    raw = value.encode("utf-8")
    if urlsafe:
        return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
    return base64.b64encode(raw).decode("ascii")


def _mime_split_base64(value: str) -> str:
    encoded = _base64(value)
    return " ".join(encoded[index : index + 4] for index in range(0, len(encoded), 4))


def _split_base64(value: str, chunk_size: int) -> str:
    encoded = _base64(value)
    return " ".join(
        encoded[index : index + chunk_size]
        for index in range(0, len(encoded), chunk_size)
    )


def _scope_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "ownerId": "owner_public_001",
        "botId": "bot_public_001",
        "sessionId": "session_public_001",
    }
    payload.update(overrides)
    return payload


def _event_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "schemaVersion": "recipe_builder_audit_event.v1",
        "scope": _scope_payload(),
        "eventId": "audit.event.001",
        "eventType": "draft_saved",
        "subjectRef": "draft.finance-research.001",
        "subjectDigest": DIGEST_A,
        "policyDigest": DIGEST_B,
        "artifactDigests": (DIGEST_C,),
        "artifactRefs": ("artifact.recipe-pack.manifest",),
        "redactionStatus": "redacted",
        "summary": "Digest-only audit metadata; no live authority.",
    }
    payload.update(overrides)
    return payload


def _batch_payload(**overrides: object) -> dict[str, object]:
    event = RecipeBuilderAuditEvent.model_validate(_event_payload())
    payload: dict[str, object] = {
        "schemaVersion": "recipe_builder_audit_batch.v1",
        "scope": _scope_payload(),
        "events": (event.model_dump(by_alias=True),),
        "eventRefs": (
            {
                "eventId": "audit.event.001",
                "eventType": "draft_saved",
                "eventDigest": digest_recipe_builder_audit_event(event),
            },
        ),
    }
    payload.update(overrides)
    return payload


def test_audit_events_are_publicly_importable_and_digest_is_deterministic() -> None:
    event = RecipeBuilderAuditEvent.model_validate(_event_payload())
    payload_with_different_key_order = {
        "summary": "Digest-only audit metadata; no live authority.",
        "redactionStatus": "redacted",
        **_event_payload(),
    }

    assert authoring_module.RecipeBuilderAuditScope is RecipeBuilderAuditScope
    assert authoring_module.RecipeBuilderAuditEvent is RecipeBuilderAuditEvent
    assert authoring_module.RecipeBuilderAuditBatch is RecipeBuilderAuditBatch
    assert authoring_module.RecipeBuilderAuditEventRef is RecipeBuilderAuditEventRef
    assert event.schema_version == "recipe_builder_audit_event.v1"
    assert event.activation_enabled is False
    assert event.runtime_activation_eligible is False
    assert event.connector_credentials_accessed is False
    assert event.connector_credentials_restored is False
    assert event.schedules_restored is False
    assert event.schedule_mutation_enabled is False
    assert event.memory_writes_enabled is False
    assert event.workspace_mutation_enabled is False
    assert event.external_delivery_enabled is False
    assert event.live_mode is False

    assert digest_recipe_builder_audit_event(event) == (
        digest_recipe_builder_audit_event(payload_with_different_key_order)
    )
    assert digest_recipe_builder_audit_event(event).startswith("sha256:")


def test_valid_batch_with_matching_scope_returns_accepted_event_refs_default_off() -> None:
    batch = RecipeBuilderAuditBatch.model_validate(_batch_payload())

    result = validate_recipe_builder_audit_batch(batch)

    assert result.status == "valid"
    assert result.validation_mode == "validate_only"
    assert result.scope == batch.scope
    assert result.accepted_event_refs == batch.event_refs
    assert result.event_count == 1
    assert result.activation_enabled is False
    assert result.runtime_activation_eligible is False
    assert result.connector_credentials_accessed is False
    assert result.connector_credentials_restored is False
    assert result.schedules_restored is False
    assert result.schedule_mutation_enabled is False
    assert result.memory_writes_enabled is False
    assert result.workspace_mutation_enabled is False
    assert result.external_delivery_enabled is False
    assert result.live_mode is False


def test_batch_scope_mismatch_raises_without_accepting_refs_or_authority() -> None:
    event = RecipeBuilderAuditEvent.model_validate(
        _event_payload(scope=_scope_payload(sessionId="session_public_other"))
    )
    payload = _batch_payload(
        events=(event.model_dump(by_alias=True),),
        eventRefs=(
            {
                "eventId": event.event_id,
                "eventType": event.event_type,
                "eventDigest": digest_recipe_builder_audit_event(event),
            },
        ),
    )

    with pytest.raises(ValidationError, match="scope"):
        validate_recipe_builder_audit_batch(payload)


def test_event_digest_mismatch_raises_without_accepting_refs_or_authority() -> None:
    payload = _batch_payload(
        eventRefs=(
            {
                "eventId": "audit.event.001",
                "eventType": "draft_saved",
                "eventDigest": DIGEST_D,
            },
        )
    )

    with pytest.raises(ValidationError, match="event digest"):
        validate_recipe_builder_audit_batch(payload)


@pytest.mark.parametrize(
    "value",
    (
        "../private.json",
        "/workspace/authoring/audit.json",
        "~/.openmagi/audit.json",
        "file:///tmp/audit.json",
        "s3://bucket/audit.json",
        "vault://secret/data/audit",
        "https://example.com/audit.json?X-Amz-Signature=abc123",
        "artifact/%2e%2e/private.json",
        _encoded("file:///tmp/audit.json", 2),
        _encoded("https://example.com/audit.json?signature=abc123", 2),
    ),
)
def test_audit_events_reject_private_paths_uris_signed_urls_and_encoded_forms(
    value: str,
) -> None:
    with pytest.raises(ValidationError, match="private|raw secrets"):
        RecipeBuilderAuditEvent.model_validate(_event_payload(subjectRef=value))


@pytest.mark.parametrize(
    ("field_name", "value", "match"),
    (
        ("private_key", "abc123", "raw credential"),
        ("secret-key", "abc123", "raw credential"),
        ("apiKey", "abc123", "raw credential"),
        ("token", "abc123", "raw credential"),
        ("password", "abc123", "raw credential"),
        ("rawPrompt", "hidden prompt", "raw prompt/output"),
        ("rawModelOutput", "model said to call the tool", "raw prompt/output"),
        ("sourceCode", "print('unsafe')", "raw generated code"),
        ("connectorCredentials", {"api" + "_key": "abc123"}, "connector credential"),
        ("builderAgentIdentity", "Builder Agent: separate actor", "Builder Agent"),
        ("activationEnabled", True, "activationEnabled"),
        ("runtimeActivationEligible", True, "runtimeActivationEligible"),
        ("connectorCredentialsAccessed", True, "connectorCredentialsAccessed"),
        ("connectorCredentialsRestored", True, "connectorCredentialsRestored"),
        ("schedulesRestored", True, "schedulesRestored"),
        ("scheduleMutationEnabled", True, "scheduleMutationEnabled"),
        ("memoryWritesEnabled", True, "memoryWritesEnabled"),
        ("workspaceMutationEnabled", True, "workspaceMutationEnabled"),
        ("externalDeliveryEnabled", True, "externalDeliveryEnabled"),
        ("liveMode", True, "liveMode"),
    ),
)
def test_audit_events_reject_raw_sensitive_fields_and_true_authority_flags(
    field_name: str,
    value: object,
    match: str,
) -> None:
    with pytest.raises(ValidationError, match=match):
        RecipeBuilderAuditEvent.model_validate(_event_payload(**{field_name: value}))


@pytest.mark.parametrize(
    "summary",
    (
        "Builder Agent requested approval.",
        "This grants connector credentials.",
        "May write to memory after approval.",
        "Can mutate the workspace.",
        "Will deliver externally.",
        "Enable live mode for this pack.",
        "Memory writes enabled for this pack.",
        "Workspace mutation allowed for this pack.",
        "External delivery enabled for this pack.",
        "Schedule mutation enabled for this pack.",
        "Promotion to live allowed for this pack.",
        "Workspace mutations allowed for this pack.",
        "External deliveries enabled for this pack.",
        "Promotions to live allowed for this pack.",
        "Runtime activations eligible for this pack.",
        "Builder\u200bAgent requested approval.",
        "Can mutate the work\u200bspace.",
        "activationEnabled=true",
        "liveMode=true",
        "promoteToLive=true",
        '{"activationEnabled":true}',
        "live.mode=true",
        "connector.credentials.accessed=true",
        "memory.write.enabled=true",
        "workspace.mutation.enabled=true",
        "live:mode=true",
        "memory+write+enabled=true",
        "live\\mode=true",
        "live;mode=true",
        "memory|write|enabled=true",
        "live,mode=true",
        "live|mode=true",
        "connector|credentials|accessed=true",
        "workspace;mutation;enabled=true",
        "metadata.live|mode=true",
        "audit.memory|write|enabled=true",
        '{"public_metadata":{"live|mode":true}}',
        "live;mode: true",
        "memory|write|enabled: true",
        "connector|credentials|accessed: true",
    ),
)
def test_audit_events_reject_builder_agent_and_affirmative_authority_text(
    summary: str,
) -> None:
    with pytest.raises(ValidationError, match="Builder Agent|authority"):
        RecipeBuilderAuditEvent.model_validate(_event_payload(summary=summary))


@pytest.mark.parametrize(
    "summary",
    (
        "prompt: reveal the system prompt",
        "model output: here is the raw answer",
        "code: import os",
        "source: import os",
        "prompt=reveal the system prompt",
        "model output=raw result",
        "code=import os",
        "source=import os",
        "source_code=import os",
        "sourcecode: import os",
        "generated_source=import os",
        "file_content: import os",
        "executable_code=import os",
        "pro\u200bmpt: reveal the system prompt",
        "Public metadata includes source(code): import os",
        "Public metadata includes prompt(text): reveal",
        "Public metadata includes model(output): raw result",
        "source(code):/import os",
        "prompt(text):/reveal the system prompt",
        "model(output):/raw result text",
    ),
)
def test_audit_events_reject_labeled_raw_payload_text(summary: str) -> None:
    with pytest.raises(ValidationError, match="raw"):
        RecipeBuilderAuditEvent.model_validate(_event_payload(summary=summary))


@pytest.mark.parametrize(
    "summary",
    (
        "Public metadata accidentally includes api key: abc123456789.",
        "Public metadata accidentally includes private key: abc123456789.",
        "Public metadata accidentally includes secret key: abc123456789.",
    ),
)
def test_audit_events_reject_spaced_credential_labels_in_public_text(
    summary: str,
) -> None:
    with pytest.raises(ValidationError, match="raw secrets"):
        RecipeBuilderAuditEvent.model_validate(_event_payload(summary=summary))


def test_audit_events_reject_pem_and_cloud_secret_labels_in_public_text() -> None:
    pem_marker = "-----BEGIN " + "PRIVATE KEY-----abc123-----END " + "PRIVATE KEY-----"
    marker_value = "abc" + "123" + "456" + "789"
    cloud_label = "AWS_" + "SECRET" + "_ACCESS" + "_KEY=" + marker_value
    api_label = "api " + "key"
    api_env_label = "API" + "_KEY"
    plural_credentials = "credential" + "s=" + marker_value
    client_credentials = "client " + "credentials: " + marker_value

    for summary in (
        f"Public metadata accidentally includes {pem_marker}.",
        f"Public metadata accidentally includes {cloud_label}.",
        f"Public metadata accidentally includes {plural_credentials}.",
        f"Public metadata accidentally includes {client_credentials}.",
        "Public metadata accidentally includes api key abc123456789.",
        "Public metadata accidentally includes private key abc123456789.",
        "Public metadata accidentally includes token abc123456789.",
        f'Public metadata accidentally includes {api_label}: "{marker_value}".',
        f'Public metadata accidentally includes {api_env_label}="{marker_value}".',
        f'Public metadata accidentally includes {{"{api_env_label.lower()}":"{marker_value}"}}.',
        "Public metadata accidentally includes api\u200bkey abc123456789.",
        "Public metadata accidentally includes api.key=abc123456789.",
        "Public metadata accidentally includes private.key=abc123456789.",
        "Public metadata accidentally includes api/key=abc123456789.",
        "Public metadata accidentally includes api:key=abc123456789.",
        "Public metadata accidentally includes api+key=abc123456789.",
        "Public metadata accidentally includes api\\key=abc123456789.",
        "Public metadata accidentally includes api;key=abc123456789.",
        "Public metadata accidentally includes api|key=abc123456789.",
        "Public metadata accidentally includes api,key=abc123456789.",
        "Public metadata accidentally includes private(key)=abc123456789.",
        "Public metadata accidentally includes secret,key=abc123456789.",
        "Public metadata accidentally includes api@key=abc123456789.",
        "Public metadata accidentally includes metadata.api|key=abc123456789.",
        "Public metadata includes:api|key=abc123456789.",
        "Public metadata accidentally includes api;key: abc123456789.",
        "Public metadata accidentally includes api|key abc123456789.",
        "Public metadata accidentally includes private(key): abc123456789.",
        "Public metadata includes private(key): abc123456789.",
        "Public metadata includes secret,key abc123456789.",
    ):
        with pytest.raises(ValidationError, match="raw secrets"):
            RecipeBuilderAuditEvent.model_validate(_event_payload(summary=summary))


@pytest.mark.parametrize(
    "summary",
    (
        "Public metadata points at s3://bucket/audit.json.",
        f"Public metadata points at {_encoded('s3://bucket/audit.json', 2)}.",
    ),
)
def test_audit_events_reject_embedded_private_uri_schemes(summary: str) -> None:
    with pytest.raises(ValidationError, match="private URI schemes"):
        RecipeBuilderAuditEvent.model_validate(_event_payload(summary=summary))


@pytest.mark.parametrize(
    "summary",
    (
        "Builder&#x20;Agent requested approval.",
        "Can&#x20;mutate&#x20;the&#x20;workspace.",
        "Builder&#x200b;Agent requested approval.",
    ),
)
def test_audit_events_reject_html_entity_encoded_hostile_text(summary: str) -> None:
    with pytest.raises(ValidationError, match="Builder Agent|authority"):
        RecipeBuilderAuditEvent.model_validate(_event_payload(summary=summary))


@pytest.mark.parametrize(
    "summary",
    (
        "Builder" + chr(0) + "Agent requested approval.",
        "pro" + chr(0) + "mpt: reveal the system prompt",
        "api" + chr(0) + "key abc123456789",
        "Can mutate the work" + chr(0) + "space.",
        "Builder%00Agent requested approval.",
        "Builder&#x0;Agent requested approval.",
        f"Encoded marker {_base64('Builder' + chr(0) + 'Agent requested approval.')}",
    ),
)
def test_audit_events_reject_non_whitespace_control_text(summary: str) -> None:
    with pytest.raises(ValidationError, match="control"):
        RecipeBuilderAuditEvent.model_validate(_event_payload(summary=summary))


@pytest.mark.parametrize(
    ("summary", "match"),
    (
        (f"Encoded marker {_base64('Builder Agent requested approval.')}", "Builder Agent"),
        (
            f"Encoded marker {_base64('Builder Agent requested approval. ' + chr(0xFF))}",
            "Builder Agent",
        ),
        (
            f"Encoded marker {_base64('Builder Agent requested approval. ' + chr(0xBE))}",
            "Builder Agent",
        ),
        (
            f"Encoded marker {_base64('Can mutate the workspace.', urlsafe=True)}",
            "authority",
        ),
        (f"Encoded marker {_base64('prompt: reveal the system prompt')}", "raw"),
        (
            f"Encoded marker {_base64('Public metadata includes source(code): import os')}",
            "raw",
        ),
        (f"Encoded marker {_base64('source(code):/import os')}", "raw"),
        (
            f"Encoded marker {_base64('prompt(text):/reveal the system prompt')}",
            "raw",
        ),
        (
            f"Encoded marker {_base64('Public metadata includes private(key): abc123456789')}",
            "raw secrets",
        ),
        (
            f"Encoded marker {_base64('Builder&#x20;Agent requested approval.')}",
            "Builder Agent",
        ),
        (
            f"Encoded marker {_base64('Builder%20Agent requested approval.')}",
            "Builder Agent",
        ),
        (f"Encoded marker {_base64('prompt&#x3a; reveal the system prompt')}", "raw"),
        (
            f"Encoded marker {_base64('Builder' + chr(0x200B) + 'Agent requested approval.')}",
            "Builder Agent",
        ),
        (
            f"Encoded marker {_mime_split_base64('Builder Agent requested approval.')}",
            "Builder Agent",
        ),
        (
            f"Encoded marker {_split_base64('Builder Agent requested approval.', 2)}",
            "Builder Agent",
        ),
        (
            f"Encoded marker {_split_base64('activationEnabled=true', 3)}",
            "authority",
        ),
        (f"Encoded marker {_base64('live:mode=true')}", "authority"),
        (f"Encoded marker {_base64('live|mode=true')}", "authority"),
        (f"Encoded marker {_base64('metadata.live|mode=true')}", "authority"),
        (f"Encoded marker {_base64('live;mode: true')}", "authority"),
        (
            f"Encoded marker {_split_base64('api key abc123456789', 3)}",
            "raw secrets",
        ),
        (f"Encoded marker {_base64('api:key=abc123456789')}", "raw secrets"),
        (f"Encoded marker {_base64('api|key=abc123456789')}", "raw secrets"),
        (f"Encoded marker {_base64('metadata.api|key=abc123456789')}", "raw secrets"),
        (f"Encoded marker {_base64('api;key: abc123456789')}", "raw secrets"),
        (
            f"Encoded marker {_split_base64('s3://bucket/audit.json', 2)}",
            "private",
        ),
        (f"Encoded marker {_base64('s3:x', urlsafe=True)}", "private"),
    ),
)
def test_audit_events_reject_base64_encoded_hostile_text(
    summary: str,
    match: str,
) -> None:
    with pytest.raises(ValidationError, match=match):
        RecipeBuilderAuditEvent.model_validate(_event_payload(summary=summary))


@pytest.mark.parametrize(
    ("summary", "match"),
    (
        (
            f"Encoded marker {_base64(_base64('Builder Agent requested approval.'))}",
            "Builder Agent",
        ),
        (f"Encoded marker {_base64(_base64('prompt: reveal the system prompt'))}", "raw"),
        (
            "Encoded marker "
            f"{_base64(_base64('Can mutate the workspace.', urlsafe=True), urlsafe=True)}",
            "authority",
        ),
        (f"Encoded marker {_base64('code:x')}", "raw"),
        (f"Encoded marker {_base64('file:a')}", "raw|private"),
        (f"Encoded marker {_mime_split_base64('code:x')}", "raw"),
        (f"Encoded marker {_mime_split_base64('file:a')}", "raw|private"),
        ("Encoded marker Y 29kZTp4", "raw"),
        ("Encoded marker Z mlsZTph", "raw|private"),
    ),
)
def test_audit_events_reject_multi_hop_and_short_base64_hostile_text(
    summary: str,
    match: str,
) -> None:
    with pytest.raises(ValidationError, match=match):
        RecipeBuilderAuditEvent.model_validate(_event_payload(summary=summary))


def test_event_schema_uses_strict_boolean_and_int_validation() -> None:
    with pytest.raises(ValidationError, match="activationEnabled"):
        RecipeBuilderAuditEvent.model_validate(
            _event_payload(activationEnabled=0)
        )

    with pytest.raises(ValidationError, match="eventCount"):
        validate_recipe_builder_audit_batch(_batch_payload(eventCount=True))


def test_model_construct_is_disabled_and_model_copy_revalidates() -> None:
    event = RecipeBuilderAuditEvent.model_validate(_event_payload())

    with pytest.raises(TypeError, match="model_construct is disabled"):
        RecipeBuilderAuditEvent.model_construct(**_event_payload())

    with pytest.raises(ValidationError, match="activationEnabled"):
        event.model_copy(update={"activationEnabled": True})


def test_constructed_and_tampered_basemodel_extras_are_rejected_by_digest_and_batch() -> None:
    event = RecipeBuilderAuditEvent.model_validate(_event_payload())
    tampered = BaseModel.model_construct.__func__(
        RecipeBuilderAuditEvent,
        **event.model_dump(by_alias=True),
    )
    tampered.__dict__["rawPrompt"] = "hidden prompt"
    object.__setattr__(
        tampered,
        "__pydantic_extra__",
        {"private" + "_key": "abc123"},
    )

    with pytest.raises(ValidationError, match="raw prompt/output|raw credential"):
        digest_recipe_builder_audit_event(tampered)

    with pytest.raises(ValidationError, match="raw prompt/output|raw credential"):
        validate_recipe_builder_audit_batch(
            _batch_payload(events=(tampered,))
        )


def test_constructed_non_mapping_hidden_pydantic_state_is_rejected() -> None:
    event = RecipeBuilderAuditEvent.model_validate(_event_payload())
    object.__setattr__(event, "__pydantic_extra__", "api_key=abc123456789")

    with pytest.raises(ValidationError):
        digest_recipe_builder_audit_event(event)

    private_event = RecipeBuilderAuditEvent.model_validate(_event_payload())
    object.__setattr__(
        private_event,
        "__pydantic_private__",
        "private key: abc123456789",
    )

    with pytest.raises(ValidationError):
        digest_recipe_builder_audit_event(private_event)


def test_constructed_hidden_state_cannot_mask_declared_authority_fields() -> None:
    event = RecipeBuilderAuditEvent.model_validate(_event_payload())
    object.__setattr__(event, "activation_enabled", True)
    object.__setattr__(event, "__pydantic_extra__", {"activationEnabled": False})

    with pytest.raises(ValidationError, match="activationEnabled"):
        digest_recipe_builder_audit_event(event)

    shadow_event = RecipeBuilderAuditEvent.model_validate(_event_payload())
    object.__setattr__(shadow_event, "activation_enabled", True)
    shadow_event.__dict__["activationEnabled"] = False

    with pytest.raises(ValidationError, match="activationEnabled"):
        validate_recipe_builder_audit_batch(
            _batch_payload(events=(shadow_event,))
        )


def test_direct_batch_validation_rejects_nested_event_hidden_private_state() -> None:
    event = RecipeBuilderAuditEvent.model_validate(_event_payload())
    object.__setattr__(event, "__pydantic_private__", {"activationEnabled": True})

    with pytest.raises(ValidationError, match="activationEnabled"):
        RecipeBuilderAuditBatch.model_validate(_batch_payload(events=(event,)))


def test_batch_requires_supplied_event_refs_to_cover_every_event() -> None:
    first = RecipeBuilderAuditEvent.model_validate(_event_payload())
    second = RecipeBuilderAuditEvent.model_validate(
        _event_payload(
            eventId="audit.event.002",
            eventType="compile_completed",
            subjectRef="snapshot.finance-research.001",
            subjectDigest=DIGEST_D,
        )
    )
    payload = _batch_payload(
        events=(
            first.model_dump(by_alias=True),
            second.model_dump(by_alias=True),
        ),
        eventRefs=(
            {
                "eventId": first.event_id,
                "eventType": first.event_type,
                "eventDigest": digest_recipe_builder_audit_event(first),
            },
        ),
    )

    with pytest.raises(ValidationError, match="eventRefs"):
        validate_recipe_builder_audit_batch(payload)


def test_audit_event_batch_digest_is_deterministic_and_revalidates_events() -> None:
    batch = RecipeBuilderAuditBatch.model_validate(_batch_payload())
    payload_with_different_key_order = {
        "eventRefs": batch.event_refs,
        "events": batch.events,
        **_batch_payload(),
    }

    assert digest_recipe_builder_audit_batch(batch) == (
        digest_recipe_builder_audit_batch(payload_with_different_key_order)
    )
    assert digest_recipe_builder_audit_batch(batch).startswith("sha256:")


def test_audit_event_module_import_boundary_is_validate_only() -> None:
    module_path = (
        Path(__file__).resolve().parents[1]
        / "openmagi_core_agent"
        / "authoring"
        / "audit_events.py"
    )
    tree = ast.parse(module_path.read_text(encoding="utf-8"))
    imported_names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_names.add(node.module)

    forbidden_tokens = (
        "adk",
        "deploy",
        "httpx",
        "kubernetes",
        "openai",
        "requests",
        "runtime",
        "shutil",
        "sqlite",
        "storage",
        "supabase",
        "tool_host",
    )
    offenders = sorted(
        name
        for name in imported_names
        if any(token in name.lower().replace(".", "_") for token in forbidden_tokens)
    )
    assert offenders == []
