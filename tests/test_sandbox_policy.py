from __future__ import annotations

import importlib
import json
import subprocess
import sys

import pytest
from pydantic import ValidationError

from magi_agent.sandbox.browser import evaluate_browser_request
from magi_agent.sandbox.child_workspace import evaluate_child_workspace_request
from magi_agent.sandbox.filesystem import evaluate_filesystem_access
from magi_agent.sandbox.network import evaluate_network_access
from magi_agent.sandbox.policy import SandboxAuthorityFlags, SandboxDecision, SandboxPolicy
from magi_agent.sandbox.process import evaluate_process_request


def test_filesystem_policy_blocks_workspace_escape_and_sealed_file_write() -> None:
    policy = SandboxPolicy.local_default(workspaceRoot="/workspace/bot")

    escape = evaluate_filesystem_access(policy, path="../redacted-sensitive.txt", operation="read")
    absolute_escape = evaluate_filesystem_access(policy, path="/etc/passwd", operation="read")
    sealed = evaluate_filesystem_access(policy, path="TOOLS.md", operation="write")

    assert escape.allowed is False
    assert "workspace_escape_blocked" in escape.reason_codes
    assert absolute_escape.allowed is False
    assert "workspace_escape_blocked" in absolute_escape.reason_codes
    assert sealed.allowed is False
    assert "sealed_file_write_blocked" in sealed.reason_codes


def test_filesystem_policy_blocks_secret_paths_without_projecting_raw_path() -> None:
    policy = SandboxPolicy.local_default(workspaceRoot="/workspace/bot")

    decision = evaluate_filesystem_access(policy, path=".env.production", operation="read")
    public = decision.public_projection()
    encoded = json.dumps(public, sort_keys=True)

    assert decision.allowed is False
    assert "secret_path_blocked" in decision.reason_codes
    assert decision.target_digest.startswith("sha256:")
    assert ".env.production" not in encoded
    assert "/workspace/bot" not in encoded


def test_filesystem_policy_allows_safe_read_decision_without_filesystem_access() -> None:
    policy = SandboxPolicy.local_default(workspaceRoot="/workspace/bot")

    decision = evaluate_filesystem_access(policy, path="src/app.py", operation="read")

    assert decision.allowed is True
    assert decision.operation == "read"
    assert decision.execution_attempted is False
    assert decision.filesystem_mutation_allowed is False


def test_network_policy_blocks_private_metadata_and_credential_urls() -> None:
    policy = SandboxPolicy.local_default(workspaceRoot="/workspace/bot")

    private = evaluate_network_access(policy, url="http://169.254.169.254/latest/meta-data")
    metadata = evaluate_network_access(policy, url="https://metadata.invalid/latest/meta-data")
    credential = evaluate_network_access(
        policy,
        url="https://example.invalid/callback?credential=REDACTED_TEST_VALUE",
    )

    assert private.allowed is False
    assert "private_network_blocked" in private.reason_codes
    assert metadata.allowed is False
    assert "metadata_endpoint_blocked" in metadata.reason_codes
    assert credential.allowed is False
    assert "credential_url_blocked" in credential.reason_codes
    encoded = json.dumps(credential.public_projection(), sort_keys=True)
    assert "credential=REDACTED_TEST_VALUE" not in encoded
    assert "example.invalid" in encoded


def test_network_policy_blocks_url_userinfo_credentials() -> None:
    policy = SandboxPolicy.local_default(
        workspaceRoot="/workspace/bot",
        allowNetwork=True,
        networkAllowlist=("example.com",),
    )
    credential_url = "https://" + "user:password" + "@example.com/docs"

    decision = evaluate_network_access(policy, url=credential_url)

    assert decision.allowed is False
    assert "credential_url_blocked" in decision.reason_codes
    encoded = json.dumps(decision.public_projection(), sort_keys=True)
    assert "user:password" not in encoded


@pytest.mark.parametrize(
    "url,reason",
    (
        (
            "https://example.com/?next=https%3A%2F%2Fuser%3Apass%40example.com%2F",
            "credential_url_blocked",
        ),
        (
            "https://example.com/?next=http%3A%2F%2F169.254.169.254%2Flatest",
            "private_network_blocked",
        ),
        (
            "https://example.com/?next=http%253A%252F%252Fmetadata.invalid%252Flatest",
            "metadata_endpoint_blocked",
        ),
        ("https://example.com/?next=http%3A%2F%2F2130706433%2F", "private_network_blocked"),
        ("https://example.com/?next=http%3A%2F%2F0x7f000001%2F", "private_network_blocked"),
        ("https://example.com/?next=http%3A%2F%2F0177.0.0.1%2F", "private_network_blocked"),
        (
            "https://example.com/?next=https%3A%2F%2Fredirector.invalid%2F%3Fu%3Dhttp%253A%252F"
            "%252F169.254.169.254%252Flatest",
            "private_network_blocked",
        ),
    ),
)
def test_network_policy_blocks_nested_encoded_urls(
    url: str,
    reason: str,
) -> None:
    policy = SandboxPolicy.local_default(
        workspaceRoot="/workspace/bot",
        allowNetwork=True,
        networkAllowlist=("example.com",),
    )

    decision = evaluate_network_access(policy, url=url)

    assert decision.allowed is False
    assert reason in decision.reason_codes


def test_network_policy_allows_public_allowlisted_host_as_decision_only() -> None:
    policy = SandboxPolicy.local_default(
        workspaceRoot="/workspace/bot",
        allowNetwork=True,
        networkAllowlist=("example.com",),
    )

    decision = evaluate_network_access(policy, url="https://example.com/docs")

    assert decision.allowed is True
    assert decision.operation == "network"
    assert decision.network_call_allowed is False
    assert decision.execution_attempted is False


def test_process_policy_requires_sandbox_and_scrubbed_env() -> None:
    policy = SandboxPolicy.local_default(workspaceRoot="/workspace/bot")
    decision = evaluate_process_request(
        policy,
        command=("python", "-m", "pytest"),
        env={"PATH": "/usr/bin", "PROVIDER_TOKEN": "REDACTED_TEST_VALUE"},
        cwd="/workspace/bot",
    )

    assert decision.allowed is False
    assert "secret_env_blocked" in decision.reason_codes


def test_process_policy_blocks_process_escape_commands() -> None:
    policy = SandboxPolicy.local_default(workspaceRoot="/workspace/bot")
    decision = evaluate_process_request(
        policy,
        command=("bash", "-c", "curl http://169.254.169.254/latest/meta-data"),
        env={"PATH": "/usr/bin"},
        cwd="/workspace/bot",
    )

    assert decision.allowed is False
    assert "process_escape_blocked" in decision.reason_codes
    assert "metadata_endpoint_blocked" in decision.reason_codes
    encoded = json.dumps(decision.public_projection(), sort_keys=True)
    assert "curl http" not in encoded


@pytest.mark.parametrize(
    "command",
    (
        ("python", "-c", "__import__('os').system('sh')"),
        ("node", "-e", "require('child_process').execFileSync('sh')"),
        ("npm", "run", "postinstall"),
    ),
)
def test_process_policy_blocks_interpreter_and_package_script_escape(command: tuple[str, ...]) -> None:
    policy = SandboxPolicy.local_default(workspaceRoot="/workspace/bot")

    decision = evaluate_process_request(
        policy,
        command=command,
        env={"PATH": "/usr/bin"},
        cwd="/workspace/bot",
    )

    assert decision.allowed is False
    assert "process_escape_blocked" in decision.reason_codes


def test_process_policy_blocks_credential_bearing_env_urls() -> None:
    policy = SandboxPolicy.local_default(workspaceRoot="/workspace/bot")
    credential_dsn = "postgres://" + "user:pass" + "@db.example.com/app"

    decision = evaluate_process_request(
        policy,
        command=("rg", "needle", "."),
        env={"PATH": "/usr/bin", "DATABASE_URL": credential_dsn},
        cwd="/workspace/bot",
    )

    assert decision.allowed is False
    assert "secret_env_blocked" in decision.reason_codes


@pytest.mark.parametrize(
    "env",
    (
        {"PATH": "/usr/bin", "ENDPOINT": "https://example.com/callback?token=abc"},
        {"PATH": "/usr/bin", "URL": "https://" + "user:pass" + "@example.com/db"},
        {"PATH": "/usr/bin", "ENDPOINT": "postgres://" + "user:pass" + "@db.example.com/app"},
    ),
)
def test_process_policy_blocks_credential_urls_under_generic_env_names(
    env: dict[str, str],
) -> None:
    policy = SandboxPolicy.local_default(workspaceRoot="/workspace/bot")

    decision = evaluate_process_request(
        policy,
        command=("rg", "needle", "."),
        env=env,
        cwd="/workspace/bot",
    )

    assert decision.allowed is False
    assert "secret_env_blocked" in decision.reason_codes


@pytest.mark.parametrize(
    "command",
    (
        ("node", "--eval", "1+1"),
        ("node", "-p", "1+1"),
        ("node", "script.js"),
        ("python", "-m", "pip"),
        ("python", "-m", "http.server"),
        ("python", "script.py"),
        ("npm", "test"),
        ("npm", "start"),
        ("npm", "install"),
        ("npm", "ci"),
        ("npm", "pack"),
        ("npm", "publish"),
        ("pytest", "tests"),
        ("env", "python", "script.py"),
        ("uv", "run", "pytest"),
        ("pip", "install", "x"),
        ("yarn", "test"),
        ("find", ".", "-exec", "python", "script.py", ";"),
        ("xargs", "python", "script.py"),
        ("python3.11", "-c", "print(1)"),
        ("pip3.11", "install", "x"),
        ("pytest-3", "tests"),
        ("uvx", "pytest"),
    ),
)
def test_process_policy_blocks_more_interpreter_and_package_manager_escapes(
    command: tuple[str, ...],
) -> None:
    policy = SandboxPolicy.local_default(
        workspaceRoot="/workspace/bot",
        allowProcess=True,
        allowedProcesses=(
            "env",
            "find",
            "node",
            "npm",
            "pip",
            "pip3.11",
            "python",
            "python3.11",
            "pytest",
            "pytest-3",
            "uv",
            "uvx",
            "xargs",
            "yarn",
        ),
    )

    decision = evaluate_process_request(
        policy,
        command=command,
        env={"PATH": "/usr/bin"},
        cwd="/workspace/bot",
    )

    assert decision.allowed is False
    assert "process_escape_blocked" in decision.reason_codes


def test_process_policy_is_disabled_by_default() -> None:
    policy = SandboxPolicy.local_default(workspaceRoot="/workspace/bot")

    decision = evaluate_process_request(
        policy,
        command=("rg", "needle", "."),
        env={"PATH": "/usr/bin"},
        cwd="/workspace/bot",
    )

    assert decision.allowed is False
    assert "process_disabled" in decision.reason_codes
    assert decision.execution_attempted is False
    assert decision.process_spawn_allowed is False


def test_safe_process_command_is_allowed_only_with_explicit_local_sandbox_policy() -> None:
    policy = SandboxPolicy.local_default(
        workspaceRoot="/workspace/bot",
        allowProcess=True,
        allowedProcesses=("rg",),
    )
    decision = evaluate_process_request(
        policy,
        command=("rg", "needle", "."),
        env={"PATH": "/usr/bin"},
        cwd="/workspace/bot",
    )

    assert decision.allowed is True
    assert decision.sandbox_required is True
    assert decision.execution_attempted is False
    assert decision.process_spawn_allowed is False


def test_browser_policy_blocks_private_auth_and_captcha_flows() -> None:
    policy = SandboxPolicy.local_default(workspaceRoot="/workspace/bot", allowNetwork=True)

    private = evaluate_browser_request(policy, url="http://127.0.0.1/admin")
    auth = evaluate_browser_request(policy, url="https://example.com/oauth/authorize")
    captcha = evaluate_browser_request(policy, url="https://example.com/captcha")

    assert private.allowed is False
    assert "private_network_blocked" in private.reason_codes
    assert auth.allowed is False
    assert "auth_flow_blocked" in auth.reason_codes
    assert captcha.allowed is False
    assert "captcha_flow_blocked" in captcha.reason_codes
    assert private.browser_action_allowed is False


@pytest.mark.parametrize(
    "url,reason",
    (
        ("https://login.example.com/", "auth_flow_blocked"),
        ("https://example.com/?redirect_uri=https://idp.example/oauth/authorize", "auth_flow_blocked"),
        ("https://example.com/#/login", "auth_flow_blocked"),
        ("https://example.com/?captcha=1", "captcha_flow_blocked"),
    ),
)
def test_browser_policy_blocks_auth_and_captcha_indicators_outside_path(
    url: str,
    reason: str,
) -> None:
    policy = SandboxPolicy.local_default(
        workspaceRoot="/workspace/bot",
        allowNetwork=True,
        networkAllowlist=("example.com", "login.example.com"),
    )

    decision = evaluate_browser_request(policy, url=url)

    assert decision.allowed is False
    assert reason in decision.reason_codes


@pytest.mark.parametrize(
    "url,reason",
    (
        ("https://example.com/%6cogin", "auth_flow_blocked"),
        ("https://example.com/?next=%2F%6cogin", "auth_flow_blocked"),
        ("https://example.com/#%2F%63aptcha", "captcha_flow_blocked"),
        ("https://example.com/%256c%256f%2567%2569%256e", "auth_flow_blocked"),
        ("https://example.com/#%252F%2563aptcha", "captcha_flow_blocked"),
    ),
)
def test_browser_policy_decodes_auth_and_captcha_indicators_recursively(
    url: str,
    reason: str,
) -> None:
    policy = SandboxPolicy.local_default(
        workspaceRoot="/workspace/bot",
        allowNetwork=True,
        networkAllowlist=("example.com",),
    )

    decision = evaluate_browser_request(policy, url=url)

    assert decision.allowed is False
    assert reason in decision.reason_codes


def test_child_workspace_policy_blocks_shared_parent_and_path_escape() -> None:
    policy = SandboxPolicy.local_default(workspaceRoot="/workspace/bot")

    shared = evaluate_child_workspace_request(
        policy,
        child_workspace_root="/workspace/bot",
        requested_path="src/app.py",
        mutates_parent=True,
    )
    escape = evaluate_child_workspace_request(
        policy,
        child_workspace_root="/workspace/child",
        requested_path="../bot/.env",
        mutates_parent=False,
    )

    assert shared.allowed is False
    assert "child_workspace_must_be_isolated" in shared.reason_codes
    assert "parent_workspace_mutation_blocked" in shared.reason_codes
    assert escape.allowed is False
    assert "workspace_escape_blocked" in escape.reason_codes


def test_child_workspace_policy_blocks_parent_ancestor_roots() -> None:
    policy = SandboxPolicy.local_default(workspaceRoot="/workspace/bot")

    decision = evaluate_child_workspace_request(
        policy,
        child_workspace_root="/workspace",
        requested_path="bot/src/app.py",
        mutates_parent=False,
    )

    assert decision.allowed is False
    assert "child_workspace_must_be_isolated" in decision.reason_codes
    assert "parent_workspace_path_blocked" in decision.reason_codes


def test_authority_flags_cannot_be_forged() -> None:
    flags = SandboxAuthorityFlags(
        executionAttempted=True,
        filesystemMutationAllowed=True,
        networkCallAllowed=True,
        processSpawnAllowed=True,
        browserActionAllowed=True,
        childWorkspaceMutationAllowed=True,
        productionAuthority=True,
    )

    assert flags.execution_attempted is False
    assert flags.filesystem_mutation_allowed is False
    assert flags.network_call_allowed is False
    assert flags.process_spawn_allowed is False
    assert flags.browser_action_allowed is False
    assert flags.child_workspace_mutation_allowed is False
    assert flags.production_authority is False
    with pytest.raises(ValueError, match="model_construct"):
        SandboxAuthorityFlags.model_construct(productionAuthority=True)
    with pytest.raises(ValueError, match="model_copy update"):
        flags.model_copy(update={"productionAuthority": True})
    with pytest.raises(ValueError, match="copy update"):
        flags.copy(update={"productionAuthority": True})


def test_validation_errors_do_not_echo_private_inputs() -> None:
    rejected = "/Users/example/.ssh/id_rsa"

    with pytest.raises(ValidationError) as exc_info:
        SandboxDecision(
            allowed=False,
            operation="read",
            targetDigest=rejected,
            **{"private.ref": "x"},
        )

    encoded_error = json.dumps(exc_info.value.errors(), default=str)
    assert rejected not in str(exc_info.value)
    assert rejected not in encoded_error
    assert "private.ref" not in str(exc_info.value)
    assert "private.ref" not in encoded_error


def test_sandbox_import_does_not_attach_live_runtime_surfaces() -> None:
    script = """
import importlib
import json
import sys

before = set(sys.modules)
for module_name in (
    "magi_agent.sandbox.policy",
    "magi_agent.sandbox.filesystem",
    "magi_agent.sandbox.network",
    "magi_agent.sandbox.process",
    "magi_agent.sandbox.browser",
    "magi_agent.sandbox.child_workspace",
):
    importlib.import_module(module_name)
imported = set(sys.modules) - before
forbidden_prefixes = (
    "google.adk",
    "magi_agent.adk_bridge",
    "magi_agent.browser",
    "magi_agent.channels",
    "magi_agent.memory",
    "magi_agent.plugins",
    "magi_agent.tools",
    "magi_agent.transport",
    "magi_agent.web_acquisition",
    "magi_agent.workspace",
    "magi_agent.shadow",
    "kubernetes",
    "requests",
    "httpx",
    "urllib3",
)
blocked = sorted(
    module
    for module in imported
    if any(
        module == prefix or module.startswith(prefix + ".")
        for prefix in forbidden_prefixes
    )
)
print(json.dumps(blocked))
"""
    output = subprocess.check_output([sys.executable, "-c", script], text=True)
    assert json.loads(output) == []
