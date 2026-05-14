import { describe, expect, it } from "vitest";
import { buildMessageCopyText } from "./message-copy";

describe("message copy text", () => {
  it("strips hidden KB context metadata from full-message copies", () => {
    const text = buildMessageCopyText({
      content:
        "[KB_CONTEXT: 56fdc3c1-7da2-47c4-a87a-83d35beced19=example_notes.md]\n" +
        "Please summarize the attached project notes.",
      selection: "",
    });

    expect(text).toBe("Please summarize the attached project notes.");
    expect(text).not.toContain("KB_CONTEXT");
    expect(text).not.toContain("example_notes.md");
  });

  it("strips hidden attachment markers from full-message copies", () => {
    const text = buildMessageCopyText({
      content:
        "완료했습니다.\n" +
        "[attachment:00000000-0000-4000-8000-000000000001:report.md]",
      selection: "",
    });

    expect(text).toBe("완료했습니다.");
  });

  it("keeps explicit selected text instead of normalizing the whole message", () => {
    const text = buildMessageCopyText({
      content: "[KB_CONTEXT: doc-1=notes.md]\n원문 전체",
      selection: "선택한 일부",
    });

    expect(text).toBe("선택한 일부");
  });
});
