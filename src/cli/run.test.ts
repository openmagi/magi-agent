import { describe, expect, it } from "vitest";
import { resolveRunPrompt } from "./run.js";

describe("resolveRunPrompt", () => {
  it("uses the command-line prompt when supplied", () => {
    expect(resolveRunPrompt("write a release note", "ignored stdin")).toBe(
      "write a release note",
    );
  });

  it("uses piped stdin when no command-line prompt is supplied", () => {
    expect(resolveRunPrompt(undefined, "summarize this file\n")).toBe(
      "summarize this file",
    );
  });

  it("requires either an argv prompt or stdin", () => {
    expect(() => resolveRunPrompt(undefined, "  \n")).toThrow(/No prompt/);
  });
});
