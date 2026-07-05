import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { SubagentWorkPanel } from "./subagent-work-panel";
import { deriveWorkConsoleRows } from "@/chat-core";
import type { ChannelState, SubagentActivity } from "@/chat-core";

function channelState(overrides: Partial<ChannelState> = {}): ChannelState {
  return {
    streaming: false,
    streamingText: "",
    thinkingText: "",
    error: null,
    hasTextContent: false,
    thinkingStartedAt: null,
    turnPhase: null,
    heartbeatElapsedMs: null,
    pendingInjectionCount: 0,
    activeTools: [],
    subagents: [],
    taskBoard: null,
    fileProcessing: false,
    ...overrides,
  };
}

function agentRows(state: ChannelState) {
  return deriveWorkConsoleRows({ channelState: state }).filter(
    (row) => row.group === "subagent",
  );
}

describe("SubagentWorkPanel completed-child rendering (T2)", () => {
  it("renders a completed child chip with model and task labels after turn end", () => {
    const completed: SubagentActivity = {
      taskId: "child-1",
      role: "researcher",
      status: "done",
      taskTitle: "Survey the codebase",
      model: "claude-opus-4-8",
      startedAt: 100,
      updatedAt: 200,
    };
    const state = channelState({ streaming: false, subagents: [completed] });

    const html = renderToStaticMarkup(
      <SubagentWorkPanel rows={agentRows(state)} channelState={state} />,
    );

    // The chip is present (a subagent option button) while streaming=false.
    expect(html).toContain('data-work-console-subagent-option="child-1"');
    // Task title surfaces as the chip detail.
    expect(html).toContain("Survey the codebase");
    // Model surfaces in the chip meta (role . model).
    expect(html).toContain("claude-opus-4-8");
    // The done status dot styling is applied (emerald), not the running accent.
    expect(html).toContain("bg-emerald-500");
  });

  it("renders multiple retained children including an errored one", () => {
    const done: SubagentActivity = {
      taskId: "child-done",
      role: "writer",
      status: "done",
      taskTitle: "Draft section",
      model: "claude-sonnet-4-5",
      startedAt: 100,
      updatedAt: 220,
    };
    const errored: SubagentActivity = {
      taskId: "child-err",
      role: "reviewer",
      status: "error",
      taskTitle: "Review draft",
      model: "claude-opus-4-8",
      startedAt: 110,
      updatedAt: 210,
    };
    const state = channelState({ streaming: false, subagents: [done, errored] });

    const html = renderToStaticMarkup(
      <SubagentWorkPanel rows={agentRows(state)} channelState={state} />,
    );

    expect(html).toContain('data-work-console-subagent-option="child-done"');
    expect(html).toContain('data-work-console-subagent-option="child-err"');
    expect(html).toContain("Draft section");
    expect(html).toContain("Review draft");
    // Errored child renders the red status dot.
    expect(html).toContain("bg-red-500");
  });
});
