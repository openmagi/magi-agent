import { describe, expect, it, beforeEach, vi } from "vitest";
import { makeSwitchToActModeTool } from "./SwitchToActMode.js";
import type { ToolContext } from "../Tool.js";
import { ToolRegistry } from "./ToolRegistry.js";

function stubCtx(overrides?: Partial<ToolContext>): ToolContext {
  return {
    botId: "test-bot",
    sessionKey: "test-session",
    turnId: "test-turn",
    workspaceRoot: "/tmp/test",
    askUser: vi.fn(),
    emitProgress: vi.fn(),
    abortSignal: new AbortController().signal,
    staging: {
      stageFileWrite: vi.fn(),
      stageTranscriptAppend: vi.fn(),
      stageAuditEvent: vi.fn(),
    },
    ...overrides,
  };
}

describe("SwitchToActMode tool", () => {
  let registry: ToolRegistry;

  beforeEach(() => {
    registry = new ToolRegistry();
  });

  it("has correct name and meta permission", () => {
    const tool = makeSwitchToActModeTool(registry);
    expect(tool.name).toBe("SwitchToActMode");
    expect(tool.permission).toBe("meta");
  });

  it("is available in plan mode only", () => {
    const tool = makeSwitchToActModeTool(registry);
    expect(tool.availableInModes).toEqual(["plan"]);
  });

  it("switches registry to act mode on execute", async () => {
    registry.setMode("plan");
    const tool = makeSwitchToActModeTool(registry);
    const result = await tool.execute({}, stubCtx());
    expect(result.status).toBe("ok");
    expect(registry.getMode()).toBe("act");
  });

  it("logs mode transition in result metadata", async () => {
    registry.setMode("plan");
    const tool = makeSwitchToActModeTool(registry);
    const result = await tool.execute({}, stubCtx());
    expect(result.metadata).toEqual(
      expect.objectContaining({
        previousMode: "plan",
        currentMode: "act",
      }),
    );
  });

  it("stages an audit event for the mode transition", async () => {
    registry.setMode("plan");
    const ctx = stubCtx();
    const tool = makeSwitchToActModeTool(registry);
    await tool.execute({}, ctx);
    expect(ctx.staging.stageAuditEvent).toHaveBeenCalledWith(
      "mode_transition",
      expect.objectContaining({ from: "plan", to: "act" }),
    );
  });
});
