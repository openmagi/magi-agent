import { describe, expect, it } from "vitest";
import { ResearchContractStore } from "./ResearchContract.js";

describe("ResearchContractStore", () => {
  it("records classifier-provided source sensitivity", () => {
    const contract = new ResearchContractStore({ now: () => 123 });

    const turn = contract.startTurn({
      turnId: "turn-1",
      sourceSensitive: true,
      reason: "The classifier says this answer needs inspected-source citations.",
    });

    expect(turn).toMatchObject({
      turnId: "turn-1",
      sourceSensitive: true,
      requiredSourceKinds: ["web_search", "web_fetch"],
      reason: "The classifier says this answer needs inspected-source citations.",
      startedAt: 123,
    });
    expect(contract.turnFor("turn-1")).toMatchObject({
      turnId: "turn-1",
      sourceSensitive: true,
    });
  });

  it("does not require sources for ordinary local workflow replies", () => {
    const contract = new ResearchContractStore({ now: () => 1 });

    const turn = contract.startTurn({
      turnId: "turn-local",
      sourceSensitive: false,
    });

    expect(turn.sourceSensitive).toBe(false);
    expect(turn.requiredSourceKinds).toEqual([]);
  });

  it("defaults turns to non-source-sensitive without classifier input", () => {
    const contract = new ResearchContractStore({ now: () => 1 });

    const turn = contract.startTurn({
      turnId: "turn-ui-test",
    });

    expect(turn.sourceSensitive).toBe(false);
    expect(turn.requiredSourceKinds).toEqual([]);
  });

  it("records citation coverage claims with stable ids and snapshots", () => {
    const contract = new ResearchContractStore({ now: () => 500 });
    contract.startTurn({
      turnId: "turn-1",
      sourceSensitive: true,
    });

    const records = contract.recordCitationCoverage("turn-1", [
      {
        text: "The API supports streaming responses.",
        status: "covered",
        sourceIds: ["src_1"],
      },
      {
        text: "The SDK defaults to model X.",
        status: "missing",
        sourceIds: [],
      },
    ]);

    expect(records.map((record) => record.claimId)).toEqual(["claim_1", "claim_2"]);
    expect(contract.claimsForTurn("turn-1")).toEqual([
      {
        claimId: "claim_1",
        turnId: "turn-1",
        text: "The API supports streaming responses.",
        status: "covered",
        sourceIds: ["src_1"],
        recordedAt: 500,
      },
      {
        claimId: "claim_2",
        turnId: "turn-1",
        text: "The SDK defaults to model X.",
        status: "missing",
        sourceIds: [],
        recordedAt: 500,
      },
    ]);

    const snapshot = contract.snapshot();
    snapshot.claims[0]?.sourceIds.push("mutated");
    expect(contract.claimsForTurn("turn-1")[0]?.sourceIds).toEqual(["src_1"]);
  });
});
