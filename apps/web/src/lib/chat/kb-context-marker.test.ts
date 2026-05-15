import { describe, expect, it } from "vitest";
import { buildKbContextMarker, parseKbContextMarker } from "./kb-context-marker";

describe("kb-context-marker", () => {
  it("builds and strips a KB_CONTEXT prefix from message content", () => {
    const marker = buildKbContextMarker([
      { id: "doc-1", filename: "budget.xlsx" },
      { id: "doc-2", filename: "notes.pdf" },
    ]);

    const parsed = parseKbContextMarker(`${marker}\n이거 봐줘`);

    expect(parsed.refs).toEqual([
      { id: "doc-1", filename: "budget.xlsx" },
      { id: "doc-2", filename: "notes.pdf" },
    ]);
    expect(parsed.text).toBe("이거 봐줘");
  });
});
