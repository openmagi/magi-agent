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
"""User tool provider — registers a ToolManifest AND a runnable inline handler.

The inline handler ``(args, ctx) -> output`` receives the narrow ToolCtx (read
args + session + a progress sink); it needs NO WorkspaceHostView. Replace the
body with your own logic (call an API, compute something) and return a dict.
A tool that reads/writes workspace files instead can author the gate5b workspace
seam via ``context.register_workspace_handler(name, handler)`` whose handler is
``(args, WorkspaceHostView) -> output``.
"""
from __future__ import annotations

from collections.abc import Mapping

from magi_agent.packs.context import ToolCtx, ToolProvideContext
from magi_agent.tools.catalog import CORE_TOOL_INPUT_SCHEMA
from magi_agent.tools.manifest import Budget, ToolManifest, ToolSource


def handler(args: Mapping[str, object], ctx: ToolCtx) -> dict[str, object]:
    """Echo the ``text`` argument back. Replace with your own logic."""
    return {"echoed": str(args.get("text", "")), "tool": ctx.tool_name}


def provide(context: ToolProvideContext) -> None:
    manifest = ToolManifest(
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
    if context.register_handler is not None:
        context.register_handler(manifest, handler)
    else:  # pragma: no cover - projector predates the inline-handler seam
        context.register(manifest)
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

# Code-computed recipe-as-code impl (PR4). The callable is invoked ONCE at
# registration (NOT during a turn) and must be idempotent + side-effect free; it
# returns a dict (or RecipePackManifest). An UNTRUSTED pack must use an ``ext.``
# packId and must NOT set hardSafety/ownership/defaultEnabled (compose-only trust
# boundary). Activation requires MAGI_RECIPE_AS_CODE_ENABLED (default-OFF).
_RECIPE_CODE_TEMPLATE = '''\
"""User code recipe — computes its RecipePackManifest with YOUR OWN code.

``provide_recipe`` is invoked once at registration (never during a turn): keep it
idempotent and side-effect free. Return a dict (or RecipePackManifest). Untrusted
packs must use an ``ext.`` packId and stay non-hard-safety / non-default-enabled.
"""
from __future__ import annotations


def provide_recipe() -> dict:
    return {
        "packId": __PACK_ID__,
        "version": "1",
        "displayName": __DISPLAY__,
        "description": "User-authored code-computed recipe.",
        # FileWrite is a canonical known-ref (CompileRecipePackCatalog.default), so
        # this recipe passes R2 ref-closure. Untrusted recipes may only reference
        # refs that exist in the trusted runtime (catalog + first-party recipes).
        "toolRefs": ["FileWrite"],
    }
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

_SMOKE_CODE_RECIPE = '''\
"""Smoke test scaffolded by `magi pack new --code recipe`. A code recipe is
activation-gated: the loader drops it (and never imports the callable) unless
MAGI_RECIPE_AS_CODE_ENABLED is set. Run: pytest <this file>."""
from __future__ import annotations

from pathlib import Path

from magi_agent.packs.loader import RecordingSink, load_from_bases
from magi_agent.packs.registries import PackRegistries, project_into_registries

PACKS_BASE = Path(__file__).resolve().parent.parent
PTYPE = __PTYPE__
REF = __REF__


def test_code_recipe_off_is_dropped(monkeypatch) -> None:
    monkeypatch.delenv("MAGI_RECIPE_AS_CODE_ENABLED", raising=False)
    result, _catalog = load_from_bases([PACKS_BASE], RecordingSink())
    assert (PTYPE, REF) not in {(p.type, p.ref) for p in result.primitives}


def test_code_recipe_registers_when_flag_on(monkeypatch) -> None:
    monkeypatch.setenv("MAGI_RECIPE_AS_CODE_ENABLED", "1")
    result, _catalog = load_from_bases([PACKS_BASE], RecordingSink())
    report = project_into_registries(result.primitives, PackRegistries())
    assert REF in report.registered
'''

_SMOKE_TOOL_DISPATCH = '''\


def test_tool_inline_handler_runs() -> None:
    from magi_agent.packs.registries import PackRegistries, project_into_registries

    result, _catalog = load_from_bases([PACKS_BASE], RecordingSink())
    registries = PackRegistries()
    project_into_registries(result.primitives, registries)
    handler = registries.tool_inline_handlers.resolve(REF)
    assert handler is not None, "scaffolded tool bound no inline handler"
    output = handler({"text": "hi"}, _StubToolCtx())
    assert isinstance(output, dict)


class _StubToolCtx:
    tool_name = REF
'''

# Types whose generated smoke test exercises project_into_registries.
_PROJECTION_TYPES: frozenset[str] = frozenset(
    {"tool", "callback", "evidence_producer", "recipe", "connector", "harness"}
)


def _manifest_toml(
    ptype: str, ref: str, pack_id: str, display: str, mod: str, *, code: bool = False
) -> str:
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
        # Code-computed recipe-as-code (PR4) vs declarative spec TOML.
        if code:
            lines.append(f'spec_callable = "{mod}.impl:provide_recipe"')
        else:
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


def scaffold_pack(
    ptype: str, name: str, dest_root: Path, *, code: bool = False
) -> ScaffoldResult:
    """Write a loadable user pack for ``ptype`` under ``dest_root/<module_name>``.

    ``code`` (recipe only): emit a code-computed recipe-as-code variant (PR4) — a
    ``provide_recipe()`` callable + ``spec_callable`` in pack.toml — instead of a
    declarative ``recipe.toml`` spec. The scaffold uses an ``ext.`` packId so the
    untrusted-pack trust boundary admits it; activation needs default-OFF
    ``MAGI_RECIPE_AS_CODE_ENABLED``. Ignored for non-recipe types.
    """
    if ptype not in PACK_TYPES:
        raise ValueError(
            f"unknown pack type {ptype!r}; expected one of: {', '.join(PACK_TYPES)}"
        )
    if code and ptype != "recipe":
        raise ValueError("code=True is only valid for the 'recipe' pack type")
    mod = _module_name(name)
    pack_dir = dest_root / mod
    if pack_dir.exists():
        raise ValueError(f"pack dir already exists: {pack_dir}")
    ref = default_ref(ptype, name)
    # A code recipe registers through the external trust boundary, which requires
    # an ``ext.`` namespace (R1); declarative + other types keep the user id.
    pack_id = (
        f"ext.user.{mod.replace('_', '-')}"
        if code
        else f"user.{mod.replace('_', '-')}"
    )
    camel = _camel(name)
    pascal = camel[:1].upper() + camel[1:]

    pack_dir.mkdir(parents=True)
    (pack_dir / "__init__.py").write_text("")
    pack_toml = pack_dir / "pack.toml"
    pack_toml.write_text(_manifest_toml(ptype, ref, pack_id, name, mod, code=code))

    impl_path: Path | None = None
    spec_path: Path | None = None
    if ptype == "recipe" and code:
        impl_path = pack_dir / "impl.py"
        impl_path.write_text(
            _render(_RECIPE_CODE_TEMPLATE, PACK_ID=repr(pack_id), DISPLAY=repr(name))
        )
    elif ptype == "recipe":
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

    if ptype == "recipe" and code:
        # A code recipe is activation-gated: the loader drops it when
        # MAGI_RECIPE_AS_CODE_ENABLED is OFF, so the default load/projection smoke
        # would (correctly) see nothing. Emit a flag-ON smoke test instead.
        smoke = _render(_SMOKE_CODE_RECIPE, PTYPE=repr(ptype), REF=repr(ref))
    else:
        smoke = _render(_SMOKE_HEADER, PTYPE=repr(ptype), REF=repr(ref))
        if ptype in _PROJECTION_TYPES:
            smoke += _SMOKE_PROJECTION
            if ptype == "tool":
                smoke += _SMOKE_TOOL_DISPATCH
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
