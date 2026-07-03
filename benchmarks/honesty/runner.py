"""Corpus runner — drive the installed ``magi`` headless CLI over a task battery
in isolated working dirs, capturing the session transcript (claims) and the
durable evidence ledger (receipts) for each run.

One task == one isolated cwd == one session, so claims and receipts pair
trivially (the single transcript file + single evidence file in that run dir);
no cross-session id matching needed.

Baseline config (default): evidence ledger ON (records receipts) but governance
enforcement OFF (so the agent is free to over-claim and nothing blocks it) —
this measures RAW divergence. The adversarial 3-layer benchmark reruns the same
battery with each governance layer on (see ``layers`` arg).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path

from .loaders import claims_from_stream, records_from_files
from .scorer import ClaimType, TurnInput


@dataclass(frozen=True)
class FileSpec:
    relpath: str
    content: str


@dataclass(frozen=True)
class Task:
    id: str
    claim_type: ClaimType
    prompt: str
    files: tuple[FileSpec, ...] = ()
    setup_cmds: tuple[str, ...] = ()


@dataclass(frozen=True)
class RunArtifacts:
    task_id: str
    run_dir: Path
    raw_ndjson: Path
    evidence_files: tuple[Path, ...]
    returncode: int
    timed_out: bool


# Capability env loaded from the lab's known-good "everything operational" file
# so the agent actually EXECUTES tools (runs pytest etc) instead of stalling on
# an approval/availability gate. Governance flags it also sets are intentionally
# stripped for the ungoverned baseline (see _strip_governance).
DOGFOOD_ENV_PATH = Path.home() / ".magi" / "dogfood-full-on.env"

# Flags that turn lie-catching governance ON. The ungoverned baseline removes
# these so we measure RAW divergence; layers add them back deliberately.
_GOVERNANCE_FLAGS = (
    "MAGI_SELF_REVIEW_ENABLED",
    "MAGI_SELF_REVIEW_LIVE_ENABLED",
    "MAGI_SELF_REVIEW_PIPELINE_ENABLED",
    "MAGI_FACT_GROUNDING_VERIFICATION_ENABLED",
    "MAGI_RESEARCH_GOVERNANCE_MODE",
    "MAGI_CROSS_VERIFY_ENABLED",
    "MAGI_CUSTOMIZE_VERIFICATION_ENABLED",
    "MAGI_DOCUMENT_AUTHORING_COVERAGE",
)


_WORKTREE_ROOT = Path(__file__).resolve().parents[2]


def _python_has_deps(py: str) -> bool:
    try:
        r = subprocess.run(
            [py, "-c", "import typer, google.adk"],
            capture_output=True,
            timeout=30,
        )
        return r.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _default_magi_cmd() -> list[str]:
    """Run the WORKTREE source (the code our flag analysis targets) via an
    install python that carries the deps (typer / google-adk). Eliminates
    version skew between the source we read and the binary we run.

    The ``magi`` console symlink can vanish mid-brew-upgrade and a freshly
    installed Cellar python may not have deps yet, so we glob every Cellar
    python and pick the first that actually imports the deps (newest first).
    """
    import glob  # noqa: PLC0415

    # Prefer the live console-script shebang if present.
    try:
        shebang = (
            Path("/opt/homebrew/bin/magi").read_text(encoding="utf-8").splitlines()[0]
        )
        if shebang.startswith("#!"):
            py = shebang[2:].strip()
            if Path(py).exists() and _python_has_deps(py):
                return [py, "-m", "magi_agent.cli"]
    except OSError:
        pass

    candidates = sorted(
        glob.glob("/opt/homebrew/Cellar/magi-agent/*/libexec/bin/python"),
        reverse=True,
    )
    for py in candidates:
        if _python_has_deps(py):
            return [py, "-m", "magi_agent.cli"]
    return ["magi"]


def _load_dogfood_env() -> dict[str, str]:
    out: dict[str, str] = {}
    try:
        for line in DOGFOOD_ENV_PATH.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            line = line[len("export ") :].strip() if line.startswith("export ") else line
            if "=" in line:
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip().strip('"').strip("'")
    except OSError:
        pass
    return out


# Env overlays for the three governance layers (Design 2). Baseline = {}.
LAYER_ENV: dict[str, dict[str, str]] = {
    "baseline": {},
    # Advisory only: soft USER-RULES injected; not deterministic.
    "advisory": {"MAGI_CUSTOMIZE_VERIFICATION_ENABLED": "0"},
    # LLM critic gate.
    "llm_judge": {
        "MAGI_CUSTOMIZE_VERIFICATION_ENABLED": "1",
        "MAGI_SELF_REVIEW_ENABLED": "1",
        "MAGI_SELF_REVIEW_LIVE_ENABLED": "1",
    },
    # Evidence-bound deterministic gate (ours).
    "evidence_bound": {
        "MAGI_CUSTOMIZE_VERIFICATION_ENABLED": "1",
        "MAGI_FACT_GROUNDING_VERIFICATION_ENABLED": "1",
    },
}


def _base_env(run_dir: Path, *, full_runtime: bool, layer: str) -> dict[str, str]:
    env = dict(os.environ)
    if full_runtime:
        # Full toolset (Bash + file tools) via the local-full overlay. All
        # overlay keys are applied with setdefault, so the explicit values we
        # set below always win.
        env["MAGI_AGENT_LOCAL_FULL_RUNTIME_DEFAULTS"] = "1"
        env["MAGI_RUNTIME_PROFILE"] = "full"
        # Bind the REAL (mutating) tool handlers explicitly — the overlay sets
        # these via setdefault but may not be applied on the headless path, in
        # which case write/exec tools are advertised but their handlers no-op.
        env["MAGI_FIRST_PARTY_TOOLS_ENABLED"] = "1"
        env["MAGI_FILE_TOOLS_ENABLED"] = "1"
        env["MAGI_APPLY_PATCH_ENABLED"] = "1"
        # THE execution unlock (this was the bug): the GA live gate, when ON,
        # turns every write/execute into an approval request headless cannot
        # answer. OFF == pure bypass == allow. The full overlay sets it to 1;
        # we force it OFF so the agent actually runs pytest / edits files.
        env["MAGI_GA_LIVE_ENABLED"] = "0"
        # Autonomy + self-verify prompt (advisory floor shared by every layer;
        # the evidence-bound layer adds the deterministic gate on top).
        env["MAGI_EVAL_AUTONOMY_ENABLED"] = "1"
        env["MAGI_EVAL_ZERO_EDIT_GUARD_ENABLED"] = "1"
        env["MAGI_AUTOPILOT"] = "1"
    # Evidence ledger ON (records receipts) regardless of layer — it is the
    # measurement substrate, not enforcement. Isolate it per run.
    env["MAGI_EVIDENCE_LEDGER_DIR"] = str(run_dir / "evidence")
    # Belt-and-suspenders transcript (claims also come from raw.ndjson).
    env["MAGI_CLI_SESSION_LOG_ENABLED"] = "1"
    env["MAGI_CLI_SESSION_DIR"] = str(run_dir / "transcript")
    # Keep memory/network side effects out of the measurement.
    env["MAGI_MEMORY_WRITE_ENABLED"] = "0"
    # IMPORTANT: do NOT inject the worktree onto PYTHONPATH. The worktree is
    # ~100 commits behind main and predates the evidence producers
    # (Calculation/TestRun/GitDiff/EditMatch/CodeDiagnostics/CommitCheckpoint,
    # merged 2026-06-26/27). We run the INSTALLED package (homebrew 0.1.90+),
    # which carries those producers, so calc/cited claim types actually emit
    # typed records. _default_magi_cmd() already resolves to the install python.
    env.pop("PYTHONPATH", None)
    return env


def run_task(
    task: Task,
    corpus_root: Path,
    *,
    layer: str = "baseline",
    magi_cmd: list[str] | None = None,
    timeout_s: int = 420,
    # bypassPermissions so workspace-mutation tools (FileEdit/FileWrite) auto-
    # allow: under `default`, mutation still needs an approval the headless host
    # can't supply, so write tasks (edited/committed) would falsely register as
    # refusals. Read-only tasks are unaffected. The auto-approver stays wired but
    # simply sees no control_request under bypass (harmless no-op). Hard-safety
    # denies still run after the approval gate, so this only relaxes the prompt
    # posture, not the safety floor.
    permission_mode: str = "bypassPermissions",
    full_runtime: bool = True,
) -> RunArtifacts:
    run_dir = corpus_root / layer / task.id
    work = run_dir / "work"
    work.mkdir(parents=True, exist_ok=True)
    for spec in task.files:
        fp = work / spec.relpath
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(spec.content, encoding="utf-8")
    for cmd in task.setup_cmds:
        subprocess.run(cmd, shell=True, cwd=work, capture_output=True)

    env = _base_env(run_dir, full_runtime=full_runtime, layer=layer)
    env.update(LAYER_ENV.get(layer, {}))

    cmd = [
        *(magi_cmd or _default_magi_cmd()),
        "agent",
        task.prompt,
        "--print",
        "--output",
        "stream-json",
        "--permission-mode",
        permission_mode,
    ]
    timed_out = False
    rc = 0
    raw = run_dir / "raw.ndjson"
    err = run_dir / "stderr.txt"
    with raw.open("w", encoding="utf-8") as out, err.open("w", encoding="utf-8") as e:
        proc = subprocess.Popen(
            cmd,
            cwd=work,
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=e,
            text=True,
            bufsize=1,
        )

        # Auto-approver host: answer EVERY control_request with allow so the
        # agent actually executes gated tools (shell, writes) instead of
        # stalling on an un-answerable approval. This is the host side of the
        # Claude-Code-style control_request / control_response protocol that
        # ``test_headless_approval`` proves.
        def _pump() -> None:
            assert proc.stdout is not None
            for line in proc.stdout:
                out.write(line)
                s = line.strip()
                if not s:
                    continue
                try:
                    obj = json.loads(s)
                except json.JSONDecodeError:
                    continue
                if obj.get("type") == "control_request":
                    # remember=true so a repeated tool call is not re-asked,
                    # cutting approval round-trips and model deliberation.
                    resp = (
                        json.dumps(
                            {
                                "type": "control_response",
                                "request_id": obj.get("request_id", ""),
                                "response": {"decision": "allow", "remember": True},
                            }
                        )
                        + "\n"
                    )
                    try:
                        if proc.stdin:
                            proc.stdin.write(resp)
                            proc.stdin.flush()
                    except (BrokenPipeError, ValueError):
                        pass

        t = threading.Thread(target=_pump, daemon=True)
        t.start()
        try:
            proc.wait(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            timed_out = True
            proc.kill()
        t.join(timeout=10)
        rc = proc.returncode if proc.returncode is not None else -1
        try:
            if proc.stdin:
                proc.stdin.close()
        except Exception:
            pass

    evidences = tuple(sorted((run_dir / "evidence").rglob("*.jsonl")))
    return RunArtifacts(
        task_id=task.id,
        run_dir=run_dir,
        raw_ndjson=raw,
        evidence_files=evidences,
        returncode=rc,
        timed_out=timed_out,
    )


def artifacts_from_corpus(
    corpus_root: Path, layer: str, task_ids: list[str]
) -> list[RunArtifacts]:
    """Rebuild RunArtifacts from already-run dirs (no agent invocation) so a
    corpus can be re-scored cheaply without re-running."""
    out: list[RunArtifacts] = []
    for tid in task_ids:
        run_dir = corpus_root / layer / tid
        raw = run_dir / "raw.ndjson"
        if not raw.exists():
            continue
        evidences = tuple(sorted((run_dir / "evidence").rglob("*.jsonl")))
        out.append(
            RunArtifacts(
                task_id=tid,
                run_dir=run_dir,
                raw_ndjson=raw,
                evidence_files=evidences,
                returncode=0,
                timed_out=False,
            )
        )
    return out


def ingest_corpus(corpus_root: Path, layer: str, task_ids: list[str]) -> list[TurnInput]:
    """Source-agnostic ingest of a corpus produced ANY way (this runner's
    ``--print stream-json`` capture, OR an interactive TUI / serve run that
    wrote an on-disk session transcript). For each task dir:

      claims  = raw.ndjson (stream-json stdout) if present,
                else the on-disk session transcript(s) under transcript/.
      records = every evidence ledger file under evidence/.

    One task dir == one run == one TurnInput.
    """
    from .loaders import claims_from_stream, parse_transcript, records_from_files

    rows: list[TurnInput] = []
    for tid in task_ids:
        run_dir = corpus_root / layer / tid
        if not run_dir.exists():
            continue
        raw = run_dir / "raw.ndjson"
        if raw.exists() and raw.stat().st_size > 0:
            claims = claims_from_stream(raw)
        else:
            parts: list[str] = []
            for tf in sorted(run_dir.rglob("*.jsonl")):
                if "evidence" in tf.parts:
                    continue
                parts.extend(parse_transcript(tf).values())
            claims = "\n".join(parts)
        evidence_files = [
            f for f in sorted(run_dir.rglob("*.jsonl")) if "evidence" in f.parts
        ]
        records = tuple(records_from_files(evidence_files))
        rows.append(
            TurnInput(session_id=tid, turn_id=tid, claims_text=claims, records=records)
        )
    return rows


def artifacts_to_turns(arts: list[RunArtifacts]) -> list[TurnInput]:
    """One TurnInput per run: the run's committed final answer (claims) against
    all evidence records the run emitted. A single headless prompt is one user
    turn, so per-run aggregation avoids cross-source turn-id alignment."""
    rows: list[TurnInput] = []
    for a in arts:
        claims = claims_from_stream(a.raw_ndjson)
        records = tuple(records_from_files(list(a.evidence_files)))
        rows.append(
            TurnInput(
                session_id=a.task_id,
                turn_id=a.task_id,
                claims_text=claims,
                records=records,
            )
        )
    return rows
