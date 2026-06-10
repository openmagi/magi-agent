from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DOCKERFILE = ROOT / "Dockerfile"


def test_runtime_image_installs_runtime_extras() -> None:
    dockerfile = DOCKERFILE.read_text(encoding="utf-8")

    assert 'python -m pip install --no-cache-dir ".[cli,composio,providers]"' in dockerfile
    assert '".[cli]"' not in dockerfile
    assert '".[cli,composio]"' not in dockerfile


def test_build_metadata_args_are_in_final_stage_scope() -> None:
    """Build-metadata ARGs must be declared after FROM (final-stage scope).

    The Dockerfile is single-stage, so exactly one declaration per ARG is
    correct. (A previous revision declared each ARG twice; the duplicate
    block was removed as dead — same-stage re-declaration has no effect.)
    """
    dockerfile = DOCKERFILE.read_text(encoding="utf-8")
    assert dockerfile.count("FROM ") == 1, "single-stage assumption changed; revisit ARG scoping"
    after_from = dockerfile.split("FROM ", maxsplit=1)[1]
    final_stage = after_from.split("WORKDIR /app", maxsplit=1)[0]

    for name in (
        "CORE_AGENT_BUILD_SHA",
        "CORE_AGENT_IMAGE_REPO",
        "CORE_AGENT_IMAGE_TAG",
        "CORE_AGENT_EXPECTED_IMAGE_DIGEST",
    ):
        assert final_stage.count(f"ARG {name}") == 1
