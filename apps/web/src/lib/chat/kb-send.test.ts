import { describe, expect, it } from "vitest";
import { buildMessageContentWithKbContext, mergeKbDocReferences } from "./kb-send";
import type { KbDocReference } from "./types";

const DOC_A: KbDocReference = {
  id: "doc-a",
  filename: "budget.xlsx",
  collectionId: "col-1",
  collectionName: "Downloads",
};

const DOC_B: KbDocReference = {
  id: "doc-b",
  filename: "photo.png",
  collectionId: "col-1",
  collectionName: "Downloads",
  mimeType: "image/png",
  source: "chat_upload",
};

describe("mergeKbDocReferences", () => {
  it("dedupes by doc id while preserving first-seen order", () => {
    expect(mergeKbDocReferences([DOC_A, DOC_B], [DOC_B, DOC_A])).toEqual([
      DOC_A,
      DOC_B,
    ]);
  });
});

describe("buildMessageContentWithKbContext", () => {
  it("prepends a KB_CONTEXT marker when refs exist", () => {
    expect(buildMessageContentWithKbContext("이거 봐줘", [DOC_A, DOC_B])).toBe(
      "[KB_CONTEXT: doc-a=budget.xlsx, doc-b=photo.png]\n이거 봐줘",
    );
  });

  it("returns only the marker when the body is empty", () => {
    expect(buildMessageContentWithKbContext("   ", [DOC_A])).toBe(
      "[KB_CONTEXT: doc-a=budget.xlsx]",
    );
  });
});
