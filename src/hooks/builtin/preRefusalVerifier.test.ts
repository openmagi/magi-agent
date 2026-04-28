import { describe, it, expect } from "vitest";
import { matchesRefusal, countInvestigationsThisTurn } from "./preRefusalVerifier.js";

describe("matchesRefusal (LLM-based)", () => {
  it("returns false for empty text", async () => {
    expect(await matchesRefusal("")).toBe(false);
  });

  it("returns false when no LLM context available (fail-open)", async () => {
    expect(await matchesRefusal("KB에 해당 정보가 없습니다")).toBe(false);
  });
});

describe("countInvestigationsThisTurn", () => {
  it("counts investigation tools in current turn only", () => {
    const transcript = [
      { kind: "tool_call", turnId: "t1", name: "Glob" },
      { kind: "tool_call", turnId: "t1", name: "Grep" },
      { kind: "tool_call", turnId: "t1", name: "SpawnAgent" },
      { kind: "tool_call", turnId: "t2", name: "FileRead" },
    ];
    expect(countInvestigationsThisTurn(transcript, "t1")).toBe(2);
    expect(countInvestigationsThisTurn(transcript, "t2")).toBe(1);
  });
});
