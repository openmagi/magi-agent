import { describe, it, expect } from "vitest";
import { matchesDeferral, countWorkToolsThisTurn } from "./deferralBlocker.js";

describe("matchesDeferral (LLM-based)", () => {
  it("returns false for empty text", async () => {
    expect(await matchesDeferral("")).toBe(false);
  });

  it("returns false when no LLM context available (fail-open)", async () => {
    expect(await matchesDeferral("완료되면 결과 보내드리겠습니다")).toBe(false);
  });
});

describe("countWorkToolsThisTurn", () => {
  it("counts only WORK_TOOLS in the current turn", () => {
    const transcript = [
      { kind: "tool_call", turnId: "t1", name: "Bash" },
      { kind: "tool_call", turnId: "t1", name: "FileRead" },
      { kind: "tool_call", turnId: "t1", name: "SpawnAgent" },
      { kind: "tool_call", turnId: "t2", name: "Bash" },
    ];
    expect(countWorkToolsThisTurn(transcript, "t1")).toBe(2);
    expect(countWorkToolsThisTurn(transcript, "t2")).toBe(1);
  });
});
