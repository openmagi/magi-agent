import { describe, expect, it } from "vitest";
import {
  findLatestAssistantServerMessage,
  shouldPatchAssistantTextFromServer,
} from "./server-reconcile";
import type { ServerMessage } from "./types";

describe("server chat reconciliation", () => {
  it("patches a streamed assistant message when the local text contains UTF-8 replacement characters", () => {
    expect(
      shouldPatchAssistantTextFromServer(
        "저는 존\uFFFD\uFFFD\uFFFD하지 않는 상태예요.",
        "저는 존재하지 않는 상태예요.",
      ),
    ).toBe(true);
  });

  it("patches a streamed assistant message when server history has the completed tail", () => {
    expect(
      shouldPatchAssistantTextFromServer(
        "This streamed response stopped early.",
        "This streamed response stopped early, then the committed history included the missing final sentence.",
      ),
    ).toBe(true);
  });

  it("does not patch clean streamed text with a longer server copy that only adds inline progress noise", () => {
    const cleanAnswer = [
      "재전송 완료했습니다.",
      "",
      "확인해보세요.",
      "",
      "[attachment:att-pdf:portfolio-review-report-2026-05-19.pdf]",
    ].join("\n");
    const noisyServerCopy = [
      cleanAnswer,
      "",
      "Thinking through next step 요청 처리 중",
      "공개 진행 로그를 갱신하고 있습니다",
      "Reviewing PDF document outputs/portfolio-review-report-2026-05-19.pdf 17ms",
      "Calling anthropic/claude-opus-4-6",
      "",
      "재전송 완료했습니다.[META: i[META: intent=실행, domain=문서전달, complexity=단순, route=직접]",
      "",
      "재전송 완료했습니다.",
      "",
      "확인해보세요.",
      "",
      "[attachment:att-pdf:portfolio-review-report-2026-05-19.pdf]",
    ].join("\n");

    expect(
      shouldPatchAssistantTextFromServer(cleanAnswer, noisyServerCopy),
    ).toBe(false);
  });

  it("does not patch unrelated short differences without replacement characters", () => {
    expect(
      shouldPatchAssistantTextFromServer("OK", "Done"),
    ).toBe(false);
  });

  it("finds the latest assistant message from ordered server history", () => {
    const messages: ServerMessage[] = [
      {
        id: "system-1",
        role: "system",
        content: "status",
        created_at: "2026-05-09T07:28:00.000Z",
      },
      {
        id: "assistant-1",
        role: "assistant",
        content: "first",
        created_at: "2026-05-09T07:29:00.000Z",
      },
      {
        id: "assistant-2",
        role: "assistant",
        content: "latest",
        created_at: "2026-05-09T07:30:00.000Z",
      },
    ];

    expect(findLatestAssistantServerMessage(messages)?.id).toBe("assistant-2");
  });
});
