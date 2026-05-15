import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import {
  AgentRulesSection,
  appendUniqueAgentRule,
  buildCustomSafeguardRule,
  hasAgentRule,
  removeAgentRule,
} from "./agent-rules-section";
import { compileAgentRulesPreview } from "@/lib/agent-harness/preview";

vi.mock("@/hooks/use-auth-fetch", () => ({
  useAuthFetch: () => vi.fn(),
}));

vi.mock("@/lib/i18n", () => ({
  useMessages: () => ({
    settingsPage: {},
  }),
}));

describe("AgentRulesSection", () => {
  it("shows a user-facing safeguard builder before the raw rule editor", () => {
    const html = renderToStaticMarkup(
      <AgentRulesSection botId="bot-1" initialRules={null} />,
    );

    expect(html).toContain("Safeguard library");
    expect(html).toContain("Add file delivery check");
    expect(html).toContain("Add final answer check");
    expect(html).toContain("Edit rules directly");
    expect(html.indexOf("Safeguard library")).toBeLessThan(
      html.indexOf("Edit rules directly"),
    );
  });

  it("previews active safeguards with user-facing language", () => {
    const html = renderToStaticMarkup(
      <AgentRulesSection
        botId="bot-1"
        initialRules="- 파일을 만들면 반드시 채팅에 첨부해줘."
      />,
    );

    expect(html).toContain("Active safeguards");
    expect(html).toContain("File attachment check");
    expect(html).toContain("Blocks completion until the created file is delivered in chat.");
    expect(html).toContain("Show technical details");
    expect(html).not.toContain("Runtime harness preview");
  });

  it("shows all built-in preset safeguards in the active list", () => {
    const html = renderToStaticMarkup(
      <AgentRulesSection
        botId="bot-1"
        initialRules={[
          "- When you create a file or document, deliver it in chat before saying the task is complete.",
          "- Before the final answer, verify once more that every requested deliverable is satisfied.",
          "- For answers that need sources, verify source grounding before replying.",
          "- Before sending email, uploading files externally, making payments, or posting publicly, ask for confirmation.",
          "- For long-running work, provide brief progress updates and do not go silent until everything is done.",
        ].join("\n")}
      />,
    );

    expect(html).toContain("File attachment check");
    expect(html).toContain("Final answer check");
    expect(html).toContain("Source grounding check");
    expect(html).toContain("External action confirmation");
    expect(html).toContain("Progress updates");
    expect(html).not.toContain("Concise responses");
  });

  it("shows advisory prompt rules separately from native controls", () => {
    const html = renderToStaticMarkup(
      <AgentRulesSection botId="bot-1" initialRules="- Use a witty tone." />,
    );

    expect(html).toContain("Saved as prompt rules");
    expect(html).toContain("Use a witty tone.");
  });

  it("offers a modal launcher for custom hooks and checkpoints", () => {
    const html = renderToStaticMarkup(
      <AgentRulesSection botId="bot-1" initialRules={null} />,
    );

    expect(html).toContain("Custom safeguard builder");
    expect(html).toContain("Create custom safeguard");
  });

  it("does not expose runtime hook jargon in the default custom builder", () => {
    const html = renderToStaticMarkup(
      <AgentRulesSection botId="bot-1" initialRules={null} />,
    );

    expect(html).not.toContain("beforeCommit checkpoint");
    expect(html).not.toContain("pre-tool hook");
    expect(html).not.toContain("block_on_fail");
  });

  it("does not expose native selects on the default safeguards page", () => {
    const html = renderToStaticMarkup(
      <AgentRulesSection botId="bot-1" initialRules={null} />,
    );

    expect(html).not.toContain("<select");
  });

  it("marks already-added preset safeguards and keeps applied state near the library", () => {
    const html = renderToStaticMarkup(
      <AgentRulesSection
        botId="bot-1"
        initialRules="- When you create a file or document, deliver it in chat before saying the task is complete."
      />,
    );

    expect(html).toContain("Remove");
    expect(html.indexOf("Custom safeguard builder")).toBeLessThan(
      html.indexOf("Active safeguards"),
    );
    expect(html.indexOf("Active safeguards")).toBeLessThan(
      html.indexOf("Edit rules directly"),
    );
  });

  it("keeps the custom builder collapsed behind a modal launcher by default", () => {
    const html = renderToStaticMarkup(
      <AgentRulesSection botId="bot-1" initialRules={null} />,
    );

    expect(html).toContain("Create custom safeguard");
    expect(html).not.toContain("When should it run?");
    expect(html).not.toContain("Target or condition");
  });

  it("links users to the skills page for custom skill installation", () => {
    const html = renderToStaticMarkup(
      <AgentRulesSection botId="bot-1" initialRules={null} />,
    );

    expect(html).toContain("Custom skills");
    expect(html).toContain("/dashboard/bot-1/skills");
  });

  it("deduplicates agent rules regardless of bullet formatting", () => {
    const fileRule =
      "When you create a file or document, deliver it in chat before saying the task is complete.";
    const existing = `  - ${fileRule}\n- Use a witty tone.`;

    expect(hasAgentRule(existing, fileRule)).toBe(true);
    expect(appendUniqueAgentRule(existing, fileRule)).toBe(existing);
    expect(appendUniqueAgentRule("", fileRule)).toBe(`- ${fileRule}`);
  });

  it("removes preset agent rules so already-added cards can be toggled off", () => {
    const fileRule =
      "When you create a file or document, deliver it in chat before saying the task is complete.";
    const existing = [
      `  - ${fileRule}`,
      "- Use a witty tone.",
      "- Before the final answer, verify once more that every requested deliverable is satisfied.",
    ].join("\n");

    expect(removeAgentRule(existing, fileRule)).toBe(
      [
        "- Use a witty tone.",
        "- Before the final answer, verify once more that every requested deliverable is satisfied.",
      ].join("\n"),
    );
    expect(removeAgentRule(`- ${fileRule}`, fileRule)).toBe("");
  });

  it("builds readable custom safeguard rules from hook parts", () => {
    const deliverableRule = buildCustomSafeguardRule({
      trigger: "beforeCommit",
      action: "verifyDeliverables",
      enforcement: "blockAndRetry",
      target: "requested report and attachments",
    });
    const sourceRule = buildCustomSafeguardRule({
      trigger: "afterToolUse",
      action: "verifySources",
      enforcement: "askUser",
      target: "web search results",
    });

    expect(deliverableRule).toBe(
      "Before the final answer, verify requested report and attachments. If the check fails, block completion and retry the missing work.",
    );
    expect(sourceRule).toBe(
      "After each tool call, verify source grounding for web search results. If the check fails, ask the user before continuing.",
    );
    expect(
      compileAgentRulesPreview(`- ${deliverableRule}`).controls[0]?.id,
    ).toBe("user-harness:final-answer-verifier");
    expect(
      compileAgentRulesPreview(`- ${sourceRule}`).controls[0]?.id,
    ).toBe("user-harness:source-grounding-verifier");
    expect(
      compileAgentRulesPreview(
        `- ${buildCustomSafeguardRule({
          trigger: "afterFileCreate",
          action: "requireFileDelivery",
          enforcement: "blockAndRetry",
          target: "",
        })}`,
      ).controls[0]?.id,
    ).toBe("user-harness:file-delivery-after-create");
  });
});
