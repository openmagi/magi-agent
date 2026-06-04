from pathlib import Path
import tomllib


README = Path(__file__).resolve().parents[1] / "README.md"
PYPROJECT = Path(__file__).resolve().parents[1] / "pyproject.toml"


def _readme() -> str:
    return README.read_text(encoding="utf-8")


def test_readme_uses_public_open_magi_agent_naming() -> None:
    text = _readme()

    assert text.startswith("<div align=\"center\">\n\n# Open Magi Agent\n")
    assert "**The programmable agent that complies with your rules.**" in text
    assert "Local-first agent runtime and CLI" not in text
    assert "OpenMagi Python ADK runtime" not in text
    assert ("Cla" + "wy") not in text
    assert ("cla" + "wy") not in text


def test_readme_documents_live_homebrew_formula() -> None:
    text = _readme()

    assert "brew install --force-bottle openmagi/tap/magi-agent" in text
    assert "magi-agent serve --help" in text
    assert "brew reinstall openmagi/tap/magi-agent --force-bottle" in text
    assert "Homebrew Tap" not in text
    assert "install-Homebrew" not in text


def test_readme_documents_local_web_dashboard() -> None:
    text = _readme()

    assert "Local web dashboard" in text
    assert "http://localhost:8080/dashboard" in text
    assert "magi-agent serve --port 8080" in text
    assert text.index("brew install --force-bottle openmagi/tap/magi-agent") < text.index("uv sync")
    assert "npm run web:dev" not in text
    assert "http://localhost:3001" not in text


def test_readme_avoids_hosted_rollout_language() -> None:
    text = _readme().lower()

    assert "hosted" not in text
    assert "cloud" not in text
    assert "selected-bot" not in text
    assert "rollout" not in text


def test_public_package_metadata_uses_simple_open_magi_agent_copy() -> None:
    metadata = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))

    description = metadata["project"]["description"]
    assert description == "Open Magi agent runtime and CLI for personal AI agents"
    assert "OpenMagi Python ADK runtime" not in description
    assert "hosted" not in description.lower()
