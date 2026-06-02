from __future__ import annotations

import json
import re
from hashlib import sha256
from pathlib import Path


MATRIX = Path("tests/fixtures/security/hermes_security_setup_matrix.json")

REQUIRED_IDS = {
    "os_level_isolation_boundary",
    "external_surface_allowlist_fail_closed",
    "approval_gate_heuristic",
    "credential_scope_and_pass_through",
    "web_ssrf_egress_policy",
    "context_file_injection_guard",
    "supply_chain_advisory_and_lazy_deps",
    "dashboard_api_loopback_default",
}

ALLOWED_OWNERS = {
    "core-config-substrate",
    "first-party-security-harness",
    "first-party-native-plugin",
    "first-party-recipe",
    "existing-toolhost-policy",
    "existing-web-acquisition-policy",
    "infra-approval-track",
}

ALLOWED_BOUNDARY_CLASSES = {
    "os-boundary",
    "authorization-boundary",
    "credential-boundary",
    "heuristic",
    "metadata-only",
}
SHA256_RE = re.compile(r"^sha256:[a-f0-9]{64}$")

EXPECTED_SOURCE_METADATA = {
    "approval_gate_heuristic": (
        "https://github.com/NousResearch/hermes-agent/security",
        "security-policy",
        "2.4 In-Process Heuristics",
    ),
    "context_file_injection_guard": (
        "https://hermes-agent.nousresearch.com/docs/user-guide/security",
        "official-docs",
        "Context File Injection Protection",
    ),
    "credential_scope_and_pass_through": (
        "https://hermes-agent.nousresearch.com/docs/user-guide/security",
        "official-docs",
        "Environment Variable Passthrough",
    ),
    "dashboard_api_loopback_default": (
        "https://hermes-agent.nousresearch.com/docs/user-guide/docker",
        "official-docs",
        "Running in gateway mode and Running the dashboard",
    ),
    "external_surface_allowlist_fail_closed": (
        "https://github.com/NousResearch/hermes-agent/security",
        "security-policy",
        "2.6 External Surfaces",
    ),
    "os_level_isolation_boundary": (
        "https://github.com/NousResearch/hermes-agent/security",
        "security-policy",
        "2.2 The Boundary: OS-Level Isolation",
    ),
    "supply_chain_advisory_and_lazy_deps": (
        "https://hermes-agent.nousresearch.com/docs/user-guide/security",
        "official-docs",
        "Supply-chain advisory checking and Lazy install of optional dependencies",
    ),
    "web_ssrf_egress_policy": (
        "https://hermes-agent.nousresearch.com/docs/user-guide/security",
        "official-docs",
        "SSRF Protection",
    ),
}


def _claim_digest(row: dict[str, object]) -> str:
    payload = "\n".join(
        str(row[key])
        for key in (
            "id",
            "finding",
            "sourceUrl",
            "sourceType",
            "sourceSection",
            "checkedAt",
        )
    )
    return f"sha256:{sha256(payload.encode('utf-8')).hexdigest()}"


def test_hermes_security_setup_matrix_is_complete_and_default_off() -> None:
    data = json.loads(MATRIX.read_text())
    assert data["schemaVersion"] == 1
    rows = data["rows"]
    by_id = {row["id"]: row for row in rows}

    assert len(rows) == len(REQUIRED_IDS)
    assert set(by_id) == REQUIRED_IDS
    for row in rows:
        expected_url, expected_type, expected_section = EXPECTED_SOURCE_METADATA[
            row["id"]
        ]
        assert row["owner"] in ALLOWED_OWNERS
        assert row["boundaryClass"] in ALLOWED_BOUNDARY_CLASSES
        assert row["sourceUrl"] == expected_url
        assert row["sourceType"] == expected_type
        assert row["sourceSection"] == expected_section
        assert SHA256_RE.fullmatch(row["sourceClaimDigest"])
        assert row["sourceClaimDigest"] == _claim_digest(row)
        assert row["checkedAt"] == "2026-05-25"
        assert row["openmagiSurface"]
        assert row["activationGate"]
        assert row["defaultOff"] is True
        assert row["productionMutationAuthorized"] is False
