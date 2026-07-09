"""U4 tamper resistance (B-1 core): the agent config directory is write-protected.

The allowlist, mode, and builtin_policies toggle all live in
``~/.magi/customize.json`` (agent-writable today; an append via complex shell is
bypass-preapproved). Without protection, block mode is decorative: an
injection-influenced agent could enlarge its own allowlist, flip its mode, or
self-disable a toggle. U4 protects the RESOLVED customize directory (OQ-7:
directory-wide ``~/.magi``) as a HARD deny (``protected_config_write_denied``)
covering shell append AND FileWrite/FileEdit/PatchApply, firing even under
bypass, attributed to the new ``system_safety.config_protection`` member.

The resolved path respects ``MAGI_CUSTOMIZE`` / ``MAGI_CONFIG`` / HOME (hosted
uses a PVC-relocated customize path), so the protection keys on the resolved
directory, never a hardcoded literal. Reads stay allowed; the operator
hand-editing the file in their own editor is untouched (safety governs only
AGENT tool calls).
"""

from __future__ import annotations

import pytest

from magi_agent.tools.context import ToolContext
from magi_agent.tools.manifest import ToolManifest, ToolSource
from magi_agent.tools.permission import ToolPermissionPolicy


@pytest.fixture(autouse=True)
def _config_env(monkeypatch: pytest.MonkeyPatch, tmp_path):
    """Point the resolved customize path at a tmp dir inside a fake HOME.

    Using MAGI_CUSTOMIZE mirrors the hosted PVC relocation and gives every test
    a deterministic protected directory to target.
    """
    magi_dir = tmp_path / ".magi"
    magi_dir.mkdir(parents=True, exist_ok=True)
    cj = magi_dir / "customize.json"
    cj.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(cj))
    monkeypatch.delenv("MAGI_CONFIG", raising=False)
    monkeypatch.delenv("MAGI_RUNTIME_PROFILE", raising=False)
    return magi_dir


def _bash_manifest() -> ToolManifest:
    from magi_agent.tools.catalog import core_tool_manifests

    return {m.name: m for m in core_tool_manifests()}["Bash"]


def _file_manifest(name: str) -> ToolManifest:
    from magi_agent.tools.catalog import core_tool_manifests

    manifests = {m.name: m for m in core_tool_manifests()}
    if name in manifests:
        return manifests[name]
    return ToolManifest(
        name=name,
        description=f"{name} write tool",
        kind="native",
        source=ToolSource(kind="native-plugin", package="openmagi.core"),
        permission="write",
        input_schema={"type": "object", "additionalProperties": True},
        timeout_ms=0,
        side_effect_class="workspace",
        parallel_safety="unsafe",
        mutates_workspace=True,
    )


def _ctx(scope: object | None = None) -> ToolContext:
    return ToolContext(botId="bot", sessionId="s1", turnId="t1", permissionScope=scope)


def _decide(manifest: ToolManifest, arguments: dict[str, object], scope: object | None = None):
    return ToolPermissionPolicy().decide(manifest, arguments, _ctx(scope), mode="act")


def _reasons(decision) -> tuple:
    return decision.metadata.get("reasonCodes") or ()


# --------------------------------------------------------------------------- #
# Shell append to the resolved customize path denies, even under bypass       #
# --------------------------------------------------------------------------- #
def test_shell_append_to_customize_denied(_config_env) -> None:
    magi_dir = _config_env
    cmd = f"echo '{{}}' >> {magi_dir}/customize.json"
    decision = _decide(_bash_manifest(), {"command": cmd})
    assert decision.action == "deny"
    assert "protected_config_write_denied" in _reasons(decision)


def test_shell_append_to_customize_denied_under_bypass(_config_env) -> None:
    magi_dir = _config_env
    cmd = f"echo 'x' >> {magi_dir}/customize.json"
    decision = _decide(_bash_manifest(), {"command": cmd}, scope={"mode": "bypass"})
    assert decision.action == "deny"
    assert "protected_config_write_denied" in _reasons(decision)


def test_shell_write_anywhere_in_magi_dir_denied(_config_env) -> None:
    # OQ-7: the WHOLE ~/.magi directory is protected, not just customize.json.
    magi_dir = _config_env
    cmd = f"echo 'x' > {magi_dir}/some-other-file.json"
    decision = _decide(_bash_manifest(), {"command": cmd}, scope={"mode": "bypass"})
    assert decision.action == "deny"
    assert "protected_config_write_denied" in _reasons(decision)


def test_shell_read_of_customize_still_allowed(_config_env) -> None:
    # Reads are not writes: a plain cat must not be denied by config protection.
    magi_dir = _config_env
    cmd = f"cat {magi_dir}/customize.json"
    decision = _decide(_bash_manifest(), {"command": cmd})
    assert "protected_config_write_denied" not in _reasons(decision)


# --------------------------------------------------------------------------- #
# Native file tools (FileWrite/FileEdit/PatchApply) deny writes into ~/.magi  #
# --------------------------------------------------------------------------- #
def test_file_write_to_customize_denied(_config_env) -> None:
    magi_dir = _config_env
    decision = _decide(
        _file_manifest("FileWrite"),
        {"path": f"{magi_dir}/customize.json", "content": "{}"},
        scope={"mode": "bypass"},
    )
    assert decision.action == "deny"
    assert "protected_config_write_denied" in _reasons(decision)


def test_file_edit_to_customize_denied(_config_env) -> None:
    magi_dir = _config_env
    decision = _decide(
        _file_manifest("FileEdit"),
        {"path": f"{magi_dir}/customize.json", "oldString": "a", "newString": "b"},
        scope={"mode": "bypass"},
    )
    assert decision.action == "deny"
    assert "protected_config_write_denied" in _reasons(decision)


def test_patch_apply_to_customize_denied(_config_env) -> None:
    magi_dir = _config_env
    patch = (
        f"*** Begin Patch\n*** Update File: {magi_dir}/customize.json\n"
        "@@\n-old\n+new\n*** End Patch\n"
    )
    decision = _decide(
        _file_manifest("PatchApply"),
        {"patch": patch},
        scope={"mode": "bypass"},
    )
    assert decision.action == "deny"
    assert "protected_config_write_denied" in _reasons(decision)


# --------------------------------------------------------------------------- #
# Attribution: the deny is attributed to system_safety.config_protection      #
# --------------------------------------------------------------------------- #
def test_attribution_config_protection() -> None:
    from magi_agent.tools.safety_policy_attribution import attribute_safety_decision

    result = attribute_safety_decision("protected_config_write_denied")
    assert result is not None
    assert result["policyId"] == "system_safety"
    assert result["ruleId"] == "system_safety.config_protection"


def test_config_protection_member_in_system_safety_policy() -> None:
    from magi_agent.customize.policies import BUILTIN_POLICIES

    pol = next((p for p in BUILTIN_POLICIES if p.policy_id == "system_safety"), None)
    assert pol is not None
    assert "system_safety.config_protection" in pol.rule_ids
