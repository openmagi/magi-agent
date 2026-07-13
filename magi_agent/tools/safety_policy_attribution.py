"""Attribution mapping from safety.py deny reason codes to system_safety policy members.

Used by the deny-event emitter (tools/kernel.py) to attach policyId/ruleId to
tool-denied evidence records so safety denials are visible in the audit trail.

Design: every deny reason code producible by safety.py decision branches must
be either in SAFETY_REASON_TO_MEMBER (mapped to a system_safety member ruleId)
or in EXCLUDED_DENY_REASONS (explicitly excluded with a stated rationale).
The U1 exhaustiveness test enforces this invariant so new deny reason codes
cannot silently go unattributed.
"""
from __future__ import annotations

SAFETY_REASON_TO_MEMBER: dict[str, str] = {
    # Destructive shell: rm -rf /, dd, mkfs, disk erase, recursive chmod/chown
    "destructive_shell": "system_safety.destructive_shell",
    "system_shell_denied": "system_safety.destructive_shell",
    # bypass_denied_hard_safety collapses destructive + system-boundary denies
    # under bypass into one reason code; mapped to destructive_shell for v1.
    "bypass_denied_hard_safety": "system_safety.destructive_shell",
    # Curl-pipe-exec: piping a download into a shell (curl|sh pattern)
    "curl_pipe_exec": "system_safety.curl_pipe_exec",
    # Network exfiltration: upload-shaped commands (scp, rsync, sftp, piped curl/wget)
    "network_exfiltration_denied": "system_safety.network_exfiltration",
    # Inline interpreter: python3 -c / perl -e / ruby -e in default posture
    "interpreter_inline_code_denied": "system_safety.inline_interpreter",
    # Workspace confinement: path escapes, absolute paths, sealed files, memory paths
    "path_escapes_workspace": "system_safety.workspace_confinement",
    "system_path_denied": "system_safety.workspace_confinement",
    "absolute_path_denied": "system_safety.workspace_confinement",
    "sealed_file_write_blocked": "system_safety.workspace_confinement",
    "protected_memory_path": "system_safety.workspace_confinement",
    # Secret paths: .env, credentials, API-key files; env executable (env-var leak)
    "secret_path_denied": "system_safety.secret_paths",
    "env_leak_denied": "system_safety.secret_paths",
    # Shell hygiene: path expansion, mutating flags, unsafe flags, unknown executables
    "shell_path_expansion_denied": "system_safety.shell_hygiene",
    "mutating_shell_flag_denied": "system_safety.shell_hygiene",
    "mutating_command_flag_denied": "system_safety.shell_hygiene",
    "unsafe_command_flag_denied": "system_safety.shell_hygiene",
    "safe_command_executable_denied": "system_safety.shell_hygiene",
    "safe_command_shell_expansion_denied": "system_safety.shell_hygiene",
    # Unsafe git: push --force, reset --hard, gc/prune/reflog/fsck subcommands
    "unsafe_git": "system_safety.unsafe_git",
}

EXCLUDED_DENY_REASONS: frozenset[str] = frozenset(
    {
        # plan_mode_mutation_blocked: plan-mode semantics, not machine protection.
        "plan_mode_mutation_blocked",
        # complex_shell_requires_approval DENY variant: selected_full_toolhost
        # approval-routing (posture, not a machine-protection class).
        "complex_shell_requires_approval",
        # path_required: malformed call guard (missing argument), not safety.
        "path_required",
        # read_ledger_preflight_blocked: read-ledger preflight check, not safety policy.
        "read_ledger_preflight_blocked",
    }
)


def attribute_safety_decision(reason_code: str) -> dict[str, str] | None:
    """Return {policyId, ruleId} for a safety deny reason code, or None.

    Returns None for excluded codes and for unknown/unmapped codes (fail-quiet).
    The U1 exhaustiveness test ensures that in practice every deny reason code
    is either mapped or explicitly excluded, so None at runtime means excluded.
    """
    member = SAFETY_REASON_TO_MEMBER.get(reason_code)
    if member is None:
        return None
    return {"policyId": "system_safety", "ruleId": member}
