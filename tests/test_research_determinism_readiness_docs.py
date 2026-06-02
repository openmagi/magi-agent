from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[4]
README_PATH = REPO_ROOT / "infra" / "docker" / "clawy-core-agent-python" / "README.md"
READINESS_NOTE_PATH = (
    REPO_ROOT
    / "docs"
    / "notes"
    / "2026-05-26-first-party-research-determinism-harness-readiness.md"
)

REQUIRED_README_PHRASES = (
    "First-Party Research Determinism Harness",
    "evidence graph, not citation decoration",
    "Action proof",
    "Source proof",
    "Claim proof",
    "Task proof",
    "Intermediate and final boundary enforcement",
    "Repair semantics",
    "Default-off activation blockers",
)

REQUIRED_NOTE_HEADINGS = (
    "# First-Party Research Determinism Harness Readiness",
    "## What Exists",
    "## Core And Harness Ownership",
    "## Proof Contracts",
    "## Boundary Enforcement",
    "## Repair Semantics",
    "## ADK And ToolHost Ownership",
    "## Implemented Contract Surface",
    "## Examples",
    "## Activation Blockers",
    "## Readiness Summary",
)

REQUIRED_NOTE_PHRASES = (
    "research runtime is an evidence graph, not citation decoration",
    "runtime-issued receipts",
    "opened snapshot",
    "support span",
    "acceptance criteria",
    "child evidence envelopes",
    "ToolHost",
    "ADK `Agent` and `Runner`",
    "default-off/local-only/fake-provider",
    "No live provider",
    "No production routing",
    "\"I reviewed pricing\"",
    "\"Competitor A targets enterprise customers\"",
)

FORBIDDEN_ACTIVATION_PHRASES = (
    "live activation is enabled",
    "live web/search/fetch is enabled",
    "browser execution is enabled",
    "production routing is enabled",
    "live provider calls are enabled",
    "live model calls are enabled",
    "ToolHost dispatch is enabled",
    "ADK Runner is attached live",
    "ADK FunctionTool projection is attached live",
    "memory writes are enabled",
    "channel delivery is enabled",
    "deploy/K8s/env changes are part of this track",
    "deploy/Kubernetes/env/secret changes are part of this track",
    "frontend changes are part of this track",
    "Supabase changes are part of this track",
    "provisioning changes are part of this track",
    "chat-proxy changes are part of this track",
    "user-visible Python authority is enabled",
)


def test_research_determinism_readme_documents_readiness_contract() -> None:
    readme = README_PATH.read_text()

    for phrase in REQUIRED_README_PHRASES:
        assert phrase in readme


def test_research_determinism_readiness_note_documents_activation_boundary() -> None:
    note = READINESS_NOTE_PATH.read_text()

    for heading in REQUIRED_NOTE_HEADINGS:
        assert heading in note
    for phrase in REQUIRED_NOTE_PHRASES:
        assert phrase in note


def test_research_determinism_docs_do_not_enable_live_authority() -> None:
    docs = {
        "README.md": README_PATH.read_text(),
        READINESS_NOTE_PATH.name: READINESS_NOTE_PATH.read_text(),
    }

    for doc_name, text in docs.items():
        for phrase in FORBIDDEN_ACTIVATION_PHRASES:
            assert phrase not in text, (doc_name, phrase)
