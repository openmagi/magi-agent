/**
 * Source-grep tests for ConversationalPolicyCompose + its Customize-hub
 * wiring. Same pattern as the sibling .local.test.ts files: we read the
 * .tsx source and assert on the invariants the slice spec calls out. The
 * vitest config in this repo does not resolve the ``@/`` path alias the
 * component imports under, so we deliberately do NOT execute the
 * component; that coupling stays a separate runtime-test concern.
 */

import { readFileSync } from "node:fs";

import { describe, expect, it } from "vitest";


const SRC = readFileSync(
  new URL("./conversational-policy-compose.tsx", import.meta.url),
  "utf-8",
);

const HUB = readFileSync(
  new URL("./customize-hub.tsx", import.meta.url),
  "utf-8",
);


describe("summarizePolicyParams helper: source-level invariants", () => {
  it("is exported so the draft pane (and tests) can reuse it", () => {
    expect(SRC).toContain("export function summarizePolicyParams(");
  });

  it("reads the policy PARAMS shape the backend assembles", () => {
    // gatedTool / fetchTool / allowlistDomains / evidenceLabel /
    // onUnavailable are the params nl_policy_interactive.step_policy_compile
    // returns. Grep the field names so a backend param rename surfaces in
    // code review, not silently as a blank pane.
    expect(SRC).toContain("p.evidenceLabel");
    expect(SRC).toContain("p.gatedTool");
    expect(SRC).toContain("p.fetchTool");
    expect(SRC).toContain("p.allowlistDomains");
    expect(SRC).toContain("p.onUnavailable");
  });

  it("defaults fetchTool to web_fetch (mirrors the templater default)", () => {
    expect(SRC).toContain('"web_fetch"');
  });

  it("renders onUnavailable as plain language (deny / ask), no raw enum leak", () => {
    expect(SRC).toContain("deny the tool");
    expect(SRC).toContain("ask for approval");
  });
});


describe("ConversationalPolicyCompose source-level invariants", () => {
  it("is a client component (uses React state + effects)", () => {
    expect(SRC.startsWith("/**")).toBe(true); // file-level doc block
    expect(SRC).toContain('"use client"');
    expect(SRC).toContain("useState");
    expect(SRC).toContain("useEffect");
    expect(SRC).toContain("useRef");
  });

  it("preserves the IME composition guard from the single-rule component", () => {
    // Korean (Hangul) IME signals Enter to finalize a composition; we MUST
    // NOT send the message on that keystroke.
    expect(SRC).toContain("composingRef");
    expect(SRC).toContain("onCompositionStart");
    expect(SRC).toContain("onCompositionEnd");
    expect(SRC).toContain("isComposing");
  });

  it("preserves the AbortController + monotonic reqId race guards", () => {
    expect(SRC).toContain("AbortController");
    expect(SRC).toContain("sendAbortRef");
    expect(SRC).toContain("reqIdRef");
    expect(SRC).toContain("++reqIdRef.current");
    // Stale-response drop: the async branch compares its myId against the
    // current reqIdRef before writing state.
    expect(SRC).toContain("myId !== reqIdRef.current");
  });

  it("uses functional setHistory everywhere (no closure snapshots)", () => {
    const direct = SRC.match(/setHistory\(\[/g);
    expect(direct, "direct setHistory([...]) is forbidden").toBeNull();
    expect(SRC).toMatch(/setHistory\(\(prev\)/);
  });

  it("threads server-driven params/plan (client never mutates them)", () => {
    // The next turn echoes the last response's params as paramsSoFar, and
    // the plan is whatever the server assembled; the client only mirrors.
    expect(SRC).toContain("paramsSoFar: params");
    expect(SRC).toContain("setParams(body.params");
    expect(SRC).toContain("setPlan(body.plan");
    const directParams = SRC.match(/setParams\(\{/g);
    expect(directParams, "params are server-driven, never hand-built").toBeNull();
  });

  it("renders the chat scroll with role=log + aria-live=polite", () => {
    expect(SRC).toContain('role="log"');
    expect(SRC).toContain('aria-live="polite"');
  });

  it("posts to the POLICY interactive endpoint, not the single-rule one", () => {
    expect(SRC).toContain("compilePolicyInteractive");
    // Must NOT reach for the single-rule interactive helper.
    expect(SRC).not.toContain("compileCustomRuleInteractive");
  });

  it("owns the save via savePolicyFromPlan, gated on ready_to_save", () => {
    // Unlike the single-rule component (parent persists), the policy save
    // is a 3-store composition behind one endpoint, so the component calls
    // savePolicyFromPlan itself and reports up via onSaved.
    expect(SRC).toContain("savePolicyFromPlan");
    expect(SRC).toContain("onSaved(result)");
    // Save CTA is disabled until the server flips ready_to_save.
    expect(SRC).toContain("readyToSave");
    expect(SRC).toContain("disabled={!readyToSave || saving}");
    // A save failure surfaces inline and does not throw.
    expect(SRC).toContain("setSaveError");
  });

  it("treats a present error string as an error even without ok:false", () => {
    // The policy interactive route returns {ready_to_save:false,
    // error:"compile timed out"|"compile failed"} at HTTP 200 with NO ok
    // field. An ok===false-only check would fall through to the success
    // path and wipe accumulated params, so the guard must also fire on a
    // non-empty error string.
    expect(SRC).toMatch(/body\.ok === false \|\| rawError\.length > 0/);
  });

  it("disables question pills while a save is in flight", () => {
    // sendTurn also early-returns on `saving`, but the pills must visibly
    // disable too so a concurrent compile turn is never initiated mid-save.
    expect(SRC).toContain("disabled={pending || saving}");
  });

  it("surfaces the policy as TWO linked rules (producer + gate)", () => {
    // The whole point of a policy is the multi-rule composition; the draft
    // pane makes both halves legible.
    expect(SRC).toContain('data-testid="policy-draft-producer"');
    expect(SRC).toContain('data-testid="policy-draft-gate"');
    expect(SRC).toMatch(/>\s*Records\s*</);
    expect(SRC).toMatch(/>\s*Blocks\s*</);
  });

  it("carries the dashboard testid contract", () => {
    expect(SRC).toContain('data-testid="conversational-policy-root"');
    expect(SRC).toContain('data-testid="conversational-policy-input"');
    expect(SRC).toContain('data-testid="conversational-policy-send"');
    expect(SRC).toContain('data-testid="conversational-policy-typing"');
    expect(SRC).toContain('data-testid="conversational-policy-starters"');
    expect(SRC).toContain('data-testid="policy-draft-pane"');
    expect(SRC).toContain('data-testid="policy-draft-save"');
  });

  it("surfaces the reuse badge from producer_reused", () => {
    // The backend echoes producer_reused when the plan binds an existing
    // producer; the pane shows a badge so the operator sees no duplicate was
    // created.
    expect(SRC).toContain("body.producer_reused");
    expect(SRC).toContain("setProducerReused");
    expect(SRC).toContain('data-testid="policy-draft-reused-badge"');
  });

  it("offers an advisory review that never gates Save", () => {
    // reviewPolicyPlan is called on demand; the verdict is displayed but the
    // Save CTA stays gated only on readyToSave + saving (NOT on the verdict).
    expect(SRC).toContain("reviewPolicyPlan");
    expect(SRC).toContain('data-testid="policy-draft-review-run"');
    expect(SRC).toContain("Advisory only");
    // Save stays gated on readiness/saving alone, never on the review verdict.
    expect(SRC).toContain("disabled={!readyToSave || saving}");
    expect(SRC).not.toMatch(/disabled=\{[^}]*review[^}]*\}[\s\S]{0,40}policy-draft-save/);
    // A fresh compile turn resets the review, and an in-flight review drops
    // its verdict if the plan changed under it (monotonic reqId stale-drop,
    // same pattern as sendTurn). Source-grep only asserts the guards exist.
    expect(SRC).toContain("setReview(null)");
    expect(SRC).toContain("if (myReqId === reqIdRef.current) setReview(result)");
  });

  it("uses sub-path imports only (CLAUDE.md rule)", () => {
    expect(SRC).not.toMatch(/from\s+["']@\/components\/ui["']/);
  });

  it("offers starter pills for the policy shape", () => {
    // Count the pill entries by their `label: "..."` key (formatting-
    // agnostic; the type annotation `label: string` has no quote).
    const matches = SRC.match(/label:\s*"/g) ?? [];
    expect(matches.length).toBeGreaterThanOrEqual(3);
  });

  it("contains no em-dash or en-dash (house style)", () => {
    // Escaped code points so this guard does not itself contain the
    // characters it forbids (U+2014 em, U+2013 en).
    expect(SRC).not.toMatch(/[\u2014\u2013]/);
  });
});


describe("Customize-hub wiring for the policy composer", () => {
  it("imports and mounts ConversationalPolicyCompose", () => {
    expect(HUB).toContain(
      'import { ConversationalPolicyCompose } from "./conversational-policy-compose"',
    );
    expect(HUB).toContain("<ConversationalPolicyCompose");
  });

  it('mounts inside the single "Add policy" flow as the "linked" surface (PR-4)', () => {
    expect(HUB).toContain('phase: "policy"');
    expect(HUB).toContain("Add policy");
    // PR-4 consolidation: the composer is the producer+gate surface of the
    // ONE Add-policy flow; the standalone add-rule button is retired.
    expect(HUB).toContain('composeSurface("linked")');
    expect(HUB).not.toMatch(/>\s*Add rule\b/);
  });

  it("reloads catalog + dashboard checks on a successful save", () => {
    // onSaved wires reload() + reloadDashboardChecks() so the new gate
    // (dashboard check) AND the new custom_rule/Policy both re-render.
    expect(HUB).toMatch(
      /onSaved=\{\(\)\s*=>\s*\{[\s\S]{0,200}reloadDashboardChecks\(\)/,
    );
  });
});
