import { describe, expect, it } from "vitest";
import { compileAgentRulesPreview } from "./preview";

describe("compileAgentRulesPreview", () => {
  it("recognizes user rules that become runtime harness controls", () => {
    const preview = compileAgentRulesPreview(
      [
        "- 파일을 만들면 반드시 채팅에 첨부해줘.",
        "- 최종 답변 전에는 요구사항을 충족했는지 한 번 더 검사해.",
        "- 출처가 필요한 답변은 근거가 있는지 확인해.",
      ].join("\n"),
    );

    expect(preview.controls.map((item) => item.id)).toEqual([
      "user-harness:file-delivery-after-create",
      "user-harness:final-answer-verifier",
      "user-harness:source-grounding-verifier",
    ]);
    expect(preview.controls[0]).toMatchObject({
      kind: "harness",
      trigger: "beforeCommit",
      action: "Require FileDeliver",
      enforcement: "block_on_fail",
    });
  });

  it("recognizes native policy directives and keeps unknown rules advisory", () => {
    const preview = compileAgentRulesPreview(
      [
        "- Always answer in Korean.",
        "- Always cite sources with page numbers.",
        "- Be terse but warm.",
      ].join("\n"),
    );

    expect(preview.controls).toEqual([
      expect.objectContaining({
        id: "policy:response-language",
        kind: "policy",
        action: "Set response language",
        summary: "Respond in Korean.",
      }),
      expect.objectContaining({
        id: "policy:citations-page-numbers",
        kind: "policy",
        action: "Require source citations",
      }),
    ]);
    expect(preview.advisoryRules).toEqual(["Be terse but warm."]);
  });

  it("warns when later native policy directives override earlier ones", () => {
    const preview = compileAgentRulesPreview(
      [
        "- Always answer in Korean.",
        "- Always answer in English.",
      ].join("\n"),
    );

    expect(preview.controls).toContainEqual(
      expect.objectContaining({
        id: "policy:response-language",
        summary: "Respond in English.",
      }),
    );
    expect(preview.warnings).toContain(
      "Conflicting response language directives detected; keeping English.",
    );
  });

  it("recognizes every built-in safeguard preset without collapsing progress into concise replies", () => {
    const preview = compileAgentRulesPreview(
      [
        "- When you create a file or document, deliver it in chat before saying the task is complete.",
        "- Before the final answer, verify once more that every requested deliverable is satisfied.",
        "- For answers that need sources, verify source grounding before replying.",
        "- Before sending email, uploading files externally, making payments, or posting publicly, ask for confirmation.",
        "- For long-running work, provide brief progress updates and do not go silent until everything is done.",
      ].join("\n"),
    );

    expect(preview.controls.map((item) => item.id)).toEqual([
      "user-harness:file-delivery-after-create",
      "user-harness:final-answer-verifier",
      "user-harness:source-grounding-verifier",
      "user-harness:external-action-confirmation",
      "policy:progress-updates",
    ]);
    expect(preview.advisoryRules).toEqual([]);
    expect(preview.controls).not.toContainEqual(
      expect.objectContaining({ id: "policy:concise" }),
    );
  });
});
