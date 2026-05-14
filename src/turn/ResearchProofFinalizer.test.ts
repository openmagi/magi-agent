import { describe, expect, it } from "vitest";
import { SourceLedgerStore } from "../research/SourceLedger.js";
import { buildResearchProofFallback } from "./ResearchProofFinalizer.js";

describe("buildResearchProofFallback", () => {
  it("does not build a user-visible verifier fallback from source-ledger internals", () => {
    const sourceLedger = new SourceLedgerStore({ now: () => 10 });
    sourceLedger.recordSource({
      turnId: "turn-1",
      toolName: "WebSearch",
      kind: "web_search",
      uri: "search:latest tool pricing",
      title: "Web search: latest tool pricing",
      snippets: [
        '{"type":"search","query":{"original":"latest tool pricing"},"results":[{"url":"https://example.com"}]}',
      ],
    });
    sourceLedger.recordSource({
      turnId: "turn-1",
      toolName: "FileRead",
      kind: "file",
      uri: "file:workspace/secret-research-notes.md",
      title: "workspace/secret-research-notes.md",
      snippets: ["Internal workspace note that should never be echoed as a final answer."],
    });
    const fallback = buildResearchProofFallback({
      sources: sourceLedger.sourcesForTurn("turn-1"),
    });

    expect(fallback).toBeNull();
  });
});
