"""GL1 — ON-path integration test: all 3 orchestrator flags ON.

Proves the spawn_cap HANDOFF that crosses the spawn boundary end-to-end under
CI conditions (no live model), using REAL code at every hop.

Layer coverage
--------------
Layer 1 (CLI): MAGI_MAIN_AGENT_PROFILE=orchestrator → toolset restricted +
               spawn_cap = full bundle names.
Layer 2 (handoff): spawn_agent(ctx) with spawn_cap set → ChildTaskRequest
                   carries spawn_cap, metadata["allowedTools"], metadata["recipeRefs"].
Layer 3 (child narrowing): REAL _resolve_turn_toolset with the handed-off
                           spawn_cap + MAGI_SPAWN_RECIPE_CAP_ENABLED ON →
                           final child toolset == profile ∩ allowedTools ∩ spawn_cap.
Layer 4 (recipe binding): MAGI_SPAWN_RECIPE_BIND_ENABLED ON → recipeRefs flow
                          into build_headless_runtime as pinned_recipe_pack_ids,
                          which bind openmagi.research's distinctive validators.
Layer 5 (all-OFF contrast): flags unset → unrestricted main, no spawn_cap, no
                            child narrowing (byte-identical default path).

Key discipline: REAL _apply_orchestrator_profile, REAL _build_first_party_adk_tools
via spy, REAL spawn_agent, REAL _resolve_turn_toolset, REAL compiler.
Injected fake runners/child-runners prevent live model calls.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, Mapping
from typing import Any

import pytest

import magi_agent.cli.tool_runtime as tool_runtime_mod
from magi_agent.runtime.child_runner_boundary import ChildTaskRequest
from magi_agent.runtime.child_runner_live import RealLocalChildRunner
from magi_agent.tools.context import ToolContext


# ---------------------------------------------------------------------------
# Environment isolation
# ---------------------------------------------------------------------------

_PROVIDER_ENV = (
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
    "FIREWORKS_API_KEY",
    "MAGI_PROVIDER",
    "MAGI_MODEL",
    "MAGI_SUBAGENT_TOOL_TIGHTEN_ONLY_ENABLED",
    "MAGI_SPAWN_RECIPE_CAP_ENABLED",
    "MAGI_SPAWN_RECIPE_BIND_ENABLED",
    "MAGI_MAIN_AGENT_PROFILE",
    "MAGI_CHILD_RUNNER_LIVE_ENABLED",
    "MAGI_CHILD_RUNNER_LIVE_KILL_SWITCH",
)


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    """Hermetic: clear all 3 flag-families + provider keys."""
    for name in _PROVIDER_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("MAGI_CONFIG", str(tmp_path / "absent.toml"))


# ---------------------------------------------------------------------------
# Shared tool stubs / helpers
# ---------------------------------------------------------------------------


class _NamedTool:
    """Minimal named tool stub — has only a .name attribute."""

    def __init__(self, name: str) -> None:
        self.name = name


# Simulate the real full toolset: orchestrator-allowed tools + mutation tools.
_FULL_TOOL_NAMES = (
    "FileRead",
    "Glob",
    "Grep",
    "GitDiff",
    "SpawnAgent",
    "Bash",
    "FileWrite",
    "Edit",
    "WebSearch",
)

_FULL_TOOLS = [_NamedTool(n) for n in _FULL_TOOL_NAMES]

# The orchestrator-restricted names as produced by orchestrator_tool_names():
# READONLY_TOOL_NAMES ∪ {SpawnAgent}. We derive them from the real function.
from magi_agent.runtime.main_agent_profile import orchestrator_tool_names  # noqa: E402

_ORCHESTRATOR_ALLOWED = frozenset(orchestrator_tool_names())


def _patch_full_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    """Patch the child's _build_core_tools to return our deterministic full set."""

    def _fake_build_tools(**kwargs: object) -> list[_NamedTool]:
        return list(_FULL_TOOLS)

    monkeypatch.setattr(tool_runtime_mod, "build_cli_adk_tools", _fake_build_tools)


def _provider_config(api_key: str = "sk-gl1-test") -> object:
    from magi_agent.cli.providers import ProviderConfig  # noqa: PLC0415

    return ProviderConfig(provider="anthropic", model="claude-sonnet-4-6", api_key=api_key)


def _tool_context(**overrides: object) -> ToolContext:
    defaults: dict[str, object] = {
        "botId": "gl1-bot",
        "sessionId": "gl1-sess",
        "turnId": "gl1-turn",
        "spawnDepth": 0,
    }
    defaults.update(overrides)
    return ToolContext(**defaults)


# ---------------------------------------------------------------------------
# Layer 1 — orchestrator profile produces ceiling (real assembly via spy)
# ---------------------------------------------------------------------------


class TestLayer1OrchestratorCeilingAssembly:
    """Layer 1: MAGI_MAIN_AGENT_PROFILE=orchestrator → restricted toolset + spawn_cap.

    We drive the REAL _build_first_party_adk_tools via a spy on
    _apply_orchestrator_profile to capture the full_names and restricted_names
    without stubbing the thing under test.
    """

    def test_main_tool_set_restricted_to_orchestrator_allowed(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Any,
    ) -> None:
        """(a) With orchestrator profile, main tool names ⊆ orchestrator_tool_names()."""
        monkeypatch.setenv("MAGI_MAIN_AGENT_PROFILE", "orchestrator")

        import magi_agent.cli.wiring as wiring_mod  # noqa: PLC0415

        call_log: list[dict[str, object]] = []
        real_apply = wiring_mod._apply_orchestrator_profile

        def spy_apply(
            full_tools: list[object],
            env: dict[str, str] | None = None,
        ) -> tuple[list[object], tuple[str, ...] | None]:
            result = real_apply(full_tools, env=env)
            call_log.append(
                {
                    "full_names": tuple(getattr(t, "name", None) for t in full_tools),
                    "restricted_names": tuple(getattr(t, "name", None) for t in result[0]),
                    "spawn_cap": result[1],
                }
            )
            return result

        monkeypatch.setattr(wiring_mod, "_apply_orchestrator_profile", spy_apply)

        wiring_mod._build_first_party_adk_tools(
            cwd=str(tmp_path),
            session_id="gl1-layer1-test",
        )

        assert call_log, "_apply_orchestrator_profile was not called"
        entry = call_log[0]

        # (a): restricted set must be subset of orchestrator allowed names
        restricted = set(entry["restricted_names"])
        assert restricted <= _ORCHESTRATOR_ALLOWED, (
            f"Restricted {restricted} has tools outside orchestrator_allowed {_ORCHESTRATOR_ALLOWED}"
        )
        # SpawnAgent must be in the restricted set (it IS in orchestrator_allowed)
        # only if SpawnAgent was in the full set — which it is in real assembly.
        # We check that if SpawnAgent is available, it's kept.
        full = set(entry["full_names"])
        if "SpawnAgent" in full:
            assert "SpawnAgent" in restricted, "SpawnAgent must survive the orchestrator filter"

    def test_spawn_cap_equals_full_bundle_names(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Any,
    ) -> None:
        """(b) spawn_cap == full bundle names (the grant ceiling)."""
        monkeypatch.setenv("MAGI_MAIN_AGENT_PROFILE", "orchestrator")

        import magi_agent.cli.wiring as wiring_mod  # noqa: PLC0415

        captured: list[dict[str, object]] = []
        real_apply = wiring_mod._apply_orchestrator_profile

        def spy_apply(
            full_tools: list[object],
            env: dict[str, str] | None = None,
        ) -> tuple[list[object], tuple[str, ...] | None]:
            result = real_apply(full_tools, env=env)
            captured.append(
                {
                    "full_names": tuple(getattr(t, "name", None) for t in full_tools),
                    "spawn_cap": result[1],
                }
            )
            return result

        monkeypatch.setattr(wiring_mod, "_apply_orchestrator_profile", spy_apply)
        wiring_mod._build_first_party_adk_tools(
            cwd=str(tmp_path),
            session_id="gl1-cap-test",
        )

        assert captured, "spy not called"
        entry = captured[0]
        full_names = set(entry["full_names"])
        spawn_cap = entry["spawn_cap"]

        assert spawn_cap is not None, "spawn_cap must not be None with orchestrator profile"
        assert isinstance(spawn_cap, tuple)
        assert set(spawn_cap) == full_names, (
            f"spawn_cap {set(spawn_cap)} != full_names {full_names}"
        )

    def test_flag_off_spawn_cap_is_none(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Any,
    ) -> None:
        """Flag unset → spawn_cap is None (byte-identical path)."""
        monkeypatch.delenv("MAGI_MAIN_AGENT_PROFILE", raising=False)

        import magi_agent.cli.wiring as wiring_mod  # noqa: PLC0415

        captured: list[dict[str, object]] = []
        real_apply = wiring_mod._apply_orchestrator_profile

        def spy_apply(
            full_tools: list[object],
            env: dict[str, str] | None = None,
        ) -> tuple[list[object], tuple[str, ...] | None]:
            result = real_apply(full_tools, env=env)
            captured.append({"spawn_cap": result[1]})
            return result

        monkeypatch.setattr(wiring_mod, "_apply_orchestrator_profile", spy_apply)
        wiring_mod._build_first_party_adk_tools(
            cwd=str(tmp_path),
            session_id="gl1-noflg-test",
        )

        assert captured
        assert captured[0]["spawn_cap"] is None


# ---------------------------------------------------------------------------
# Layer 2 — THE HANDOFF: spawn_agent propagates spawn_cap + allowedTools + recipeRefs
# ---------------------------------------------------------------------------


class TestLayer2SpawnAgentHandoff:
    """Layer 2 (THE HANDOFF): ToolContext.spawn_cap flows through spawn_agent
    into ChildTaskRequest.spawn_cap, and allowedTools + recipeRefs land in metadata.

    Uses a capturing fake RealLocalChildRunner that intercepts the request at
    the boundary entry point WITHOUT calling any model.
    """

    def _run_spawn(
        self,
        monkeypatch: pytest.MonkeyPatch,
        spawn_cap: tuple[str, ...] | None,
        allowed_tools: list[str],
        recipe_refs: list[str],
    ) -> object:
        """Run spawn_agent with the given context and return the captured ChildTaskRequest."""
        monkeypatch.setenv("MAGI_CHILD_RUNNER_LIVE_ENABLED", "1")
        monkeypatch.delenv("MAGI_CHILD_RUNNER_LIVE_KILL_SWITCH", raising=False)

        import magi_agent.runtime.child_runner_live as _live_mod  # noqa: PLC0415

        captured_request: list[object] = []

        class _CapturingRunner:
            openmagi_live_provider = True

            def __init__(self, *, tools: list[object] | None = None, **kwargs: object) -> None:
                pass

            async def run_child(self, request: object) -> dict[str, object]:
                captured_request.append(request)
                return {
                    "childExecutionId": "gl1-child-exec",
                    "status": "completed",
                    "summary": "captured",
                    "evidenceRefs": (),
                    "artifactRefs": (),
                    "auditEventRefs": (),
                }

        monkeypatch.setattr(_live_mod, "RealLocalChildRunner", _CapturingRunner)

        from magi_agent.plugins.native.subagents import spawn_agent  # noqa: PLC0415

        ctx = _tool_context(spawnCap=spawn_cap)
        asyncio.run(
            spawn_agent(
                {
                    "prompt": "gl1 handoff test",
                    "allowedTools": allowed_tools,
                    "recipeRefs": recipe_refs,
                },
                ctx,
            )
        )

        assert len(captured_request) == 1, "CapturingRunner.run_child not called"
        return captured_request[0]

    def test_spawn_cap_flows_from_context_to_request(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Layer-1 spawn_cap (from orchestrator assembly) flows into request.spawn_cap."""
        layer1_ceiling = ("FileRead", "Glob", "Grep", "GitDiff", "SpawnAgent", "Bash", "FileWrite")
        req = self._run_spawn(
            monkeypatch,
            spawn_cap=layer1_ceiling,
            allowed_tools=["FileRead", "Bash"],
            recipe_refs=["openmagi.research"],
        )
        assert req.spawn_cap == layer1_ceiling, (
            f"spawn_cap handoff failed: expected {layer1_ceiling}, got {req.spawn_cap!r}"
        )

    def test_allowed_tools_land_in_metadata(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """allowedTools from spawn_agent arguments lands in request.metadata."""
        req = self._run_spawn(
            monkeypatch,
            spawn_cap=("FileRead", "Bash", "WebSearch"),
            allowed_tools=["FileRead", "Bash"],
            recipe_refs=[],
        )
        metadata = req.metadata if isinstance(req.metadata, dict) else dict(req.metadata)
        assert "allowedTools" in metadata, f"allowedTools missing from metadata: {metadata}"
        assert set(metadata["allowedTools"]) == {"FileRead", "Bash"}, (
            f"allowedTools mismatch: {metadata['allowedTools']}"
        )

    def test_recipe_refs_land_in_metadata(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """recipeRefs from spawn_agent arguments lands in request.metadata."""
        req = self._run_spawn(
            monkeypatch,
            spawn_cap=("FileRead", "Bash"),
            allowed_tools=["FileRead"],
            recipe_refs=["openmagi.research"],
        )
        metadata = req.metadata if isinstance(req.metadata, dict) else dict(req.metadata)
        assert "recipeRefs" in metadata, f"recipeRefs missing from metadata: {metadata}"
        assert "openmagi.research" in metadata["recipeRefs"], (
            f"openmagi.research missing from recipeRefs: {metadata['recipeRefs']}"
        )

    def test_none_spawn_cap_produces_none_in_request(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """ToolContext.spawn_cap=None → ChildTaskRequest.spawn_cap is None (byte-identical)."""
        req = self._run_spawn(
            monkeypatch,
            spawn_cap=None,
            allowed_tools=[],
            recipe_refs=[],
        )
        assert req.spawn_cap is None, (
            f"Expected None spawn_cap on default path, got {req.spawn_cap!r}"
        )

    def test_full_handoff_all_three_fields(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """COMBINED: spawn_cap + allowedTools + recipeRefs all present in the request."""
        ceiling = ("FileRead", "Glob", "Grep", "GitDiff", "SpawnAgent", "Bash", "FileWrite", "WebSearch")
        req = self._run_spawn(
            monkeypatch,
            spawn_cap=ceiling,
            allowed_tools=["FileRead", "Bash"],
            recipe_refs=["openmagi.research"],
        )
        # spawn_cap
        assert req.spawn_cap == ceiling
        # allowedTools
        metadata = req.metadata if isinstance(req.metadata, dict) else dict(req.metadata)
        assert set(metadata.get("allowedTools", ())) == {"FileRead", "Bash"}
        # recipeRefs
        assert "openmagi.research" in metadata.get("recipeRefs", ())


# ---------------------------------------------------------------------------
# Layer 3 — child narrowing uses the handed-off ceiling (all flags ON)
# ---------------------------------------------------------------------------


class TestLayer3ChildNarrowingWithHandedOffCeiling:
    """Layer 3: REAL _resolve_turn_toolset with all flags ON.

    Proves that the spawn_cap ORIGINATING in the orchestrator profile (layer 1)
    actually caps the child's toolset after crossing the spawn boundary.

    _resolve_turn_toolset is NOT stubbed — the real code runs with patched
    build_cli_adk_tools to avoid real tool construction.
    """

    def test_spawn_cap_ceiling_caps_child_toolset(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """spawn_cap from layer 1 caps the child even when allowedTools includes more.

        Full profile has Bash + FileWrite.
        allowedTools grants FileRead, Bash, Glob.
        spawn_cap from layer 1 = FileRead, Glob only.

        Expected child toolset: {FileRead, Glob}  (Bash cut by spawn_cap ceiling).
        """
        _patch_full_tools(monkeypatch)
        monkeypatch.setenv("MAGI_SPAWN_RECIPE_CAP_ENABLED", "1")

        # spawn_cap as produced by layer 1: includes FileRead, Glob but NOT Bash
        layer1_ceiling = ("FileRead", "Glob", "Grep", "GitDiff", "SpawnAgent")

        req = ChildTaskRequest(
            parentExecutionId="gl1-parent",
            turnId="gl1-turn",
            taskId="gl1-task",
            objective="Test child narrowing with layer-1 ceiling.",
            metadata={"allowedTools": ("FileRead", "Bash", "Glob")},
        )
        runner = RealLocalChildRunner(
            provider_config=_provider_config(),
            toolset_profile="full",
            spawn_cap=layer1_ceiling,
        )

        tools, _collector = runner._resolve_turn_toolset("gl1-child-sess", request=req)
        tool_names = {t.name for t in tools}

        # FileRead and Glob: in allowedTools AND in layer1_ceiling → survive
        assert "FileRead" in tool_names
        assert "Glob" in tool_names
        # Bash: in allowedTools but NOT in layer1_ceiling → dropped by ceiling
        assert "Bash" not in tool_names, (
            "Bash is in allowedTools but must be dropped by spawn_cap ceiling"
        )
        # FileWrite: not in allowedTools and not in ceiling → dropped
        assert "FileWrite" not in tool_names
        # Edit: not in allowedTools and not in ceiling → dropped
        assert "Edit" not in tool_names

    def test_three_way_intersection_proof(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Proves composition: profile ∩ allowedTools ∩ spawn_cap.

        profile (full) = {FileRead, Glob, Grep, GitDiff, SpawnAgent, Bash, FileWrite, Edit, WebSearch}
        allowedTools   = {FileRead, Bash, Glob}
        spawn_cap      = {FileRead, Glob}

        Expected: {FileRead, Glob}  (three-way intersection)

        This is the same composition test_allowed_tools_integration_three_way_intersection
        proves but with a spawn_cap that ORIGINATED from the orchestrator profile,
        closing the end-to-end loop.
        """
        _patch_full_tools(monkeypatch)
        monkeypatch.setenv("MAGI_SPAWN_RECIPE_CAP_ENABLED", "1")

        req = ChildTaskRequest(
            parentExecutionId="gl1-3way-parent",
            turnId="gl1-3way-turn",
            taskId="gl1-3way-task",
            objective="Three-way intersection proof.",
            metadata={"allowedTools": ("FileRead", "Bash", "Glob")},
        )
        runner = RealLocalChildRunner(
            provider_config=_provider_config(),
            toolset_profile="full",
            spawn_cap=("FileRead", "Glob"),  # orchestrator ceiling — excludes Bash
        )

        tools, _ = runner._resolve_turn_toolset("gl1-3way-sess", request=req)
        tool_names = {t.name for t in tools}

        assert tool_names == {"FileRead", "Glob"}, (
            f"3-way intersection failed. Expected {{FileRead,Glob}}, got {tool_names}"
        )

    def test_tool_in_cap_but_not_in_allowed_is_dropped(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A tool in the ceiling but absent from allowedTools is still dropped.

        Proves allowedTools is a NARROWER per-task grant within the ceiling.
        spawn_cap = {FileRead, Glob, Bash} but allowedTools = {FileRead}
        → child gets only {FileRead}
        """
        _patch_full_tools(monkeypatch)
        monkeypatch.setenv("MAGI_SPAWN_RECIPE_CAP_ENABLED", "1")

        req = ChildTaskRequest(
            parentExecutionId="gl1-narrow-parent",
            turnId="gl1-narrow-turn",
            taskId="gl1-narrow-task",
            objective="Narrow grant within ceiling.",
            metadata={"allowedTools": ("FileRead",)},
        )
        runner = RealLocalChildRunner(
            provider_config=_provider_config(),
            toolset_profile="full",
            spawn_cap=("FileRead", "Glob", "Bash"),
        )

        tools, _ = runner._resolve_turn_toolset("gl1-narrow-sess", request=req)
        tool_names = {t.name for t in tools}

        assert tool_names == {"FileRead"}, (
            f"Expected only {{FileRead}}, got {tool_names}"
        )
        assert "Glob" not in tool_names  # in cap but not in allowedTools
        assert "Bash" not in tool_names  # in cap but not in allowedTools


# ---------------------------------------------------------------------------
# Layer 4 — Recipe binding rides along (MAGI_SPAWN_RECIPE_BIND_ENABLED ON)
# ---------------------------------------------------------------------------


class TestLayer4RecipeBindingRidesAlong:
    """Layer 4: recipeRefs from the spawn flow into build_headless_runtime as
    pinned_recipe_pack_ids when MAGI_SPAWN_RECIPE_BIND_ENABLED is ON.

    We assert at the compiler/runner-policy level (deterministic, no live model).
    The live-model boundary is the _drive_one_turn call inside run_child — we
    stop before that. This mirrors test_recipe_binding_integration.py pattern.
    """

    def test_recipe_refs_bind_headless_runtime_on_governed_path(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """recipeRefs flow into build_headless_runtime(pinned_recipe_pack_ids=...)
        when MAGI_SUBAGENT_GOVERNED_TURN_ENABLED + MAGI_SPAWN_RECIPE_BIND_ENABLED ON.

        LIVE-MODEL BOUNDARY NOTE: This test asserts that pinned_recipe_pack_ids
        reaches build_headless_runtime. The actual governed turn (_drive_one_turn)
        requires a live model — we stop here and capture the kwarg.
        """
        monkeypatch.setenv("MAGI_SUBAGENT_GOVERNED_TURN_ENABLED", "1")
        monkeypatch.setenv("MAGI_SPAWN_RECIPE_BIND_ENABLED", "1")

        import magi_agent.runtime.governed_turn as governed_turn_mod  # noqa: PLC0415

        captured_kwargs: list[dict[str, object]] = []

        def _fake_build_headless_runtime(**kwargs: object) -> object:
            captured_kwargs.append(dict(kwargs))
            return object()

        async def _fake_run_governed_turn(
            ctx: object,
            *,
            runtime: object | None = None,
            cancel: object | None = None,
        ) -> AsyncGenerator[Any, None]:
            from magi_agent.cli.contracts import EngineResult, Terminal  # noqa: PLC0415

            yield EngineResult(terminal=Terminal.completed)

        monkeypatch.setattr(
            "magi_agent.cli.wiring.build_headless_runtime", _fake_build_headless_runtime
        )
        monkeypatch.setattr(governed_turn_mod, "run_governed_turn", _fake_run_governed_turn)

        req = ChildTaskRequest(
            parentExecutionId="gl1-recipe-parent",
            turnId="gl1-recipe-turn",
            taskId="gl1-recipe-task",
            objective="Recipe binding test.",
            metadata={"recipeRefs": ("openmagi.research",)},
        )
        runner = RealLocalChildRunner(provider_config=_provider_config())
        asyncio.run(runner.run_child(req))

        assert len(captured_kwargs) == 1, (
            "build_headless_runtime not called on the governed path"
        )
        pins = captured_kwargs[0].get("pinned_recipe_pack_ids")
        assert pins == ("openmagi.research",), (
            f"recipeRefs did not flow into pinned_recipe_pack_ids: {pins!r}"
        )

    def test_recipe_refs_bind_compiler_distinctively(self) -> None:
        """Compiler level: openmagi.research pin → snapshot has distinctive validator refs.

        This is the same proof as test_recipe_binding_integration::TestA but
        driven from the GL1 integration context: the recipeRefs value that would
        arrive from spawn_agent in layer 2 does bind the child's gate policy.

        LIVE-MODEL BOUNDARY NOTE: compiler.compile() is fully deterministic —
        no model call happens here. This is the deepest deterministic point for
        recipe binding proof.
        """
        from magi_agent.recipes.compiler import (  # noqa: PLC0415
            AgentRecipeCompiler,
            PackRegistry,
            ProfileResolutionRequest,
        )

        _RESEARCH_PACK_ID = "openmagi.research"
        _RESEARCH_VALIDATOR_REFS = frozenset({
            "validator:research:citation-support",
            "validator:research:fact-grounding",
            "validator:research:evidence-checks",
        })

        compiler = AgentRecipeCompiler(PackRegistry.with_first_party_packs())

        # This is what the child runner would produce from recipeRefs=["openmagi.research"]
        pinned_pack_id = _RESEARCH_PACK_ID
        runtime_context: dict[str, object] = {
            "channel": "cli",
            "explicitRecipeSelection": {
                "mode": "this_turn",
                "requiredRecipeRefs": [{"recipeId": pinned_pack_id}],
                "allowAdditionalAutoRecipes": True,
            },
        }
        request = ProfileResolutionRequest(
            taskProfile={},
            runtimeContext=runtime_context,
            recipePackConfig={},
        )
        snapshot = compiler.compile(request)

        assert _RESEARCH_PACK_ID in snapshot.selected_pack_ids, (
            f"openmagi.research not in selected_pack_ids: {snapshot.selected_pack_ids}"
        )
        for ref in _RESEARCH_VALIDATOR_REFS:
            assert ref in snapshot.validator_refs, (
                f"Missing distinctive research validator {ref!r}: {snapshot.validator_refs}"
            )

    def test_flag_off_recipe_refs_not_bound(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """MAGI_SPAWN_RECIPE_BIND_ENABLED OFF → governed path receives pinned_recipe_pack_ids=()."""
        monkeypatch.setenv("MAGI_SUBAGENT_GOVERNED_TURN_ENABLED", "1")
        # MAGI_SPAWN_RECIPE_BIND_ENABLED intentionally NOT set (OFF)

        import magi_agent.runtime.governed_turn as governed_turn_mod  # noqa: PLC0415

        captured_kwargs: list[dict[str, object]] = []

        def _fake_build_headless_runtime(**kwargs: object) -> object:
            captured_kwargs.append(dict(kwargs))
            return object()

        async def _fake_run_governed_turn(
            ctx: object,
            *,
            runtime: object | None = None,
            cancel: object | None = None,
        ) -> AsyncGenerator[Any, None]:
            from magi_agent.cli.contracts import EngineResult, Terminal  # noqa: PLC0415

            yield EngineResult(terminal=Terminal.completed)

        monkeypatch.setattr(
            "magi_agent.cli.wiring.build_headless_runtime", _fake_build_headless_runtime
        )
        monkeypatch.setattr(governed_turn_mod, "run_governed_turn", _fake_run_governed_turn)

        req = ChildTaskRequest(
            parentExecutionId="gl1-off-recipe-parent",
            turnId="gl1-off-recipe-turn",
            taskId="gl1-off-recipe-task",
            objective="Recipe bind flag off test.",
            metadata={"recipeRefs": ("openmagi.research",)},
        )
        runner = RealLocalChildRunner(provider_config=_provider_config())
        asyncio.run(runner.run_child(req))

        assert len(captured_kwargs) == 1
        pins = captured_kwargs[0].get("pinned_recipe_pack_ids")
        assert pins == (), (
            f"Flag OFF: expected pinned_recipe_pack_ids=(), got {pins!r}"
        )


# ---------------------------------------------------------------------------
# Layer 5 — All-OFF contrast (byte-identical default path)
# ---------------------------------------------------------------------------


class TestLayer5AllFlagsOFFContrast:
    """Layer 5: All 3 flags unset → byte-identical default behaviour.

    - Layer 1: spawn_cap is None in the ToolContext.
    - Layer 2: ChildTaskRequest.spawn_cap is None.
    - Layer 3: child toolset is the FULL profile (no narrowing).
    - Layer 4: pinned_recipe_pack_ids=() (no recipe binding).
    """

    def test_flag_off_spawn_cap_none_in_context(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Any,
    ) -> None:
        """Layer 1 OFF: _apply_orchestrator_profile returns None spawn_cap."""
        # All flags cleared by autouse fixture
        import magi_agent.cli.wiring as wiring_mod  # noqa: PLC0415
        import magi_agent.adk_bridge.tool_adapter as ta  # noqa: PLC0415

        captured_factory: dict[str, object] = {}

        def capturing_build(
            registry: object,
            dispatcher: object,
            mode: object = "act",
            tool_context_factory: object = None,
            attach_enabled: bool = True,
            exposed_tool_names: object = None,
        ) -> list[object]:
            captured_factory["factory"] = tool_context_factory
            return []

        monkeypatch.setattr(ta, "build_adk_function_tools_for_registry", capturing_build)

        from types import SimpleNamespace  # noqa: PLC0415

        wiring_mod._build_first_party_adk_tools(
            cwd=str(tmp_path),
            session_id="gl1-off-test",
        )

        factory = captured_factory.get("factory")
        assert factory is not None, "tool_context_factory not captured"

        adk_ctx = SimpleNamespace(
            function_call=SimpleNamespace(name="FileRead", id="call-off"),
            state={},
        )
        ctx = factory(adk_ctx)  # type: ignore[operator]
        assert ctx.spawn_cap is None, (
            f"All flags OFF: spawn_cap must be None, got {ctx.spawn_cap!r}"
        )

    def test_flag_off_child_toolset_unrestricted(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Layer 3 OFF: child sees the full profile toolset (no spawn_cap narrowing)."""
        _patch_full_tools(monkeypatch)
        # MAGI_SPAWN_RECIPE_CAP_ENABLED intentionally NOT set → OFF

        req = ChildTaskRequest(
            parentExecutionId="gl1-off-narrow-parent",
            turnId="gl1-off-narrow-turn",
            taskId="gl1-off-narrow-task",
            objective="Full toolset when all flags off.",
            metadata={"allowedTools": ("FileRead",)},  # allowedTools present but flag OFF
        )
        runner = RealLocalChildRunner(
            provider_config=_provider_config(),
            toolset_profile="full",
            spawn_cap=("FileRead", "Glob"),  # spawn_cap present but flag OFF
        )

        tools, _ = runner._resolve_turn_toolset("gl1-off-narrow-sess", request=req)
        tool_names = {t.name for t in tools}

        # Flag OFF → full profile, no allowedTools/spawn_cap narrowing
        assert tool_names == {t.name for t in _FULL_TOOLS}, (
            f"Flags OFF: expected full toolset, got {tool_names}"
        )
        assert "Bash" in tool_names
        assert "FileWrite" in tool_names
        assert "Edit" in tool_names

    def test_flag_off_spawn_handoff_no_spawn_cap(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Layer 2 OFF: spawn with no spawn_cap in context → request.spawn_cap is None."""
        monkeypatch.setenv("MAGI_CHILD_RUNNER_LIVE_ENABLED", "1")
        monkeypatch.delenv("MAGI_CHILD_RUNNER_LIVE_KILL_SWITCH", raising=False)

        import magi_agent.runtime.child_runner_live as _live_mod  # noqa: PLC0415

        captured_request: list[object] = []

        class _CapturingRunner:
            openmagi_live_provider = True

            def __init__(self, **kwargs: object) -> None:
                pass

            async def run_child(self, request: object) -> dict[str, object]:
                captured_request.append(request)
                return {
                    "childExecutionId": "gl1-off-exec",
                    "status": "completed",
                    "summary": "ok",
                    "evidenceRefs": (),
                    "artifactRefs": (),
                    "auditEventRefs": (),
                }

        monkeypatch.setattr(_live_mod, "RealLocalChildRunner", _CapturingRunner)

        from magi_agent.plugins.native.subagents import spawn_agent  # noqa: PLC0415

        ctx = _tool_context()  # no spawnCap → None
        asyncio.run(spawn_agent({"prompt": "all-off test"}, ctx))

        assert len(captured_request) == 1
        req = captured_request[0]
        assert req.spawn_cap is None, (
            f"Layer 2 OFF: expected spawn_cap=None, got {req.spawn_cap!r}"
        )
