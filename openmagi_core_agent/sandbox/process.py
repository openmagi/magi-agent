from __future__ import annotations

from collections.abc import Mapping, Sequence
import re
from urllib.parse import unquote, urlparse

from .filesystem import resolve_workspace_path
from .network import classify_network_url
from .policy import SandboxDecision, SandboxPolicy, build_decision, digest_payload


_SECRET_ENV_KEY_RE = re.compile(
    r"(?:TOKEN|SECRET|PASSWORD|CREDENTIAL|PRIVATE_KEY|API_KEY|AUTH|SESSION|COOKIE|"
    r"DATABASE_URL|DB_URL|REDIS_URL|CONNECTOR_URL)",
    re.IGNORECASE,
)
_SECRET_ENV_VALUE_RE = re.compile(
    r"(?:Bearer\s+\S+|gh[opusr]_[A-Za-z0-9_]{8,}|sk-[A-Za-z0-9._-]{8,}|"
    r"AKIA[A-Z0-9]{8,}|REDACTED_TEST_VALUE|[a-z][a-z0-9+.-]*://[^\s/@]+:[^\s/@]+@)",
    re.IGNORECASE,
)
_BLOCKED_EXECUTABLES = frozenset(
    {
        "bash",
        "curl",
        "docker",
        "kubectl",
        "nc",
        "netcat",
        "osascript",
        "scp",
        "sh",
        "ssh",
        "sudo",
        "wget",
    }
)
_DYNAMIC_CODE_EXECUTABLES = frozenset(
    {
        "bun",
        "corepack",
        "node",
        "npm",
        "npx",
        "pip",
        "pip3",
        "pnpm",
        "poetry",
        "pytest",
        "python",
        "python3",
        "tox",
        "uv",
        "yarn",
    }
)
_DYNAMIC_CODE_EXECUTABLE_RE = re.compile(
    r"^(?:"
    r"bun|corepack|node(?:js)?(?:\d+(?:\.\d+)*)?|npm|npx|pnpm|"
    r"pip(?:\d+(?:\.\d+)*)?|poetry|py(?:thon)?(?:\d+(?:\.\d+)*)?|"
    r"py\.test|pytest(?:[-_]?\d+(?:\.\d+)*)?|tox|uvx?|yarn"
    r")$",
    re.IGNORECASE,
)
_URL_RE = re.compile(r"https?://[^\s'\"]+")


def evaluate_process_request(
    policy: SandboxPolicy,
    *,
    command: Sequence[str],
    env: Mapping[str, str],
    cwd: str,
) -> SandboxDecision:
    reason_codes: list[str] = []
    command_tuple = tuple(str(part) for part in command)
    executable = command_tuple[0] if command_tuple else ""
    _resolved_cwd, escaped = resolve_workspace_path(policy, cwd)

    if not command_tuple:
        reason_codes.append("empty_command_blocked")
    if not policy.allow_process:
        reason_codes.append("process_disabled")
    if executable not in policy.allowed_processes:
        reason_codes.append("process_not_allowlisted")
    if executable in _BLOCKED_EXECUTABLES or _contains_process_escape(command_tuple):
        reason_codes.append("process_escape_blocked")
    if escaped:
        reason_codes.append("workspace_escape_blocked")
    if _has_secret_env(env):
        reason_codes.append("secret_env_blocked")
    for url in _URL_RE.findall(" ".join(command_tuple)):
        _host, url_reasons = classify_network_url(url)
        reason_codes.extend(url_reasons)

    return build_decision(
        allowed=not reason_codes,
        operation="execute",
        reason_codes=tuple(reason_codes),
        target_digest=digest_payload(
            {
                "commandDigest": digest_payload({"command": command_tuple}),
                "envKeys": tuple(sorted(env)),
                "cwdDigest": digest_payload({"cwd": cwd}),
            }
        ),
        target_kind="process_command",
        policy=policy,
    )


def _contains_process_escape(command: Sequence[str]) -> bool:
    joined = " ".join(command)
    executable = command[0] if command else ""
    argv_tokens = {part.rsplit("/", 1)[-1].lower() for part in command}
    if argv_tokens & _DYNAMIC_CODE_EXECUTABLES or any(
        _DYNAMIC_CODE_EXECUTABLE_RE.fullmatch(token) for token in argv_tokens
    ):
        return True
    if executable in {"env", "find", "xargs"}:
        return True
    if executable in _DYNAMIC_CODE_EXECUTABLES:
        return True
    if executable in {"python", "python3", "node"} and any(flag in command for flag in ("-c", "-e")):
        return True
    if executable == "node" and any(flag in command for flag in ("--eval", "-p", "--print")):
        return True
    if executable == "python" and len(command) > 2 and command[1] == "-m" and command[2] in {"pip", "ensurepip", "venv"}:
        return True
    if executable == "npm" and len(command) > 1 and command[1] in {
        "exec",
        "install",
        "run",
        "start",
        "test",
        "x",
    }:
        return True
    lowered = joined.lower()
    return any(
        marker in joined for marker in ("&&", "||", ";", "`", "$(", ">|", "<(")
    ) or any(
        marker in lowered
        for marker in ("child_process", "__import__('os')", "__import__(\"os\")", ".system(", "execfile", "spawn(")
    )


def _has_secret_env(env: Mapping[str, str]) -> bool:
    for key, value in env.items():
        if _SECRET_ENV_KEY_RE.search(key) or _SECRET_ENV_VALUE_RE.search(str(value)):
            return True
        if _url_value_has_credentials(str(value)):
            return True
        if "://" in str(value):
            _host, url_reasons = classify_network_url(str(value))
            if any(
                reason in url_reasons
                for reason in (
                    "credential_url_blocked",
                    "metadata_endpoint_blocked",
                    "private_network_blocked",
                )
            ):
                return True
    return False


def _url_value_has_credentials(value: str) -> bool:
    current = value
    for _ in range(4):
        parsed = urlparse(current)
        if parsed.scheme and parsed.netloc and (parsed.username or parsed.password):
            return True
        decoded = unquote(current)
        if decoded == current:
            break
        current = decoded
    return False


__all__ = ["evaluate_process_request"]
