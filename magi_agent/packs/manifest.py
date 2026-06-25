"""Static pack manifest schema (D2/D3).

A pack is a directory containing ``pack.toml``. The manifest declares its
``provides`` entries STATICALLY so the catalog can be built without importing
any impl. Each entry is one of 8 typed variants:

    tool · callback · validator · harness · control_plane ·
    evidence_producer · recipe · connector

Code primitives carry ``impl = "module:symbol"``; declarative recipes carry
``spec = "<relpath>"``. Ordered types (callback, control_plane) carry
``priority`` + ``phase``; control_plane additionally carries ``gate_position``
(default ``"after"`` the permission gate).

Mirrors the frozen/camelCase conventions of ``packs/types.py``
(the catalog contract re-homed from the deleted authoring plane).
"""
from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

_MODEL_CONFIG = ConfigDict(frozen=True, populate_by_name=True, extra="forbid")

ProvidesType = Literal[
    "tool",
    "callback",
    "validator",
    "harness",
    "control_plane",
    "evidence_producer",
    "recipe",
    "connector",
    # Declarative scope-label type: a namespaced agent role (D2 extension).
    "role",
    # Pack C policy types (decomposed-subsystem policies; same loader, no privilege)
    "loop_policy",
    "schedule_policy",
    "memory_strategy",
]

# Types whose impl is a declarative spec file (relpath) rather than code.
_SPEC_TYPES: frozenset[str] = frozenset({"recipe", "role"})
# Types that participate in an ordered fan-out (carry priority + phase).
_ORDERED_TYPES: frozenset[str] = frozenset({"callback", "control_plane"})
# Only control_plane may pin a gate_position.
_GATE_POSITION_TYPES: frozenset[str] = frozenset({"control_plane"})

GatePosition = Literal["before", "after"]


class _PackModel(BaseModel):
    model_config = _MODEL_CONFIG


class ProvidesEntry(_PackModel):
    type: ProvidesType
    ref: str
    impl: str | None = None
    spec: str | None = None
    # Code-computed recipe-as-code (PR4): a spec-type entry may instead carry a
    # callable ref ``"module.path:symbol"`` whose callable returns a manifest. The
    # SHAPE is validated here always; ACTIVATION is gated at load time by the
    # default-OFF ``MAGI_RECIPE_AS_CODE_ENABLED`` flag (see loader.load_packs), so
    # an OFF run never imports or calls the callable (byte-identical discovery).
    spec_callable: str | None = Field(default=None, alias="specCallable")
    priority: int | None = None
    phase: str | None = None
    gate_position: GatePosition | None = Field(default=None, alias="gatePosition")

    @model_validator(mode="after")
    def _validate(self) -> "ProvidesEntry":
        if not self.ref.strip():
            raise ValueError("provides.ref must be a non-empty string")

        is_spec_type = self.type in _SPEC_TYPES
        if is_spec_type:
            # Exactly ONE of {spec, spec_callable} declarative source, never impl.
            declared = (self.spec is not None) + (self.spec_callable is not None)
            if declared != 1 or self.impl is not None:
                raise ValueError(
                    f"provides type {self.type!r} must declare exactly one of "
                    "'spec' or 'spec_callable' and not 'impl'"
                )
            if self.spec_callable is not None:
                if (
                    ":" not in self.spec_callable
                    or self.spec_callable.startswith(":")
                    or self.spec_callable.endswith(":")
                ):
                    raise ValueError(
                        "spec_callable must be of the form 'module.path:symbol'"
                    )
        else:
            if self.spec_callable is not None:
                raise ValueError(
                    f"provides type {self.type!r} must not declare 'spec_callable'"
                )
            if self.impl is None or self.spec is not None:
                raise ValueError(
                    f"provides type {self.type!r} must declare 'impl' and not 'spec'"
                )
            if ":" not in self.impl or self.impl.startswith(":") or self.impl.endswith(":"):
                raise ValueError("impl must be of the form 'module.path:symbol'")

        if self.type not in _ORDERED_TYPES and (
            self.priority is not None or self.phase is not None
        ):
            raise ValueError(
                f"priority/phase only allowed on ordered types {_ORDERED_TYPES}"
            )

        if self.type not in _GATE_POSITION_TYPES and self.gate_position is not None:
            raise ValueError("gatePosition only allowed on control_plane entries")

        # control_plane defaults gate_position to 'after' when unset.
        if self.type == "control_plane" and self.gate_position is None:
            object.__setattr__(self, "gate_position", "after")

        return self


class PackManifest(_PackModel):
    pack_id: str = Field(alias="packId")
    version: str = "1"
    display_name: str = Field(alias="displayName")
    description: str = ""
    default_enabled: bool = Field(default=True, alias="defaultEnabled")
    provides: tuple[ProvidesEntry, ...] = ()

    @model_validator(mode="after")
    def _validate(self) -> "PackManifest":
        if not self.pack_id.strip():
            raise ValueError("packId must be a non-empty string")
        seen: set[str] = set()
        for entry in self.provides:
            if entry.ref in seen:
                raise ValueError(f"duplicate provides ref within pack: {entry.ref}")
            seen.add(entry.ref)
        return self


def load_manifest_from_toml(path: "Path") -> PackManifest:
    """Parse a ``pack.toml`` into a ``PackManifest`` STATICALLY.

    This never imports any impl referenced by a ``provides`` entry — it only
    reads the declarative manifest so the catalog can be built before any pack
    code executes (D3). Raises ``ValueError`` on malformed TOML; lets pydantic
    ``ValidationError`` propagate on schema violations.
    """
    try:
        with open(path, "rb") as handle:
            raw = tomllib.load(handle)
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(f"malformed pack.toml at {path}: {exc}") from exc
    return PackManifest.model_validate(raw)
