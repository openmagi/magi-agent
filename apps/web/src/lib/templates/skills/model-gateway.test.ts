import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const skillBody = readFileSync(new URL("./model-gateway/SKILL.md", import.meta.url), "utf8");
const agentsBody = readFileSync(new URL("../static/AGENTS.md", import.meta.url), "utf8");
const toolsBody = readFileSync(new URL("../static/TOOLS.md", import.meta.url), "utf8");

describe("model-gateway skill model labels", () => {
  it("describes local beta models with versions and without host details", () => {
    expect(skillBody).toContain("Gemma 4 Fast");
    expect(skillBody).toContain("Gemma 4 Max");
    expect(skillBody).toContain("Qwen 3.5 Uncensored");
    expect(skillBody).not.toContain("Mac Studio");
  });

  it("documents native SpawnAgent model selection as a first-class runtime manual", () => {
    expect(agentsBody).toContain("Native SpawnAgent");
    expect(agentsBody).toContain("Use the `model` enum from the `SpawnAgent` tool schema");
    expect(agentsBody).toContain("Omit `model` to inherit your current configured runtime model");
    expect(toolsBody).toContain("`SpawnAgent`");
    expect(toolsBody).toContain("Do not invent model IDs");
  });
});
