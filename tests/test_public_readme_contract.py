from pathlib import Path
import tomllib


README = Path(__file__).resolve().parents[1] / "README.md"
PYPROJECT = Path(__file__).resolve().parents[1] / "pyproject.toml"
ARCHITECTURE = Path(__file__).resolve().parents[1] / "magi_agent" / "ARCHITECTURE.md"


def _readme() -> str:
    return README.read_text(encoding="utf-8")


def _architecture() -> str:
    return ARCHITECTURE.read_text(encoding="utf-8")


def test_readme_uses_public_open_magi_agent_naming() -> None:
    text = _readme()

    assert text.startswith("<div align=\"center\">\n\n# Open Magi Agent\n")
    assert "OpenMagi Python ADK runtime" not in text
    assert ("Cla" + "wy") not in text
    assert ("cla" + "wy") not in text


def test_readme_documents_live_homebrew_formula() -> None:
    text = _readme()

    assert "brew install openmagi/tap/magi-agent" in text
    assert "Homebrew Tap" not in text
    assert "install-Homebrew" not in text


def test_readme_documents_local_web_dashboard() -> None:
    text = _readme()

    assert "Local web dashboard" in text
    assert "http://localhost:8080/dashboard" in text
    assert "magi-agent serve --port 8080" in text
    assert "npm run web:dev" not in text
    assert "http://localhost:3001" not in text


def test_readme_links_runtime_taxonomy_and_bundled_defaults() -> None:
    text = _readme()

    assert "magi_agent/ARCHITECTURE.md" in text
    assert "package taxonomy" in text
    assert "bundled defaults" in text
    assert "defaultInstalled" in text
    assert "defaultEnabled" in text


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


def test_architecture_documents_package_taxonomy_and_bundled_defaults() -> None:
    text = _architecture()

    assert "Not every top-level package is a harness" in text
    assert "Core tools are included but disabled by default" in text
    assert "defaultInstalled" in text
    assert "defaultEnabled" in text
    assert "openmagi.documents" in text
    assert "openmagi.knowledge" in text
    assert "openmagi.web" in text
    assert "openmagi.security-posture" in text
    assert "cannot be opted out" in text
    assert "openmagi-opinionated" in text
    assert "real-child-execution" in text
    assert "MAGI_EXTERNAL_HOOKS_ENABLED=true" in text
    assert "MAGI_LLM_HOOKS_ENABLED=true" in text
    assert "magi_agent/runtime" in text
    assert "magi_agent/harness/" in text
    assert "magi_agent/plugins/" in text
