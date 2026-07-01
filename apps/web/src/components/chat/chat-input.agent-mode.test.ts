// Agent-mode (posture) composer selector — WS-10 of the mode/pack/component
// model (clawy docs/plans/2026-06-30-magi-mode-pack-component-model.md). The
// composer sends the active mode id as the per-turn `agentMode` field; the
// runtime resolves it into the system prompt + tool delta.
//
// As with the goal-toggle test, ChatInput can't be rendered here (the `@/`
// alias chain requires Next.js), so we pin the contract by reading the source.

import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

const source = readFileSync(join(import.meta.dirname, "chat-input.tsx"), "utf-8");

describe("ChatInput agent-mode selector (WS-10)", () => {
  it("renders a mode selector carrying data-chat-mode-selector", () => {
    expect(source).toMatch(/data-chat-mode-selector="dropdown"/);
  });

  it("labels the selector as Agent mode in English and 에이전트 모드 in Korean", () => {
    expect(source).toContain("Agent mode");
    expect(source).toContain("에이전트 모드");
  });

  it("offers a Default option (empty value) plus the fetched modes", () => {
    // The Default option sends no field (empty string), byte-identical to a
    // send with no mode selected.
    expect(source).toMatch(/<option value="">\{t\(language, "Default", "기본값"\)\}/);
    expect(source).toMatch(/availableModes\.map\(/);
  });

  it("only renders the selector when at least one mode exists", () => {
    expect(source).toMatch(/availableModes\.length > 0 &&/);
  });

  it("guards against sending a stale mode id no longer in the list", () => {
    // effectiveAgentMode must validate the selection against availableModes so
    // a mode deleted server-side while selected never leaks onto the payload.
    expect(source).toMatch(
      /availableModes\.some\(\s*\(mode\)\s*=>\s*mode\.id === agentMode\s*\)/,
    );
  });

  it("buildChatInputSendOptions takes an agentMode arg and only emits it when set", () => {
    expect(source).toMatch(/buildChatInputSendOptions\([^)]*agentMode[^)]*\)/s);
    // Conditional spread — no field for the Default (empty) selection.
    expect(source).toMatch(
      /\.\.\.\(\s*agentMode\s*\?\s*\{\s*agentMode\s*\}\s*:\s*\{\}\s*\)/,
    );
  });
});
