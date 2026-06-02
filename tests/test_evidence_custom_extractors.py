from __future__ import annotations

import subprocess
import sys
from copy import deepcopy

import pytest
from pydantic import ValidationError

from magi_agent.evidence import CustomEvidenceExtractor
from magi_agent.evidence.extractors import (
    CustomEvidenceExtractorConfig,
    CustomEvidenceExtractorSource,
    CustomEvidenceFieldMapping,
    CustomEvidenceSuccessCondition,
)


def _valid_extractor_payload() -> dict[str, object]:
    return {
        "id": "stripe-webhook-ack",
        "emitsType": "custom:StripeWebhookAck",
        "source": {
            "kind": "tool_result",
            "toolName": "WebhookWait",
            "toolCallId": "call-123",
        },
        "fields": {
            "event_id": {"path": "event.id", "required": True},
            "status": {"path": "status"},
            "received_at": {"path": "receivedAt"},
        },
        "successWhen": [{"path": "status", "equals": "received"}],
    }


def test_custom_extractor_accepts_valid_custom_output_type() -> None:
    extractor = CustomEvidenceExtractor.model_validate(_valid_extractor_payload())

    assert extractor.id == "stripe-webhook-ack"
    assert extractor.emits_type == "custom:StripeWebhookAck"
    assert extractor.source.kind == "tool_result"
    assert {field.name: field.path for field in extractor.fields} == {
        "event_id": "event.id",
        "status": "status",
        "received_at": "receivedAt",
    }


def test_custom_extractor_rejects_builtin_output_type() -> None:
    payload = _valid_extractor_payload()
    payload["emitsType"] = "TestRun"

    with pytest.raises(ValidationError):
        CustomEvidenceExtractor.model_validate(payload)


@pytest.mark.parametrize(
    "emits_type",
    (
        "custom:",
        "custom:badName",
        "custom:Bad Name",
        "custom:Bad/Name",
        f"custom:{'A' * 74}",
    ),
)
def test_custom_extractor_rejects_invalid_custom_evidence_names(emits_type: str) -> None:
    payload = _valid_extractor_payload()
    payload["emitsType"] = emits_type

    with pytest.raises(ValidationError):
        CustomEvidenceExtractor.model_validate(payload)


@pytest.mark.parametrize(
    "source_payload,expected_alias",
    (
        (
            {"kind": "tool_result", "toolName": "Bash", "toolCallId": "call-1"},
            {"kind": "tool_result", "toolName": "Bash", "toolCallId": "call-1"},
        ),
        (
            {"kind": "adk_event", "eventId": "event-1", "eventType": "tool_end"},
            {"kind": "adk_event", "eventId": "event-1", "eventType": "tool_end"},
        ),
        (
            {
                "kind": "transcript",
                "transcriptEntryId": "entry-1",
                "turnId": "turn-1",
            },
            {
                "kind": "transcript",
                "transcriptEntryId": "entry-1",
                "turnId": "turn-1",
            },
        ),
        (
            {"kind": "artifact", "artifactId": "artifact-1"},
            {"kind": "artifact", "artifactId": "artifact-1"},
        ),
        (
            {"kind": "verifier", "verifierName": "deterministic-verifier"},
            {"kind": "verifier", "verifierName": "deterministic-verifier"},
        ),
        (
            {"kind": "plugin", "pluginId": "billing-plugin", "pluginName": "Billing"},
            {"kind": "plugin", "pluginId": "billing-plugin", "pluginName": "Billing"},
        ),
    ),
)
def test_source_metadata_supports_projected_source_kinds(
    source_payload: dict[str, object],
    expected_alias: dict[str, object],
) -> None:
    source = CustomEvidenceExtractorSource.model_validate(source_payload)

    assert source.model_dump(by_alias=True, exclude_none=True) == expected_alias


@pytest.mark.parametrize("kind", ("external_ack", "artifact_json", "route", "runner"))
def test_source_metadata_rejects_out_of_scope_source_kinds(kind: str) -> None:
    with pytest.raises(ValidationError):
        CustomEvidenceExtractorSource.model_validate({"kind": kind})


def test_field_mappings_are_declarative_path_metadata_only() -> None:
    mapping = CustomEvidenceFieldMapping.model_validate(
        {
            "name": "status",
            "path": "result.status",
            "required": True,
            "default": "unknown",
        }
    )

    assert mapping.name == "status"
    assert mapping.path == "result.status"
    assert mapping.required is True
    assert mapping.model_dump(by_alias=True, exclude_none=True) == {
        "name": "status",
        "path": "result.status",
        "required": True,
        "default": "unknown",
    }


@pytest.mark.parametrize(
    "extra_payload",
    (
        {"callable": "pkg.module:function"},
        {"function": "lambda value: value"},
        {"importPath": "pkg.module.function"},
        {"python": "def extract(value): return value"},
        {"javascript": "value => value"},
        {"transform": {"importPath": "pkg.module.function"}},
    ),
)
def test_field_mappings_reject_arbitrary_callable_code_or_import_metadata(
    extra_payload: dict[str, object],
) -> None:
    payload: dict[str, object] = {"name": "status", "path": "result.status"}
    payload.update(extra_payload)

    with pytest.raises(ValidationError):
        CustomEvidenceFieldMapping.model_validate(payload)


@pytest.mark.parametrize(
    "model_type,payload",
    (
        (
            CustomEvidenceExtractorSource,
            {"kind": "tool_result", "toolName": "Bash", "trafficAttached": False},
        ),
        (
            CustomEvidenceFieldMapping,
            {"name": "status", "path": "status", "importPath": "pkg.module.function"},
        ),
        (
            CustomEvidenceSuccessCondition,
            {"path": "status", "equals": "received", "callable": "pkg.module:function"},
        ),
        (
            CustomEvidenceExtractor,
            {**_valid_extractor_payload(), "trafficAttached": False},
        ),
        (
            CustomEvidenceExtractorConfig,
            {"customEvidenceExtractors": [], "trafficAttached": False},
        ),
    ),
)
@pytest.mark.parametrize("extra_mode", ("allow", "ignore"))
def test_public_model_validate_cannot_override_forbidden_extra(
    model_type: type,
    payload: dict[str, object],
    extra_mode: str,
) -> None:
    with pytest.raises(ValidationError):
        model_type.model_validate(payload, extra=extra_mode)


def test_success_conditions_are_declarative_and_bounded() -> None:
    condition = CustomEvidenceSuccessCondition.model_validate(
        {"path": "status", "oneOf": ["received", "accepted"]}
    )

    assert condition.path == "status"
    assert condition.one_of == ("received", "accepted")

    payload = _valid_extractor_payload()
    payload["successWhen"] = [{"path": f"checks.check_{index}", "exists": True} for index in range(11)]

    with pytest.raises(ValidationError):
        CustomEvidenceExtractor.model_validate(payload)


def test_success_condition_one_of_model_copy_revalidates_existing_state() -> None:
    condition = CustomEvidenceSuccessCondition.model_validate(
        {"path": "status", "oneOf": ["received", "accepted"]}
    )

    copied = condition.model_copy()

    assert copied.one_of == ("received", "accepted")


def test_success_condition_one_of_model_validate_revalidates_existing_state() -> None:
    condition = CustomEvidenceSuccessCondition.model_validate(
        {"path": "status", "oneOf": ["received", "accepted"]}
    )

    revalidated = CustomEvidenceSuccessCondition.model_validate(condition)

    assert revalidated.one_of == ("received", "accepted")


def test_custom_extractor_accepts_one_of_success_conditions() -> None:
    payload = _valid_extractor_payload()
    payload["successWhen"] = [{"path": "status", "oneOf": ["received", "accepted"]}]

    extractor = CustomEvidenceExtractor.model_validate(payload)

    assert extractor.success_when[0].one_of == ("received", "accepted")
    assert extractor.model_dump(by_alias=True, exclude_none=True)["successWhen"] == (
        {"path": "status", "oneOf": ["received", "accepted"]},
    )


@pytest.mark.parametrize(
    "condition_payload",
    (
        {"path": "status"},
        {"path": "status", "equals": "received", "exists": True},
        {"path": "status", "oneOf": []},
        {"path": "status", "oneOf": ("received", "accepted")},
        {"path": "status", "exists": "true"},
        {"path": "status", "callable": "pkg.module:function"},
        {"path": "status", "importPath": "pkg.module.function"},
        {"path": "pkg.module:function", "equals": "received"},
    ),
)
def test_success_conditions_reject_ambiguous_or_executable_metadata(
    condition_payload: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        CustomEvidenceSuccessCondition.model_validate(condition_payload)


@pytest.mark.parametrize(
    "bad_value",
    (
        float("inf"),
        float("-inf"),
        float("nan"),
        b"bytes",
        bytearray(b"bytes"),
        ("tuple",),
        object(),
        {"nested": ("tuple",)},
        {1: "non-string-key"},
    ),
)
def test_json_like_values_reject_non_json_values(bad_value: object) -> None:
    with pytest.raises(ValidationError):
        CustomEvidenceFieldMapping.model_validate(
            {"name": "status", "path": "status", "default": bad_value}
        )

    with pytest.raises(ValidationError):
        CustomEvidenceSuccessCondition.model_validate(
            {"path": "status", "equals": bad_value}
        )


def test_json_like_values_accept_nested_json_objects_and_lists() -> None:
    mapping = CustomEvidenceFieldMapping.model_validate(
        {
            "name": "details",
            "path": "details",
            "default": {
                "ok": True,
                "count": 2,
                "ratio": 0.5,
                "items": ["a", None, {"nested": "value"}],
            },
        }
    )

    assert mapping.model_dump(by_alias=True)["default"] == {
        "ok": True,
        "count": 2,
        "ratio": 0.5,
        "items": ["a", None, {"nested": "value"}],
    }


def test_duplicate_field_mappings_are_rejected() -> None:
    payload = _valid_extractor_payload()
    payload["fields"] = [
        {"name": "status", "path": "status"},
        {"name": "status", "path": "result.status"},
    ]

    with pytest.raises(ValidationError):
        CustomEvidenceExtractor.model_validate(payload)


def test_custom_extractor_model_copy_rejects_constructed_duplicate_fields() -> None:
    status_mapping = CustomEvidenceFieldMapping.model_validate(
        {"name": "status", "path": "status"}
    )
    duplicate_status_mapping = CustomEvidenceFieldMapping.model_validate(
        {"name": "status", "path": "result.status"}
    )
    extractor = CustomEvidenceExtractor.model_construct(
        id="constructed-duplicate-fields",
        emits_type="custom:ConstructedDuplicateFields",
        source=CustomEvidenceExtractorSource.model_validate({"kind": "tool_result"}),
        fields=(status_mapping, duplicate_status_mapping),
        success_when=(),
    )

    with pytest.raises(ValidationError):
        extractor.model_copy()


def test_custom_extractor_model_validate_rejects_constructed_invalid_state() -> None:
    status_mapping = CustomEvidenceFieldMapping.model_validate(
        {"name": "status", "path": "status"}
    )
    duplicate_status_mapping = CustomEvidenceFieldMapping.model_validate(
        {"name": "status", "path": "result.status"}
    )
    extractor = CustomEvidenceExtractor.model_construct(
        id="constructed-invalid-direct",
        emits_type="TestRun",
        source=CustomEvidenceExtractorSource.model_validate({"kind": "tool_result"}),
        fields=(status_mapping, duplicate_status_mapping),
        success_when=(),
    )

    with pytest.raises(ValidationError):
        CustomEvidenceExtractor.model_validate(extractor)


def test_source_model_validate_rejects_constructed_invalid_state() -> None:
    source = CustomEvidenceExtractorSource.model_construct(kind="external_ack")

    with pytest.raises(ValidationError):
        CustomEvidenceExtractorSource.model_validate(source)


def test_field_mapping_model_validate_rejects_constructed_invalid_state() -> None:
    mapping = CustomEvidenceFieldMapping.model_construct(name="1bad", path="bad:path")

    with pytest.raises(ValidationError):
        CustomEvidenceFieldMapping.model_validate(mapping)


def test_success_condition_model_validate_rejects_constructed_invalid_state() -> None:
    condition = CustomEvidenceSuccessCondition.model_construct(
        path="bad:path",
        equals=object(),
    )

    with pytest.raises(ValidationError):
        CustomEvidenceSuccessCondition.model_validate(condition)


def test_extractor_config_model_copy_rejects_constructed_duplicate_nested_fields() -> None:
    status_mapping = CustomEvidenceFieldMapping.model_validate(
        {"name": "status", "path": "status"}
    )
    duplicate_status_mapping = CustomEvidenceFieldMapping.model_validate(
        {"name": "status", "path": "result.status"}
    )
    extractor = CustomEvidenceExtractor.model_construct(
        id="constructed-config-copy-duplicate-fields",
        emits_type="custom:ConstructedConfigCopyDuplicateFields",
        source=CustomEvidenceExtractorSource.model_validate({"kind": "tool_result"}),
        fields=(status_mapping, duplicate_status_mapping),
        success_when=(),
    )
    config = CustomEvidenceExtractorConfig.model_construct(
        custom_evidence_extractors=(extractor,)
    )

    with pytest.raises(ValidationError):
        config.model_copy()


def test_extractor_config_model_validate_rejects_constructed_invalid_state() -> None:
    status_mapping = CustomEvidenceFieldMapping.model_validate(
        {"name": "status", "path": "status"}
    )
    duplicate_status_mapping = CustomEvidenceFieldMapping.model_validate(
        {"name": "status", "path": "result.status"}
    )
    extractor = CustomEvidenceExtractor.model_construct(
        id="constructed-config-invalid-direct",
        emits_type="TestRun",
        source=CustomEvidenceExtractorSource.model_validate({"kind": "tool_result"}),
        fields=(status_mapping, duplicate_status_mapping),
        success_when=(),
    )
    config = CustomEvidenceExtractorConfig.model_construct(
        custom_evidence_extractors=(extractor,)
    )

    with pytest.raises(ValidationError):
        CustomEvidenceExtractorConfig.model_validate(config)


def test_extractor_config_rejects_constructed_duplicate_nested_fields() -> None:
    status_mapping = CustomEvidenceFieldMapping.model_validate(
        {"name": "status", "path": "status"}
    )
    duplicate_status_mapping = CustomEvidenceFieldMapping.model_validate(
        {"name": "status", "path": "result.status"}
    )
    extractor = CustomEvidenceExtractor.model_construct(
        id="constructed-config-duplicate-fields",
        emits_type="custom:ConstructedConfigDuplicateFields",
        source=CustomEvidenceExtractorSource.model_validate({"kind": "tool_result"}),
        fields=(status_mapping, duplicate_status_mapping),
        success_when=(),
    )

    with pytest.raises(ValidationError):
        CustomEvidenceExtractorConfig.model_validate(
            {"customEvidenceExtractors": (extractor,)}
        )


def test_mapping_fields_reject_inner_name_that_overrides_mapping_key() -> None:
    payload = _valid_extractor_payload()
    payload["fields"] = {"status": {"name": "renamed", "path": "status"}}

    with pytest.raises(ValidationError):
        CustomEvidenceExtractor.model_validate(payload)


def test_mapping_form_fields_reject_non_string_keys() -> None:
    payload = _valid_extractor_payload()
    payload["fields"] = {b"status": {"path": "status"}}

    with pytest.raises(ValidationError):
        CustomEvidenceExtractor.model_validate(payload)


def test_success_when_rejects_external_tuple_of_dicts() -> None:
    payload = _valid_extractor_payload()
    payload["successWhen"] = ({"path": "status", "equals": "received"},)

    with pytest.raises(ValidationError):
        CustomEvidenceExtractor.model_validate(payload)


def test_success_when_rejects_public_tuple_of_condition_models() -> None:
    condition = CustomEvidenceSuccessCondition.model_validate(
        {"path": "status", "equals": "received"}
    )
    payload = _valid_extractor_payload()
    payload["successWhen"] = (condition,)

    with pytest.raises(ValidationError):
        CustomEvidenceExtractor.model_validate(payload)


def test_success_when_omitted_defaults_to_empty_tuple() -> None:
    payload = _valid_extractor_payload()
    payload.pop("successWhen")

    extractor = CustomEvidenceExtractor.model_validate(payload)

    assert extractor.success_when == ()


@pytest.mark.parametrize("field_name", ("successWhen", "success_when"))
def test_success_when_rejects_explicit_empty_tuple_input(field_name: str) -> None:
    payload = _valid_extractor_payload()
    payload.pop("successWhen")
    payload[field_name] = ()

    with pytest.raises(ValidationError):
        CustomEvidenceExtractor.model_validate(payload)


def test_extractor_config_rejects_external_tuple_of_dicts() -> None:
    with pytest.raises(ValidationError):
        CustomEvidenceExtractorConfig.model_validate(
            {"customEvidenceExtractors": (_valid_extractor_payload(),)}
        )


def test_extractor_config_rejects_public_tuple_of_extractor_models() -> None:
    extractor = CustomEvidenceExtractor.model_validate(_valid_extractor_payload())

    with pytest.raises(ValidationError):
        CustomEvidenceExtractorConfig.model_validate(
            {"customEvidenceExtractors": (extractor,)}
        )


def test_extractor_config_omitted_extractors_default_to_empty_tuple() -> None:
    config = CustomEvidenceExtractorConfig.model_validate({})

    assert config.custom_evidence_extractors == ()


@pytest.mark.parametrize(
    "field_name",
    ("customEvidenceExtractors", "custom_evidence_extractors"),
)
def test_extractor_config_rejects_explicit_empty_tuple_input(field_name: str) -> None:
    with pytest.raises(ValidationError):
        CustomEvidenceExtractorConfig.model_validate({field_name: ()})


def test_extractor_config_model_copy_preserves_internal_extractor_tuple() -> None:
    config = CustomEvidenceExtractorConfig.model_validate(
        {"customEvidenceExtractors": [_valid_extractor_payload()]}
    )

    copied = config.model_copy()

    assert len(copied.custom_evidence_extractors) == 1
    assert copied.custom_evidence_extractors[0].id == "stripe-webhook-ack"


@pytest.mark.parametrize(
    "path",
    (
        "",
        ".status",
        "status.",
        "event..id",
        "event.__proto__",
        "event.prototype",
        "event.constructor",
        "items[0].id",
        "$.status",
        "status.*",
        "pkg.module:function",
    ),
)
def test_invalid_dot_paths_are_rejected(path: str) -> None:
    with pytest.raises(ValidationError):
        CustomEvidenceFieldMapping.model_validate({"name": "status", "path": path})


@pytest.mark.parametrize(
    "payload",
    (
        {**_valid_extractor_payload(), "id": b"stripe-webhook-ack"},
        {**_valid_extractor_payload(), "emitsType": b"custom:StripeWebhookAck"},
        {
            **_valid_extractor_payload(),
            "fields": {"status": {"path": b"status"}},
        },
        {
            **_valid_extractor_payload(),
            "source": {"kind": "tool_result", "toolName": b"Bash"},
        },
    ),
)
def test_custom_extractor_rejects_bytes_for_string_fields(
    payload: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        CustomEvidenceExtractor.model_validate(payload)


def test_dumps_use_camel_case_aliases_and_accept_snake_case_input() -> None:
    extractor = CustomEvidenceExtractor.model_validate(
        {
            "id": "snake-input",
            "emits_type": "custom:SnakeInput",
            "source": {
                "kind": "adk_event",
                "event_id": "event-1",
                "event_type": "tool_end",
            },
            "fields": {
                "exit_code": {"path": "result.exitCode", "required": True},
            },
            "success_when": [{"path": "status", "equals": "ok"}],
        }
    )

    dumped = extractor.model_dump(by_alias=True, exclude_none=True)

    assert dumped["emitsType"] == "custom:SnakeInput"
    assert dumped["source"]["eventId"] == "event-1"
    assert dumped["source"]["eventType"] == "tool_end"
    assert dumped["fields"]["exit_code"] == {
        "path": "result.exitCode",
        "required": True,
    }
    assert dumped["successWhen"] == ({"path": "status", "equals": "ok"},)


def test_extractor_metadata_does_not_attach_traffic_or_execution_flags() -> None:
    extractor = CustomEvidenceExtractor.model_validate(_valid_extractor_payload())
    dumped = extractor.model_dump(by_alias=True)

    assert "trafficAttached" not in dumped
    assert "executionAttached" not in dumped
    assert "routeAttached" not in dumped

    for flag in ("trafficAttached", "executionAttached", "routeAttached"):
        payload = _valid_extractor_payload()
        payload[flag] = False
        with pytest.raises(ValidationError):
            CustomEvidenceExtractor.model_validate(payload)


def test_extractor_config_enforces_extractor_and_field_limits() -> None:
    config_payload = {
        "custom_evidence_extractors": [
            {
                **_valid_extractor_payload(),
                "id": f"extractor-{index}",
                "emitsType": f"custom:Evidence{index}",
            }
            for index in range(21)
        ]
    }

    with pytest.raises(ValidationError):
        CustomEvidenceExtractorConfig.model_validate(config_payload)

    extractor_payload = _valid_extractor_payload()
    extractor_payload["fields"] = {
        f"field_{index}": {"path": f"fields.field_{index}"} for index in range(26)
    }

    with pytest.raises(ValidationError):
        CustomEvidenceExtractor.model_validate(extractor_payload)


def test_extractor_config_rejects_duplicate_extractor_ids() -> None:
    first = _valid_extractor_payload()
    second = deepcopy(first)
    second["emitsType"] = "custom:AnotherWebhookAck"

    with pytest.raises(ValidationError):
        CustomEvidenceExtractorConfig.model_validate(
            {"custom_evidence_extractors": [first, second]}
        )


def test_evidence_extractors_import_stays_adk_runner_runtime_and_route_free() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import importlib
import sys

importlib.import_module("magi_agent.evidence.extractors")
forbidden_modules = (
    "google.adk.runners",
    "google.adk.sessions",
    "google.adk.artifacts",
    "magi_agent.adk_bridge.runner_adapter",
    "magi_agent.runtime.openmagi_runtime",
    "magi_agent.transport.chat",
    "magi_agent.transport.tools",
)
loaded = [module for module in forbidden_modules if module in sys.modules]
if loaded:
    raise AssertionError(f"extractor schema import loaded forbidden modules: {loaded}")
""",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
