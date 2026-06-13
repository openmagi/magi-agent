import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_runtime_dockerfile_installs_hosted_first_party_tool_extras() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")

    match = re.search(r'pip install --no-cache-dir "\.\[([^\]]+)\]"', dockerfile)
    assert match is not None, "runtime Dockerfile must install magi-agent with extras"

    extras = {extra.strip() for extra in match.group(1).split(",")}
    assert {"browser", "cli", "composio", "providers", "waf"}.issubset(extras)


def test_runtime_dockerfile_installs_playwright_chromium_with_os_deps() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert "python -m playwright install --with-deps chromium" in dockerfile
