# Phase 3 — Validator Vertical Slice (end-to-end proof)

> **For agentic workers:** REQUIRED SUB-SKILL: `superpowers:subagent-driven-development` or
> `superpowers:executing-plans`. Read `00-BLUEPRINT.md` first. This phase depends on **Phase 1**
> (manifest + discovery + loader) and **Phase 2** (typed-context ABI + registries); it **gates
> Phase 4 and Phase 5**. Re-grep every `:NNN` line ref before editing — they are HEAD-`802e707b`
> snapshots and drift.

**Goal:** Prove the *whole neutral loop* with exactly **one** `provides` type — `validator` —
because live validator enforcement already exists at `cli/engine.py` (re-grep
`required_validators`). End-to-end: a **bundled first-party** validator pack on disk →
discovery (Phase 1) → catalog gets its `validator_ref` injected → registration via a typed
`ValidatorCtx` (Phase 2) → the registered ref reaches the **existing** enforce point where
`assembly.required_validators` is compared against `observed_public_refs` and a missing one yields
the gate's `block` decision. Then a **user pack** in a temp `~/.magi/packs/` proves **add +
override + remove (forbid)** with **no first-party privilege**, and a **fake-model turn** proves a
user validator actually enforces. This is the architecture proof; if it holds, Phase 4/5 are
mechanical replication.

**Architecture:** Disk pack (`pack.toml`) declares `provides = [{ type="validator", ref=…,
impl="module:symbol" }]` statically (D3). Loader (Phase 1) builds the flat catalog (D4) →
`CompileRecipePackCatalog.validator_refs` carries the union of discovered refs. The registry
(Phase 2) lazy-imports each `impl` and registers a `ValidatorImpl` whose only argument is the
typed `ValidatorCtx` (D5). The live enforce path is untouched in shape: the registered ref flows
into `RunnerPolicyAssembly.required_validators` (built in `cli/real_runner.py`) and the existing
`MagiEngineDriver._pre_final_gate_payload` compares it against observed refs — we only **confirm/
route**, we do not rewrite the gate. First-party holds no privilege: the bundled pack uses the
same loader, same catalog field, same `ValidatorCtx` as the user pack.

**Tech stack:** Python 3.12+, `uv`, pydantic v2 (`frozen=True`, `extra="forbid"`,
`populate_by_name=True`), `tomllib` (stdlib), pytest, fake-model
(`LOCAL_DEV_MODEL_SENTINEL="local-dev"`), **no API keys**. Every pytest command is prefixed with
`MAGI_CONFIG="$(mktemp -d)/config.toml"` to avoid `~/.magi/config.toml` contamination.

---

## 0. Ground truth re-verified at HEAD-`802e707b` (re-grep before trusting)

These are the **real** APIs this phase wires against. Confirm each at the point of use.

- **The live enforce point** — `magi_agent/cli/engine.py`, method
  `MagiEngineDriver._pre_final_gate_payload` (≈`:2105`). The keystone comparison (≈`:2155–2165`):
  ```python
  missing_evidence = [
      ref for ref in assembly.evidence_requirements if ref not in observed_public_refs
  ]
  missing_validators = [
      ref for ref in assembly.required_validators if ref not in observed_public_refs
  ]
  decision = (
      "block"
      if (missing_evidence or missing_validators or failed_document_coverage)
      else "pass"
  )
  ```
  **A registered validator's ref must land in `assembly.required_validators`. If a tool emits that
  ref as a public validator ref, `observed_public_refs` contains it → `pass`; otherwise → `block`.**

- **Where `required_validators` is sourced** — `magi_agent/cli/real_runner.py`,
  `_build_default_runner_policy_assembly` (≈`:418–491`):
  ```python
  required_validators = list(plan.final_gate_policy.required_validators)
  if "openmagi.dev-coding" in plan.selected_pack_ids:
      required_validators.append("verifier:dev-coding:test-evidence")
  # (… intervening assembly construction elided — re-grep the full body …)
  return RunnerPolicyAssembly(
      # … other fields …
      requiredValidators=tuple(dict.fromkeys(required_validators)),
      # … other fields …
  )
  ```
  Phase 3 adds **one** confirm/route step here: append validator refs discovered from **loaded
  packs** (via the Phase-1 catalog) to `required_validators`, so a pack-authored validator reaches
  the gate the same way `final_gate_policy.required_validators` does.

- **Gate-applies guard** — `_pre_final_gate_applies` (`cli/engine.py:~532`) returns **`True` for any
  pack set that does NOT contain `openmagi.dev-coding`**. Our validator packs use a **non-dev-coding
  pack id**, so the gate *always applies* — no prompt-classifier noise in the proof.

- **How an observed ref appears** — `execute_pre_final_verifier_bus`
  (`magi_agent/harness/verifier_bus.py:~829`) folds tool-emitted `metadata.validatorRefs` (collected
  by `LocalToolEvidenceCollector`, `magi_agent/evidence/local_tool_collector.py:~42`) into
  `matchedRefs`, which the engine assigns into `observed_public_refs`
  (`cli/engine.py:~2144–2146`). A validator "passes" when a tool result emits its ref.

- **The catalog field** — `CompileRecipePackCatalog.validator_refs`
  (`magi_agent/authoring/compiler.py:52`, alias `validatorRefs`). Phase 1's loader populates the
  live catalog from manifests; Phase 3 reads `catalog.validator_refs` to route refs into the gate.

- **The driver DI seams** — `MagiEngineDriver.__init__` (`cli/engine.py:~821`) accepts
  `runner_policy_assembly=` and `evidence_collector=` directly. The end-to-end fake-model test
  drives the gate through these without booting a server.

- **Phase-1/Phase-2 dependencies (their deliverables, named per the blueprint file map):**
  `magi_agent/packs/manifest.py` → `PackManifest`, `ProvidesEntry`; `…/discovery.py` →
  `discover_packs`; `…/loader.py` → `load_packs`; `…/registries.py` → `PrimitiveRegistries`;
  `…/context.py` → `ValidatorCtx`; `…/catalog_build.py` → `build_catalog_from_manifests`.
  **If a Phase-1/Phase-2 symbol differs at execution time, re-grep `magi_agent/packs/` and adapt
  the import — the contract below is the minimum surface this phase consumes.**

> **Phase-1/Phase-2 contract this phase relies on (verify it exists before Task 3.x; if Phase 1/2
> were authored with different names, map onto them):**
> - `discover_packs(search_paths: list[Path]) -> list[PackManifest]` — reads each `pack.toml`,
>   returns frozen manifests; never imports impls.
> - `PackManifest.pack_id: str`, `PackManifest.provides: tuple[ProvidesEntry, ...]`.
> - `ProvidesEntry.type: Literal["validator", ...]`, `.ref: str`, `.impl: str | None`
>   (`"module:symbol"`), `.spec: str | None`.
> - `build_catalog_from_manifests(manifests) -> CompileRecipePackCatalog` — union of refs into the
>   typed fields (validators → `validator_refs`).
> - `PrimitiveRegistries.from_manifests(manifests) -> PrimitiveRegistries` — lazy-imports each
>   `impl`; `.validators: Mapping[str, ValidatorImpl]` keyed by `ref`.
> - `ValidatorCtx` — the D5 typed context (read-mostly) passed to a validator impl; minimum fields
>   used here: `.observed_public_refs: frozenset[str]`, `.required_ref: str`.
> - `[packs]` resolution: `discover_packs` honors `config.toml [packs]` enable/disable/override/
>   order (D1). For this phase the user pack lives in a temp dir we pass explicitly.

---

## Task 3.1: Bundled first-party validator pack on disk (`pack.toml` + impl)

**Files:**
- Create: `magi_agent/firstparty/packs/source_opened_validator/pack.toml`
- Create: `magi_agent/firstparty/packs/source_opened_validator/__init__.py` (empty, package marker)
- Create: `magi_agent/firstparty/packs/source_opened_validator/impl.py`
- Create: `magi_agent/firstparty/__init__.py` (empty, if missing)
- Create: `magi_agent/firstparty/packs/__init__.py` (empty, if missing)
- Test: `tests/firstparty/test_source_opened_validator_pack.py`

- [ ] **Step 1: Confirm the firstparty package does not exist yet (greenfield) and the manifest
  loader is present.**

Run:
```bash
cd /Users/kevin/Desktop/claude_code/magi-agent/.worktrees/neutral-runtime
ls magi_agent/firstparty 2>/dev/null || echo "NO firstparty dir (expected greenfield)"
python -c "from magi_agent.packs.manifest import PackManifest, ProvidesEntry; print('phase1 ok')"
python -c "from magi_agent.packs.context import ValidatorCtx; print('phase2 ok')"
```
Expected: `NO firstparty dir`, then `phase1 ok` and `phase2 ok`. If either import fails, **STOP** —
Phase 1/Phase 2 are incomplete; this phase cannot proceed.

- [ ] **Step 2: Write the failing test** that loads the bundled pack manifest from disk and asserts
  it declares the validator `provides` entry with a `module:symbol` impl.

```python
# tests/firstparty/test_source_opened_validator_pack.py
from __future__ import annotations

import tomllib
from pathlib import Path

import magi_agent

_PACK = (
    Path(magi_agent.__file__).parent
    / "firstparty"
    / "packs"
    / "source_opened_validator"
    / "pack.toml"
)


def test_first_party_validator_pack_declares_validator_statically() -> None:
    raw = tomllib.loads(_PACK.read_text())
    assert raw["pack"]["id"] == "openmagi.source-opened"
    provides = raw["provides"]
    assert len(provides) == 1
    entry = provides[0]
    assert entry["type"] == "validator"
    assert entry["ref"] == "validator:sourceOpened@1"
    assert entry["impl"] == (
        "magi_agent.firstparty.packs.source_opened_validator.impl:source_opened_validator"
    )


def test_first_party_validator_impl_is_importable_and_typed() -> None:
    from magi_agent.firstparty.packs.source_opened_validator.impl import (
        source_opened_validator,
    )
    from magi_agent.packs.context import ValidatorCtx

    ctx = ValidatorCtx(
        required_ref="validator:sourceOpened@1",
        observed_public_refs=frozenset({"validator:sourceOpened@1"}),
    )
    result = source_opened_validator(ctx)
    assert result.validator_id == "validator:sourceOpened@1"
    assert result.status == "supported"
```

- [ ] **Step 3: Run it, see it fail.**

Run:
```bash
MAGI_CONFIG="$(mktemp -d)/config.toml" uv run pytest \
  tests/firstparty/test_source_opened_validator_pack.py -q
```
Expected: FAIL (`FileNotFoundError` on `pack.toml` / `ModuleNotFoundError` on `impl`).

- [ ] **Step 4: Create the package markers** (empty files):
  `magi_agent/firstparty/__init__.py`, `magi_agent/firstparty/packs/__init__.py`,
  `magi_agent/firstparty/packs/source_opened_validator/__init__.py` — each containing exactly:
```python
```
(empty file).

- [ ] **Step 5: Create the bundled manifest** `pack.toml`:

```toml
# magi_agent/firstparty/packs/source_opened_validator/pack.toml
# Bundled first-party validator pack. Loaded by the SAME loader/format a user
# pack uses (D1/D3). First-party holds NO privilege: this manifest is
# discovered, overridable, and removable exactly like ~/.magi/packs/* manifests.

[pack]
id = "openmagi.source-opened"
version = "1.0.0"
description = "First-party deterministic validator: a source was opened before a quote."

[[provides]]
type = "validator"
ref = "validator:sourceOpened@1"
impl = "magi_agent.firstparty.packs.source_opened_validator.impl:source_opened_validator"
```

- [ ] **Step 6: Create the impl** `impl.py`. It receives **only** the typed `ValidatorCtx` (D5) and
  returns a `ValidatorResult` from the real taxonomy (`magi_agent/evidence/validator_taxonomy.py`):

```python
# magi_agent/firstparty/packs/source_opened_validator/impl.py
from __future__ import annotations

from magi_agent.evidence.validator_taxonomy import ValidatorResult
from magi_agent.packs.context import ValidatorCtx

_REF = "validator:sourceOpened@1"


def source_opened_validator(ctx: ValidatorCtx) -> ValidatorResult:
    """Deterministic first-party validator (no privilege, typed-ctx only).

    Supported iff the runtime observed this validator's public ref this turn
    (i.e. a tool emitted ``validatorRefs=["validator:sourceOpened@1"]``).
    Receives ONLY the narrow ValidatorCtx — identical capability to any
    user-authored validator.
    """
    observed = ctx.required_ref in ctx.observed_public_refs
    return ValidatorResult(
        validatorId=_REF,
        trustClass="deterministic",
        status="supported" if observed else "failed",
        claimRef=_REF,
    )
```

- [ ] **Step 7: Run it, see it pass.**

Run:
```bash
MAGI_CONFIG="$(mktemp -d)/config.toml" uv run pytest \
  tests/firstparty/test_source_opened_validator_pack.py -q
```
Expected: PASS (2 tests).

- [ ] **Step 8: Commit.**

```bash
git add magi_agent/firstparty/ tests/firstparty/test_source_opened_validator_pack.py
git commit -m "feat(firstparty): bundle source-opened validator pack (disk pack.toml + typed impl)"
```

---

## Task 3.2: Loader discovers the bundled pack → catalog gets the ref injected → registry registers it

**Files:**
- Test: `tests/firstparty/test_validator_discovery_to_registry.py`
- Modify (only if the bundled-pack search path is not already wired): `magi_agent/packs/discovery.py`

- [ ] **Step 1: Re-grep the Phase-1 discovery search-path** to confirm the bundled dir
  (`magi_agent/firstparty/packs/`) is included (D1). Read, do not assume:

```bash
cd /Users/kevin/Desktop/claude_code/magi-agent/.worktrees/neutral-runtime
grep -n "firstparty\|search_path\|def discover_packs\|packs\b" magi_agent/packs/discovery.py
```
- If `magi_agent/firstparty/packs` is already a default search root → no edit; go to Step 2.
- If NOT → this is the one allowed modification (see Step 5).

- [ ] **Step 2: Write the failing integration test** — discover → catalog → registry, end-to-end on
  the bundled pack, no user packs:

```python
# tests/firstparty/test_validator_discovery_to_registry.py
from __future__ import annotations

from pathlib import Path

import magi_agent
from magi_agent.packs.catalog_build import build_catalog_from_manifests
from magi_agent.packs.discovery import discover_packs
from magi_agent.packs.registries import PrimitiveRegistries

_FIRST_PARTY_ROOT = Path(magi_agent.__file__).parent / "firstparty" / "packs"


def test_bundled_validator_flows_discovery_to_catalog_to_registry() -> None:
    manifests = discover_packs([_FIRST_PARTY_ROOT])
    pack_ids = {m.pack_id for m in manifests}
    assert "openmagi.source-opened" in pack_ids

    catalog = build_catalog_from_manifests(manifests)
    assert "validator:sourceOpened@1" in catalog.validator_refs

    registries = PrimitiveRegistries.from_manifests(manifests)
    assert "validator:sourceOpened@1" in registries.validators

    # registered impl is callable via the typed ValidatorCtx (Phase 2 ABI)
    from magi_agent.packs.context import ValidatorCtx

    impl = registries.validators["validator:sourceOpened@1"]
    passed = impl(
        ValidatorCtx(
            required_ref="validator:sourceOpened@1",
            observed_public_refs=frozenset({"validator:sourceOpened@1"}),
        )
    )
    failed = impl(
        ValidatorCtx(
            required_ref="validator:sourceOpened@1",
            observed_public_refs=frozenset(),
        )
    )
    assert passed.status == "supported"
    assert failed.status == "failed"
```

- [ ] **Step 3: Run it, see it fail (or pass discovery, fail at catalog/registry).**

Run:
```bash
MAGI_CONFIG="$(mktemp -d)/config.toml" uv run pytest \
  tests/firstparty/test_validator_discovery_to_registry.py -q
```
Expected: FAIL if the bundled dir is not a search root, or PASS-through to the catalog/registry
assertions if Phase 1/2 already enumerate it. If it already passes, the bundled dir is wired —
skip Step 5, commit the test only (Step 6).

- [ ] **Step 4: If discovery already finds the pack, skip to Step 6.** Otherwise proceed to Step 5.

- [ ] **Step 5 (conditional): Wire the bundled dir as a default search root.** First quote the
  ACTUAL current code in `magi_agent/packs/discovery.py` (re-grep — line numbers drift). For
  example, if the current default-roots helper reads:

```python
# CURRENT (quote whatever is actually there after re-grep):
def _default_search_paths() -> list[Path]:
    return [
        Path.home() / ".magi" / "packs",
        Path.cwd() / ".magi" / "packs",
    ]
```
replace it with (bundled first-party dir FIRST so user packs can override by load order, D1):

```python
def _default_search_paths() -> list[Path]:
    import magi_agent  # local import: avoid import cycle at module load

    bundled = Path(magi_agent.__file__).parent / "firstparty" / "packs"
    return [
        bundled,
        Path.home() / ".magi" / "packs",
        Path.cwd() / ".magi" / "packs",
    ]
```
**This is additive** — it does not change user-pack precedence semantics (override/forbid are
resolved by `[packs]` config + load order, Task 3.4). If Phase 1 named this differently, adapt.

- [ ] **Step 6: Run it, see it pass.**

Run:
```bash
MAGI_CONFIG="$(mktemp -d)/config.toml" uv run pytest \
  tests/firstparty/test_validator_discovery_to_registry.py -q
```
Expected: PASS.

- [ ] **Step 7: Commit.**

```bash
git add tests/firstparty/test_validator_discovery_to_registry.py magi_agent/packs/discovery.py
git commit -m "feat(packs): bundled first-party packs dir as default discovery root + e2e validator flow test"
```
(If Step 5 was skipped, drop `magi_agent/packs/discovery.py` from the `git add`.)

---

## Task 3.3: WIRE the live path — pack-discovered validator refs reach the existing gate

**Files:**
- Modify: `magi_agent/cli/real_runner.py` (the `_build_default_runner_policy_assembly` confirm/route)
- Test: `tests/cli/test_pack_validators_reach_gate.py`

**Goal:** Confirm/route so a **registered** validator's ref reaches the existing enforce point.
We do NOT touch `cli/engine.py`'s comparison — we only ensure the ref is present in
`RunnerPolicyAssembly.required_validators` (the left side of the `missing_validators` comprehension).

- [ ] **Step 1: Re-grep the real-runner assembly builder** and quote the exact append site:

```bash
cd /Users/kevin/Desktop/claude_code/magi-agent/.worktrees/neutral-runtime
grep -n "required_validators\|_build_default_runner_policy_assembly\|with_first_party_packs\|requiredValidators" magi_agent/cli/real_runner.py
```
Confirm the current body (≈`:461–463`) reads:
```python
required_validators = list(plan.final_gate_policy.required_validators)
if "openmagi.dev-coding" in plan.selected_pack_ids:
    required_validators.append("verifier:dev-coding:test-evidence")
```

- [ ] **Step 2: Write the failing test** — asserts that when packs are loaded with a validator
  `provides`, the produced assembly's `required_validators` contains that ref. Drive the helper
  directly with an injected catalog so the test needs no recipe compile and no API key:

```python
# tests/cli/test_pack_validators_reach_gate.py
from __future__ import annotations

from magi_agent.cli.real_runner import _merge_pack_validator_refs


def test_pack_validator_refs_are_appended_for_gate() -> None:
    base = ("verifier:dev-coding:test-evidence",)
    pack_validator_refs = ("validator:sourceOpened@1", "validator:userQuote@1")
    merged = _merge_pack_validator_refs(base, pack_validator_refs)
    assert merged[0] == "verifier:dev-coding:test-evidence"
    assert "validator:sourceOpened@1" in merged
    assert "validator:userQuote@1" in merged


def test_pack_validator_refs_dedupe_against_base() -> None:
    base = ("validator:sourceOpened@1",)
    merged = _merge_pack_validator_refs(base, ("validator:sourceOpened@1",))
    assert merged.count("validator:sourceOpened@1") == 1
```

- [ ] **Step 3: Run it, see it fail.**

Run:
```bash
MAGI_CONFIG="$(mktemp -d)/config.toml" uv run pytest \
  tests/cli/test_pack_validators_reach_gate.py -q
```
Expected: FAIL (`ImportError: cannot import name '_merge_pack_validator_refs'`).

- [ ] **Step 4: Add the minimal helper + call site in `magi_agent/cli/real_runner.py`.**

First add the pure helper near `_build_default_runner_policy_assembly` (above it):
```python
def _merge_pack_validator_refs(
    base: tuple[str, ...],
    pack_validator_refs: tuple[str, ...],
) -> tuple[str, ...]:
    """Append pack-discovered validator refs to the gate's required set (D7 confirm/route).

    Order-stable, dedup-on-merge. ``base`` (recipe-final-gate validators) keeps its
    position; pack refs are appended. This is the ONLY wiring the live gate needs:
    the comparison in ``cli/engine.py`` already enforces ``required_validators``.
    """
    return tuple(dict.fromkeys((*base, *pack_validator_refs)))
```

Then quote the CURRENT append block (re-grep, ≈`:461–463`) and extend it. CURRENT:
```python
    required_validators = list(plan.final_gate_policy.required_validators)
    if "openmagi.dev-coding" in plan.selected_pack_ids:
        required_validators.append("verifier:dev-coding:test-evidence")
```
REPLACE with (route loaded-pack validator refs into the gate, default-safe when no packs):
```python
    required_validators = list(plan.final_gate_policy.required_validators)
    if "openmagi.dev-coding" in plan.selected_pack_ids:
        required_validators.append("verifier:dev-coding:test-evidence")
    required_validators = list(
        _merge_pack_validator_refs(
            tuple(required_validators),
            _loaded_pack_validator_refs(),
        )
    )
```

Add the loader bridge (lazy, fail-open to empty so the OFF path is byte-identical) below the helper:
```python
def _loaded_pack_validator_refs() -> tuple[str, ...]:
    """Validator refs from disk-discovered packs (first-party + user), via the
    Phase-1 loader. Fail-open to () so a missing/empty pack tree leaves the
    assembly byte-identical to pre-Phase-3 behavior.
    """
    try:
        from magi_agent.packs.catalog_build import build_catalog_from_manifests
        from magi_agent.packs.discovery import (
            _default_search_paths,
            discover_packs,
        )
    except Exception:
        return ()
    try:
        manifests = discover_packs(_default_search_paths())
        catalog = build_catalog_from_manifests(manifests)
        return tuple(catalog.validator_refs)
    except Exception:
        return ()
```
> **Re-grep note:** the contract above (`discover_packs(search_paths: list[Path])`, line 103) has no
> `None` default, so we pass `_default_search_paths()` explicitly. If Phase 1 instead exposed a
> `None`-default `discover_packs()` that internally resolves the default roots, call
> `discover_packs(None)` and drop the `_default_search_paths` import. Verify the actual signature in
> `magi_agent/packs/discovery.py` before editing.

- [ ] **Step 5: Run it, see it pass.**

Run:
```bash
MAGI_CONFIG="$(mktemp -d)/config.toml" uv run pytest \
  tests/cli/test_pack_validators_reach_gate.py -q
```
Expected: PASS (2 tests).

- [ ] **Step 6: Regression — confirm the existing assembly behavior is unchanged when no extra
  packs add validators.** The dev-coding wiring test must still pass:

```bash
MAGI_CONFIG="$(mktemp -d)/config.toml" uv run pytest \
  magi_agent/cli/tests/test_runtime_policy_wiring.py -q
```
Expected: all pass (the bundled source-opened ref now also appears in `required_validators`, which
is additive; if a strict equality assertion on `required_validators` fails because the new ref was
appended, that is an **intended additive change** — update that single assertion to membership
(`in`) and note it in the commit body).

- [ ] **Step 7: Commit.**

```bash
git add magi_agent/cli/real_runner.py tests/cli/test_pack_validators_reach_gate.py
git commit -m "feat(packs): route disk-pack validator refs into the live required_validators gate"
```

---

## Task 3.4: USER pack — add + override + remove (forbid), no first-party privilege

**Files:**
- Test: `tests/packs/test_user_validator_pack_add_override_remove.py`
- (Test-only fixtures written to a temp `~/.magi/packs/` inside the test — no repo files.)

**Goal:** Prove §1: a user pack can **add** a second validator, **override** the first-party ref,
and **remove/forbid** the first-party ref — all through the same loader/catalog/`ValidatorCtx`,
with no first-party shortcut.

- [ ] **Step 1: Re-grep how Phase-1 resolves override/forbid** (so the test drives the real knob,
  not an invented one):

```bash
cd /Users/kevin/Desktop/claude_code/magi-agent/.worktrees/neutral-runtime
grep -n "override\|forbid\|disable\|\[packs\]\|enabled\|order\|precedence" magi_agent/packs/discovery.py magi_agent/packs/loader.py
```
Record the exact mechanism (e.g. load-order-last-wins for same `ref`; a `config.toml [packs]`
`disable = [...]` / `forbid = [...]`). The assertions below use **that** mechanism; if Phase 1
exposes a `config.toml` knob, the test writes it into the temp config.

- [ ] **Step 2: Write the failing test.** It builds a temp first-party-equivalent tree + a temp user
  tree, then asserts add/override/remove via discovery+registry. (Uses the real bundled pack for
  first-party; the user tree is created in `tmp_path`.)

```python
# tests/packs/test_user_validator_pack_add_override_remove.py
from __future__ import annotations

from pathlib import Path

import magi_agent
from magi_agent.packs.catalog_build import build_catalog_from_manifests
from magi_agent.packs.context import ValidatorCtx
from magi_agent.packs.discovery import discover_packs
from magi_agent.packs.registries import PrimitiveRegistries

_FIRST_PARTY_ROOT = Path(magi_agent.__file__).parent / "firstparty" / "packs"


def _write_user_pack(root: Path, *, pack_id: str, ref: str, status: str, name: str) -> Path:
    pack_dir = root / name
    pack_dir.mkdir(parents=True, exist_ok=True)
    (pack_dir / "__init__.py").write_text("")
    (pack_dir / "impl.py").write_text(
        "from magi_agent.evidence.validator_taxonomy import ValidatorResult\n"
        "from magi_agent.packs.context import ValidatorCtx\n"
        f"_REF = {ref!r}\n"
        "def user_validator(ctx: ValidatorCtx) -> ValidatorResult:\n"
        "    return ValidatorResult(\n"
        "        validatorId=_REF, trustClass='deterministic',\n"
        f"        status={status!r}, claimRef=_REF,\n"
        "    )\n"
    )
    (pack_dir / "pack.toml").write_text(
        f"[pack]\nid = {pack_id!r}\nversion = \"0.0.1\"\n\n"
        "[[provides]]\n"
        "type = \"validator\"\n"
        f"ref = {ref!r}\n"
        f"impl = \"{name}.impl:user_validator\"\n"
    )
    return pack_dir


def _make_importable(root: Path, monkeypatch) -> None:
    import sys

    monkeypatch.syspath_prepend(str(root))


def test_user_pack_ADDS_a_second_validator(tmp_path, monkeypatch) -> None:
    user_root = tmp_path / "user_packs"
    _write_user_pack(
        user_root, pack_id="user.quote", ref="validator:userQuote@1",
        status="supported", name="user_quote_pack",
    )
    _make_importable(user_root, monkeypatch)

    manifests = discover_packs([_FIRST_PARTY_ROOT, user_root])
    catalog = build_catalog_from_manifests(manifests)
    assert "validator:sourceOpened@1" in catalog.validator_refs  # first-party still present
    assert "validator:userQuote@1" in catalog.validator_refs     # user ADD works

    registries = PrimitiveRegistries.from_manifests(manifests)
    assert "validator:userQuote@1" in registries.validators


def test_user_pack_OVERRIDES_a_first_party_ref(tmp_path, monkeypatch) -> None:
    user_root = tmp_path / "user_packs"
    # Same ref as the first-party validator → override by load order (user dir last).
    _write_user_pack(
        user_root, pack_id="user.override-source", ref="validator:sourceOpened@1",
        status="failed", name="user_override_pack",
    )
    _make_importable(user_root, monkeypatch)

    manifests = discover_packs([_FIRST_PARTY_ROOT, user_root])
    registries = PrimitiveRegistries.from_manifests(manifests)
    impl = registries.validators["validator:sourceOpened@1"]
    # First-party returns 'supported' when observed; the user override always 'failed'.
    result = impl(
        ValidatorCtx(
            required_ref="validator:sourceOpened@1",
            observed_public_refs=frozenset({"validator:sourceOpened@1"}),
        )
    )
    assert result.status == "failed"  # user impl WON — no first-party privilege


def test_user_pack_REMOVES_forbids_a_first_party_ref(tmp_path, monkeypatch) -> None:
    user_root = tmp_path / "user_packs"
    config_path = tmp_path / "config.toml"
    # Re-grep Phase-1's forbid knob (Task 3.4 Step 1); this is the canonical [packs] form.
    config_path.write_text(
        "[packs]\nforbid = [\"validator:sourceOpened@1\"]\n"
    )
    monkeypatch.setenv("MAGI_CONFIG", str(config_path))

    manifests = discover_packs([_FIRST_PARTY_ROOT, user_root])
    catalog = build_catalog_from_manifests(manifests)
    registries = PrimitiveRegistries.from_manifests(manifests)
    assert "validator:sourceOpened@1" not in catalog.validator_refs  # forbidden out
    assert "validator:sourceOpened@1" not in registries.validators
```

> **Re-grep adaptation:** if Phase 1's forbid mechanism is `disable = [...]` keyed by **pack_id**
> (not ref), change the forbid test to `disable = ["openmagi.source-opened"]` and keep the same
> assertions. Use whatever Step-1 revealed; do not invent a knob.

- [ ] **Step 3: Run it, see it fail.**

Run:
```bash
MAGI_CONFIG="$(mktemp -d)/config.toml" uv run pytest \
  tests/packs/test_user_validator_pack_add_override_remove.py -q
```
Expected: FAIL (override/forbid not yet honored, or the temp config path not read).

- [ ] **Step 4: Make it pass — this is a Phase-1 resolution responsibility, but Phase 3 OWNS the
  proof.** If discovery already honors load-order-override and `[packs] forbid`, the test passes
  with no code change (Phase 1 was complete). If it does NOT:
  - **Override:** ensure `PrimitiveRegistries.from_manifests` registers in manifest order so a later
    same-`ref` impl replaces the earlier (last-wins). Quote the actual `from_manifests` body and, if
    it raises on duplicate ref, change the duplicate handling to overwrite (last-wins) — keep a
    debug log of the override. This is a **minimal Phase-1 follow-up**, committed here.
  - **Forbid:** ensure `discover_packs` / `build_catalog_from_manifests` drop refs/pack_ids listed
    in `[packs] forbid` / `disable`. If `discover_packs(None)` reads `MAGI_CONFIG`, the test's
    `monkeypatch.setenv("MAGI_CONFIG", ...)` already routes it; otherwise pass the config path
    through the signature Phase 1 provides.

  Make the smallest change that satisfies the §1 assertions; **do not** add a first-party-only code
  path (that would violate §1 — the whole point is parity).

- [ ] **Step 5: Run it, see it pass.**

Run:
```bash
MAGI_CONFIG="$(mktemp -d)/config.toml" uv run pytest \
  tests/packs/test_user_validator_pack_add_override_remove.py -q
```
Expected: PASS (3 tests).

- [ ] **Step 6: Commit.**

```bash
git add tests/packs/test_user_validator_pack_add_override_remove.py \
        magi_agent/packs/registries.py magi_agent/packs/discovery.py
git commit -m "test(packs): prove user validator add/override/forbid first-party with no privilege (§1)"
```
(Drop `registries.py`/`discovery.py` from `git add` if no code change was needed.)

---

## Task 3.5: End-to-end fake-model turn — a USER validator actually enforces

**Files:**
- Test: `tests/cli/test_user_validator_enforces_end_to_end.py`

**Goal:** The capstone. Run a real `MagiEngineDriver` turn (fake-model, no keys). A **user**
validator's ref is in `required_validators`. Assert: (a) when the tool does NOT emit the ref →
gate `block`; (b) when the tool DOES emit the ref → gate `pass`. This proves the registered
user validator reaches the **live** enforce point and changes the runtime decision.

- [ ] **Step 1: Re-grep the driver gate seam + the verifier bus matcher** to confirm the DI we
  drive:

```bash
cd /Users/kevin/Desktop/claude_code/magi-agent/.worktrees/neutral-runtime
grep -n "def __init__\|runner_policy_assembly\|evidence_collector\|_pre_final_gate_payload\|observed_public_refs" magi_agent/cli/engine.py | head
grep -n "matchedRefs\|validatorRefs\|def record_tool_result\|def collect_for_turn" magi_agent/evidence/local_tool_collector.py | head
```
Confirm: `MagiEngineDriver(runner_policy_assembly=…, evidence_collector=…)` and that a collected
record carrying `validatorRefs=[ref]` makes that ref land in `observed_public_refs`.

- [ ] **Step 2: Write the failing test.** It assembles `required_validators` via the SAME merge
  helper from Task 3.3 (so the path is identical to production), then drives the gate payload
  directly (the engine's pure decision function), once without the ref observed (block) and once
  with it observed (pass).

```python
# tests/cli/test_user_validator_enforces_end_to_end.py
from __future__ import annotations

from magi_agent.cli.engine import MagiEngineDriver, RunnerPolicyAssembly
from magi_agent.cli.real_runner import _merge_pack_validator_refs

_USER_REF = "validator:userQuote@1"


def _assembly_with_user_validator() -> RunnerPolicyAssembly:
    # Non-dev-coding pack id → _pre_final_gate_applies returns True every turn.
    required_validators = _merge_pack_validator_refs((), (_USER_REF,))
    return RunnerPolicyAssembly(
        modelProvider="local",
        modelLabel="local-dev",
        selectedPackIds=("user.quote",),
        evidenceRequirements=(),
        requiredValidators=required_validators,
        missingEvidenceAction="audit",
    )


def _tool_record_emitting(ref: str) -> dict[str, object]:
    # Shape mirrors LocalToolEvidenceCollector output consumed by the verifier bus.
    return {
        "type": "ToolResult",
        "status": "ok",
        "observedAt": 1000.0,
        "source": {"kind": "tool_trace"},
        "fields": {},
        "validatorRefs": [ref],
        "evidenceRefs": [],
    }


def test_user_validator_blocks_when_not_observed() -> None:
    driver = MagiEngineDriver(
        runner=None,
        runner_policy_assembly=_assembly_with_user_validator(),
        evidence_collector=lambda _turn: (),  # tool emitted nothing
    )
    payload = driver._pre_final_gate_payload(
        session_id="s1",
        turn_id="t1",
        prompt="please produce a final answer",
        harness_state=None,
        observed_public_refs=set(),
    )
    assert payload is not None
    assert payload["decision"] == "block"
    assert _USER_REF in payload["missingValidators"]


def test_user_validator_passes_when_tool_emits_ref() -> None:
    driver = MagiEngineDriver(
        runner=None,
        runner_policy_assembly=_assembly_with_user_validator(),
        evidence_collector=lambda _turn: (_tool_record_emitting(_USER_REF),),
    )
    payload = driver._pre_final_gate_payload(
        session_id="s1",
        turn_id="t1",
        prompt="please produce a final answer",
        harness_state=None,
        observed_public_refs=set(),
    )
    assert payload is not None
    assert payload["decision"] == "pass"
    assert _USER_REF not in payload["missingValidators"]
```

> **Re-grep adaptation:** if `MagiEngineDriver.__init__` requires a non-`None` `runner`, pass the
> `MockRunner([])` from `magi_agent/cli/tests/test_engine.py` instead of `None`. If the evidence
> collector expects typed records (not dicts), build the record via
> `LocalToolEvidenceCollector.record_tool_result(...)` then `collect_for_turn(turn_id)` — re-grep
> the exact `record_tool_result` signature (`local_tool_collector.py:~61`) and mirror
> `tests/cli/tests/test_local_tool_evidence_wiring.py`.

- [ ] **Step 3: Run it, see it fail.**

Run:
```bash
LOCAL_DEV_MODEL_SENTINEL="local-dev" MAGI_CONFIG="$(mktemp -d)/config.toml" uv run pytest \
  tests/cli/test_user_validator_enforces_end_to_end.py -q
```
Expected: FAIL if the evidence-record shape isn't folded into `observed_public_refs` (then adapt
per the re-grep note using `LocalToolEvidenceCollector`); otherwise FAIL only if a prior task's
wiring regressed.

- [ ] **Step 4: Make it pass.** No new runtime code should be needed — Task 3.3 already routes the
  ref into `required_validators`, and the engine + verifier bus already match observed refs. If the
  pass case still blocks, the tool-record shape is wrong: switch to the real collector path:

```python
from magi_agent.evidence.local_tool_collector import LocalToolEvidenceCollector

def _collector_emitting(ref: str):
    collector = LocalToolEvidenceCollector()
    collector.record_tool_result(
        session_id="s1",
        turn_id="t1",
        tool_name="QuoteCheck",
        tool_call_id="call-1",
        result={
            "status": "ok",
            "metadata": {"validatorRefs": [ref], "evidenceRefs": []},
        },
    )
    return lambda turn_id: collector.collect_for_turn(turn_id)
```
Re-grep `record_tool_result` (`local_tool_collector.py:~61`) for the exact kwarg names and align.

- [ ] **Step 5: Run it, see it pass.**

Run:
```bash
LOCAL_DEV_MODEL_SENTINEL="local-dev" MAGI_CONFIG="$(mktemp -d)/config.toml" uv run pytest \
  tests/cli/test_user_validator_enforces_end_to_end.py -q
```
Expected: PASS (2 tests) — block when unobserved, pass when the tool emits the user ref.

- [ ] **Step 6: Golden regression — the gate path touches no control-plane LoopControl, but run the
  oracle to prove it.** Task 3.3 edits only `real_runner.py`'s assembly builder (not any of the 6
  LoopControls), so the golden trace MUST be unchanged:

```bash
MAGI_CONFIG="$(mktemp -d)/config.toml" uv run pytest \
  tests/fixtures/neutral_runtime_golden/test_golden_regression.py -q
```
Expected: PASS (no diff). **A diff here = an unintended behavior change to review; regenerate via
`python -m tests.fixtures.neutral_runtime_golden.capture --write` only if the change is intended.**

- [ ] **Step 7: Commit.**

```bash
git add tests/cli/test_user_validator_enforces_end_to_end.py
git commit -m "test(packs): e2e fake-model proof — user validator enforces at the live gate (block/pass)"
```

---

## Task 3.6: Phase-wide green + §1 micro-assertion

**Files:**
- Test: `tests/packs/test_no_first_party_validator_privilege.py`

- [ ] **Step 1: Write the §1 micro-assertion** — the first-party validator is registered through the
  exact same registry path as a user one (no hardcoded shortcut), and its impl signature takes only
  `ValidatorCtx`:

```python
# tests/packs/test_no_first_party_validator_privilege.py
from __future__ import annotations

import inspect
from pathlib import Path

import magi_agent
from magi_agent.packs.discovery import discover_packs
from magi_agent.packs.registries import PrimitiveRegistries

_FIRST_PARTY_ROOT = Path(magi_agent.__file__).parent / "firstparty" / "packs"


def test_first_party_validator_has_no_privileged_registration() -> None:
    manifests = discover_packs([_FIRST_PARTY_ROOT])
    registries = PrimitiveRegistries.from_manifests(manifests)
    impl = registries.validators["validator:sourceOpened@1"]
    sig = inspect.signature(impl)
    # Exactly one positional param: the typed ValidatorCtx (no god-object, no privileged kwargs).
    params = [p for p in sig.parameters.values() if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)]
    assert len(params) == 1


def test_no_hardcoded_first_party_validator_in_real_runner() -> None:
    # The bundled validator ref must NOT be string-literal injected on the live path;
    # it must arrive via the pack catalog (Task 3.3 _loaded_pack_validator_refs).
    src = (Path(magi_agent.__file__).parent / "cli" / "real_runner.py").read_text()
    assert "validator:sourceOpened@1" not in src
```

- [ ] **Step 2: Run, see it pass** (it should pass given Tasks 3.1–3.3):

```bash
MAGI_CONFIG="$(mktemp -d)/config.toml" uv run pytest \
  tests/packs/test_no_first_party_validator_privilege.py -q
```
Expected: PASS (2 tests).

- [ ] **Step 3: Run the full Phase-3 selection + the Phase-0 oracle together** (final gate):

```bash
LOCAL_DEV_MODEL_SENTINEL="local-dev" MAGI_CONFIG="$(mktemp -d)/config.toml" uv run pytest \
  tests/firstparty/ \
  tests/packs/test_user_validator_pack_add_override_remove.py \
  tests/packs/test_no_first_party_validator_privilege.py \
  tests/cli/test_pack_validators_reach_gate.py \
  tests/cli/test_user_validator_enforces_end_to_end.py \
  magi_agent/cli/tests/test_runtime_policy_wiring.py \
  tests/fixtures/neutral_runtime_golden/test_golden_regression.py -q
```
Expected: all green, headless, no API keys. **The golden regression in this selection is the
control-plane safety net: a diff = behavior change to review; regenerate via `capture --write` only
if intended.**

- [ ] **Step 4: Commit.**

```bash
git add tests/packs/test_no_first_party_validator_privilege.py
git commit -m "test(packs): §1 micro-assertion — first-party validator registered with no privilege"
```

---

## Acceptance criteria (Phase 3 done)

- [ ] A bundled first-party validator pack exists on disk at
  `magi_agent/firstparty/packs/source_opened_validator/pack.toml` with a `module:symbol` impl,
  loaded by the **same** loader a user pack uses (D1/D3).
- [ ] Discovery → catalog → registry flow is proven for the bundled pack:
  `catalog.validator_refs` contains `validator:sourceOpened@1` and
  `PrimitiveRegistries.validators` has its `ValidatorCtx`-typed impl (D4/D5).
- [ ] Pack-discovered validator refs reach the **existing** enforce point: they appear in
  `RunnerPolicyAssembly.required_validators` (Task 3.3) and the unchanged
  `cli/engine.py` comparison yields `block`/`pass` accordingly (D7).
- [ ] A user pack in a temp `~/.magi/packs/` can **add** a validator, **override** the first-party
  ref (user impl wins by load order), and **remove/forbid** the first-party ref — all proven by
  tests (§1).
- [ ] An end-to-end fake-model turn proves a **user** validator enforces: `block` when its ref is
  not observed, `pass` when a tool emits it (Task 3.5).
- [ ] §1 micro-assertions pass: first-party validator impl takes only `ValidatorCtx`; no
  `validator:sourceOpened@1` string-literal on the live path in `real_runner.py`.
- [ ] `uv run pytest` of the full Phase-3 selection **plus** the Phase-0 golden regression is green,
  headless, no API keys.

## Rollback

Phase 3 is additive and revertible by commit/branch:
- New files (`magi_agent/firstparty/**`, `tests/firstparty/**`, `tests/packs/**`, the two
  `tests/cli/**` files) — delete or `git revert`.
- The single live-path edit is in `magi_agent/cli/real_runner.py`
  (`_merge_pack_validator_refs` + `_loaded_pack_validator_refs` + the one append line). Reverting
  that block restores byte-identical pre-Phase-3 assembly behavior because `_loaded_pack_validator_refs`
  fails open to `()` — the OFF/no-packs path is unchanged.
- The conditional discovery edit (Task 3.2 Step 5, bundled dir as search root) is additive; revert
  if present. No control-plane LoopControl is touched, so the Phase-0 golden never needs regenerating
  for a Phase-3 rollback.

## Hand-off to later phases

- **Phase 4 (easy provides types)** replicates this exact pattern per type. The reusable shape is:
  (1) bundled `firstparty/packs/<name>/pack.toml` + `impl.py` taking only its typed ctx;
  (2) discovery→catalog→registry integration test; (3) a confirm/route line that lets the
  discovered ref reach its already-live consumer; (4) a user-pack add/override/forbid test; (5) an
  e2e enforce/observe test. `_merge_pack_validator_refs` is the template for the per-type
  "route discovered refs into the live consumer" step.
- **Phase 5 (control-plane migration)** reuses the proven loader+registry+typed-ctx surface; its
  extra burden is the Phase-0 golden diff per migrated control, which Phase 3 leaves green.
- **Phase 6 (microkernel shrink + flat-catalog flip)** depends on this proof that a pack-loaded
  primitive reaches the live gate; it flips `_loaded_pack_validator_refs` from "additive append" to
  "the catalog source of truth" and removes any remaining first-party-only validator hardcode,
  asserted by `test_no_hardcoded_first_party_validator_in_real_runner` generalized across types.
- **Known re-grep dependencies** future phases must honor: the Phase-1 `discover_packs` signature
  (`None`-default vs explicit paths), the override mechanism (load-order last-wins) and forbid knob
  (`[packs] forbid`/`disable`), and the `ValidatorCtx` field set
  (`.required_ref`, `.observed_public_refs`). If any drifted, fix the import/call, not the proof.
