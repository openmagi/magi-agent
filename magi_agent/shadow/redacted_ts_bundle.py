from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from magi_agent.shadow.fixture_runner import (
    Gate2ShadowFixtureInput,
    Gate2ShadowFixtureReport,
    _is_credential_comparison_metadata_key,
    _is_output_attachment_comparison_metadata_key,
    _normalize_live_surface_string,
    _reject_report_boundary_comparison_metadata_claims,
    _reject_non_json_like_comparison_metadata,
    _reject_production_like_value,
    _resolve_gate2_shadow_fixture_path,
    _validated_fixture_input_snapshot,
    run_gate2_shadow_fixture,
)


_BUNDLE_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
)
_REDACTED_TS_FIXTURE_SOURCE = "redacted_ts_bundle"


class RedactedTypeScriptBundle(BaseModel):
    """Local-only, already-redacted TypeScript capture bundle."""

    model_config = _BUNDLE_MODEL_CONFIG

    source_runtime: Literal["TypeScript"] = Field(alias="sourceRuntime")
    bundle_kind: Literal["redacted_ts_capture", "redacted_ts_bundle"] = Field(
        alias="bundleKind",
    )
    redacted: Literal[True]
    fixture: Gate2ShadowFixtureInput

    @model_validator(mode="before")
    @classmethod
    def _reject_unsafe_bundle_payload(cls, value: object) -> object:
        if isinstance(value, Mapping):
            if value.get("redacted") is not True:
                raise ValueError("redacted TypeScript bundles must declare redacted=true")
            _reject_non_json_like_comparison_metadata(value)
            _reject_redacted_ts_bundle_claims(value)
            _reject_production_like_value(value)
        return value

    @field_validator("fixture", mode="before")
    @classmethod
    def _validate_fixture_source(cls, value: object) -> object:
        if not isinstance(value, Mapping):
            return value
        payload = dict(value)
        source = payload.get("source")
        if source is not None and source != _REDACTED_TS_FIXTURE_SOURCE:
            raise ValueError("redacted TypeScript bundle fixture source must be redacted_ts_bundle")
        payload["source"] = _REDACTED_TS_FIXTURE_SOURCE
        return payload

    @model_validator(mode="after")
    def _require_redacted_bundle_fixture_source(self) -> Self:
        if self.fixture.source != _REDACTED_TS_FIXTURE_SOURCE:
            raise ValueError("redacted TypeScript bundle fixture source must be redacted_ts_bundle")
        _reject_fixture_comparison_metadata_bundle_kind_claims(
            self.fixture.comparison_metadata
        )
        return self

    def to_shadow_fixture_input(self) -> Gate2ShadowFixtureInput:
        bundle = _validated_redacted_ts_bundle_snapshot(self)
        payload = bundle.fixture.model_dump(by_alias=True, mode="python", warnings=False)
        payload["source"] = _REDACTED_TS_FIXTURE_SOURCE
        return Gate2ShadowFixtureInput.model_validate(payload)


def _reject_redacted_ts_bundle_claims(value: object) -> None:
    if isinstance(value, Mapping):
        fixture = value.get("fixture")
        if isinstance(fixture, Mapping):
            for metadata_key in ("comparisonMetadata", "comparison_metadata"):
                comparison_metadata = fixture.get(metadata_key)
                if comparison_metadata is not None:
                    _reject_fixture_comparison_metadata_bundle_kind_claims(
                        comparison_metadata
                    )
        for raw_key, nested_value in value.items():
            if isinstance(raw_key, str):
                normalized_key = _normalize_live_surface_string(raw_key)
                if _is_output_attachment_comparison_metadata_key(normalized_key):
                    raise ValueError("redacted TypeScript bundles must not claim output attachment")
                if _is_credential_comparison_metadata_key(normalized_key):
                    raise ValueError("redacted TypeScript bundles must not contain credential keys")
            _reject_redacted_ts_bundle_claims(nested_value)
        return
    if isinstance(value, (list, tuple)):
        for item in value:
            _reject_redacted_ts_bundle_claims(item)


def _reject_fixture_comparison_metadata_bundle_kind_claims(value: object) -> None:
    try:
        _reject_report_boundary_comparison_metadata_claims(value)
    except ValueError as exc:
        if "bundleKind" in str(exc):
            raise ValueError(
                "redacted TypeScript bundle fixture comparisonMetadata must not declare bundleKind"
            ) from exc
        raise


def _validated_redacted_ts_bundle_snapshot(
    bundle: RedactedTypeScriptBundle,
) -> RedactedTypeScriptBundle:
    _validated_fixture_input_snapshot(bundle.fixture)
    raw_extra = getattr(bundle, "__pydantic_extra__", None)
    if raw_extra is not None:
        if not isinstance(raw_extra, Mapping):
            raise ValueError(
                "redacted TypeScript bundle raw extra state must be a mapping"
            )
        raw_extra_items = tuple(raw_extra.items())
        if not raw_extra_items:
            raise ValueError("redacted TypeScript bundle must not contain raw extra state")
        raise ValueError("redacted TypeScript bundle must not contain raw extra state")
    payload = bundle.model_dump(
        by_alias=True,
        exclude_defaults=True,
        mode="json",
        warnings=False,
    )
    raw_model_state = getattr(bundle, "__dict__", {})
    field_names = set(bundle.__class__.model_fields)
    raw_extra_keys = tuple(
        raw_key for raw_key in raw_model_state if raw_key not in field_names
    )
    if raw_extra_keys:
        payload["__rawModelStateExtraKeys"] = tuple(
            str(raw_key) for raw_key in raw_extra_keys
        )
    return RedactedTypeScriptBundle.model_validate(payload)


def load_redacted_ts_bundle(
    path: str | Path,
    *,
    fixture_root: str | Path | None,
) -> RedactedTypeScriptBundle:
    if fixture_root is None:
        raise ValueError("fixture_root is required for redacted TypeScript bundle loading")
    bundle_path = _resolve_gate2_shadow_fixture_path(path, fixture_root=fixture_root)
    with bundle_path.open("r", encoding="utf-8") as bundle_file:
        payload: Any = json.load(bundle_file)
    return RedactedTypeScriptBundle.model_validate(payload)


def compare_redacted_ts_bundle(
    bundle: RedactedTypeScriptBundle,
    *,
    base_fixture_dir: str | Path,
) -> Gate2ShadowFixtureReport:
    bundle = _validated_redacted_ts_bundle_snapshot(bundle)
    return run_gate2_shadow_fixture(
        bundle.to_shadow_fixture_input(),
        base_fixture_dir=base_fixture_dir,
        _trusted_comparison_metadata={"bundleKind": bundle.bundle_kind},
    )


__all__ = [
    "RedactedTypeScriptBundle",
    "compare_redacted_ts_bundle",
    "load_redacted_ts_bundle",
]
