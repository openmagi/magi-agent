import { describe, it, expect } from "vitest";
import { generateSkillMd } from "./skill-md-generator";
import type { SkillMdInput } from "./skill-md-generator";

describe("generateSkillMd", () => {
  const baseInput: SkillMdInput = {
    botName: "TestBot",
    botPurpose: "A test bot for unit testing",
    botId: "abc-123",
    walletAddress: "0x1234567890abcdef1234567890abcdef12345678",
    registryAgentId: "42",
  };

  it("should include bot name as heading", () => {
    const result = generateSkillMd(baseInput);
    expect(result).toContain("# TestBot");
  });

  it("should include bot purpose", () => {
    const result = generateSkillMd(baseInput);
    expect(result).toContain("A test bot for unit testing");
  });

  it("should include chat completions endpoint with botId", () => {
    const result = generateSkillMd(baseInput);
    expect(result).toContain("### POST /v1/chat/abc-123/completions");
  });

  it("should include wallet address", () => {
    const result = generateSkillMd(baseInput);
    expect(result).toContain("- Wallet: 0x1234567890abcdef1234567890abcdef12345678");
  });

  it("should include Base chain", () => {
    const result = generateSkillMd(baseInput);
    expect(result).toContain("- Chain: Base (8453)");
  });

  it("should include registry agent ID", () => {
    const result = generateSkillMd(baseInput);
    expect(result).toContain("- Registry: ERC-8004 #42");
  });

  it("should use default purpose when botPurpose is null", () => {
    const result = generateSkillMd({ ...baseInput, botPurpose: null });
    expect(result).toContain("AI agent deployed on openmagi.ai");
  });

  it("should show not provisioned when wallet is null", () => {
    const result = generateSkillMd({ ...baseInput, walletAddress: null });
    expect(result).toContain("- Wallet: (not provisioned)");
  });

  it("should show not registered when registryAgentId is null", () => {
    const result = generateSkillMd({ ...baseInput, registryAgentId: null });
    expect(result).toContain("- Registry: (not registered)");
  });

  it("should include openmagi.ai platform reference", () => {
    const result = generateSkillMd(baseInput);
    expect(result).toContain("openmagi.ai");
  });
});
