"""Tests for built-in LLM safety hook presets.

Covers:
1.  Each preset returns a valid HookManifest
2.  Each preset has execution_type="llm" and a non-empty prompt_template
3.  Each preset has the expected hook point
4.  Each preset is disabled by default
5.  Each preset has fail_open=True, priority=50, opt_out=True
6.  Source is builtin with the correct package
7.  Presets work end-to-end with LLMHookExecutor (mocked classifier)
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from openmagi_core_agent.hooks.builtin.llm_safety_hooks import (
    bash_safety_classifier,
    edit_scope_classifier,
    response_quality_gate,
)
from openmagi_core_agent.hooks.context import HookContext
from openmagi_core_agent.hooks.executors.llm_executor import LLMHookExecutor
from openmagi_core_agent.hooks.manifest import HookManifest, HookPoint


_CONTEXT = HookContext(botId="test-bot")


# ---------------------------------------------------------------------------
# Manifest validity
# ---------------------------------------------------------------------------


class TestBashSafetyClassifier:
    def test_returns_valid_manifest(self) -> None:
        m = bash_safety_classifier()
        assert isinstance(m, HookManifest)

    def test_execution_type_is_llm(self) -> None:
        m = bash_safety_classifier()
        assert m.execution_type == "llm"

    def test_prompt_template_non_empty(self) -> None:
        m = bash_safety_classifier()
        assert m.prompt_template is not None
        assert len(m.prompt_template) > 0

    def test_hook_point_is_before_tool_use(self) -> None:
        m = bash_safety_classifier()
        assert m.point == HookPoint.BEFORE_TOOL_USE

    def test_disabled_by_default(self) -> None:
        m = bash_safety_classifier()
        assert m.enabled is False

    def test_fail_open(self) -> None:
        m = bash_safety_classifier()
        assert m.fail_open is True

    def test_priority(self) -> None:
        m = bash_safety_classifier()
        assert m.priority == 50

    def test_opt_out(self) -> None:
        m = bash_safety_classifier()
        assert m.opt_out is True

    def test_source(self) -> None:
        m = bash_safety_classifier()
        assert m.source.kind == "builtin"
        assert m.source.package == "openmagi_core_agent.hooks.builtin"


class TestEditScopeClassifier:
    def test_returns_valid_manifest(self) -> None:
        m = edit_scope_classifier()
        assert isinstance(m, HookManifest)

    def test_execution_type_is_llm(self) -> None:
        m = edit_scope_classifier()
        assert m.execution_type == "llm"

    def test_prompt_template_non_empty(self) -> None:
        m = edit_scope_classifier()
        assert m.prompt_template is not None
        assert len(m.prompt_template) > 0

    def test_hook_point_is_before_tool_use(self) -> None:
        m = edit_scope_classifier()
        assert m.point == HookPoint.BEFORE_TOOL_USE

    def test_disabled_by_default(self) -> None:
        m = edit_scope_classifier()
        assert m.enabled is False

    def test_fail_open(self) -> None:
        m = edit_scope_classifier()
        assert m.fail_open is True

    def test_priority(self) -> None:
        m = edit_scope_classifier()
        assert m.priority == 50

    def test_opt_out(self) -> None:
        m = edit_scope_classifier()
        assert m.opt_out is True

    def test_source(self) -> None:
        m = edit_scope_classifier()
        assert m.source.kind == "builtin"
        assert m.source.package == "openmagi_core_agent.hooks.builtin"


class TestResponseQualityGate:
    def test_returns_valid_manifest(self) -> None:
        m = response_quality_gate()
        assert isinstance(m, HookManifest)

    def test_execution_type_is_llm(self) -> None:
        m = response_quality_gate()
        assert m.execution_type == "llm"

    def test_prompt_template_non_empty(self) -> None:
        m = response_quality_gate()
        assert m.prompt_template is not None
        assert len(m.prompt_template) > 0

    def test_hook_point_is_before_commit(self) -> None:
        m = response_quality_gate()
        assert m.point == HookPoint.BEFORE_COMMIT

    def test_disabled_by_default(self) -> None:
        m = response_quality_gate()
        assert m.enabled is False

    def test_fail_open(self) -> None:
        m = response_quality_gate()
        assert m.fail_open is True

    def test_priority(self) -> None:
        m = response_quality_gate()
        assert m.priority == 50

    def test_opt_out(self) -> None:
        m = response_quality_gate()
        assert m.opt_out is True

    def test_source(self) -> None:
        m = response_quality_gate()
        assert m.source.kind == "builtin"
        assert m.source.package == "openmagi_core_agent.hooks.builtin"


# ---------------------------------------------------------------------------
# LLMHookExecutor integration (mocked classifier)
# ---------------------------------------------------------------------------


class TestPresetsWithLLMExecutor:
    @pytest.mark.asyncio
    async def test_bash_safety_allow(self) -> None:
        manifest = bash_safety_classifier()
        with patch(
            "openmagi_core_agent.hooks.executors.llm_executor._call_classifier",
            new_callable=AsyncMock,
            return_value="ALLOW — safe command",
        ):
            result = await LLMHookExecutor().execute(_CONTEXT, manifest)
        assert result.action == "permission_decision"
        assert result.decision == "approve"

    @pytest.mark.asyncio
    async def test_bash_safety_deny(self) -> None:
        manifest = bash_safety_classifier()
        with patch(
            "openmagi_core_agent.hooks.executors.llm_executor._call_classifier",
            new_callable=AsyncMock,
            return_value="DENY — rm -rf is destructive",
        ):
            result = await LLMHookExecutor().execute(_CONTEXT, manifest)
        assert result.action == "permission_decision"
        assert result.decision == "deny"

    @pytest.mark.asyncio
    async def test_edit_scope_allow(self) -> None:
        manifest = edit_scope_classifier()
        with patch(
            "openmagi_core_agent.hooks.executors.llm_executor._call_classifier",
            new_callable=AsyncMock,
            return_value="ALLOW — edit is within scope",
        ):
            result = await LLMHookExecutor().execute(_CONTEXT, manifest)
        assert result.action == "permission_decision"
        assert result.decision == "approve"

    @pytest.mark.asyncio
    async def test_edit_scope_ask(self) -> None:
        manifest = edit_scope_classifier()
        with patch(
            "openmagi_core_agent.hooks.executors.llm_executor._call_classifier",
            new_callable=AsyncMock,
            return_value="ASK — unclear if config change was requested",
        ):
            result = await LLMHookExecutor().execute(_CONTEXT, manifest)
        assert result.action == "permission_decision"
        assert result.decision == "ask"

    @pytest.mark.asyncio
    async def test_response_quality_allow(self) -> None:
        manifest = response_quality_gate()
        with patch(
            "openmagi_core_agent.hooks.executors.llm_executor._call_classifier",
            new_callable=AsyncMock,
            return_value="ALLOW — response is complete and correct",
        ):
            result = await LLMHookExecutor().execute(_CONTEXT, manifest)
        assert result.action == "permission_decision"
        assert result.decision == "approve"

    @pytest.mark.asyncio
    async def test_response_quality_deny(self) -> None:
        manifest = response_quality_gate()
        with patch(
            "openmagi_core_agent.hooks.executors.llm_executor._call_classifier",
            new_callable=AsyncMock,
            return_value="DENY — response is empty",
        ):
            result = await LLMHookExecutor().execute(_CONTEXT, manifest)
        assert result.action == "permission_decision"
        assert result.decision == "deny"

    @pytest.mark.asyncio
    async def test_metadata_includes_classification(self) -> None:
        manifest = bash_safety_classifier()
        with patch(
            "openmagi_core_agent.hooks.executors.llm_executor._call_classifier",
            new_callable=AsyncMock,
            return_value="ALLOW — safe",
        ):
            result = await LLMHookExecutor().execute(_CONTEXT, manifest)
        evidence = result.metadata.get("llm_hook_classification")
        assert isinstance(evidence, dict)
        assert "model" in evidence
        assert "decision" in evidence
        assert evidence["decision"] == "approve"
