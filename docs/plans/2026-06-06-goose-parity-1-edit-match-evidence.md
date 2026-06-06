# PR1 — Make fuzzy edit matching auditable (EditMatch evidence)

**Lesson source:** goose (`crates/goose/src/agents/platform_extensions/developer/edit.rs:158`
`string_replace`) does exact-match-only editing — it *fails honestly* (0 matches → loud
error, never guesses). magi's `magi_agent/coding/edit_matching.py` `replace()` runs a
9-stage fuzzy cascade and **returns only the result string**, discarding which tier fired
and how confident it was. A low-confidence `_context_aware` (>=50% middle-line) or
`_block_anchor` (Levenshtein) guess can silently patch the wrong location and leaves **no
evidence trace** — directly at odds with magi's determinism/anti-forgery philosophy.

**Goal:** make every fuzzy edit auditable. Record the matcher tier + confidence + matched
span, emit it as an `EditMatch` evidence receipt, and gate the two low-confidence tiers
behind post-edit verification — without breaking exact-match ergonomics.

## Current state (verified, on `origin/main` @ debd41d)

- `magi_agent/coding/edit_matching.py`
  - `_MATCHERS` = 9 generators (`_simple, _line_trimmed, _block_anchor,
    _whitespace_normalized, _indentation_flexible, _escape_normalized, _trimmed_boundary,
    _context_aware, _multi_occurrence`).
  - `_block_anchor` accepts at `best_score >= 0.3`; `_context_aware` accepts at
    `matches/total >= 0.5`. These are the two LOW-confidence tiers.
  - `replace(content, old, new, replace_all=False) -> str` iterates `_MATCHERS`, returns
    the first unique candidate replaced; raises `NoMatchError` / `MultipleMatchesError`.
- **Exactly two production callers** (both alias the function as `_fuzzy_replace`, lazy
  import behind a flag):
  - `magi_agent/gates/gate5b_full_toolhost.py` (`FileEdit` branch) — writes the file,
    returns `edit_result = {pathDigest, replacements, contentDigest}`.
  - `magi_agent/recipes/coding_mutation.py` — produces a receipt/decision, never writes.
- Evidence plumbing pattern to mirror: `magi_agent/evidence/code_diagnostics_receipts.py`
  (`CodeDiagnosticsRecord` builtin type) and `magi_agent/evidence/coding_tool_receipts.py`.
  Enforcement vocabulary: `magi_agent/evidence/types.py`
  `EvidenceEnforcement = Literal["off","audit","block_final_answer"]`,
  `EvidenceOnMissing`, `BUILTIN_EVIDENCE_TYPES`.
- Edit-confidence verification analogue: `magi_agent/evidence/coding_verification.py`
  (existing GitDiff+TestRun contracts at audit / block_final_answer levels).

## Design

### A. `edit_matching.py` — structured result
Add a frozen dataclass and a tier table; change `replace()` to return it. Preserve
backward compatibility so existing string callers keep working.

```python
@dataclass(frozen=True)
class EditMatchResult:
    result: str                      # new file content (was the bare return)
    tier: str                        # e.g. "context_aware"
    tier_index: int                  # 0..8 position in _MATCHERS
    confidence: float                # 0.0..1.0
    matched_span: tuple[int, int]    # (start, end) byte offsets into content_body
    ambiguous: bool                  # a unique match was forced despite other candidates
    def __str__(self) -> str:        # str(res) == res.result  (back-compat)
        return self.result
```

Tier → nominal confidence table (index-aligned to `_MATCHERS`):

| idx | matcher | tier | confidence | class |
|----|---------|------|-----------|-------|
| 0 | `_simple` | `simple` | 1.00 | high |
| 1 | `_line_trimmed` | `line_trimmed` | 0.95 | high |
| 2 | `_block_anchor` | `block_anchor` | `0.3 + 0.7*score` | **LOW** |
| 3 | `_whitespace_normalized` | `whitespace_normalized` | 0.90 | high |
| 4 | `_indentation_flexible` | `indentation_flexible` | 0.85 | high |
| 5 | `_escape_normalized` | `escape_normalized` | 0.80 | high |
| 6 | `_trimmed_boundary` | `trimmed_boundary` | 0.85 | high |
| 7 | `_context_aware` | `context_aware` | actual `matches/total` | **LOW** |
| 8 | `_multi_occurrence` | `multi_occurrence` | 0.70 | mid |

- For `_block_anchor` / `_context_aware`, the matcher must surface the actual score so
  `replace()` can compute real confidence. Minimal-touch approach: have those two
  generators `yield (candidate, score)` and have the loop detect 2-tuples; all other
  matchers keep yielding bare strings (default confidence from the table). Keep the change
  internal — public matcher behavior otherwise unchanged.
- Public API: keep `replace(...) -> EditMatchResult`. Add `replace_text(...) -> str`
  returning `.result` for any caller/test that wants the plain string. Because
  `EditMatchResult.__str__` returns `result`, callers doing `target.write_text(str(res))`
  keep working; migrate the two known callers to use the structured fields.

### B. New evidence receipt — `magi_agent/evidence/edit_match_receipts.py`
Model exactly on `code_diagnostics_receipts.py`.

```python
class EditMatchReceiptRecord(BaseModel):
    type: Literal["EditMatch"] = "EditMatch"
    tier: str
    confidence: float = Field(ge=0.0, le=1.0)
    ambiguous: bool = False
    file_digest: str = Field(alias="fileDigest")   # sha256 of file content
    span_digest: str = Field(alias="spanDigest")   # sha256 of matched span text (never raw)
    def public_projection(self) -> dict[str, object]: ...

class EditMatchReceiptBoundary:
    def build_record(self, *, match: EditMatchResult, file_content: str) -> EditMatchReceiptRecord: ...
```

- Register `"EditMatch"` in `BUILTIN_EVIDENCE_TYPES` (`evidence/types.py`).
- Never store raw matched text — only digests (consistent with existing receipts).

### C. Thread receipts to callers
- `gate5b_full_toolhost.py` FileEdit branch: capture `match = _fuzzy_replace(...)`, write
  `str(match)`, build an `EditMatchReceiptRecord` next to the existing diagnostics/coding
  receipts and pass it through the same `finish_call(...)` path (add an
  `edit_match_receipt` field on the outcome dataclass). Add `tier`/`confidence` to the
  returned `edit_result`.
- `coding_mutation.py`: thread `match.tier`/`match.confidence`/`match.ambiguous` into the
  decision/diff-summary (whitelist the new keys in the safe-summary projector).

### D. Gating tied to enforcement levels (DEFAULT-OFF)
Add `build_edit_confidence_contract(...)` in `coding_verification.py`:
- High-confidence tiers (conf ≥ 0.80): emit `EditMatch` receipt, enforcement `audit`
  (log only, never block).
- LOW tiers (`_block_anchor`, `_context_aware`, or `ambiguous=True`): enforcement
  `block_final_answer` — require a corroborating fresh `GitDiff` + `TestRun(exit_code=0)`
  after the edit before the final answer is allowed (reuse existing hard-gate
  requirements). On `off`, behavior is identical to today (receipts still emitted under
  `audit` if that is separately enabled).

**Flag:** gate the *blocking* behavior behind a new env flag, default OFF, e.g.
`MAGI_EDIT_MATCH_EVIDENCE_ENFORCEMENT` ∈ `{off,audit,block_final_answer}` default `off`
(reuse the `EvidenceEnforcement` literal). When `off`: receipts are still built and
attached (cheap, auditable) but never block. Receipt *emission* itself is always-on and
side-effect free.

## Tests (TDD — write first)
- `tests/coding/test_edit_matching.py` (extend): each of the 9 matchers returns the
  correct `tier`/`tier_index`; `_block_anchor`/`_context_aware` confidence reflects actual
  score; `ambiguous` set when forced; `str(result) == result.result`; `replace_text`
  returns the string.
- `tests/evidence/test_edit_match_receipts.py` (new): receipt builds, digests are sha256,
  `public_projection` carries no raw text, `EditMatch` is a registered builtin type.
- `tests/gates/...` (extend existing gate5b test): FileEdit attaches an `EditMatch`
  receipt with the right tier; low-tier edit under `block_final_answer` triggers the
  verification requirement; under `off` it does not block.

## Acceptance criteria
1. `replace()` returns `EditMatchResult`; all existing tests pass (via `__str__`/`replace_text`).
2. Both production callers thread tier/confidence; gate5b emits an `EditMatch` receipt.
3. New flag default `off` → zero behavior change vs today except receipts are now present.
4. The enforcement **contract** (`build_edit_confidence_contract`) and the
   `MAGI_EDIT_MATCH_EVIDENCE_ENFORCEMENT` flag (default `off`) ship and are unit-tested:
   LOW tiers → `block_final_answer`, high tiers → `audit`, `off` → never blocks.
   **Follow-up (not in this PR):** the contract is not yet *consumed* by gate5b's
   final-answer projection — i.e. no production path currently enforces the block. This PR
   delivers auditable receipts + the contract builder as a wired-but-not-yet-consumed seam;
   hooking it into the final-answer gate is a separate PR. Because the flag defaults `off`,
   no behavior ships either way.
5. `uv run --extra dev pytest -q` green for touched test modules.

## Out of scope
- Changing matcher acceptance thresholds. Auto-correcting wrong matches. Wiring the
  enforcement flag default to anything other than `off`.
- **Consuming the enforcement contract in gate5b's final-answer projection** (separate
  follow-up PR). This PR ships the receipts + contract builder + flag only.
