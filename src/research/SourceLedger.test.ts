import { describe, expect, it } from "vitest";
import { SourceLedgerStore } from "./SourceLedger.js";

describe("SourceLedgerStore", () => {
  it("records inspected sources with stable ids and snapshots", () => {
    const ledger = new SourceLedgerStore({ now: () => 1234 });

    const first = ledger.recordSource({
      turnId: "turn-1",
      toolName: "WebFetch",
      kind: "web_fetch",
      uri: "https://example.com/docs",
      title: "Example Docs",
      contentHash: "sha256:abc",
      contentType: "text/html",
      trustTier: "unknown",
      snippets: ["Example snippet"],
    });
    const second = ledger.recordSource({
      turnId: "turn-1",
      toolName: "WebSearch",
      kind: "web_search",
      uri: "search:example docs",
    });

    expect(first.sourceId).toBe("src_1");
    expect(second.sourceId).toBe("src_2");
    expect(ledger.snapshot()).toEqual([
      {
        sourceId: "src_1",
        turnId: "turn-1",
        toolName: "WebFetch",
        kind: "web_fetch",
        uri: "https://example.com/docs",
        title: "Example Docs",
        contentHash: "sha256:abc",
        contentType: "text/html",
        trustTier: "unknown",
        snippets: ["Example snippet"],
        inspectedAt: 1234,
      },
      {
        sourceId: "src_2",
        turnId: "turn-1",
        toolName: "WebSearch",
        kind: "web_search",
        uri: "search:example docs",
        inspectedAt: 1234,
      },
    ]);
  });

  it("filters source records by turn", () => {
    const ledger = new SourceLedgerStore({ now: () => 1 });
    ledger.recordSource({
      turnId: "turn-a",
      toolName: "WebFetch",
      kind: "web_fetch",
      uri: "https://a.example",
    });
    ledger.recordSource({
      turnId: "turn-b",
      toolName: "WebFetch",
      kind: "web_fetch",
      uri: "https://b.example",
    });

    expect(ledger.sourcesForTurn("turn-b").map((record) => record.uri)).toEqual([
      "https://b.example",
    ]);
  });
});
