"""T1 golden corpus: replay every handwritten scenario in CI (scripted LLM).

Pure in-process HTTP against a scripted fake compiler + deterministic user
turns. No network, no live LLM. This is the CI gate that keeps the whole
authoring surface honest.
"""
from __future__ import annotations

from pathlib import Path

import pytest

_HANDWRITTEN = (
    Path(__file__).resolve().parents[2]
    / "benchmarks"
    / "authoring"
    / "corpus"
    / "v1"
    / "handwritten"
)


def _runtime():
    from magi_agent.config.models import BuildInfo, RuntimeConfig
    from magi_agent.runtime.openmagi_runtime import OpenMagiRuntime

    return OpenMagiRuntime(
        config=RuntimeConfig(
            bot_id="local-bot",
            user_id="local-user",
            gateway_token="test-gateway-token",
            api_proxy_url="http://api-proxy.local",
            chat_proxy_url="http://chat-proxy.local",
            redis_url="redis://redis.local:6379/0",
            model="gpt-5.2",
            build=BuildInfo(version="0.1.0", build_sha="sha-test"),
        )
    )


def _scenario_files() -> list[Path]:
    return sorted(p for p in _HANDWRITTEN.glob("*.yaml"))


def test_corpus_dir_has_scenarios() -> None:
    files = _scenario_files()
    assert len(files) >= 12, f"expected >=12 handwritten scenarios, got {len(files)}"


def test_loader_and_lint_all_files() -> None:
    from benchmarks.authoring.corpus_lint import lint_corpus_file
    from benchmarks.authoring.scenario import load_scenario

    for f in _scenario_files():
        scenario = load_scenario(f)          # must not raise
        problems = lint_corpus_file(f)       # schema + slot/oracle consistency
        assert problems == [], f"{f.name}: {problems}"
        assert scenario.id
        assert scenario.flow in ("single_rule", "linked_policy")


@pytest.mark.parametrize(
    "scenario_path", _scenario_files(), ids=lambda p: p.stem
)
def test_replay_scenario_green(
    scenario_path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from benchmarks.authoring.runner import run_scenario
    from benchmarks.authoring.scenario import load_scenario

    monkeypatch.setenv("MAGI_CUSTOMIZE", str(tmp_path / "customize.json"))
    monkeypatch.setenv("MAGI_CUSTOMIZE_NL_INTERACTIVE_ENABLED", "1")
    monkeypatch.setattr(
        "magi_agent.packs.discovery.default_search_bases", lambda: [tmp_path]
    )
    scenario = load_scenario(scenario_path)
    result = run_scenario(scenario, _runtime(), token="test-gateway-token", tier="t1")
    assert result.passed, (
        f"{scenario.id} failed: first_divergence={result.first_divergence}"
    )


def test_loader_rejects_schema_violation(tmp_path: Path) -> None:
    from benchmarks.authoring.scenario import ScenarioSchemaError, load_scenario

    bad = tmp_path / "bad.yaml"
    bad.write_text("schema_version: 1\nflow: not_a_flow\n", encoding="utf-8")
    with pytest.raises(ScenarioSchemaError):
        load_scenario(bad)


def test_lint_warns_missing_llm_script(tmp_path: Path) -> None:
    from benchmarks.authoring.corpus_lint import lint_corpus_file

    # A flow-B scenario with no llm_script is live-only; the linter WARNS.
    doc = tmp_path / "warn.yaml"
    doc.write_text(
        "schema_version: 1\n"
        "id: warn_no_script_001\n"
        "flow: linked_policy\n"
        "archetype: linked\n"
        "language: en\n"
        "turns:\n"
        "  - say: 'gate execute_trade'\n"
        "oracle:\n"
        "  expect_ready: false\n",
        encoding="utf-8",
    )
    problems = lint_corpus_file(doc, warn_only_ok=True)
    assert any("llm_script" in p for p in problems)


def test_broken_fixture_populates_first_divergence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A deliberately-wrong oracle produces a populated first_divergence."""
    from benchmarks.authoring.runner import run_scenario
    from benchmarks.authoring.scenario import load_scenario

    broken = (
        Path(__file__).resolve().parents[2]
        / "benchmarks"
        / "authoring"
        / "corpus"
        / "v1"
        / "handwritten"
        / "_broken"
        / "deliberately_broken_oracle_001.yaml"
    )
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(tmp_path / "customize.json"))
    monkeypatch.setenv("MAGI_CUSTOMIZE_NL_INTERACTIVE_ENABLED", "1")
    monkeypatch.setattr(
        "magi_agent.packs.discovery.default_search_bases", lambda: [tmp_path]
    )
    scenario = load_scenario(broken)
    result = run_scenario(scenario, _runtime(), token="test-gateway-token", tier="t1")
    assert result.passed is False
    fd = result.first_divergence
    assert fd is not None
    assert "expected" in fd and "got" in fd
    assert "turn" in fd or "oracle" in fd or "invariant" in fd
