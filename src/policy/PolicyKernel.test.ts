import { afterEach, describe, expect, it } from "vitest";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { Workspace } from "../storage/Workspace.js";
import { PolicyKernel } from "./PolicyKernel.js";

const roots: string[] = [];

async function makeWorkspaceRoot(userRules?: string): Promise<string> {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), "policy-kernel-"));
  roots.push(root);
  if (userRules !== undefined) {
    await fs.writeFile(path.join(root, "USER-RULES.md"), userRules, "utf8");
  }
  return root;
}

afterEach(async () => {
  await Promise.all(
    roots.splice(0).map((root) => fs.rm(root, { recursive: true, force: true })),
  );
});

describe("PolicyKernel", () => {
  it("returns platform defaults even when user rules are absent", async () => {
    const root = await makeWorkspaceRoot();
    const kernel = new PolicyKernel(new Workspace(root));

    const snapshot = await kernel.current();

    expect(snapshot.policy.approval.explicitConsentForExternalActions).toBe(true);
    expect(snapshot.policy.verification.requireCompletionEvidence).toBe(true);
    expect(snapshot.policy.delivery.requireDeliveredArtifactsBeforeCompletion).toBe(true);
    expect(snapshot.policy.async.requireRealNotificationMechanism).toBe(true);
    expect(snapshot.policy.retry.retryTransientToolFailures).toBe(true);
    expect(snapshot.status.executableDirectives).toContain(
      "approval.explicit_consent_for_external_actions=true",
    );
    expect(snapshot.status.advisoryDirectives).toEqual([]);
    expect(snapshot.status.warnings).toEqual([]);
  });

  it("parses a conservative executable subset from user rules", async () => {
    const root = await makeWorkspaceRoot(
      [
        "- Always answer in Korean.",
        "- Include page numbers when citing sources.",
        "- No profanity.",
        "- Be concise.",
      ].join("\n"),
    );
    const kernel = new PolicyKernel(new Workspace(root));

    const snapshot = await kernel.current();

    expect(snapshot.policy.responseMode.language).toBe("ko");
    expect(snapshot.policy.citations.requireSources).toBe(true);
    expect(snapshot.policy.citations.includePageNumbers).toBe(true);
    expect(snapshot.policy.responseMode.noProfanity).toBe(true);
    expect(snapshot.policy.responseMode.concise).toBe(true);
    expect(snapshot.status.userDirectives).toEqual([
      "response.language=ko",
      "citations.require_sources=true",
      "citations.include_page_numbers=true",
      "response.no_profanity=true",
      "response.concise=true",
    ]);
  });

  it("compiles recognized operational user rules into typed harness rules", async () => {
    const root = await makeWorkspaceRoot(
      [
        "- 파일을 만들면 반드시 채팅에 첨부해줘.",
        "- 최종 답변 전에는 요구사항을 충족했는지 한 번 더 검사해.",
        "- 출처가 필요한 답변은 근거가 있는지 확인해.",
      ].join("\n"),
    );
    const kernel = new PolicyKernel(new Workspace(root));

    const snapshot = await kernel.current();

    expect(snapshot.policy.harnessRules).toEqual([
      expect.objectContaining({
        id: "user-harness:file-delivery-after-create",
        trigger: "beforeCommit",
        enforcement: "block_on_fail",
        action: { type: "require_tool", toolName: "FileDeliver" },
      }),
      expect.objectContaining({
        id: "user-harness:final-answer-verifier",
        trigger: "beforeCommit",
        enforcement: "block_on_fail",
        action: expect.objectContaining({ type: "llm_verifier" }),
      }),
      expect.objectContaining({
        id: "user-harness:source-grounding-verifier",
        trigger: "beforeCommit",
        enforcement: "block_on_fail",
        action: expect.objectContaining({ type: "llm_verifier" }),
      }),
    ]);
    expect(snapshot.status.harnessDirectives).toContain(
      "user-harness:file-delivery-after-create beforeCommit require_tool FileDeliver block_on_fail",
    );
    expect(snapshot.status.harnessDirectives).toContain(
      "user-harness:final-answer-verifier beforeCommit llm_verifier block_on_fail",
    );
    expect(snapshot.status.harnessDirectives).toContain(
      "user-harness:source-grounding-verifier beforeCommit llm_verifier block_on_fail",
    );
    expect(snapshot.status.advisoryDirectives).toEqual([]);
  });

  it("loads structured harness rules from USER-HARNESS-RULES.md", async () => {
    const root = await makeWorkspaceRoot();
    await fs.writeFile(
      path.join(root, "USER-HARNESS-RULES.md"),
      [
        "---",
        "id: user-harness:file-delivery-after-create",
        "trigger: beforeCommit",
        "condition:",
        "  anyToolUsed:",
        "    - DocumentWrite",
        "    - SpreadsheetWrite",
        "action:",
        "  type: require_tool",
        "  toolName: FileDeliver",
        "enforcement: block_on_fail",
        "timeoutMs: 2000",
        "---",
        "",
        "When a document or spreadsheet is created, deliver it to the chat before claiming completion.",
      ].join("\n"),
      "utf8",
    );
    const kernel = new PolicyKernel(new Workspace(root));

    const snapshot = await kernel.current();

    expect(snapshot.policy.harnessRules).toEqual([
      expect.objectContaining({
        id: "user-harness:file-delivery-after-create",
        sourceText: expect.stringContaining("deliver it to the chat"),
        condition: { anyToolUsed: ["DocumentWrite", "SpreadsheetWrite"] },
        action: { type: "require_tool", toolName: "FileDeliver" },
      }),
    ]);
  });

  it("loads downloaded harness rule packs from harness-rules/*.md", async () => {
    const root = await makeWorkspaceRoot();
    await fs.mkdir(path.join(root, "harness-rules"), { recursive: true });
    await fs.writeFile(
      path.join(root, "harness-rules", "final-answer-check.md"),
      [
        "---",
        "id: user-harness:final-answer-verifier",
        "trigger: beforeCommit",
        "action:",
        "  type: llm_verifier",
        "enforcement: block_on_fail",
        "timeoutMs: 8000",
        "---",
        "",
        "Check whether the assistant's final answer satisfies the user's request and does not skip requested deliverables.",
      ].join("\n"),
      "utf8",
    );
    const kernel = new PolicyKernel(new Workspace(root));

    const snapshot = await kernel.current();

    expect(snapshot.policy.harnessRules).toEqual([
      expect.objectContaining({
        id: "user-harness:final-answer-verifier",
        action: expect.objectContaining({
          type: "llm_verifier",
          prompt: expect.stringContaining("does not skip requested deliverables"),
        }),
      }),
    ]);
  });

  it("keeps unknown lines advisory and warns on conflicting language directives", async () => {
    const root = await makeWorkspaceRoot(
      [
        "- Always answer in Korean.",
        "- Always answer in English.",
        "- Use a witty tone.",
      ].join("\n"),
    );
    const kernel = new PolicyKernel(new Workspace(root));

    const snapshot = await kernel.current();

    expect(snapshot.policy.responseMode.language).toBe("en");
    expect(snapshot.status.advisoryDirectives).toEqual(["Use a witty tone."]);
    expect(snapshot.status.warnings).toContain(
      "conflicting response.language directives detected; keeping response.language=en",
    );
  });
});
