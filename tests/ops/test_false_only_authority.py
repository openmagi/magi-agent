"""Unit tests for ``magi_agent.ops.authority.FalseOnlyAuthorityModel`` (C-4 PR-A).

This base introspects ``Literal[False]`` fields (also ``Optional[Literal[False]]``
and ``Field(default=False, alias=...)`` with a ``Literal[False]`` annotation) and
force-falses every such field on every construction surface AND on serialization
-- replacing the hand-maintained ``_FALSE_ONLY_FIELDS`` tuple + per-class
``field_serializer`` list pattern repeated 15+ times across the tree.

The tests deliberately use synthetic fixture models (no production model is
re-parented in this PR) so we exercise the new base in isolation. The
companion meta-test (``tests/meta/test_every_literal_false_roundtrips.py``)
captures the CURRENT behavior of every existing force-false model and locks
it as a golden -- the migration PRs use those goldens as the round-trip gate.
"""

from __future__ import annotations

from typing import Literal

import pytest
from pydantic import BaseModel, Field, ValidationError

from magi_agent.ops.authority import (
    FalseOnlyAuthorityModel,
    _annotation_is_literal_false,
    _is_literal_false,
)


# ---------------------------------------------------------------------------
# Fixture models
# ---------------------------------------------------------------------------


class FixtureAuthority(FalseOnlyAuthorityModel):
    """Mixed-field fixture: 3 Literal[False] + 2 plain bool + 1 Optional[Literal[False]]
    + 1 aliased Literal[False]."""

    a: Literal[False] = False
    b: Literal[False] = False
    c: Literal[False] = False
    plain_x: bool = False
    plain_y: bool = True
    maybe_false: Literal[False] | None = None
    aliased_false: Literal[False] = Field(default=False, alias="someField")


class FixtureEmpty(FalseOnlyAuthorityModel):
    """Subclass with NO Literal[False] field -- empty introspection result."""

    value: int = 0
    name: str = "x"


# ---------------------------------------------------------------------------
# Introspection helpers (low-level)
# ---------------------------------------------------------------------------


def test_is_literal_false_accepts_only_exact_literal_false() -> None:
    assert _is_literal_false(Literal[False]) is True
    assert _is_literal_false(Literal[True]) is False
    # Literal[0] is rejected -- 0 is not the bool False (identity check).
    assert _is_literal_false(Literal[0]) is False
    assert _is_literal_false(bool) is False
    assert _is_literal_false(int) is False
    assert _is_literal_false(None) is False


def test_annotation_is_literal_false_accepts_optional() -> None:
    assert _annotation_is_literal_false(Literal[False]) is True
    assert _annotation_is_literal_false(Literal[False] | None) is True
    # The Union form must be EXACTLY {Literal[False], None}; wider unions reject.
    assert _annotation_is_literal_false(Literal[False] | Literal[True]) is False
    assert _annotation_is_literal_false(bool | None) is False
    assert _annotation_is_literal_false(int) is False


# ---------------------------------------------------------------------------
# _false_only_fields() introspection + caching
# ---------------------------------------------------------------------------


def test_false_only_fields_lists_literal_false_fields() -> None:
    names = FixtureAuthority._false_only_fields()
    # All four Literal[False]-typed fields (incl. Optional + aliased) appear,
    # in declaration order; plain bool fields do not.
    assert names == ("a", "b", "c", "maybe_false", "aliased_false")


def test_false_only_fields_empty_for_model_without_literal_false() -> None:
    assert FixtureEmpty._false_only_fields() == ()


def test_false_only_fields_cached_per_class() -> None:
    """Second call returns the SAME tuple object (id-equal) -- proves caching."""
    first = FixtureAuthority._false_only_fields()
    second = FixtureAuthority._false_only_fields()
    assert first is second


# ---------------------------------------------------------------------------
# model_validate: force-false on construct, leave plain bools alone
# ---------------------------------------------------------------------------


def test_model_validate_forces_literal_false_fields_even_when_asserted_true() -> None:
    # A malicious payload tries to assert True on every Literal[False] field.
    instance = FixtureAuthority.model_validate(
        {
            "a": True,
            "b": True,
            "c": True,
            "plain_x": True,
            "plain_y": False,
            "maybe_false": True,
            "someField": True,  # aliased form
        }
    )
    assert instance.a is False
    assert instance.b is False
    assert instance.c is False
    # Plain bool fields are untouched.
    assert instance.plain_x is True
    assert instance.plain_y is False
    # Optional[Literal[False]] forced to False (caller asserted a non-None value).
    assert instance.maybe_false is False
    # Aliased Literal[False] forced to False.
    assert instance.aliased_false is False


def test_model_validate_leaves_optional_literal_false_default_none() -> None:
    instance = FixtureAuthority.model_validate({})
    assert instance.maybe_false is None


def test_model_validate_optional_literal_false_explicit_none_stays_none() -> None:
    instance = FixtureAuthority.model_validate({"maybe_false": None})
    assert instance.maybe_false is None


# ---------------------------------------------------------------------------
# model_construct: routes through validate (NOT pydantic's bypass)
# ---------------------------------------------------------------------------


def test_model_construct_forces_literal_false_via_validate() -> None:
    instance = FixtureAuthority.model_construct(a=True, b=True, plain_x=True)
    assert instance.a is False
    assert instance.b is False
    assert instance.c is False  # default
    assert instance.plain_x is True
    assert instance.plain_y is True  # default


def test_model_construct_uses_alias_form_too() -> None:
    instance = FixtureAuthority.model_construct(someField=True)
    assert instance.aliased_false is False


# ---------------------------------------------------------------------------
# model_dump: serializer forces False (by-alias and by-name)
# ---------------------------------------------------------------------------


def test_model_dump_by_alias_serializes_literal_false_as_false() -> None:
    # Even if a future pydantic bug let a True value through __init__, the
    # serializer overrides it on the way out. We can't easily fabricate that
    # condition here, so we just assert the steady-state contract.
    instance = FixtureAuthority(plain_x=True, plain_y=False)
    dumped = instance.model_dump(by_alias=True)
    assert dumped["a"] is False
    assert dumped["b"] is False
    assert dumped["c"] is False
    assert dumped["maybe_false"] is None
    assert dumped["someField"] is False  # alias key, not "aliased_false"
    assert "aliased_false" not in dumped
    assert dumped["plain_x"] is True
    assert dumped["plain_y"] is False


def test_model_dump_by_name_serializes_literal_false_as_false() -> None:
    instance = FixtureAuthority(plain_x=True)
    dumped = instance.model_dump(by_alias=False)
    assert dumped["a"] is False
    assert dumped["aliased_false"] is False
    assert "someField" not in dumped


# ---------------------------------------------------------------------------
# model_copy / copy: alias-aware force-false on update
# ---------------------------------------------------------------------------


def test_model_copy_update_still_forces_literal_false() -> None:
    instance = FixtureAuthority()
    copied = instance.model_copy(update={"a": True, "plain_x": True})
    assert copied.a is False
    assert copied.plain_x is True


def test_model_copy_update_by_alias_still_forces_literal_false() -> None:
    instance = FixtureAuthority()
    copied = instance.model_copy(update={"someField": True})
    assert copied.aliased_false is False


def test_legacy_copy_method_routes_through_model_copy() -> None:
    instance = FixtureAuthority()
    copied = instance.copy(update={"a": True, "b": True})
    assert copied.a is False
    assert copied.b is False


# ---------------------------------------------------------------------------
# public_projection
# ---------------------------------------------------------------------------


def test_public_projection_is_by_alias_dump() -> None:
    instance = FixtureAuthority(plain_x=True)
    projection = instance.public_projection()
    assert projection["someField"] is False
    assert projection["a"] is False
    assert projection["plain_x"] is True


# ---------------------------------------------------------------------------
# Config: extra="forbid", frozen, populate_by_name
# ---------------------------------------------------------------------------


def test_extra_forbid_raises_on_unknown_key() -> None:
    with pytest.raises(ValidationError):
        FixtureAuthority.model_validate({"unknown_field": "x"})


def test_frozen_raises_on_attribute_set() -> None:
    instance = FixtureAuthority()
    with pytest.raises(ValidationError):
        instance.a = True  # type: ignore[misc]


def test_populate_by_name_accepts_both_keys_for_aliased_field() -> None:
    # Validator force-falses the aliased field whichever key the caller used.
    by_alias = FixtureAuthority.model_validate({"someField": True})
    by_name = FixtureAuthority.model_validate({"aliased_false": True})
    assert by_alias.aliased_false is False
    assert by_name.aliased_false is False


# ---------------------------------------------------------------------------
# Subclass with NO Literal[False] field works (empty introspection result)
# ---------------------------------------------------------------------------


def test_subclass_with_no_literal_false_works_normally() -> None:
    instance = FixtureEmpty.model_validate({"value": 7, "name": "ok"})
    assert instance.value == 7
    assert instance.name == "ok"
    # Empty list of forced fields -- serializer is a no-op.
    assert instance.model_dump() == {"value": 7, "name": "ok"}


# ---------------------------------------------------------------------------
# Pydantic ``BaseModel`` subclasses outside the new base are unchanged
# (sanity: the base is opt-in and does not pollute BaseModel globally).
# ---------------------------------------------------------------------------


def test_plain_base_model_unaffected_by_force_false() -> None:
    class Plain(BaseModel):
        flag: Literal[False] = False
        value: int = 0

    # Plain BaseModel still permits asserting True on a Literal[False] field
    # only via model_construct (pydantic's documented escape hatch). The point
    # here is that the C-4 base does not monkey-patch BaseModel globally.
    instance = Plain.model_construct(flag=True, value=1)
    assert instance.flag is True  # documented pydantic bypass
    assert instance.value == 1
