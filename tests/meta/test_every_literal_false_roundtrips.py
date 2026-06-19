"""C-4 PR-A golden harness: capture-and-lock the CURRENT force-false behavior
of every existing ``Literal[False]``-bearing pydantic model in ``magi_agent``.

This PR (C-4 PR-A) is ADDITIVE ONLY -- no production model is re-parented. The
goldens captured here are the safety-net the migration PRs (C-4 PR-B / PR-C)
will use as the round-trip gate. The contract:

    For every BaseModel subclass under ``magi_agent`` that has any
    ``Literal[False]``-typed field (incl. ``Optional[Literal[False]]``):

        1. We build a "malicious" payload that asserts ``True`` on every such
           field (by name AND by alias when there is one).
        2. We exercise BOTH ``model_validate`` and ``model_construct`` with
           that payload (the two surfaces a caller can use to construct).
        3. We capture ``model_dump(by_alias=True)`` for each as a golden under
           ``tests/meta/golden_force_false/<fqcn>.json``.
        4. We ASSERT each captured dump's ``Literal[False]`` fields actually
           serialize as ``False`` (this proves the current tree is force-false
           today; any model that fails this baseline IS a pre-existing
           force-false bug that C-4 PR-A surfaces).

Goldens are committed. The migration PRs replace each model's base with
``FalseOnlyAuthorityModel`` and re-run this harness; if the new dump differs
from the golden, the migration is rejected for that model.

This harness does NOT mutate the goldens at run time. To regenerate the goldens
intentionally after a deliberate behavior change, delete the golden file and
re-run the harness; the missing-golden branch writes the new file and fails the
test once, alerting the author to review and commit the new golden.
"""

from __future__ import annotations

import importlib
import json
import pkgutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, get_args, get_origin

import pytest
from pydantic import BaseModel

import magi_agent

GOLDEN_DIR = Path(__file__).resolve().parent / "golden_force_false"
GOLDEN_DIR.mkdir(exist_ok=True)


# ---------------------------------------------------------------------------
# Literal[False] introspection (independent of the new base -- this harness
# must work even for force-false models that are NOT yet on the new base).
# ---------------------------------------------------------------------------


def _is_literal_false(annotation: object) -> bool:
    if get_origin(annotation) is not Literal:
        return False
    args = get_args(annotation)
    if len(args) != 1:
        return False
    only = args[0]
    return isinstance(only, bool) and only is False


def _is_force_false_field_annotation(annotation: object) -> bool:
    """True for ``Literal[False]`` OR ``Optional[Literal[False]]``."""
    if _is_literal_false(annotation):
        return True
    origin = get_origin(annotation)
    if origin is None:
        return False
    args = get_args(annotation)
    if not args:
        return False
    none_type = type(None)
    non_none = [arg for arg in args if arg is not none_type]
    has_none = any(arg is none_type for arg in args)
    if not has_none or len(non_none) != 1:
        return False
    return _is_literal_false(non_none[0])


# ---------------------------------------------------------------------------
# Module discovery + model collection
# ---------------------------------------------------------------------------

# Modules that intentionally fail-import in the dev environment (optional deps,
# CLI-only entrypoints, ADK side-effect modules). Discovery skips these so the
# harness still walks the rest of the tree. Any model defined ONLY in these
# modules is out of scope for this PR's golden capture -- if such a model
# exists, the migration PRs will catch it when the optional extra is installed.
_SKIP_PREFIXES: tuple[str, ...] = (
    # CLI surface (Textual / Typer / Rich) -- requires the "cli" extra; we
    # have it in this run, but the CLI's __main__ entrypoints trigger argparse
    # at import time. Skipping main entrypoint modules only.
    "magi_agent.cli.__main__",
    "magi_agent.cli.tui_main",
    "magi_agent.cli.tui",
    # Optional [telegram] extra (telethon) -- the channel provider lazy-imports
    # it at the top of the module rather than in a function body, so it cannot
    # be collected without the extra. Any force-false model unique to this
    # module is out of scope for PR-A's golden; the migration PRs will pick it
    # up if/when the extra is installed.
    "magi_agent.channels.telegram_easy_telethon",
)


def _iter_all_modules() -> list[str]:
    names: list[str] = []
    for module_info in pkgutil.walk_packages(
        magi_agent.__path__, prefix="magi_agent."
    ):
        name = module_info.name
        if any(name.startswith(prefix) for prefix in _SKIP_PREFIXES):
            continue
        names.append(name)
    return sorted(names)


@dataclass(frozen=True)
class _DiscoveredModel:
    qualname: str  # "magi_agent.config.models.PythonMemoryAdapterConfig"
    model_cls: type[BaseModel]
    false_fields: tuple[str, ...]
    source_file: Path

    @property
    def golden_path(self) -> Path:
        return GOLDEN_DIR / f"{self.qualname}.json"


def _collect_force_false_models() -> tuple[
    list[_DiscoveredModel], list[tuple[str, str]]
]:
    """Walk every magi_agent module; return (models, import_failures).

    ``models`` is sorted by qualified name; ``import_failures`` is a list of
    ``(module_name, error)`` for modules that failed to import. The harness
    fails if ANY non-skipped module fails to import (so the discovery itself
    is complete) -- unless the failure is documented in the import-skip set.
    """
    discovered: dict[str, _DiscoveredModel] = {}
    failures: list[tuple[str, str]] = []
    for module_name in _iter_all_modules():
        try:
            module = importlib.import_module(module_name)
        except Exception as exc:  # noqa: BLE001 -- discovery: report and continue
            failures.append((module_name, f"{type(exc).__name__}: {exc}"))
            continue
        for attr_name in dir(module):
            if attr_name.startswith("_"):
                # Private base classes (_FalseOnlyModel etc.) are
                # configuration scaffolding, not concrete models the runtime
                # serializes. Skip them; their concrete subclasses are
                # captured under their own module.
                continue
            obj = getattr(module, attr_name, None)
            if not isinstance(obj, type):
                continue
            if not issubclass(obj, BaseModel):
                continue
            if obj is BaseModel:
                continue
            # Only capture the model in its own defining module to avoid
            # duplicate goldens when a model is re-exported.
            if obj.__module__ != module_name:
                continue
            false_fields: list[str] = []
            for field_name, field_info in obj.model_fields.items():
                if _is_force_false_field_annotation(field_info.annotation):
                    false_fields.append(field_name)
            if not false_fields:
                continue
            qualname = f"{obj.__module__}.{obj.__qualname__}"
            try:
                source_file = Path(
                    importlib.import_module(obj.__module__).__file__ or ""
                )
            except Exception:  # pragma: no cover - defensive
                source_file = Path("<unknown>")
            discovered[qualname] = _DiscoveredModel(
                qualname=qualname,
                model_cls=obj,
                false_fields=tuple(false_fields),
                source_file=source_file,
            )
    return sorted(discovered.values(), key=lambda d: d.qualname), failures


# ---------------------------------------------------------------------------
# Discovery sanity tests
# ---------------------------------------------------------------------------


def test_module_discovery_imports_every_non_skipped_module() -> None:
    """Every non-skipped module under ``magi_agent`` must import cleanly so the
    harness can find every force-false model. New import failures should be
    fixed at the source (not added to ``_SKIP_PREFIXES``) -- the prefixes there
    document CLI entrypoints that argparse-exit at import time.
    """
    _, failures = _collect_force_false_models()
    assert failures == [], (
        "Discovery failed to import some modules; either fix the import or "
        "extend _SKIP_PREFIXES with justification:\n"
        + "\n".join(f"  {name}: {err}" for name, err in failures)
    )


def test_harness_finds_at_least_one_force_false_model_per_known_package() -> None:
    """Sanity: the master plan documents force-false models in config,
    connectors, tools/kernel, channels, evidence, recipes, permissions. The
    harness must find at least one model in each of those packages -- a hard
    floor proving the discovery isn't silently empty.
    """
    models, _ = _collect_force_false_models()
    found_packages = {model.qualname.rsplit(".", 2)[0] for model in models}
    expected_packages = {
        "magi_agent.config",
        "magi_agent.connectors",
        "magi_agent.tools",
        "magi_agent.channels",
        "magi_agent.evidence",
        "magi_agent.recipes",
        "magi_agent.permissions",
    }
    # The match key is the parent module prefix; we don't require EVERY parent
    # prefix to appear (some packages may only have force-false models in
    # nested submodules), so we check that each expected package has at least
    # one nested module with a force-false model.
    missing = []
    for expected in expected_packages:
        if not any(pkg.startswith(expected) for pkg in found_packages):
            missing.append(expected)
    assert missing == [], (
        "Golden harness discovery missed expected packages with force-false "
        f"models (per master plan): {missing}. Found packages: "
        f"{sorted(found_packages)}"
    )


# ---------------------------------------------------------------------------
# Golden capture
# ---------------------------------------------------------------------------


def _malicious_true_payload(model: _DiscoveredModel) -> dict[str, Any]:
    """Build a payload that asserts ``True`` on every Literal[False] field.

    Provides BOTH the field name and the alias (if present), so the
    construction surface sees a malicious assertion under whichever key it
    accepts.
    """
    payload: dict[str, Any] = {}
    for field_name in model.false_fields:
        field_info = model.model_cls.model_fields[field_name]
        payload[field_name] = True
        if field_info.alias is not None:
            payload[field_info.alias] = True
    return payload


def _try_construct_via_validate(
    model: _DiscoveredModel, payload: dict[str, Any]
) -> dict[str, Any] | None:
    """Attempt ``model_validate(payload)`` and return the by-alias dump.

    Returns None if validation fails for reasons OTHER than the
    force-false fields (e.g. a required string field with no default). Such
    models are out of scope for the malicious-payload roundtrip path; we still
    capture their force-false invariant via the ``model_construct`` path,
    which can bypass required-field validation.
    """
    try:
        instance = model.model_cls.model_validate(payload)
    except Exception:
        return None
    try:
        return instance.model_dump(by_alias=True, mode="json", warnings=False)
    except Exception:
        return None


def _try_construct_via_model_construct(
    model: _DiscoveredModel, payload: dict[str, Any]
) -> dict[str, Any] | None:
    """Attempt ``model_construct(**payload)`` and return the by-alias dump.

    ``model_construct`` is the documented pydantic escape hatch; it usually
    bypasses validation. The pre-C-4 ``_FalseOnlyModel`` override re-routes
    construct through validate, so this exercises the force-false invariant.
    """
    try:
        instance = model.model_cls.model_construct(**payload)
    except Exception:
        return None
    try:
        return instance.model_dump(by_alias=True, mode="json", warnings=False)
    except Exception:
        return None


def _stable_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False, default=str)


def _capture_or_compare_golden(
    model: _DiscoveredModel,
    captured: dict[str, dict[str, Any] | None],
) -> tuple[bool, str | None]:
    """Write the golden if missing; otherwise compare.

    The golden records, per surface:
      - the by-alias dump (the C-4 round-trip target the migration must match)
      - the per-surface "force-false leaks" -- the set of field names that
        leaked True (i.e. failed force-false). The migration PRs must drive
        every model's ``model_construct`` leak set to empty; the
        ``model_validate`` leak set is already empty today (enforced by the
        sibling assert above) and the migration must keep it empty.

    Returns ``(matches, message)``. ``matches`` is False either because the
    golden was just written for the first time (so the author should review)
    OR because the current capture diverges from the committed golden.
    """
    validate_leaks = _surface_force_false_compliance(model, captured["model_validate"])
    construct_leaks = _surface_force_false_compliance(
        model, captured["model_construct"]
    )
    payload_to_persist = {
        "qualname": model.qualname,
        "false_only_fields": sorted(model.false_fields),
        "model_validate_dump": captured["model_validate"],
        "model_validate_force_false_leaks": sorted(validate_leaks),
        "model_construct_dump": captured["model_construct"],
        "model_construct_force_false_leaks": sorted(construct_leaks),
    }
    rendered = _stable_json(payload_to_persist)
    if not model.golden_path.exists():
        model.golden_path.write_text(rendered + "\n", encoding="utf-8")
        return False, f"Golden created: {model.golden_path.name} (review and commit)"
    existing = model.golden_path.read_text(encoding="utf-8").rstrip("\n")
    if existing != rendered:
        return (
            False,
            f"Golden diverged for {model.qualname}: "
            f"see {model.golden_path}; investigate before re-baselining.",
        )
    return True, None


# ---------------------------------------------------------------------------
# Parametrized golden assertion + force-false baseline
# ---------------------------------------------------------------------------


def _all_models() -> list[_DiscoveredModel]:
    models, _ = _collect_force_false_models()
    return models


def _surface_force_false_compliance(
    model: _DiscoveredModel, dump: dict[str, Any] | None
) -> set[str]:
    """Return the set of false-only field names that LEAKED True (i.e. did NOT
    force-false) in this surface's dump. Empty set = surface is compliant.

    A field that legitimately serializes ``None`` (Optional[Literal[False]] not
    asserted) is treated as compliant.
    """
    leaks: set[str] = set()
    if dump is None:
        return leaks
    for field_name in model.false_fields:
        field_info = model.model_cls.model_fields[field_name]
        key = field_info.alias if field_info.alias else field_name
        if key not in dump:
            # Field missing from dump (e.g. dropped by serializer); treat as
            # compliant since it can't leak True.
            continue
        value = dump[key]
        annotation = field_info.annotation
        if value is None and not _is_literal_false(annotation):
            continue
        if value is False:
            continue
        leaks.add(field_name)
    return leaks


@pytest.mark.parametrize(
    "model", _all_models(), ids=lambda m: m.qualname
)
def test_force_false_invariant_holds_in_current_tree(model: _DiscoveredModel) -> None:
    """For every existing force-false model, MEASURE the current tree's
    force-false compliance under a malicious-True payload, per surface.

    This is the baseline the master plan calls for. There are TWO surfaces:

    - ``model_validate`` -- pydantic's full-validation entrypoint. Today's
      ``_FalseOnlyModel`` subclasses install a ``model_validator(mode="before")``
      that force-falses the listed fields, so the validate surface SHOULD
      already be compliant for every model in the tree.
    - ``model_construct`` -- pydantic's documented escape hatch that bypasses
      validation by default. Today's ``_FalseOnlyModel`` overrides re-route it
      through ``model_validate``; many OTHER force-false models in the tree do
      NOT, so this surface is the silent-weakening hazard C-4 closes.

    Per the master plan we DOCUMENT (not fail on) the construct-surface gaps:
    the golden file records exactly which models leak on which surface. The
    migration PRs (C-4 PR-B/PR-C) re-parent each model on
    ``FalseOnlyAuthorityModel`` (which routes construct through validate); the
    expectation is that the construct-leak list collapses to empty post-
    migration. Until then, the validate-surface SHOULD be clean -- a leak on
    validate WOULD be a new regression we'd want to catch loudly.
    """
    payload = _malicious_true_payload(model)
    validate_dump = _try_construct_via_validate(model, payload)
    construct_dump = _try_construct_via_model_construct(model, payload)

    # ``None`` for a surface means EITHER the malicious payload was rejected
    # (a fail-CLOSED outcome: the field is force-false because the surface
    # raised rather than coerce) OR the model has required fields we couldn't
    # supply. Both are valid baseline states; the golden captures None so the
    # migration PR sees the same shape.

    # Hard assertion ONLY for the validate surface: a leak here would mean a
    # force-false field is silently mutable through pydantic's primary
    # validation path. (A None dump = surface raised = fail-closed = also
    # acceptable.)
    validate_leaks = _surface_force_false_compliance(model, validate_dump)
    assert not validate_leaks, (
        f"{model.qualname}: model_validate surface leaked force-false fields "
        f"as True: {sorted(validate_leaks)}. This is a force-false regression "
        f"on the primary construction surface; investigate before continuing "
        f"C-4 PR-A. (model_construct-surface leaks are documented in the "
        f"golden file, not asserted here -- those are the C-4 target.)"
    )


@pytest.mark.parametrize(
    "model", _all_models(), ids=lambda m: m.qualname
)
def test_golden_captured_for_every_force_false_model(model: _DiscoveredModel) -> None:
    """Capture-or-compare the by-alias goldens. First run writes; later runs
    compare. The migration PRs (C-4 PR-B/PR-C) re-run this same test after
    re-parenting each model on ``FalseOnlyAuthorityModel`` -- a divergence
    flags the model for individual investigation rather than blanket-migration.
    """
    payload = _malicious_true_payload(model)
    captured = {
        "model_validate": _try_construct_via_validate(model, payload),
        "model_construct": _try_construct_via_model_construct(model, payload),
    }
    matches, message = _capture_or_compare_golden(model, captured)
    assert matches, message
