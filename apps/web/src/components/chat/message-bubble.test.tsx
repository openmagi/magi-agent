import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { MessageBubble } from "./message-bubble";

describe("MessageBubble", () => {
  it("hides KB_CONTEXT markers and renders KB file chips for user messages", () => {
    const html = renderToStaticMarkup(
      <MessageBubble
        role="user"
        botId="bot-1"
        content={"[KB_CONTEXT: doc-1=budget.xlsx, doc-2=notes.pdf]\n이거 봐줘"}
        timestamp={1_800_000_000_000}
      />,
    );

    expect(html).toContain("budget.xlsx");
    expect(html).toContain("notes.pdf");
    expect(html).toContain("이거 봐줘");
    expect(html).not.toContain("[KB_CONTEXT:");
  });

  it("renders duplicate attachment markers for the same file only once", () => {
    const html = renderToStaticMarkup(
      <MessageBubble
        role="assistant"
        botId="bot-1"
        content={[
          "완료했습니다.",
          "[attachment:00000000-0000-4000-8000-000000000001:duolingo_ic_memo_v2.docx]",
          "[attachment:00000000-0000-4000-8000-000000000002:duolingo_ic_memo_v2.pdf]",
          "[attachment:00000000-0000-4000-8000-000000000001:duolingo_ic_memo_v2.docx]",
          "[attachment:00000000-0000-4000-8000-000000000002:duolingo_ic_memo_v2.pdf]",
        ].join("\n")}
        timestamp={1_800_000_000_000}
      />,
    );

    expect(html.match(/duolingo_ic_memo_v2\.docx/g) ?? []).toHaveLength(1);
    expect(html.match(/duolingo_ic_memo_v2\.pdf/g) ?? []).toHaveLength(1);
    expect(html).not.toContain("[attachment:");
  });

  it("renders 5 separate buttons for 5 attachments with the same filename", () => {
    const ids = [
      "00000000-0000-4000-8000-000000000001",
      "00000000-0000-4000-8000-000000000002",
      "00000000-0000-4000-8000-000000000003",
      "00000000-0000-4000-8000-000000000004",
      "00000000-0000-4000-8000-000000000005",
    ];
    const markers = ids.map((id) => `[attachment:${id}:REPORT.pdf]`).join("\n");
    const html = renderToStaticMarkup(
      <MessageBubble
        role="assistant"
        botId="bot-1"
        content={`리포트 5개 전달합니다.\n${markers}`}
        timestamp={1_800_000_000_000}
      />,
    );

    expect(html.match(/REPORT\.pdf/g) ?? []).toHaveLength(5);
    expect(html).not.toContain("[attachment:");
  });

  it("hides a leading route meta preamble from assistant history rows", () => {
    const html = renderToStaticMarkup(
      <MessageBubble
        role="assistant"
        content={
          "[META: intent=실행, domain=문서, complexity=단순, route=직접]\n\n" +
          "벤처 리포트 v2 재전송했어."
        }
        timestamp={1_800_000_000_000}
      />,
    );

    expect(html).toContain("벤처 리포트 v2 재전송했어.");
    expect(html).not.toContain("[META:");
  });

  it("renders compact token and cost usage under completed assistant messages", () => {
    const html = renderToStaticMarkup(
      <MessageBubble
        role="assistant"
        content="완료했습니다."
        timestamp={1_800_000_000_000}
        usage={{
          inputTokens: 1234,
          outputTokens: 56,
          costUsd: 0.0123,
        }}
      />,
    );

    expect(html).toContain("1,290 tokens");
    expect(html).toContain("1,234 in / 56 out");
    expect(html).toContain("$0.0123");
  });

  it("renders image attachments as expandable image buttons", () => {
    const html = renderToStaticMarkup(
      <MessageBubble
        role="assistant"
        botId="bot-1"
        content={[
          "확인했습니다.",
          "[attachment:00000000-0000-4000-8000-000000000003:google_screenshot.png]",
        ].join("\n")}
        timestamp={1_800_000_000_000}
      />,
    );

    expect(html).toContain('type="button"');
    expect(html).toContain('aria-label="Open image google_screenshot.png"');
    expect(html).toContain("google_screenshot.png");
    expect(html).not.toContain("[attachment:");
  });

  it("does not show persisted assistant activity as still in progress", () => {
    const html = renderToStaticMarkup(
      <MessageBubble
        role="assistant"
        botId="bot-1"
        content="완료했습니다."
        timestamp={1_800_000_000_000}
        activities={[
          {
            id: "tool-1",
            label: "DocumentWrite",
            status: "running",
            startedAt: 1_800_000_000_000,
          },
          {
            id: "tool-2",
            label: "FileDeliver",
            status: "done",
            startedAt: 1_800_000_001_000,
          },
        ]}
        taskBoard={{
          receivedAt: 1_800_000_002_000,
          tasks: [
            {
              id: "task-1",
              title: "Create files",
              description: "",
              status: "in_progress",
            },
          ],
        }}
      />,
    );

    expect(html).toContain("Ran 3 actions");
    expect(html).not.toContain("actions in progress");
    expect(html).not.toContain("Running DocumentWrite");
    expect(html).not.toContain("aria-label=\"in progress\"");
  });

  it("renders persisted research evidence without raw private metadata", () => {
    const html = renderToStaticMarkup(
      <MessageBubble
        role="assistant"
        botId="bot-1"
        content="완료했습니다."
        timestamp={1_800_000_000_000}
        researchEvidence={{
          inspectedSources: [
            {
              sourceId: "src_child_1",
              kind: "subagent_result",
              uri: "child-agent://bull-case",
              title: "Bull case partner",
              inspectedAt: 1_800_000_000_000,
            },
            {
              sourceId: "src_doc_1",
              kind: "external_doc",
              uri: "https://example.com/report.pdf",
              title: "Market Report",
              inspectedAt: 1_800_000_000_100,
            },
          ],
          citationGate: {
            ruleId: "claim-citation-gate",
            verdict: "ok",
            checkedAt: 1_800_000_000_200,
          },
          capturedAt: 1_800_000_000_300,
        }}
      />,
    );

    expect(html).toContain("Research evidence");
    expect(html).toContain("2 sources");
    expect(html).toContain("Citation check passed");
    expect(html).toContain("Bull case partner");
    expect(html).toContain('data-research-evidence-toggle="true"');
    expect(html).toContain('aria-expanded="false"');
    expect(html).toContain("+1 more");
    expect(html).not.toContain("clawy:research-evidence");
    expect(html).not.toContain("private");
  });
});
