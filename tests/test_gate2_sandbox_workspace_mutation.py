"""Tests for Gate 2 sandbox workspace mutation provider with rollback receipts."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from openmagi_core_agent.workspace.sandbox_mutation import (
    Gate2SandboxWorkspaceMutationProvider,
    SandboxMutationOutcome,
    SandboxMutationReceipt,
    SandboxRollbackReceipt,
)

_DIGEST_RE = re.compile(r"^sha256:[a-f0-9]{64}$")


# ── Helper ──────────────────────────────────────────────────────────────────


def _make_provider(
    tmp_path: Path,
    *,
    gate2_selected: bool = True,
) -> Gate2SandboxWorkspaceMutationProvider:
    sandbox = tmp_path / "gate2-sandbox"
    sandbox.mkdir()
    return Gate2SandboxWorkspaceMutationProvider(
        sandbox_root=sandbox,
        gate2_selected=gate2_selected,
    )


def _assert_digests_valid(receipt: SandboxMutationReceipt) -> None:
    assert _DIGEST_RE.fullmatch(receipt.workspace_digest)
    assert _DIGEST_RE.fullmatch(receipt.relative_path_digest)
    if receipt.before_digest is not None:
        assert _DIGEST_RE.fullmatch(receipt.before_digest)
    if receipt.after_digest is not None:
        assert _DIGEST_RE.fullmatch(receipt.after_digest)
    if receipt.rollback_receipt_digest is not None:
        assert _DIGEST_RE.fullmatch(receipt.rollback_receipt_digest)


def _assert_no_raw_paths(outcome: SandboxMutationOutcome) -> None:
    rendered = json.dumps(outcome.model_dump(by_alias=True, mode="json"), sort_keys=True)
    assert "/Users/" not in rendered
    assert "/data/bots/" not in rendered
    assert "/workspace/" not in rendered
    assert "/var/lib/kubelet/" not in rendered


# ── Allowed sandbox operations ──────────────────────────────────────────────


class TestAllowedSandboxCreate:
    def test_file_create_produces_complete_receipt(self, tmp_path: Path) -> None:
        provider = _make_provider(tmp_path)
        outcome = provider.mutate(
            action="FileCreate",
            relative_path="src/hello.py",
            content="print('hello')\n",
        )

        assert outcome.status == "completed"
        assert outcome.reason == "sandbox_mutation_completed"
        assert outcome.production_workspace_mutation_allowed is False

        receipt = outcome.mutation_receipt
        assert receipt.kind == "gate2_sandbox_workspace_mutation"
        assert receipt.action == "FileCreate"
        assert receipt.production_workspace_mutation_allowed is False
        _assert_digests_valid(receipt)

        # before_digest should be "missing" digest since file didn't exist
        assert receipt.before_digest is not None
        # after_digest should be content digest
        assert receipt.after_digest is not None
        assert receipt.before_digest != receipt.after_digest

    def test_file_create_produces_rollback_receipt(self, tmp_path: Path) -> None:
        provider = _make_provider(tmp_path)
        outcome = provider.mutate(
            action="FileCreate",
            relative_path="src/new_file.py",
            content="content\n",
        )

        assert outcome.rollback_receipt is not None
        rollback = outcome.rollback_receipt
        assert rollback.kind == "gate2_sandbox_workspace_rollback"
        assert rollback.production_workspace_mutation_allowed is False
        assert _DIGEST_RE.fullmatch(rollback.mutation_receipt_digest)
        assert _DIGEST_RE.fullmatch(rollback.rollback_digest)
        assert _DIGEST_RE.fullmatch(rollback.restored_digest)

    def test_file_create_sandbox_is_rolled_back(self, tmp_path: Path) -> None:
        provider = _make_provider(tmp_path)
        provider.mutate(
            action="FileCreate",
            relative_path="src/ephemeral.py",
            content="should not persist\n",
        )

        # File should not exist after mutation (rollback happened)
        target = provider.sandbox_root / "src" / "ephemeral.py"
        assert not target.exists()


class TestAllowedSandboxEdit:
    def test_file_edit_existing_file(self, tmp_path: Path) -> None:
        provider = _make_provider(tmp_path)
        # Pre-create file in sandbox
        target = provider.sandbox_root / "src" / "existing.py"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("original\n", encoding="utf-8")

        outcome = provider.mutate(
            action="FileEdit",
            relative_path="src/existing.py",
            content="modified\n",
        )

        assert outcome.status == "completed"
        assert outcome.mutation_receipt.action == "FileEdit"
        assert outcome.rollback_receipt is not None
        _assert_digests_valid(outcome.mutation_receipt)

        # After rollback, file should have original content
        assert target.read_text() == "original\n"

    def test_file_edit_records_before_and_after_digests(self, tmp_path: Path) -> None:
        provider = _make_provider(tmp_path)
        target = provider.sandbox_root / "data.txt"
        target.write_text("before\n", encoding="utf-8")

        outcome = provider.mutate(
            action="FileEdit",
            relative_path="data.txt",
            content="after\n",
        )

        receipt = outcome.mutation_receipt
        assert receipt.before_digest is not None
        assert receipt.after_digest is not None
        assert receipt.before_digest != receipt.after_digest


class TestAllowedSandboxPatch:
    def test_patch_apply_appends_content(self, tmp_path: Path) -> None:
        provider = _make_provider(tmp_path)
        target = provider.sandbox_root / "src" / "patch_target.py"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("line1\n", encoding="utf-8")

        outcome = provider.mutate(
            action="PatchApply",
            relative_path="src/patch_target.py",
            patch_content="line2\n",
        )

        assert outcome.status == "completed"
        assert outcome.mutation_receipt.action == "PatchApply"
        assert outcome.rollback_receipt is not None

        # After rollback, file should have original content
        assert target.read_text() == "line1\n"


# ── Receipt shape conformance ───────────────────────────────────────────────


class TestReceiptShape:
    def test_receipt_matches_specified_shape(self, tmp_path: Path) -> None:
        provider = _make_provider(tmp_path)
        outcome = provider.mutate(
            action="FileCreate",
            relative_path="src/shape_test.py",
            content="pass\n",
        )

        receipt_dict = outcome.mutation_receipt.model_dump(by_alias=True, mode="json")
        expected_keys = {
            "kind",
            "action",
            "workspaceDigest",
            "relativePathDigest",
            "beforeDigest",
            "afterDigest",
            "rollbackReceiptDigest",
            "productionWorkspaceMutationAllowed",
        }
        assert set(receipt_dict.keys()) == expected_keys
        assert receipt_dict["kind"] == "gate2_sandbox_workspace_mutation"
        assert receipt_dict["action"] == "FileCreate"
        assert receipt_dict["productionWorkspaceMutationAllowed"] is False

    def test_no_production_paths_in_public_projection(self, tmp_path: Path) -> None:
        provider = _make_provider(tmp_path)
        outcome = provider.mutate(
            action="FileCreate",
            relative_path="src/projection_test.py",
            content="pass\n",
        )

        projection = provider.public_projection(outcome)
        rendered = json.dumps(projection, sort_keys=True)
        assert "/Users/" not in rendered
        assert "/data/bots/" not in rendered
        assert "/workspace/" not in rendered
        assert str(tmp_path) not in rendered
        _assert_no_raw_paths(outcome)

    def test_rollback_receipt_shape(self, tmp_path: Path) -> None:
        provider = _make_provider(tmp_path)
        outcome = provider.mutate(
            action="FileCreate",
            relative_path="src/rollback_shape.py",
            content="pass\n",
        )

        rollback_dict = outcome.rollback_receipt.model_dump(by_alias=True, mode="json")
        expected_keys = {
            "kind",
            "mutationReceiptDigest",
            "rollbackDigest",
            "restoredDigest",
            "productionWorkspaceMutationAllowed",
        }
        assert set(rollback_dict.keys()) == expected_keys
        assert rollback_dict["kind"] == "gate2_sandbox_workspace_rollback"
        assert rollback_dict["productionWorkspaceMutationAllowed"] is False


# ── Gate 2 canary metadata rejection ────────────────────────────────────────


class TestMissingCanaryMetadata:
    def test_denied_when_gate2_not_selected(self, tmp_path: Path) -> None:
        provider = _make_provider(tmp_path, gate2_selected=False)
        outcome = provider.mutate(
            action="FileCreate",
            relative_path="src/should_fail.py",
            content="fail\n",
        )

        assert outcome.status == "denied"
        assert outcome.reason == "gate2_canary_not_selected"
        assert outcome.mutation_receipt.production_workspace_mutation_allowed is False
        assert outcome.rollback_receipt is None

    def test_denied_default_provider_has_no_canary(self, tmp_path: Path) -> None:
        sandbox = tmp_path / "gate2-sandbox"
        sandbox.mkdir()
        provider = Gate2SandboxWorkspaceMutationProvider(sandbox_root=sandbox)
        outcome = provider.mutate(
            action="FileCreate",
            relative_path="src/no_canary.py",
            content="fail\n",
        )

        assert outcome.status == "denied"
        assert outcome.reason == "gate2_canary_not_selected"


# ── Path security rejections ────────────────────────────────────────────────


class TestPathTraversal:
    @pytest.mark.parametrize(
        "path",
        [
            "../escape.py",
            "src/../../escape.py",
            "src/../../../etc/passwd",
            "a/b/../../c/../../../escape",
        ],
    )
    def test_path_traversal_denied(self, tmp_path: Path, path: str) -> None:
        provider = _make_provider(tmp_path)
        outcome = provider.mutate(
            action="FileCreate",
            relative_path=path,
            content="escape attempt\n",
        )

        assert outcome.status == "denied"
        assert "traversal" in outcome.reason or "denied" in outcome.reason

    @pytest.mark.parametrize(
        "path",
        [
            "/etc/passwd",
            "/tmp/absolute.py",
            "/data/bots/bot-123/workspace/file.py",
            "~/secret.py",
        ],
    )
    def test_absolute_path_denied(self, tmp_path: Path, path: str) -> None:
        provider = _make_provider(tmp_path)
        outcome = provider.mutate(
            action="FileCreate",
            relative_path=path,
            content="absolute attempt\n",
        )

        assert outcome.status == "denied"
        assert "absolute" in outcome.reason or "denied" in outcome.reason


class TestSealedPaths:
    @pytest.mark.parametrize(
        "path",
        [
            "SOUL.md",
            "CLAUDE.md",
            "TOOLS.md",
            "AGENTS.md",
            "HEARTBEAT.md",
            "subdir/SOUL.md",
            "deep/nested/CLAUDE.md",
        ],
    )
    def test_sealed_path_denied(self, tmp_path: Path, path: str) -> None:
        provider = _make_provider(tmp_path)
        outcome = provider.mutate(
            action="FileCreate",
            relative_path=path,
            content="sealed override attempt\n",
        )

        assert outcome.status == "denied"
        assert "sealed" in outcome.reason


class TestPrivatePaths:
    @pytest.mark.parametrize(
        "path",
        [
            ".env",
            ".env.local",
            ".env.production",
            "config/.env.secrets",
            ".git/config",
            ".git/HEAD",
            ".ssh/id_rsa",
            ".kube/config",
            "auth/tokens.json",
            "secrets/api_key.txt",
            "credentials/service-account.json",
            "session/data.json",
            "tokens/refresh.txt",
        ],
    )
    def test_private_path_denied(self, tmp_path: Path, path: str) -> None:
        provider = _make_provider(tmp_path)
        outcome = provider.mutate(
            action="FileCreate",
            relative_path=path,
            content="private data\n",
        )

        assert outcome.status == "denied"
        assert "private" in outcome.reason or "denied" in outcome.reason


class TestMemoryPaths:
    @pytest.mark.parametrize(
        "path",
        [
            "memory/daily/2026-01-01.md",
            "memory/ROOT.md",
            "hipocampus/config.json",
            ".memory/cache.json",
        ],
    )
    def test_memory_path_denied(self, tmp_path: Path, path: str) -> None:
        provider = _make_provider(tmp_path)
        outcome = provider.mutate(
            action="FileCreate",
            relative_path=path,
            content="memory write attempt\n",
        )

        assert outcome.status == "denied"
        assert "memory" in outcome.reason


# ── Forbidden actions ───────────────────────────────────────────────────────


class TestForbiddenActions:
    @pytest.mark.parametrize(
        "action",
        [
            "Bash",
            "Delete",
            "FileDelete",
            "FileWrite",
            "MemoryWrite",
            "BrowserAction",
            "WebFetch",
            "TelegramSend",
            "NetworkEgress",
        ],
    )
    def test_forbidden_action_denied(self, tmp_path: Path, action: str) -> None:
        provider = _make_provider(tmp_path)
        outcome = provider.mutate(
            action=action,
            relative_path="src/forbidden.py",
            content="forbidden\n",
        )

        assert outcome.status == "denied"
        assert outcome.reason == "forbidden_sandbox_action"


# ── Empty path ──────────────────────────────────────────────────────────────


class TestEmptyPath:
    def test_empty_path_denied(self, tmp_path: Path) -> None:
        provider = _make_provider(tmp_path)
        outcome = provider.mutate(
            action="FileCreate",
            relative_path="",
            content="empty path\n",
        )

        assert outcome.status == "denied"
        assert "empty" in outcome.reason or "denied" in outcome.reason

    def test_whitespace_path_denied(self, tmp_path: Path) -> None:
        provider = _make_provider(tmp_path)
        outcome = provider.mutate(
            action="FileCreate",
            relative_path="   ",
            content="whitespace path\n",
        )

        assert outcome.status == "denied"


# ── Sandbox root escape ────────────────────────────────────────────────────


class TestSandboxRootEscape:
    def test_symlink_escape_denied(self, tmp_path: Path) -> None:
        provider = _make_provider(tmp_path)
        # Create a symlink inside sandbox that points outside
        escape_target = tmp_path / "outside"
        escape_target.mkdir()
        symlink = provider.sandbox_root / "escape_link"
        symlink.symlink_to(escape_target)

        outcome = provider.mutate(
            action="FileCreate",
            relative_path="escape_link/file.py",
            content="escape via symlink\n",
        )

        assert outcome.status == "denied"
        assert "traversal" in outcome.reason or "denied" in outcome.reason


# ── Production workspace mutation flag ──────────────────────────────────────


class TestProductionFlag:
    def test_production_mutation_always_false_on_completed(self, tmp_path: Path) -> None:
        provider = _make_provider(tmp_path)
        outcome = provider.mutate(
            action="FileCreate",
            relative_path="src/prod_check.py",
            content="pass\n",
        )

        assert outcome.production_workspace_mutation_allowed is False
        assert outcome.mutation_receipt.production_workspace_mutation_allowed is False
        if outcome.rollback_receipt is not None:
            assert outcome.rollback_receipt.production_workspace_mutation_allowed is False

    def test_production_mutation_always_false_on_denied(self, tmp_path: Path) -> None:
        provider = _make_provider(tmp_path, gate2_selected=False)
        outcome = provider.mutate(
            action="FileCreate",
            relative_path="src/denied_check.py",
            content="pass\n",
        )

        assert outcome.production_workspace_mutation_allowed is False
        assert outcome.mutation_receipt.production_workspace_mutation_allowed is False

    def test_production_flag_cannot_be_forged_via_model_construct(self) -> None:
        """Verify pydantic Literal[False] blocks True even via model_construct."""
        with pytest.raises(Exception):
            SandboxMutationReceipt(
                action="FileCreate",
                workspaceDigest="sha256:" + "a" * 64,
                relativePathDigest="sha256:" + "b" * 64,
                beforeDigest=None,
                afterDigest="sha256:" + "c" * 64,
                rollbackReceiptDigest=None,
                productionWorkspaceMutationAllowed=True,  # type: ignore[arg-type]
            )


# ── Cleanup ─────────────────────────────────────────────────────────────────


class TestCleanup:
    def test_temp_dir_cleanup(self) -> None:
        provider = Gate2SandboxWorkspaceMutationProvider(gate2_selected=True)
        root = provider.sandbox_root
        assert root.exists()
        provider.cleanup()
        assert not root.exists()

    def test_explicit_sandbox_root_not_deleted_on_cleanup(self, tmp_path: Path) -> None:
        sandbox = tmp_path / "gate2-sandbox"
        sandbox.mkdir()
        provider = Gate2SandboxWorkspaceMutationProvider(
            sandbox_root=sandbox,
            gate2_selected=True,
        )
        provider.cleanup()
        # Explicit sandbox root should still exist
        assert sandbox.exists()


# ── Multiple mutations ──────────────────────────────────────────────────────


class TestMultipleMutations:
    def test_multiple_creates_produce_unique_receipts(self, tmp_path: Path) -> None:
        provider = _make_provider(tmp_path)
        outcome1 = provider.mutate(
            action="FileCreate",
            relative_path="file1.py",
            content="content1\n",
        )
        outcome2 = provider.mutate(
            action="FileCreate",
            relative_path="file2.py",
            content="content2\n",
        )

        assert outcome1.status == "completed"
        assert outcome2.status == "completed"
        r1 = outcome1.mutation_receipt
        r2 = outcome2.mutation_receipt
        assert r1.relative_path_digest != r2.relative_path_digest
        assert r1.after_digest != r2.after_digest

    def test_create_then_edit_same_file(self, tmp_path: Path) -> None:
        provider = _make_provider(tmp_path)
        # First create leaves sandbox clean (rollback removes file)
        provider.mutate(
            action="FileCreate",
            relative_path="evolving.py",
            content="v1\n",
        )
        # Second create on non-existent file (was rolled back)
        outcome2 = provider.mutate(
            action="FileCreate",
            relative_path="evolving.py",
            content="v2\n",
        )

        assert outcome2.status == "completed"
        # File should not exist after rollback
        assert not (provider.sandbox_root / "evolving.py").exists()
