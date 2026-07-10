"""T0 property fuzz — the deterministic containment shell (zero-live-LLM, CI).

Design section 12 U4, engine-side properties. magi-agent's NL compiler has NO
thick deterministic extractor (design section 2); the LLM is load-bearing only
for free-text->field mapping and prose. Everything ELSE in the turn is the
containment shell around an untrusted model, and THAT is what T0 fuzzes:

1. Client-input surface: arbitrary JSON-ish history/draft/answers into the pure
   sanitizers and bookkeeping functions.
2. LLM-output surface (the important one): a ``ScriptedLlm`` returning FUZZED
   envelope text driven through the FULL ``step_compile`` — malformed JSON,
   hostile ``draft_updates`` overwriting operator fields, questions targeting
   non-missing fields, vocabulary leaks. The containment invariants I2/I4/I5/I6
   must hold for EVERY LLM output.
3. Policy-side: ``onUnavailable: "allow"`` is unreachable, no-clobber, and a
   ready plan is always validator-clean.

Every property was first shown to FAIL against a deliberately-weakened copy of
the production logic (the mutation check, documented in the commit message);
here they assert the production behavior.

CI determinism + budget: the Hypothesis ``ci`` profile
(tests/authoring_harness/conftest.py) pins ``derandomize=True``, ``database=None``
and ``max_examples`` (50 default). Override with ``MAGI_AUTHORING_FUZZ_EXAMPLES``.
"""
from __future__ import annotations

import asyncio
from typing import Any

from hypothesis import assume, given
from hypothesis import strategies as st

from benchmarks.authoring.fakes import ScriptedLlm
from magi_agent.customize.custom_rules import (
    _LEGAL,
    ACTIONS,
    FIRES_AT,
    KINDS,
    SCOPES,
    validate_custom_rule,
)
from magi_agent.customize.nl_compiler_interactive import (
    _CANONICAL_FIELD_ORDER,
    _apply_answers_to_draft,
    _auto_fill_singletons,
    _merge_updates,
    _missing_fields_for_draft,
    _sanitize_draft_so_far,
    _to_plain_language,
    step_compile,
)
from magi_agent.customize.nl_policy_interactive import (
    _PARAM_KEYS,
    _sanitize_params,
    step_policy_compile,
)
from magi_agent.customize.policy_plan import validate_policy_plan

_DRAFT_ALLOWED_KEYS = frozenset(
    {"id", "scope", "enabled", "firesAt", "action", "what", "description",
     "projection", "_payload_hint"}
)
_ANSWER_IDS = ("q_what.kind", "q_firesAt", "q_action", "q_scope", "q_what.payload")

# ---------------------------------------------------------------------------
# JSON-ish value strategies (arbitrary, hostile-shaped, but JSON-serializable).
# ---------------------------------------------------------------------------

_json_scalars = st.one_of(
    st.none(),
    st.booleans(),
    st.integers(min_value=-1000, max_value=1000),
    st.floats(allow_nan=False, allow_infinity=False, width=32),
    st.text(max_size=40),
)


def _json_values(max_leaves: int = 12):
    return st.recursive(
        _json_scalars,
        lambda children: st.one_of(
            st.lists(children, max_size=4),
            st.dictionaries(st.text(max_size=12), children, max_size=4),
        ),
        max_leaves=max_leaves,
    )


# A "draft-ish" mapping: sometimes real keys, sometimes junk, sometimes a mix.
_vocab_or_junk = st.one_of(
    st.sampled_from(sorted(KINDS) + sorted(FIRES_AT) + sorted(ACTIONS) + sorted(SCOPES)),
    st.text(max_size=20),
    st.none(),
    st.integers(),
)

_draftish = st.dictionaries(
    keys=st.one_of(
        st.sampled_from(
            ["id", "scope", "enabled", "firesAt", "action", "what",
             "description", "projection", "junk", "__proto__"]
        ),
        st.text(max_size=12),
    ),
    values=st.one_of(_vocab_or_junk, _json_values(max_leaves=6)),
    max_size=8,
)

# An answers map: canonical ids (valid + invalid values) plus junk ids.
_answers_strategy = st.dictionaries(
    keys=st.one_of(st.sampled_from(_ANSWER_IDS), st.text(max_size=20)),
    values=st.text(max_size=30),
    max_size=6,
)


# ---------------------------------------------------------------------------
# Property: _sanitize_draft_so_far never raises + only emits allowlisted shapes.
# ---------------------------------------------------------------------------


@given(draft=st.one_of(_draftish, _json_values(), _json_scalars))
def test_sanitize_draft_never_raises_and_allowlisted(draft: Any) -> None:
    out = _sanitize_draft_so_far(draft)
    assert isinstance(out, dict)
    # Only the allowlisted top-level keys survive (never junk / __proto__).
    allowed = {"id", "scope", "enabled", "firesAt", "action", "what",
               "description", "projection"}
    assert set(out.keys()) <= allowed
    # A DICT ``what`` is coerced to at most {kind: str, payload: dict}. A
    # non-dict ``what`` is passed through untouched (production only coerces the
    # dict shape); the missing-field bookkeeping treats a non-dict ``what`` as
    # "kind unknown", so the containment guarantee is upheld downstream, not by
    # sanitize. We therefore only assert the coerced shape for the dict case.
    what = out.get("what")
    if isinstance(what, dict):
        assert set(what.keys()) <= {"kind", "payload"}
        if "kind" in what:
            assert isinstance(what["kind"], str)
        if "payload" in what:
            assert isinstance(what["payload"], dict)


@given(params=st.one_of(_draftish, _json_values(), _json_scalars))
def test_sanitize_params_never_raises_and_allowlisted(params: Any) -> None:
    out = _sanitize_params(params)
    assert isinstance(out, dict)
    assert set(out.keys()) <= set(_PARAM_KEYS)
    # onUnavailable can never be "allow" (unrepresentable at every surface).
    assert out.get("onUnavailable") != "allow"
    # allowlistDomains is always a list of stripped strings when present.
    if "allowlistDomains" in out:
        assert isinstance(out["allowlistDomains"], list)
        assert all(isinstance(d, str) and d == d.strip() and d for d in out["allowlistDomains"])


# ---------------------------------------------------------------------------
# Property: _apply_answers_to_draft never writes an out-of-vocabulary value.
# ---------------------------------------------------------------------------


@given(draft=_draftish, answers=_answers_strategy)
def test_apply_answers_never_writes_out_of_vocab(draft: Any, answers: dict) -> None:
    sanitized = _sanitize_draft_so_far(draft)
    out = _apply_answers_to_draft(sanitized, answers)
    assert isinstance(out, dict)
    what = out.get("what") if isinstance(out.get("what"), dict) else {}
    # A vocabulary field is EITHER absent, EITHER carried from the sanitized
    # seed, OR one of the answered valid values — never an out-of-vocab answer.
    kind = what.get("kind")
    seed_what = sanitized.get("what") if isinstance(sanitized.get("what"), dict) else {}
    seed_kind = seed_what.get("kind")
    assert kind is None or not isinstance(kind, str) or kind in KINDS or kind == seed_kind
    for field_name, vocab, seed_key in (
        ("firesAt", FIRES_AT, "firesAt"),
        ("action", ACTIONS, "action"),
        ("scope", SCOPES, "scope"),
    ):
        val = out.get(field_name)
        if isinstance(val, str):
            # It landed as a string: either it's a legal vocab value, or it was
            # already present in the sanitized seed (the function copies seed
            # values through untouched).
            assert val in vocab or val == sanitized.get(seed_key), (
                f"{field_name}={val!r} is neither legal vocab nor a carried seed value"
            )


# ---------------------------------------------------------------------------
# Property: _merge_updates never overwrites a NON-EMPTY operator field.
# ---------------------------------------------------------------------------

_nonempty_scalar = st.one_of(
    st.sampled_from(sorted(FIRES_AT)),
    st.sampled_from(sorted(ACTIONS)),
    st.sampled_from(sorted(SCOPES)),
    st.text(min_size=1, max_size=12),
)

# A base draft where some scalar fields are already set (operator-supplied).
_base_draft = st.fixed_dictionaries(
    {},
    optional={
        "firesAt": _nonempty_scalar,
        "action": _nonempty_scalar,
        "scope": _nonempty_scalar,
        "id": st.text(min_size=1, max_size=10),
        "description": st.text(min_size=1, max_size=10),
    },
)

# LLM patch trying to overwrite them.
_patch = st.fixed_dictionaries(
    {},
    optional={
        "firesAt": _nonempty_scalar,
        "action": _nonempty_scalar,
        "scope": _nonempty_scalar,
        "id": st.text(min_size=1, max_size=10),
        "description": st.text(min_size=1, max_size=10),
        "what": st.dictionaries(
            st.sampled_from(["kind", "payload"]),
            st.one_of(
                st.text(max_size=8),
                st.dictionaries(st.text(max_size=4), st.text(max_size=4), max_size=2),
            ),
            max_size=2,
        ),
    },
)


@given(base=_base_draft, patch=_patch)
def test_merge_updates_never_clobbers_nonempty_operator_field(base: dict, patch: dict) -> None:
    out = _merge_updates(dict(base), patch)
    for key in ("firesAt", "action", "scope", "id", "description"):
        base_val = base.get(key)
        # A non-empty operator value must survive the merge byte-for-byte.
        if base_val not in (None, "", []):
            assert out.get(key) == base_val, (
                f"merge clobbered operator field {key}={base_val!r} with {out.get(key)!r}"
            )


# ---------------------------------------------------------------------------
# Property: _auto_fill_singletons output is always _LEGAL-consistent.
# (Any value it FILLS must be legal for the known kind; it never invents an
# illegal firesAt/action.)
# ---------------------------------------------------------------------------


@given(
    kind=st.sampled_from(sorted(KINDS)),
    seed_fires=st.one_of(st.none(), st.sampled_from(sorted(FIRES_AT)), st.text(max_size=8)),
    seed_action=st.one_of(st.none(), st.sampled_from(sorted(ACTIONS)), st.text(max_size=8)),
)
def test_auto_fill_singletons_is_legal_consistent(kind: str, seed_fires, seed_action) -> None:
    draft: dict = {"what": {"kind": kind}}
    if seed_fires is not None:
        draft["firesAt"] = seed_fires
    if seed_action is not None:
        draft["action"] = seed_action
    out = _auto_fill_singletons(draft)
    legal = _LEGAL.get(kind, {})
    fires = out.get("firesAt")
    # If auto-fill CHANGED firesAt from the seed, the filled value must be legal.
    if fires != seed_fires and isinstance(fires, str):
        assert fires in legal, f"auto-filled firesAt={fires!r} not legal for {kind}"
    action = out.get("action")
    changed_action = action != seed_action and isinstance(action, str)
    if changed_action and isinstance(fires, str) and fires in legal:
        assert action in legal[fires], (
            f"auto-filled action={action!r} not legal for {kind}/{fires}"
        )


# ---------------------------------------------------------------------------
# Property: _missing_fields_for_draft is canonical-ordered, and empty implies
# every validator-visible canonical field is present.
# ---------------------------------------------------------------------------


# A draft strategy that reaches DEEP into the canonical field order: a valid
# kind (so kind is NOT the sole missing field) plus optional later fields, which
# forces MULTIPLE fields to be missing so their ORDER is actually load-bearing
# (a single-element list is trivially ordered and cannot catch a reordering bug).
_multi_missing_draft = st.fixed_dictionaries(
    {"what": st.fixed_dictionaries({"kind": st.sampled_from(sorted(KINDS))})},
    optional={
        "firesAt": st.sampled_from(sorted(FIRES_AT)),
        "action": st.sampled_from(sorted(ACTIONS)),
        "scope": st.sampled_from(sorted(SCOPES)),
    },
)


@given(draft=st.one_of(_draftish, _json_values(), _multi_missing_draft))
def test_missing_fields_canonical_ordered(draft: Any) -> None:
    sanitized = _sanitize_draft_so_far(draft)
    missing = _missing_fields_for_draft(sanitized)
    # No duplicates.
    assert len(missing) == len(set(missing))
    # STRONG order property: the returned list is EXACTLY the canonical-order
    # filter of its own contents — any reordering (e.g. a reversed emitter) is
    # caught the moment two or more fields are missing.
    missing_set = set(missing)
    expected_order = [f for f in _CANONICAL_FIELD_ORDER if f in missing_set]
    assert missing == expected_order, (
        f"missing not canonical-ordered: got {missing}, expected {expected_order}"
    )
    # Empty-missing implies the canonical fields are all present & legal.
    if not missing:
        what = sanitized.get("what") or {}
        assert what.get("kind") in KINDS
        assert sanitized.get("firesAt") in FIRES_AT
        assert sanitized.get("action") in ACTIONS
        assert sanitized.get("scope") in SCOPES
        payload = what.get("payload")
        assert isinstance(payload, dict) and payload


# ---------------------------------------------------------------------------
# Property: fuzzed LLM envelopes through the FULL step_compile can never flip
# the containment invariants I2/I4/I5/I6. This is the important one.
# ---------------------------------------------------------------------------

# Hostile envelope fragments the fuzzer stitches into a "compiler" reply.
_envelope_strategy = st.one_of(
    # Well-formed but hostile: overwrite operator fields, leak vocab, bad ids.
    st.builds(
        lambda msg, upd: __import__("json").dumps(
            {"assistant_message": msg, "draft_updates": upd, "questions": []}
        ),
        msg=st.one_of(
            st.text(max_size=40),
            st.sampled_from([
                "Use a regex matcher at the firesAt lifecycle",
                "set the kind and matcher via shacl",
                "an EvidenceReq llm_critic gate",
            ]),
        ),
        upd=st.dictionaries(
            keys=st.sampled_from(
                ["firesAt", "action", "scope", "id", "what", "description", "enabled", "junk"]
            ),
            values=st.one_of(
                st.sampled_from(sorted(FIRES_AT) + sorted(ACTIONS) + sorted(SCOPES)),
                st.text(max_size=20),
                st.dictionaries(
                    st.sampled_from(["kind", "payload", "junk"]),
                    st.one_of(
                        st.text(max_size=10),
                        st.dictionaries(st.text(max_size=4), st.text(max_size=4), max_size=2),
                    ),
                    max_size=3,
                ),
            ),
            max_size=5,
        ),
    ),
    # A well-formed envelope that ALWAYS tries to overwrite scope with a DIFFERENT
    # legal value than the one the operator answered. scope has no singleton
    # auto-fill, so this is the sharpest I4 probe: a merge that clobbered the
    # operator would flip scope to the LLM's value. Paired below with an answer
    # set that always supplies q_scope.
    st.builds(
        lambda s: __import__("json").dumps(
            {"assistant_message": "ok", "draft_updates": {"scope": s}, "questions": []}
        ),
        s=st.sampled_from(sorted(SCOPES)),
    ),
    # Malformed / fenced / truncated JSON the parser must survive.
    st.sampled_from([
        "not json at all",
        "```json\n{\"assistant_message\": \"hi\", \"draft_updates\": {}}\n```",
        "{\"assistant_message\": \"trailing\", \"draft_updates\": {} ",  # unbalanced
        "{}",
        "{\"draft_updates\": {\"action\": \"audit\"}}",
        'prefix junk {"assistant_message": "x", "draft_updates": {"scope": "weird"}} tail',
        "",
    ]),
)

# Always supply q_scope (the no-singleton-escape field) so the I4 collision with
# the scope-overwriting envelope above is exercised on every example; the other
# canonical answers stay optional to keep breadth.
_answer_valid = st.fixed_dictionaries(
    {"q_scope": st.sampled_from(sorted(SCOPES))},
    optional={
        "q_what.kind": st.sampled_from(sorted(KINDS)),
        "q_firesAt": st.sampled_from(sorted(FIRES_AT)),
        "q_action": st.sampled_from(sorted(ACTIONS)),
    },
)


# A seed draft the client would echo back: it supplies the ``what.payload`` that
# the (dead-at-HEAD) LLM path would otherwise compile, so the DETERMINISTIC
# answer + auto-fill path can actually REACH ready_to_save. Without a completable
# seed, ``missing`` is always non-empty (kind unknown / payload absent) and the
# I2/I6 assertions would be vacuous. HEAD-DRIFT NOTE: route A's live LLM path
# crashes ``str.format`` before the model is called (Wave A drift note; PR #1459
# is the fix, not merged here), so a fuzzed envelope's ``draft_updates`` are DEAD
# CODE through ``step_compile`` at this ref — the operator answers + this seed are
# what drive convergence. The property still feeds the fuzzed envelope so the
# PARSER, the ``assistant_message`` scrub (I5) and the fallback narration are
# exercised for arbitrary model output; it just cannot (and does not claim to)
# route hostile ``draft_updates`` into the merge at this HEAD.
_completable_seed = st.one_of(
    st.just({}),  # non-completable: exercises the still-missing path
    st.just({"what": {"payload": {"match": {"tool": "Bash"}, "decision": "deny"}}}),
)

# For the completable seed, tool_perm's full legal answer set makes ready
# reachable: kind=tool_perm, firesAt=before_tool_use, action in {block,
# ask_approval}, scope any. We draw answers that MAY or MAY NOT complete it.
_answer_toward_toolperm = st.fixed_dictionaries(
    {"q_scope": st.sampled_from(sorted(SCOPES))},
    optional={
        "q_what.kind": st.sampled_from(["tool_perm"] + sorted(KINDS)),
        "q_firesAt": st.sampled_from(["before_tool_use"] + sorted(FIRES_AT)),
        "q_action": st.sampled_from(["block", "ask_approval"] + sorted(ACTIONS)),
    },
)


@given(
    envelope=_envelope_strategy,
    say=st.text(max_size=40),
    answers=st.one_of(_answer_valid, _answer_toward_toolperm),
    seed=_completable_seed,
)
def test_fuzzed_envelope_cannot_flip_containment_invariants(
    envelope: str, say: str, answers: dict, seed: dict
) -> None:
    scripted = ScriptedLlm([envelope])
    history = [{"role": "user", "content": say}] if say else []
    result = asyncio.run(
        step_compile(
            history=history,
            draft_so_far=seed or None,
            answers=answers,
            model_factory=scripted.as_factory(),
        )
    )
    draft = result.get("draft") or {}
    missing = result.get("missing_fields") or []
    ready = bool(result.get("ready_to_save"))

    # --- I6 working-state hygiene: draft only ever holds allowlisted keys. ---
    assert set(draft.keys()) <= _DRAFT_ALLOWED_KEYS, (
        f"draft leaked non-allowlisted keys: {sorted(set(draft.keys()) - _DRAFT_ALLOWED_KEYS)}"
    )

    # --- I2 ready-truth: ready IFF missing empty AND validator clean. ---
    validator_clean = not validate_custom_rule(draft)
    assert ready == ((not missing) and validator_clean), (
        f"ready={ready} but missing_empty={not missing}, validator_clean={validator_clean}"
    )

    # --- I5 vocabulary containment: every rendered string is a scrubber fixed point. ---
    rendered = [result.get("assistant_message") or ""]
    rendered += [q.get("prompt") or "" for q in (result.get("questions") or [])]
    rendered += [str(s) for s in (result.get("schema_issues") or [])]
    for s in rendered:
        assert _to_plain_language(s) == s, f"vocabulary leak reached the wire: {s!r}"

    # --- I4 operator supremacy: an operator answer is NEVER overwritten by the
    # untrusted LLM. The ONLY thing that may replace it is the DETERMINISTIC
    # singleton auto-fill (_auto_fill_singletons), which collapses a field to
    # the sole legal value for the chosen kind — e.g. an operator who answered
    # ``action=ask_approval`` for capability_scope (whose only legal action is
    # ``block``) is corrected to ``block`` by the legal matrix, not by the model.
    # So: the landed value is EITHER the operator's answer, OR a legal
    # singleton for the known kind — but a hostile ``draft_updates`` can flip it
    # to NEITHER.
    what = draft.get("what") if isinstance(draft.get("what"), dict) else {}
    kind = what.get("kind") if isinstance(what, dict) else None
    legal = _LEGAL.get(kind, {}) if isinstance(kind, str) else {}
    for ans_id, (path_key, vocab) in {
        "q_what.kind": (("what", "kind"), KINDS),
        "q_firesAt": (("firesAt",), FIRES_AT),
        "q_action": (("action",), ACTIONS),
        "q_scope": (("scope",), SCOPES),
    }.items():
        if ans_id not in answers:
            continue
        answered = answers[ans_id]
        if answered not in vocab:
            continue
        if path_key == ("what", "kind"):
            landed = what.get("kind")
        else:
            landed = draft.get(path_key[0])
        if landed == answered:
            continue
        # Not the operator's value: the only legitimate override is a
        # deterministic singleton auto-fill to a LEGAL value for this kind.
        if ans_id == "q_firesAt":
            assert isinstance(landed, str) and landed in legal and len(legal) == 1, (
                f"firesAt={landed!r} replaced the operator answer but is not a legal singleton"
            )
        elif ans_id == "q_action":
            fires = draft.get("firesAt")
            legal_actions = legal.get(fires, set()) if isinstance(fires, str) else set()
            singleton = (
                isinstance(landed, str)
                and landed in legal_actions
                and len(legal_actions) == 1
            )
            assert singleton, (
                f"action={landed!r} replaced the operator answer but is not a legal singleton"
            )
        else:
            # scope / kind have no singleton auto-fill; the operator answer must
            # survive verbatim.
            raise AssertionError(
                f"operator answer {ans_id}={answered!r} did not survive (got {landed!r})"
            )


# ---------------------------------------------------------------------------
# Policy-side: a ready plan is ALWAYS validator-clean, and onUnavailable is
# never "allow" (both through the full step_policy_compile with a fuzzed LLM).
# ---------------------------------------------------------------------------

_policy_envelope = st.one_of(
    st.builds(
        lambda upd: __import__("json").dumps(
            {"assistant_message": "ok", "param_updates": upd, "questions": []}
        ),
        upd=st.dictionaries(
            keys=st.sampled_from(list(_PARAM_KEYS) + ["junk"]),
            values=st.one_of(
                st.text(max_size=20),
                st.sampled_from(["deny", "ask", "allow"]),  # smuggle "allow"
                st.lists(st.text(max_size=12), max_size=3),
            ),
            max_size=5,
        ),
    ),
    st.sampled_from(["not json", "{}", "```\n{}\n```", ""]),
)

_policy_answers = st.fixed_dictionaries(
    {},
    optional={
        "gatedTool": st.text(min_size=1, max_size=16),
        "evidenceLabel": st.text(min_size=1, max_size=16),
        "allowlistDomains": st.text(min_size=1, max_size=24),
        "onUnavailable": st.sampled_from(["deny", "ask", "allow"]),
    },
)


@given(envelope=_policy_envelope, say=st.text(max_size=30), answers=_policy_answers)
def test_policy_ready_plan_always_clean_and_no_allow(
    envelope: str, say: str, answers: dict
) -> None:
    scripted = ScriptedLlm([envelope])
    history = [{"role": "user", "content": say}] if say else []
    result = asyncio.run(
        step_policy_compile(
            history=history,
            params_so_far=None,
            answers=answers,
            model_factory=scripted.as_factory(),
        )
    )
    params = result.get("params") or {}
    plan = result.get("plan")
    ready = bool(result.get("ready_to_save"))

    # onUnavailable can never be "allow", from ANY input surface.
    assert params.get("onUnavailable") != "allow"
    # params never carry a non-allowlisted key.
    assert set(params.keys()) <= set(_PARAM_KEYS)

    # A ready result MUST carry a plan and that plan MUST validate clean.
    if ready:
        assert plan is not None
        assert validate_policy_plan(plan) == [], "ready plan failed validate_policy_plan"
    # And plan-present IFF ready (I7 consistency at the engine boundary).
    assert (plan is not None) == ready


# ---------------------------------------------------------------------------
# Policy-side: no-clobber. An operator-answered scalar param is never overwritten
# by the LLM's param_updates.
# ---------------------------------------------------------------------------


@given(
    gated=st.text(min_size=1, max_size=16),
    label=st.text(min_size=1, max_size=16),
    patch_gated=st.text(min_size=1, max_size=16),
    patch_label=st.text(min_size=1, max_size=16),
)
def test_policy_merge_no_clobber(
    gated: str, label: str, patch_gated: str, patch_label: str
) -> None:
    from magi_agent.customize.nl_policy_interactive import _merge_updates as pmerge

    base = _sanitize_params({"gatedTool": gated, "evidenceLabel": label})
    assume("gatedTool" in base and "evidenceLabel" in base)
    out = pmerge(base, {"gatedTool": patch_gated, "evidenceLabel": patch_label})
    assert out["gatedTool"] == base["gatedTool"], "LLM clobbered operator gatedTool"
    assert out["evidenceLabel"] == base["evidenceLabel"], "LLM clobbered operator evidenceLabel"
