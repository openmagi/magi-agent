"""PR3 — ToolHost Coding Mutation Receipt Boundary.

Tests that:
1. Text-only claims cannot substitute for real tool execution receipts.
2. ToolHost mutation results materialize structured receipts.
3. Forbidden/blocked tools do not emit success receipts.
4. No raw paths, secrets, or auth tokens leak into public projection.
5. All features are default-off unless Gate 2 canary metadata is present.
"""

from __future__ import annotations

import hashlib
import json

import pytest

from openmagi_core_agent.evidence.coding_tool_receipts import (
    CodingToolReceiptBoundary,
    CodingToolReceiptConfig,
    CodingToolReceiptRecord,
    is_coding_mutation_tool,
    text_claim_is_not_receipt,
)
from openmagi_core_agent.tools.result import ToolResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sha256(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _ok_tool_result(
    *,
    tool_name: str = "FileEdit",
    output: str = "applied 1 edit",
    metadata: dict[str, object] | None = None,
) -> ToolResult:
    return ToolResult(
        status="ok",
        output=output,
        metadata=metadata or {"toolName": tool_name, "mutatesWorkspace": True},
    )


def _blocked_tool_result(
    *,
    tool_name: str = "FileEdit",
    reason: str = "tool not exposed",
) -> ToolResult:
    return ToolResult(
        status="blocked",
        metadata={"toolName": tool_name, "reason": reason, "mutatesWorkspace": True},
    )


def _error_tool_result(
    *,
    tool_name: str = "FileEdit",
    error_message: str = "tool handler error",
) -> ToolResult:
    return ToolResult(
        status="error",
        error_code="tool_threw",
        error_message=error_message,
        metadata={"toolName": tool_name, "mutatesWorkspace": True},
    )


# ---------------------------------------------------------------------------
# 1. Text claims are NOT receipts
# ---------------------------------------------------------------------------

class TestTextClaimIsNotReceipt:
    """A model cannot synthesize mutation evidence by text alone."""

    def test_plain_text_claim_is_rejected(self) -> None:
        assert text_claim_is_not_receipt("I edited file app.py and applied the changes.")

    def test_json_shaped_text_claim_is_rejected(self) -> None:
        fake = json.dumps({
            "receiptId": "fake-receipt-001",
            "toolName": "FileEdit",
            "status": "success",
            "inputDigest": _sha256("fake"),
        })
        assert text_claim_is_not_receipt(fake)

    def test_real_receipt_object_is_not_text_claim(self) -> None:
        boundary = CodingToolReceiptBoundary(
            config=CodingToolReceiptConfig(enabled=True),
        )
        result = _ok_tool_result()
        receipt = boundary.extract_receipt(
            tool_call_id="call-1",
            tool_name="FileEdit",
            arguments={"path": "src/app.py", "old_string": "a", "new_string": "b"},
            result=result,
        )
        assert receipt is not None
        assert not text_claim_is_not_receipt(receipt)


# ---------------------------------------------------------------------------
# 2. ToolHost mutation receipts materialize
# ---------------------------------------------------------------------------

class TestReceiptMaterialization:
    """ToolHost-dispatched coding mutations emit structured receipts."""

    def test_ok_result_produces_receipt(self) -> None:
        boundary = CodingToolReceiptBoundary(
            config=CodingToolReceiptConfig(enabled=True),
        )
        result = _ok_tool_result(tool_name="FileEdit")
        receipt = boundary.extract_receipt(
            tool_call_id="call-1",
            tool_name="FileEdit",
            arguments={"path": "src/app.py", "old_string": "a", "new_string": "b"},
            result=result,
        )
        assert receipt is not None
        assert receipt.tool_name == "FileEdit"
        assert receipt.tool_call_id == "call-1"
        assert receipt.status == "success"
        assert receipt.input_digest.startswith("sha256:")
        assert receipt.output_digest.startswith("sha256:")

    def test_receipt_includes_workspace_digest(self) -> None:
        boundary = CodingToolReceiptBoundary(
            config=CodingToolReceiptConfig(enabled=True),
        )
        result = _ok_tool_result(tool_name="FileWrite")
        receipt = boundary.extract_receipt(
            tool_call_id="call-2",
            tool_name="FileWrite",
            arguments={"path": "src/new.py", "content": "print('hello')"},
            result=result,
        )
        assert receipt is not None
        assert receipt.workspace_digest.startswith("sha256:")

    def test_receipt_public_projection_is_safe(self) -> None:
        boundary = CodingToolReceiptBoundary(
            config=CodingToolReceiptConfig(enabled=True),
        )
        args = {
            "path": "/Users/kevin/Desktop/project/src/app.py",
            "old_string": "secret_token = 'sk-live-xxx'",
            "new_string": "secret_token = os.environ['TOKEN']",
        }
        result = _ok_tool_result()
        receipt = boundary.extract_receipt(
            tool_call_id="call-3",
            tool_name="FileEdit",
            arguments=args,
            result=result,
        )
        assert receipt is not None
        projection = receipt.public_projection()
        projection_str = json.dumps(projection, default=str)
        assert "/Users/kevin" not in projection_str
        assert "sk-live-xxx" not in projection_str
        assert "secret_token" not in projection_str
        assert projection["productionWorkspaceMutationAllowed"] is False

    def test_receipt_action_field(self) -> None:
        boundary = CodingToolReceiptBoundary(
            config=CodingToolReceiptConfig(enabled=True),
        )
        result = _ok_tool_result(tool_name="FileEdit")
        receipt = boundary.extract_receipt(
            tool_call_id="call-4",
            tool_name="FileEdit",
            arguments={"path": "src/app.py", "old_string": "a", "new_string": "b"},
            result=result,
        )
        assert receipt is not None
        assert receipt.action == "edit"

    def test_file_write_receipt_action(self) -> None:
        boundary = CodingToolReceiptBoundary(
            config=CodingToolReceiptConfig(enabled=True),
        )
        result = _ok_tool_result(tool_name="FileWrite")
        receipt = boundary.extract_receipt(
            tool_call_id="call-5",
            tool_name="FileWrite",
            arguments={"path": "src/new.py", "content": "x = 1"},
            result=result,
        )
        assert receipt is not None
        assert receipt.action == "write"

    def test_bash_receipt_action(self) -> None:
        boundary = CodingToolReceiptBoundary(
            config=CodingToolReceiptConfig(enabled=True),
        )
        result = ToolResult(
            status="ok",
            output="success",
            metadata={"toolName": "Bash", "mutatesWorkspace": True},
        )
        receipt = boundary.extract_receipt(
            tool_call_id="call-6",
            tool_name="Bash",
            arguments={"command": "npm run build"},
            result=result,
        )
        assert receipt is not None
        assert receipt.action == "execute"


# ---------------------------------------------------------------------------
# 3. Forbidden tools do NOT emit success receipts
# ---------------------------------------------------------------------------

class TestForbiddenToolsNoSuccessReceipt:
    """Blocked or errored tools should not produce success receipts."""

    def test_blocked_tool_emits_blocked_receipt(self) -> None:
        boundary = CodingToolReceiptBoundary(
            config=CodingToolReceiptConfig(enabled=True),
        )
        result = _blocked_tool_result(tool_name="FileEdit", reason="tool not exposed")
        receipt = boundary.extract_receipt(
            tool_call_id="call-b1",
            tool_name="FileEdit",
            arguments={"path": "src/app.py", "old_string": "a", "new_string": "b"},
            result=result,
        )
        assert receipt is not None
        assert receipt.status == "blocked"

    def test_error_tool_emits_error_receipt(self) -> None:
        boundary = CodingToolReceiptBoundary(
            config=CodingToolReceiptConfig(enabled=True),
        )
        result = _error_tool_result()
        receipt = boundary.extract_receipt(
            tool_call_id="call-e1",
            tool_name="FileEdit",
            arguments={"path": "src/app.py"},
            result=result,
        )
        assert receipt is not None
        assert receipt.status == "error"

    def test_non_coding_tool_returns_none(self) -> None:
        boundary = CodingToolReceiptBoundary(
            config=CodingToolReceiptConfig(enabled=True),
        )
        result = ToolResult(status="ok", output="some data", metadata={})
        receipt = boundary.extract_receipt(
            tool_call_id="call-nc",
            tool_name="WebSearch",
            arguments={"query": "hello"},
            result=result,
        )
        assert receipt is None


# ---------------------------------------------------------------------------
# 4. Default-off behavior
# ---------------------------------------------------------------------------

class TestDefaultOffBehavior:
    """All handlers are default-off unless enabled."""

    def test_default_config_is_disabled(self) -> None:
        config = CodingToolReceiptConfig()
        assert config.enabled is False
        assert config.production_workspace_mutation_allowed is False

    def test_disabled_boundary_returns_none(self) -> None:
        boundary = CodingToolReceiptBoundary()
        result = _ok_tool_result()
        receipt = boundary.extract_receipt(
            tool_call_id="call-d1",
            tool_name="FileEdit",
            arguments={"path": "src/app.py"},
            result=result,
        )
        assert receipt is None

    def test_production_workspace_mutation_always_false(self) -> None:
        config = CodingToolReceiptConfig(enabled=True)
        assert config.production_workspace_mutation_allowed is False

    def test_cannot_set_production_mutation_true(self) -> None:
        with pytest.raises(Exception):
            CodingToolReceiptConfig(
                enabled=True,
                productionWorkspaceMutationAllowed=True,
            )


# ---------------------------------------------------------------------------
# 5. is_coding_mutation_tool utility
# ---------------------------------------------------------------------------

class TestIsCodingMutationTool:
    def test_file_edit_is_coding_tool(self) -> None:
        assert is_coding_mutation_tool("FileEdit")

    def test_file_write_is_coding_tool(self) -> None:
        assert is_coding_mutation_tool("FileWrite")

    def test_bash_is_coding_tool(self) -> None:
        assert is_coding_mutation_tool("Bash")

    def test_patch_apply_is_coding_tool(self) -> None:
        assert is_coding_mutation_tool("PatchApply")

    def test_web_search_is_not_coding_tool(self) -> None:
        assert not is_coding_mutation_tool("WebSearch")

    def test_read_file_is_not_coding_tool(self) -> None:
        assert not is_coding_mutation_tool("ReadFile")


# ---------------------------------------------------------------------------
# 6. Digest correctness
# ---------------------------------------------------------------------------

class TestDigestCorrectness:
    """Input/output/workspace digests use sha256."""

    def test_input_digest_is_deterministic(self) -> None:
        boundary = CodingToolReceiptBoundary(
            config=CodingToolReceiptConfig(enabled=True),
        )
        args = {"path": "src/app.py", "old_string": "a", "new_string": "b"}
        result = _ok_tool_result()

        receipt1 = boundary.extract_receipt(
            tool_call_id="call-d1",
            tool_name="FileEdit",
            arguments=args,
            result=result,
        )
        receipt2 = boundary.extract_receipt(
            tool_call_id="call-d2",
            tool_name="FileEdit",
            arguments=args,
            result=result,
        )
        assert receipt1 is not None and receipt2 is not None
        assert receipt1.input_digest == receipt2.input_digest

    def test_different_args_produce_different_digest(self) -> None:
        boundary = CodingToolReceiptBoundary(
            config=CodingToolReceiptConfig(enabled=True),
        )
        result = _ok_tool_result()

        r1 = boundary.extract_receipt(
            tool_call_id="call-x1",
            tool_name="FileEdit",
            arguments={"path": "a.py", "old_string": "x", "new_string": "y"},
            result=result,
        )
        r2 = boundary.extract_receipt(
            tool_call_id="call-x2",
            tool_name="FileEdit",
            arguments={"path": "b.py", "old_string": "x", "new_string": "y"},
            result=result,
        )
        assert r1 is not None and r2 is not None
        assert r1.input_digest != r2.input_digest
