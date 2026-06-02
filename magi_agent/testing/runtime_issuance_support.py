from __future__ import annotations

from collections.abc import Iterable
from weakref import finalize

from magi_agent.evidence import runtime_issuance as runtime_issuance_module
from magi_agent.evidence.runtime_issuance import (
    RuntimeIssueAuthority,
    RuntimeIssueScope,
)


def issue_test_runtime_authority(
    *,
    authority_id: str,
    scopes: Iterable[RuntimeIssueScope],
) -> RuntimeIssueAuthority:
    authority = RuntimeIssueAuthority(
        authorityId=authority_id,
        scopes=tuple(dict.fromkeys(scopes)),
    )
    object_id = id(authority)
    authority.__pydantic_private__["_issued_by_runtime_boundary"] = True
    runtime_issuance_module._AUTHORITY_OBJECT_IDS.add(object_id)
    runtime_issuance_module._AUTHORITY_FINGERPRINTS[object_id] = (
        runtime_issuance_module._authority_fingerprint(authority)
    )
    runtime_issuance_module._AUTHORITY_FINALIZERS[object_id] = finalize(
        authority,
        runtime_issuance_module._discard_authority_object_id,
        object_id,
    )
    return authority
