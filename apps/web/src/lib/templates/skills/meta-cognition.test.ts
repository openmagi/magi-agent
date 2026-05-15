import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const skillBody = readFileSync(new URL("./meta-cognition/SKILL.md", import.meta.url), "utf8");

describe("meta-cognition skill identity", () => {
  it("identifies the bot as an Open Magi Agent instead of an OpenClaw agent", () => {
    expect(skillBody).toContain("You are a **Open Magi Agent**");
    expect(skillBody).toContain("Open Magi Agent runtime");
    expect(skillBody).not.toMatch(/\bOpenClaw agent\b/i);
  });

  it("uses the current Open Magi positioning and plan surface", () => {
    expect(skillBody).toContain("keeps work from starting over");
    expect(skillBody).toContain("| FLEX | $499.99/mo |");
    expect(skillBody).not.toContain("The AI that knows your context");
    expect(skillBody).not.toContain("A smart intern");
  });

  it("describes local beta models without exposing the host name", () => {
    expect(skillBody).toContain("Gemma 4 Fast");
    expect(skillBody).toContain("Gemma 4 Max");
    expect(skillBody).toContain("Qwen 3.5 Uncensored");
    expect(skillBody).not.toContain("Mac Studio");
  });
});
