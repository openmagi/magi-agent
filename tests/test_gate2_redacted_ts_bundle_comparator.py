from __future__ import annotations

import importlib
import json
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from magi_agent.shadow.fixture_runner import Gate2ShadowOutputFlags
from magi_agent.shadow.redacted_ts_bundle import (
    RedactedTypeScriptBundle,
    compare_redacted_ts_bundle,
    load_redacted_ts_bundle,
)


FIXTURES = Path(__file__).parent / "fixtures"
GATE2_FIXTURES = FIXTURES / "gate2"


class _FalseyMapping(dict[str, object]):
    def __bool__(self) -> bool:
        return False


def _write_bundle(path: Path, **overrides: object) -> None:
    payload = json.loads((GATE2_FIXTURES / "redacted_ts_bundle_text_turn.json").read_text())
    payload.update(overrides)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_valid_redacted_ts_bundle_compares_to_local_transcript_and_sse_fixtures() -> None:
    bundle = load_redacted_ts_bundle(
        "redacted_ts_bundle_text_turn.json",
        fixture_root=GATE2_FIXTURES,
    )

    assert bundle.source_runtime == "TypeScript"
    assert bundle.bundle_kind == "redacted_ts_capture"
    assert bundle.redacted is True

    report = compare_redacted_ts_bundle(bundle, base_fixture_dir=FIXTURES)

    assert report.input_source == "redacted_ts_bundle"
    assert report.source_runtime == "TypeScript"
    assert report.shadow_runtime == "Python ADK"
    assert report.mode == "fixture_shadow_audit"
    assert report.output_flags.user_visible is False
    assert report.output_flags.network_sse is False
    assert report.output_flags.traffic_attached is False
    assert report.output_flags.canary_attached is False
    assert report.output_flags.production_attached is False
    assert report.projected_adk_event_ids == ("evt-ts-text-partial", "evt-ts-text-final")
    assert report.transcript_refs == ("gate1/simple_assistant_text.jsonl",)
    assert report.sse_refs == ("gate1/simple_assistant_text.sse",)
    assert report.comparison_metadata["status"] == "diagnostic_only"
    assert report.comparison_metadata["bundleKind"] == "redacted_ts_capture"
    assert report.comparison_metadata["bundleFixture"] == "text_turn"
    assert report.comparison_metadata["transcriptComparisons"] == {
        "gate1/simple_assistant_text.jsonl": "matched",
    }
    assert report.comparison_metadata["sseComparisons"] == {
        "gate1/simple_assistant_text.sse": "matched",
    }


def test_redacted_ts_bundle_loader_rejects_relative_escape_before_opening(tmp_path: Path) -> None:
    root = tmp_path / "fixtures"
    root.mkdir()
    escaped = tmp_path / "escaped.json"
    escaped.write_text("not-json", encoding="utf-8")

    with pytest.raises(ValueError, match="fixture_root"):
        load_redacted_ts_bundle("../escaped.json", fixture_root=root)


def test_redacted_ts_bundle_loader_rejects_missing_fixture_root_before_opening(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle_path = tmp_path / "bundle.json"
    bundle_path.write_text("{}", encoding="utf-8")

    def reject_open(self: Path, *args: object, **kwargs: object) -> None:
        raise AssertionError(f"loader opened {self} before rejecting missing fixture_root")

    monkeypatch.setattr(Path, "open", reject_open)

    with pytest.raises(ValueError, match="fixture_root"):
        load_redacted_ts_bundle(bundle_path, fixture_root=None)


def test_redacted_ts_bundle_loader_rejects_symlink_escape_before_opening(tmp_path: Path) -> None:
    root = tmp_path / "fixtures"
    root.mkdir()
    escaped = tmp_path / "escaped.json"
    escaped.write_text("not-json", encoding="utf-8")
    (root / "escape.json").symlink_to(escaped)

    with pytest.raises(ValueError, match="fixture_root"):
        load_redacted_ts_bundle(root / "escape.json", fixture_root=root)


@pytest.mark.parametrize(
    "update",
    (
        pytest.param({"redacted": False}, id="redacted-false"),
        pytest.param({"source_runtime": "Python ADK"}, id="wrong-source-runtime"),
        pytest.param({"sourceRuntime": "Python ADK"}, id="wrong-source-runtime-alias"),
        pytest.param(
            {"source_runtime": "Python ADK", "sourceRuntime": "TypeScript"},
            id="wrong-source-runtime-masked-by-alias",
        ),
        pytest.param({"bundle_kind": "redacted_ts_raw"}, id="invalid-bundle-kind"),
        pytest.param({"bundleKind": "redacted_ts_raw"}, id="invalid-bundle-kind-alias"),
        pytest.param(
            {"bundle_kind": "redacted_ts_raw", "bundleKind": "redacted_ts_capture"},
            id="invalid-bundle-kind-masked-by-alias",
        ),
    ),
)
def test_compare_redacted_ts_bundle_revalidates_copied_invalid_bundle(
    update: dict[str, object],
) -> None:
    bundle = load_redacted_ts_bundle(
        "redacted_ts_bundle_text_turn.json",
        fixture_root=GATE2_FIXTURES,
    )
    invalid_bundle = bundle.model_copy(update=update)

    with pytest.raises(ValidationError):
        compare_redacted_ts_bundle(invalid_bundle, base_fixture_dir=FIXTURES)


@pytest.mark.parametrize(
    "overrides",
    (
        pytest.param({"redacted": False}, id="redacted-false"),
        pytest.param({"sourceRuntime": "Python ADK"}, id="wrong-source-runtime"),
        pytest.param({"bundleKind": "live_capture"}, id="live-capture-kind"),
        pytest.param({"redactionState": "raw"}, id="raw-redaction-state"),
    ),
)
def test_redacted_ts_bundle_rejects_unredacted_or_non_ts_claims(
    tmp_path: Path,
    overrides: dict[str, object],
) -> None:
    bundle_path = tmp_path / "bundle.json"
    _write_bundle(bundle_path, **overrides)

    with pytest.raises(ValidationError):
        load_redacted_ts_bundle(bundle_path, fixture_root=tmp_path)


@pytest.mark.parametrize(
    "comparison_metadata",
    (
        pytest.param({"bundleKind": "redacted_ts_raw"}, id="camel-bundle-kind"),
        pytest.param({"bundle_kind": "redacted_ts_raw"}, id="snake-bundle-kind"),
        pytest.param(
            {"nested": [{"bundlekind": "redacted_ts_raw"}]},
            id="nested-compact-bundle-kind",
        ),
    ),
)
def test_redacted_ts_bundle_rejects_fixture_comparison_metadata_bundle_kind_claim(
    tmp_path: Path,
    comparison_metadata: dict[str, object],
) -> None:
    payload = json.loads((GATE2_FIXTURES / "redacted_ts_bundle_text_turn.json").read_text())
    payload["fixture"]["comparisonMetadata"] = comparison_metadata
    bundle_path = tmp_path / "bundle.json"
    bundle_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValidationError, match="bundleKind"):
        load_redacted_ts_bundle(bundle_path, fixture_root=tmp_path)


@pytest.mark.parametrize(
    "comparison_metadata",
    (
        pytest.param({"outputAttached": False}, id="output-attached"),
        pytest.param({"outputsAttached": False}, id="outputs-attached"),
        pytest.param({"attachmentOutput": False}, id="attachment-output"),
    ),
)
def test_redacted_ts_bundle_rejects_fixture_comparison_metadata_output_attachment_claims(
    tmp_path: Path,
    comparison_metadata: dict[str, object],
) -> None:
    payload = json.loads((GATE2_FIXTURES / "redacted_ts_bundle_text_turn.json").read_text())
    payload["fixture"]["comparisonMetadata"] = comparison_metadata
    bundle_path = tmp_path / "bundle.json"
    bundle_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValidationError, match="output|traffic|attachment"):
        load_redacted_ts_bundle(bundle_path, fixture_root=tmp_path)


@pytest.mark.parametrize(
    "payload",
    (
        pytest.param({"captureMode": "live_capture"}, id="live-capture"),
        pytest.param({"routeAttached": False}, id="route-attached-key"),
        pytest.param({"proxy": "chat proxy"}, id="proxy-key"),
        pytest.param({"api": "local api compatibility"}, id="api-key"),
        pytest.param({"dashboard": "dashboard capture"}, id="dashboard-key"),
        pytest.param({"k8s": "namespace fixture"}, id="k8s-key"),
        pytest.param({"deploy": "image deploy"}, id="deploy-key"),
        pytest.param({"runtimeSelector": "typescript runtime"}, id="runtime-selector"),
        pytest.param({"telegram": "polling event"}, id="telegram-key"),
        pytest.param({"metadata": {"productionRoute": "disabled"}}, id="nested-route"),
    ),
)
def test_redacted_ts_bundle_rejects_live_route_and_runtime_surface_claims(
    tmp_path: Path,
    payload: dict[str, object],
) -> None:
    bundle_path = tmp_path / "bundle.json"
    _write_bundle(bundle_path, **payload)

    with pytest.raises(ValidationError):
        load_redacted_ts_bundle(bundle_path, fixture_root=tmp_path)


@pytest.mark.parametrize(
    "payload",
    (
        pytest.param({"metadata": {"token": "fixture"}}, id="credential-key"),
        pytest.param({"metadata": {"header": "Authorization: Bearer abcdefgh"}}, id="auth-header"),
        pytest.param({"metadata": {"key": "sk-liveabcdefgh"}}, id="secret-shaped"),
        pytest.param({"workspacePath": "/data/bots/bot-123/workspace"}, id="workspace-path"),
        pytest.param({"pvc": "pvc-1234567890"}, id="pvc-reference"),
        pytest.param({"botId": "bot-1234567890"}, id="bot-id"),
    ),
)
def test_redacted_ts_bundle_rejects_credentials_and_production_workspace_claims(
    tmp_path: Path,
    payload: dict[str, object],
) -> None:
    bundle_path = tmp_path / "bundle.json"
    _write_bundle(bundle_path, **payload)

    with pytest.raises(ValidationError):
        load_redacted_ts_bundle(bundle_path, fixture_root=tmp_path)


@pytest.mark.parametrize(
    "payload",
    (
        pytest.param({"outputFlags": {"userVisible": False}}, id="output-flags"),
        pytest.param({"userVisible": False}, id="user-visible"),
        pytest.param({"networkSse": False}, id="network-sse"),
        pytest.param({"trafficAttached": False}, id="traffic-attached"),
        pytest.param({"canaryAttached": False}, id="canary-attached"),
        pytest.param({"productionAttached": False}, id="production-attached"),
        pytest.param({"outputAttached": False}, id="output-attached"),
        pytest.param({"outputsAttached": False}, id="outputs-attached"),
        pytest.param({"attachmentOutput": False}, id="attachment-output"),
        pytest.param({"routeattached": False}, id="compact-route-attached"),
        pytest.param({"trafficoutput": False}, id="compact-traffic-output"),
        pytest.param({"productiontranscriptappend": False}, id="compact-production-transcript"),
    ),
)
def test_redacted_ts_bundle_rejects_output_network_traffic_and_production_claims(
    tmp_path: Path,
    payload: dict[str, object],
) -> None:
    bundle_path = tmp_path / "bundle.json"
    _write_bundle(bundle_path, **payload)

    with pytest.raises(ValidationError):
        load_redacted_ts_bundle(bundle_path, fixture_root=tmp_path)


def test_redacted_ts_bundle_rejects_direct_output_attachment_model_claim() -> None:
    with pytest.raises(ValidationError):
        RedactedTypeScriptBundle.model_validate(
            {
                "sourceRuntime": "TypeScript",
                "bundleKind": "redacted_ts_bundle",
                "redacted": True,
                "fixture": {},
                "outputFlags": {"userVisible": False},
            }
        )


def test_compare_redacted_ts_bundle_rejects_nested_fixture_output_flags_alias_injection() -> None:
    bundle = load_redacted_ts_bundle(
        "redacted_ts_bundle_text_turn.json",
        fixture_root=GATE2_FIXTURES,
    )
    mutated_fixture_state = dict(bundle.fixture.__dict__)
    mutated_fixture_state["outputFlags"] = Gate2ShadowOutputFlags.model_construct(
        user_visible=True,
    )
    object.__setattr__(bundle.fixture, "__dict__", mutated_fixture_state)

    with pytest.raises((ValueError, ValidationError), match="output flags|outputFlags"):
        compare_redacted_ts_bundle(bundle, base_fixture_dir=FIXTURES)


def test_compare_redacted_ts_bundle_rejects_nested_fixture_falsey_raw_extra_output_flags_injection() -> None:
    bundle = load_redacted_ts_bundle(
        "redacted_ts_bundle_text_turn.json",
        fixture_root=GATE2_FIXTURES,
    )
    object.__setattr__(
        bundle.fixture,
        "__pydantic_extra__",
        _FalseyMapping(
            {
                "outputFlags": Gate2ShadowOutputFlags.model_construct(
                    user_visible=True,
                ),
            }
        ),
    )

    with pytest.raises((ValueError, ValidationError), match="output flags|outputFlags"):
        compare_redacted_ts_bundle(bundle, base_fixture_dir=FIXTURES)


def test_compare_redacted_ts_bundle_rejects_top_level_falsey_raw_extra_state() -> None:
    bundle = load_redacted_ts_bundle(
        "redacted_ts_bundle_text_turn.json",
        fixture_root=GATE2_FIXTURES,
    )
    object.__setattr__(
        bundle,
        "__pydantic_extra__",
        _FalseyMapping({"rawFixtureOnly": "diagnostic only"}),
    )

    with pytest.raises((ValueError, ValidationError), match="raw extra"):
        compare_redacted_ts_bundle(bundle, base_fixture_dir=FIXTURES)


def test_redacted_ts_bundle_import_boundary_stays_production_runtime_free() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import importlib
import sys

module = importlib.import_module("magi_agent.shadow.redacted_ts_bundle")
assert hasattr(module, "load_redacted_ts_bundle")

forbidden_prefixes = (
    "magi_agent.transport.chat",
    "magi_agent.transport.tools",
    "magi_agent.transport.plugins",
    "magi_agent.routing",
    "magi_agent.runtime.openmagi_runtime",
    "magi_agent.runtime.turn_controller",
    "magi_agent.channels",
    "magi_agent.deploy",
    "magi_agent.provisioning",
    "magi_agent.k8s",
    "magi_agent.telegram",
    "magi_agent.api",
    "magi_agent.proxy",
    "magi_agent.dashboard",
    "magi_agent.typescript_runtime",
    "magi_agent.ts_runtime",
)
loaded = [
    module_name
    for module_name in sys.modules
    if any(
        module_name == prefix or module_name.startswith(f"{prefix}.")
        for prefix in forbidden_prefixes
    )
]
if loaded:
    raise AssertionError(f"redacted TS bundle import loaded forbidden modules: {loaded}")
""",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert importlib.import_module("magi_agent.shadow.redacted_ts_bundle")
