"""Guard against shipping a wheel that omits required package data.

Source-tree tests cannot catch a missing-from-wheel bug (the data file exists on
disk during the test run), so we assert that every non-Python data file loaded
via ``importlib.resources`` is declared in ``[tool.setuptools.package-data]``.
The bundled slash-command templates (``cli/commands/templates/*.txt``) were
omitted once, which crashed the TUI on launch with ``FileNotFoundError``.
"""

from __future__ import annotations

import fnmatch
import pathlib
import tomllib

_ROOT = pathlib.Path(__file__).resolve().parents[1]


def _package_data_globs() -> list[str]:
    data = tomllib.loads((_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return list(data["tool"]["setuptools"]["package-data"]["magi_agent"])


def test_bundled_command_templates_are_packaged() -> None:
    template_dir = _ROOT / "magi_agent" / "cli" / "commands" / "templates"
    txts = sorted(p.name for p in template_dir.glob("*.txt"))
    assert txts, "expected bundled command templates on disk"

    globs = _package_data_globs()
    assert any(
        g.startswith("cli/commands/templates/") and g.endswith(".txt") for g in globs
    ), (
        "cli/commands/templates/*.txt missing from [tool.setuptools.package-data]; "
        f"bundled.py loads {txts} via importlib.resources and the wheel must ship them"
    )


def test_bundled_templates_load_via_resources() -> None:
    # The actual call path bundled.py uses at import time.
    from importlib import resources

    pkg = resources.files("magi_agent.cli.commands") / "templates"
    for name in ("initialize.txt", "review.txt"):
        assert (pkg / name).is_file(), f"{name} not loadable via importlib.resources"


def _packages_find_config() -> dict[str, list[str]]:
    data = tomllib.loads((_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return data["tool"]["setuptools"]["packages"]["find"]


# Benchmark *subpackages* with zero runtime (non-test) consumers. They exist in
# the repo for offline harness runs but must NOT bloat the published wheel.
# (``coding_eval`` / ``legal_eval`` are single modules inside the
# ``magi_agent.benchmarks`` package, not subpackages, so they cannot be dropped
# via ``packages.find`` exclude without also dropping the runtime legal path —
# they ship with the parent package, which is intentional.)
_BENCHMARK_DIET_PACKAGES = (
    "magi_agent.benchmarks.gaia",
    "magi_agent.benchmarks.taubench",
    "magi_agent.benchmarks.multibug",
)

# Benchmark subpackages imported at runtime (legal-eval CLI + first_party legal
# recipe) — these MUST stay in the wheel or `magi legal-eval` breaks on install.
# The ``magi_agent.benchmarks`` parent package also carries the ``legal_eval``
# module that ``cli/app.py`` imports for the ``magi legal-eval`` command.
_BENCHMARK_RUNTIME_PACKAGES = (
    "magi_agent.benchmarks",
    "magi_agent.benchmarks.legalbench",
)


def _on_disk_packages() -> set[str]:
    """Dotted names of every importable package directory under the repo root.

    A package dir is any directory containing ``__init__.py`` (the same rule
    setuptools' ``find_packages`` applies). We only need the ``magi_agent`` tree
    for these assertions.
    """
    packages: set[str] = set()
    for init in (_ROOT / "magi_agent").rglob("__init__.py"):
        rel = init.parent.relative_to(_ROOT)
        packages.add(".".join(rel.parts))
    return packages


def _discovered_packages() -> set[str]:
    """Replicate setuptools find-packages include/exclude over on-disk packages.

    Mirrors ``[tool.setuptools.packages.find]``: keep packages matching an
    ``include`` glob and drop those matching an ``exclude`` glob (fnmatch on the
    dotted name, the same semantics setuptools uses for the build).
    """
    cfg = _packages_find_config()
    include = cfg.get("include", ["*"])
    exclude = cfg.get("exclude", [])
    discovered: set[str] = set()
    for pkg in _on_disk_packages():
        if not any(fnmatch.fnmatch(pkg, pat) for pat in include):
            continue
        if any(fnmatch.fnmatch(pkg, pat) for pat in exclude):
            continue
        discovered.add(pkg)
    return discovered


def test_diet_benchmarks_excluded_from_wheel() -> None:
    discovered = _discovered_packages()
    for pkg in _BENCHMARK_DIET_PACKAGES:
        assert not any(p == pkg or p.startswith(pkg + ".") for p in discovered), (
            f"{pkg} has no runtime consumer and must be excluded from the published "
            f"wheel via [tool.setuptools.packages.find].exclude; discovered packages "
            f"still include it"
        )


def test_runtime_benchmarks_stay_in_wheel() -> None:
    discovered = _discovered_packages()
    for pkg in _BENCHMARK_RUNTIME_PACKAGES:
        assert pkg in discovered, (
            f"{pkg} is imported at runtime (cli/app.py legal-eval + "
            f"recipes.first_party.legal) and must remain in the wheel"
        )


def test_legal_eval_import_path_still_works() -> None:
    # The exact symbols cli/app.py imports for the `magi legal-eval` command.
    from magi_agent.benchmarks.legal_eval import lift  # noqa: PLC0415
    from magi_agent.benchmarks.legalbench.cli import (  # noqa: PLC0415
        GateDisabledError,
        ensure_enabled,
        run_checkpoint_ablation,
        run_eval,
    )

    assert callable(lift)
    assert callable(ensure_enabled)
    assert callable(run_eval)
    assert callable(run_checkpoint_ablation)
    assert issubclass(GateDisabledError, Exception)
