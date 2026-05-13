import { afterEach, describe, expect, it } from "vitest";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import type { HookContext } from "../types.js";
import { Workspace } from "../../storage/Workspace.js";
import { PolicyKernel } from "../../policy/PolicyKernel.js";
import { makePolicyPromptBlockHook } from "./policyPromptBlock.js";

const roots: string[] = [];

async function makeRoot(userRules?: string): Promise<string> {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), "policy-hook-"));
  roots.push(root);
  if (userRules !== undefined) {
    await fs.writeFile(path.join(root, "USER-RULES.md"), userRules, "utf8");
  }
  return root;
}

function makeCtx(): HookContext {
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
  };
}

afterEach(async () => {
  await Promise.all(
    roots.splice(0).map((root) => fs.rm(root, { recursive: true, force: true })),
  );
});

describe("policyPromptBlock", () => {
  it("injects a compact runtime policy block on the first iteration", async () => {
    const root = await makeRoot("- Always answer in Korean.\n- Include page numbers when citing.");
    const hook = makePolicyPromptBlockHook({
      policy: new PolicyKernel(new Workspace(root)),
    });

    const result = await hook.handler(
      {
        messages: [{ role: "user", content: "안녕" }],
        tools: [],
        system: "base system",
        iteration: 0,
      },
      makeCtx(),
    );

    expect(result?.action).toBe("replace");
    if (result?.action === "replace") {
      expect(result.value.system).toContain("<runtime_policy");
      expect(result.value.system).toContain("approval.explicit_consent_for_external_actions=true");
      expect(result.value.system).toContain("response.language=ko");
      expect(result.value.system).toContain("citations.include_page_numbers=true");
    }
  });

  it("adds the concrete latest-user-language target before streaming starts", async () => {
    const root = await makeRoot("- Match the user's language.");
    const hook = makePolicyPromptBlockHook({
      policy: new PolicyKernel(new Workspace(root)),
    });

    const result = await hook.handler(
      {
        messages: [
          { role: "user", content: "이전 질문입니다." },
          { role: "assistant", content: "이전 답변입니다." },
          { role: "user", content: "Please calculate 1 + 1." },
        ],
        tools: [],
        system: "base system",
        iteration: 0,
      },
      makeCtx(),
    );

    expect(result?.action).toBe("replace");
    if (result?.action === "replace") {
      expect(result.value.system).toContain("response.language=auto");
      expect(result.value.system).toContain("response.target_language=en");
      expect(result.value.system).toContain("streamed progress");
      expect(result.value.system).toContain("latest user message is English");
    }
  });

  it("does not inject on follow-up iterations", async () => {
    const root = await makeRoot("- Always answer in Korean.");
    const hook = makePolicyPromptBlockHook({
      policy: new PolicyKernel(new Workspace(root)),
    });

    const result = await hook.handler(
      {
        messages: [{ role: "user", content: "안녕" }],
        tools: [],
        system: "base system",
        iteration: 1,
      },
      makeCtx(),
    );

    expect(result).toEqual({ action: "continue" });
  });
});
