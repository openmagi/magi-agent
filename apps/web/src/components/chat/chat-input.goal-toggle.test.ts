// Phase 1 of the goal-loop design (clawy docs/plans/2026-06-21-magi-goal-loop-
// clean-break-judge-design.md) restores the per-send Goal-mission toggle that
// 14f0c7f9 removed. The earlier "always-on goalMode" PR shipped without backend
// wiring, leaving goalMode dormant. We bring back the explicit opt-in until
// judge accuracy + latency are measured; then we promote to default-on and
// (eventually) hide the toggle.
//
// We can't easily render ChatInput in this repo's test environment (alias chain
// requires Next.js / @/chat-core/attachments), so pin the contract by reading
// the source file directly — same approach as
// model-options.label-consistency.test.ts.

import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

const source = readFileSync(
  join(import.meta.dirname, "chat-input.tsx"),
  "utf-8",
);

describe("ChatInput goal-mode toggle (Phase 1)", () => {
  it("renders a button carrying data-chat-goal-toggle", () => {
    expect(source).toMatch(/data-chat-goal-toggle="true"/);
  });

  it("labels the toggle as Deep run in English and 집중 모드 in Korean", () => {
    expect(source).toContain("Deep run");
    expect(source).toContain("집중 모드");
  });

  it("declares a goalMode state hook initialized to false (Phase 1 opt-in)", () => {
    // useState(false) for the toggle. We match the literal call so a future
    // flip to useState(true) (Phase 2 default-on) trips this test and forces
    // a documented promotion.
    expect(source).toMatch(/useState<boolean>\(false\)|useState\(false\)/);
    expect(source).toMatch(/setGoalMode|setGoalModeEnabled|setGoal/);
  });

  it("buildChatInputSendOptions takes a goalMode argument and only emits it when true", () => {
    // The exported helper carries goalMode as an explicit parameter so callers
    // (chat-view-client) pass the live toggle state, not a hard-coded literal.
    expect(source).toMatch(
      /buildChatInputSendOptions\([^)]*goalMode[^)]*\)/,
    );
    // Conditional spread — goalMode field only present when the toggle is on.
    // Pattern: ...(goalMode ? { goalMode: true } : {})
    expect(source).toMatch(
      /\.\.\.\(\s*goalMode\s*\?\s*\{\s*goalMode:\s*true\s*\}\s*:\s*\{\}\s*\)/,
    );
  });

  it("drops the dead always-on literal that masqueraded as a working feature", () => {
    // The 14f0c7f9 hard-coded `goalMode: true` literal must be gone from the
    // helper body — its presence is what hid the missing backend wiring for
    // months. If a future change resurrects always-on it must do so behind a
    // verified default-on flag, not a bare literal.
    expect(source).not.toMatch(/\n\s*goalMode:\s*true,\s*\n/);
  });
});
