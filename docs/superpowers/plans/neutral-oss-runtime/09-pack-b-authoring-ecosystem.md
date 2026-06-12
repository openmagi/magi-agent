# Pack B — Authoring Ecosystem (B0 zero-setup loading + B1 scaffolding CLI + B2 authoring docs + B3 example pack)

> **For agentic workers:** REQUIRED SUB-SKILL: `superpowers:subagent-driven-development` or
> `superpowers:executing-plans`. Read `00-BLUEPRINT.md` (§1 "no privilege", D1–D7, conventions)
> and `08-ROADMAP-post-p6.md` (§Pack B scope contract) first. This phase **depends on P1–P6
> being landed on this branch** (they are — the `magi_agent/packs/` kernel, the 8 bundled
> first-party packs, and the pack-loaded `build_default_plugin` all ship in the tree).
> **B4/B5 (clawy hosted surfaces: recipe-making skill, hosted Customize tab) are OUT of this
> doc** — they are a separate checkpoint-gated workstream per the roadmap's autonomy policy.

**Goal:** Make the neutral runtime *usable by a stranger*. After P6 the runtime is neutral but
"real-but-unusable" (roadmap diagnosis: third-party ecosystem = policy doc only, 0 external
examples). Pack B closes that: **B0** fixes the measured DX gap from real-model QA (a pure disk
drop of a user pack fails to import without manual `PYTHONPATH` setup), **B1** ships
`magi pack new <type> <name>` scaffolding for all 8 provides types, **B2** ships authoring docs
generated from the REAL shipped models, **B3** commits a working external-shaped example pack
(custom validator + custom callback) as a template.

**Architecture:** No new runtime surfaces. B0 is a narrow fallback inside the existing
`lazy_import_symbol` seam in `magi_agent/packs/loader.py` (try plain import; on
`ModuleNotFoundError` of the pack's own top-level module, append the discovered pack's parent
dir to `sys.path` and retry). B1 adds a pure-functions scaffolding engine
(`magi_agent/packs/scaffold.py`) plus a thin Typer sub-app in `magi_agent/cli/app.py`
(the existing `auth_app`/`gateway_app` pattern). B2 is docs + a consistency test that pins the
docs to the real pydantic models. B3 is a committed pack under `examples/packs/` exercising the
B0 zero-setup path end-to-end.

**Tech stack:** Python 3.11+ (`requires-python = ">=3.11"`), `uv`, pydantic v2 (frozen,
`extra="forbid"`), `tomllib` (stdlib), pytest, typer (in the `cli` extra). No new dependencies.

---

## 0. Ground truth (measured against this branch's HEAD, 0ee21b8c)

The branch has ~60 commits beyond the roadmap's assumptions. **The tree is ground truth** —
re-verify each fact at the point of use (grep, don't trust line numbers). Two post-75a45520
commits matter here: `42288b81` made pack override resolution **base-precedence** (see the
forbid/override bullet below), and `0ee21b8c` made `build_control_plane_from_packs`
(`packs/registries.py`) statically filter to control_plane-declaring packs before `load_packs`
(so a broken non-control pack no longer blocks runner start — no impact on B0's seam).

- **The kernel shipped:** `magi_agent/packs/{manifest,discovery,loader,registries,context,
  catalog_build,types}.py` all exist. `CompileRecipePackCatalog` lives in `packs/types.py`
  (re-homed; `authoring/compiler.py` was **deleted** on main).
- **Manifest schema is top-level** (NOT a `[pack]` table): `packId`, `displayName`, `version`,
  `description`, `defaultEnabled`, `[[provides]]` with `type/ref/impl/spec/priority/phase/
  gatePosition`. See `magi_agent/packs/manifest.py` (`PackManifest`, `ProvidesEntry`).
- **8 bundled first-party packs** under `magi_agent/firstparty/packs/`: `tools_clock`,
  `callback_turn_audit`, `source_opened_validator`, `harness_coding_lean`,
  `control_plane_default` (4 provides entries: `control_plane:default@1`,
  `control_plane:loop-resilience@1`, `control_plane:facts-replan@1`,
  `control_plane:tool-synthesis-nudge@1`), `evidence_gitdiff`, `recipe_authoring_static`
  (declarative `spec`), `connector_local_readonly`.
- **Forbid/remove** = `config.toml [packs] disable = ["<pack_id>"]` (there is NO `"-"`-prefix
  or by-ref forbid in `PacksConfig`; an `override` tuple exists but is carried, not consumed,
  on this path). Override = load order last-wins; resolved order is **base precedence**
  (bundled first-party → `~/.magi/packs/` → `<cwd>/.magi/packs/`; sorted paths within a base)
  — NOT a global `pack_id` sort (changed by `42288b81`; see
  `packs/discovery.py::discover_pack_files`/`resolve_enabled_packs` docstrings). User bases
  load after the bundled base, so a user pack still overrides first-party by default.
- **The B0 DX gap is real and measured:** every disk-user-pack test in the tree needs
  `monkeypatch.syspath_prepend(str(user_root))` (14 occurrences across `tests/packs/` and
  `tests/adk_bridge/test_build_default_plugin_pack_loaded.py`; 16 total under `tests/`,
  also in `tests/cli/` and `tests/firstparty/`). Reproduced live on this HEAD:
  a tmp pack with `impl = "user_cp.impl:provide"` fed to `loader.load_from_bases` raises
  `ModuleNotFoundError: No module named 'user_cp'`.
- **CLI:** the Typer app lives in `magi_agent/cli/app.py` (`app = typer.Typer(...,
  cls=DefaultCommandGroup)`); subcommands register via `@app.command()` /
  `app.add_typer(sub_app, name=...)` (see `auth_app` at ~`:467–493`, `gateway_app` at
  ~`:500–711`). `magi_agent/cli/__main__.py` is a thin `--version` shim — do NOT add commands
  there. `magi_agent/cli/commands/` is the in-session **slash-command** layer, not Typer.
- **Docs site:** flat `docs/*.md` + `docs/manifest.json` page registry (groups include
  `"Developer"`, `"Recipes"`, `"Reference"`) + hand-maintained `docs/llms.txt` index.
  `openmagi.ai/docs` renders these manifest-driven. CLI reference source = `docs/cli/magi.md`.
- **Test env:** CI = `uv sync --extra dev --extra cli` then
  `MAGI_CONFIG=<tmp> uv run --no-sync pytest -q`. typer is in the `cli` extra.
- **Golden oracle:** `tests/fixtures/neutral_runtime_golden/test_golden_regression.py` — any
  task touching the control-plane controls **or their load/assembly path** must keep it green.

---

## Conventions recap (do not skip)

- **One-time env setup** (then use `--no-sync` everywhere, matching CI):

```bash
cd /Users/kevin/Desktop/claude_code/magi-agent/.worktrees/neutral-runtime
uv sync --extra dev --extra cli
```

- **TDD, bite-sized:** failing test → run (FAIL) → minimal impl → run (PASS) → commit. One
  logical change per commit. Conventional-commit messages.
- **No API keys, isolated config:** prefix EVERY pytest run with
  `MAGI_CONFIG="$(mktemp -d)/config.toml"` (avoids `~/.magi/config.toml` contamination).
- **Re-grep first:** every modify-task's Step 1 locates the current target; `:NNN` refs are
  HEAD-0ee21b8c snapshots and may have drifted.
- **Golden gate:** B0 changes the loader that feeds `build_control_plane_from_packs` →
  `build_default_plugin`. Both B0 tasks therefore run
  `tests/fixtures/neutral_runtime_golden/test_golden_regression.py` as a verify step. A diff =
  a behavior change to review (regenerate only if intentional, per the Phase-5 doc).
- **Reversibility:** every task is additive or fallback-only and independently revertible by
  commit. B0.2 depends on B0.1 (revert together).

---

## Task B0.1 — Loader auto-injects discovered pack roots into `sys.path` (the measured DX gap)

**Why first:** real-model QA measured that a user pack dropped at `<cwd>/.magi/packs/user_cp`
with `impl = "user_cp.impl:provide"` requires the user to export `PYTHONPATH` before `magi`
can load it. A pure disk drop MUST work with zero env setup — otherwise B1's scaffolding and
B3's example template generate packs that don't load.

**Files:**
- New test: `tests/packs/test_loader_zero_setup_import.py`
- Modify: `magi_agent/packs/loader.py`

- [ ] **Step 1: Re-grep the current seam.**

```bash
cd /Users/kevin/Desktop/claude_code/magi-agent/.worktrees/neutral-runtime
grep -n "def lazy_import_symbol\|import importlib\|^import sys\|lazy_import_symbol(entry.impl" magi_agent/packs/loader.py
```

Expected (current shipped code — the seam to extend):

```python
def lazy_import_symbol(impl: str) -> Any:
    """Resolve a ``"module.path:symbol"`` string to the live object. ..."""
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
```

and the single call site inside `load_packs` (~`:139`):

```python
                    impl=lazy_import_symbol(entry.impl),
```

There is no `import sys` in the module today (only `importlib`).

- [ ] **Step 2: Write the failing test** — the exact `user_cp` shape from
`tests/adk_bridge/test_build_default_plugin_pack_loaded.py::test_disk_user_control_plane_pack_loads_in_parallel_with_first_party`,
but **without** `monkeypatch.syspath_prepend`:

```python
# tests/packs/test_loader_zero_setup_import.py
"""Pack B0 — a pure disk drop of a user pack MUST import with ZERO env setup.

Measured DX gap (real-model QA): a pack at <root>/user_cp with
``impl = "user_cp.impl:provide"`` needed a manual PYTHONPATH export before the
loader could import it. The loader must auto-inject the discovered pack's
parent dir into ``sys.path`` (idempotent, append — installed packages keep
winning on a name collision) so ``magi pack new`` output and ~/.magi/packs
drops Just Work.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from magi_agent.packs.loader import RecordingSink, lazy_import_symbol, load_from_bases


def _write_user_cp_pack(root: Path) -> Path:
    """The EXACT user_cp shape from the §1 keystone test (external module path)."""
    pack_dir = root / "user_cp"
    pack_dir.mkdir(parents=True)
    (pack_dir / "__init__.py").write_text("")
    (pack_dir / "impl.py").write_text(
        "from magi_agent.adk_bridge.control_plane import BaseLoopControl\n"
        "class UserParallelControl(BaseLoopControl):\n"
        "    name = 'user.parallel.control'\n"
        "    async def on_before_model(self, *, callback_context, llm_request):\n"
        "        return None\n"
        "def provide(ctx):\n"
        "    ctx.register(UserParallelControl())\n"
    )
    (pack_dir / "pack.toml").write_text(
        'packId = "user.control-plane-extra"\n'
        'displayName = "user cp extra"\nversion = "0.0.1"\n\n'
        '[[provides]]\ntype = "control_plane"\n'
        'ref = "control_plane:user-extra@1"\n'
        'impl = "user_cp.impl:provide"\ngatePosition = "after"\n'
    )
    return pack_dir


def test_disk_pack_imports_with_zero_syspath_setup(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MAGI_CONFIG", str(tmp_path / "config.toml"))
    # Snapshot sys.path so the loader's auto-injection is reverted at teardown.
    monkeypatch.setattr(sys, "path", [*sys.path])
    # NOTE: deliberately NO monkeypatch.syspath_prepend — that is the gap.
    root = tmp_path / "packs"
    _write_user_cp_pack(root)

    result, _catalog = load_from_bases([root], RecordingSink())

    primitives = {(p.type, p.ref): p for p in result.primitives}
    assert ("control_plane", "control_plane:user-extra@1") in primitives
    assert callable(primitives[("control_plane", "control_plane:user-extra@1")].impl)


def test_missing_symbol_still_raises_import_error(tmp_path, monkeypatch) -> None:
    """Auto-injection must not swallow real errors: module found, symbol absent."""
    monkeypatch.setattr(sys, "path", [*sys.path])
    pack_dir = tmp_path / "sym_pack"
    pack_dir.mkdir(parents=True)
    (pack_dir / "__init__.py").write_text("")
    (pack_dir / "impl.py").write_text("def provide(ctx):\n    pass\n")
    with pytest.raises(ImportError, match="not found in module"):
        lazy_import_symbol("sym_pack.impl:nope", search_root=tmp_path)


def test_unrelated_missing_module_still_raises(tmp_path, monkeypatch) -> None:
    """A module path that does NOT live under the pack root must not be retried."""
    monkeypatch.setattr(sys, "path", [*sys.path])
    with pytest.raises(ModuleNotFoundError):
        lazy_import_symbol("definitely_not_a_real_module_xyz.impl:provide",
                           search_root=tmp_path)
```

- [ ] **Step 3: Run, see it FAIL.**

```bash
MAGI_CONFIG="$(mktemp -d)/config.toml" uv run --no-sync pytest tests/packs/test_loader_zero_setup_import.py -q
```

Expected: `test_disk_pack_imports_with_zero_syspath_setup` FAILS with
`ModuleNotFoundError: No module named 'user_cp'` (reproduced live on this HEAD).
`test_missing_symbol_still_raises_import_error` FAILS with
`TypeError: lazy_import_symbol() got an unexpected keyword argument 'search_root'`.

- [ ] **Step 4: Minimal impl.** Edit `magi_agent/packs/loader.py`:

(a) add `import sys` next to the existing `import importlib`;

(b) replace the body of `lazy_import_symbol` with:

```python
def lazy_import_symbol(impl: str, *, search_root: Path | None = None) -> Any:
    """Resolve a ``"module.path:symbol"`` string to the live object.

    Imports the module (lazily, at call time) and returns the attribute. Raises
    ``ValueError`` for a malformed ref and ``ImportError`` if the module or
    symbol cannot be resolved.

    ``search_root`` (B0, zero-setup disk packs): when the top-level module is
    not importable AND a matching package/module exists directly under
    ``search_root`` (the discovered pack's parent dir), the root is APPENDED to
    ``sys.path`` (append, not prepend — installed packages keep winning on a
    name collision) and the import retried once. ``importlib.invalidate_caches``
    is required because pack dirs are typically created after interpreter start.
    The appended entry is left in place (other entries of the same pack resolve
    through it); pack directory names should be unique across pack roots —
    ``sys.modules`` is keyed by top-level name, so the first pack imported under
    a given dir name wins.
    """
    if impl.count(":") != 1:
        raise ValueError(f"impl must be 'module.path:symbol', got {impl!r}")
    module_path, _, symbol = impl.partition(":")
    if not module_path or not symbol:
        raise ValueError(f"impl must be 'module.path:symbol', got {impl!r}")
    try:
        module = importlib.import_module(module_path)
    except ModuleNotFoundError as exc:
        top = module_path.split(".", 1)[0]
        if (
            search_root is None
            or exc.name != top
            or not (
                (search_root / top).is_dir()
                or (search_root / f"{top}.py").is_file()
            )
        ):
            raise
        root = str(search_root)
        if root not in sys.path:
            sys.path.append(root)
        importlib.invalidate_caches()
        module = importlib.import_module(module_path)
    try:
        return getattr(module, symbol)
    except AttributeError as exc:
        raise ImportError(f"symbol {symbol!r} not found in module {module_path!r}") from exc
```

(c) update the single call site in `load_packs` to pass the discovered pack's parent dir:

```python
                    impl=lazy_import_symbol(entry.impl, search_root=disc.pack_dir.parent),
```

(`disc.pack_dir` is the directory containing `pack.toml`; its parent is the search base the
impl's top-level module name resolves against — e.g. `<root>/user_cp/pack.toml` →
`search_root=<root>`, module `user_cp`.)

- [ ] **Step 5: Run, see it PASS, then the full packs + loader suites.**

```bash
MAGI_CONFIG="$(mktemp -d)/config.toml" uv run --no-sync pytest tests/packs/test_loader_zero_setup_import.py -q
MAGI_CONFIG="$(mktemp -d)/config.toml" uv run --no-sync pytest tests/packs/ tests/adk_bridge/test_build_default_plugin_pack_loaded.py -q
```

Expected: all PASS (existing tests that still `syspath_prepend` are unaffected — plain import
succeeds before the fallback is consulted).

- [ ] **Step 6: Golden regression (the loader feeds control-plane assembly).**

```bash
MAGI_CONFIG="$(mktemp -d)/config.toml" uv run --no-sync pytest tests/fixtures/neutral_runtime_golden/test_golden_regression.py -q
```

Expected: PASS with **zero diff** (the fallback never fires for bundled
`magi_agent.firstparty.*` impls — they import directly).

- [ ] **Step 7: Commit.**

```bash
git add magi_agent/packs/loader.py tests/packs/test_loader_zero_setup_import.py
git commit -m "fix(packs): auto-inject discovered pack roots into sys.path on impl import"
```

---

## Task B0.2 — Prove zero-setup on the §1 keystone test (drop the `syspath_prepend`)

**Files:**
- Modify: `tests/adk_bridge/test_build_default_plugin_pack_loaded.py`

- [ ] **Step 1: Re-grep the current keystone test.**

```bash
grep -n "syspath_prepend" tests/adk_bridge/test_build_default_plugin_pack_loaded.py
```

Expected — one hit inside
`test_disk_user_control_plane_pack_loads_in_parallel_with_first_party` (~`:101`):

```python
    monkeypatch.syspath_prepend(str(user_root))
    monkeypatch.setenv("MAGI_CONFIG", str(tmp_path / "config.toml"))
```

- [ ] **Step 2: Edit** — replace those two lines with a sys.path snapshot (hygiene: revert the
loader's auto-injection at teardown) and keep the env isolation:

```python
    # B0: NO syspath_prepend — the loader auto-injects the pack root (zero env setup).
    monkeypatch.setattr(sys, "path", [*sys.path])
    monkeypatch.setenv("MAGI_CONFIG", str(tmp_path / "config.toml"))
```

and add `import sys` to the module's imports (top of file currently imports only from
`magi_agent.adk_bridge.control_plane`; the function-local `from pathlib import Path` stays).

- [ ] **Step 3: Run — keystone + golden.**

```bash
MAGI_CONFIG="$(mktemp -d)/config.toml" uv run --no-sync pytest tests/adk_bridge/test_build_default_plugin_pack_loaded.py -q
MAGI_CONFIG="$(mktemp -d)/config.toml" uv run --no-sync pytest tests/fixtures/neutral_runtime_golden/test_golden_regression.py -q
```

Expected: all PASS, zero golden diff.

- [ ] **Step 4: Commit.**

```bash
git add tests/adk_bridge/test_build_default_plugin_pack_loaded.py
git commit -m "test(packs): keystone disk user pack loads with zero sys.path setup"
```

---

## Task B1.1 — Scaffolding engine: `magi_agent/packs/scaffold.py` (all 8 provides types)

Pure functions, no typer dependency (the CLI wrapper is B1.2). Every generated impl stub is a
copy-shape of the matching **shipped first-party impl** (capability parity by construction);
the generated smoke test loads the pack through the **real** loader and exercises the stub.

**Files:**
- New: `magi_agent/packs/scaffold.py`
- New test: `tests/packs/test_scaffold.py`

- [ ] **Step 1: Write the failing test.**

```python
# tests/packs/test_scaffold.py
"""Pack B1 — the scaffolding engine yields a loadable pack for every provides type."""
from __future__ import annotations

import sys

import pytest

from magi_agent.packs.loader import RecordingSink, load_from_bases
from magi_agent.packs.scaffold import PACK_TYPES, scaffold_pack


@pytest.mark.parametrize("ptype", PACK_TYPES)
def test_scaffolded_pack_loads_with_zero_syspath_setup(ptype, tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MAGI_CONFIG", str(tmp_path / "config.toml"))
    monkeypatch.setattr(sys, "path", [*sys.path])  # revert loader auto-injection
    # Unique name per type: sys.modules caches by top-level dir name.
    meta = scaffold_pack(ptype, f"demo-{ptype.replace('_', '-')}", tmp_path / "packs")

    result, _catalog = load_from_bases([tmp_path / "packs"], RecordingSink())
    primitives = {(p.type, p.ref): p for p in result.primitives}
    assert (ptype, meta.ref) in primitives, sorted(primitives)
    if ptype == "recipe":
        assert primitives[(ptype, meta.ref)].spec_path is not None
        assert meta.impl_path is None and meta.spec_path is not None
    else:
        assert callable(primitives[(ptype, meta.ref)].impl)
        assert meta.impl_path is not None and meta.spec_path is None
    assert meta.pack_toml.is_file() and meta.test_path.is_file()


def test_scaffold_rejects_unknown_type(tmp_path) -> None:
    with pytest.raises(ValueError, match="unknown pack type"):
        scaffold_pack("widget", "x", tmp_path / "packs")


def test_scaffold_rejects_existing_dir(tmp_path) -> None:
    scaffold_pack("tool", "dup-name", tmp_path / "packs")
    with pytest.raises(ValueError, match="already exists"):
        scaffold_pack("tool", "dup-name", tmp_path / "packs")


def test_module_name_sanitization_and_validator_ref(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MAGI_CONFIG", str(tmp_path / "config.toml"))
    monkeypatch.setattr(sys, "path", [*sys.path])
    meta = scaffold_pack("validator", "My Fancy-Check", tmp_path / "packs")
    assert meta.pack_dir.name == "my_fancy_check"
    assert meta.ref == "verifier:myFancyCheck@1"
```

- [ ] **Step 2: Run, see it FAIL.**

```bash
MAGI_CONFIG="$(mktemp -d)/config.toml" uv run --no-sync pytest tests/packs/test_scaffold.py -q
```

Expected: FAIL — `ModuleNotFoundError: No module named 'magi_agent.packs.scaffold'`.

- [ ] **Step 3: Minimal impl.** Create `magi_agent/packs/scaffold.py` (complete file):

```python
"""`magi pack new` scaffolding engine (Pack B1).

Generates a ready-to-load user pack for any of the 8 provides types (D2): a
``pack.toml`` manifest (schema = :mod:`magi_agent.packs.manifest`), an impl stub
that receives ONLY its D5 typed context (capability parity with first-party —
each stub is a copy-shape of the matching bundled first-party impl), and a
pytest smoke test that loads the pack through the REAL loader.

The generated impl module path is ``"<dir_name>.impl:provide"`` — importable
with ZERO env setup because the loader auto-injects the pack's parent dir into
``sys.path`` on demand (B0, ``loader.lazy_import_symbol`` search_root fallback).
Pack directory names must be unique across your pack roots (``sys.modules`` is
keyed by top-level name).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from magi_agent.packs.manifest import load_manifest_from_toml

PACK_TYPES: tuple[str, ...] = (
    "tool",
    "callback",
    "validator",
    "harness",
    "control_plane",
    "evidence_producer",
    "recipe",
    "connector",
)


@dataclass(frozen=True)
class ScaffoldResult:
    """Where everything was written, plus the ref the pack contributes."""

    pack_dir: Path
    ref: str
    pack_toml: Path
    impl_path: Path | None  # None for declarative recipe packs
    spec_path: Path | None  # set only for recipe packs
    test_path: Path


def _module_name(name: str) -> str:
    mod = re.sub(r"[^0-9a-zA-Z_]+", "_", name).strip("_").lower()
    if not mod or mod[0].isdigit():
        raise ValueError(f"cannot derive a python module name from {name!r}")
    return mod


def _camel(name: str) -> str:
    parts = [p for p in re.split(r"[^0-9a-zA-Z]+", name) if p]
    if not parts:
        raise ValueError(f"cannot derive a ref from {name!r}")
    return parts[0].lower() + "".join(p.title() for p in parts[1:])


def default_ref(ptype: str, name: str) -> str:
    """Per-type ref conventions, grounded in the bundled first-party shapes."""
    camel = _camel(name)
    pascal = camel[:1].upper() + camel[1:]
    kebab = _module_name(name).replace("_", "-")
    refs = {
        "tool": pascal,  # tools are reffed by ToolManifest.name (e.g. "Clock")
        "callback": kebab,  # hook name (e.g. "turn-audit")
        "validator": f"verifier:{camel}@1",  # live enforce-path public prefix
        "harness": f"harness:{kebab}@1",
        "control_plane": f"control_plane:{kebab}@1",
        "evidence_producer": f"evidence:{camel}@1",
        "recipe": f"recipe:{kebab}@1",
        "connector": f"connector:{kebab}@1",
    }
    return refs[ptype]


# --------------------------------------------------------------------------- #
# Templates. Token substitution (__TOKEN__ + .replace) — NOT str.format —      #
# because the generated python contains literal braces.                        #
# --------------------------------------------------------------------------- #

_IMPL_TEMPLATES: dict[str, str] = {
    "validator": '''\
"""User validator — receives ONLY the typed ValidatorCtx (capability parity)."""
from __future__ import annotations

from magi_agent.packs.context import ValidatorCtx, ValidatorVerdict


def provide(ctx: ValidatorCtx) -> ValidatorVerdict | None:
    """Pass iff the runtime observed this validator's public ref this turn.

    Replace the body with your own deterministic check over ``ctx.artifact``.
    """
    observed = tuple(ctx.artifact.get("observedRefs") or ())
    passed = ctx.ref in observed
    ctx.emit(passed=passed, detail=None if passed else "ref not observed this turn")
    return ctx.verdict()
''',
    "tool": '''\
"""User tool provider — registers a ToolManifest via the typed provide context."""
from __future__ import annotations

from magi_agent.packs.context import ToolProvideContext
from magi_agent.tools.catalog import CORE_TOOL_INPUT_SCHEMA
from magi_agent.tools.manifest import Budget, ToolManifest, ToolSource


def provide(context: ToolProvideContext) -> None:
    context.register(
        ToolManifest(
            name=__REF__,
            description="Describe what this tool does.",
            kind="external",
            source=ToolSource(kind="external", package=__PACK_ID__),
            permission="read",
            input_schema=CORE_TOOL_INPUT_SCHEMA,
            timeout_ms=30_000,
            budget=Budget(max_calls_per_turn=10, max_parallel=1),
            dangerous=False,
            is_concurrency_safe=True,
            mutates_workspace=False,
            parallel_safety="readonly",
            available_in_modes=("plan", "act"),
            tags=("user",),
            enabled_by_default=True,
            opt_out=True,
        )
    )
''',
    "callback": '''\
"""User callback provider — registers a HookManifest + handler (non-blocking)."""
from __future__ import annotations

from magi_agent.hooks.context import HookContext
from magi_agent.hooks.manifest import HookManifest, HookPoint
from magi_agent.hooks.result import HookResult
from magi_agent.packs.context import CallbackProvideContext
from magi_agent.tools.manifest import ToolSource


def handler(context: HookContext) -> HookResult:
    return HookResult(action="continue", reason="user callback observed")


def provide(context: CallbackProvideContext) -> None:
    context.register(
        HookManifest(
            name=__REF__,
            point=HookPoint.BEFORE_TURN_START,
            description="Describe what this callback audits.",
            source=ToolSource(kind="custom-plugin", package=__PACK_ID__),
            priority=100,
            blocking=False,
            opt_out=True,
        ),
        handler,
    )
''',
    "harness": '''\
"""User harness provider — registers a ResolvedHarnessPack."""
from __future__ import annotations

from magi_agent.harness.resolved import ResolvedHarnessPack
from magi_agent.packs.context import HarnessProvideContext


def provide(context: HarnessProvideContext) -> None:
    context.register(
        __REF__,
        ResolvedHarnessPack(
            enabled=True,
            source="custom-plugin",
            components={
                "tools": ("FileRead",),
                "hooks": (),
                "childAgent": (),
                "permissionDefaults": (),
            },
            opt_out_allowed=(),
        ),
    )
''',
    "control_plane": '''\
"""User control_plane provider — registers LoopControls via the typed context.

Receives the IDENTICAL ControlPlaneProvideContext first-party gets (no
privilege): ``context.env`` for env-gating plus the same runtime collaborators.
"""
from __future__ import annotations

from magi_agent.adk_bridge.control_plane import BaseLoopControl
from magi_agent.packs.context import ControlPlaneProvideContext


class UserControl(BaseLoopControl):
    name = __CONTROL_NAME__

    async def on_before_model(self, *, callback_context, llm_request):
        return None


def provide(context: ControlPlaneProvideContext) -> None:
    context.register(UserControl())
''',
    "evidence_producer": '''\
"""User evidence producer — registers a ProducerSpec (public_ref needs a
recognized prefix: evidence:/verifier:/receipt:sha256:/sha256:)."""
from __future__ import annotations

from magi_agent.packs.context import EvidenceProducerProvideContext, ProducerSpec


def provide(context: EvidenceProducerProvideContext) -> None:
    context.register(
        __REF__,
        ProducerSpec(
            evidence_type=__EVIDENCE_TYPE__,
            public_ref=__REF__,
            producer_surfaces=("tool_host",),
        ),
    )
''',
    "connector": '''\
"""User connector provider — registers a ConnectorSpec projecting ToolManifests."""
from __future__ import annotations

from magi_agent.packs.context import ConnectorProvideContext, ConnectorSpec
from magi_agent.tools.catalog import CORE_TOOL_INPUT_SCHEMA
from magi_agent.tools.manifest import Budget, ToolManifest, ToolSource


def provide(context: ConnectorProvideContext) -> None:
    context.register(
        __REF__,
        ConnectorSpec(
            server_ref=__SERVER_REF__,
            readonly=True,
            tool_manifests=(
                ToolManifest(
                    name=__TOOL_NAME__,
                    description="Describe what this connector tool does.",
                    kind="external",
                    source=ToolSource(kind="external", package=__PACK_ID__),
                    permission="read",
                    input_schema=CORE_TOOL_INPUT_SCHEMA,
                    timeout_ms=30_000,
                    budget=Budget(max_calls_per_turn=10, max_parallel=1),
                    dangerous=False,
                    is_concurrency_safe=True,
                    mutates_workspace=False,
                    parallel_safety="readonly",
                    available_in_modes=("plan", "act"),
                    tags=("connector", "user"),
                    enabled_by_default=True,
                    opt_out=True,
                ),
            ),
        ),
    )
''',
}

# Declarative recipe spec (camelCase aliases match recipes/compiler.py
# RecipePackManifest; ``description`` is REQUIRED — no model default).
_RECIPE_SPEC_TEMPLATE = '''\
# Declarative RecipePackManifest spec. Read + validated by the pack projector
# (magi_agent/packs/registries.py project_into_registries) into the live
# recipe registry.

packId = __PACK_ID__
version = "1"
displayName = __DISPLAY__
description = "User-authored declarative recipe."
defaultEnabled = true
toolRefs = ["FileRead"]
'''

_SMOKE_HEADER = '''\
"""Smoke test scaffolded by `magi pack new` — verifies this pack loads through
the REAL pack loader with zero sys.path setup. Run: pytest <this file>."""
from __future__ import annotations

from pathlib import Path

from magi_agent.packs.loader import RecordingSink, load_from_bases

PACKS_BASE = Path(__file__).resolve().parent.parent
PTYPE = __PTYPE__
REF = __REF__


def test_pack_loads_through_the_real_loader() -> None:
    result, _catalog = load_from_bases([PACKS_BASE], RecordingSink())
    primitives = {(p.type, p.ref): p for p in result.primitives}
    assert (PTYPE, REF) in primitives, sorted(primitives)
'''

_SMOKE_PROJECTION = '''\


def test_pack_projects_into_the_live_registries() -> None:
    from magi_agent.packs.registries import PackRegistries, project_into_registries

    result, _catalog = load_from_bases([PACKS_BASE], RecordingSink())
    report = project_into_registries(result.primitives, PackRegistries())
    assert REF in report.registered
'''

_SMOKE_VALIDATOR = '''\


def test_validator_emits_a_verdict() -> None:
    from magi_agent.packs.context import SessionReadView, ValidatorCtx

    result, _catalog = load_from_bases([PACKS_BASE], RecordingSink())
    impl = next(p.impl for p in result.primitives if p.ref == REF)
    session = SessionReadView(invocation_id="i", agent_name="a", turn_index=0)
    ctx = ValidatorCtx(ref=REF, artifact={"observedRefs": [REF]}, session=session)
    verdict = impl(ctx)
    assert verdict is not None and verdict.passed is True
'''

_SMOKE_CONTROL_PLANE = '''\


def test_provider_registers_at_least_one_control() -> None:
    from magi_agent.packs.context import ControlPlaneProvideContext

    result, _catalog = load_from_bases([PACKS_BASE], RecordingSink())
    impl = next(p.impl for p in result.primitives if p.ref == REF)
    registered: list = []
    impl(ControlPlaneProvideContext(register=registered.append))
    assert registered, "provider registered no LoopControl"
'''

# Types whose generated smoke test exercises project_into_registries.
_PROJECTION_TYPES: frozenset[str] = frozenset(
    {"tool", "callback", "evidence_producer", "recipe", "connector", "harness"}
)


def _manifest_toml(ptype: str, ref: str, pack_id: str, display: str, mod: str) -> str:
    lines = [
        f'packId = "{pack_id}"',
        f'displayName = "{display}"',
        'version = "0.1.0"',
        f'description = "User-authored {ptype} pack scaffolded by magi pack new."',
        "",
        "[[provides]]",
        f'type = "{ptype}"',
        f'ref = "{ref}"',
    ]
    if ptype == "recipe":
        lines.append('spec = "recipe.toml"')
    else:
        lines.append(f'impl = "{mod}.impl:provide"')
    if ptype == "control_plane":
        lines += ["priority = 100", 'phase = "loop"', 'gatePosition = "after"']
    elif ptype == "callback":
        lines += ["priority = 100", 'phase = "beforeTurnStart"']
    return "\n".join(lines) + "\n"


def _render(template: str, **tokens: str) -> str:
    out = template
    for key, value in tokens.items():
        out = out.replace(f"__{key}__", value)
    return out


def scaffold_pack(ptype: str, name: str, dest_root: Path) -> ScaffoldResult:
    """Write a loadable user pack for ``ptype`` under ``dest_root/<module_name>``."""
    if ptype not in PACK_TYPES:
        raise ValueError(
            f"unknown pack type {ptype!r}; expected one of: {', '.join(PACK_TYPES)}"
        )
    mod = _module_name(name)
    pack_dir = dest_root / mod
    if pack_dir.exists():
        raise ValueError(f"pack dir already exists: {pack_dir}")
    ref = default_ref(ptype, name)
    pack_id = f"user.{mod.replace('_', '-')}"
    camel = _camel(name)
    pascal = camel[:1].upper() + camel[1:]

    pack_dir.mkdir(parents=True)
    (pack_dir / "__init__.py").write_text("")
    pack_toml = pack_dir / "pack.toml"
    pack_toml.write_text(_manifest_toml(ptype, ref, pack_id, name, mod))

    impl_path: Path | None = None
    spec_path: Path | None = None
    if ptype == "recipe":
        spec_path = pack_dir / "recipe.toml"
        spec_path.write_text(
            _render(_RECIPE_SPEC_TEMPLATE, PACK_ID=repr(pack_id), DISPLAY=repr(name))
        )
    else:
        impl_path = pack_dir / "impl.py"
        impl_path.write_text(
            _render(
                _IMPL_TEMPLATES[ptype],
                REF=repr(ref),
                PACK_ID=repr(pack_id),
                CONTROL_NAME=repr(f"user.{mod}"),
                EVIDENCE_TYPE=repr(pascal),
                SERVER_REF=repr(mod.replace("_", "-")),
                TOOL_NAME=repr(f"{pascal}Open"),
            )
        )

    smoke = _render(_SMOKE_HEADER, PTYPE=repr(ptype), REF=repr(ref))
    if ptype in _PROJECTION_TYPES:
        smoke += _SMOKE_PROJECTION
    elif ptype == "validator":
        smoke += _SMOKE_VALIDATOR
    elif ptype == "control_plane":
        smoke += _SMOKE_CONTROL_PLANE
    test_path = pack_dir / f"test_{mod}_pack.py"
    test_path.write_text(smoke)

    # Self-check: the generated manifest must parse against the REAL schema.
    load_manifest_from_toml(pack_toml)

    return ScaffoldResult(
        pack_dir=pack_dir,
        ref=ref,
        pack_toml=pack_toml,
        impl_path=impl_path,
        spec_path=spec_path,
        test_path=test_path,
    )
```

- [ ] **Step 4: Run, see it PASS** (the parametrized test executes every template through the
real loader; the projection types additionally exercise their stub models when the generated
smoke tests run in B1.3):

```bash
MAGI_CONFIG="$(mktemp -d)/config.toml" uv run --no-sync pytest tests/packs/test_scaffold.py -q
```

If a template's model kwargs drifted from the shipped models (e.g. `ResolvedHarnessPack` in
`magi_agent/harness/resolved.py`, `ToolManifest` in `magi_agent/tools/manifest.py`,
`HookManifest` in `magi_agent/hooks/manifest.py`), the failure names the bad field — re-read
the model and fix the template; the shapes above are copied from the shipped first-party impls
(`tools_clock/impl.py`, `callback_turn_audit/impl.py`, `harness_coding_lean/impl.py`,
`evidence_gitdiff/impl.py`, `connector_local_readonly/impl.py`,
`recipe_authoring_static/authoring_static.recipe.toml`) on HEAD 0ee21b8c.

- [ ] **Step 5: Lint + type-check the new module.**

```bash
uv run --no-sync ruff check magi_agent/packs/scaffold.py tests/packs/test_scaffold.py
uv run --no-sync mypy magi_agent/packs/scaffold.py
```

- [ ] **Step 6: Commit.**

```bash
git add magi_agent/packs/scaffold.py tests/packs/test_scaffold.py
git commit -m "feat(packs): pack scaffolding engine for all 8 provides types"
```

---

## Task B1.2 — `magi pack new` Typer subcommand

**Files:**
- Modify: `magi_agent/cli/app.py`
- New test: `tests/cli/test_app_pack_new.py`

- [ ] **Step 1: Re-grep the registration anchor.**

```bash
grep -n 'app.add_typer(auth_app, name="auth")' magi_agent/cli/app.py
```

Expected (~`:493`):

```python
app.add_typer(auth_app, name="auth")
```

(`DefaultCommandGroup.parse_args` routes unknown first tokens to the default `agent` command;
registering `pack` via `add_typer` makes it a recognized sibling subcommand — same mechanism as
`auth`/`gateway`. `doctor` is a plain `@app.command()`, not an `add_typer` sub-app.)

- [ ] **Step 2: Write the failing CliRunner test** (mirrors
`tests/cli/test_app_legalbench.py`'s deferred-import pattern):

```python
# tests/cli/test_app_pack_new.py
"""CliRunner tests for the `magi pack new` scaffolding subcommand (Pack B1)."""
from __future__ import annotations

import sys


def _make_app():
    from magi_agent.cli.app import app
    return app


def test_pack_new_scaffolds_a_loadable_validator_pack(tmp_path, monkeypatch) -> None:
    from typer.testing import CliRunner

    monkeypatch.setenv("MAGI_CONFIG", str(tmp_path / "config.toml"))
    monkeypatch.setattr(sys, "path", [*sys.path])  # revert loader auto-injection
    runner = CliRunner()
    result = runner.invoke(
        _make_app(),
        ["pack", "new", "validator", "my-check", "--dest", str(tmp_path / "packs")],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output
    pack_dir = tmp_path / "packs" / "my_check"
    assert (pack_dir / "pack.toml").is_file()
    assert (pack_dir / "impl.py").is_file()
    assert (pack_dir / "test_my_check_pack.py").is_file()
    assert "pack created" in result.output

    from magi_agent.packs.loader import RecordingSink, load_from_bases

    loaded, _catalog = load_from_bases([tmp_path / "packs"], RecordingSink())
    assert any(p.ref == "verifier:myCheck@1" for p in loaded.primitives)


def test_pack_new_unknown_type_exits_2(tmp_path) -> None:
    from typer.testing import CliRunner

    runner = CliRunner()
    result = runner.invoke(
        _make_app(), ["pack", "new", "widget", "x", "--dest", str(tmp_path / "packs")]
    )
    assert result.exit_code == 2
    assert "unknown pack type" in result.output


def test_pack_root_prints_usage(tmp_path) -> None:
    from typer.testing import CliRunner

    runner = CliRunner()
    result = runner.invoke(_make_app(), ["pack"], catch_exceptions=False)
    assert result.exit_code == 0
    assert "magi pack new" in result.output
```

- [ ] **Step 3: Run, see it FAIL.**

```bash
MAGI_CONFIG="$(mktemp -d)/config.toml" uv run --no-sync pytest tests/cli/test_app_pack_new.py -q
```

Expected: FAIL. Because of `DefaultCommandGroup`, an unregistered `pack` token routes to the
default `agent` command (exit code != 0 in the CliRunner env / no "pack created" output) —
NOT a clean "no such command" error. That routing is exactly why Step 4 must register `pack`
as a real subcommand.

- [ ] **Step 4: Minimal impl.** In `magi_agent/cli/app.py`, insert directly AFTER the
`app.add_typer(auth_app, name="auth")` line:

```python
# ---------------------------------------------------------------------------
# `magi pack` — pack authoring (Pack B1)
# ---------------------------------------------------------------------------

pack_app = typer.Typer(
    name="pack",
    help="Author and manage user packs.",
    invoke_without_command=True,
    no_args_is_help=False,
)


@pack_app.callback(invoke_without_command=True)
def pack_root(ctx: typer.Context) -> None:
    """Author and manage user packs."""
    if ctx.invoked_subcommand is None:
        typer.echo("magi pack: use `magi pack new <type> <name>`.", err=False)


@pack_app.command("new")
def pack_new(
    ptype: str = typer.Argument(
        ...,
        metavar="TYPE",
        help=(
            "One of: tool, callback, validator, harness, control_plane, "
            "evidence_producer, recipe, connector."
        ),
    ),
    name: str = typer.Argument(..., help="Human name for the primitive (e.g. my-check)."),
    dest: Optional[Path] = typer.Option(
        None,
        "--dest",
        help="Packs root to scaffold into. Default: <cwd>/.magi/packs.",
    ),
) -> None:
    """Scaffold a ready-to-load user pack (pack.toml + impl stub + smoke test)."""
    from magi_agent.packs.scaffold import scaffold_pack  # noqa: PLC0415

    dest_root = dest if dest is not None else Path.cwd() / ".magi" / "packs"
    try:
        result = scaffold_pack(ptype, name, dest_root)
    except ValueError as exc:
        typer.echo(f"magi pack new: {exc}", err=True)
        raise typer.Exit(code=2)
    typer.echo(f"pack created: {result.pack_dir}")
    typer.echo(f"  ref:        {result.ref}")
    typer.echo(f"  manifest:   {result.pack_toml}")
    if result.impl_path is not None:
        typer.echo(f"  impl:       {result.impl_path}")
    if result.spec_path is not None:
        typer.echo(f"  spec:       {result.spec_path}")
    typer.echo(f"  smoke test: {result.test_path}")
    typer.echo(
        "next: edit the impl, then verify it loads with "
        f"`pytest {result.test_path}` — packs under <cwd>/.magi/packs are "
        "discovered automatically (no PYTHONPATH needed)."
    )


app.add_typer(pack_app, name="pack")
```

(`Optional` and `Path` are already imported at the top of `cli/app.py`; `typer` is the module's
top-level import. No other imports needed.)

- [ ] **Step 5: Run, see it PASS; then the wider CLI suite for regressions.**

```bash
MAGI_CONFIG="$(mktemp -d)/config.toml" uv run --no-sync pytest tests/cli/test_app_pack_new.py -q
MAGI_CONFIG="$(mktemp -d)/config.toml" uv run --no-sync pytest tests/cli/ -q
```

- [ ] **Step 6: Lint + mypy, commit.**

```bash
uv run --no-sync ruff check magi_agent/cli/app.py tests/cli/test_app_pack_new.py
uv run --no-sync mypy magi_agent/cli/app.py
git add magi_agent/cli/app.py tests/cli/test_app_pack_new.py
git commit -m "feat(cli): magi pack new scaffolding subcommand"
```

---

## Task B1.3 — End-to-end acceptance (the roadmap's B1 acceptance criterion, verify-only)

**Acceptance:** `magi pack new validator my-check` produces a pack that loads **and** its
generated smoke test passes — run through the real console script, not CliRunner.

- [ ] **Step 1: Run the real CLI and the generated smoke test.**

```bash
cd /Users/kevin/Desktop/claude_code/magi-agent/.worktrees/neutral-runtime
DEST="$(mktemp -d)"
MAGI_CONFIG="$DEST/config.toml" uv run --no-sync magi pack new validator my-check --dest "$DEST/packs"
MAGI_CONFIG="$DEST/config.toml" uv run --no-sync pytest "$DEST/packs/my_check/test_my_check_pack.py" -q
```

Expected: first command prints `pack created: …/packs/my_check` with ref
`verifier:myCheck@1`; second prints `2 passed` (load + verdict tests) with **zero**
`PYTHONPATH`/`sys.path` setup. Spot-check one more type the same way
(`magi pack new control_plane my-guard --dest "$DEST/packs"` → its smoke test passes).

- [ ] **Step 2: No commit** (verification only; B1 is already committed).

---

## Task B2.1 — `docs/pack-manifest-reference.md` + docs-consistency test

The reference is pinned to the REAL models by a test so it cannot rot silently.

**Files:**
- New test: `tests/packs/test_authoring_docs_consistency.py`
- New doc: `docs/pack-manifest-reference.md`

- [ ] **Step 1: Write the failing consistency test** (this file grows across B2.2–B2.4; create
it now with the B2.1 function):

```python
# tests/packs/test_authoring_docs_consistency.py
"""Pack B2 — authoring docs exist and reference the REAL shipped schema/ABI."""
from __future__ import annotations

from pathlib import Path

import magi_agent
from magi_agent.packs.manifest import PackManifest, ProvidesEntry

_DOCS = Path(magi_agent.__file__).resolve().parent.parent / "docs"


def _alias_or_name(model: type) -> set[str]:
    return {
        (field.alias or name) for name, field in model.model_fields.items()
    }


def test_manifest_reference_covers_every_real_field() -> None:
    text = (_DOCS / "pack-manifest-reference.md").read_text()
    for field in sorted(_alias_or_name(PackManifest) | _alias_or_name(ProvidesEntry)):
        assert f"`{field}`" in text, f"pack-manifest-reference.md missing `{field}`"
```

- [ ] **Step 2: Run, see it FAIL** (`FileNotFoundError: … docs/pack-manifest-reference.md`):

```bash
MAGI_CONFIG="$(mktemp -d)/config.toml" uv run --no-sync pytest tests/packs/test_authoring_docs_consistency.py -q
```

- [ ] **Step 3: Write the doc.** Create `docs/pack-manifest-reference.md` with exactly this
content (every field/alias below is quoted from `magi_agent/packs/manifest.py` on this branch —
if Step 4 fails, re-read that file and fix the doc, not the test):

```markdown
# Pack Manifest Reference (`pack.toml`)

A pack is a directory containing a `pack.toml` manifest. The manifest declares
its `provides` entries **statically**, so the catalog is built without importing
any pack code (impls are lazy-imported at registration time). Schema source:
`magi_agent/packs/manifest.py` (`PackManifest`, `ProvidesEntry` — pydantic v2,
frozen, `extra="forbid"`, camelCase aliases).

## Discovery

Packs are discovered by globbing `pack.toml` under three bases, in priority
order (`magi_agent/packs/discovery.py`):

1. bundled first-party: `magi_agent/firstparty/packs/`
2. user home: `~/.magi/packs/`
3. project: `<cwd>/.magi/packs/`

`config.toml` `[packs]` controls the set: `disable = ["<packId>"]` removes a
pack (this is also how you *remove/forbid* a first-party pack), `order = [...]`
pins load order (and opts a `defaultEnabled = false` pack back in). On a
colliding `(type, ref)` the **last** pack in resolved order wins, and packs
resolve in base-precedence order — bundled first-party, then `~/.magi/packs/`,
then `<cwd>/.magi/packs/` — so a pack in your user or project dir overrides
first-party by default. First-party holds no privilege: it is discovered,
overridable, and removable exactly like your packs.

Impl module paths resolve with zero env setup: the loader auto-appends the
discovered pack's parent directory to `sys.path` when the impl's top-level
module lives there. Keep pack directory names unique across your pack roots.

## Top-level fields

| Field | Type | Required | Notes |
|---|---|---|---|
| `packId` | string | yes | Non-empty, globally unique (e.g. `user.my-check`). |
| `displayName` | string | yes | Human-readable name. |
| `version` | string | no (default `"1"`) | Free-form version string. |
| `description` | string | no (default `""`) | One-line summary. |
| `defaultEnabled` | bool | no (default `true`) | `false` packs load only when pinned in `[packs] order`. |
| `provides` | array of tables | no (default empty) | The `[[provides]]` entries below. Duplicate `ref` within one pack is rejected. |

## `[[provides]]` entries

| Field | Type | Notes |
|---|---|---|
| `type` | string | One of the 8 provides types: `tool`, `callback`, `validator`, `harness`, `control_plane`, `evidence_producer`, `recipe`, `connector`. |
| `ref` | string | Non-empty public ref this entry contributes (e.g. `verifier:myCheck@1`). |
| `impl` | string | `"module.path:symbol"` — required for every type except `recipe`; mutually exclusive with `spec`. |
| `spec` | string | Relpath to a declarative spec file — required for `recipe`, forbidden elsewhere. |
| `priority` | int | Ordered types only (`callback`, `control_plane`): ascending registration order. |
| `phase` | string | Ordered types only: free-form phase label (e.g. `"loop"`, `"beforeTurnStart"`). |
| `gatePosition` | `"before"` \| `"after"` | `control_plane` only; defaults to `"after"` the permission gate. A before_tool-deciding control MUST opt into `"before"` explicitly (the dispatcher raises `GatePositionViolation` otherwise). |

Validation rules (enforced by the model validator):

- `recipe` entries must declare `spec` and not `impl`; every other type must
  declare `impl` and not `spec`.
- `impl` must be of the form `module.path:symbol`.
- `priority`/`phase` are rejected on non-ordered types; `gatePosition` is
  rejected on non-`control_plane` types.

## Catalog mapping

Loaded refs land in the flat live catalog (`magi_agent/packs/catalog_build.py`),
with no first-party-only tier: `tool` → `toolRefs`, `connector` →
`connectorRefs`, `validator` → `validatorRefs`, `harness` → `harnessRefs`,
`evidence_producer` → `evidenceProducerRefs`, `control_plane` + `callback` →
`pluginRefs`. `recipe` entries register their spec instead of a catalog ref.

## Example (a bundled first-party manifest — same format you use)

    packId = "openmagi.tools-clock"
    displayName = "Clock tool"
    version = "1.0.0"
    description = "First-party Clock tool bundled as a removable pack (no privilege)."

    [[provides]]
    type = "tool"
    ref = "Clock"
    impl = "magi_agent.firstparty.packs.tools_clock.impl:provide_clock"

Scaffold any of the 8 types with `magi pack new <type> <name>`.
```

- [ ] **Step 4: Run, see it PASS.**

```bash
MAGI_CONFIG="$(mktemp -d)/config.toml" uv run --no-sync pytest tests/packs/test_authoring_docs_consistency.py -q
```

- [ ] **Step 5: Commit.**

```bash
git add docs/pack-manifest-reference.md tests/packs/test_authoring_docs_consistency.py
git commit -m "docs(packs): pack.toml manifest schema reference pinned by consistency test"
```

---

## Task B2.2 — `docs/pack-context-reference.md` (the typed-context capability ceiling)

**Files:**
- Modify: `tests/packs/test_authoring_docs_consistency.py` (add one test)
- New doc: `docs/pack-context-reference.md`

- [ ] **Step 1: Add the failing test** to `tests/packs/test_authoring_docs_consistency.py`:

```python
def test_context_reference_covers_every_provides_type_and_real_classes() -> None:
    from magi_agent.packs import context as ctx
    from magi_agent.packs.context import PrimitiveType

    text = (_DOCS / "pack-context-reference.md").read_text()
    for ptype in PrimitiveType:
        assert ptype.value in text, f"missing provides type {ptype.value}"
    for cls in (
        "ToolProvideContext", "CallbackProvideContext", "ValidatorCtx",
        "HarnessProvideContext", "ControlPlaneProvideContext",
        "EvidenceProducerProvideContext", "RecipeProvideContext",
        "ConnectorProvideContext", "ControlPlaneContext", "Capability",
        "BeforeToolCtx", "AfterToolCtx", "BeforeModelCtx", "AfterAgentCtx",
    ):
        assert hasattr(ctx, cls), f"{cls} no longer exists in packs/context.py"
        assert f"`{cls}`" in text, f"pack-context-reference.md missing `{cls}`"
```

Run → FAIL (`FileNotFoundError`).

- [ ] **Step 2: Write the doc.** Create `docs/pack-context-reference.md` (every class/method
below is quoted from `magi_agent/packs/context.py` on this branch):

```markdown
# Typed-Context API Reference (the capability ceiling)

Every primitive impl receives ONLY a narrow, typed context exposing exactly its
type's capabilities. **First-party impls receive the same objects** — there is
no richer first-party handle (capability parity). Source:
`magi_agent/packs/context.py`. Contexts carry a frozen `Capability` token set
that full-trust local does not gate; a hosted build can later restrict it
without changing any impl signature.

## Registration-time provide contexts (one per provides type)

Your manifest's `impl = "module:symbol"` resolves to a callable invoked once at
load time with the matching provide context:

| `provides` type | Context class | What you call |
|---|---|---|
| `tool` | `ToolProvideContext` | `register(tool_manifest)` — a `magi_agent.tools.manifest.ToolManifest`. |
| `callback` | `CallbackProvideContext` | `register(hook_manifest, handler)` — a `magi_agent.hooks.manifest.HookManifest` plus a `HookContext -> HookResult` handler. |
| `evidence_producer` | `EvidenceProducerProvideContext` | `register(ref, spec)` — a `ProducerSpec(evidence_type, public_ref, producer_surfaces)`; `public_ref` must carry a recognized public-ref prefix (`evidence:` / `verifier:` / `receipt:sha256:` / `sha256:`). |
| `recipe` | `RecipeProvideContext` | `register(ref, manifest)` — disk packs normally use a declarative `spec` file instead (no code). |
| `connector` | `ConnectorProvideContext` | `register(ref, spec)` — a `ConnectorSpec(server_ref, tool_manifests, readonly)`. |
| `harness` | `HarnessProvideContext` | `register(ref, pack)` — a `magi_agent.harness.resolved.ResolvedHarnessPack`. |
| `control_plane` | `ControlPlaneProvideContext` | `register(loop_control)` per control you build. Also carries read-only collaborators: `env` (mapping for your own gating), `general_automation_receipts`, `contract_required`, `agent_role`, `self_review_fork_runner`, `self_review_candidate_sink`, `self_review_config`, `self_review_now`, `self_review_scheduler`, `tool_synthesis_model_label`. First-party's bundled controls receive the IDENTICAL object. |
| `validator` | — | validators register declaratively; the impl itself is the invoke-time callable below. |

## Invoke-time contexts

- `ValidatorCtx` — what a `validator` impl receives per evaluation: `ref`, a
  read-only `artifact` mapping, `session` (a `SessionReadView`). Emit with
  `ctx.emit(passed=..., detail=...)` and return `ctx.verdict()` (a
  `ValidatorVerdict(ref, passed, detail)`).
- Control-plane hook contexts (built by the dispatcher per fan-out):
  - `BeforeToolCtx` — `tool_name`, read-only `tool_args`, `session`,
    `evidence`; decide via `ctx.decide("allow" | "deny" | "rewrite", ...)`.
    A deciding before_tool impl must declare `gatePosition = "before"` in its
    manifest or the dispatcher raises `GatePositionViolation`.
  - `AfterToolCtx` — `tool_name`, `tool_args`, `result`; `ctx.override(result)`
    (first non-None override wins).
  - `BeforeModelCtx` — `ctx.reinject(role=..., text=...)` and
    `ctx.clear_tools()` mutate the outgoing model request.
  - `AfterAgentCtx` — observe-only completed turn.
- `ControlPlaneContext` — the shared seam carrier for `control_plane` impls
  (first-party and user receive the identical object): `evidence`
  (`EvidenceLedgerView`), `turn_snapshot` (`TurnSnapshot`), `fork_runner`
  (public ForkRunner capability — full-trust local), `per_invocation`
  (`PerInvocationState`, the only mutable struct: LRU-bounded, cleared on turn
  complete), `compaction` (narrowed compaction-decision capability).

## Read views

- `SessionReadView` — frozen projection: `invocation_id`, `agent_name`,
  `turn_index`, `get_state(key, default)`, `state_keys()`. Never aliases live
  ADK state.
- `EvidenceReadView` — `present`, `owed`, `has(evidence_type)`.

## `Capability` tokens

`read_session`, `read_evidence`, `decide_tool`, `rewrite_tool_args`,
`override_tool_result`, `mutate_model_request`, `reinject_message`,
`clear_tools`, `emit_validation`, `emit_evidence`, `spawn_agent`. Local
full-trust passes the full set; the tokens reserve the hosted-restriction seam.
```

- [ ] **Step 3: Run, see it PASS; commit.**

```bash
MAGI_CONFIG="$(mktemp -d)/config.toml" uv run --no-sync pytest tests/packs/test_authoring_docs_consistency.py -q
git add docs/pack-context-reference.md tests/packs/test_authoring_docs_consistency.py
git commit -m "docs(packs): typed-context API reference (the capability ceiling)"
```

---

## Task B2.3 — `docs/pack-authoring.md` ("write your first pack" walkthrough)

Uses the EXACT `user_cp` shape from
`tests/adk_bridge/test_build_default_plugin_pack_loaded.py::test_disk_user_control_plane_pack_loads_in_parallel_with_first_party`
— the §1 keystone proof a stranger can replicate verbatim.

**Files:**
- Modify: `tests/packs/test_authoring_docs_consistency.py` (add one test)
- New doc: `docs/pack-authoring.md`

- [ ] **Step 1: Add the failing test:**

```python
def test_authoring_walkthrough_uses_the_zero_setup_user_cp_shape() -> None:
    text = (_DOCS / "pack-authoring.md").read_text()
    assert "user_cp.impl:provide" in text
    assert "control_plane:user-extra@1" in text
    assert "magi pack new" in text
```

Run → FAIL.

- [ ] **Step 2: Write the doc.** Create `docs/pack-authoring.md`:

```markdown
# Write Your First Pack

Magi's runtime is neutral: every primitive seam — tool, callback, validator,
harness, control-plane policy, evidence producer, recipe, connector — is
authored through the **same disk-pack mechanism first-party uses**. First-party
ships as bundled packs in the same format and loader; your pack can add a new
primitive, override a first-party ref, or remove a first-party pack entirely.

## The fastest path

    magi pack new validator my-check

scaffolds `<cwd>/.magi/packs/my_check/` with a validated `pack.toml`, an impl
stub receiving only its typed context, and a pytest smoke test. Edit the impl,
run the smoke test, done — the runtime discovers `<cwd>/.magi/packs/` and
`~/.magi/packs/` automatically, and impl imports need **zero env setup** (the
loader auto-resolves your pack's modules; no PYTHONPATH).

`magi pack new <type> <name>` supports all 8 types: `tool`, `callback`,
`validator`, `harness`, `control_plane`, `evidence_producer`, `recipe`,
`connector`.

## By hand: a control-plane pack in three files

This is the exact shape the runtime's own no-privilege keystone test loads in
parallel with first-party. Drop it at `~/.magi/packs/user_cp/` (or
`<cwd>/.magi/packs/user_cp/`):

`pack.toml`:

    packId = "user.control-plane-extra"
    displayName = "user cp extra"
    version = "0.0.1"

    [[provides]]
    type = "control_plane"
    ref = "control_plane:user-extra@1"
    impl = "user_cp.impl:provide"
    gatePosition = "after"

`__init__.py` (empty), and `impl.py`:

    from magi_agent.adk_bridge.control_plane import BaseLoopControl

    class UserParallelControl(BaseLoopControl):
        name = "user.parallel.control"

        async def on_before_model(self, *, callback_context, llm_request):
            return None

    def provide(ctx):
        ctx.register(UserParallelControl())

That's it. On the next run your control registers alongside the bundled
first-party controls — loaded by the identical loader, ordered by the same
manifest `priority`, receiving the identical `ControlPlaneProvideContext`.

## Override and remove

- **Override:** declare the same `(type, ref)` as a first-party entry — the
  last pack in resolved order wins, and your pack dirs (`~/.magi/packs/`,
  `<cwd>/.magi/packs/`) load after the bundled first-party base, so your impl
  replaces first-party's with no special-casing.
- **Remove:** disable any pack (first-party included) in `~/.magi/config.toml`:

      [packs]
      disable = ["openmagi.source-opened"]

## Capability parity

Your impl receives the same narrow typed context first-party receives — see
the Typed-Context API Reference for what each type can do (its capability
ceiling) and the Pack Manifest Reference for the full `pack.toml` schema. A
committed external-shaped example (a custom validator + a custom callback)
lives at `examples/packs/review_guard/` in the repo.
```

- [ ] **Step 3: Run, see it PASS; commit.**

```bash
MAGI_CONFIG="$(mktemp -d)/config.toml" uv run --no-sync pytest tests/packs/test_authoring_docs_consistency.py -q
git add docs/pack-authoring.md tests/packs/test_authoring_docs_consistency.py
git commit -m "docs(packs): write-your-first-pack walkthrough (zero-setup user_cp shape)"
```

---

## Task B2.4 — Register the docs (manifest.json, llms.txt, CLI reference)

**Files:**
- Modify: `docs/manifest.json`, `docs/llms.txt`, `docs/cli/magi.md`
- Modify: `tests/packs/test_authoring_docs_consistency.py` (add one test)

- [ ] **Step 1: Add the failing test:**

```python
def test_docs_manifest_registers_the_three_authoring_pages() -> None:
    import json

    manifest = json.loads((_DOCS / "manifest.json").read_text())
    slugs = {page["slug"] for page in manifest["pages"]}
    for slug in ("pack-authoring", "pack-manifest-reference", "pack-context-reference"):
        assert slug in slugs, f"docs/manifest.json missing page slug {slug!r}"
    assert len(slugs) == len(manifest["pages"]), "duplicate slug in docs/manifest.json"
```

Run → FAIL.

- [ ] **Step 2: Edit `docs/manifest.json`.** Locate the existing `first-party-packs` page
object (grep `"slug": "first-party-packs"` — currently ~`:229–234`, group `"Recipes"`):

```json
    {
      "slug": "first-party-packs",
      "title": "First-party Packs",
      "path": "docs/first-party-packs.md",
      "group": "Recipes"
    },
```

Insert immediately AFTER it (keeping valid JSON commas):

```json
    {
      "slug": "pack-authoring",
      "title": "Write Your First Pack",
      "path": "docs/pack-authoring.md",
      "group": "Developer"
    },
    {
      "slug": "pack-manifest-reference",
      "title": "Pack Manifest Reference",
      "path": "docs/pack-manifest-reference.md",
      "group": "Developer"
    },
    {
      "slug": "pack-context-reference",
      "title": "Typed-Context API Reference",
      "path": "docs/pack-context-reference.md",
      "group": "Developer"
    },
```

- [ ] **Step 3: Append to `docs/llms.txt`** (hand-maintained index; mirror its existing
`- Title: URL - description` line format):

```text
- Write Your First Pack: https://openmagi.ai/docs/pack-authoring - Author any of the 8 primitive types as a disk pack with zero env setup; the same loader/format first-party uses (no privilege).
- Pack Manifest Reference: https://openmagi.ai/docs/pack-manifest-reference - Complete pack.toml schema (PackManifest/ProvidesEntry): packId, displayName, defaultEnabled, [[provides]] type/ref/impl/spec/priority/phase/gatePosition.
- Typed-Context API Reference: https://openmagi.ai/docs/pack-context-reference - The capability ceiling per primitive type: provide contexts, ValidatorCtx, control-plane hook contexts, ControlPlaneContext seams, Capability tokens.
```

- [ ] **Step 4: Document the subcommand in `docs/cli/magi.md`.** Grep the anchor
`` ## `magi doctor` `` (~`:325`) and insert this section immediately BEFORE it:

````markdown
## `magi pack`

`magi pack new <type> <name>` scaffolds a ready-to-load user pack into
`<cwd>/.magi/packs/` (override with `--dest`): a validated `pack.toml`, an impl
stub receiving only its typed context, and a pytest smoke test. `<type>` is one
of `tool`, `callback`, `validator`, `harness`, `control_plane`,
`evidence_producer`, `recipe`, `connector`.

```bash
magi pack new validator my-check
pytest .magi/packs/my_check/test_my_check_pack.py
```

Exit code 2 on an unknown type or an already-existing pack directory. See the
Write Your First Pack guide for the full authoring path.
````

- [ ] **Step 5: Verify** — consistency test green, JSON parses, llms.txt lines present:

```bash
MAGI_CONFIG="$(mktemp -d)/config.toml" uv run --no-sync pytest tests/packs/test_authoring_docs_consistency.py -q
uv run --no-sync python -c "import json; json.load(open('docs/manifest.json')); print('manifest.json OK')"
grep -c "pack-authoring\|pack-manifest-reference\|pack-context-reference" docs/llms.txt docs/manifest.json
```

- [ ] **Step 6: Commit.**

```bash
git add docs/manifest.json docs/llms.txt docs/cli/magi.md tests/packs/test_authoring_docs_consistency.py
git commit -m "docs(packs): register authoring pages (site manifest, llms.txt, CLI reference)"
```

---

## Task B3 — Example third-party pack committed as a template (`examples/packs/review_guard/`)

A real, **external-shaped** working pack (NOT under `magi_agent.*`; impl ref relies on the B0
zero-setup import) with a custom validator + a custom callback, plus a CI smoke test. There is
no `examples/` dir today; `[tool.setuptools.packages.find] include = ["magi_agent*"]` means
nothing under `examples/` ships in the wheel (verified in `pyproject.toml`).

**Files:**
- New: `examples/packs/review_guard/__init__.py` (empty), `pack.toml`, `impl.py`
- New test: `tests/examples/__init__.py` (empty), `tests/examples/test_review_guard_pack.py`

- [ ] **Step 1: Write the failing smoke test.**

```python
# tests/examples/test_review_guard_pack.py
"""Pack B3 — the committed external-shaped example pack loads end-to-end.

Proves the full stranger path: a pack outside magi_agent.*, impl module path
relative to the pack dir ("review_guard.impl:..."), loaded with zero sys.path
setup, projecting a custom validator + a custom callback into the live
registries with no first-party privilege.
"""
from __future__ import annotations

import sys
from pathlib import Path

from magi_agent.packs.context import SessionReadView, ValidatorCtx
from magi_agent.packs.loader import RecordingSink, load_from_bases
from magi_agent.packs.registries import PackRegistries, project_into_registries

_EXAMPLES_BASE = Path(__file__).resolve().parents[2] / "examples" / "packs"
_VALIDATOR_REF = "verifier:noTodoLeft@1"
_CALLBACK_REF = "review-guard-audit"


def test_example_pack_loads_and_projects(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MAGI_CONFIG", str(tmp_path / "config.toml"))
    monkeypatch.setattr(sys, "path", [*sys.path])  # revert loader auto-injection

    result, catalog = load_from_bases([_EXAMPLES_BASE], RecordingSink())
    refs = {(p.type, p.ref) for p in result.primitives}
    assert ("validator", _VALIDATOR_REF) in refs
    assert ("callback", _CALLBACK_REF) in refs
    assert _VALIDATOR_REF in catalog.validator_refs
    assert _CALLBACK_REF in catalog.plugin_refs

    registries = PackRegistries()
    report = project_into_registries(result.primitives, registries)
    assert _CALLBACK_REF in report.registered
    assert registries.hooks_handler(_CALLBACK_REF) is not None


def test_example_validator_passes_and_fails(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MAGI_CONFIG", str(tmp_path / "config.toml"))
    monkeypatch.setattr(sys, "path", [*sys.path])

    result, _catalog = load_from_bases([_EXAMPLES_BASE], RecordingSink())
    impl = next(p.impl for p in result.primitives if p.ref == _VALIDATOR_REF)
    session = SessionReadView(invocation_id="i", agent_name="a", turn_index=0)
    ok = impl(ValidatorCtx(ref=_VALIDATOR_REF,
                           artifact={"summary": "all clean"}, session=session))
    bad = impl(ValidatorCtx(ref=_VALIDATOR_REF,
                            artifact={"summary": "TODO: finish this"}, session=session))
    assert ok.passed is True
    assert bad.passed is False and bad.detail is not None
```

Run → FAIL (base dir does not exist → zero primitives → first assert fails):

```bash
MAGI_CONFIG="$(mktemp -d)/config.toml" uv run --no-sync pytest tests/examples/test_review_guard_pack.py -q
```

- [ ] **Step 2: Create the pack.** `examples/packs/review_guard/pack.toml`:

```toml
# Example third-party pack — the shape a stranger ships. External: NOT under
# magi_agent.*; the impl module path is relative to this pack's directory and
# resolves with ZERO env setup (the loader auto-injects the pack root).
# Manifest schema = magi_agent/packs/manifest.py (top-level packId/displayName).

packId = "examples.review-guard"
displayName = "Review guard (example third-party pack)"
version = "0.1.0"
description = "External-shaped example: a custom validator + a custom callback."

[[provides]]
type = "validator"
ref = "verifier:noTodoLeft@1"
impl = "review_guard.impl:no_todo_validator"

[[provides]]
type = "callback"
ref = "review-guard-audit"
impl = "review_guard.impl:provide_audit_callback"
priority = 100
phase = "beforeTurnStart"
```

`examples/packs/review_guard/__init__.py`: empty file.

`examples/packs/review_guard/impl.py`:

```python
"""Example third-party pack impls: a custom validator + a custom callback.

Both receive ONLY their typed contexts (magi_agent/packs/context.py) —
identical capability to first-party (no privilege). Copy this directory as a
starting template, or scaffold fresh shapes with `magi pack new <type> <name>`.
"""
from __future__ import annotations

from magi_agent.hooks.context import HookContext
from magi_agent.hooks.manifest import HookManifest, HookPoint
from magi_agent.hooks.result import HookResult
from magi_agent.packs.context import (
    CallbackProvideContext,
    ValidatorCtx,
    ValidatorVerdict,
)
from magi_agent.tools.manifest import ToolSource


def no_todo_validator(ctx: ValidatorCtx) -> ValidatorVerdict | None:
    """Deterministic check over the produced artifact: no TODO marker left."""
    summary = str(ctx.artifact.get("summary") or "")
    passed = "TODO" not in summary
    ctx.emit(passed=passed, detail=None if passed else "summary still contains TODO")
    return ctx.verdict()


def _audit_handler(context: HookContext) -> HookResult:
    """Pure-observe, non-blocking audit; never alters the turn outcome."""
    return HookResult(action="continue", reason="review-guard observed turn start")


def provide_audit_callback(context: CallbackProvideContext) -> None:
    context.register(
        HookManifest(
            name="review-guard-audit",
            point=HookPoint.BEFORE_TURN_START,
            description="Non-blocking audit marker at turn start (example).",
            source=ToolSource(kind="external", package="examples.review-guard"),
            priority=100,
            blocking=False,
            opt_out=True,
        ),
        _audit_handler,
    )
```

Also create the empty `tests/examples/__init__.py` (mirrors `tests/packs/__init__.py`).

- [ ] **Step 3: Run, see it PASS; lint.**

```bash
MAGI_CONFIG="$(mktemp -d)/config.toml" uv run --no-sync pytest tests/examples/test_review_guard_pack.py -q
uv run --no-sync ruff check examples/ tests/examples/
```

- [ ] **Step 4: Commit.**

```bash
git add examples/packs/review_guard tests/examples
git commit -m "feat(examples): external-shaped review-guard pack (validator + callback) as authoring template"
```

---

## Final verification (whole-phase barrier)

- [ ] Full suite + golden + lint + types, exactly as CI runs them:

```bash
cd /Users/kevin/Desktop/claude_code/magi-agent/.worktrees/neutral-runtime
MAGI_CONFIG="$(mktemp -d)/config.toml" uv run --no-sync pytest -q
MAGI_CONFIG="$(mktemp -d)/config.toml" uv run --no-sync pytest tests/fixtures/neutral_runtime_golden/test_golden_regression.py -q
uv run --no-sync ruff check .
uv run --no-sync mypy magi_agent
uv run --no-sync python scripts/check_xfail_budget.py
uv run --no-sync python scripts/check_flag_reads.py
```

Expected: all green, zero golden diff. (Two known import-boundary tests —
`model_tiers`, `pr18` — can fail on this machine even on pristine main due to local
socket/subprocess loading; compare against a pristine-main run before attributing them to
this phase.)

---

## Acceptance criteria

1. **B0 (DX gap closed):** a pack dropped at `<root>/user_cp` with
   `impl = "user_cp.impl:provide"` loads via `loader.load_from_bases` with NO
   `PYTHONPATH`/`sys.path` setup (`tests/packs/test_loader_zero_setup_import.py` green), and
   the §1 keystone disk-pack test passes **without** `monkeypatch.syspath_prepend`. The
   Phase-0 golden regression shows zero diff.
2. **B1:** `magi pack new validator my-check` (real console script) produces a pack whose
   generated smoke test passes; the engine covers all 8 provides types
   (`tests/packs/test_scaffold.py` parametrized green); unknown type / existing dir exit 2.
3. **B2:** `docs/pack-authoring.md`, `docs/pack-manifest-reference.md`,
   `docs/pack-context-reference.md` exist, are registered in `docs/manifest.json` +
   `docs/llms.txt` + `docs/cli/magi.md`, and the consistency test pins every documented
   field/class to the real shipped models (`tests/packs/test_authoring_docs_consistency.py`).
4. **B3:** `examples/packs/review_guard/` is committed, external-shaped (impl ref
   `review_guard.impl:*`, zero-setup import), provides a working custom validator + custom
   callback, and its CI smoke test (`tests/examples/test_review_guard_pack.py`) is green.
5. Full suite + ruff + mypy + golden regression green; no change to any control-plane
   behavior, registry semantics, or catalog contents for the bundled packs.

## Rollback

Every task is one commit and additive:

- B0.1+B0.2 revert together (`git revert <B0.2> <B0.1>`) — restores the
  syspath-required behavior; the fallback never altered the happy path (plain import is
  always attempted first), so no other task's runtime behavior depends on rollback order
  except the generated-pack/example import path (B1/B3 tests would then need
  `syspath_prepend` again — i.e. revert B1/B3 too if reverting B0).
- B1.1/B1.2, each B2.x, and B3 revert independently (`git revert <sha>`); docs pages can also
  be unregistered by reverting only B2.4.
- No migration, no config change, no deployment surface: everything is repo-local.

## Hand-off

- **B4/B5 (clawy hosted):** assessment-first tasks against the shipped pack surface
  (recipe-making bot skill; hosted Customize tab) — separate workstream, **Kevin checkpoint
  before rollout** (real bots/users; mass-bot-patch care). Not started here by design.
- **Pack C (full microkernel):** proceeds per `08-ROADMAP-post-p6.md` §Pack C; B1's scaffolder
  and B2's references should be extended as new provides sub-types appear (e.g. loop-policy).
- **v1.1 ideas surfaced while authoring (not in scope):** `magi pack list` (render discovered
  packs + enable/disable state from `resolve_enabled_packs`), `magi pack validate <dir>`
  (manifest parse + dry-run lazy import), entry-points discovery as the D1 secondary source,
  and a uniqueness lint for pack directory names across roots (the `sys.modules` caveat
  documented in B0/scaffold docstrings).
