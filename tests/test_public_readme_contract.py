from pathlib import Path
import tomllib


README = Path(__file__).resolve().parents[1] / "README.md"
PYPROJECT = Path(__file__).resolve().parents[1] / "pyproject.toml"


def _readme() -> str:
    return README.read_text(encoding="utf-8")


def test_readme_uses_public_open_magi_agent_naming() -> None:
    text = _readme()

    assert text.startswith("<div align=\"center\">\n\n# Open Magi Agent\n")
    assert "OpenMagi Python ADK runtime" not in text
    assert "Clawy" not in text
    assert "clawy" not in text


def test_readme_does_not_claim_homebrew_before_formula_is_live() -> None:
    text = _readme()

    assert "brew install openmagi/tap/magi-agent" not in text
    assert "Homebrew Tap" not in text
    assert "install-Homebrew" not in text


def test_readme_documents_local_web_dashboard() -> None:
    text = _readme()

    assert "Local web dashboard" not in text
    assert "npm run web:dev" not in text
    assert "http://localhost:3001" not in text
    assert "magi-agent serve --port 8080" in text


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
