from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DOCKERFILE = ROOT / "Dockerfile"


def test_runtime_image_installs_cli_and_composio_extras() -> None:
    dockerfile = DOCKERFILE.read_text(encoding="utf-8")

    assert 'python -m pip install --no-cache-dir ".[cli,composio]"' in dockerfile
    assert '".[cli]"' not in dockerfile
