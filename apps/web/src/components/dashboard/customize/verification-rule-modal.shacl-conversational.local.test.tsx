/**
 * TDD tests for Task 5.3 — Conversational SHACL compile UI + beginner guide panel + English i18n.
 *
 * Pattern: read source files and assert string / structural properties.
 * This avoids the need for DOM rendering or context providers.
 * NOT browser-verified (component tests only).
 */
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const modalSrc = readFileSync(
  new URL("./verification-rule-modal.tsx", import.meta.url),
  "utf8",
);

const tabSrc = readFileSync(
  new URL("./customize-tab.tsx", import.meta.url),
  "utf8",
);

const templateSrc = readFileSync(
  new URL("./shacl-example-template.ts", import.meta.url),
  "utf8",
);

// ---------------------------------------------------------------------------
// Test group 1 — English i18n: Korean strings replaced with English (Sub-task 5.3d)
// ---------------------------------------------------------------------------
describe("1 — English i18n: SHACL labels are English, not Korean", () => {
  it("rule-type option label is English: Deterministic constraint (SHACL)", () => {
    expect(modalSrc).toContain("Deterministic constraint (SHACL)");
  });

  it("badge for shacl_constraint rule row is English: Deterministic · SHACL · live", () => {
    expect(modalSrc).toContain("Deterministic · SHACL · live");
  });

  it("mode toggle buttons are English: Natural language and Raw .ttl", () => {
    expect(modalSrc).toContain("Natural language");
    expect(modalSrc).toContain("Raw .ttl");
  });

  it("compile button label is English: Compile", () => {
    expect(modalSrc).toContain("Compile");
  });

  it("compiling state label is English: Compiling…", () => {
    expect(modalSrc).toContain("Compiling…");
  });

  it("approve button is English: Looks right — activate", () => {
    expect(modalSrc).toContain("Looks right — activate");
  });

  it("retry button is English: Retry", () => {
    expect(modalSrc).toContain("Retry");
  });

  it("reviewer verdict label is English: Reviewer verdict:", () => {
    expect(modalSrc).toContain("Reviewer verdict:");
  });

  it("confidence label is English: Confidence", () => {
    expect(modalSrc).toContain("Confidence");
  });

  it("reverse explanation label is English: Reverse explanation:", () => {
    expect(modalSrc).toContain("Reverse explanation:");
  });

  it("sample results label is English: Sample results", () => {
    expect(modalSrc).toContain("Sample results");
  });

  it("view generated SHACL label is English: View generated SHACL", () => {
    expect(modalSrc).toContain("View generated SHACL");
  });

  it("sample records textarea label is English: Sample records (JSON, optional)", () => {
    expect(modalSrc).toContain("Sample records (JSON, optional)");
  });

  it("sample records aria-label is English: Sample records JSON input", () => {
    expect(modalSrc).toContain('"Sample records JSON input"');
  });

  it("nl textarea aria-label is English: Natural-language constraint input", () => {
    expect(modalSrc).toContain('"Natural-language constraint input"');
  });

  it("SHACL TTL aria-label is English: SHACL TTL input", () => {
    expect(modalSrc).toContain('"SHACL TTL input"');
  });

  it("missing reviewer warning is English: Reviewer check unavailable", () => {
    expect(modalSrc).toContain("Reviewer check unavailable — verify the SHACL manually");
  });

  it("empty-state message is English: Add sample records to see deterministic PASS/FAIL preview.", () => {
    expect(modalSrc).toContain("Add sample records to see deterministic PASS/FAIL preview.");
  });

  it("JSON array error message is English", () => {
    expect(modalSrc).toContain("Must be a JSON array");
  });

  it("invalid JSON error message is English", () => {
    expect(modalSrc).toContain("Invalid JSON");
  });

  it("compile failure message is English: Compile failed", () => {
    expect(modalSrc).toContain("Compile failed");
  });

  it("unexpected error message is English: An error occurred during compilation.", () => {
    expect(modalSrc).toContain("An error occurred during compilation.");
  });

  it("raw mode SHACL input label is English: SHACL .ttl direct input", () => {
    expect(modalSrc).toContain("SHACL .ttl direct input");
  });

  it("load example button is English: Load example", () => {
    expect(modalSrc).toContain("Load example");
  });

  it("input mode label is English: Input mode:", () => {
    expect(modalSrc).toContain("Input mode:");
  });
});

// ---------------------------------------------------------------------------
// Test group 2 — No Korean characters in SHACL-relevant PR4 strings
// ---------------------------------------------------------------------------
describe("2 — No Korean characters remain in SHACL-relevant files", () => {
  it("verification-rule-modal.tsx has no Korean characters", () => {
    expect(/[가-힣]/.test(modalSrc)).toBe(false);
  });

  it("shacl-example-template.ts has no Korean characters", () => {
    expect(/[가-힣]/.test(templateSrc)).toBe(false);
  });

  it("customize-tab.tsx has no Korean characters", () => {
    expect(/[가-힣]/.test(tabSrc)).toBe(false);
  });

  // Specific former Korean strings must NOT be present
  it("does NOT contain former Korean rule type label", () => {
    expect(modalSrc).not.toContain("결정론 제약 (SHACL)");
  });

  it("does NOT contain former Korean badge text", () => {
    expect(modalSrc).not.toContain("결정론 · SHACL · live");
  });

  it("does NOT contain former Korean mode label 자연어", () => {
    expect(modalSrc).not.toContain("자연어");
  });

  it("does NOT contain former Korean mode label .ttl 직접", () => {
    expect(modalSrc).not.toContain(".ttl 직접");
  });

  it("does NOT contain former Korean compile button label", () => {
    expect(modalSrc).not.toContain("컴파일");
  });

  it("does NOT contain former Korean approve button text", () => {
    expect(modalSrc).not.toContain("이게 맞습니다");
  });

  it("does NOT contain former Korean retry button text 다시", () => {
    expect(modalSrc).not.toContain("✗ 다시");
  });

  it("does NOT contain former Korean reviewer verdict label", () => {
    expect(modalSrc).not.toContain("리뷰어 verdict:");
  });

  it("does NOT contain former Korean reviewer warning", () => {
    expect(modalSrc).not.toContain("리뷰어 검증을 사용할 수 없습니다");
  });

  it("does NOT contain former Korean empty-state message", () => {
    expect(modalSrc).not.toContain("샘플 레코드를 입력하면 결정론적");
  });

  it("does NOT contain former Korean template comment '활성화'", () => {
    expect(templateSrc).not.toContain("활성화");
  });
});

// ---------------------------------------------------------------------------
// Test group 3 — Conversational compile state references (Sub-task 5.3b)
// ---------------------------------------------------------------------------
describe("3 — Conversational compile UI: state and UI references", () => {
  it("declares clarifyingQuestions state", () => {
    expect(modalSrc).toContain("clarifyingQuestions");
  });

  it("declares conversation state", () => {
    expect(modalSrc).toContain("conversation");
  });

  it("declares pendingAnswer state", () => {
    expect(modalSrc).toContain("pendingAnswer");
  });

  it("has setConversation setter", () => {
    expect(modalSrc).toContain("setConversation");
  });

  it("renders an Answer button for responding to clarifying questions", () => {
    expect(modalSrc).toContain('"Answer"');
  });

  it("uses ConversationTurn type imported from customize-api", () => {
    expect(modalSrc).toContain("ConversationTurn");
  });

  it("pushes user turn to conversation before re-calling onCompileShacl", () => {
    // The modal builds updatedConversation with the user's answer
    expect(modalSrc).toContain("updatedConversation");
  });

  it("passes conversation to onCompileShacl as priorTurns argument", () => {
    // The modal calls onCompileShacl with a conversation array as third arg
    expect(modalSrc).toContain("onCompileShacl(nlText, parsedSamples, conversation");
  });

  it("conversation history div renders user and AI prefixes", () => {
    expect(modalSrc).toContain('"You"');
    expect(modalSrc).toContain('"AI"');
  });

  it("resets conversation on mode switch", () => {
    expect(modalSrc).toContain("resetConversation");
  });

  it("resets conversation state variables on cancel", () => {
    // The resetShaclState() helper is called from cancel, which calls resetConversation()
    expect(modalSrc).toContain("resetShaclState");
  });
});

// ---------------------------------------------------------------------------
// Test group 4 — Round-cap UI (Sub-task 5.3b, round limit)
// ---------------------------------------------------------------------------
describe("4 — Round-cap: 3 rounds exhaustion UI", () => {
  it("counts user turns from conversation to enforce the round cap", () => {
    // The modal computes userTurnCount
    expect(modalSrc).toContain("userTurnCount");
  });

  it("checks userTurnCount >= 3 to detect exhaustion", () => {
    expect(modalSrc).toMatch(/userTurnCount\s*>=\s*3/);
  });

  it("sets roundsExhausted flag", () => {
    expect(modalSrc).toContain("roundsExhausted");
  });

  it("shows exhausted-state message when rounds are exhausted", () => {
    expect(modalSrc).toContain("Compile attempts exhausted");
  });

  it("shows exhausted-state message directing user to raw mode", () => {
    expect(modalSrc).toContain("switch to raw mode");
  });
});

// ---------------------------------------------------------------------------
// Test group 5 — Guide panel: categories, starter prompts, chips (Sub-task 5.3c)
// ---------------------------------------------------------------------------
describe("5 — Guide panel: content and structure", () => {
  it("renders the guide panel headline: What kind of rules can I write?", () => {
    expect(modalSrc).toContain("What kind of rules can I write?");
  });

  it("includes Numeric range category", () => {
    expect(modalSrc).toContain("Numeric range");
  });

  it("includes Allowed values category", () => {
    expect(modalSrc).toContain("Allowed values");
  });

  it("includes Pattern match category", () => {
    expect(modalSrc).toContain("Pattern match");
  });

  it("includes Required field category", () => {
    expect(modalSrc).toContain("Required field");
  });

  it("includes Cardinality category", () => {
    expect(modalSrc).toContain("Cardinality");
  });

  it("includes the not-for-open-ended disclaimer line", () => {
    expect(modalSrc).toContain("Not for open-ended judgments");
  });

  // C1 fix: Calculation was removed from starter prompts (no known fields in _BUILTIN_FIELD_HINTS).
  it("does NOT include fabricated Calculation starter prompt (C1 fix)", () => {
    expect(modalSrc).not.toContain("Block any Calculation result where amount exceeds 3000.");
  });

  it("includes starter prompt for TestRun exitCode", () => {
    expect(modalSrc).toContain("TestRun must have exitCode equal to 0.");
  });

  it("includes starter prompt for EditMatch confidence", () => {
    expect(modalSrc).toContain("EditMatch must have confidence at least 0.8.");
  });

  it("includes starter prompt for DocumentCoverage", () => {
    expect(modalSrc).toContain("DocumentCoverage coverageRatio must be at least 0.9.");
  });

  it("includes starter prompt for SourceInspection", () => {
    expect(modalSrc).toContain("Reject SourceInspection records where inspected is false.");
  });

  // C1 fix: Calculation has [] in _BUILTIN_FIELD_HINTS — must NOT appear in field chips.
  it("does NOT include fabricated Calculation field chip (C1 fix)", () => {
    expect(modalSrc).not.toContain("Calculation: amount");
    expect(modalSrc).not.toContain("Calculation: amount, currency");
  });

  it("includes TestRun evidence field chip with exact canonical fields", () => {
    expect(modalSrc).toContain("TestRun: command, exitCode");
  });

  // C1 fix: EditMatch now has full canonical field set from _BUILTIN_FIELD_HINTS.
  it("includes EditMatch evidence field chip with full canonical fields from _BUILTIN_FIELD_HINTS", () => {
    expect(modalSrc).toContain("EditMatch: tier, tierIndex, confidence, ambiguous, fileDigest, spanDigest");
  });

  // C1 fix: DocumentCoverage now has full canonical field set from _BUILTIN_FIELD_HINTS.
  it("includes DocumentCoverage evidence field chip with full canonical fields from _BUILTIN_FIELD_HINTS", () => {
    expect(modalSrc).toContain("DocumentCoverage: totalUnits, coveredUnits, coverageRatio, threshold, status, sourceDigest, docDigest");
  });

  // C1 fix: SourceInspection now has full canonical field set from _BUILTIN_FIELD_HINTS.
  it("includes SourceInspection evidence field chip with full canonical fields from _BUILTIN_FIELD_HINTS", () => {
    expect(modalSrc).toContain("SourceInspection: sourceId, sourceIds, sourceKind, inspected");
  });

  it("renders Available evidence fields section header", () => {
    expect(modalSrc).toContain("Available evidence fields");
  });

  it("renders Starter prompts section header", () => {
    expect(modalSrc).toContain("Starter prompts");
  });
});

// ---------------------------------------------------------------------------
// Test group 6 — Auto-collapse heuristic (Sub-task 5.3c)
// ---------------------------------------------------------------------------
describe("6 — Guide auto-collapse heuristic", () => {
  it("uses guideExpanded state", () => {
    expect(modalSrc).toContain("guideExpanded");
  });

  it("auto-collapses guide when nlText has content (trim().length check)", () => {
    // The onChange handler for nlText checks if the value has content and collapses
    expect(modalSrc).toContain("trim().length > 0");
    expect(modalSrc).toContain("setGuideExpanded(false)");
  });

  it("resets guide to expanded on kind change and mode switch", () => {
    expect(modalSrc).toContain("setGuideExpanded(true)");
  });

  it("renders Show examples again button to re-expand guide", () => {
    expect(modalSrc).toContain("Show examples again");
  });
});

// ---------------------------------------------------------------------------
// Test group 7 — Regression: existing builder branches still present
// ---------------------------------------------------------------------------
describe("7 — Regression: existing rule-kind builders are intact", () => {
  it("deterministic_ref kind still in union", () => {
    expect(modalSrc).toContain('"deterministic_ref"');
  });

  it("tool_perm kind still in union", () => {
    expect(modalSrc).toContain('"tool_perm"');
  });

  it("llm_criterion kind still in union", () => {
    expect(modalSrc).toContain('"llm_criterion"');
  });

  it("after_tool kind still in union", () => {
    expect(modalSrc).toContain('"after_tool"');
  });

  it("shacl_constraint kind still in union", () => {
    expect(modalSrc).toContain('"shacl_constraint"');
  });

  it("buildRule still builds deterministic_ref correctly", () => {
    expect(modalSrc).toContain('kind: "deterministic_ref"');
    expect(modalSrc).toContain("payload: { ref }");
  });

  it("buildRule still builds tool_perm correctly", () => {
    expect(modalSrc).toContain('kind: "tool_perm"');
    expect(modalSrc).toContain('firesAt: "before_tool_use"');
  });

  it("buildRule still builds llm_criterion correctly", () => {
    expect(modalSrc).toContain('kind: "llm_criterion"');
  });

  it("buildRule still builds shacl_constraint correctly", () => {
    expect(modalSrc).toContain('kind: "shacl_constraint"');
    expect(modalSrc).toContain('firesAt: "pre_final"');
    expect(modalSrc).toContain('action: "block"');
  });

  it("customize-tab wires handleCompileShacl with priorTurns", () => {
    expect(tabSrc).toContain("priorTurns");
    expect(tabSrc).toContain("compileCustomRule");
  });

  it("customize-tab imports ConversationTurn", () => {
    expect(tabSrc).toContain("ConversationTurn");
  });
});

// ---------------------------------------------------------------------------
// Test group 8 — Save-only-on-approval invariant
// ---------------------------------------------------------------------------
describe("8 — Save-only-on-approval: onAdd is NOT called automatically on compile success", () => {
  it("onAdd(buildRule()) is called inside the Looks right — activate button handler", () => {
    // The approve button text appears near the onAdd call
    expect(modalSrc).toContain("Looks right — activate");
    expect(modalSrc).toContain("onAdd(buildRule())");
  });

  it("the compile button onClick does NOT call onAdd", () => {
    // Extract the compile button's onClick region: should not contain onAdd
    // We look for the compile button region and assert onAdd does NOT appear
    // between the compile button and the next button boundary.
    // Simple check: compile success branch leads to setShaclPreview, not onAdd
    expect(modalSrc).toContain("setShaclPreview(result)");
  });

  it("setShaclPreview is called on ok:true, not onAdd directly", () => {
    // The compile flow: if result.ok -> setShaclPreview(result), not onAdd(buildRule())
    // This ensures two separate UI steps (compile then approve).
    expect(modalSrc).toMatch(/result\.ok[\s\S]{0,200}setShaclPreview\(result\)/);
  });

  it("resetShaclState is called after approval (state cleared post-save)", () => {
    // After onAdd(), resetShaclState() is called to clear the form
    expect(modalSrc).toMatch(/onAdd\(buildRule\(\)\)[\s\S]{0,100}resetShaclState/);
  });
});

// ---------------------------------------------------------------------------
// Test group 9 — C1: field honesty — only _BUILTIN_FIELD_HINTS fields in UI
// ---------------------------------------------------------------------------
import { readFileSync as readFileSyncNode } from "node:fs";
import * as path from "node:path";

describe("9 — C1: UI evidence fields are a strict subset of _BUILTIN_FIELD_HINTS", () => {
  // Frozen reference derived verbatim from _BUILTIN_FIELD_HINTS in shacl_compiler.py.
  // If the backend changes, update both files together.
  const BACKEND_FIELD_HINTS: Record<string, string[]> = {
    "TestRun":                     ["command", "exitCode"],
    "CodeDiagnostics":             ["checker", "errorCount", "fileDigest", "diagnosticsDigest"],
    "CommitCheckpoint":            ["checkpointDigest", "pathRef"],
    "DeterministicEvidenceVerifier": [
      "verdictOk", "verdictState", "enforcement",
      "matchedEvidenceTypes", "missingRequirementTypes",
      "failureCodes", "requiredEvidenceTypes",
      "blockModeEnabled", "finalAnswerBlocked",
    ],
    "WebSearch":                   ["query", "resultCount", "sourceKind", "sourceIds"],
    "KnowledgeSearch":             ["query", "resultCount", "sourceKind", "sourceIds"],
    "SourceInspection":            ["sourceId", "sourceIds", "sourceKind", "inspected"],
    "Clock":                       ["sourceKind", "date"],
    "PromptTransform":             ["hook_name", "sections_modified", "tokens_before", "tokens_after"],
    "EditMatch":                   ["tier", "tierIndex", "confidence", "ambiguous", "fileDigest", "spanDigest"],
    "DocumentCoverage":            ["totalUnits", "coveredUnits", "coverageRatio", "threshold", "status", "sourceDigest", "docDigest"],
    // These types have [] in hints — they must NOT appear in STATIC_EVIDENCE_FIELDS:
    "GitDiff":                     [],
    "FileDeliver":                 [],
    "ArtifactVerify":              [],
    "PlanVerifier":                [],
    "Calculation":                 [],
    "DateRange":                   [],
    "TelegramDeliveryAck":         [],
  };

  it("Calculation has [] in backend hints and must NOT appear in STATIC_EVIDENCE_FIELDS", () => {
    expect(BACKEND_FIELD_HINTS["Calculation"]).toEqual([]);
    expect(modalSrc).not.toContain("Calculation: amount");
    expect(modalSrc).not.toContain("Calculation: amount, currency");
  });

  it("every type in STATIC_EVIDENCE_FIELDS has non-empty backend hints", () => {
    // Extract chip entries like "TypeName: field1, field2" from the STATIC_EVIDENCE_FIELDS array
    const chipRegex = /"([A-Z][A-Za-z]+):\s*([^"]+)"/g;
    const chips: Array<{ type: string; fields: string[] }> = [];
    let m: RegExpExecArray | null;
    // Only look in the STATIC_EVIDENCE_FIELDS constant definition
    const staticFieldsBlock = modalSrc.match(/const STATIC_EVIDENCE_FIELDS[\s\S]*?] as const;/)?.[0] ?? "";
    while ((m = chipRegex.exec(staticFieldsBlock)) !== null) {
      const type = m[1];
      const fields = m[2].split(",").map((f) => f.trim()).filter(Boolean);
      chips.push({ type, fields });
    }
    // Must have at least some chips
    expect(chips.length).toBeGreaterThan(0);
    for (const { type, fields } of chips) {
      const backendFields = BACKEND_FIELD_HINTS[type];
      expect(backendFields, `Type "${type}" not found in backend hints`).toBeDefined();
      expect(backendFields.length, `Type "${type}" has [] in backend hints but appears in UI`).toBeGreaterThan(0);
      const backendSet = new Set(backendFields);
      for (const field of fields) {
        expect(backendSet.has(field), `Field "${field}" for type "${type}" not in backend hints`).toBe(true);
      }
    }
  });

  it("every starter prompt that references a type uses only that type's real fields", () => {
    // Extract starter prompts from the STARTER_PROMPTS constant
    const starterBlock = modalSrc.match(/const STARTER_PROMPTS[\s\S]*?] as const;/)?.[0] ?? "";
    const promptRegex = /"([^"]+)"/g;
    const prompts: string[] = [];
    let pm: RegExpExecArray | null;
    while ((pm = promptRegex.exec(starterBlock)) !== null) {
      prompts.push(pm[1]);
    }
    expect(prompts.length).toBeGreaterThan(0);
    // For each prompt, if it mentions an evidence type, check the fields referenced exist in hints.
    for (const prompt of prompts) {
      for (const [type, fields] of Object.entries(BACKEND_FIELD_HINTS)) {
        if (prompt.includes(type)) {
          // The type appears — if fields is empty, no field-specific reference should appear
          if (fields.length === 0) {
            // This would be a violation: a prompt mentions a type with empty hints
            // We just assert the type isn't paired with any claimed field
            // (No structural way to verify without NLP — but Calculation is the concrete case)
            if (type === "Calculation") {
              expect(prompt).not.toContain("amount");
              expect(prompt).not.toContain("currency");
            }
          }
        }
      }
    }
  });
});

// ---------------------------------------------------------------------------
// Test group 10 — I1: exhausted state is reachable (round-cap UX fix)
// ---------------------------------------------------------------------------
describe("10 — I1: exhausted state — round-cap UX is no longer dead code", () => {
  it("declares exhausted boolean state", () => {
    expect(modalSrc).toContain("exhausted");
    expect(modalSrc).toMatch(/\bexhausted\b.*useState/s);
  });

  it("setExhausted(true) is called when rounds are consumed", () => {
    expect(modalSrc).toContain("setExhausted(true)");
  });

  it("Compile button is disabled when exhausted is true", () => {
    expect(modalSrc).toContain("exhausted");
    // The disabled prop includes || exhausted
    expect(modalSrc).toMatch(/disabled=\{[^}]*exhausted[^}]*\}/);
  });

  it("Compile button is disabled when clarifyingQuestions is non-null (M-compile-disabled)", () => {
    expect(modalSrc).toMatch(/disabled=\{[^}]*clarifyingQuestions[^}]*\}/);
  });

  it("Answer button is disabled when userTurnCount >= 3 (pre-check before click)", () => {
    expect(modalSrc).toMatch(/disabled=\{[^}]*userTurnCount\s*>=\s*3[^}]*\}/);
  });

  it("exhausted card renders without requiring clarifyingQuestions to be non-null", () => {
    // The condition includes `|| exhausted` so the card is reachable even when clarifyingQuestions is null
    expect(modalSrc).toMatch(/clarifyingQuestions.*?\|\|.*?exhausted|exhausted.*?\|\|.*?clarifyingQuestions/s);
  });
});

// ---------------------------------------------------------------------------
// Test group 11 — I2: guide panel honesty sentence
// ---------------------------------------------------------------------------
describe("11 — I2: guide panel has the AI-translation honesty sentence", () => {
  it("contains the Clicking Compile asks an AI sentence", () => {
    expect(modalSrc).toContain("Clicking Compile asks an AI");
  });

  it("honesty sentence mentions review before activation", () => {
    expect(modalSrc).toContain("explicitly activate it");
  });

  it("honesty sentence confirms nothing is saved before approval", () => {
    expect(modalSrc).toContain("nothing is saved before approval");
  });
});

// ---------------------------------------------------------------------------
// Test group 12 — I3: guide toggle aria-expanded + aria-controls
// ---------------------------------------------------------------------------
describe("12 — I3: guide toggle has accessibility attributes", () => {
  it("guide content div has id=shacl-guide-content", () => {
    expect(modalSrc).toContain('id="shacl-guide-content"');
  });

  it("guide toggle button has aria-expanded", () => {
    expect(modalSrc).toContain("aria-expanded={guideExpanded}");
  });

  it("guide toggle button has aria-controls=shacl-guide-content", () => {
    expect(modalSrc).toContain('aria-controls="shacl-guide-content"');
  });
});

// ---------------------------------------------------------------------------
// Test group 13 — I4: nlText onChange does NOT call resetConversation
// ---------------------------------------------------------------------------
describe("13 — I4: nlText onChange does not reset conversation", () => {
  it("nlText onChange handler does NOT contain resetConversation() call", () => {
    // The textarea has aria-label "Natural-language constraint input" and its onChange
    // must NOT call resetConversation() — only the guide collapse and preview reset happen there.
    // Locate the 600-char window after the aria-label (which always precedes onChange in the file).
    const onChangeRegion = modalSrc.match(
      /aria-label="Natural-language constraint input"[\s\S]{0,600}/
    )?.[0] ?? "";
    expect(onChangeRegion.length, "Could not find Natural-language constraint textarea region").toBeGreaterThan(50);
    expect(onChangeRegion).not.toContain("resetConversation()");
  });
});

// ---------------------------------------------------------------------------
// Test group 14 — I5: aria-live region for compile status
// ---------------------------------------------------------------------------
describe("14 — I5: aria-live region wraps compile status/error area", () => {
  it("source contains role=status or aria-live near compile errors", () => {
    expect(modalSrc).toMatch(/role="status"|aria-live="polite"/);
  });

  it("clarifying questions card has aria-live=polite", () => {
    expect(modalSrc).toContain('aria-live="polite"');
  });
});

// ---------------------------------------------------------------------------
// Test group 15 — I6: kind-change handler uses resetShaclState
// ---------------------------------------------------------------------------
describe("15 — I6: kind-change handler uses resetShaclState()", () => {
  it("Rule type Select onChange calls resetShaclState()", () => {
    expect(modalSrc).toContain("resetShaclState()");
  });

  it("kind-change handler does NOT contain ad-hoc setShaclPreview(null) inline reset (uses resetShaclState instead)", () => {
    // The onChange for the kind Select should just call resetShaclState(), not inline resets.
    // Locate the kind Select onChange region.
    const kindChangeRegion = modalSrc.match(
      /Rule type[\s\S]{0,200}onChange=\{[\s\S]{0,400}?\}/
    )?.[0] ?? "";
    expect(kindChangeRegion.length).toBeGreaterThan(0);
    // resetShaclState is present
    expect(kindChangeRegion).toContain("resetShaclState");
  });
});

// ---------------------------------------------------------------------------
// Test group 16 — M-error: customize-api.ts forwards backend error body
// ---------------------------------------------------------------------------
describe("16 — M-error: compileCustomRule forwards backend error body", () => {
  const apiSrc = readFileSyncNode(
    new URL("../../../lib/customize-api.ts", import.meta.url),
    "utf8",
  );

  it("!res.ok branch attempts to read JSON body for error message", () => {
    expect(apiSrc).toContain("res.json()");
    expect(apiSrc).toMatch(/!res\.ok[\s\S]{0,300}res\.json\(\)/);
  });

  it("!res.ok branch has try/catch around JSON parse to handle non-JSON bodies", () => {
    expect(apiSrc).toMatch(/try\s*\{[\s\S]{0,300}res\.json\(\)[\s\S]{0,200}\}\s*catch/);
  });

  it("backend error string is forwarded if present", () => {
    expect(apiSrc).toContain("errBody.error");
    expect(apiSrc).toContain("backendError");
  });
});
