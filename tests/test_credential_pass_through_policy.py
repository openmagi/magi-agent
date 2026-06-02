from __future__ import annotations

import pytest
from pydantic import ValidationError

from openmagi_core_agent.security.credentials import (
    CredentialDecision,
    CredentialPassThroughPolicy,
    CredentialRequest,
    evaluate_credential_request,
)


def test_raw_credential_values_are_rejected() -> None:
    raw_value = "sk-" + "not-real-" + "credential"
    with pytest.raises(ValueError) as exc_info:
        CredentialRequest(
            credential_name="OPENAI_API_KEY",
            source="environment",
            raw_value=raw_value,
            destination="sandbox",
        )

    assert raw_value not in str(exc_info.value)


def test_not_allowlisted_credential_is_denied() -> None:
    decision = evaluate_credential_request(
        CredentialRequest(
            credential_name="GITHUB_TOKEN",
            source="user",
            lease_ref="credential-lease:github:1234",
            destination="sandbox",
        ),
        CredentialPassThroughPolicy(allowed_names=("OPENAI_API_KEY",)),
    )

    assert decision.allowed is False
    assert decision.reason_codes == ("credential_not_allowlisted",)
    assert "leaseRef" not in decision.public_projection()


def test_allowlisted_credential_requires_lease_ref() -> None:
    decision = evaluate_credential_request(
        CredentialRequest(
            credential_name="OPENAI_API_KEY",
            source="user",
            destination="sandbox",
        ),
        CredentialPassThroughPolicy(allowed_names=("OPENAI_API_KEY",)),
    )

    assert decision.allowed is False
    assert decision.reason_codes == ("credential_lease_required",)


def test_allowlisted_lease_is_projected_without_secret_material() -> None:
    decision = evaluate_credential_request(
        CredentialRequest(
            credential_name="OPENAI_API_KEY",
            source="user",
            lease_ref="credential-lease:openai:1234",
            destination="sandbox",
        ),
        CredentialPassThroughPolicy(allowed_names=("OPENAI_API_KEY",)),
    )

    assert decision.allowed is True
    assert decision.reason_codes == ("credential_lease_allowed",)
    assert decision.public_projection() == {
        "credentialName": "OPENAI_API_KEY",
        "source": "user",
        "destination": "sandbox",
        "allowed": True,
        "reasonCodes": ["credential_lease_allowed"],
        "leaseRef": "credential-lease:openai:1234",
    }


def test_policy_rejects_invalid_allowed_names() -> None:
    with pytest.raises(ValidationError):
        CredentialPassThroughPolicy(allowed_names=("OPENAI_API_KEY", "sk-not-real"))


def test_secret_shaped_credential_name_is_rejected() -> None:
    aws_shape = "AKIA" + "IOSFODNN7EXAMPLE"

    with pytest.raises(ValidationError):
        CredentialRequest(
            credential_name=aws_shape,
            source="user",
            destination="sandbox",
            lease_ref="credential-lease:aws:1234",
        )

    with pytest.raises(ValidationError):
        CredentialPassThroughPolicy(allowed_names=(aws_shape,))


def test_bypassed_raw_secret_material_never_projects() -> None:
    raw_value = "sk-" + "not-real-" + "credential"
    request = CredentialRequest.model_construct(
        credential_name="sk-not-real",
        source="/private/source",
        destination="provider",
        lease_ref="/private/lease",
        raw_value=raw_value,
    )
    decision = CredentialDecision.model_construct(
        allowed=True,
        reason_codes=("credential_lease_allowed", "provider_payload_ref"),
        request=request,
    )

    projection = decision.public_projection()
    dumped = repr(projection)

    assert raw_value not in dumped
    assert "/private" not in dumped
    assert "sk-not-real" not in dumped
    assert "provider_payload_ref" not in dumped
    assert projection["credentialName"] == "redacted"
    assert projection["source"] == "unknown"
    assert projection["allowed"] is False
    assert projection["reasonCodes"] == ["credential_lease_allowed", "redacted"]
    assert "leaseRef" not in projection


def test_public_projection_does_not_publish_forged_denial_allowed_state() -> None:
    decision = CredentialDecision.model_construct(
        allowed=True,
        reason_codes=("credential_not_allowlisted",),
        request=CredentialRequest.model_construct(
            credential_name="OPENAI_API_KEY",
            source="user",
            destination="sandbox",
            lease_ref="credential-lease:openai:1234",
            raw_value=None,
        ),
    )

    projection = decision.public_projection()

    assert decision.allowed is True
    assert projection["allowed"] is False
    assert "leaseRef" not in projection


def test_public_projection_does_not_publish_forged_allowed_reason() -> None:
    decision = CredentialDecision.model_construct(
        allowed=True,
        reason_codes=("credential_lease_allowed",),
        request=CredentialRequest.model_construct(
            credential_name="OPENAI_API_KEY",
            source="user",
            destination="sandbox",
            lease_ref="credential-lease:openai:1234",
            raw_value=None,
        ),
    )

    projection = decision.public_projection()

    assert decision.allowed is True
    assert projection["allowed"] is False
    assert "leaseRef" not in projection


def test_model_copy_cannot_forge_allowed_projection() -> None:
    decision = evaluate_credential_request(
        CredentialRequest(
            credential_name="OPENAI_API_KEY",
            source="user",
            destination="sandbox",
        ),
        CredentialPassThroughPolicy(allowed_names=("OPENAI_API_KEY",)),
    ).model_copy(
        update={
            "allowed": True,
            "reason_codes": ("credential_lease_allowed",),
            "request": CredentialRequest(
                credential_name="OPENAI_API_KEY",
                source="user",
                destination="sandbox",
                lease_ref="credential-lease:openai:1234",
            ),
        },
    )

    projection = decision.public_projection()

    assert decision.allowed is True
    assert projection["allowed"] is False
    assert "leaseRef" not in projection


def test_bypassed_invalid_credential_name_fails_closed() -> None:
    decision = evaluate_credential_request(
        CredentialRequest.model_construct(
            credential_name="sk-not-real",
            source="user",
            destination="sandbox",
            lease_ref="credential-lease:openai:1234",
            raw_value=None,
        ),
        CredentialPassThroughPolicy.model_construct(allowed_names=("sk-not-real",)),
    )

    assert decision.allowed is False
    assert decision.reason_codes == ("invalid_credential_name",)
    assert decision.public_projection()["credentialName"] == "redacted"


def test_bypassed_invalid_lease_ref_fails_closed() -> None:
    decision = evaluate_credential_request(
        CredentialRequest.model_construct(
            credential_name="OPENAI_API_KEY",
            source="user",
            destination="sandbox",
            lease_ref="/private/lease",
            raw_value=None,
        ),
        CredentialPassThroughPolicy(allowed_names=("OPENAI_API_KEY",)),
    )

    projection = decision.public_projection()

    assert decision.allowed is False
    assert decision.reason_codes == ("invalid_credential_lease_ref",)
    assert projection["allowed"] is False
    assert "leaseRef" not in projection


def test_sensitive_lease_ref_label_fails_closed() -> None:
    lease_ref = "credential-lease:" + "secret" + ":1234"
    decision = evaluate_credential_request(
        CredentialRequest(
            credential_name="OPENAI_API_KEY",
            source="user",
            destination="sandbox",
            lease_ref=lease_ref,
        ),
        CredentialPassThroughPolicy(allowed_names=("OPENAI_API_KEY",)),
    )

    projection = decision.public_projection()
    dumped = repr(projection)

    assert decision.allowed is False
    assert decision.reason_codes == ("invalid_credential_lease_ref",)
    assert lease_ref not in dumped
    assert "leaseRef" not in projection


def test_secret_shaped_lease_ref_fails_closed() -> None:
    lease_ref = "credential-lease:" + "sk-" + "not-real:1234"
    decision = evaluate_credential_request(
        CredentialRequest(
            credential_name="OPENAI_API_KEY",
            source="user",
            destination="sandbox",
            lease_ref=lease_ref,
        ),
        CredentialPassThroughPolicy(allowed_names=("OPENAI_API_KEY",)),
    )

    projection = decision.public_projection()
    dumped = repr(projection)

    assert decision.allowed is False
    assert decision.reason_codes == ("invalid_credential_lease_ref",)
    assert lease_ref not in dumped
    assert "leaseRef" not in projection
