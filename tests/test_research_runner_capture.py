from __future__ import annotations

import importlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from openmagi_core_agent.shadow.research_runner_capture import (
    ClaimRecord,
    ClaimSourceLinkRecord,
    ResearchArtifactAuthorityFlags,
    ResearchArtifactRow,
    ResearchRunDocument,
    ResearchRunResultRow,
    SourceRecord,
    build_local_sample_capture,
    write_research_artifacts_jsonl,
    write_research_run_json,
)


PYTHON_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = PYTHON_ROOT.parents[2]
CANONICAL_SAMPLE_ARTIFACTS = REPO_ROOT / "docs/notes/research-parity/sample-artifacts.jsonl"
RESEARCH_EVAL_SCRIPT = REPO_ROOT / "scripts/research-parity-eval.mjs"
RESEARCH_BENCHMARK = REPO_ROOT / "docs/notes/research-parity/benchmark-v1.json"


def _read_jsonl(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def test_canonical_research_artifact_v1_sample_validates() -> None:
    sample_row = _read_jsonl(CANONICAL_SAMPLE_ARTIFACTS)[0]

    row = ResearchArtifactRow.model_validate(sample_row)
    dumped = row.model_dump(by_alias=True, mode="json", exclude_none=True)

    assert dumped["schemaVersion"] == "research-artifact-v1"
    assert dumped["routeIntent"] == "new_research"
    assert dumped["claims"][0]["text"].startswith("OpenCode documents")
    assert dumped["claimSourceLinks"][0]["support"] == "supports"
    assert dumped["requiredFields"][0]["fieldId"] == "verdict"
    assert dumped["inspectedUrls"][0]["url"].startswith("https://github.com/")
    assert dumped["verifier"]["synthesis"] == "integrated"


def test_valid_capture_writes_run_json_and_artifacts_jsonl(tmp_path: Path) -> None:
    source = SourceRecord(
        sourceId="src_1",
        kind="web_fetch",
        uri="https://example.com/research",
        title="Example research source",
        toolName="WebFetch",
        trustTier="primary",
        isPrimary=True,
        inspectedAt="2026-05-24T12:00:00Z",
        contentHash="sha256:" + "a" * 64,
        snippets=("Short inspected public snippet.",),
    )
    claim = ClaimRecord(
        claimId="claim_1",
        claim="Example research source supports the sample claim.",
        supportStatus="supported",
        sourceIds=("src_1",),
        reasoning={
            "status": "supported",
            "premiseSourceIds": ("src_1",),
            "inference": "The normalized snippet directly supports the sample claim.",
        },
    )
    row = ResearchArtifactRow(
        taskId="api-docs-research",
        turnId="turn_1",
        routeIntent="research",
        finalAnswer="Sample public final answer.",
        sources=(source,),
        claims=(claim,),
        claimSourceLinks=(ClaimSourceLinkRecord(claimId="claim_1", sourceId="src_1"),),
    )
    run = ResearchRunDocument(
        benchmarkVersion=1,
        agent="openmagi-python-adk-local-fake",
        runId="run_pr22",
        createdAt="2026-05-24T12:00:00Z",
        results=(
            ResearchRunResultRow(
                taskId="api-docs-research",
                finalAnswer="Sample local fake answer citing normalized source refs only.",
                inspectedSources=(source,),
                toolCalls=({"name": "WebFetch", "count": 1},),
            ),
        ),
    )

    run_path = write_research_run_json(tmp_path / "python-adk-research-run.json", run)
    artifacts_path = write_research_artifacts_jsonl(
        tmp_path / "python-adk-research-artifacts.jsonl",
        (row,),
    )

    assert json.loads(run_path.read_text())["runId"] == "run_pr22"
    rows = _read_jsonl(artifacts_path)
    assert len(rows) == 1
    assert rows[0]["schemaVersion"] == "research-artifact-v1"
    assert rows[0]["taskId"] == "api-docs-research"
    assert rows[0]["finalAnswer"] == "Sample public final answer."
    assert rows[0]["sources"][0]["contentHash"] == "sha256:" + "a" * 64
    assert rows[0]["claims"][0]["text"] == "Example research source supports the sample claim."
    assert "claim" not in rows[0]["claims"][0]
    assert rows[0]["claims"][0]["sourceIds"] == ["src_1"]


def test_sample_capture_represents_missing_unused_child_and_gate_overreach(
    tmp_path: Path,
) -> None:
    sample = build_local_sample_capture(tmp_path)

    run = json.loads(sample.run_path.read_text())
    artifacts = {row["taskId"]: row for row in _read_jsonl(sample.artifacts_path)}

    missing = artifacts["current-public-facts"]
    assert missing["sources"] == []
    assert missing["claims"][0]["supportStatus"] == "unsupported"
    assert missing["claimSourceLinks"] == []

    child = artifacts["parallel-long-running-research"]
    assert child["sources"][0]["kind"] == "subagent_result"
    assert child["claimSourceLinks"] == []

    followup = artifacts["research-followup-option-selection"]
    assert followup["routeIntent"] == "followup_control"
    assert followup["verifier"]["blocked"] is True
    assert "CLAIM_CITATION" in json.dumps(followup["events"])

    assert {result["taskId"] for result in run["results"]} == set(artifacts)


def test_artifact_writer_rejects_duplicate_task_rows(tmp_path: Path) -> None:
    row = ResearchArtifactRow(
        taskId="duplicate-task",
        turnId="turn_1",
        routeIntent="research",
        finalAnswer="Sample public final answer.",
    )

    with pytest.raises(ValueError, match="duplicate taskId"):
        write_research_artifacts_jsonl(tmp_path / "artifacts.jsonl", (row, row))


def test_raw_private_fields_are_rejected_or_redacted() -> None:
    with pytest.raises(ValidationError):
        SourceRecord(
            sourceId="src_1",
            kind="file",
            uri="/Users/kevin/private/notes.md",
            inspectedAt="2026-05-24T12:00:00Z",
            contentHash="sha256:" + "b" * 64,
        )

    with pytest.raises(ValidationError):
        SourceRecord(
            sourceId="src_1",
            kind="web_fetch",
            uri="https://example.com",
            inspectedAt="2026-05-24T12:00:00Z",
            contentHash="sha256:" + "c" * 64,
            body="<html>raw body must not be accepted</html>",
        )

    row = ResearchArtifactRow(
        taskId="redaction-check",
        turnId="turn_1",
        routeIntent="research",
        finalAnswer="Safe public final answer.",
        sources=(
            SourceRecord(
                sourceId="src_1",
                kind="web_fetch",
                uri="https://example.com",
                inspectedAt="2026-05-24T12:00:00Z",
                contentHash="sha256:" + "d" * 64,
                snippets=("token sk-secret hidden_reasoning /Users/kevin/file",),
            ),
        ),
    )

    rendered = json.dumps(row.model_dump(by_alias=True), sort_keys=True)
    assert "sk-secret" not in rendered
    assert "hidden_reasoning" not in rendered
    assert "/Users/kevin" not in rendered
    assert "[redacted]" in rendered


def test_default_off_authority_flags_are_false_and_cannot_be_enabled() -> None:
    flags = ResearchArtifactAuthorityFlags()
    assert set(flags.model_dump(by_alias=True).values()) == {False}

    with pytest.raises(ValidationError):
        ResearchArtifactAuthorityFlags(trafficAttached=True)
    with pytest.raises(ValidationError):
        ResearchArtifactRow(
            taskId="authority-check",
            turnId="turn_1",
            routeIntent="research",
            finalAnswer="Safe public final answer.",
            authorityFlags={"modelCall": True},
        )
    assert set(
        ResearchArtifactAuthorityFlags.model_construct(
            trafficAttached=True,
            productionAuthority=True,
            modelCall=True,
        ).model_dump(by_alias=True).values()
    ) == {False}
    assert set(
        flags.model_copy(update={"liveToolDispatched": True}).model_dump(
            by_alias=True,
        ).values()
    ) == {False}
    assert ResearchArtifactRow.model_construct(
        taskId="authority-construct",
        turnId="turn_1",
        routeIntent="research",
        finalAnswer="Safe public final answer.",
        authorityFlags={"modelCall": True},
    ).authority_flags.model_dump(by_alias=True)["modelCall"] is False

    row = ResearchArtifactRow(
        taskId="authority-copy",
        turnId="turn_1",
        routeIntent="research",
        finalAnswer="Safe public final answer.",
    )
    copied = row.model_copy(
        update={
            "authority_flags": {
                "modelCall": True,
                "productionStorageWritten": True,
                "trafficAttached": True,
            },
        }
    )
    assert set(copied.authority_flags.model_dump(by_alias=True).values()) == {False}


def test_writers_revalidate_forged_nested_instances(tmp_path: Path) -> None:
    source = SourceRecord(
        sourceId="src_1",
        kind="web_fetch",
        uri="https://example.com",
        inspectedAt="2026-05-24T12:00:00Z",
        contentHash="sha256:" + "e" * 64,
    ).model_copy(update={"uri": "/Users/kevin/private.txt"})
    row = ResearchArtifactRow(
        taskId="forged-source",
        turnId="turn_1",
        routeIntent="research",
        finalAnswer="Safe public final answer.",
    )
    object.__setattr__(row, "sources", (source,))
    run = ResearchRunDocument.model_construct(
        benchmarkVersion=1,
        agent="openmagi-python-adk-local-fake",
        runId="forged-run",
        createdAt="2026-05-24T12:00:00Z",
        results=(
            ResearchRunResultRow.model_construct(
                taskId="forged-source",
                finalAnswer="/Users/kevin/private.txt sk-secret",
            ),
        ),
    )

    with pytest.raises(ValidationError, match="source text"):
        write_research_artifacts_jsonl(tmp_path / "artifacts.jsonl", (row,))
    with pytest.raises(ValidationError, match="run result text"):
        write_research_run_json(tmp_path / "run.json", run)


def test_generated_sample_is_compatible_with_js_research_evaluator(tmp_path: Path) -> None:
    if shutil.which("node") is None:
        pytest.skip("node is required for JS research evaluator compatibility test")
    capture = build_local_sample_capture(tmp_path)
    report_path = tmp_path / "report.json"

    completed = subprocess.run(
        [
            "node",
            str(RESEARCH_EVAL_SCRIPT),
            "--benchmark",
            str(RESEARCH_BENCHMARK),
            "--run",
            str(capture.run_path),
            "--artifacts",
            str(capture.artifacts_path),
            "--out",
            str(report_path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    report = json.loads(report_path.read_text())
    assert report["ok"] is True
    assert report["threshold"] == "incomplete"
    assert {
        "no_sources_inspected",
        "child_evidence_unused",
        "gate_scope_overreach",
    }.issubset(set(report["failureCategories"]))


def test_import_boundary_avoids_forbidden_runtime_modules() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import importlib
import json
import sys

before = set(sys.modules)
importlib.import_module("openmagi_core_agent.shadow.research_runner_capture")
after = set(sys.modules) - before
forbidden = [
    "google.adk",
    "openmagi_core_agent.adk_bridge.runner_adapter",
    "openmagi_core_agent.transport",
    "openmagi_core_agent.web_acquisition.provider_boundary",
    "openmagi_core_agent.browser.provider_boundary",
    "openmagi_core_agent.knowledge.provider_boundary",
    "openmagi_core_agent.memory",
    "openmagi_core_agent.tools.dispatcher",
    "openmagi_core_agent.tools.kernel",
]
print(json.dumps(sorted(name for name in after if any(name == prefix or name.startswith(prefix + ".") for prefix in forbidden))))
""",
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    assert json.loads(completed.stdout) == []


def test_module_import_is_side_effect_free() -> None:
    module = importlib.import_module("openmagi_core_agent.shadow.research_runner_capture")

    assert module.RESEARCH_CAPTURE_DEFAULT_ENABLED is False
