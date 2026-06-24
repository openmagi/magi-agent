"""Tests for F4: operator-authored ``capability_scope`` custom rule applied
to the spawned child's resolved toolset.

The capability_scope filter sits at the spawn boundary in
``RealLocalChildRunner._resolve_turn_toolset``, between the parent_cap
intersection (``MAGI_SUBAGENT_TOOL_TIGHTEN_ONLY_ENABLED``) and the
orchestrator's per-task ``allowedTools``/``spawn_cap`` grants
(``MAGI_SPAWN_RECIPE_CAP_ENABLED``). The filter is triple-gated:

* strict ``MAGI_CUSTOMIZE_CAPABILITY_SCOPE_ENABLED`` (this PR's new ``_b`` flag)
* profile-aware ``MAGI_CUSTOMIZE_VERIFICATION_ENABLED``
* profile-aware ``MAGI_CUSTOMIZE_CUSTOM_RULES_ENABLED``

Tests are hermetic: no real model / provider key. Customize overrides are
written to a tmp ``customize.json`` and ``MAGI_CUSTOMIZE`` points the runtime
at it. The two profile-aware flags resolve ON under the ``full`` profile;
tests stamp ``MAGI_RUNTIME_PROFILE=full`` so the gate triple lights up.

The runtime integration is also fail-open: a broken overrides file must never
block a spawn. Flag-OFF byte-identity guarantees pre-F4 behaviour is preserved
on every fresh install.
"""
from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from pathlib import Path

import pytest

import magi_agent.cli.tool_runtime as tool_runtime_mod
from magi_agent.customize.verification_policy import CustomizeVerificationPolicy
from magi_agent.runtime.child_runner_boundary import ChildTaskRequest
from magi_agent.runtime.child_runner_live import RealLocalChildRunner

# ---------------------------------------------------------------------------
# Env isolation
# ---------------------------------------------------------------------------

_PROVIDER_ENV = (
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
    "FIREWORKS_API_KEY",
    "MAGI_PROVIDER",
    "MAGI_MODEL",
    "MAGI_CUSTOMIZE",
    "MAGI_CUSTOMIZE_CAPABILITY_SCOPE_ENABLED",
    "MAGI_CUSTOMIZE_VERIFICATION_ENABLED",
    "MAGI_CUSTOMIZE_CUSTOM_RULES_ENABLED",
    "MAGI_RUNTIME_PROFILE",
    "MAGI_SUBAGENT_TOOL_TIGHTEN_ONLY_ENABLED",
)


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch, tmp_path) -> None:
    """Hermetic env. The profile-aware customize flags resolve ON under
    ``MAGI_RUNTIME_PROFILE=full`` and OFF under ``safe``/``eval``; tests set
    the profile explicitly per case."""
    for name in _PROVIDER_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("MAGI_CONFIG", str(tmp_path / "absent.toml"))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _request(**overrides: object) -> ChildTaskRequest:
    data: dict[str, object] = {
        "parentExecutionId": "parent-exec-capscope",
        "turnId": "turn-capscope-1",
        "taskId": "task-capscope-1",
        "objective": "Complete delegated subtask.",
        "role": "research",
        "delivery": "return",
    }
    data.update(overrides)
    return ChildTaskRequest(**data)


def _provider_config(api_key: str = "sk-test") -> object:
    from magi_agent.cli.providers import ProviderConfig

    return ProviderConfig(provider="anthropic", model="claude-sonnet-4-6", api_key=api_key)


class _NamedTool:
    def __init__(self, name: str) -> None:
        self.name = name


_FULL_TOOLS = [
    _NamedTool("FileRead"),
    _NamedTool("Glob"),
    _NamedTool("Grep"),
    _NamedTool("GitDiff"),
    _NamedTool("FileWrite"),
    _NamedTool("Bash"),
    _NamedTool("Edit"),
    _NamedTool("shell_exec"),
]


class _FakeRunner:
    async def run_async(self, **kwargs: object) -> AsyncGenerator[object, None]:
        if False:  # pragma: no cover — generator type hint only
            yield None


def _patch_full_tools(monkeypatch) -> None:
    def _fake_build_tools(**kwargs: object) -> list[_NamedTool]:
        return list(_FULL_TOOLS)

    monkeypatch.setattr(tool_runtime_mod, "build_cli_adk_tools", _fake_build_tools)


def _write_overrides(tmp_path: Path, rules: list[dict[str, object]]) -> Path:
    """Persist a customize.json under tmp_path and return its absolute path.

    The runtime ``customize.store.load_overrides`` will read from this file
    when ``MAGI_CUSTOMIZE`` is set in the env, so the spawn-time gate sees
    the rules without depending on the user's real ``~/.magi`` dir.
    """
    cfile = tmp_path / "customize.json"
    payload = {
        "verification": {
            "harness_presets": [],
            "recipes": [],
            "hooks": {},
            "modes": {},
            "preset_overrides": {},
            "custom_rules": rules,
            "seam_specs": [],
        },
        "tools": {},
        "user_rules": "",
        "control_plane": {},
    }
    cfile.write_text(json.dumps(payload), encoding="utf-8")
    return cfile


def _capscope_rule(
    *,
    rule_id: str,
    deny_tools: list[str] | None = None,
    max_class: str | None = None,
    enabled: bool = True,
) -> dict[str, object]:
    payload: dict[str, object] = {"tightenOnly": True}
    if deny_tools is not None:
        payload["denyTools"] = deny_tools
    if max_class is not None:
        payload["maxPermissionClass"] = max_class
    return {
        "id": rule_id,
        "scope": "always",
        "enabled": enabled,
        "what": {"kind": "capability_scope", "payload": payload},
        "firesAt": "spawn",
        "action": "audit",
    }


# ---------------------------------------------------------------------------
# Accessor unit tests (verification_policy)
# ---------------------------------------------------------------------------


def test_accessor_returns_enabled_capability_scope_rules() -> None:
    policy = CustomizeVerificationPolicy.from_overrides(
        {
            "verification": {
                "custom_rules": [
                    _capscope_rule(rule_id="r1", deny_tools=["shell_exec"]),
                    _capscope_rule(
                        rule_id="r2", max_class="readonly", enabled=False
                    ),
                    _capscope_rule(rule_id="r3", max_class="safe_write"),
                ]
            }
        }
    )
    rules = policy.enabled_capability_scope_rules()
    ids = [r["id"] for r in rules]
    # Disabled rule r2 dropped; stored order preserved otherwise.
    assert ids == ["r1", "r3"]


def test_accessor_skips_non_capability_scope_kinds() -> None:
    policy = CustomizeVerificationPolicy.from_overrides(
        {
            "verification": {
                "custom_rules": [
                    {
                        "id": "tp1",
                        "scope": "always",
                        "enabled": True,
                        "what": {
                            "kind": "tool_perm",
                            "payload": {"match": {"tool": "Bash"}, "decision": "deny"},
                        },
                        "firesAt": "before_tool_use",
                        "action": "block",
                    },
                    _capscope_rule(rule_id="r1", deny_tools=["Bash"]),
                ]
            }
        }
    )
    rules = policy.enabled_capability_scope_rules()
    assert [r["id"] for r in rules] == ["r1"]


def test_accessor_skips_capability_scope_with_wrong_firesAt() -> None:
    # A capability_scope rule must declare ``firesAt: "spawn"`` to be picked
    # up; a misauthored fire-point silently no-ops rather than running at the
    # wrong slot (e.g. pre_final).
    bad = _capscope_rule(rule_id="bad", deny_tools=["Bash"])
    bad["firesAt"] = "pre_final"
    policy = CustomizeVerificationPolicy.from_overrides(
        {"verification": {"custom_rules": [bad]}}
    )
    assert policy.enabled_capability_scope_rules() == []


# ---------------------------------------------------------------------------
# Runtime integration tests (_resolve_turn_toolset)
# ---------------------------------------------------------------------------


def test_capability_scope_flag_on_deny_tools_removes_named_tool(
    monkeypatch, tmp_path
) -> None:
    """Flag ON + ``denyTools=["shell_exec"]`` removes shell_exec from the spawn
    toolset; the rest of the profile is untouched."""
    _patch_full_tools(monkeypatch)
    cfile = _write_overrides(
        tmp_path,
        [_capscope_rule(rule_id="r-shell", deny_tools=["shell_exec"])],
    )
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(cfile))
    monkeypatch.setenv("MAGI_RUNTIME_PROFILE", "full")
    monkeypatch.setenv("MAGI_CUSTOMIZE_CAPABILITY_SCOPE_ENABLED", "1")

    runner = RealLocalChildRunner(
        provider_config=_provider_config(),
        toolset_profile="full",
        runner=_FakeRunner(),
    )
    tools, _collector = runner._resolve_turn_toolset(
        "session-capscope-on", request=_request()
    )
    tool_names = {t.name for t in tools}

    assert "shell_exec" not in tool_names, "denyTools entry not removed"
    # Everything else preserved
    assert tool_names == {t.name for t in _FULL_TOOLS} - {"shell_exec"}


def test_capability_scope_flag_off_byte_identical(monkeypatch, tmp_path) -> None:
    """Flag OFF (default) → no filter applied even when rules are authored.

    Byte-identity proof: the full profile toolset reaches the caller unchanged.
    """
    _patch_full_tools(monkeypatch)
    cfile = _write_overrides(
        tmp_path,
        [_capscope_rule(rule_id="r-shell", deny_tools=["shell_exec"])],
    )
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(cfile))
    monkeypatch.setenv("MAGI_RUNTIME_PROFILE", "full")
    # F4 flag explicitly OFF.
    monkeypatch.delenv("MAGI_CUSTOMIZE_CAPABILITY_SCOPE_ENABLED", raising=False)

    runner = RealLocalChildRunner(
        provider_config=_provider_config(),
        toolset_profile="full",
        runner=_FakeRunner(),
    )
    tools, _collector = runner._resolve_turn_toolset(
        "session-capscope-off", request=_request()
    )
    tool_names = {t.name for t in tools}

    # Full profile preserved — flag-OFF is a no-op even with a deny rule on disk.
    assert tool_names == {t.name for t in _FULL_TOOLS}
    assert "shell_exec" in tool_names


def test_capability_scope_max_permission_class_narrowing_observable(
    monkeypatch, tmp_path
) -> None:
    """A rule that only sets ``maxPermissionClass=readonly`` (no denyTools)
    must observably narrow the spawned child's toolset to the canonical
    read-only inspection tools (FileRead / Glob / Grep / GitDiff) — NOT just
    compute a label that the runtime discards.

    BLOCKER fix (F4 honesty contract): pre-fix the runtime captured the
    narrowed class as ``_capped_class`` and threw it away. The dashboard
    showed ``Subagents capped at readonly permission class`` but the child
    still got the unrestricted profile toolset. This test asserts that
    `apply_permission_class_filter` is wired into ``_resolve_turn_toolset``
    so the UI promise is real.
    """
    from magi_agent.tools.local_readonly import LOCAL_READONLY_TOOL_NAMES

    _patch_full_tools(monkeypatch)
    cfile = _write_overrides(
        tmp_path,
        [_capscope_rule(rule_id="r-cap", max_class="readonly")],
    )
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(cfile))
    monkeypatch.setenv("MAGI_RUNTIME_PROFILE", "full")
    monkeypatch.setenv("MAGI_CUSTOMIZE_CAPABILITY_SCOPE_ENABLED", "1")

    runner = RealLocalChildRunner(
        provider_config=_provider_config(),
        toolset_profile="full",
        runner=_FakeRunner(),
    )
    tools, _collector = runner._resolve_turn_toolset(
        "session-capscope-cap", request=_request()
    )
    tool_names = {t.name for t in tools}

    # Observable narrowing: only readonly tools survive the cap.
    expected_readonly = set(LOCAL_READONLY_TOOL_NAMES) & {
        t.name for t in _FULL_TOOLS
    }
    assert tool_names == expected_readonly, (
        f"maxPermissionClass=readonly must restrict the spawn toolset to "
        f"{sorted(expected_readonly)}; got {sorted(tool_names)}"
    )
    # Write/exec capabilities specifically excluded.
    assert "Bash" not in tool_names
    assert "shell_exec" not in tool_names
    assert "FileWrite" not in tool_names
    assert "Edit" not in tool_names


def test_capability_scope_max_permission_class_safe_write_filter(
    monkeypatch, tmp_path
) -> None:
    """``maxPermissionClass=safe_write`` must narrow to readonly ∪ edit-class
    tools (file mutation OK; Bash / shell_exec excluded). Mirrors the
    runtime's apply_permission_class_filter mapping."""
    from magi_agent.cli.permissions import EDIT_CLASS_TOOLS
    from magi_agent.tools.local_readonly import LOCAL_READONLY_TOOL_NAMES

    _patch_full_tools(monkeypatch)
    cfile = _write_overrides(
        tmp_path,
        [_capscope_rule(rule_id="r-safe", max_class="safe_write")],
    )
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(cfile))
    monkeypatch.setenv("MAGI_RUNTIME_PROFILE", "full")
    monkeypatch.setenv("MAGI_CUSTOMIZE_CAPABILITY_SCOPE_ENABLED", "1")

    runner = RealLocalChildRunner(
        provider_config=_provider_config(),
        toolset_profile="full",
        runner=_FakeRunner(),
    )
    tools, _collector = runner._resolve_turn_toolset(
        "session-capscope-safe", request=_request()
    )
    tool_names = {t.name for t in tools}

    allowed = (set(LOCAL_READONLY_TOOL_NAMES) | set(EDIT_CLASS_TOOLS)) & {
        t.name for t in _FULL_TOOLS
    }
    assert tool_names == allowed
    # Edit tools survive under safe_write.
    assert "FileWrite" in tool_names
    assert "Edit" in tool_names
    # Exec tools dropped under safe_write.
    assert "Bash" not in tool_names
    assert "shell_exec" not in tool_names


def test_capability_scope_max_permission_class_off_no_filter(
    monkeypatch, tmp_path
) -> None:
    """Flag-OFF with a maxPermissionClass rule on disk MUST be a no-op:
    the full profile_tools reach the caller (byte-identical to pre-F4)."""
    _patch_full_tools(monkeypatch)
    cfile = _write_overrides(
        tmp_path,
        [_capscope_rule(rule_id="r-cap", max_class="readonly")],
    )
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(cfile))
    monkeypatch.setenv("MAGI_RUNTIME_PROFILE", "full")
    monkeypatch.delenv("MAGI_CUSTOMIZE_CAPABILITY_SCOPE_ENABLED", raising=False)

    runner = RealLocalChildRunner(
        provider_config=_provider_config(),
        toolset_profile="full",
        runner=_FakeRunner(),
    )
    tools, _collector = runner._resolve_turn_toolset(
        "session-capscope-cap-off", request=_request()
    )
    tool_names = {t.name for t in tools}
    # Full profile preserved on flag-OFF, including Bash/shell_exec.
    assert tool_names == {t.name for t in _FULL_TOOLS}


def test_capability_scope_multiple_rules_compose(monkeypatch, tmp_path) -> None:
    """Two enabled rules each narrow independently — denyTools UNION is applied
    and the narrower permission class wins (observably filtering the toolset)."""
    from magi_agent.cli.permissions import EDIT_CLASS_TOOLS
    from magi_agent.tools.local_readonly import LOCAL_READONLY_TOOL_NAMES

    _patch_full_tools(monkeypatch)
    cfile = _write_overrides(
        tmp_path,
        [
            _capscope_rule(rule_id="r-shell", deny_tools=["shell_exec"]),
            _capscope_rule(
                rule_id="r-write",
                deny_tools=["FileWrite", "Edit"],
                max_class="safe_write",
            ),
        ],
    )
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(cfile))
    monkeypatch.setenv("MAGI_RUNTIME_PROFILE", "full")
    monkeypatch.setenv("MAGI_CUSTOMIZE_CAPABILITY_SCOPE_ENABLED", "1")

    runner = RealLocalChildRunner(
        provider_config=_provider_config(),
        toolset_profile="full",
        runner=_FakeRunner(),
    )
    tools, _collector = runner._resolve_turn_toolset(
        "session-capscope-multi", request=_request()
    )
    tool_names = {t.name for t in tools}

    # UNION of denyTools applied; safe_write class ALSO filters to
    # (readonly ∪ EDIT_CLASS_TOOLS) so Bash is dropped too.
    safe_write_allowed = (
        set(LOCAL_READONLY_TOOL_NAMES) | set(EDIT_CLASS_TOOLS)
    )
    expected = (
        {t.name for t in _FULL_TOOLS}
        - {"shell_exec", "FileWrite", "Edit"}
    ) & safe_write_allowed
    assert tool_names == expected
    # Explicit assertions on the security-critical drops:
    assert "shell_exec" not in tool_names  # denyTools
    assert "FileWrite" not in tool_names   # denyTools
    assert "Edit" not in tool_names        # denyTools
    assert "Bash" not in tool_names        # excluded by safe_write class


def test_capability_scope_fail_open_on_broken_overrides(monkeypatch, tmp_path) -> None:
    """A broken customize.json must NEVER block a spawn — the runtime falls
    back to the unfiltered profile_tools so the agent stays usable."""
    _patch_full_tools(monkeypatch)
    cfile = tmp_path / "customize.json"
    cfile.write_text("{this is not json", encoding="utf-8")
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(cfile))
    monkeypatch.setenv("MAGI_RUNTIME_PROFILE", "full")
    monkeypatch.setenv("MAGI_CUSTOMIZE_CAPABILITY_SCOPE_ENABLED", "1")

    runner = RealLocalChildRunner(
        provider_config=_provider_config(),
        toolset_profile="full",
        runner=_FakeRunner(),
    )
    tools, _collector = runner._resolve_turn_toolset(
        "session-capscope-broken", request=_request()
    )
    tool_names = {t.name for t in tools}

    # Broken overrides → load_overrides returns the empty default, no rules
    # match, the gate is a no-op. Either way the spawn proceeds.
    assert tool_names == {t.name for t in _FULL_TOOLS}
