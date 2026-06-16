import { describe, expect, it } from "vitest";
import type { ControlRequestRecord } from "./types";
import {
  controlQuestionText,
  firstNaturalAnswerControlQuestion,
  isNaturalAnswerControlQuestion,
} from "./control-questions";

const baseRequest: ControlRequestRecord = {
  requestId: "cr_1",
  kind: "user_question",
  state: "pending",
  sessionKey: "agent:main:app:general",
  channelName: "general",
  source: "system",
  prompt: "Which output format should I create?",
  createdAt: 1,
  expiresAt: Date.now() + 60_000,
};

describe("control question helpers", () => {
  it("treats ordinary user questions as natural chat answers", () => {
    expect(isNaturalAnswerControlQuestion(baseRequest)).toBe(true);
    expect(firstNaturalAnswerControlQuestion([baseRequest])).toBe(baseRequest);
  });

  it("keeps social-browser questions on the explicit control UI path", () => {
    const socialRequest: ControlRequestRecord = {
      ...baseRequest,
      proposedInput: {
        choices: [
          { id: "social_browser_connect_instagram", label: "Open Instagram" },
        ],
      },
    };

    expect(isNaturalAnswerControlQuestion(socialRequest)).toBe(false);
    expect(firstNaturalAnswerControlQuestion([socialRequest])).toBeNull();
  });

  it("renders choice labels as plain question text", () => {
    expect(
      controlQuestionText({
        ...baseRequest,
        proposedInput: {
          choices: [
            { id: "choice_1", label: "DOCX" },
            { id: "choice_2", label: "PDF" },
          ],
        },
      }),
    ).toBe("Which output format should I create?\n\n- DOCX\n- PDF");
  });
});
