"""Producer reuse: a new policy binds to an already-authored producer that
emits the same evidence type, instead of minting a duplicate. ZERO network."""
from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any

import pytest

from magi_agent.customize.policy_compiler import (
    _build_plan,
    _find_reusable_producer,
    compile_nl_to_policy,
)
from magi_agent.customize.policy_persist import persist_policy_plan
from magi_agent.customize.policy_plan import validate_policy_plan
from magi_agent.packs.dashboard_authored import read_sidecar


# --- fakes / fixtures ------------------------------------------------------


class _FakePart:
    def __init__(self, text: str) -> None:
        self.text = text


class _FakeContent:
    def __init__(self, text: str) -> None:
        self.parts = [_FakePart(text)]


class _FakeLlmResponse:
    def __init__(self, text: str) -> None:
        self.content = _FakeContent(text)


def _factory(response_text: str):
    class _FakeModel:
        model = "fake-policy-compiler"

        async def generate_content_async(
            self, _req: Any, stream: bool = False
        ) -> AsyncGenerator:
            yield _FakeLlmResponse(response_text)

    return lambda: _FakeModel()


_PARAMS = {
    "intent": "require a credible source before running the trade tool",
    "gatedTool": "execute_trade",
    "fetchTool": "web_fetch",
    "allowlistDomains": ["sec.gov"],
    "evidenceLabel": "source credibility",
    "onUnavailable": "deny",
}


def _existing_producer(**over) -> dict:
    """A previously-authored producer emitting custom:SourceCredibility."""
    base = {
        "id": "my-credible-source",
        "label": "records credibility",
        "scope": "always",
        "enabled": True,
        "trigger": {"tool": "web_fetch", "domainAllowlist": ["sec.gov", "europa.eu"]},
        "action": "audit",
        "emitsEvidenceType": "custom:SourceCredibility",
    }
    base.update(over)
    return base


# --- matcher unit ----------------------------------------------------------


def test_matcher_finds_producer_by_evidence_type() -> None:
    found = _find_reusable_producer([_existing_producer()], "custom:SourceCredibility")
    assert found is not None
    assert found["id"] == "my-credible-source"


def test_matcher_ignores_disabled_producer() -> None:
    assert (
        _find_reusable_producer([_existing_producer(enabled=False)], "custom:SourceCredibility")
        is None
    )


def test_matcher_ignores_non_deterministic_producer() -> None:
    # A result-text producer (no domainAllowlist) is not unlock-eligible.
    advisory = _existing_producer(trigger={"tool": "web_fetch", "match": {"contains": "SEC"}})
    assert _find_reusable_producer([advisory], "custom:SourceCredibility") is None


def test_matcher_ignores_type_mismatch() -> None:
    assert _find_reusable_producer([_existing_producer()], "custom:KycCheck") is None


def test_matcher_empty_list() -> None:
    assert _find_reusable_producer(None, "custom:SourceCredibility") is None
    assert _find_reusable_producer([], "custom:SourceCredibility") is None


# --- _build_plan reuse behavior --------------------------------------------


def test_build_plan_reuses_existing_producer_id() -> None:
    plan = _build_plan(_PARAMS, existing_producers=[_existing_producer()])
    assert plan["producerReused"] is True
    # The gate + binding point at the EXISTING producer id, not a fresh slug.
    assert plan["binding"]["producerRuleId"] == "my-credible-source"
    assert plan["gate"]["what"]["payload"]["requireEvidence"]["producerRuleId"] == "my-credible-source"
    # The producer body is the existing one verbatim (so validation stays sound).
    assert plan["producer"]["id"] == "my-credible-source"
    assert validate_policy_plan(plan) == []


def test_build_plan_gate_id_keyed_on_producer_and_tool() -> None:
    # Two policies reusing the SAME producer but gating DIFFERENT tools must get
    # DISTINCT gate ids (else the second overwrites the first on save).
    plan_a = _build_plan(
        {**_PARAMS, "gatedTool": "execute_trade"}, existing_producers=[_existing_producer()]
    )
    plan_b = _build_plan(
        {**_PARAMS, "gatedTool": "deploy"}, existing_producers=[_existing_producer()]
    )
    assert plan_a["gate"]["id"] != plan_b["gate"]["id"]
    assert plan_a["binding"]["producerRuleId"] == plan_b["binding"]["producerRuleId"]


def test_build_plan_no_match_mints_fresh_producer() -> None:
    plan = _build_plan(_PARAMS, existing_producers=[_existing_producer(emitsEvidenceType="custom:Other")])
    assert plan["producerReused"] is False
    assert plan["producer"]["id"] == "source-credibility"  # freshly slugged from the label


def test_build_plan_reuse_note_flags_uncovered_domain() -> None:
    # The reused producer trusts sec.gov + europa.eu; requesting a new domain
    # surfaces an advisory note (not a hard error).
    plan = _build_plan(
        {**_PARAMS, "allowlistDomains": ["sec.gov", "irs.gov"]},
        existing_producers=[_existing_producer()],
    )
    assert plan["producerReused"] is True
    assert "reuseNote" in plan
    assert "irs.gov" in plan["reuseNote"]


# --- compile_nl_to_policy surfaces reuse -----------------------------------


def test_compile_surfaces_producer_reused() -> None:
    out = asyncio.run(
        compile_nl_to_policy(
            "require verified source before trading",
            model_factory=_factory(json.dumps(_PARAMS)),
            existing_producers=[_existing_producer()],
        )
    )
    assert out["ok"] is True
    assert out["producerReused"] is True
    assert out["plan"]["binding"]["producerRuleId"] == "my-credible-source"
    assert "my-credible-source" in out["explanation"]


def test_compile_without_existing_producers_is_fresh() -> None:
    out = asyncio.run(
        compile_nl_to_policy(
            "require verified source before trading",
            model_factory=_factory(json.dumps(_PARAMS)),
        )
    )
    assert out["ok"] is True
    assert out["producerReused"] is False


# --- persist: reuse does NOT clobber the existing producer ------------------


@pytest.fixture(autouse=True)
def _writable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(tmp_path / "customize.json"))
    monkeypatch.setattr(
        "magi_agent.packs.discovery.default_search_bases", lambda: [tmp_path]
    )


def _reuse_plan() -> dict:
    return _build_plan(_PARAMS, existing_producers=[_existing_producer()])


def test_persist_reuse_leaves_existing_producer_untouched(tmp_path: Path) -> None:
    # Seed an existing producer (with its own domains) into the writable sidecar
    # via a first, non-reuse policy save that creates 'my-credible-source'.
    seed_plan = _build_plan({**_PARAMS, "evidenceLabel": "source credibility"})
    seed_plan["producer"]["id"] = "my-credible-source"
    seed_plan["producer"]["trigger"]["domainAllowlist"] = ["sec.gov", "europa.eu"]
    seed_plan["binding"]["producerRuleId"] = "my-credible-source"
    seed_plan["gate"]["what"]["payload"]["requireEvidence"]["producerRuleId"] = "my-credible-source"
    persist_policy_plan(seed_plan)
    before = read_sidecar(tmp_path / "dashboard-authored")
    assert len(before) == 1
    assert set(before[0].trigger.domain_allowlist) == {"sec.gov", "europa.eu"}

    # Now author a SECOND policy that reuses that producer but gates a new tool
    # and requests only a subset of domains.
    reuse = _build_plan(
        {**_PARAMS, "gatedTool": "deploy", "allowlistDomains": ["sec.gov"]},
        existing_producers=[b.model_dump(by_alias=True, mode="json") for b in before],
    )
    assert reuse["producerReused"] is True
    persist_policy_plan(reuse)

    after = read_sidecar(tmp_path / "dashboard-authored")
    # Still exactly ONE producer (no duplicate), domains unchanged (not clobbered).
    assert len(after) == 1
    assert after[0].id == "my-credible-source"
    assert set(after[0].trigger.domain_allowlist) == {"sec.gov", "europa.eu"}
    # Two distinct gates now exist (one per gated tool).
    from magi_agent.customize.store import customize_path, load_overrides

    rules = load_overrides(customize_path())["verification"]["custom_rules"]
    gate_tools = {
        r["what"]["payload"]["match"]["tool"]
        for r in rules
        if r.get("what", {}).get("kind") == "tool_perm"
    }
    assert {"execute_trade", "deploy"} <= gate_tools
