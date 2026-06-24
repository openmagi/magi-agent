import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const src = readFileSync(
  new URL("./nl-rule-compose.tsx", import.meta.url),
  "utf8",
);

describe("NlRuleCompose — F-UX6 interview-driven architect mode", () => {
  it("imports the InterviewMessage + ProposalCard chat-thread components", () => {
    expect(src).toContain("InterviewMessage");
    expect(src).toContain("ProposalCard");
  });

  it("imports the interview-mode types from customize-api", () => {
    expect(src).toContain("ArchitectProposal");
    expect(src).toContain("InterviewQuestion");
    expect(src).toContain("ConversationTurn");
  });

  it("tracks an architect chat thread state separate from the legacy result", () => {
    expect(src).toContain("ChatTurn");
    expect(src).toContain("setThread");
    expect(src).toContain("architectState");
  });

  it("dispatches mode='interview' on subsequent compile turns so the architect keeps the loop rolling", () => {
    expect(src).toContain('"interview"');
    expect(src).toContain("handleInterviewAnswer");
    expect(src).toContain("priorTurns");
  });

  it("classifies the compile response into interview / proposal / legacy branches", () => {
    expect(src).toContain('out.mode === "interview"');
    expect(src).toContain('out.mode === "proposal"');
  });

  it("activates a hybrid proposal by writing N rules sharing a generated groupId", () => {
    expect(src).toContain("newGroupId");
    expect(src).toContain("activatePrimitive");
    // Hybrid mode → fresh groupId; single → null (no group stamp).
    expect(src).toContain('proposal.mode === "hybrid"');
    expect(src).toContain("for (const primitive of proposal.primitives)");
  });

  it("stamps the groupId onto CustomRule-shaped primitives on activate", () => {
    expect(src).toContain("stampGroupId");
    expect(src).toContain("groupId");
  });

  it("offers an 'Author manually instead' affordance that drops to the wizard", () => {
    expect(src).toContain("onAuthorManually");
    expect(src).toContain("handleAuthorManually");
  });

  it("renders the chat thread above the question / proposal surfaces", () => {
    expect(src).toContain("ChatThread");
    expect(src).toContain("Architect chat thread");
  });

  it("rewrites the header copy from 'parser' to 'architect'", () => {
    expect(src).toContain("policy architect, not a sentence parser");
  });
});
