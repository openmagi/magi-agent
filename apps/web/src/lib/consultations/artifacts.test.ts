import { describe, expect, it } from "vitest";

import {
  buildConsultationMemoMarkdown,
  buildConsultationTasksJson,
  buildTranscriptMarkdown,
} from "./artifacts";

describe("consultation artifacts", () => {
  it("builds a timestamped speaker transcript markdown", () => {
    const markdown = buildTranscriptMarkdown({
      sourceFilename: "client-call.m4a",
      processedAt: "2026-04-26T12:00:00.000Z",
      backend: "mock_fixture",
      durationSeconds: 75,
      warnings: ["low confidence around 00:00:30"],
      segments: [
        { speaker: "Speaker 1", startSeconds: 3, endSeconds: 10, text: "안녕하세요." },
        { speaker: "Speaker 2", startSeconds: 17, endSeconds: 24, text: "자료를 보내드리겠습니다." },
      ],
    });

    expect(markdown).toContain("# Consultation Transcript");
    expect(markdown).toContain("- Source file: client-call.m4a");
    expect(markdown).toContain("- Duration: 1m 15s");
    expect(markdown).toContain("- low confidence around 00:00:30");
    expect(markdown).toContain("[00:00:03-00:00:10] Speaker 1: 안녕하세요.");
    expect(markdown).toContain("[00:00:17-00:00:24] Speaker 2: 자료를 보내드리겠습니다.");
  });

  it("builds a consultation memo with vertical hint", () => {
    const markdown = buildConsultationMemoMarkdown({
      sourceFilename: "tax-call.mp3",
      verticalHint: "accounting",
      generatedAt: "2026-04-26T12:10:00.000Z",
      summary: ["부가세 신고 자료 확인이 필요합니다."],
      keyIssues: ["매출 누락 가능성"],
      clientRequests: ["필요자료 목록 요청"],
      neededMaterials: ["카드 매출 내역"],
      followUpQuestions: ["면세 매출이 있는지 확인"],
      nextActions: ["고객에게 자료 요청"],
      deadlinesAndDates: ["2026-04-30 신고 마감"],
      risksAndCaveats: ["AI 초안이며 전문가 검토 필요"],
      sourceNotes: ["00:02:10 고객이 누락 가능성을 언급"],
    });

    expect(markdown).toContain("# Consultation Memo");
    expect(markdown).toContain("- Source file: tax-call.mp3");
    expect(markdown).toContain("- Vertical hint: accounting");
    expect(markdown).toContain("## Needed Materials");
    expect(markdown).toContain("- 카드 매출 내역");
    expect(markdown).toContain("AI draft");
  });

  it("builds structured follow-up tasks", () => {
    const tasks = buildConsultationTasksJson([
      {
        title: "Request bank statements",
        ownerHint: "user",
        dueDate: null,
        sourceTimestamp: "00:17:42",
        confidence: "medium",
      },
    ]);

    expect(tasks).toEqual({
      tasks: [
        {
          title: "Request bank statements",
          owner_hint: "user",
          due_date: null,
          source_timestamp: "00:17:42",
          confidence: "medium",
        },
      ],
    });
  });
});
