import { describe, expect, it } from "vitest";
import type { HookContext } from "../types.js";
import { DebugWorkflow } from "../../debug/DebugWorkflow.js";
import { makeDebugTurnClassifierHook } from "./debugTurnClassifier.js";
import { makeDebugInvestigationGuardHook } from "./debugInvestigationGuard.js";
import {
  makeDebugAfterToolCheckpointHook,
  makeDebugCommitCheckpointHook,
} from "./debugCheckpointRecorder.js";

function makeCtx(workflow: DebugWorkflow): HookContext {
  return {
    botId: "bot-test",
    userId: "user-test",
    sessionKey: "session-test",
    turnId: "turn-test",
    llm: {} as HookContext["llm"],
    transcript: [],
    emit: () => {},
    log: () => {},
    agentModel: "test-model",
    abortSignal: new AbortController().signal,
    deadlineMs: 5_000,
    debugWorkflow: workflow,
  };
}

describe("debug workflow hooks", () => {
  it("classifies debug turns at beforeTurnStart", async () => {
    const workflow = new DebugWorkflow();
    const hook = makeDebugTurnClassifierHook({ workflow });
    await hook.handler({ userMessage: "The build is failing, debug it." }, makeCtx(workflow));

    expect(workflow.getTurnState("session-test", "turn-test")?.classified).toBe(true);
  });

  it("blocks patch tools on debug turns until investigation happened", async () => {
    const workflow = new DebugWorkflow();
    workflow.classifyTurn("session-test", "turn-test", "Regression failing in production");

    const hook = makeDebugInvestigationGuardHook({ workflow });
    const result = await hook.handler(
      {
        toolName: "FileEdit",
        toolUseId: "tool-1",
        input: { path: "src/app.ts", oldText: "a", newText: "b" },
      },
      makeCtx(workflow),
    );

    expect(result?.action).toBe("block");
    if (result?.action === "block") {
      expect(result.reason).toContain("[RETRY:DEBUG_INVESTIGATE]");
    }
  });

  it("records investigation, patch, verification, and hypothesis checkpoints", async () => {
    const workflow = new DebugWorkflow();
    workflow.classifyTurn("session-test", "turn-test", "Tests started failing after the refactor");
    const ctx = makeCtx(workflow);
    const afterTool = makeDebugAfterToolCheckpointHook({ workflow });
    const beforeCommit = makeDebugCommitCheckpointHook({ workflow });

    await afterTool.handler(
      {
        toolName: "FileRead",
        toolUseId: "tool-1",
        input: { path: "src/app.ts" },
        result: { ok: true, output: "const value = bug();", metadata: {} },
      },
      ctx,
    );
    await afterTool.handler(
      {
        toolName: "FileEdit",
        toolUseId: "tool-2",
        input: { path: "src/app.ts", oldText: "bug()", newText: "fix()" },
        result: { ok: true, output: "updated", metadata: {} },
      },
      ctx,
    );
    await afterTool.handler(
      {
        toolName: "Bash",
        toolUseId: "tool-3",
        input: { command: "npm test -- app.test.ts" },
        result: { ok: true, output: "1 passed", metadata: {} },
      },
      ctx,
    );
    await beforeCommit.handler(
      {
        assistantText: "Likely cause was the stale helper import. I fixed it and the test now passes.",
        toolCallCount: 3,
        toolReadHappened: true,
        userMessage: "Regression failing in production",
        retryCount: 0,
      },
      ctx,
    );

    expect(workflow.getTurnState("session-test", "turn-test")).toMatchObject({
      classified: true,
      investigated: true,
      hypothesized: true,
      patched: true,
      verified: true,
    });
  });
});
