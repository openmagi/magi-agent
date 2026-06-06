from __future__ import annotations

import tomllib
from pathlib import Path


def _pyproject() -> dict[str, object]:
    return tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))


def test_default_install_includes_litellm_for_provider_runner() -> None:
    project = _pyproject()["project"]
    dependencies = tuple(project["dependencies"])

    assert any(dependency.startswith("litellm") for dependency in dependencies)

