"""Dependency-inverted host boundaries for dormant execution authority."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from magi_agent.execution_authority.contracts import (
    AuthorityResumeBinding,
    UserDecisionReceipt,
    UserDecisionRequest,
)


@runtime_checkable
class UserDecisionVerifierPort(Protocol):
    def verify(
        self,
        *,
        opaque_envelope: object,
        request: UserDecisionRequest,
    ) -> UserDecisionReceipt: ...


@runtime_checkable
class UserDecisionKeyPort(Protocol):
    def key_for(
        self,
        *,
        key_id: str,
        tenant_id: str,
        principal_id: str,
        authentication_context_digest: str,
    ) -> bytes | None: ...


@runtime_checkable
class ResumeBindingVerifierPort(Protocol):
    def verify_current(
        self,
        binding: AuthorityResumeBinding,
    ) -> AuthorityResumeBinding: ...
