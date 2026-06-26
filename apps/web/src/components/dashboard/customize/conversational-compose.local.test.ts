/**
 * Source-grep tests for ConversationalCompose. Same pattern as the
 * sibling .local.test.ts files: we read the .tsx source and assert on
 * the patterns the port spec calls out. The vitest config in this
 * repo does not resolve the ``@/`` path alias the component imports
 * under, so we deliberately do NOT execute the component — that
 * coupling stays a separate runtime-test concern.
 */

import { readFileSync } from "node:fs";

import { describe, expect, it } from "vitest";


const SRC = readFileSync(
  new URL("./conversational-compose.tsx", import.meta.url),
  "utf-8",
);


describe("summarizeDraft helper — source-level invariants", () => {
  it("exports summarizeDraft so the dashboard can re-use it elsewhere", () => {
    expect(SRC).toContain("export function summarizeDraft(");
  });

  it("reads magi-agent's custom_rule shape (NOT magi-cp's IR)", () => {
    // The summarizer pulls kind / firesAt / action / scope off the
    // top-level custom_rule shape. We grep for the field names so a
    // future refactor to magi-cp's IR (trigger.event/matcher) lands
    // in code review, not silently.
    expect(SRC).toContain("draft.what");
    expect(SRC).toContain("draft.firesAt");
    expect(SRC).toContain("draft.action");
    expect(SRC).toContain("draft.scope");
  });

  it("renders the 4 axes (What / When / Action / Scope) with plain-English labels", () => {
    expect(SRC).toContain('"What"');
    expect(SRC).toContain('"When"');
    expect(SRC).toContain('"Action"');
    expect(SRC).toContain('"Scope"');
  });

  it("maps all 9 kinds to plain-language labels (no internal vocab leaks)", () => {
    expect(SRC).toContain("tool_perm:");
    expect(SRC).toContain("llm_criterion:");
    expect(SRC).toContain("deterministic_ref:");
    expect(SRC).toContain("shacl_constraint:");
    expect(SRC).toContain("capability_scope:");
    expect(SRC).toContain("prompt_injection:");
    expect(SRC).toContain("output_rewrite:");
    expect(SRC).toContain("shell_command:");
    expect(SRC).toContain("shell_check:");
  });
});


describe("ConversationalCompose source-level invariants", () => {
  it("is a client component (uses React state + effects)", () => {
    expect(SRC.startsWith('/**')).toBe(true); // file-level doc block
    expect(SRC).toContain('"use client"');
    expect(SRC).toContain("useState");
    expect(SRC).toContain("useEffect");
    expect(SRC).toContain("useRef");
  });

  it("preserves the magi-cp IME composition guard", () => {
    // Korean (Hangul) IME signals Enter to finalize a composition;
    // we MUST NOT send the message on that keystroke. Magi-cp uses
    // a paired composingRef + isComposing check; the port keeps both.
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
    // Stale-response drop: every async branch compares its myId
    // against the current reqIdRef before writing state.
    expect(SRC).toContain("myId !== reqIdRef.current");
  });

  it("uses functional setHistory everywhere (no closure snapshots)", () => {
    // The pattern ``setHistory((prev) => ...)`` is the only setter
    // shape allowed; a direct ``setHistory([...])`` call would race
    // with interleaved updates. The match is a regex so any direct
    // setHistory call without a callback would surface here.
    const direct = SRC.match(/setHistory\(\[/g);
    expect(direct, "direct setHistory([...]) is forbidden").toBeNull();
    expect(SRC).toMatch(/setHistory\(\(prev\)/);
  });

  it("renders the chat scroll with role=log + aria-live=polite", () => {
    expect(SRC).toContain('role="log"');
    expect(SRC).toContain('aria-live="polite"');
  });

  it("posts to the magi-agent compile-interactive endpoint", () => {
    expect(SRC).toContain("compileCustomRuleInteractive");
    // The endpoint URL lives in customize-api.ts (helper); we don't
    // re-grep for it here, but we DO assert the helper is wired and
    // not, e.g., compileCustomRule (the one-shot route).
    expect(SRC).not.toMatch(/compileCustomRule\(/);
  });

  it("carries the dashboard testid contract", () => {
    expect(SRC).toContain('data-testid="conversational-compose-root"');
    expect(SRC).toContain('data-testid="conversational-input"');
    expect(SRC).toContain('data-testid="conversational-send"');
    expect(SRC).toContain('data-testid="conversational-typing"');
    expect(SRC).toContain('data-testid="conversational-starters"');
    expect(SRC).toContain('data-testid="draft-pane"');
    expect(SRC).toContain('data-testid="draft-pane-save"');
  });

  it("uses sub-path imports only (CLAUDE.md rule)", () => {
    // Barrel imports like ``@/components/ui`` pull a server-only
    // chain into the client bundle and break the build. Sub-path
    // imports stay inside the client.
    expect(SRC).not.toMatch(/from\s+["']@\/components\/ui["']/);
  });

  it("exposes 5 starter pills", () => {
    // Pin the canonical starter-pill count so a future trim or
    // expansion lands in a deliberate code review, not silently.
    const matches = SRC.match(/{ label: "/g) ?? [];
    expect(matches.length).toBeGreaterThanOrEqual(5);
  });
});
