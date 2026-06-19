"""Frozen-contract / authority model bases (C-4 / C-5 shared home).

A dependency-free leaf (stdlib + pydantic only). No imports from other
``magi_agent`` subpackages, so every consumer can safely import it.

``FrozenContractModel`` is the single frozen-contract base: the canonical
``ConfigDict`` trio (``frozen``, ``populate_by_name``, ``extra="forbid"``,
``validate_default``, ``hide_input_in_errors``) plus the escape-hatch disabling
(``model_construct`` rejected, ``model_copy(update=...)`` rejected) that was
hand-re-pasted across ~15 sites (``billing/quota``, ``billing/spend_guard``,
``tenancy/context``, ``ops/job_queue``, ``ops/metrics`` x4, ...).

``FalseOnlyAuthorityModel`` is the single force-false authority base (C-4): a
frozen model whose every ``Literal[False]``-typed field is force-false on
construct/copy/validate, with a generic serializer that round-trips them to
False -- derived from the type annotations, NOT a hand-maintained field tuple.
Pre-C-4 the same shape was hand-re-pasted across ~15 sites (config
``_FalseOnlyModel`` + 8 subclasses, ``connectors/_ConnectorModel`` /
``_LeaseModel`` / ``_MarketplaceModel``, ``tools/kernel._ToolKernelModel``,
``channels/contract``, ``evidence/coding_verification``,
``recipes/coding_subagents``, ``permissions/auto_control._SealedPermissionRecord``)
with each subclass re-listing the field tuple by hand -- a serializer-drift
hazard the introspection-based base closes.

NOTE (C-4 PR-A, this commit): only the base + golden harness land here. NO
existing force-false model has been re-parented in this PR; that migration is
the next batch (C-4 PR-B/PR-C). Public behavior of the runtime is byte-identical
after this PR.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal, Self, get_args, get_origin

from pydantic import BaseModel, ConfigDict, model_serializer, model_validator
from pydantic.fields import FieldInfo

FROZEN_CONTRACT_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    hide_input_in_errors=True,
)


class FrozenContractModel(BaseModel):
    """Immutable, alias-aware, extra-forbidding contract model.

    Disables the pydantic escape hatches that would let a caller bypass
    validation: ``model_construct`` always raises, and ``model_copy`` rejects an
    ``update`` (a frozen contract must be rebuilt through validation, not mutated
    in place). ``model_copy()`` without an update round-trips through
    ``model_validate`` so the copy is re-validated.
    """

    model_config = FROZEN_CONTRACT_CONFIG

    @classmethod
    def model_construct(cls, _fields_set: set[str] | None = None, **values: object) -> Self:
        _ = _fields_set, values
        raise ValueError(f"model_construct is disabled for {cls.__name__}")

    def model_copy(self, *, update: Mapping[str, object] | None = None, deep: bool = False) -> Self:
        if update:
            raise ValueError(f"model_copy update is disabled for {type(self).__name__}")
        _ = deep
        return type(self).model_validate(self.model_dump(by_alias=True, mode="json"))


# ---------------------------------------------------------------------------
# FalseOnlyAuthorityModel (C-4) -- introspection-based force-false base.
# ---------------------------------------------------------------------------

# Sentinel: avoid colliding with a falsy class attribute on subclasses.
_FALSE_ONLY_CACHE_ATTR = "__false_only_fields_cache__"


def _is_literal_false(annotation: object) -> bool:
    """Return True iff ``annotation`` is exactly ``Literal[False]``.

    Used as the inner test for the introspection helper. We compare against
    ``Literal[False]`` by matching ``get_origin`` (``Literal``) and ``get_args``
    (a one-element tuple containing the *literal* ``False``).

    ``bool`` instances must compare by *identity* to ``False`` because ``0``
    would otherwise sneak through (``0 == False`` is True in Python). The
    introspection contract is "the annotation is the literal ``False``", not
    "anything falsy".
    """
    if get_origin(annotation) is not Literal:
        return False
    args = get_args(annotation)
    if len(args) != 1:
        return False
    only = args[0]
    return isinstance(only, bool) and only is False


def _annotation_is_literal_false(annotation: object) -> bool:
    """``Literal[False]`` OR ``Optional[Literal[False]]`` (i.e. ``Literal[False] | None``).

    For the Optional/Union form we accept exactly ``{Literal[False], NoneType}``
    -- a wider union (``Literal[False] | Literal[True]``, etc.) is NOT a
    false-only field. ``Optional[Literal[False]]`` IS treated as false-only so a
    caller asserting True on such a field is forced back to False (the default
    None stays None unless explicitly asserted).
    """
    if _is_literal_false(annotation):
        return True
    origin = get_origin(annotation)
    if origin is None:
        return False
    # Union / X | Y form.
    try:
        # `types.UnionType` (PEP 604) and `typing.Union` both report their
        # members via `get_args`; the origin differs (`types.UnionType` vs
        # `typing.Union`) but the args are the same. We treat any union shape
        # as a candidate and inspect its members.
        args = get_args(annotation)
    except Exception:  # pragma: no cover - defensive
        return False
    if not args:
        return False
    none_type = type(None)
    non_none = [arg for arg in args if arg is not none_type]
    has_none = any(arg is none_type for arg in args)
    if not has_none or len(non_none) != 1:
        return False
    return _is_literal_false(non_none[0])


class FalseOnlyAuthorityModel(BaseModel):
    """Frozen authority model whose every ``Literal[False]``-typed field is
    force-false on construct/copy/validate, with a generic serializer that
    round-trips them to False -- derived from the type annotations, NOT a
    hand-maintained field tuple.

    Construction surfaces (``__init__``, ``model_validate``, ``model_construct``,
    ``model_copy``, ``copy``) all funnel through ``model_validate`` so the same
    invariant applies regardless of how the model is built. The serializer
    overrides any drift on the way back out (e.g. a future pydantic version that
    permits in-place mutation cannot leak a non-False value through
    ``model_dump``).

    Subclasses declare false-only fields directly with ``Literal[False]`` (or
    ``Optional[Literal[False]]``) -- there is NO ``_FALSE_ONLY_FIELDS`` tuple
    and NO per-class ``field_serializer`` list. ``Field(default=False,
    alias=...)`` is supported (aliases are honored in both directions: incoming
    by-alias payloads are forced false; ``model_dump(by_alias=True)`` emits the
    alias key with ``False``).
    """

    model_config = ConfigDict(
        frozen=True,
        populate_by_name=True,
        extra="forbid",
        validate_default=True,
        hide_input_in_errors=True,
    )

    # ---- introspection ----------------------------------------------------
    @classmethod
    def _false_only_fields(cls) -> tuple[str, ...]:
        """Return the alias-aware NAMES of every ``Literal[False]`` field.

        The names are pydantic field names (i.e. python attribute names), not
        the aliases. Alias-awareness is handled separately in the validator /
        serializer (each looks up the alias from ``cls.model_fields``).

        The result is cached per-class via a private dunder attribute so the
        introspection cost is paid once per subclass.
        """
        cached = cls.__dict__.get(_FALSE_ONLY_CACHE_ATTR)
        if cached is not None:
            return cached
        names: list[str] = []
        for name, field in cls.model_fields.items():
            annotation = field.annotation
            if _annotation_is_literal_false(annotation):
                names.append(name)
        result = tuple(names)
        # Store directly on the class (not inherited) so each subclass caches
        # its own result -- using setattr to bypass pydantic's __setattr__ on
        # instances (this is a classmethod path; setattr on a class is allowed).
        setattr(cls, _FALSE_ONLY_CACHE_ATTR, result)
        return result

    # ---- validator (force-false before validation) ------------------------
    @model_validator(mode="before")
    @classmethod
    def _force_false(cls, value: object) -> object:
        if not isinstance(value, Mapping):
            return value
        payload = dict(value)
        for field_name in cls._false_only_fields():
            field_info = cls.model_fields[field_name]
            alias = field_info.alias
            # If either form is present, normalize: write False to BOTH the
            # field name and the alias (if any). populate_by_name=True means
            # pydantic accepts either key.
            present = field_name in payload or (alias is not None and alias in payload)
            if not present:
                # Field not asserted -- leave default to its declared value.
                # For Literal[False] with default=False that is False; for
                # Optional[Literal[False]] with default=None that is None.
                continue
            # The annotation may be Optional[Literal[False]]; in that case a
            # caller asserting None is permitted (and means "absent"). Anything
            # else is forced to False.
            value_in = payload.get(field_name, payload.get(alias) if alias else None)
            forced: object
            if _is_literal_false(field_info.annotation):
                forced = False
            else:
                # Optional[Literal[False]]: keep None, otherwise force False.
                forced = None if value_in is None else False
            payload.pop(field_name, None)
            if alias is not None:
                payload.pop(alias, None)
                payload[alias] = forced
            else:
                payload[field_name] = forced
        return payload

    # ---- model_construct (route through validate) -------------------------
    @classmethod
    def model_construct(
        cls, _fields_set: set[str] | None = None, **values: object
    ) -> Self:
        _ = _fields_set
        return cls.model_validate(values)

    # ---- model_copy / copy (alias-aware) ----------------------------------
    def model_copy(
        self,
        *,
        update: Mapping[str, object] | None = None,
        deep: bool = False,
    ) -> Self:
        _ = deep
        cls = type(self)
        payload = self.model_dump(by_alias=True, mode="python", warnings=False)
        if update:
            payload.update(dict(update))
        # The validator will force-false any false-only key the caller tried to
        # override via ``update``. No extra work required here.
        return cls.model_validate(payload)

    def copy(  # type: ignore[override]
        self,
        *,
        include: Any = None,
        exclude: Any = None,
        update: Mapping[str, Any] | None = None,
        deep: bool = False,
    ) -> Self:
        _ = include, exclude
        return self.model_copy(update=update, deep=deep)

    # ---- serializer (force-false on the way out) --------------------------
    @model_serializer(mode="wrap")
    def _ser(self, handler: Any) -> Any:
        data = handler(self)
        if not isinstance(data, dict):
            return data
        cls = type(self)
        for field_name in cls._false_only_fields():
            field_info: FieldInfo = cls.model_fields[field_name]
            alias = field_info.alias
            # Determine the by-alias key that pydantic chose for this dump.
            # We force-false BOTH the field name AND the alias if present in
            # the dict (covers by_alias=True and by_alias=False).
            for key in (field_name, alias):
                if key is None:
                    continue
                if key in data:
                    if _is_literal_false(field_info.annotation):
                        data[key] = False
                    else:
                        # Optional[Literal[False]]: keep None as-is, force the
                        # only non-None value to False.
                        if data[key] is not None:
                            data[key] = False
        return data

    # ---- public projection -----------------------------------------------
    def public_projection(self) -> dict[str, object]:
        """Public-safe by-alias projection.

        The single replacement for every hand-written ``public_projection`` that
        re-listed the false-only field tuple a third time alongside a
        ``field_serializer``. Derives directly from ``model_dump(by_alias=True)``
        -- the serializer above already guarantees the false-only fields are
        ``False`` in that output, so this is just an unambiguous public name for
        the by-alias dump.
        """
        return self.model_dump(by_alias=True, mode="json", warnings=False)


__all__ = [
    "FROZEN_CONTRACT_CONFIG",
    "FalseOnlyAuthorityModel",
    "FrozenContractModel",
]
