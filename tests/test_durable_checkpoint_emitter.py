"""WS1 PR1c - persisted-aligned digests + checkpoint assembly (section 0.4).

``compute_persisted_digests`` is the SINGLE function the headless tap (emit) and
the boot sweep (recovery, PR1d) both call over the IDENTICAL persisted bytes:
the Envelope prefix truncated at ``watermark_uuid`` + the persisted evidence
JSONL pinned by ``evidence_line_count``. Its three real digests
(``state_digest``/``ledger_head_digest``/``context_projection_digest``) are
byte-reproducible across a fresh process; ``effective_policy_snapshot_digest``
is a fixed sentinel (the 11 ``build_effective_policy_snapshot`` inputs are not
cold-boot reconstructable, section 0.4 mechanism 2).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

from magi_agent.cli.session_log import SessionLog
from magi_agent.runtime.checkpointing import (
    ExecutionCheckpoint,
    verify_resume_request,
)
from magi_agent.runtime.durable_checkpoint_emitter import (
    POLICY_SNAPSHOT_SENTINEL,
    CheckpointDigests,
    build_checkpoint,
    compute_persisted_digests,
)
from magi_agent.runtime.events import RuntimeEvent


def _text_event(text: str) -> RuntimeEvent:
    return RuntimeEvent(type="token", payload={"type": "text_delta", "delta": text}, turn_id="t1")


def _tool_end(call_id: str, name: str) -> RuntimeEvent:
    return RuntimeEvent(
        type="tool",
        payload={"type": "tool_end", "id": call_id, "name": name, "status": "ok"},
        turn_id="t1",
    )


def _seed_session(cwd: Path, session_id: str) -> str:
    log = SessionLog(bot_id="", session_id=session_id, cwd=str(cwd))
    log.append(_text_event("Hello "))
    log.append(_text_event("world."))
    watermark = log.append(_tool_end("call_1", "read_file"))
    log.close()
    return watermark


def _seed_evidence(cwd: Path, session_id: str, *, extra_torn: bool = False) -> int:
    evidence_dir = cwd / ".magi" / "evidence"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    path = evidence_dir / f"{session_id}.jsonl"
    rows = [
        {"sessionId": session_id, "turnId": "t1", "toolName": "read_file", "status": "ok"},
        {"sessionId": session_id, "turnId": "t1", "toolName": "grep", "status": "ok"},
    ]
    with open(path, "w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, sort_keys=True, default=str) + "\n")
        if extra_torn:
            # A blank line + a torn (unparseable) final line. read() skips both;
            # the LOGICAL row count stays 2 so the digest must NOT change.
            fh.write("\n")
            fh.write('{"sessionId": "x", "tor')
    return len(rows)


def test_policy_digest_is_sentinel_and_available_fail_closed(tmp_path: Path) -> None:
    session_id = "sess-policy"
    watermark = _seed_session(tmp_path, session_id)
    line_count = _seed_evidence(tmp_path, session_id)
    env = {**os.environ, "MAGI_EVIDENCE_LEDGER_DIR": str(tmp_path / ".magi" / "evidence")}
    digests = compute_persisted_digests(
        session_id=session_id,
        cwd=str(tmp_path),
        watermark_uuid=watermark,
        evidence_line_count=line_count,
        env=env,
    )
    assert isinstance(digests, CheckpointDigests)
    assert digests.effective_policy_snapshot_digest == POLICY_SNAPSHOT_SENTINEL
    # Fail-closed: at emit time (no engine selection inputs) the snapshot does
    # not genuinely build, so policy_available is False.
    assert digests.policy_available is False


def test_persisted_digests_reproducible_across_fresh_process(tmp_path: Path) -> None:
    session_id = "sess-repro"
    watermark = _seed_session(tmp_path, session_id)
    # Include a trailing-blank-line / torn-final-line case (minor #6): the digest
    # is over LOGICAL re-serialized rows, NOT raw bytes, so no false mismatch.
    line_count = _seed_evidence(tmp_path, session_id, extra_torn=True)
    evidence_dir = str(tmp_path / ".magi" / "evidence")
    env = {**os.environ, "MAGI_EVIDENCE_LEDGER_DIR": evidence_dir}

    in_process = compute_persisted_digests(
        session_id=session_id,
        cwd=str(tmp_path),
        watermark_uuid=watermark,
        evidence_line_count=line_count,
        env=env,
    )

    # Recompute in a SUBPROCESS (fresh interpreter, cleared sys.modules) and
    # assert byte-equality of all four digest fields.
    script = textwrap.dedent(
        f"""
        import json, os
        os.environ["MAGI_EVIDENCE_LEDGER_DIR"] = {evidence_dir!r}
        from magi_agent.runtime.durable_checkpoint_emitter import compute_persisted_digests
        d = compute_persisted_digests(
            session_id={session_id!r},
            cwd={str(tmp_path)!r},
            watermark_uuid={watermark!r},
            evidence_line_count={line_count},
        )
        print(json.dumps({{
            "state": d.state_digest,
            "ledger": d.ledger_head_digest,
            "context": d.context_projection_digest,
            "policy": d.effective_policy_snapshot_digest,
        }}))
        """
    )
    proc = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        cwd=str(Path(__file__).resolve().parents[1]),
        env={**os.environ, "MAGI_EVIDENCE_LEDGER_LIFECYCLE_ENABLED": "1"},
    )
    assert proc.returncode == 0, proc.stderr
    fresh = json.loads(proc.stdout.strip().splitlines()[-1])
    assert fresh["state"] == in_process.state_digest
    assert fresh["ledger"] == in_process.ledger_head_digest
    assert fresh["context"] == in_process.context_projection_digest
    assert fresh["policy"] == in_process.effective_policy_snapshot_digest


def test_persisted_digests_reproducible_cross_process_cwd_unset_env(
    tmp_path: Path,
) -> None:
    """Cross-process boot (PR1d) with MAGI_EVIDENCE_LEDGER_DIR UNSET.

    The boot sweep runs in a DIFFERENT process whose cwd differs from emit and,
    on the shipped full profile, has only MAGI_EVIDENCE_LEDGER_LIFECYCLE_ENABLED
    set (NOT _DIR). The evidence dir must therefore be derived from the ``cwd``
    ARGUMENT, not the process cwd, so ``ledger_head_digest`` is byte-identical.

    RED against the pre-fix code (which resolved the dir via
    ``resolve_evidence_ledger_dir`` -> ``Path.cwd()`` fallback): the subprocess,
    chdir'd to ``other_cwd`` with the env var unset, read an empty dir and
    produced a divergent ledger digest. GREEN after the cwd-pure resolver.
    """
    session_id = "sess-repro-cwd"
    watermark = _seed_session(tmp_path, session_id)
    # Evidence lives at <tmp_path>/.magi/evidence (the writer's unset-env layout).
    line_count = _seed_evidence(tmp_path, session_id, extra_torn=True)

    # Reference baseline: resolve the evidence dir EXPLICITLY (env var SET to the
    # real dir) so the reference ledger digest genuinely covers the 2 seeded
    # rows. The cross-process recompute below leaves the env var UNSET and chdir's
    # away; it must still match this reference purely from the cwd argument.
    evidence_dir = str(tmp_path / ".magi" / "evidence")
    in_process = compute_persisted_digests(
        session_id=session_id,
        cwd=str(tmp_path),
        watermark_uuid=watermark,
        evidence_line_count=line_count,
        env={**os.environ, "MAGI_EVIDENCE_LEDGER_DIR": evidence_dir},
    )
    # Guard: the reference ledger digest must NOT be the empty-ledger digest,
    # otherwise the cross-process assertion below would pass vacuously.
    empty_ledger = compute_persisted_digests(
        session_id="no-such-session",
        cwd=str(tmp_path),
        watermark_uuid=watermark,
        evidence_line_count=0,
        env={**os.environ, "MAGI_EVIDENCE_LEDGER_DIR": evidence_dir},
    )
    assert in_process.ledger_head_digest != empty_ledger.ledger_head_digest

    # A DIFFERENT directory the subprocess will chdir into. The evidence is NOT
    # here; only the cwd argument (tmp_path) locates it.
    other_cwd = tmp_path / "elsewhere"
    other_cwd.mkdir()

    script = textwrap.dedent(
        f"""
        import json, os
        os.environ.pop("MAGI_EVIDENCE_LEDGER_DIR", None)
        os.chdir({str(other_cwd)!r})
        from magi_agent.runtime.durable_checkpoint_emitter import compute_persisted_digests
        d = compute_persisted_digests(
            session_id={session_id!r},
            cwd={str(tmp_path)!r},
            watermark_uuid={watermark!r},
            evidence_line_count={line_count},
        )
        print(json.dumps({{
            "state": d.state_digest,
            "ledger": d.ledger_head_digest,
            "context": d.context_projection_digest,
            "policy": d.effective_policy_snapshot_digest,
        }}))
        """
    )
    repo_root = Path(__file__).resolve().parents[1]
    sub_env = {k: v for k, v in os.environ.items() if k != "MAGI_EVIDENCE_LEDGER_DIR"}
    sub_env["MAGI_EVIDENCE_LEDGER_LIFECYCLE_ENABLED"] = "1"
    # The subprocess chdir's AWAY from the repo before importing magi_agent, so
    # pin PYTHONPATH to the repo root; otherwise the import (not the digest)
    # would fail and mask the real divergence under test.
    existing_pp = sub_env.get("PYTHONPATH", "")
    sub_env["PYTHONPATH"] = (
        f"{repo_root}{os.pathsep}{existing_pp}" if existing_pp else str(repo_root)
    )
    proc = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        cwd=str(repo_root),
        env=sub_env,
    )
    assert proc.returncode == 0, proc.stderr
    fresh = json.loads(proc.stdout.strip().splitlines()[-1])
    # The load-bearing assertion: ledger digest is cwd-argument-pure, not
    # process-cwd-coupled. This is what FAILED before the fix.
    assert fresh["ledger"] == in_process.ledger_head_digest
    assert fresh["state"] == in_process.state_digest
    assert fresh["context"] == in_process.context_projection_digest
    assert fresh["policy"] == in_process.effective_policy_snapshot_digest


def test_torn_evidence_tail_no_false_ledger_mismatch(tmp_path: Path) -> None:
    session_id = "sess-torn-ledger"
    watermark = _seed_session(tmp_path, session_id)
    evidence_dir = str(tmp_path / ".magi" / "evidence")
    env = {**os.environ, "MAGI_EVIDENCE_LEDGER_DIR": evidence_dir}

    clean_count = _seed_evidence(tmp_path, session_id, extra_torn=False)
    clean = compute_persisted_digests(
        session_id=session_id,
        cwd=str(tmp_path),
        watermark_uuid=watermark,
        evidence_line_count=clean_count,
        env=env,
    )
    # Re-write with a trailing blank + torn final line; the LOGICAL rows are
    # identical so the ledger digest must be byte-identical (no false mismatch).
    torn_count = _seed_evidence(tmp_path, session_id, extra_torn=True)
    torn = compute_persisted_digests(
        session_id=session_id,
        cwd=str(tmp_path),
        watermark_uuid=watermark,
        evidence_line_count=torn_count,
        env=env,
    )
    assert torn.ledger_head_digest == clean.ledger_head_digest


def test_build_checkpoint_carries_real_digests_and_resumable(tmp_path: Path) -> None:
    session_id = "sess-build"
    watermark = _seed_session(tmp_path, session_id)
    line_count = _seed_evidence(tmp_path, session_id)
    env = {**os.environ, "MAGI_EVIDENCE_LEDGER_DIR": str(tmp_path / ".magi" / "evidence")}
    digests = compute_persisted_digests(
        session_id=session_id,
        cwd=str(tmp_path),
        watermark_uuid=watermark,
        evidence_line_count=line_count,
        env=env,
    )
    ckpt = build_checkpoint(
        run_id="run-1",
        turn_id="t1",
        step_id="step-3",
        digests=digests,
        resumable=True,
    )
    assert isinstance(ckpt, ExecutionCheckpoint)
    # Real sha256: digests, not placeholders (schema validator passed).
    assert ckpt.state_digest == digests.state_digest
    assert ckpt.ledger_head_digest == digests.ledger_head_digest
    assert ckpt.context_projection_digest == digests.context_projection_digest
    assert ckpt.effective_policy_snapshot_digest == POLICY_SNAPSHOT_SENTINEL
    assert ckpt.resumable is True


def test_verify_resume_request_refuses_on_ledger_mismatch(tmp_path: Path) -> None:
    # The armed-gate proof (section 0.4 / critical 6): verify_resume_request
    # refuses on a real ledger change. This is a UNIT proof the gate is armed on
    # the dimension it governs; v1 foreground continuation does NOT route through
    # it (Correction F).
    session_id = "sess-armed"
    watermark = _seed_session(tmp_path, session_id)
    line_count = _seed_evidence(tmp_path, session_id)
    env = {**os.environ, "MAGI_EVIDENCE_LEDGER_DIR": str(tmp_path / ".magi" / "evidence")}
    digests = compute_persisted_digests(
        session_id=session_id,
        cwd=str(tmp_path),
        watermark_uuid=watermark,
        evidence_line_count=line_count,
        env=env,
    )
    ckpt = build_checkpoint(
        run_id="run-1",
        turn_id="t1",
        step_id="step-3",
        digests=digests,
        resumable=True,
    )

    different = "sha256:" + "0" * 64
    report = verify_resume_request(
        ckpt,
        ledgerHeadDigest=different,
        effectivePolicySnapshotDigest=ckpt.effective_policy_snapshot_digest,
        effectivePolicySnapshotAvailable=True,
        authorityScopeWouldExpand=False,
        pendingApprovalExpired=False,
        requiredEvidenceMissing=False,
    )
    assert report.ok is False
    assert "ledger_head_digest_mismatch" in report.reason_codes

    # Companion: matching digest + available=True + no expansions => ok=True.
    ok_report = verify_resume_request(
        ckpt,
        ledgerHeadDigest=ckpt.ledger_head_digest,
        effectivePolicySnapshotDigest=ckpt.effective_policy_snapshot_digest,
        effectivePolicySnapshotAvailable=True,
        authorityScopeWouldExpand=False,
        pendingApprovalExpired=False,
        requiredEvidenceMissing=False,
    )
    assert ok_report.ok is True

    # Policy fail-closed (Correction F1): available=False => refuse with
    # effective_policy_snapshot_unavailable.
    unavail = verify_resume_request(
        ckpt,
        ledgerHeadDigest=ckpt.ledger_head_digest,
        effectivePolicySnapshotDigest=ckpt.effective_policy_snapshot_digest,
        effectivePolicySnapshotAvailable=False,
        authorityScopeWouldExpand=False,
        pendingApprovalExpired=False,
        requiredEvidenceMissing=False,
    )
    assert unavail.ok is False
    assert "effective_policy_snapshot_unavailable" in unavail.reason_codes


def test_evidence_dir_resolver_identical_emit_and_boot(tmp_path: Path) -> None:
    # minor #7: emit (CLI tap) and recompute (boot) resolve the evidence dir
    # through the SAME resolver. compute_persisted_digests uses
    # resolve_evidence_ledger_dir, never serve_evidence_ledger_dir.
    import inspect

    from magi_agent.runtime import durable_checkpoint_emitter as emitter

    src = inspect.getsource(emitter)
    assert "resolve_evidence_ledger_dir" in src
    assert "serve_evidence_ledger_dir" not in src
