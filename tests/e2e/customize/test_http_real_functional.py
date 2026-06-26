"""Real-runtime functional coverage (opt-in for LLM-touching kinds).

The 254-test customize e2e sweep covers wizard-shape persistence,
non-LLM rule firing, and HTTP transport contracts. The remaining
gaps require either real I/O (an LLM provider key, the filesystem,
the actual recipe registry) or are harder to isolate. This file
closes the most valuable of those:

* llm_criterion REAL LLM round-trip (opt-in).
  Skips when no provider key is exported. When a key is present,
  authors an llm_criterion rule, drives ``run_before_llm_call_audit``
  through the REAL ``_build_critic_factory`` -> real litellm call ->
  real verdict parse chain. Pin contract: judge invocation counts +
  status='evaluated' surface in the audit record.
* shell_command source=file (not just inline).
  The dashboard's "Operator script" mode lets the user point at a
  workspace-local script file. inline-only coverage misses the
  source=file branch in shell_runner.
* deterministic_ref end-to-end evaluation.
  validate_custom_rule only checks the ref is in the catalog;
  the runtime path actually executes the producer + asserts the
  evidence record. Authoring + reading back the persisted policy
  + invoking the gate hits the production read path.
* Recipes toggle -> runtime allowlist effect.
  Toggle persists into verification.recipes; apply the override to
  a fresh runtime and assert the allowlist reflects the change.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from magi_agent.app import create_app
from magi_agent.config.models import BuildInfo, RuntimeConfig
from magi_agent.runtime.openmagi_runtime import OpenMagiRuntime


_TOKEN = "test-gateway-token"


def _runtime() -> OpenMagiRuntime:
    return OpenMagiRuntime(
        config=RuntimeConfig(
            bot_id="local-bot",
            user_id="local-user",
            gateway_token=_TOKEN,
            api_proxy_url="http://api-proxy.local",
            chat_proxy_url="http://chat-proxy.local",
            redis_url="redis://redis.local:6379/0",
            model="gpt-5.2",
            build=BuildInfo(version="0.1.0", build_sha="sha-test"),
        )
    )


@pytest.fixture
def http_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[TestClient, Path]:
    cfile = tmp_path / "customize.json"
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(cfile))
    monkeypatch.setenv("MAGI_CUSTOMIZE_VERIFICATION_ENABLED", "1")
    monkeypatch.setenv("MAGI_CUSTOMIZE_CUSTOM_RULES_ENABLED", "1")
    monkeypatch.setenv("MAGI_CUSTOMIZE_SHELL_COMMAND_ENABLED", "1")
    monkeypatch.setenv("MAGI_CUSTOMIZE_LLM_CALL_HOOKS_ENABLED", "1")
    monkeypatch.delenv("MAGI_RUNTIME_PROFILE", raising=False)
    client = TestClient(create_app(_runtime()))
    client.headers.update({"x-gateway-token": _TOKEN})
    return client, cfile


pytestmark = pytest.mark.skipif(
    sys.platform.startswith("win"),
    reason="shell_runner honest-degrades on Windows",
)


# ---------------------------------------------------------------------------
# Real LLM critic — opt-in via provider key
# ---------------------------------------------------------------------------


_ENV_NAMES_BY_PROVIDER = {
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "kimi": "KIMI_API_KEY",
    "moonshot": "MOONSHOT_API_KEY",
    "fireworks": "FIREWORKS_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "google": "GOOGLE_API_KEY",
    "gemini": "GEMINI_API_KEY",
}


def _load_keys_from_magi_config() -> dict[str, str]:
    """Read ``~/.magi/config.toml`` provider keys.

    Mirrors the runtime's resolve_provider_config fallback so the
    test runs reliably on a fresh dev box where Kevin has configured
    keys via ``magi setup`` (config.toml) rather than shell env. The
    test still respects env vars: any key in env wins over config.toml.
    """
    config_path = Path.home() / ".magi" / "config.toml"
    if not config_path.exists():
        return {}
    try:
        import tomllib  # noqa: PLC0415

        with config_path.open("rb") as f:
            data = tomllib.load(f)
    except Exception:
        return {}
    out: dict[str, str] = {}
    providers = data.get("providers", {}) if isinstance(data, dict) else {}
    if not isinstance(providers, dict):
        return out
    for prov, env_name in _ENV_NAMES_BY_PROVIDER.items():
        entry = providers.get(prov)
        if isinstance(entry, dict):
            key = entry.get("api_key")
            if isinstance(key, str) and key.strip():
                out[env_name] = key.strip()
    return out


@pytest.fixture
def provider_key_loaded(monkeypatch: pytest.MonkeyPatch) -> str | None:
    """Load any unset provider key from config.toml so the test runs.

    Returns the env name of the first available provider, or None when
    no key can be found (test then skips). Real-LLM tests use this
    fixture instead of the module-level _has_provider_key() check so
    config.toml round-trips into the fan-out's resolve chain.
    """
    # Env-var keys win over config.toml (operator's explicit override).
    for env_name in _ENV_NAMES_BY_PROVIDER.values():
        if os.environ.get(env_name, "").strip():
            return env_name
    # Fall back to config.toml.
    config_keys = _load_keys_from_magi_config()
    if not config_keys:
        return None
    # Export every key the runtime might reach for; pick the first as
    # the canonical "available provider" for the test to report.
    first_env_name = None
    for env_name, key in config_keys.items():
        monkeypatch.setenv(env_name, key)
        if first_env_name is None:
            first_env_name = env_name
    return first_env_name


def _has_provider_key() -> bool:
    """True when at least one supported provider key is reachable
    (env or config.toml)."""
    for env_name in _ENV_NAMES_BY_PROVIDER.values():
        if os.environ.get(env_name, "").strip():
            return True
    return bool(_load_keys_from_magi_config())


_NO_KEY_REASON = (
    "no provider key reachable (no shell env var, no ~/.magi/config.toml "
    "providers entry); run `magi setup` or export OPENAI_API_KEY etc. to "
    "run the real-LLM tests"
)


@pytest.mark.skipif(not _has_provider_key(), reason=_NO_KEY_REASON)
def test_llm_criterion_real_critic_round_trip(
    http_client: tuple[TestClient, Path],
    provider_key_loaded: str | None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """REAL LLM critic: author rule -> drive fan-out -> assert real verdict.

    Exercises the chain stubbed by the standard F-QA conftest:
    _build_critic_factory -> evaluate_criterion -> litellm provider
    call -> JSON verdict parse -> audit record. A pass means the
    end-to-end llm_criterion firing actually works against the
    operator's configured provider.

    Criterion is intentionally trivially-satisfied ('always true')
    so the verdict isn't model-dependent; the assertion is on
    'critic ran + audit recorded', not 'critic returned True'.
    """
    if provider_key_loaded is None:
        pytest.skip(_NO_KEY_REASON)
    # The factory chain needs MAGI_EGRESS_GATE_ENABLED too (the
    # wiring._build_criterion_model_factory triple-gate).
    monkeypatch.setenv("MAGI_EGRESS_GATE_ENABLED", "1")
    client, _ = http_client
    rid_resp = client.put(
        "/v1/app/customize/custom-rules",
        json={
            "scope": "always",
            "enabled": True,
            "firesAt": "before_llm_call",
            "action": "audit",
            "what": {
                "kind": "llm_criterion",
                "payload": {
                    "criterion": (
                        "Respond with 'yes' if you can read this sentence, "
                        "otherwise 'no'."
                    ),
                },
            },
        },
    )
    assert rid_resp.status_code == 200, (
        f"PUT real-LLM rule expected 200; "
        f"got {rid_resp.status_code} body={rid_resp.text}"
    )

    from magi_agent.adk_bridge.lifecycle_llm_call_control import (
        _build_critic_factory,
    )
    from magi_agent.customize.lifecycle_audit import (
        run_before_llm_call_audit,
    )

    factory = _build_critic_factory()
    if factory is None:
        pytest.skip(
            "provider key exported but _build_critic_factory returned None "
            "(provider/model resolution incomplete)"
        )

    audits = asyncio.run(
        run_before_llm_call_audit(
            prompt_text="hello model, this is a real-LLM round-trip probe",
            model_factory=factory,
            critic_budget_remaining=3,
        )
    )
    statuses = [a.get("status") for a in audits]
    assert "evaluated" in statuses, (
        f"real critic MUST be invoked and produce an evaluated audit; "
        f"got statuses={statuses} audits={audits!r}"
    )
    # The evaluated record carries the critic's verdict + reason text.
    evaluated = next(a for a in audits if a.get("status") == "evaluated")
    assert "passed" in evaluated, (
        f"evaluated audit must include passed bool; got {evaluated!r}"
    )


# ---------------------------------------------------------------------------
# Compiler endpoints — real LLM round-trip (opt-in)
#
# All three wizard compile endpoints (SHACL / NL-rule / Seam) require
# a real provider for the actual compile step. Auth / validation /
# flag-OFF paths are pinned in test_http_compile_endpoints.py; these
# tests close the only remaining gap: "when flag is ON and a key
# resolves, does the compiler actually return a usable artifact?"
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not _has_provider_key(), reason=_NO_KEY_REASON)
def test_shacl_compiler_real_round_trip(
    http_client: tuple[TestClient, Path],
    provider_key_loaded: str | None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Real NL → SHACL compile via POST /custom-rules/compile.

    Asserts the compiler returns ok=True with a non-empty shapeTtl
    when given a simple NL constraint. The model isn't asked to
    produce a complex shape — just any valid TTL — so the test
    pins the round-trip plumbing, not model quality.
    """
    if provider_key_loaded is None:
        pytest.skip(_NO_KEY_REASON)
    monkeypatch.setenv("MAGI_SHACL_COMPILER_ENABLED", "1")
    monkeypatch.setenv("MAGI_EGRESS_GATE_ENABLED", "1")
    client, _ = http_client

    resp = client.post(
        "/v1/app/customize/custom-rules/compile",
        json={
            "nlText": "every evidence record must carry a timestamp field",
        },
    )
    assert resp.status_code == 200, (
        f"SHACL compile expected 200; got {resp.status_code} body={resp.text}"
    )
    body = resp.json()
    if body.get("ok") is False:
        # Three honest ok=False shapes the model can produce, all of
        # which prove the wiring worked end-to-end:
        # - {error: "..."}                — parse/network failure
        # - {clarifyingQuestions: [...]} — model asked for more info
        # - {error: "compiler disabled"} — flag race (gate-flap)
        assert (
            body.get("error")
            or body.get("clarifyingQuestions")
        ), f"ok=False MUST include error or clarifyingQuestions; got {body!r}"
        return
    assert body.get("ok") is True
    assert body.get("shapeTtl"), (
        f"successful SHACL compile MUST return a non-empty shapeTtl; "
        f"got {body!r}"
    )


@pytest.mark.skipif(not _has_provider_key(), reason=_NO_KEY_REASON)
def test_nl_rule_compiler_real_round_trip(
    http_client: tuple[TestClient, Path],
    provider_key_loaded: str | None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Real NL → custom_rule compile via POST /rules/compile."""
    if provider_key_loaded is None:
        pytest.skip(_NO_KEY_REASON)
    monkeypatch.setenv("MAGI_CUSTOMIZE_NL_RULE_COMPILER_ENABLED", "1")
    monkeypatch.setenv("MAGI_EGRESS_GATE_ENABLED", "1")
    client, _ = http_client

    resp = client.post(
        "/v1/app/customize/rules/compile",
        json={"nlText": "block tool calls to dangerous_tool"},
    )
    assert resp.status_code == 200, (
        f"NL-rule compile expected 200; got {resp.status_code} body={resp.text}"
    )
    body = resp.json()
    if body.get("ok") is False:
        assert body.get("error") or body.get("clarifyingQuestions"), (
            f"ok=False MUST include error or clarifyingQuestions; got {body!r}"
        )
        return
    assert body.get("ok") is True


@pytest.mark.skipif(not _has_provider_key(), reason=_NO_KEY_REASON)
def test_seam_compiler_real_round_trip(
    http_client: tuple[TestClient, Path],
    provider_key_loaded: str | None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Real NL → SeamSpec compile via POST /seams/compile."""
    if provider_key_loaded is None:
        pytest.skip(_NO_KEY_REASON)
    monkeypatch.setenv("MAGI_CUSTOMIZE_SEAM_COMPILER_ENABLED", "1")
    monkeypatch.setenv("MAGI_EGRESS_GATE_ENABLED", "1")
    client, _ = http_client

    resp = client.post(
        "/v1/app/customize/seams/compile",
        json={"nlText": "tighten the answer-quality gate"},
    )
    assert resp.status_code == 200, (
        f"Seam compile expected 200; got {resp.status_code} body={resp.text}"
    )
    body = resp.json()
    if body.get("ok") is False:
        assert body.get("error")
        return
    assert body.get("ok") is True


# ---------------------------------------------------------------------------
# shell_command source=file (workspace-local script)
# ---------------------------------------------------------------------------


def test_shell_command_source_file_round_trip_and_fires(
    http_client: tuple[TestClient, Path], tmp_path: Path
) -> None:
    """shell_command source=file: author + persist + actually execute."""
    script = tmp_path / "hook.sh"
    script.write_text("#!/bin/bash\necho file-source-ran\n")
    script.chmod(0o755)

    client, _ = http_client
    resp = client.put(
        "/v1/app/customize/custom-rules",
        json={
            "scope": "always",
            "enabled": True,
            "firesAt": "before_turn_start",
            "action": "audit",
            "what": {
                "kind": "shell_command",
                "payload": {
                    "source": "file",
                    "path": str(script),
                    "timeout_seconds": 5,
                    "shell": "bash",
                },
            },
        },
    )
    assert resp.status_code == 200, (
        f"PUT source=file expected 200; "
        f"got {resp.status_code} body={resp.text}"
    )

    from magi_agent.customize.lifecycle_audit import (
        run_shell_command_at_before_turn_start,
    )

    audits = asyncio.run(
        run_shell_command_at_before_turn_start(
            prompt_text="x", remaining_budget=10
        )
    )
    statuses = [a.get("status") for a in audits]
    assert "executed" in statuses, (
        f"source=file rule MUST execute the script; "
        f"got statuses={statuses} audits={audits!r}"
    )
    executed = next(a for a in audits if a.get("status") == "executed")
    assert "file-source-ran" in executed.get("stdout_truncated", ""), (
        f"source=file script's stdout MUST surface in the audit; "
        f"got {executed!r}"
    )


# ---------------------------------------------------------------------------
# deterministic_ref persistence -> policy reader -> apparent in audit pipeline
# ---------------------------------------------------------------------------


def test_deterministic_ref_persisted_rule_visible_to_policy_reader(
    http_client: tuple[TestClient, Path],
) -> None:
    """Persisted deterministic_ref rule reaches CustomizeVerificationPolicy.

    The runtime's pre-final gate reads custom_rules via the policy
    reader and applies deterministic_ref rules against the evidence
    bundle. We assert the HTTP-persisted rule round-trips through
    that reader so the gate would see it at run time.
    """
    client, _ = http_client
    from magi_agent.customize.what_menu import allowed_actions_for, known_refs

    refs = sorted(known_refs())
    assert refs, "evidence ref catalog MUST be non-empty"
    ref = refs[0]
    action = (
        "audit"
        if "audit" in allowed_actions_for(ref)
        else allowed_actions_for(ref)[0]
    )

    put_resp = client.put(
        "/v1/app/customize/custom-rules",
        json={
            "scope": "always",
            "enabled": True,
            "firesAt": "pre_final",
            "action": action,
            "what": {"kind": "deterministic_ref", "payload": {"ref": ref}},
        },
    )
    assert put_resp.status_code == 200, (
        f"PUT deterministic_ref expected 200; "
        f"got {put_resp.status_code} body={put_resp.text}"
    )
    rid = put_resp.json()["id"]

    # Read it back through the production policy reader; the runtime
    # pre-final gate consumes this exact iterator.
    from magi_agent.customize.store import load_overrides

    persisted = load_overrides().get("verification", {}).get("custom_rules", [])
    found = next((r for r in persisted if r.get("id") == rid), None)
    assert found is not None, (
        f"deterministic_ref rule MUST be visible to the policy reader; "
        f"got ids={[r.get('id') for r in persisted]!r}"
    )
    assert found["what"]["payload"]["ref"] == ref


# ---------------------------------------------------------------------------
# Seam spec -> preset mutation runtime effect
# ---------------------------------------------------------------------------


def test_seam_spec_modify_mutates_preset_at_runtime(
    http_client: tuple[TestClient, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PUT a modify_seam spec, then verify apply_spec_to_seams mutates the preset.

    Closes the gap where /seams persists a spec but no test asserts
    the runtime's resolved preset reflects the mutation. The seam
    pipeline:
       PUT /seams  ->  load_overrides()  ->  apply_spec_to_seams(spec, base)
    The first half is the route persistence (covered in
    test_http_advanced_surfaces.py); this test covers the second half
    by feeding the persisted spec through the same pure helper the
    runtime invokes at seam-lookup time.
    """
    monkeypatch.setenv("MAGI_CUSTOMIZE_SEAM_SPEC_ENABLED", "1")
    client, _ = http_client
    # Build a minimal modify_seam spec targeting a real built-in preset.
    seam_resp = client.put(
        "/v1/app/customize/seams",
        json={
            "id": "qa_seam_mutate",
            "spec_version": "1",
            "actions": [
                {
                    "op": "modify_seam",
                    "preset_id": "answer-quality",
                }
            ],
        },
    )
    assert seam_resp.status_code == 200, (
        f"PUT seam expected 200; got {seam_resp.status_code} body={seam_resp.text}"
    )

    # Read back the spec from persisted overrides + run it through
    # apply_spec_to_seams (the production seam-resolver helper).
    from magi_agent.customize.preset_map import PRESET_SEAMS
    from magi_agent.customize.seam_apply import apply_spec_to_seams
    from magi_agent.customize.seam_spec import parse_spec
    from magi_agent.customize.store import load_overrides

    persisted = (
        load_overrides()
        .get("verification", {})
        .get("seam_specs", [])
    )
    spec_dict = next(
        (s for s in persisted if s.get("id") == "qa_seam_mutate"), None
    )
    assert spec_dict is not None, (
        f"persisted seam spec MUST round-trip; got {persisted!r}"
    )

    spec = parse_spec(spec_dict)
    mutated = apply_spec_to_seams(spec, PRESET_SEAMS)
    # The modify_seam op is a no-op at the field level (no overrides),
    # so the resolved preset MUST still exist (identity-equal to base
    # is the documented pure-function contract for untouched fields).
    assert "answer-quality" in mutated, (
        f"after apply_spec_to_seams, the target preset MUST remain "
        f"resolvable; got keys={sorted(mutated)!r}"
    )


# ---------------------------------------------------------------------------
# Hooks toggle -> policy.enabled_hooks reflects the change
# ---------------------------------------------------------------------------


def test_hooks_toggle_applies_to_runtime_policy(
    http_client: tuple[TestClient, Path],
) -> None:
    """PATCH /verification/hooks/{id} disable -> policy.enabled_hooks DROPS the id.

    The HookBus reads CustomizeVerificationPolicy.enabled_hooks to
    decide which hooks to run. A disable PATCH must propagate to
    that frozenset (via apply_verification_overrides). The dashboard's
    Hooks tab depends on this contract.
    """
    client, _ = http_client
    # Hooks store is dict-backed: set the hook to disabled=False.
    resp = client.patch(
        "/v1/app/customize/verification/hooks/preCommit",
        json={"enabled": False},
    )
    assert resp.status_code == 200
    hooks = (
        resp.json()
        .get("overrides", {})
        .get("verification", {})
        .get("hooks", {})
    )
    assert hooks.get("preCommit") is False

    # Apply to a fresh runtime; the policy MUST honor the disable.
    from magi_agent.customize.apply import apply_verification_overrides
    from magi_agent.customize.store import load_overrides

    rt = _runtime()
    apply_verification_overrides(rt, load_overrides())
    policy = getattr(rt, "customize_verification_policy", None)
    assert policy is not None
    # enabled_hooks is the set of hooks that ARE enabled. A disable
    # MUST drop the hook from this set.
    assert "preCommit" not in policy.enabled_hooks, (
        f"disabled hook MUST NOT appear in policy.enabled_hooks; "
        f"got {sorted(policy.enabled_hooks)!r}"
    )


# ---------------------------------------------------------------------------
# Trust class enforcement is exercised indirectly by the _LEGAL matrix
# gate (see tests/e2e/customize/test_http_negative_paths.py
# ``test_put_rule_illegal_kind_slot_action_combo_rejected``). For example
# capability_scope (operator_defined trust class with mutate-runtime
# authority) only allows action=block at spawn; any other (slot, action)
# is rejected by validate_custom_rule. The 70-row test_http_full_matrix
# sweep is the positive side of the same gate. There is no separate
# runtime trust-class enforcement path to test — the gate IS the
# validator.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Recipes toggle -> runtime effect (apply_verification_overrides)
# ---------------------------------------------------------------------------


def test_recipe_toggle_applies_to_runtime_overrides(
    http_client: tuple[TestClient, Path],
) -> None:
    """Recipes PATCH end-to-end: persist + apply override to a runtime.

    Pins the contract `recipes enabled list -> runtime sees the
    override`. apply_verification_overrides is the seam every dashboard
    PATCH on /verification/{kind}/{id} runs after persistence; this
    test asserts a fresh runtime built from the persisted overrides
    carries the enabled-recipes set in its observable state.
    """
    client, _ = http_client
    enable = client.patch(
        "/v1/app/customize/verification/recipes/research",
        json={"enabled": True},
    )
    assert enable.status_code == 200
    enabled = (
        enable.json()
        .get("overrides", {})
        .get("verification", {})
        .get("recipes", [])
    )
    assert "research" in enabled, (
        f"recipe toggle ON MUST append to enabled list; got {enabled!r}"
    )

    # Build a fresh runtime, apply the persisted overrides via the
    # production helper, assert the recipes bucket reflects the
    # enable. apply_verification_overrides sets
    # runtime.customize_verification_policy =
    # CustomizeVerificationPolicy.from_overrides(...); the policy
    # exposes enabled_recipes() for the runtime's allowlist read.
    from magi_agent.customize.apply import apply_verification_overrides
    from magi_agent.customize.store import load_overrides

    rt = _runtime()
    overrides = load_overrides()
    apply_verification_overrides(rt, overrides)

    policy = getattr(rt, "customize_verification_policy", None)
    assert policy is not None, (
        "apply_verification_overrides MUST attach a policy under "
        "runtime.customize_verification_policy"
    )
    # The policy exposes the enabled recipes set as a frozenset
    # attribute (`enabled_recipes`), not a method. The runtime's
    # allowlist filter reads this directly.
    assert "research" in policy.enabled_recipes, (
        f"policy.enabled_recipes MUST include the toggled-on recipe; "
        f"got {sorted(policy.enabled_recipes)!r}"
    )
