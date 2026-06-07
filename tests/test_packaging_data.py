"""Guard against shipping a wheel that omits required package data.

Source-tree tests cannot catch a missing-from-wheel bug (the data file exists on
disk during the test run), so we assert that every non-Python data file loaded
via ``importlib.resources`` is declared in ``[tool.setuptools.package-data]``.
The bundled slash-command templates (``cli/commands/templates/*.txt``) were
omitted once, which crashed the TUI on launch with ``FileNotFoundError``.
"""

from __future__ import annotations

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
