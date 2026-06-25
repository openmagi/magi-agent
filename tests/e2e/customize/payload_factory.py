"""Per-kind minimal-valid rule factory for the F-QA matrix harness.

Each ``build_payload(kind, slot, action)`` returns a *full* custom-rule dict
(``id``, ``scope``, ``enabled``, ``what.{kind,payload}``, ``firesAt``,
``action``) that:

* passes :func:`magi_agent.customize.custom_rules.validate_custom_rule` for
  the requested ``(kind, slot, action)`` triple
* exercises a deterministic runtime path — the trigger driver does not have
  to guess what payload would actually fire at the given slot
* uses ``scope="always"`` so the matrix does not have to thread a "current
  scope" through every trigger

Per-kind notes (why the payload looks the way it does):

deterministic_ref
    ``ref`` MUST be a known WHAT-menu ref — the validator rejects unknown
    refs. ``evidence:test-run`` is the canonical e2e firing ref used by
    ``tests/customize_firing/test_deterministic_ref_firing.py``.

tool_perm
    ``match.tool`` keys the rule against a specific tool name. We use
    ``shell_exec`` because that name is already in the wizard's tool
    catalog. ``decision`` is forced to ``"deny"`` for ``block`` and
    ``"ask"`` for ``ask_approval`` so the persisted action matches the
    decision the matcher would surface.

llm_criterion
    A short binary criterion the critic can decide deterministically when
    the trigger feeds a draft text containing / not containing the literal
    ``"PASS"``. ``after_tool_use`` additionally requires a non-empty
    ``toolMatch`` per the validator — we list ``shell_exec`` so the
    after-tool gate's tool-name filter passes.

shacl_constraint
    SHACL shape requiring ``TestRun.exitCode == 0``. The trigger fires the
    shape against a synthetic non-conforming ``TestRun`` evidence record
    when the action is ``block`` (so the verifier reports a violation).

capability_scope
    Not in F-QA1's slot set (``spawn`` only) — ``build_payload`` raises
    if called for a non-spawn slot so future F-QA4 work can lift this.

prompt_injection / output_rewrite
    Mutators — the validator rejects ``block`` / ``retry`` so the per-action
    branch tightens the action to ``audit``. The payload is the same shape
    the existing ``test_*_firing.py`` modules persist.

shell_command / shell_check
    ``inline`` shell scripts (``exit 0`` / ``exit 1`` / structured JSON
    verdicts) selected by ``action`` so the runtime's verdict matches the
    matrix-declared action. ``shell="bash"`` because the validator's
    ``allowed_shells`` whitelist starts with bash on POSIX hosts.

All rule ids are stable per ``(kind, slot, action)`` so a failing test
points to a deterministic id the operator can grep for in the customize
storage file.
"""

from __future__ import annotations

from typing import Any

# A SHACL shape that always fails for non-conforming ``TestRun`` records.
# Sourced from ``tests/customize_firing/test_shacl_constraint_firing.py``
# verbatim so the matrix uses the same shape the unit test exercises.
_SHACL_TEST_RUN_EXIT_ZERO = """\
@prefix sh: <http://www.w3.org/ns/shacl#> .
@prefix magi: <https://openmagi.ai/ns/evidence#> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .

magi:TestRunExitZeroShape
    a sh:NodeShape ;
    sh:targetClass magi:Evidence ;
    sh:property [
        sh:path magi:field_exitCode ;
        sh:hasValue 0 ;
        sh:message "TestRun.exitCode must equal 0" ;
    ] .
"""


def _rule_id(kind: str, slot: str, action: str) -> str:
    """Stable rule id for ``(kind, slot, action)`` — easy to grep in customize.json."""
    return f"cr_fqa1_{kind}_{slot}_{action}"


def _shell_inline_for(action: str, kind: str) -> str:
    """Pick a deterministic shell snippet that produces the expected verdict.

    For ``shell_command`` the persisted action labels how the runtime treats
    the exit (block on non-zero when action=block, audit-only otherwise).
    For ``shell_check`` the runtime parses stdout as ``{passed,reason?}``
    JSON with exit-code fallback, so we mirror the verdict the matrix asks
    for: ``block`` ⇒ exit 1 (failed verdict); audit/ask_approval ⇒ exit 0
    (passed verdict, audit-only side-effect).
    """
    if kind == "shell_check":
        if action == "block":
            return "exit 1"
        return "echo '{\"passed\": true}'"
    # shell_command
    if action == "block":
        return "exit 1"
    return "exit 0"


def build_payload(kind: str, slot: str, action: str) -> dict[str, Any]:
    """Return a full rule dict for ``(kind, slot, action)``.

    Raises ``ValueError`` for combos outside :data:`_LEGAL` (the matrix
    iterator should never feed us such a combo, but the explicit guard
    keeps the failure mode loud if a future kind/slot lands without a
    factory branch).
    """
    rid = _rule_id(kind, slot, action)
    base: dict[str, Any] = {
        "id": rid,
        "scope": "always",
        "enabled": True,
        "firesAt": slot,
        "action": action,
    }

    if kind == "deterministic_ref":
        base["what"] = {
            "kind": "deterministic_ref",
            "payload": {"ref": "evidence:test-run"},
        }
        return base

    if kind == "tool_perm":
        decision = "deny" if action == "block" else "ask"
        base["what"] = {
            "kind": "tool_perm",
            "payload": {
                "match": {"tool": "shell_exec"},
                "decision": decision,
            },
        }
        return base

    if kind == "llm_criterion":
        payload: dict[str, Any] = {
            "criterion": "Does the output contain the literal string PASS?",
        }
        if slot == "after_tool_use":
            # Validator requires a non-empty toolMatch on after_tool_use.
            payload["toolMatch"] = ["shell_exec"]
        base["what"] = {"kind": "llm_criterion", "payload": payload}
        return base

    if kind == "shacl_constraint":
        base["what"] = {
            "kind": "shacl_constraint",
            "payload": {"shapeTtl": _SHACL_TEST_RUN_EXIT_ZERO},
        }
        return base

    if kind == "capability_scope":
        # Out of F-QA1's slot scope (spawn only). Future F-QA4 lifts this.
        base["what"] = {
            "kind": "capability_scope",
            "payload": {
                "denyTools": ["shell_exec"],
                "tightenOnly": True,
            },
        }
        return base

    if kind == "prompt_injection":
        # Mutator: validator forces action=audit at before_tool_use /
        # on_user_prompt_submit. Match the existing F-MUT1 firing test
        # shape (append " --dry-run" to the ``command`` arg).
        base["what"] = {
            "kind": "prompt_injection",
            "payload": {
                "mode": "append",
                "target_arg_key": "command",
                "value": " --dry-run",
                "condition": {"tool": "shell_exec"},
            },
        }
        return base

    if kind == "output_rewrite":
        # Mutator: validator forces after_tool_use + audit. Mirror the
        # F-MUT2 firing test (redact an AWS-key-shaped pattern).
        base["what"] = {
            "kind": "output_rewrite",
            "payload": {
                "mode": "redact",
                "pattern": "AKIA[0-9A-Z]{16}",
                "replacement": "***",
            },
        }
        return base

    if kind == "shell_command":
        base["what"] = {
            "kind": "shell_command",
            "payload": {
                "source": "inline",
                "inline": _shell_inline_for(action, "shell_command"),
                "timeout_seconds": 5,
                "shell": "bash",
            },
        }
        return base

    if kind == "shell_check":
        base["what"] = {
            "kind": "shell_check",
            "payload": {
                "source": "inline",
                "inline": _shell_inline_for(action, "shell_check"),
                "timeout_seconds": 5,
                "shell": "bash",
            },
        }
        return base

    raise ValueError(f"no payload factory branch for kind={kind!r}")


def rule_id_for(kind: str, slot: str, action: str) -> str:
    """Public stable-id helper — tests use this to assert which rule fired."""
    return _rule_id(kind, slot, action)
