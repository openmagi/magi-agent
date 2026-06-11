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

Mirrors the frozen/camelCase conventions of ``authoring/compiler.py``.
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
]

# Types whose impl is a declarative spec file (relpath) rather than code.
_SPEC_TYPES: frozenset[str] = frozenset({"recipe"})
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
    priority: int | None = None
    phase: str | None = None
    gate_position: GatePosition | None = Field(default=None, alias="gatePosition")

    @model_validator(mode="after")
    def _validate(self) -> "ProvidesEntry":
        if not self.ref.strip():
            raise ValueError("provides.ref must be a non-empty string")

        is_spec_type = self.type in _SPEC_TYPES
        if is_spec_type:
            if self.spec is None or self.impl is not None:
                raise ValueError(
                    f"provides type {self.type!r} must declare 'spec' and not 'impl'"
                )
        else:
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
