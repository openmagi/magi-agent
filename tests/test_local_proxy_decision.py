"""Unit tests for the pure local-proxy decision core (no mitmproxy needed).

These cover host resolution, the three injection decisions, the header plans per
auth scheme, the approval-gating behavior, and the two security invariants:
(a) the secret is never present on the decision object, and (b) the decision
layer never logs a secret (there is nowhere for one to live).
"""

from __future__ import annotations

import logging

import pytest

from magi_agent.credentials_admin.local_proxy_decision import (
    SERVICE_HOST_MAP,
    BlockPendingApproval,
    Inject,
    PassThrough,
    decide_injection,
    resolve_host,
)

SECRET = "sk-live-abcd1234EFGH5678ijkl9012MNOP3456"


def _cred(**overrides):
    base = {
        "id": "cred-1",
        "service": "notion",
        "label": "Notion key",
        "auth_scheme": "bearer",
        "status": "active",
        "vault_ref": "ref-abc",
        "requires_approval": False,
        "host": None,
    }
    base.update(overrides)
    return base


def _never_approved(_credential_id: str) -> bool:
    return False


def _always_approved(_credential_id: str) -> bool:
    return True


# -- resolve_host ----------------------------------------------------------


def test_resolve_host_explicit_wins() -> None:
    cred = _cred(service="notion", host="custom.example.com")
    assert resolve_host(cred) == "custom.example.com"


def test_resolve_host_falls_back_to_service_map() -> None:
    assert resolve_host(_cred(service="slack", host=None)) == SERVICE_HOST_MAP["slack"]
    assert resolve_host(_cred(service="github", host=None)) == "api.github.com"


def test_resolve_host_none_when_unknown_and_no_explicit() -> None:
    assert resolve_host(_cred(service="mystery", host=None)) is None


# -- decide_injection: pass-through ---------------------------------------


def test_decide_pass_through_when_no_active_cred_for_host() -> None:
    creds = [_cred(service="slack")]  # resolves to api.slack.com
    decision = decide_injection(
        host="api.notion.com",
        credentials=creds,
        approvals_lookup=_never_approved,
    )
    assert isinstance(decision, PassThrough)


def test_decide_pass_through_when_cred_not_active() -> None:
    creds = [_cred(status="revoked", service="notion")]
    decision = decide_injection(
        host="api.notion.com",
        credentials=creds,
        approvals_lookup=_never_approved,
    )
    assert isinstance(decision, PassThrough)


def test_decide_pass_through_when_active_cred_has_no_vault_ref() -> None:
    creds = [_cred(service="notion", vault_ref=None)]
    decision = decide_injection(
        host="api.notion.com",
        credentials=creds,
        approvals_lookup=_never_approved,
    )
    assert isinstance(decision, PassThrough)


# -- decide_injection: inject (header plans) ------------------------------


def test_decide_inject_bearer_plan() -> None:
    decision = decide_injection(
        host="api.notion.com",
        credentials=[_cred(service="notion", auth_scheme="bearer")],
        approvals_lookup=_never_approved,
    )
    assert isinstance(decision, Inject)
    assert decision.vault_ref == "ref-abc"
    assert decision.header_name == "Authorization"
    assert decision.value_prefix == "Bearer "


def test_decide_inject_basic_plan() -> None:
    decision = decide_injection(
        host="api.stripe.com",
        credentials=[_cred(service="stripe", auth_scheme="basic")],
        approvals_lookup=_never_approved,
    )
    assert isinstance(decision, Inject)
    assert decision.header_name == "Authorization"
    assert decision.value_prefix == "Basic "


def test_decide_inject_api_key_uses_header_name_no_prefix() -> None:
    cred = _cred(
        service="notion",
        auth_scheme="api_key",
        header_name="X-Api-Key",
    )
    decision = decide_injection(
        host="api.notion.com",
        credentials=[cred],
        approvals_lookup=_never_approved,
    )
    assert isinstance(decision, Inject)
    assert decision.header_name == "X-Api-Key"
    assert decision.value_prefix == ""


def test_decide_inject_api_key_defaults_authorization_when_no_header_name() -> None:
    decision = decide_injection(
        host="api.notion.com",
        credentials=[_cred(service="notion", auth_scheme="api_key")],
        approvals_lookup=_never_approved,
    )
    assert isinstance(decision, Inject)
    assert decision.header_name == "Authorization"
    assert decision.value_prefix == ""


def test_decide_matches_explicit_host_over_service_map() -> None:
    cred = _cred(service="notion", host="internal.example.com", auth_scheme="bearer")
    # The service map would point at api.notion.com, but the explicit host wins,
    # so a request to api.notion.com no longer matches.
    assert isinstance(
        decide_injection(
            host="api.notion.com",
            credentials=[cred],
            approvals_lookup=_never_approved,
        ),
        PassThrough,
    )
    assert isinstance(
        decide_injection(
            host="internal.example.com",
            credentials=[cred],
            approvals_lookup=_never_approved,
        ),
        Inject,
    )


# -- decide_injection: approval gating ------------------------------------


def test_decide_block_pending_approval_when_required_and_not_approved() -> None:
    cred = _cred(service="notion", requires_approval=True)
    decision = decide_injection(
        host="api.notion.com",
        credentials=[cred],
        approvals_lookup=_never_approved,
    )
    assert isinstance(decision, BlockPendingApproval)
    assert decision.credential_id == "cred-1"


def test_decide_inject_when_required_and_approved() -> None:
    cred = _cred(service="notion", requires_approval=True)
    decision = decide_injection(
        host="api.notion.com",
        credentials=[cred],
        approvals_lookup=_always_approved,
    )
    assert isinstance(decision, Inject)
    assert decision.vault_ref == "ref-abc"


# -- security invariants ---------------------------------------------------


def test_secret_never_in_decision_object() -> None:
    """The decision must carry only the vault_ref + header plan — never a secret.

    The decision layer never even receives the plaintext (it is fetched later in
    the addon), so this asserts the structural guarantee.
    """
    cred = _cred(service="notion", auth_scheme="bearer")
    decision = decide_injection(
        host="api.notion.com",
        credentials=[cred],
        approvals_lookup=_never_approved,
    )
    assert isinstance(decision, Inject)
    serialized = repr(decision)
    assert SECRET not in serialized
    # Only the opaque ref + plan are present.
    assert "ref-abc" in serialized
    assert "Bearer " in serialized


def test_decision_never_logs_secret(caplog) -> None:
    cred = _cred(service="notion", auth_scheme="bearer")
    with caplog.at_level(logging.DEBUG):
        decide_injection(
            host="api.notion.com",
            credentials=[cred],
            approvals_lookup=_never_approved,
        )
    assert SECRET not in caplog.text
