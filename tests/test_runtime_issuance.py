from __future__ import annotations

import inspect

import pytest

from magi_agent.evidence import runtime_issuance as runtime_issuance_module
from magi_agent.evidence.runtime_issuance import (
    RuntimeIssueAuthority,
    require_runtime_issue_authority,
)

from runtime_issuance_support import issue_test_runtime_authority


def test_runtime_issuance_core_does_not_encode_domain_policy() -> None:
    source = inspect.getsource(runtime_issuance_module)

    assert "research" not in source
    assert "citation" not in source
    assert "searched" not in source
    assert "source_proof" not in source
    assert "_issue_runtime_authority" not in source

    authority = issue_test_runtime_authority(
        authority_id="authority:domain-neutral",
        scopes=("domain-neutral-scope",),
    )

    assert (
        require_runtime_issue_authority(authority, scope="domain-neutral-scope")
        is authority
    )
    with pytest.raises(RuntimeError, match="runtime issue authority"):
        require_runtime_issue_authority(authority, scope="other-scope")


def test_structural_runtime_issue_authority_is_not_accepted() -> None:
    structural = RuntimeIssueAuthority(
        authorityId="authority:forged",
        scopes=("domain-neutral-scope",),
    )

    with pytest.raises(RuntimeError, match="runtime issue authority"):
        require_runtime_issue_authority(structural, scope="domain-neutral-scope")
    with pytest.raises(TypeError):
        RuntimeIssueAuthority.model_construct(
            authorityId="authority:constructed",
            scopes=("domain-neutral-scope",),
        )


def test_runtime_issue_authority_rejects_private_scope_names_before_error_projection() -> None:
    authority = issue_test_runtime_authority(
        authority_id="authority:domain-neutral-private-scope",
        scopes=("domain-neutral-scope",),
    )

    with pytest.raises(ValueError, match="digest-safe public id"):
        require_runtime_issue_authority(authority, scope="/Users/private/scope")
