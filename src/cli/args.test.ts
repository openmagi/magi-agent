import { describe, expect, it } from "vitest";
import { parseCliArgs } from "./args.js";

describe("parseCliArgs", () => {
  it("treats chat as the interactive CLI command", () => {
    expect(parseCliArgs(["chat"])).toEqual({ command: "chat" });
  });

  it("keeps start as a backwards-compatible chat alias", () => {
    expect(parseCliArgs(["start"])).toEqual({ command: "chat" });
  });

  it("parses one-shot run prompts and runtime options", () => {
    expect(
      parseCliArgs([
        "run",
        "--session",
        "research",
        "--model",
        "llama3.1",
        "--plan",
        "summarize",
        "workspace/knowledge",
      ]),
    ).toEqual({
      command: "run",
      prompt: "summarize workspace/knowledge",
      sessionKey: "research",
      model: "llama3.1",
      planMode: true,
    });
  });

  it("supports serve --port=8081", () => {
    expect(parseCliArgs(["serve", "--port=8081"])).toEqual({
      command: "serve",
      port: 8081,
    });
  });

  it("rejects invalid ports with a usage error", () => {
    expect(() => parseCliArgs(["serve", "--port", "99999"])).toThrow(
      /invalid port/,
    );
  });
});
