"""J-10 — single source of truth for credential-register payload validation.

``transport/credentials._validate_body`` and
``credentials_admin/vault_server._validate_body`` carried two
byte-identical copies of the register-payload validator. The
vault_server copy's leading comment even said "Mirrors
transport.credentials' validation", admitting the drift risk. J-10
consolidates both behind
``credentials_admin/payload.validate_register_body``.

This module locks the no-drift invariant via meta-tests + parity
checks.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from magi_agent.credentials_admin.payload import (
    MAX_FIELD_LEN,
    RegisterFields,
    RegisterPayloadError,
    validate_register_body,
)


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_validate_register_body_minimal_payload() -> None:
    body = {
        "service": "github",
        "label": "personal",
        "auth_scheme": "bearer",
        "secret": "sk-test",
    }
    out = validate_register_body(body)
    assert isinstance(out, RegisterFields)
    assert out.service == "github"
    assert out.label == "personal"
    assert out.auth_scheme == "bearer"
    assert out.secret == "sk-test"
    assert out.requires_approval is False
    assert out.host is None


def test_validate_register_body_with_host_and_approval() -> None:
    body = {
        "service": "github",
        "label": "personal",
        "auth_scheme": "bearer",
        "secret": "sk-test",
        "requires_approval": True,
        "host": "api.github.com",
    }
    out = validate_register_body(body)
    assert isinstance(out, RegisterFields)
    assert out.requires_approval is True
    assert out.host == "api.github.com"


def test_validate_register_body_strips_whitespace() -> None:
    body = {
        "service": "  github  ",
        "label": "  personal  ",
        "auth_scheme": "  bearer  ",
        "secret": "sk-test",
    }
    out = validate_register_body(body)
    assert isinstance(out, RegisterFields)
    assert out.service == "github"
    assert out.label == "personal"
    assert out.auth_scheme == "bearer"


def test_validate_register_body_preserves_secret_raw() -> None:
    """The secret field is NOT stripped or normalized — only required to
    be a non-blank string. Locks back-compat with the legacy
    ``str(secret)`` return."""

    body = {
        "service": "github",
        "label": "personal",
        "auth_scheme": "bearer",
        "secret": "  sk-test-with-spaces  ",
    }
    out = validate_register_body(body)
    assert isinstance(out, RegisterFields)
    assert out.secret == "  sk-test-with-spaces  "


# ---------------------------------------------------------------------------
# Error paths
# ---------------------------------------------------------------------------


def test_validate_register_body_object_required() -> None:
    out = validate_register_body("not a dict")
    assert isinstance(out, RegisterPayloadError)
    assert out.error == "object_required"
    assert out.field is None


def test_validate_register_body_non_bool_requires_approval() -> None:
    body = {
        "service": "x",
        "label": "y",
        "auth_scheme": "z",
        "secret": "s",
        "requires_approval": "yes",  # str, not bool
    }
    out = validate_register_body(body)
    assert isinstance(out, RegisterPayloadError)
    assert out.error == "field_invalid"
    assert out.field == "requires_approval"


@pytest.mark.parametrize(
    "missing_field", ["service", "label", "auth_scheme", "secret"]
)
def test_validate_register_body_missing_required(missing_field: str) -> None:
    body = {
        "service": "x",
        "label": "y",
        "auth_scheme": "z",
        "secret": "s",
    }
    del body[missing_field]
    out = validate_register_body(body)
    assert isinstance(out, RegisterPayloadError)
    assert out.error == "field_required"
    assert out.field == missing_field


@pytest.mark.parametrize(
    "blank_field", ["service", "label", "auth_scheme", "secret"]
)
def test_validate_register_body_blank_required(blank_field: str) -> None:
    body = {
        "service": "x",
        "label": "y",
        "auth_scheme": "z",
        "secret": "s",
    }
    body[blank_field] = "   "
    out = validate_register_body(body)
    assert isinstance(out, RegisterPayloadError)
    assert out.error == "field_required"
    assert out.field == blank_field


def test_validate_register_body_field_too_long() -> None:
    body = {
        "service": "x" * (MAX_FIELD_LEN + 1),
        "label": "y",
        "auth_scheme": "z",
        "secret": "s",
    }
    out = validate_register_body(body)
    assert isinstance(out, RegisterPayloadError)
    assert out.error == "field_too_long"
    assert out.field == "service"


def test_validate_register_body_secret_length_unbounded() -> None:
    """The secret field is explicitly exempt from the length cap (so a
    very long token isn't rejected). Locks legacy behavior."""

    body = {
        "service": "x",
        "label": "y",
        "auth_scheme": "z",
        "secret": "s" * (MAX_FIELD_LEN * 4),  # 4× the cap
    }
    out = validate_register_body(body)
    assert isinstance(out, RegisterFields)


@pytest.mark.parametrize(
    "host_value,error_code",
    [
        ("", "field_invalid"),
        ("   ", "field_invalid"),
        (123, "field_invalid"),
        ("x" * (MAX_FIELD_LEN + 1), "field_too_long"),
    ],
)
def test_validate_register_body_host_invalid(
    host_value: object, error_code: str
) -> None:
    body = {
        "service": "x",
        "label": "y",
        "auth_scheme": "z",
        "secret": "s",
        "host": host_value,
    }
    out = validate_register_body(body)
    assert isinstance(out, RegisterPayloadError)
    assert out.error == error_code
    assert out.field == "host"


def test_validate_register_body_never_echoes_secret_in_error() -> None:
    """Security invariant: an error response must never carry the
    secret value. Locks the no-leak contract — the secret field is
    checked for presence only."""

    body = {
        "service": "",  # triggers field_required
        "label": "y",
        "auth_scheme": "z",
        "secret": "my-very-secret-token",
    }
    out = validate_register_body(body)
    assert isinstance(out, RegisterPayloadError)
    # Error carries error code + field name only.
    assert out.error == "field_required"
    assert out.field == "service"
    # Defensive: the dataclass has no other fields that could leak the
    # secret. Locks via reflection.
    assert set(out.__dataclass_fields__.keys()) == {"error", "field"}


# ---------------------------------------------------------------------------
# Meta-test: forbid a third copy of the validator from landing anywhere.
# ---------------------------------------------------------------------------


def test_only_payload_module_defines_validate_register_body() -> None:
    package_root = Path(__file__).resolve().parents[1] / "magi_agent"
    if not package_root.exists():
        package_root = Path(__file__).resolve().parents[2] / "magi_agent"
    assert package_root.exists()

    canonical = {"payload.py"}
    offenders: list[str] = []
    for path in package_root.rglob("*.py"):
        if path.name in canonical:
            continue
        if "tests" in path.relative_to(package_root).parts:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if "def validate_register_body(" in text:
            offenders.append(str(path.relative_to(package_root)))
    assert offenders == [], (
        "Second definition of ``validate_register_body`` outside "
        "``credentials_admin/payload.py``. Both ``transport/credentials._validate_body`` "
        "and ``credentials_admin/vault_server._validate_body`` must "
        "delegate, not redefine. "
        f"Offenders: {offenders}"
    )


def test_credentials_modules_do_not_redefine_max_field_len() -> None:
    """Both register-body call sites (``transport/credentials.py`` and
    ``credentials_admin/vault_server.py``) used to carry a private
    ``_MAX_FIELD_LEN = 256``. After J-10 they consult
    ``credentials_admin/payload.MAX_FIELD_LEN`` via
    ``validate_register_body`` and must not redefine the constant.

    Scoped narrowly to the two credentials modules — other modules
    (e.g. ``transport/integrations.py``) have their own unrelated
    ``_MAX_FIELD_LEN`` caps that are NOT a drift concern.
    """

    package_root = Path(__file__).resolve().parents[1] / "magi_agent"
    if not package_root.exists():
        package_root = Path(__file__).resolve().parents[2] / "magi_agent"
    assert package_root.exists()

    scoped = (
        package_root / "transport" / "credentials.py",
        package_root / "credentials_admin" / "vault_server.py",
    )
    offenders: list[str] = []
    for path in scoped:
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if "_MAX_FIELD_LEN = " in text:
            offenders.append(str(path.relative_to(package_root)))
    assert offenders == [], (
        "Credentials register-body modules must not redefine "
        "``_MAX_FIELD_LEN`` — use ``credentials_admin/payload.MAX_FIELD_LEN``. "
        f"Offenders: {offenders}"
    )
