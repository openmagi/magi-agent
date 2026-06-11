# Phase 1 — Pack Manifest, Discovery, Loader, Catalog Build

> **For agentic workers:** REQUIRED SUB-SKILL: `superpowers:subagent-driven-development` or
> `superpowers:executing-plans`. Read `00-BLUEPRINT.md` first.

**Goal:** Build the greenfield neutral-kernel package `magi_agent/packs/` — the disk-pack
mechanism first-party and third-party share with **no privilege** (§1). This phase delivers the
four pipeline stages: (1) `manifest.py` parses `pack.toml` **statically** into `PackManifest` +
`ProvidesEntry` (8 typed variants) via `tomllib` with **zero impl import**; (2) `discovery.py`
resolves the search path (bundled `magi_agent/firstparty/packs` + `~/.magi/packs` + `<cwd>/.magi/packs`),
discovers `pack.toml` by rglob, and applies `config.toml [packs]` enable/disable/order/override;
(3) `loader.py` orchestrates discovery → static catalog build → lazy impl import (`module:symbol`) →
registry registration; (4) `catalog_build.py` unions all loaded manifests' `provides` refs into a
`CompileRecipePackCatalog`-compatible flat catalog (D4, no first-party tier).

**Architecture:** Pure data + filesystem, no ADK, no model. The manifest layer is the contract
(D2/D3); discovery is the search-path resolver (D1); the loader is the orchestrator (D6 microkernel
seam); catalog_build is the D4 flat-catalog adapter feeding `authoring/compiler.py`'s
`CompileRecipePackCatalog` on the live path. This phase OWNS a **minimal registration interface**
(`RegistrationSink` protocol + `LoadedPrimitive` record) so the loader compiles even though the full
typed registries (`registries.py`) land in Phase 2. Phase 2 re-implements `registries.py` to satisfy
this same protocol — nothing in Phase 1 is thrown away.

**Tech stack:** Python ≥3.11 (`requires-python = ">=3.11"` in `pyproject.toml` — `tomllib` is
stdlib from 3.11; do not use 3.12-only syntax), `uv`, pydantic v2 (frozen, `extra="forbid"`,
`populate_by_name=True`, camelCase aliases — mirrors `authoring/compiler.py` `_MODEL_CONFIG`),
`tomllib`, pytest. **No API keys, no model, no ADK** in any Phase-1 test.

**Dependency note (registries):** the blueprint file map lists `magi_agent/packs/registries.py` as a
Phase-2 deliverable. Phase 1 cannot register into a registry that does not exist yet, so Phase 1
**defines its own minimal `RegistrationSink` protocol and `LoadedPrimitive` record inside
`loader.py`** (the loader→registry seam this phase owns). Phase 2's `registries.py` will provide a
concrete sink satisfying this protocol. This keeps the loader testable now with an in-memory fake
sink and zero coupling to unbuilt code.

**Conventions (from §6 of the blueprint, mandatory):**
- TDD bite-sized: failing test → run (FAIL) → minimal impl → run (PASS) → commit. One logical change
  per commit; conventional-commit messages.
- All pytest invocations prefix `MAGI_CONFIG="$(mktemp -d)/config.toml"` to isolate from
  `~/.magi/config.toml` contamination (known test-env gotcha). No provider keys needed this phase.
- Re-grep before editing any existing file; `:NNN` line refs are HEAD-802e707b snapshots and drift.
- Pydantic models: `frozen=True, extra="forbid", populate_by_name=True`, camelCase aliases.
- No control-plane LoopControls are touched in Phase 1 → the Phase-0 golden regression is **not**
  required here. (It becomes mandatory in Phase 5.)

---

## Grounding facts (verified against this worktree; re-grep at point of use)

- `magi_agent/authoring/compiler.py` — `CompileRecipePackCatalog` (a frozen pydantic `_CompilerModel`,
  `model_config = ConfigDict(frozen=True, populate_by_name=True, extra="forbid")`). Its ref fields
  (camelCase aliases) are exactly: `connectorRefs`, `toolRefs`, `pluginRefs`, `validatorRefs`,
  `harnessRefs`, `requiredEvidenceRefs`, `evidenceProducerRefs`, `approvalAuthorityRefs`,
  `hardInvariantRefs`, `requiredHardInvariantRefs`. `.default()` is a classmethod returning a fully
  populated instance. **There is no `callbackRefs` and no `controlPlaneRefs` field** — see "catalog
  mapping decision" in Task 1.7. `_validate_refs` rejects empty/whitespace strings;
  `_validate_required_hard_invariants` requires `requiredHardInvariantRefs ⊆ hardInvariantRefs`.
- `magi_agent/recipes/compiler.py` — `RecipePackManifest` (frozen, camelCase aliases:
  `packId`, `displayName`, `defaultEnabled`, `toolRefs`, `callbackRefs`, `validatorRefs`,
  `approvalGateRefs`, `evidenceRefs`, …). `PackRegistry` (`register` raises on duplicate `pack_id`,
  `get`/`values`/`pack_ids`, `with_first_party_packs()` classmethod). `_first_party_packs()` returns
  a `tuple[RecipePackManifest, ...]`. `build_recipe_snapshot_id(pack_ids: tuple[str, ...]) -> str`
  produces a stable digest id. We mirror these naming concepts (`pack_id`, ref-tuple union, snapshot
  id) but our `PackManifest` is the **new disk-pack** schema (8-typed `provides`), not the recipe
  manifest.
- `magi_agent/plugins/native/skills.py` — disk discovery pattern to mirror: iterate base dirs,
  `if base.is_dir()`, `sorted(base.rglob("SKILL.md"))`. We rglob `pack.toml` the same way.
- `magi_agent/cli/providers.py:198-216` — config.toml loading: `_config_path()` honors
  `os.environ["MAGI_CONFIG"]` (`.expanduser()`) else `Path.home()/".magi"/"config.toml"`;
  `_load_config_file()` `tomllib.load`s it and returns `{}` on missing/malformed. We reuse this exact
  resolution shape for the `[packs]` section.
- `pyproject.toml` — `requires-python = ">=3.11"`; pydantic `2.13.4`; tests run via `uv run pytest`.

---

## Task 1.1: `PackManifest` + `ProvidesEntry` — the 8 typed variants (manifest schema)

**Files:**
- Create: `magi_agent/packs/__init__.py`
- Create: `magi_agent/packs/manifest.py`
- Create: `tests/packs/__init__.py`
- Test: `tests/packs/test_manifest_models.py`

- [ ] **Step 1: Write the failing test for the model schema**

```python
# tests/packs/test_manifest_models.py
import pytest
from pydantic import ValidationError

from magi_agent.packs.manifest import PackManifest, ProvidesEntry


def test_provides_entry_tool_code_impl():
    entry = ProvidesEntry.model_validate(
        {"type": "tool", "ref": "FileWrite", "impl": "pkg.mod:FileWriteTool"}
    )
    assert entry.type == "tool"
    assert entry.ref == "FileWrite"
    assert entry.impl == "pkg.mod:FileWriteTool"
    assert entry.spec is None
    # ordering metadata defaults only meaningful for ordered types; None here
    assert entry.priority is None
    assert entry.phase is None
    assert entry.gate_position is None


def test_provides_entry_recipe_uses_spec_not_impl():
    entry = ProvidesEntry.model_validate(
        {"type": "recipe", "ref": "recipe.research@1", "spec": "recipes/research.toml"}
    )
    assert entry.spec == "recipes/research.toml"
    assert entry.impl is None


def test_provides_entry_rejects_both_impl_and_spec():
    with pytest.raises(ValidationError):
        ProvidesEntry.model_validate(
            {"type": "tool", "ref": "X", "impl": "a:b", "spec": "c.toml"}
        )


def test_provides_entry_rejects_neither_impl_nor_spec():
    with pytest.raises(ValidationError):
        ProvidesEntry.model_validate({"type": "tool", "ref": "X"})


def test_recipe_must_use_spec_code_types_must_use_impl():
    # recipe with impl is invalid
    with pytest.raises(ValidationError):
        ProvidesEntry.model_validate({"type": "recipe", "ref": "r", "impl": "a:b"})
    # tool with spec is invalid
    with pytest.raises(ValidationError):
        ProvidesEntry.model_validate({"type": "tool", "ref": "t", "spec": "r.toml"})


def test_provides_entry_rejects_unknown_type():
    with pytest.raises(ValidationError):
        ProvidesEntry.model_validate({"type": "wizard", "ref": "X", "impl": "a:b"})


def test_impl_must_be_module_colon_symbol():
    with pytest.raises(ValidationError):
        ProvidesEntry.model_validate({"type": "tool", "ref": "X", "impl": "no_colon"})


def test_callback_carries_priority_and_phase_via_camelcase_alias():
    entry = ProvidesEntry.model_validate(
        {"type": "callback", "ref": "cb.audit@1", "impl": "a:b",
         "priority": 10, "phase": "before_model"}
    )
    assert entry.priority == 10
    assert entry.phase == "before_model"


def test_control_plane_gate_position_defaults_to_after():
    entry = ProvidesEntry.model_validate(
        {"type": "control_plane", "ref": "cp.maxsteps@1", "impl": "a:b", "priority": 5}
    )
    assert entry.gate_position == "after"


def test_control_plane_gate_position_explicit_before():
    entry = ProvidesEntry.model_validate(
        {"type": "control_plane", "ref": "cp.gate@1", "impl": "a:b",
         "priority": 5, "gatePosition": "before"}
    )
    assert entry.gate_position == "before"


def test_gate_position_only_allowed_on_control_plane():
    with pytest.raises(ValidationError):
        ProvidesEntry.model_validate(
            {"type": "tool", "ref": "t", "impl": "a:b", "gatePosition": "before"}
        )


def test_priority_phase_only_on_ordered_types():
    # validator is unordered -> priority forbidden
    with pytest.raises(ValidationError):
        ProvidesEntry.model_validate(
            {"type": "validator", "ref": "v", "impl": "a:b", "priority": 3}
        )


def test_models_are_frozen_and_forbid_extra():
    entry = ProvidesEntry.model_validate({"type": "tool", "ref": "X", "impl": "a:b"})
    with pytest.raises(ValidationError):
        entry.ref = "Y"  # frozen
    with pytest.raises(ValidationError):
        ProvidesEntry.model_validate(
            {"type": "tool", "ref": "X", "impl": "a:b", "junk": 1}
        )


def test_pack_manifest_parses_provides_list():
    manifest = PackManifest.model_validate(
        {
            "packId": "firstparty.tools",
            "version": "1",
            "displayName": "First-party tools",
            "provides": [
                {"type": "tool", "ref": "FileWrite", "impl": "m:FileWrite"},
                {"type": "validator", "ref": "validator:x@1", "impl": "m:VX"},
            ],
        }
    )
    assert manifest.pack_id == "firstparty.tools"
    assert manifest.version == "1"
    assert len(manifest.provides) == 2
    assert manifest.provides[0].type == "tool"


def test_pack_manifest_rejects_duplicate_refs_within_pack():
    with pytest.raises(ValidationError):
        PackManifest.model_validate(
            {
                "packId": "p",
                "displayName": "p",
                "provides": [
                    {"type": "tool", "ref": "Dup", "impl": "m:A"},
                    {"type": "tool", "ref": "Dup", "impl": "m:B"},
                ],
            }
        )
```

- [ ] **Step 2: Run it, see it fail**

```bash
MAGI_CONFIG="$(mktemp -d)/config.toml" uv run pytest tests/packs/test_manifest_models.py -q
```
Expected: FAIL (`ModuleNotFoundError: magi_agent.packs.manifest`).

- [ ] **Step 3: Create the package init files (minimal impl)**

```python
# magi_agent/packs/__init__.py
"""Neutral OSS pack kernel: manifest, discovery, loader, catalog build."""
```

```python
# tests/packs/__init__.py
```

- [ ] **Step 4: Implement `manifest.py` (minimal impl)**

```python
# magi_agent/packs/manifest.py
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
```

Note on the `control_plane` default: because the model is frozen, the
`gate_position` default is applied via `object.__setattr__` inside the `after`
validator (pydantic permits this during validation). The test
`test_control_plane_gate_position_defaults_to_after` proves it.

- [ ] **Step 5: Run it, see it pass**

```bash
MAGI_CONFIG="$(mktemp -d)/config.toml" uv run pytest tests/packs/test_manifest_models.py -q
```
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add magi_agent/packs/__init__.py magi_agent/packs/manifest.py \
        tests/packs/__init__.py tests/packs/test_manifest_models.py
git commit -m "feat(packs): add PackManifest + ProvidesEntry static manifest schema"
```

---

## Task 1.2: Parse `pack.toml` statically (no impl import)

**Files:**
- Modify: `magi_agent/packs/manifest.py` (add `load_manifest_from_toml`)
- Test: `tests/packs/test_manifest_parse.py`

- [ ] **Step 1: Write the failing test** (it writes a real `pack.toml` to a tmp dir and parses it)

```python
# tests/packs/test_manifest_parse.py
import textwrap

import pytest
from pydantic import ValidationError

from magi_agent.packs.manifest import PackManifest, load_manifest_from_toml


def _write(tmp_path, body: str):
    p = tmp_path / "pack.toml"
    p.write_text(textwrap.dedent(body))
    return p


def test_load_manifest_from_toml_parses_provides(tmp_path):
    path = _write(
        tmp_path,
        """
        packId = "firstparty.tools"
        displayName = "First-party tools"
        description = "bundled tools"

        [[provides]]
        type = "tool"
        ref = "FileWrite"
        impl = "magi_agent.firstparty.packs.tools.impls:file_write"

        [[provides]]
        type = "control_plane"
        ref = "cp.maxsteps@1"
        impl = "magi_agent.firstparty.packs.controls.impls:MaxStepsBrake"
        priority = 5
        """,
    )
    manifest = load_manifest_from_toml(path)
    assert isinstance(manifest, PackManifest)
    assert manifest.pack_id == "firstparty.tools"
    assert manifest.provides[0].ref == "FileWrite"
    # gate_position default applied
    assert manifest.provides[1].gate_position == "after"


def test_load_manifest_does_not_import_impls(tmp_path, monkeypatch):
    # An impl pointing at a non-importable module must STILL parse: parsing is static.
    path = _write(
        tmp_path,
        """
        packId = "p"
        displayName = "p"

        [[provides]]
        type = "tool"
        ref = "X"
        impl = "this.module.does.not.exist:Symbol"
        """,
    )
    import builtins

    real_import = builtins.__import__

    def _boom(name, *a, **k):  # pragma: no cover - asserts it's never hit for pack impls
        if name.startswith("this.module"):
            raise AssertionError("loader imported impl during static parse")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", _boom)
    manifest = load_manifest_from_toml(path)
    assert manifest.provides[0].impl == "this.module.does.not.exist:Symbol"


def test_load_manifest_malformed_toml_raises(tmp_path):
    path = tmp_path / "pack.toml"
    path.write_text("this is = = not toml")
    with pytest.raises(ValueError):
        load_manifest_from_toml(path)


def test_load_manifest_schema_violation_raises(tmp_path):
    path = _write(
        tmp_path,
        """
        packId = "p"
        displayName = "p"

        [[provides]]
        type = "tool"
        ref = "X"
        """,  # missing impl
    )
    with pytest.raises(ValidationError):
        load_manifest_from_toml(path)
```

- [ ] **Step 2: Run, see it fail** (`ImportError: cannot import name 'load_manifest_from_toml'`)

```bash
MAGI_CONFIG="$(mktemp -d)/config.toml" uv run pytest tests/packs/test_manifest_parse.py -q
```

- [ ] **Step 3: Implement `load_manifest_from_toml` in `manifest.py`**

Add to the imports at the top of `magi_agent/packs/manifest.py`:

```python
import tomllib
from pathlib import Path
```

Append at the end of `magi_agent/packs/manifest.py`:

```python
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
```

- [ ] **Step 4: Run, see it pass**

```bash
MAGI_CONFIG="$(mktemp -d)/config.toml" uv run pytest tests/packs/test_manifest_parse.py -q
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add magi_agent/packs/manifest.py tests/packs/test_manifest_parse.py
git commit -m "feat(packs): static pack.toml parse via tomllib (no impl import)"
```

---

## Task 1.3: Discovery — search-path resolution + `pack.toml` rglob

**Files:**
- Create: `magi_agent/packs/discovery.py`
- Test: `tests/packs/test_discovery_searchpath.py`

**Design:** mirror `plugins/native/skills.py` (rglob a sentinel filename across base dirs).
Search path order (D1): bundled first-party dir → `~/.magi/packs` → `<cwd>/.magi/packs`. Each base
is rglob'd for `pack.toml`. The bundled dir resolves to `magi_agent/firstparty/packs/` (created in
Phase 6; may not exist yet — discovery must tolerate a missing base, exactly like skills.py's
`if not base.is_dir(): continue`).

- [ ] **Step 1: Write the failing test**

```python
# tests/packs/test_discovery_searchpath.py
from pathlib import Path

from magi_agent.packs.discovery import (
    DiscoveredPack,
    default_search_bases,
    discover_pack_files,
)


def test_default_search_bases_order(tmp_path, monkeypatch):
    home = tmp_path / "home"
    cwd = tmp_path / "cwd"
    home.mkdir()
    cwd.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(cwd)
    bases = default_search_bases()
    # bundled firstparty dir is first, then ~/.magi/packs, then <cwd>/.magi/packs
    assert bases[-2] == home / ".magi" / "packs"
    assert bases[-1] == cwd / ".magi" / "packs"
    assert bases[0].name == "packs"  # bundled magi_agent/firstparty/packs


def test_discover_skips_missing_bases(tmp_path):
    missing = tmp_path / "nope"
    found = discover_pack_files([missing])
    assert found == []


def test_discover_finds_pack_toml_rglob(tmp_path):
    base = tmp_path / "packs"
    (base / "alpha").mkdir(parents=True)
    (base / "alpha" / "pack.toml").write_text(
        'packId="a"\ndisplayName="a"\n'
    )
    (base / "nested" / "beta").mkdir(parents=True)
    (base / "nested" / "beta" / "pack.toml").write_text(
        'packId="b"\ndisplayName="b"\n'
    )
    found = discover_pack_files([base])
    refs = sorted(d.manifest.pack_id for d in found)
    assert refs == ["a", "b"]
    assert all(isinstance(d, DiscoveredPack) for d in found)
    assert all(d.path.name == "pack.toml" for d in found)
    # pack_dir is the directory containing pack.toml (relpath base for spec files)
    assert all(d.pack_dir == d.path.parent for d in found)


def test_discover_is_deterministic_sorted(tmp_path):
    base = tmp_path / "packs"
    for name in ("c", "a", "b"):
        (base / name).mkdir(parents=True)
        (base / name / "pack.toml").write_text(f'packId="{name}"\ndisplayName="{name}"\n')
    found = discover_pack_files([base])
    assert [d.manifest.pack_id for d in found] == ["a", "b", "c"]
```

- [ ] **Step 2: Run, see it fail** (`ModuleNotFoundError: magi_agent.packs.discovery`)

```bash
MAGI_CONFIG="$(mktemp -d)/config.toml" uv run pytest tests/packs/test_discovery_searchpath.py -q
```

- [ ] **Step 3: Implement `discovery.py`**

```python
# magi_agent/packs/discovery.py
"""Pack discovery (D1): resolve search-path bases and rglob ``pack.toml``.

Search path (in priority order):
  1. bundled first-party packs: ``magi_agent/firstparty/packs/``
  2. user home packs:           ``~/.magi/packs/``
  3. project packs:             ``<cwd>/.magi/packs/``

Mirrors the disk-discovery pattern in ``magi_agent/plugins/native/skills.py``
(rglob a sentinel filename across base dirs; tolerate missing bases).
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from magi_agent.packs.manifest import PackManifest, load_manifest_from_toml

_PACK_FILENAME = "pack.toml"


@dataclass(frozen=True)
class DiscoveredPack:
    """A parsed manifest plus where it came from (for relpath resolution)."""

    path: Path          # the pack.toml file
    pack_dir: Path      # directory containing pack.toml (base for spec relpaths)
    manifest: PackManifest


def _bundled_firstparty_base() -> Path:
    # magi_agent/packs/discovery.py -> magi_agent/ -> firstparty/packs
    return Path(__file__).resolve().parent.parent / "firstparty" / "packs"


def default_search_bases() -> list[Path]:
    """Return the ordered search-path bases (bundled first, then user, then cwd)."""
    return [
        _bundled_firstparty_base(),
        Path.home() / ".magi" / "packs",
        Path.cwd() / ".magi" / "packs",
    ]


def discover_pack_files(bases: list[Path]) -> list[DiscoveredPack]:
    """rglob each base for ``pack.toml`` and parse it. Missing bases are skipped.

    Results are sorted by ``pack_id`` for deterministic ordering. Duplicate
    ``pack_id`` across bases is NOT resolved here (that is config-aware override
    territory — handled in Task 1.4).
    """
    discovered: list[DiscoveredPack] = []
    for base in bases:
        if not base.is_dir():
            continue
        for pack_file in sorted(base.rglob(_PACK_FILENAME)):
            manifest = load_manifest_from_toml(pack_file)
            discovered.append(
                DiscoveredPack(
                    path=pack_file,
                    pack_dir=pack_file.parent,
                    manifest=manifest,
                )
            )
    discovered.sort(key=lambda d: d.manifest.pack_id)
    return discovered
```

- [ ] **Step 4: Run, see it pass**

```bash
MAGI_CONFIG="$(mktemp -d)/config.toml" uv run pytest tests/packs/test_discovery_searchpath.py -q
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add magi_agent/packs/discovery.py tests/packs/test_discovery_searchpath.py
git commit -m "feat(packs): pack discovery via search-path rglob (skills.py pattern)"
```

---

## Task 1.4: `config.toml [packs]` — enable/disable/order/override

**Files:**
- Modify: `magi_agent/packs/discovery.py` (add config loading + `resolve_enabled_packs`)
- Test: `tests/packs/test_discovery_config.py`

**Grounding (re-grep before editing):**
```bash
cd /Users/kevin/Desktop/claude_code/magi-agent/.worktrees/neutral-runtime
grep -n "_config_path\|_load_config_file\|MAGI_CONFIG" magi_agent/cli/providers.py
```
Current `providers.py:198-216` resolution shape we mirror (do NOT import providers.py — it pulls in
model machinery; re-implement the same tiny resolver locally so `packs/` has no model dependency):
```python
def _config_path() -> Path:
    override = os.environ.get("MAGI_CONFIG")
    if override and override.strip():
        return Path(override).expanduser()
    return Path.home() / ".magi" / "config.toml"
```

**`[packs]` schema (this phase defines it):**
```toml
[packs]
disable = ["firstparty.permission-gate"]   # refs/pack_ids to drop entirely
order = ["firstparty.tools", "user.custom"] # pack_ids whose order is pinned first
# Override: a later pack_id wins on a colliding provides ref. Default policy is
# "last-wins by search order"; an explicit override list makes the winner explicit.
override = ["user.custom"]                   # pack_ids allowed to override earlier refs
```

- [ ] **Step 1: Write the failing test**

```python
# tests/packs/test_discovery_config.py
from pathlib import Path

from magi_agent.packs.discovery import (
    DiscoveredPack,
    PacksConfig,
    load_packs_config,
    resolve_enabled_packs,
)
from magi_agent.packs.manifest import PackManifest


def _disc(pack_id: str, enabled: bool = True) -> DiscoveredPack:
    return DiscoveredPack(
        path=Path(f"/tmp/{pack_id}/pack.toml"),
        pack_dir=Path(f"/tmp/{pack_id}"),
        manifest=PackManifest.model_validate(
            {"packId": pack_id, "displayName": pack_id, "defaultEnabled": enabled}
        ),
    )


def test_load_packs_config_missing_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("MAGI_CONFIG", str(tmp_path / "config.toml"))
    cfg = load_packs_config()
    assert cfg == PacksConfig()


def test_load_packs_config_reads_section(tmp_path, monkeypatch):
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(
        '[packs]\n'
        'disable = ["p.bad"]\n'
        'order = ["p.first"]\n'
        'override = ["p.user"]\n'
    )
    monkeypatch.setenv("MAGI_CONFIG", str(cfg_path))
    cfg = load_packs_config()
    assert cfg.disable == ("p.bad",)
    assert cfg.order == ("p.first",)
    assert cfg.override == ("p.user",)


def test_load_packs_config_malformed_returns_empty(tmp_path, monkeypatch):
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text("= = not toml")
    monkeypatch.setenv("MAGI_CONFIG", str(cfg_path))
    assert load_packs_config() == PacksConfig()


def test_resolve_disables_drop_packs():
    discovered = [_disc("p.keep"), _disc("p.bad")]
    cfg = PacksConfig(disable=("p.bad",))
    result = resolve_enabled_packs(discovered, cfg)
    assert [d.manifest.pack_id for d in result] == ["p.keep"]


def test_resolve_default_disabled_pack_dropped():
    discovered = [_disc("p.on", enabled=True), _disc("p.off", enabled=False)]
    result = resolve_enabled_packs(discovered, PacksConfig())
    assert [d.manifest.pack_id for d in result] == ["p.on"]


def test_resolve_order_pins_listed_first_then_rest_sorted():
    discovered = [_disc("a"), _disc("b"), _disc("z.pinned")]
    cfg = PacksConfig(order=("z.pinned",))
    result = resolve_enabled_packs(discovered, cfg)
    assert [d.manifest.pack_id for d in result] == ["z.pinned", "a", "b"]
```

- [ ] **Step 2: Run, see it fail** (`ImportError: cannot import name 'PacksConfig'`)

```bash
MAGI_CONFIG="$(mktemp -d)/config.toml" uv run pytest tests/packs/test_discovery_config.py -q
```

- [ ] **Step 3: Implement the config layer in `discovery.py`**

Add to the imports at the top of `magi_agent/packs/discovery.py`:

```python
import os
import tomllib
```

Append to `magi_agent/packs/discovery.py`:

```python
from pydantic import BaseModel, ConfigDict, Field

_PACKS_CONFIG_MODEL = ConfigDict(frozen=True, populate_by_name=True, extra="ignore")


class PacksConfig(BaseModel):
    """The ``[packs]`` section of ``config.toml`` (D1 override controls).

    ``extra="ignore"`` (not ``forbid``) so unrelated future keys do not crash
    discovery. Lists are coerced to tuples for frozen-ness.
    """

    model_config = _PACKS_CONFIG_MODEL

    disable: tuple[str, ...] = ()
    order: tuple[str, ...] = ()
    override: tuple[str, ...] = ()


def _config_path() -> Path:
    override = os.environ.get("MAGI_CONFIG")
    if override and override.strip():
        return Path(override).expanduser()
    return Path.home() / ".magi" / "config.toml"


def load_packs_config() -> PacksConfig:
    """Load ``[packs]`` from config.toml. Missing/malformed -> empty config.

    Mirrors ``magi_agent/cli/providers.py``'s tolerant loader: a bad config must
    not crash discovery.
    """
    path = _config_path()
    try:
        with open(path, "rb") as handle:
            raw = tomllib.load(handle)
    except (FileNotFoundError, NotADirectoryError, IsADirectoryError):
        return PacksConfig()
    except (OSError, tomllib.TOMLDecodeError):
        return PacksConfig()
    section = raw.get("packs") if isinstance(raw, dict) else None
    if not isinstance(section, dict):
        return PacksConfig()
    try:
        return PacksConfig.model_validate(section)
    except Exception:
        return PacksConfig()


def resolve_enabled_packs(
    discovered: list[DiscoveredPack], config: PacksConfig
) -> list[DiscoveredPack]:
    """Apply enable/disable/order to a discovered set.

    1. drop packs whose ``pack_id`` is in ``config.disable``;
    2. drop packs whose manifest ``default_enabled`` is False (unless re-enabled
       by appearing in ``config.order`` — an explicit order entry is an opt-in);
    3. order: pins in ``config.order`` first (in listed order), then the rest in
       ``pack_id`` sort order.

    Override-by-ref collision is resolved downstream in ``catalog_build`` /
    loader (last pack in this returned order wins on a colliding provides ref);
    ``config.override`` is carried for that stage, not consumed here.
    """
    disabled = set(config.disable)
    ordered_ids = list(config.order)
    order_set = set(ordered_ids)

    by_id = {d.manifest.pack_id: d for d in discovered}
    kept: dict[str, DiscoveredPack] = {}
    for pack_id, disc in by_id.items():
        if pack_id in disabled:
            continue
        if not disc.manifest.default_enabled and pack_id not in order_set:
            continue
        kept[pack_id] = disc

    pinned = [kept[p] for p in ordered_ids if p in kept]
    rest = sorted(
        (d for pid, d in kept.items() if pid not in order_set),
        key=lambda d: d.manifest.pack_id,
    )
    return pinned + rest
```

- [ ] **Step 4: Run, see it pass**

```bash
MAGI_CONFIG="$(mktemp -d)/config.toml" uv run pytest tests/packs/test_discovery_config.py -q
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add magi_agent/packs/discovery.py tests/packs/test_discovery_config.py
git commit -m "feat(packs): config.toml [packs] enable/disable/order resolution"
```

---

## Task 1.5: Loader — minimal registration interface (this phase OWNS it)

**Files:**
- Create: `magi_agent/packs/loader.py`
- Test: `tests/packs/test_loader_registration.py`

**Design:** the loader owns a `RegistrationSink` protocol + `LoadedPrimitive` record. Phase 2's
`registries.py` will provide a concrete sink satisfying `RegistrationSink`. The loader: discovery →
(catalog build, Task 1.6) → lazy-import each entry's `impl` (`module:symbol`) → call
`sink.register(LoadedPrimitive(...))`. `recipe` entries (spec, not impl) are registered with their
resolved absolute spec path and **no impl import**. Override policy: last pack in resolved order wins
on a colliding `(type, ref)` — recorded so Phase 2 can honor it.

- [ ] **Step 1: Write the failing test** (uses an in-memory fake sink + a real importable symbol)

```python
# tests/packs/test_loader_registration.py
from pathlib import Path

import pytest

from magi_agent.packs.discovery import DiscoveredPack
from magi_agent.packs.loader import (
    LoadedPrimitive,
    RecordingSink,
    lazy_import_symbol,
    load_packs,
)
from magi_agent.packs.manifest import PackManifest


# A real, importable target for the lazy-import test.
def _sentinel_impl():  # noqa: D401 - test fixture symbol
    return "ok"


def test_lazy_import_symbol_resolves_module_colon_symbol():
    sym = lazy_import_symbol(f"{__name__}:_sentinel_impl")
    assert sym is _sentinel_impl
    assert sym() == "ok"


def test_lazy_import_symbol_bad_form_raises():
    with pytest.raises(ValueError):
        lazy_import_symbol("no_colon")


def test_lazy_import_symbol_missing_module_raises():
    with pytest.raises(ImportError):
        lazy_import_symbol("definitely.not.a.module:thing")


def _disc(pack_id: str, provides: list[dict]) -> DiscoveredPack:
    return DiscoveredPack(
        path=Path(f"/tmp/{pack_id}/pack.toml"),
        pack_dir=Path(f"/tmp/{pack_id}"),
        manifest=PackManifest.model_validate(
            {"packId": pack_id, "displayName": pack_id, "provides": provides}
        ),
    )


def test_load_packs_registers_code_primitive_with_impl():
    disc = _disc(
        "p.tools",
        [{"type": "tool", "ref": "Sentinel", "impl": f"{__name__}:_sentinel_impl"}],
    )
    sink = RecordingSink()
    load_packs([disc], sink)
    assert len(sink.registered) == 1
    prim = sink.registered[0]
    assert isinstance(prim, LoadedPrimitive)
    assert prim.type == "tool"
    assert prim.ref == "Sentinel"
    assert prim.impl is _sentinel_impl     # lazily imported
    assert prim.spec_path is None
    assert prim.pack_id == "p.tools"


def test_load_packs_recipe_registers_resolved_spec_path_no_import(tmp_path):
    disc = DiscoveredPack(
        path=tmp_path / "p.rec" / "pack.toml",
        pack_dir=tmp_path / "p.rec",
        manifest=PackManifest.model_validate(
            {
                "packId": "p.rec",
                "displayName": "p.rec",
                "provides": [
                    {"type": "recipe", "ref": "r@1", "spec": "recipes/r.toml"}
                ],
            }
        ),
    )
    sink = RecordingSink()
    load_packs([disc], sink)
    prim = sink.registered[0]
    assert prim.impl is None
    assert prim.spec_path == (tmp_path / "p.rec" / "recipes" / "r.toml")


def test_load_packs_last_pack_wins_on_colliding_ref():
    a = _disc("p.a", [{"type": "tool", "ref": "Dup", "impl": f"{__name__}:_sentinel_impl"}])
    b = _disc("p.b", [{"type": "tool", "ref": "Dup", "impl": f"{__name__}:_sentinel_impl"}])
    sink = RecordingSink()
    result = load_packs([a, b], sink)
    # both registrations are sent to the sink in order; the override map records winner
    assert result.overridden == {("tool", "Dup"): ("p.a", "p.b")}
    assert sink.registered[-1].pack_id == "p.b"
```

- [ ] **Step 2: Run, see it fail** (`ModuleNotFoundError: magi_agent.packs.loader`)

```bash
MAGI_CONFIG="$(mktemp -d)/config.toml" uv run pytest tests/packs/test_loader_registration.py -q
```

- [ ] **Step 3: Implement `loader.py`**

```python
# magi_agent/packs/loader.py
"""Pack loader (D3/D6): discovery -> lazy impl import -> registry registration.

This phase OWNS the loader->registry seam:
  * ``LoadedPrimitive`` — one resolved provides entry (code symbol or spec path).
  * ``RegistrationSink`` — the minimal protocol a registry must satisfy.
  * ``RecordingSink`` — an in-memory sink used by tests (and a fallback).

Phase 2's ``magi_agent/packs/registries.py`` supplies a concrete sink that also
satisfies ``RegistrationSink``; nothing here is thrown away by Phase 2.

Impls are imported LAZILY here (at registration time) — never during manifest
parse (D3). ``recipe`` entries carry a ``spec`` relpath resolved against the
pack dir and are registered WITHOUT importing anything.
"""
from __future__ import annotations

import importlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from magi_agent.packs.discovery import DiscoveredPack
from magi_agent.packs.manifest import ProvidesType


def lazy_import_symbol(impl: str) -> Any:
    """Resolve a ``"module.path:symbol"`` string to the live object.

    Imports the module (lazily, at call time) and returns the attribute. Raises
    ``ValueError`` for a malformed ref and ``ImportError`` if the module or
    symbol cannot be resolved.
    """
    if impl.count(":") != 1:
        raise ValueError(f"impl must be 'module.path:symbol', got {impl!r}")
    module_path, _, symbol = impl.partition(":")
    if not module_path or not symbol:
        raise ValueError(f"impl must be 'module.path:symbol', got {impl!r}")
    module = importlib.import_module(module_path)
    try:
        return getattr(module, symbol)
    except AttributeError as exc:
        raise ImportError(f"symbol {symbol!r} not found in module {module_path!r}") from exc


@dataclass(frozen=True)
class LoadedPrimitive:
    """A resolved provides entry ready for registration.

    Exactly one of ``impl`` (code object) or ``spec_path`` (declarative recipe
    file) is set. Ordered-type metadata (priority/phase) and control_plane
    ``gate_position`` are carried through for Phase-2 ordering.
    """

    type: ProvidesType
    ref: str
    pack_id: str
    impl: Any | None = None
    spec_path: Path | None = None
    priority: int | None = None
    phase: str | None = None
    gate_position: str | None = None


@runtime_checkable
class RegistrationSink(Protocol):
    """Minimal interface the loader registers into.

    Phase 2's typed registries satisfy this protocol. Keeping it tiny is the
    whole point: the loader has no knowledge of how a registry stores/dispatches.
    """

    def register(self, primitive: LoadedPrimitive) -> None: ...


@dataclass
class RecordingSink:
    """In-memory ``RegistrationSink`` for tests and as a no-op fallback."""

    registered: list[LoadedPrimitive] = field(default_factory=list)

    def register(self, primitive: LoadedPrimitive) -> None:
        self.registered.append(primitive)


@dataclass(frozen=True)
class LoadResult:
    """Outcome of a load pass.

    ``overridden`` maps a colliding ``(type, ref)`` to the ``(loser, winner)``
    pack ids (last pack in resolved order wins). Phase 2 consumes this to honor
    ``config.toml [packs].override``.
    """

    primitives: tuple[LoadedPrimitive, ...]
    overridden: dict[tuple[str, str], tuple[str, str]]


def load_packs(
    discovered: list[DiscoveredPack], sink: RegistrationSink
) -> LoadResult:
    """Resolve every provides entry and register it into ``sink``.

    Code entries are lazily imported; recipe entries resolve their spec path.
    On a colliding ``(type, ref)`` the later pack wins (and the collision is
    recorded). All registrations are still forwarded to the sink in order so a
    Phase-2 registry can apply its own last-wins replacement.
    """
    primitives: list[LoadedPrimitive] = []
    overridden: dict[tuple[str, str], tuple[str, str]] = {}
    winners: dict[tuple[str, str], str] = {}

    for disc in discovered:
        pack_id = disc.manifest.pack_id
        for entry in disc.manifest.provides:
            key = (entry.type, entry.ref)
            if key in winners and winners[key] != pack_id:
                overridden[key] = (winners[key], pack_id)
            winners[key] = pack_id

            if entry.spec is not None:
                primitive = LoadedPrimitive(
                    type=entry.type,
                    ref=entry.ref,
                    pack_id=pack_id,
                    spec_path=(disc.pack_dir / entry.spec).resolve(),
                    priority=entry.priority,
                    phase=entry.phase,
                    gate_position=entry.gate_position,
                )
            else:
                assert entry.impl is not None  # manifest validator guarantees this
                primitive = LoadedPrimitive(
                    type=entry.type,
                    ref=entry.ref,
                    pack_id=pack_id,
                    impl=lazy_import_symbol(entry.impl),
                    priority=entry.priority,
                    phase=entry.phase,
                    gate_position=entry.gate_position,
                )
            primitives.append(primitive)
            sink.register(primitive)

    return LoadResult(primitives=tuple(primitives), overridden=overridden)
```

Note on `spec_path.resolve()` in `test_load_packs_recipe_registers_resolved_spec_path_no_import`:
the test asserts equality with `(tmp_path / "p.rec" / "recipes" / "r.toml")`. Because `tmp_path`
is already absolute and contains no symlinks under pytest's `basetemp`, `resolve()` yields that
exact path. If your platform's `tmp_path` resolves a symlink (e.g. macOS `/var` → `/private/var`),
the test asserts equality against `(tmp_path / ...).resolve()` — update the expected value in the
test to `.resolve()` form to match. Show the macOS-safe variant:

```python
    assert prim.spec_path == (tmp_path / "p.rec" / "recipes" / "r.toml").resolve()
```

Use the `.resolve()` form in the committed test (it is correct on both Linux and macOS).

- [ ] **Step 4: Run, see it pass**

```bash
MAGI_CONFIG="$(mktemp -d)/config.toml" uv run pytest tests/packs/test_loader_registration.py -q
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add magi_agent/packs/loader.py tests/packs/test_loader_registration.py
git commit -m "feat(packs): loader with lazy impl import + RegistrationSink seam"
```

---

## Task 1.6: `catalog_build.py` — manifests → `CompileRecipePackCatalog` (D4 flat)

**Files:**
- Create: `magi_agent/packs/catalog_build.py`
- Test: `tests/packs/test_catalog_build.py`

**Grounding (re-grep before writing — confirm field names/aliases have not drifted):**
```bash
cd /Users/kevin/Desktop/claude_code/magi-agent/.worktrees/neutral-runtime
grep -n "class CompileRecipePackCatalog\|Refs\|def default\|requiredHardInvariant" magi_agent/authoring/compiler.py
```

**Catalog mapping decision (the load-bearing D4 detail).** `CompileRecipePackCatalog` has
fields: `connectorRefs, toolRefs, pluginRefs, validatorRefs, harnessRefs, requiredEvidenceRefs,
evidenceProducerRefs, approvalAuthorityRefs, hardInvariantRefs, requiredHardInvariantRefs`. Our 8
provides types map onto it as:

| provides type | catalog field |
|---|---|
| `tool` | `toolRefs` |
| `connector` | `connectorRefs` |
| `validator` | `validatorRefs` |
| `harness` | `harnessRefs` |
| `evidence_producer` | `evidenceProducerRefs` |
| `control_plane` | `pluginRefs` (control-plane plugins are the existing "plugin" tier) |
| `callback` | `pluginRefs` (callbacks are plugin-tier too; D2 folds before_model into callback) |
| `recipe` | *not a catalog ref* — recipes are spec-files, registered via the loader, not catalog refs |

`requiredEvidenceRefs`, `approvalAuthorityRefs`, `hardInvariantRefs`, `requiredHardInvariantRefs`
have **no Phase-1 provides source**. The catalog's `_validate_required_hard_invariants` requires
`requiredHardInvariantRefs ⊆ hardInvariantRefs`. **Blueprint divergence found:** §1 says "no
first-party-only ref tier" and D4 says "no first-party tier", but the existing catalog hard-codes
`requiredHardInvariantRefs=("invariant.no-live-execution","invariant.no-activation")` as a non-empty
default (a hosted floor — blueprint §0/D2 explicitly note `hard_invariant` is the "hosted floor" and
"omitted" from the neutral provides schema). For OSS local full-trust, Phase 1 builds the catalog
with these two invariant tiers **empty** (passing `hardInvariantRefs=()` and
`requiredHardInvariantRefs=()` explicitly), which the validator accepts (empty ⊆ empty). This keeps
the catalog flat and first-party-tier-free per D4; the hosted floor is layered separately and is out
of scope (blueprint §1, "hosted stays opinionated separately").

- [ ] **Step 1: Write the failing test**

```python
# tests/packs/test_catalog_build.py
from magi_agent.authoring.compiler import CompileRecipePackCatalog
from magi_agent.packs.catalog_build import build_catalog
from magi_agent.packs.loader import LoadedPrimitive


def _prim(type_: str, ref: str, pack_id: str = "p") -> LoadedPrimitive:
    return LoadedPrimitive(type=type_, ref=ref, pack_id=pack_id, impl=object())


def test_build_catalog_returns_compile_recipe_pack_catalog():
    catalog = build_catalog([_prim("tool", "FileWrite")])
    assert isinstance(catalog, CompileRecipePackCatalog)
    assert catalog.tool_refs == ("FileWrite",)


def test_build_catalog_maps_each_type_to_its_field():
    prims = [
        _prim("tool", "T"),
        _prim("connector", "C"),
        _prim("validator", "V"),
        _prim("harness", "H"),
        _prim("evidence_producer", "E"),
        _prim("control_plane", "CP"),
        _prim("callback", "CB"),
    ]
    catalog = build_catalog(prims)
    assert catalog.tool_refs == ("T",)
    assert catalog.connector_refs == ("C",)
    assert catalog.validator_refs == ("V",)
    assert catalog.harness_refs == ("H",)
    assert catalog.evidence_producer_refs == ("E",)
    # control_plane + callback both land in pluginRefs, order-preserved
    assert catalog.plugin_refs == ("CP", "CB")


def test_build_catalog_has_empty_hard_invariant_tiers_for_oss_local():
    catalog = build_catalog([_prim("tool", "T")])
    assert catalog.hard_invariant_refs == ()
    assert catalog.required_hard_invariant_refs == ()


def test_build_catalog_recipe_entries_are_not_catalog_refs():
    catalog = build_catalog(
        [
            LoadedPrimitive(type="recipe", ref="r@1", pack_id="p", spec_path=None),
            _prim("tool", "T"),
        ]
    )
    assert catalog.tool_refs == ("T",)
    # recipe ref does not appear in any *_refs tuple
    dumped = catalog.model_dump()
    assert not any("r@1" in tuple(v) for v in dumped.values() if isinstance(v, (list, tuple)))


def test_build_catalog_last_wins_dedup_on_colliding_ref():
    # two tools, same ref -> single entry (catalog refs are a set-union, last wins position)
    catalog = build_catalog([_prim("tool", "Dup", "p.a"), _prim("tool", "Dup", "p.b")])
    assert catalog.tool_refs == ("Dup",)


def test_build_catalog_empty_is_valid():
    catalog = build_catalog([])
    assert isinstance(catalog, CompileRecipePackCatalog)
    assert catalog.tool_refs == ()
```

- [ ] **Step 2: Run, see it fail** (`ModuleNotFoundError: magi_agent.packs.catalog_build`)

```bash
MAGI_CONFIG="$(mktemp -d)/config.toml" uv run pytest tests/packs/test_catalog_build.py -q
```

- [ ] **Step 3: Implement `catalog_build.py`**

```python
# magi_agent/packs/catalog_build.py
"""Build the live ``CompileRecipePackCatalog`` from loaded pack primitives (D4).

The catalog is the union of all loaded packs' provides refs, FLAT — there is no
first-party-only tier (§1 "no privilege"). It is the live-path replacement for
``CompileRecipePackCatalog.default()`` (the hardcode the blueprint removes in
later phases).

Mapping (see 02-phase1 doc, "catalog mapping decision"):
    tool              -> toolRefs
    connector         -> connectorRefs
    validator         -> validatorRefs
    harness           -> harnessRefs
    evidence_producer -> evidenceProducerRefs
    control_plane     -> pluginRefs
    callback          -> pluginRefs
    recipe            -> (not a catalog ref; registered as a spec via the loader)

For OSS local full-trust the hard-invariant tiers are empty (the hosted floor is
layered separately and is out of scope).
"""
from __future__ import annotations

from collections.abc import Iterable

from magi_agent.authoring.compiler import CompileRecipePackCatalog
from magi_agent.packs.loader import LoadedPrimitive

# provides type -> the catalog field it contributes to. Order in this dict is the
# pluginRefs emission order (control_plane before callback) when both share a field.
_FIELD_FOR_TYPE: dict[str, str] = {
    "tool": "tool_refs",
    "connector": "connector_refs",
    "validator": "validator_refs",
    "harness": "harness_refs",
    "evidence_producer": "evidence_producer_refs",
    "control_plane": "plugin_refs",
    "callback": "plugin_refs",
}


def _dedup_preserve_order(refs: Iterable[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    out: list[str] = []
    for ref in refs:
        if ref not in seen:
            seen.add(ref)
            out.append(ref)
    return tuple(out)


def build_catalog(primitives: Iterable[LoadedPrimitive]) -> CompileRecipePackCatalog:
    """Union loaded primitives' refs into a flat ``CompileRecipePackCatalog``."""
    buckets: dict[str, list[str]] = {field: [] for field in set(_FIELD_FOR_TYPE.values())}
    for primitive in primitives:
        field = _FIELD_FOR_TYPE.get(primitive.type)
        if field is None:  # recipe (spec) and any non-catalog type
            continue
        buckets[field].append(primitive.ref)

    return CompileRecipePackCatalog(
        toolRefs=_dedup_preserve_order(buckets["tool_refs"]),
        connectorRefs=_dedup_preserve_order(buckets["connector_refs"]),
        validatorRefs=_dedup_preserve_order(buckets["validator_refs"]),
        harnessRefs=_dedup_preserve_order(buckets["harness_refs"]),
        evidenceProducerRefs=_dedup_preserve_order(buckets["evidence_producer_refs"]),
        pluginRefs=_dedup_preserve_order(buckets["plugin_refs"]),
        # OSS local full-trust: no hosted hard-invariant floor.
        hardInvariantRefs=(),
        requiredHardInvariantRefs=(),
    )
```

Note: `CompileRecipePackCatalog`'s `approvalAuthorityRefs` defaults to
`("authority:owner-human@1",)` and we do not pass it, so it keeps that default — that is fine
(it is not a provides ref source in Phase 1 and the validator places no constraint on it).

- [ ] **Step 4: Run, see it pass**

```bash
MAGI_CONFIG="$(mktemp -d)/config.toml" uv run pytest tests/packs/test_catalog_build.py -q
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add magi_agent/packs/catalog_build.py tests/packs/test_catalog_build.py
git commit -m "feat(packs): build flat CompileRecipePackCatalog from loaded packs (D4)"
```

---

## Task 1.7: End-to-end pipeline wiring + on-disk fixture pack

**Files:**
- Create (fixture): `tests/packs/fixtures/example_pack/pack.toml`
- Create (fixture): `tests/packs/fixtures/example_pack/impls.py`
- Create (fixture): `tests/packs/fixtures/example_pack/recipes/demo.toml`
- Modify: `magi_agent/packs/loader.py` (add `load_from_bases` orchestrator)
- Test: `tests/packs/test_pipeline_e2e.py`

**Goal:** one entrypoint `load_from_bases(bases) -> (LoadResult, CompileRecipePackCatalog)` that runs
the full D1→D3→D4 pipeline: discover → resolve config → load+register → build catalog. Prove it
against a real on-disk fixture pack.

- [ ] **Step 1: Create the fixture pack on disk**

```toml
# tests/packs/fixtures/example_pack/pack.toml
packId = "example.demo"
displayName = "Example demo pack"
description = "fixture exercising the full pipeline"

[[provides]]
type = "tool"
ref = "DemoTool"
impl = "tests.packs.fixtures.example_pack.impls:demo_tool"

[[provides]]
type = "validator"
ref = "validator:demo@1"
impl = "tests.packs.fixtures.example_pack.impls:DemoValidator"

[[provides]]
type = "control_plane"
ref = "cp.demo@1"
impl = "tests.packs.fixtures.example_pack.impls:DemoControl"
priority = 7

[[provides]]
type = "recipe"
ref = "recipe.demo@1"
spec = "recipes/demo.toml"
```

```python
# tests/packs/fixtures/example_pack/impls.py
"""Importable impls for the example fixture pack (no model, no ADK)."""


def demo_tool():
    return "demo_tool"


class DemoValidator:
    name = "validator:demo@1"


class DemoControl:
    name = "cp.demo@1"
```

```toml
# tests/packs/fixtures/example_pack/recipes/demo.toml
recipeId = "recipe.demo@1"
title = "demo recipe spec"
```

(The recipe spec body is never parsed in Phase 1 — the loader only resolves its path.)

- [ ] **Step 2: Write the failing pipeline test**

```python
# tests/packs/test_pipeline_e2e.py
from pathlib import Path

from magi_agent.authoring.compiler import CompileRecipePackCatalog
from magi_agent.packs.loader import RecordingSink, load_from_bases

_FIXTURE_BASE = Path(__file__).parent / "fixtures"


def test_pipeline_discovers_loads_and_builds_catalog():
    sink = RecordingSink()
    result, catalog = load_from_bases([_FIXTURE_BASE], sink)

    refs = {(p.type, p.ref) for p in result.primitives}
    assert ("tool", "DemoTool") in refs
    assert ("validator", "validator:demo@1") in refs
    assert ("control_plane", "cp.demo@1") in refs
    assert ("recipe", "recipe.demo@1") in refs

    # code impls were lazily imported; recipe carries a resolved spec path.
    by_ref = {p.ref: p for p in result.primitives}
    assert callable(by_ref["DemoTool"].impl)
    assert by_ref["recipe.demo@1"].impl is None
    assert by_ref["recipe.demo@1"].spec_path.name == "demo.toml"
    assert by_ref["recipe.demo@1"].spec_path.exists()
    assert by_ref["cp.demo@1"].priority == 7
    assert by_ref["cp.demo@1"].gate_position == "after"

    # flat catalog reflects the non-recipe refs.
    assert isinstance(catalog, CompileRecipePackCatalog)
    assert catalog.tool_refs == ("DemoTool",)
    assert catalog.validator_refs == ("validator:demo@1",)
    assert catalog.plugin_refs == ("cp.demo@1",)
    # recipe ref not in any catalog tuple
    assert "recipe.demo@1" not in catalog.tool_refs + catalog.plugin_refs


def test_pipeline_missing_base_yields_empty():
    sink = RecordingSink()
    result, catalog = load_from_bases([Path("/nonexistent/base")], sink)
    assert result.primitives == ()
    assert catalog.tool_refs == ()
    assert sink.registered == []
```

- [ ] **Step 3: Run, see it fail** (`ImportError: cannot import name 'load_from_bases'`)

```bash
MAGI_CONFIG="$(mktemp -d)/config.toml" uv run pytest tests/packs/test_pipeline_e2e.py -q
```

- [ ] **Step 4: Implement `load_from_bases` in `loader.py`**

Add to the imports at the top of `magi_agent/packs/loader.py`:

```python
from magi_agent.packs.catalog_build import build_catalog
from magi_agent.packs.discovery import (
    discover_pack_files,
    load_packs_config,
    resolve_enabled_packs,
)
from magi_agent.authoring.compiler import CompileRecipePackCatalog
```

(Place the `discovery`/`catalog_build` imports at module bottom-of-import-block; `discovery`
already imports `manifest`, and `catalog_build` imports `loader` — to avoid a circular import,
import `build_catalog` **inside** `load_from_bases` rather than at module top. Use the local-import
form shown below.)

Append to `magi_agent/packs/loader.py`:

```python
def load_from_bases(
    bases: list[Path], sink: RegistrationSink
) -> tuple[LoadResult, "CompileRecipePackCatalog"]:
    """Full D1->D3->D4 pipeline: discover -> config -> load+register -> catalog.

    ``build_catalog`` is imported locally to avoid a circular import
    (``catalog_build`` imports ``loader`` for ``LoadedPrimitive``).
    """
    from magi_agent.packs.catalog_build import build_catalog
    from magi_agent.packs.discovery import (
        discover_pack_files,
        load_packs_config,
        resolve_enabled_packs,
    )

    discovered = discover_pack_files(bases)
    config = load_packs_config()
    enabled = resolve_enabled_packs(discovered, config)
    result = load_packs(enabled, sink)
    catalog = build_catalog(result.primitives)
    return result, catalog
```

Remove the two top-of-module import lines you may have added in error (`from
magi_agent.packs.catalog_build import build_catalog` and the `discovery` import) — they MUST stay
local to `load_from_bases` to avoid the circular import. The only top-level loader imports remain:
`importlib`, dataclass/typing, `magi_agent.packs.discovery.DiscoveredPack`, and
`magi_agent.packs.manifest.ProvidesType`.

- [ ] **Step 5: Run, see it pass**

```bash
MAGI_CONFIG="$(mktemp -d)/config.toml" uv run pytest tests/packs/test_pipeline_e2e.py -q
```
Expected: PASS.

- [ ] **Step 6: Run the whole Phase-1 suite to confirm no cross-task breakage**

```bash
MAGI_CONFIG="$(mktemp -d)/config.toml" uv run pytest tests/packs/ -q
```
Expected: all green.

- [ ] **Step 7: Commit**

```bash
git add tests/packs/fixtures/ magi_agent/packs/loader.py tests/packs/test_pipeline_e2e.py
git commit -m "feat(packs): end-to-end discover->load->catalog pipeline + fixture pack"
```

---

## Task 1.8: Regression guard — confirm Phase 1 touched no live runtime path

**Files:** none created — verification only.

This phase is purely additive (`magi_agent/packs/` is greenfield; only the `tests/` tree and new
package were added; **no existing runtime file was modified** — `authoring/compiler.py` is imported,
not edited). No control-plane LoopControl is touched, so the Phase-0 golden oracle is **not**
required. Still confirm nothing regressed in the modules we import from.

- [ ] **Step 1: Confirm the authoring/recipes modules still pass their own tests** (we import
  `CompileRecipePackCatalog` from `authoring/compiler.py`):

```bash
MAGI_CONFIG="$(mktemp -d)/config.toml" uv run pytest \
  tests/authoring -q
```
Expected: green (unchanged — we only import, never edit).

- [ ] **Step 2: Confirm a full collection still imports cleanly** (catches accidental import
  cycles introduced by the new package):

```bash
MAGI_CONFIG="$(mktemp -d)/config.toml" uv run pytest --collect-only -q tests/packs tests/authoring
```
Expected: collects with no import errors.

- [ ] **Step 3 (informational, only if Phase 0 already landed on this branch):** the golden
  regression must still be green because Phase 1 changes no control behavior — run it to prove
  isolation. A diff here would mean an unexpected coupling and MUST be investigated, not regenerated:

```bash
MAGI_CONFIG="$(mktemp -d)/config.toml" uv run pytest \
  tests/fixtures/neutral_runtime_golden/test_golden_regression.py -q || \
  echo "Phase 0 not yet on this branch — skip; otherwise a diff = behavior change to review."
```
Note: if Phase 0 is present and this diffs, that is a **behavior change to review** — regenerate via
`capture --write` only if the change is intended (it should NOT be in Phase 1).

- [ ] **Step 4: Commit** (only if you added a CI marker or note; otherwise skip — nothing to commit).

---

## Acceptance criteria (Phase 1 done)

- [ ] `MAGI_CONFIG="$(mktemp -d)/config.toml" uv run pytest tests/packs/ -q` is green, headless,
  **no API keys**.
- [ ] `magi_agent/packs/manifest.py` defines `PackManifest` + `ProvidesEntry` with the 8 typed
  variants; pydantic frozen, `extra="forbid"`, camelCase aliases; `impl` XOR `spec`; `recipe` uses
  `spec`, all code types use `impl = "module:symbol"`; `callback`/`control_plane` carry
  `priority`+`phase`; `control_plane` carries `gate_position` (default `after`).
- [ ] `pack.toml` is parsed STATICALLY via `tomllib` with **no impl import** (proven by
  `test_load_manifest_does_not_import_impls`).
- [ ] `magi_agent/packs/discovery.py` resolves bundled `firstparty/packs` + `~/.magi/packs` +
  `<cwd>/.magi/packs`, rglobs `pack.toml`, tolerates missing bases, and applies `config.toml [packs]`
  enable/disable/order.
- [ ] `magi_agent/packs/loader.py` lazily imports `module:symbol` impls at registration time, resolves
  recipe `spec` paths without importing, registers into a `RegistrationSink`, and records override
  collisions (last-wins). The loader OWNS `RegistrationSink`/`LoadedPrimitive` (the Phase-2 seam).
- [ ] `magi_agent/packs/catalog_build.py` produces a flat `CompileRecipePackCatalog` (D4) from loaded
  primitives with empty hard-invariant tiers (OSS local) — no first-party-only tier.
- [ ] `load_from_bases` runs the full D1→D3→D4 pipeline against a real on-disk fixture pack.
- [ ] No existing runtime file was modified; no control-plane behavior changed (golden oracle, if
  present, unchanged).

## Rollback

Phase 1 is purely additive: a new package `magi_agent/packs/` (`manifest.py`, `discovery.py`,
`loader.py`, `catalog_build.py`, `__init__.py`) and a new test tree `tests/packs/`. Nothing in the
live runtime imports it yet. Revert = `git revert` the Phase-1 commits (Tasks 1.1–1.7) or delete
`magi_agent/packs/` and `tests/packs/`. No migration, no schema, no config default change to undo.

## Hand-off to later phases

- **Phase 2 (typed-context ABI + registries)** re-implements `magi_agent/packs/registries.py` to
  satisfy the `RegistrationSink` protocol this phase OWNS (in `loader.py`). It consumes
  `LoadedPrimitive` (type/ref/pack_id/impl/spec_path/priority/phase/gate_position) and `LoadResult`
  (incl. `overridden` for `[packs].override`). The loader does not change; only a concrete sink is
  added.
- **Phase 3 (validator vertical slice)** uses `load_from_bases` + the registries to register a real
  validator and reach the live enforce path at `cli/engine.py`.
- **Phase 6 (first-party migration)** creates `magi_agent/firstparty/packs/*/pack.toml` — discovery
  already searches `_bundled_firstparty_base()`; no discovery change needed, only the bundled packs.
  It then flips the live catalog from `CompileRecipePackCatalog.default()` to `build_catalog(...)`.
- **Carried open item for Phase 2/6:** the catalog mapping folds both `control_plane` and `callback`
  into `pluginRefs` (the existing tier). If Phase 5 needs to distinguish control-plane refs from
  callback refs at catalog level, Phase 2 should track type alongside ref in the registries (the
  loader already preserves type on every `LoadedPrimitive`), so no Phase-1 rework is required.
- **Hard-invariant tiers** are intentionally empty for OSS local (D4 flat, §1 no-privilege). The
  hosted floor (`requiredHardInvariantRefs`) is layered separately and out of scope.
