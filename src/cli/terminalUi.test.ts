import { describe, expect, it } from "vitest";
import { renderCliHelp, renderCliWelcome, stripAnsi } from "./terminalUi.js";

describe("terminal UI chrome", () => {
  it("renders a Claude Code-style welcome card with runtime context", () => {
    const output = stripAnsi(
      renderCliWelcome({
        agentName: "Magi",
        provider: "openai-compatible",
        model: "llama3.1",
        workspaceRoot: "/tmp/magi/workspace",
        sessionKey: "agent:local:cli:interactive",
      }),
    );

    expect(output).toContain("Welcome to Magi");
    expect(output).toContain("openai-compatible/llama3.1");
    expect(output).toContain("/tmp/magi/workspace");
    expect(output).toContain("agent:local:cli:interactive");
    expect(output).toContain("/help");
  });

  it("documents local CLI slash commands", () => {
    const output = stripAnsi(renderCliHelp());

    expect(output).toContain("/help");
    expect(output).toContain("/exit");
    expect(output).toContain("/compact");
    expect(output).toContain("runtime slash commands");
  });
});
