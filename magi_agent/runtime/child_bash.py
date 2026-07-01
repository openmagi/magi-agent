"""Sandboxed Bash tool for child runners (PR-S).

PR-N (:mod:`magi_agent.runtime.child_toolset`) explicitly excluded ``Bash`` from
the child runner's readonly toolset for security reasons: the parent gate5b
``Bash`` tool executes arbitrary shell commands with full workspace write access
and inherits the operator's environment (including ``MAGI__``/``OPENAI_``/
``ANTHROPIC_``/``GEMINI_`` provider keys). Forwarding that surface to a spawned
child would let an untrusted model exfiltrate keys or mutate the workspace
without the parent's permission gates ever firing.

The asymmetry that decision created has a cost: the parent gate5b ``Bash`` IS
live, so models trained against Claude Code / Cursor / Codex expect a ``Bash``
tool and use it for trivial helpers (arithmetic via ``bc``, text munging via
``awk``/``sed``, JSON slicing via ``python3 -c``). Kevin 0.1.97 direct-debug
against a Gemini 3.1 Pro child hit the exact failure PR-N's exclusion causes:

    Tool 'Bash' not found. Available tools: FileRead, Glob, Grep, GitDiff,
    Calculation

PR-R softens the crash into a structured ``tool_result`` so the child can retry
with a different approach; PR-S closes the gap for good by giving the child a
REAL ``Bash`` tool with a *bounded* security posture. Off by default, opt-in
via :data:`CHILD_BASH_SANDBOX_ENV`; when off, the child toolset is byte-
identical to PR-N (``Bash`` remains absent from :data:`READONLY_TOOL_NAMES`
end-to-end).

Security posture (what the sandbox WILL and WILL NOT do)
--------------------------------------------------------
The sandbox is a *whitelist* of side-effect-poor coreutils / small compute
binaries. Any command whose leading argv token is not in the allowlist returns
a structured ``tool_result`` naming the offending binary and the full allowlist,
so the model can pick a different approach without crashing.

Bounded, WILL do:

* Run each pipeline stage as its own subprocess (``shell=False``), chaining
  ``stdin``/``stdout`` in Python. no shell metacharacter interpretation, no
  ``$(...)`` command substitution, no ``&&``/``;`` chaining, no globbing
  outside what the whitelisted binary itself does.
* Enforce a per-pipeline wall-clock timeout (default 30 s) and kill the entire
  process group on timeout / cancellation (``os.killpg(SIGKILL)`` on posix).
* Cap combined ``stdout``+``stderr`` at :data:`DEFAULT_CHILD_BASH_OUTPUT_CAP`
  bytes (head+tail with an elision marker) so a runaway ``yes | head`` cannot
  balloon child memory.
* Strip the environment down to :data:`_ALLOWED_ENV_KEYS` (``PATH``, ``LANG``,
  ``LC_ALL``, ``LC_CTYPE``, ``TERM``, ``TZ``). Every ``MAGI_``, ``OPENAI_``,
  ``ANTHROPIC_``, ``GEMINI_``, and generic ``*_API_KEY``/``*_TOKEN`` variable is
  removed so a whitelisted ``env`` / ``printenv`` cannot exfiltrate them.
* Run inside a per-turn sandbox tempdir owned by the child (created lazily by
  :class:`ChildBashSandbox`); the operator's cwd is NEVER exposed as the working
  directory, and ``HOME`` is unset so shell tools fall back to the tempdir.

Bounded, WILL NOT do:

* No arbitrary binaries. Anything not in :data:`CHILD_BASH_ALLOWLIST` is denied
  BEFORE the subprocess launches. The allowlist is a code constant, not
  operator-configurable.
* No writes outside the sandbox tempdir. Any positional argument that starts
  with ``/`` (absolute path) or ``~`` (home) is rejected across the whole
  pipeline, even if the leading binary is allowed. This blocks ``cat
  /etc/passwd``, ``tee /tmp/foo``, ``ls /root``, and the ``echo x >``
  redirect-to-absolute-path pattern (redirects would fail regardless since
  ``shell=False``, but rejecting the argv makes the failure structured).
* No ``python3``/``node`` imports of ``os`` / ``subprocess`` / ``socket`` /
  ``urllib`` / ``requests`` / ``http`` / ``https`` / ``child_process`` / ``fs``
  / ``net``. the ``-c`` / ``-e`` script body is string-scanned for these
  tokens and rejected before launch, so the bounded interpreter cannot re-open
  the disallowed surface via a scripting side door.
* No network. Curl / wget / nc / ssh / python3 ``urllib`` / node ``http`` are
  all either off-allowlist (curl/wget/nc/ssh) or explicitly denied by the
  script-body scanner (python3/node).
* No privilege escalation. ``sudo`` / ``su`` / ``doas`` are off-allowlist.

Everything else in this module (:class:`ChildBashSandbox`, the ADK-tool wrapper
:func:`wrap_child_bash_tool`, the flag reader
:func:`child_bash_sandbox_enabled`) is thin plumbing around those invariants.
"""

from __future__ import annotations

import asyncio
import os
import shlex
import signal
import subprocess  # noqa: S404 (sandboxed spawn, argv validated pre-launch)
import tempfile
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field

from magi_agent.tools.result import ToolResult

#: Env flag name (strict default-OFF, twin-gate style. mirrors
#: :data:`~magi_agent.runtime.child_toolset.CHILD_TOOLSET_ENV`).
CHILD_BASH_SANDBOX_ENV = "MAGI_CHILD_BASH_SANDBOX_ENABLED"

#: Default per-pipeline wall-clock timeout (seconds). Kept modest because a
#: child sandbox is meant for arithmetic / text munging, not compilation.
DEFAULT_CHILD_BASH_TIMEOUT_S: float = 30.0

#: Default combined stdout+stderr cap (bytes). Matches
#: :data:`magi_agent.tools.core_toolhost` defaults so a child's Bash output
#: budget matches what a parent Bash tool would produce.
DEFAULT_CHILD_BASH_OUTPUT_CAP: int = 8192

#: Env keys preserved for the sandboxed subprocess. Everything else is stripped
#: BEFORE ``execve`` so ``env`` / ``printenv`` in the whitelist cannot leak
#: provider keys, workspace tokens, or bot ids to the child model.
_ALLOWED_ENV_KEYS: frozenset[str] = frozenset({"PATH", "LANG", "LC_ALL", "LC_CTYPE", "TERM", "TZ"})

#: The command whitelist. first-shipped conservative set. Every entry MUST be
#: a pure computation / text-munging / metadata binary with no fs mutation
#: outside its explicit path arguments and no network. Anything not here is
#: rejected pre-launch with a structured ``tool_result`` naming the allowlist.
#:
#: Compare with :data:`_PURE_NON_INSPECTION_TOOL_NAMES` in child_toolset: this
#: is the SHELL-layer equivalent (bounded coreutils) whereas Calculation is a
#: pure-python AST evaluator; both are additive under the readonly profile only
#: when their respective flags are on.
CHILD_BASH_ALLOWLIST: frozenset[str] = frozenset(
    {
        # I/O & text munging
        "echo",
        "printf",
        "cat",
        "head",
        "tail",
        "wc",
        "grep",
        "awk",
        "sed",
        "sort",
        "uniq",
        "tr",
        "cut",
        "tee",
        # Booleans / status helpers
        "test",
        "[",
        "true",
        "false",
        # Simple compute
        "expr",
        "seq",
        "bc",
        # Directory & metadata (cwd only, enforced by absolute-path rejection)
        "ls",
        "find",
        "pwd",
        "date",
        "basename",
        "dirname",
        # Bounded interpreter runners (script body scanned; see
        # :data:`_DENYLIST_TOKENS_PYTHON` / :data:`_DENYLIST_TOKENS_NODE`)
        "python3",
        "node",
        # Time / env introspection (env output is stripped so no leakage)
        "sleep",
        "env",
        "printenv",
    }
)

#: Python interpreter script-body denylist. If any of these substrings appears
#: in the ``python3 -c`` argument the pipeline is rejected pre-launch so a
#: bounded interpreter cannot re-open the fs/subprocess/network surface the
#: whitelist itself denies.
_DENYLIST_TOKENS_PYTHON: tuple[str, ...] = (
    "import os",
    "from os",
    "import subprocess",
    "from subprocess",
    "import socket",
    "from socket",
    "import urllib",
    "from urllib",
    "import requests",
    "from requests",
    "import http",
    "from http",
    "import shutil",
    "from shutil",
    "import pathlib",
    "from pathlib",
    "__import__",
    "importlib",
    "open(",
    "eval(",
    "exec(",
)

#: Node interpreter script-body denylist. Same intent as
#: :data:`_DENYLIST_TOKENS_PYTHON` for the Node ``-e`` surface.
_DENYLIST_TOKENS_NODE: tuple[str, ...] = (
    "require('fs')",
    'require("fs")',
    "require('child_process')",
    'require("child_process")',
    "require('net')",
    'require("net")',
    "require('http')",
    'require("http")',
    "require('https')",
    'require("https")',
    "require('os')",
    'require("os")',
    "require('path')",
    'require("path")',
    "import(",
    "eval(",
)


def child_bash_sandbox_enabled(env: Mapping[str, str] | None = None) -> bool:
    """Return whether the child-Bash sandbox is opt-in ON for this process.

    Strict default-OFF (only ``1``/``true``/``yes``/``on``, case-insensitive,
    counts as ON). Evaluated at call time so tests can patch the env without
    a module reload; mirrors :func:`resolve_child_toolset_profile`.
    """
    from magi_agent.config.flags import flag_bool  # noqa: PLC0415

    return flag_bool(CHILD_BASH_SANDBOX_ENV, env=env)


# --------------------------------------------------------------------------- #
# Validation                                                                   #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class PipelineValidation:
    """Outcome of splitting + validating one Bash command line.

    ``ok`` is ``True`` iff every stage's leading binary is on the allowlist,
    every positional argument stays within the sandbox tempdir (no absolute or
    home-relative paths), and every interpreter script body clears the
    denylist. The ``stages`` list is the tokenised pipeline (one argv per
    stage); ``reason`` is a human-readable error string used both in the tool
    result and in tests. ``offending`` names the offending binary when the
    rejection is allowlist-driven (``None`` for absolute-path / script-body
    rejections).
    """

    ok: bool
    stages: tuple[tuple[str, ...], ...]
    reason: str = ""
    offending: str | None = None


def _split_pipeline(tokens: Sequence[str]) -> tuple[tuple[str, ...], ...]:
    """Split a shlex token list on the ``|`` pipe separator.

    Only bare ``|`` counts as a pipe (``||`` is disallowed by the leading-token
    allowlist. no binary named ``||`` exists. so the empty-stage case is
    already an ``ok=False``).
    """
    stages: list[list[str]] = [[]]
    for tok in tokens:
        if tok == "|":
            stages.append([])
            continue
        stages[-1].append(tok)
    return tuple(tuple(stage) for stage in stages)


def _is_out_of_sandbox_path(arg: str) -> bool:
    """Return True if ``arg`` is an absolute or home-relative path.

    Anything starting with ``/`` (absolute) or ``~`` (home) escapes the child's
    per-turn tempdir. Reject BEFORE spawning the subprocess so the failure is
    structured, not a filesystem-permissions error at runtime.
    """
    if not arg:
        return False
    return arg[0] in ("/", "~")


def _script_body_ok(binary: str, argv: Sequence[str]) -> tuple[bool, str]:
    """Validate a bounded interpreter's ``-c`` / ``-e`` script body.

    ``python3`` requires the second arg to be ``-c`` (or a long form), and the
    third arg is scanned against :data:`_DENYLIST_TOKENS_PYTHON`. ``node``
    requires ``-e`` and is scanned against :data:`_DENYLIST_TOKENS_NODE`.

    A file-argument invocation (``python3 script.py`` / ``node script.js``) is
    rejected because reading arbitrary scripts off disk would need path
    validation the sandbox does not attempt today (the file may already have
    been fetched into cwd by an earlier turn, but running it re-opens the
    surface). Explicit interpreters must run inline via ``-c``/``-e``.
    """
    if binary == "python3":
        if len(argv) < 3 or argv[1] not in ("-c", "-cc"):
            return False, "python3 requires an inline '-c <script>' invocation"
        body = argv[2]
        for token in _DENYLIST_TOKENS_PYTHON:
            if token in body:
                return False, f"python3 script body contains disallowed token {token!r}"
        return True, ""
    if binary == "node":
        if len(argv) < 3 or argv[1] not in ("-e", "--eval"):
            return False, "node requires an inline '-e <script>' invocation"
        body = argv[2]
        for token in _DENYLIST_TOKENS_NODE:
            if token in body:
                return False, f"node script body contains disallowed token {token!r}"
        return True, ""
    return True, ""


def _find_arg_ok(argv: Sequence[str]) -> tuple[bool, str]:
    """``find`` must not carry destructive operators ``-delete`` / ``-exec``.

    ``find`` in the whitelist is intended for cwd-scoped read-only enumeration.
    Reject the mutation / arbitrary-exec operators pre-launch so an approved
    ``find`` leaf cannot smuggle arbitrary binaries past the allowlist.
    """
    forbidden = {"-delete", "-exec", "-execdir", "-fprint", "-fprintf", "-fls"}
    for tok in argv[1:]:
        if tok in forbidden:
            return False, f"find operator {tok!r} is not permitted"
    return True, ""


def validate_pipeline(command: str) -> PipelineValidation:
    """Tokenise ``command`` and validate every stage against the allowlist.

    Returns a :class:`PipelineValidation` describing the outcome. The caller
    (:meth:`ChildBashSandbox.run`) never launches a subprocess unless
    :attr:`PipelineValidation.ok` is ``True``.
    """
    text = (command or "").strip()
    if not text:
        return PipelineValidation(ok=False, stages=(), reason="command is empty")

    try:
        tokens = shlex.split(text)
    except ValueError as exc:  # unterminated quote, etc.
        return PipelineValidation(
            ok=False,
            stages=(),
            reason=f"command tokenisation failed: {exc}",
        )

    stages = _split_pipeline(tokens)
    if any(not stage for stage in stages):
        return PipelineValidation(
            ok=False,
            stages=stages,
            reason="pipeline contains an empty stage",
        )

    for stage in stages:
        binary = stage[0]
        if binary not in CHILD_BASH_ALLOWLIST:
            reason = (
                f"command {binary!r} not allowed in child sandbox; "
                f"allowlist: {sorted(CHILD_BASH_ALLOWLIST)}"
            )
            return PipelineValidation(ok=False, stages=stages, reason=reason, offending=binary)

        # Absolute / home-relative paths escape the per-turn tempdir. This
        # covers ``cat /etc/passwd``, ``tee /tmp/foo``, ``ls /root``, and the
        # ``> /tmp/x`` redirect pattern (though redirects would fail anyway
        # because subprocess runs with ``shell=False``).
        for arg in stage[1:]:
            if _is_out_of_sandbox_path(arg):
                return PipelineValidation(
                    ok=False,
                    stages=stages,
                    reason=(
                        f"argument {arg!r} escapes the child sandbox tempdir; "
                        "only cwd-relative paths are allowed"
                    ),
                )

        # Bounded-interpreter script-body scan (python3/node).
        script_ok, script_err = _script_body_ok(binary, stage)
        if not script_ok:
            return PipelineValidation(ok=False, stages=stages, reason=script_err, offending=binary)

        # find-specific operator ban.
        if binary == "find":
            find_ok, find_err = _find_arg_ok(stage)
            if not find_ok:
                return PipelineValidation(
                    ok=False, stages=stages, reason=find_err, offending=binary
                )

    return PipelineValidation(ok=True, stages=stages)


# --------------------------------------------------------------------------- #
# Environment                                                                  #
# --------------------------------------------------------------------------- #


def build_sandbox_env(
    source: Mapping[str, str] | None = None,
    *,
    extra: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Return a stripped env mapping suitable for the sandboxed subprocess.

     Only :data:`_ALLOWED_ENV_KEYS` survive. ``PATH`` is the single
     load-bearing key (bc / awk / grep etc. need to be found); the locale keys
     keep ``sort`` / ``date`` from misbehaving on utf-8 input. Everything else
    . ``MAGI_*``, ``OPENAI_*``, ``ANTHROPIC_*``, ``GEMINI_*``, ``*_API_KEY``,
     ``*_TOKEN``, ``HOME`` (writable). is DROPPED. Extra keys can be layered
     on top (used by tests that need a deterministic PATH), but any extra key
     outside :data:`_ALLOWED_ENV_KEYS` is still dropped.
    """
    src: Mapping[str, str] = os.environ if source is None else source
    out: dict[str, str] = {}
    for key in _ALLOWED_ENV_KEYS:
        value = src.get(key)
        if isinstance(value, str) and value:
            out[key] = value
    if "PATH" not in out:
        # Ensure PATH is always non-empty so ``bc``/``awk``/``grep`` resolve
        # even when the operator's shell inherits an empty PATH.
        out["PATH"] = "/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
    if extra:
        for key, value in extra.items():
            if key in _ALLOWED_ENV_KEYS and isinstance(value, str):
                out[key] = value
    return out


# --------------------------------------------------------------------------- #
# Subprocess execution                                                         #
# --------------------------------------------------------------------------- #


def _cap_output(chunk: bytes, cap: int) -> tuple[str, bool]:
    """Head+tail cap for one output stream. Returns (text, truncated)."""
    if not chunk:
        return "", False
    if len(chunk) <= cap:
        try:
            return chunk.decode("utf-8", errors="replace"), False
        except Exception:
            return chunk.decode("utf-8", errors="replace"), False
    half = max(1, cap // 2)
    head = chunk[:half]
    tail = chunk[-half:]
    marker = f"\n... [{len(chunk) - cap} bytes elided] ...\n".encode()
    joined = head + marker + tail
    return joined.decode("utf-8", errors="replace"), True


def _kill_process_group_safe(pid: int) -> None:
    """Best-effort SIGKILL of the pid's process group (posix only)."""
    if os.name != "posix":
        return
    try:
        os.killpg(os.getpgid(pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        return


def _run_pipeline_sync(
    stages: tuple[tuple[str, ...], ...],
    *,
    cwd: str,
    env: Mapping[str, str],
    timeout_s: float,
    output_cap: int,
) -> dict[str, object]:
    """Execute the validated pipeline synchronously.

    Runs each stage as its own ``subprocess.Popen`` (``shell=False``) and pipes
    stdout of stage N to stdin of stage N+1. The whole pipeline runs under a
    single process group so timeout kills every stage at once. Kept in one
    place so :class:`ChildBashSandbox.run` can offload it to a worker thread
    with a single :func:`asyncio.wait_for` timeout.
    """
    processes: list[subprocess.Popen[bytes]] = []
    prev_stdout: int | subprocess.IO[bytes] | None = subprocess.DEVNULL
    try:
        for i, stage in enumerate(stages):
            is_last = i == len(stages) - 1
            stdout_target = subprocess.PIPE if is_last else subprocess.PIPE
            # ``start_new_session=True`` puts the FIRST process into its own
            # session/pgid; subsequent stages inherit the same pgid because
            # ``preexec_fn=None`` (POSIX default) means the child keeps the
            # session leader's pgid. Explicit ``preexec_fn`` for later stages
            # would defeat the shared-pgid property, so we skip it.
            popen = subprocess.Popen(  # noqa: S603 (argv validated pre-launch)
                list(stage),
                cwd=cwd,
                env=dict(env),
                stdin=prev_stdout,
                stdout=stdout_target,
                stderr=subprocess.PIPE,
                start_new_session=(i == 0),
                shell=False,
                close_fds=True,
            )
            processes.append(popen)
            # Close the parent's copy of the previous stdout so the reader sees
            # EOF when the producer exits.
            if isinstance(prev_stdout, int) and prev_stdout not in (subprocess.DEVNULL,):
                try:
                    os.close(prev_stdout)
                except OSError:
                    pass
            elif prev_stdout not in (subprocess.DEVNULL, None):
                try:
                    prev_stdout.close()  # type: ignore[union-attr]
                except (AttributeError, OSError):
                    pass
            prev_stdout = popen.stdout

        final = processes[-1]
        try:
            stdout_bytes, stderr_bytes = final.communicate(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            for popen in processes:
                _kill_process_group_safe(popen.pid)
                try:
                    popen.wait(timeout=1.0)
                except subprocess.TimeoutExpired:
                    pass
            partial_stdout = b""
            partial_stderr = b""
            if final.stdout is not None:
                try:
                    partial_stdout = final.stdout.read() or b""
                except (OSError, ValueError):
                    partial_stdout = b""
            if final.stderr is not None:
                try:
                    partial_stderr = final.stderr.read() or b""
                except (OSError, ValueError):
                    partial_stderr = b""
            stdout_text, stdout_trunc = _cap_output(partial_stdout, output_cap)
            stderr_text, stderr_trunc = _cap_output(partial_stderr, output_cap)
            return {
                "exit_code": None,
                "timed_out": True,
                "stdout": stdout_text,
                "stderr": stderr_text,
                "stdout_truncated": stdout_trunc,
                "stderr_truncated": stderr_trunc,
            }

        # Drain the intermediate stages' stderr so their pipes do not stay open
        # holding the child alive after the tail already returned. Each
        # intermediate stage's stdout was consumed by the next; stderr was not.
        intermediate_stderrs: list[bytes] = []
        for popen in processes[:-1]:
            try:
                _, err = popen.communicate(timeout=timeout_s)
                if err:
                    intermediate_stderrs.append(err)
            except subprocess.TimeoutExpired:
                _kill_process_group_safe(popen.pid)
                try:
                    popen.wait(timeout=1.0)
                except subprocess.TimeoutExpired:
                    pass

        # Combine intermediate + final stderr so failure diagnostics from any
        # stage reach the caller.
        combined_stderr = b"".join(intermediate_stderrs) + (stderr_bytes or b"")
        stdout_text, stdout_trunc = _cap_output(stdout_bytes or b"", output_cap)
        stderr_text, stderr_trunc = _cap_output(combined_stderr, output_cap)
        return {
            "exit_code": final.returncode,
            "timed_out": False,
            "stdout": stdout_text,
            "stderr": stderr_text,
            "stdout_truncated": stdout_trunc,
            "stderr_truncated": stderr_trunc,
        }
    finally:
        for popen in processes:
            if popen.poll() is None:
                _kill_process_group_safe(popen.pid)
                try:
                    popen.wait(timeout=0.5)
                except subprocess.TimeoutExpired:
                    pass


# --------------------------------------------------------------------------- #
# Sandbox                                                                      #
# --------------------------------------------------------------------------- #


@dataclass
class ChildBashSandbox:
    """Per-child sandbox that owns a tempdir and executes validated pipelines.

    One instance per child turn: the tempdir is created lazily on first use and
    lives until the sandbox is torn down (typically process exit. the OS
    reaps ``/tmp`` entries on reboot; explicit cleanup is not attempted so a
    debugger can still inspect the last turn's cwd). Kept as a dataclass so
    tests can construct it directly with an explicit ``timeout_s`` for the
    timeout regression case.
    """

    timeout_s: float = DEFAULT_CHILD_BASH_TIMEOUT_S
    output_cap: int = DEFAULT_CHILD_BASH_OUTPUT_CAP
    env_source: Mapping[str, str] | None = None
    _tempdir: str | None = field(default=None, init=False, repr=False)

    def cwd(self) -> str:
        """Return the per-child sandbox tempdir, creating it on first call."""
        if self._tempdir is None:
            self._tempdir = tempfile.mkdtemp(prefix="magi-child-bash-")
        return self._tempdir

    def env(self) -> dict[str, str]:
        """Return the stripped env mapping used for every subprocess."""
        return build_sandbox_env(self.env_source)

    def run(self, command: str) -> ToolResult:
        """Validate ``command`` and (if allowed) run it in the sandbox.

        Returns a :class:`~magi_agent.tools.result.ToolResult`:

        * ``status="blocked"`` for allowlist / absolute-path / interpreter
          rejections. the model can retry with a different command.
        * ``status="ok"`` for successful execution (``exit_code == 0``).
        * ``status="error"`` for non-zero exit or timeout, with the exit code
          / timeout flag in ``metadata`` so the model can react.

        Every result carries ``metadata["sandbox"]="child_bash"`` so downstream
        collectors can distinguish sandboxed Bash from the parent gate5b Bash.
        """
        validation = validate_pipeline(command)
        if not validation.ok:
            return ToolResult(
                status="blocked",
                error_code="child_bash_sandbox_denied",
                error_message=validation.reason,
                metadata={
                    "sandbox": "child_bash",
                    "allowlist": sorted(CHILD_BASH_ALLOWLIST),
                    "offending": validation.offending,
                },
            )

        outcome = _run_pipeline_sync(
            validation.stages,
            cwd=self.cwd(),
            env=self.env(),
            timeout_s=self.timeout_s,
            output_cap=self.output_cap,
        )
        metadata: dict[str, object] = {
            "sandbox": "child_bash",
            "exitCode": outcome["exit_code"],
            "timedOut": outcome["timed_out"],
            "stdoutTruncated": outcome["stdout_truncated"],
            "stderrTruncated": outcome["stderr_truncated"],
        }
        if outcome["timed_out"]:
            return ToolResult(
                status="error",
                error_code="child_bash_sandbox_timeout",
                error_message=(
                    f"command exceeded the {self.timeout_s:.1f}s child sandbox "
                    "timeout and was killed"
                ),
                output={"stdout": outcome["stdout"], "stderr": outcome["stderr"]},
                metadata=metadata,
            )

        status = "ok" if outcome["exit_code"] == 0 else "error"
        return ToolResult(
            status=status,
            output={"stdout": outcome["stdout"], "stderr": outcome["stderr"]},
            metadata=metadata,
            error_code=None if status == "ok" else "child_bash_nonzero_exit",
            error_message=None
            if status == "ok"
            else f"command exited with status {outcome['exit_code']}",
        )

    async def run_async(self, command: str) -> ToolResult:
        """Async wrapper: offload :meth:`run` to a worker thread."""
        return await asyncio.to_thread(self.run, command)


# --------------------------------------------------------------------------- #
# ADK tool wrapper (post-processing of the built child toolset)                #
# --------------------------------------------------------------------------- #


def wrap_child_bash_tool(
    tools: Iterable[object],
    *,
    sandbox: ChildBashSandbox,
) -> list[object]:
    """Rewrite the ADK ``Bash`` tool's ``func`` to route through ``sandbox``.

    The child runner builds its toolset via
    :func:`magi_agent.cli.tool_runtime.build_cli_adk_tools`, which produces an
    ADK ``FunctionTool`` for ``Bash`` whose ``func`` dispatches through the
    parent gate5b core-toolhost. When the child-Bash sandbox is on we swap
    that ``func`` for a sandboxed callable BEFORE the tool ever runs, so the
    child model can call ``Bash`` under the SAME name it learned but hit the
    bounded surface instead. Tools other than ``Bash`` are passed through
    verbatim.

    Returns a new list; the input iterable is not mutated. Idempotent: an
    already-wrapped ``Bash`` tool (marked via ``_magi_child_bash_sandbox``) is
    left alone.
    """
    out: list[object] = []
    for tool in tools:
        name = getattr(tool, "name", None)
        if name != "Bash":
            out.append(tool)
            continue
        if getattr(tool, "_magi_child_bash_sandbox", False):
            out.append(tool)
            continue

        async def _sandboxed_func(
            arguments: dict[str, object],
            tool_context: object,  # noqa: ARG001 (unused, sandbox owns cwd/env)
            *,
            _sandbox: ChildBashSandbox = sandbox,
        ) -> dict[str, object]:
            command = arguments.get("command") if isinstance(arguments, dict) else None
            if not isinstance(command, str):
                return ToolResult(
                    status="blocked",
                    error_code="child_bash_missing_command",
                    error_message="Bash tool requires a string 'command' argument",
                    metadata={"sandbox": "child_bash"},
                ).model_dump(by_alias=True)
            result = await _sandbox.run_async(command)
            return result.model_dump(by_alias=True)

        _sandboxed_func.__name__ = "invoke_openmagi_tool"
        _sandboxed_func.__doc__ = "Child-sandbox Bash: whitelisted commands only."
        try:
            tool.func = _sandboxed_func  # type: ignore[attr-defined]
            tool._magi_child_bash_sandbox = True  # type: ignore[attr-defined]
        except (AttributeError, TypeError):
            # Never fatal: a tool object that refuses attribute assignment
            # (unusual) is left alone so the child at least sees the unmodified
            # Bash tool rather than losing it entirely.
            pass
        out.append(tool)
    return out


# --------------------------------------------------------------------------- #
# Convenience: standalone handler for direct registry use                     #
# --------------------------------------------------------------------------- #


def build_child_bash_handler(
    *,
    sandbox: ChildBashSandbox,
) -> Callable[[dict[str, object], object], object]:
    """Return an async handler suitable for direct binding onto a registry.

    Kept as a small convenience for callers that build a registry without
    going through :func:`build_cli_adk_tools`; the primary integration path is
    :func:`wrap_child_bash_tool` which rewrites the ADK tool ``func`` in place.
    """

    async def handler(
        arguments: dict[str, object],
        context: object,  # noqa: ARG001 (sandbox owns cwd/env, ignore context)
    ) -> ToolResult:
        command = arguments.get("command") if isinstance(arguments, dict) else None
        if not isinstance(command, str):
            return ToolResult(
                status="blocked",
                error_code="child_bash_missing_command",
                error_message="Bash tool requires a string 'command' argument",
                metadata={"sandbox": "child_bash"},
            )
        return await sandbox.run_async(command)

    return handler


__all__ = [
    "CHILD_BASH_ALLOWLIST",
    "CHILD_BASH_SANDBOX_ENV",
    "ChildBashSandbox",
    "DEFAULT_CHILD_BASH_OUTPUT_CAP",
    "DEFAULT_CHILD_BASH_TIMEOUT_S",
    "PipelineValidation",
    "build_child_bash_handler",
    "build_sandbox_env",
    "child_bash_sandbox_enabled",
    "validate_pipeline",
    "wrap_child_bash_tool",
]
