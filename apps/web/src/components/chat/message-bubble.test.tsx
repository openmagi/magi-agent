import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { normalizeResearchEvidenceSnapshot } from "@/chat-core/research-evidence";
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

  it("right-aligns multi-file attachment chips on user messages", () => {
    const content = [
      "Review these investment reports in parallel",
      "[attachment:00000000-0000-4000-8000-000000000001:HD-Hyundai-Investment-Report.pdf]",
      "[attachment:00000000-0000-4000-8000-000000000002:SYM-Investment-Report.pdf]",
      "[attachment:00000000-0000-4000-8000-000000000003:Hanmi-Investment-Report.pdf]",
      "[attachment:00000000-0000-4000-8000-000000000004:ALAB-Investment-Report.pdf]",
      "[attachment:00000000-0000-4000-8000-000000000005:CRDO-Investment-Report.pdf]",
    ].join("\n");

    const html = renderToStaticMarkup(
      <MessageBubble
        role="user"
        botId="bot-1"
        content={content}
        timestamp={1_800_000_000_000}
      />,
    );

    expect(html).toContain('data-chat-attachment-list="user"');
    expect(html).toContain("justify-end");
    expect(html).toContain("self-end");
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

  it("renders live progress without an empty assistant body", () => {
    const html = renderToStaticMarkup(
      <MessageBubble
        role="assistant"
        content=""
        timestamp={1_800_000_000_000}
        isStreaming
        liveAssistantTurn
        inlineAfterContent={<div data-testid="inline-work">Waiting for review...</div>}
      />,
    );

    expect(html).toContain('data-chat-live-assistant-turn="true"');
    expect(html).toContain("Waiting for review...");
    expect(html).not.toContain('class="prose-chat"');
  });

  it("renders active live transcript markdown markers as literal text while streaming", () => {
    const html = renderToStaticMarkup(
      <MessageBubble
        role="assistant"
        content=""
        isStreaming
        liveAssistantTurn
        liveTranscriptItems={[
          {
            id: "text-markdown",
            kind: "text",
            content: "Streaming **bold** markers as plain text.",
            receivedAt: 1,
          },
        ]}
      />,
    );

    expect(html).toContain("Streaming **bold** markers as plain text.");
    expect(html).not.toContain("<strong>bold</strong>");
  });

  it("renders streaming assistant fallback content markdown markers as literal text", () => {
    const html = renderToStaticMarkup(
      <MessageBubble
        role="assistant"
        content="Streaming **bold** fallback"
        isStreaming
      />,
    );

    expect(html).toContain("Streaming **bold** fallback");
    expect(html).not.toContain("<strong>bold</strong>");
  });

  it("hides work rows and route meta preambles inside live transcript text", () => {
    const html = renderToStaticMarkup(
      <MessageBubble
        role="assistant"
        content="[META: intent=실행, domain=주식리서치, complexity=복잡, route=서브에"
        isStreaming
        liveAssistantTurn
        liveTranscriptItems={[
          {
            id: "text-meta",
            kind: "text",
            content: "[META: intent=실행, domain=주식리서치, complexity=복잡, route=서브에",
            receivedAt: 1,
          },
          {
            id: "work-1",
            kind: "work",
            rowId: "tool-read",
            group: "tool",
            label: "Reviewing document",
            detail: "workspace/deep-ic-2026-05/batch2/LSCC.md",
            status: "running",
            receivedAt: 2,
          },
        ]}
      />,
    );

    expect(html).not.toContain('data-chat-live-transcript="true"');
    expect(html).not.toContain("Reviewing document");
    expect(html).not.toContain("workspace/deep-ic-2026-05/batch2/LSCC.md");
    expect(html).not.toContain('data-chat-inline-work-row="true"');
    expect(html).not.toContain("[META:");
    expect(html).not.toContain("주식리서치");
  });

  it("hides split route meta fragments without rendering interleaved work rows", () => {
    const html = renderToStaticMarkup(
      <MessageBubble
        role="assistant"
        content="[META: intent=상태보고, domain=주식리서치, complexity=복잡, route=서브에이전트]\n\n결과를 정리합니다."
        isStreaming
        liveAssistantTurn
        liveTranscriptItems={[
          {
            id: "text-meta-1",
            kind: "text",
            content: "[M",
            receivedAt: 1,
          },
          {
            id: "work-1",
            kind: "work",
            rowId: "tool-read",
            group: "tool",
            label: "문서 검토",
            detail: "workspace/deep-ic-2026-05/batch2/LSCC.md",
            status: "running",
            receivedAt: 2,
          },
          {
            id: "text-meta-2",
            kind: "text",
            content: "ETA",
            receivedAt: 3,
          },
          {
            id: "work-2",
            kind: "work",
            rowId: "tool-search",
            group: "tool",
            label: "Searching the web",
            detail: "Lattice Semiconductor Q1 2026 earnings results revenue guidance",
            status: "running",
            receivedAt: 4,
          },
          {
            id: "text-meta-3",
            kind: "text",
            content: ": intent=상태보고, domain=주식리서치, complexity=복잡, route=서브에이전트]\n\n결과를 정리합니다.",
            receivedAt: 5,
          },
        ]}
      />,
    );

    expect(html).toContain("결과를 정리합니다.");
    expect(html).not.toContain("문서 검토");
    expect(html).not.toContain("Searching the web");
    expect(html).not.toContain("workspace/deep-ic-2026-05/batch2/LSCC.md");
    expect(html).not.toContain("Lattice Semiconductor Q1 2026");
    expect(html).not.toContain('data-chat-inline-work-row="true"');
    expect(html).not.toContain("[M");
    expect(html).not.toContain("ETA");
    expect(html).not.toContain("intent=상태보고");
    expect(html).not.toContain("주식리서치");
  });

  it("does not show the deprecated mid-turn label on injected user messages", () => {
    const html = renderToStaticMarkup(
      <MessageBubble
        role="user"
        content="Any news?"
        timestamp={1_800_000_000_000}
        injected
      />,
    );

    expect(html).toContain("Any news?");
    expect(html).toContain("injected");
    expect(html).not.toContain("mid-turn");
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

  it("persists a collapsed activity summary in the transcript while keeping verbose detail out", () => {
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

    expect(html).toContain("완료했습니다.");
    // A: the grouped activity summary now survives into the finalized transcript
    // (collapsed by default — the record of what the agent did stays visible).
    expect(html).toMatch(/Ran \d+ action/);
    // ...but verbose per-tool labels stay collapsed/grouped, the live task board
    // is gated to the live turn, and the in-progress phrasing never persists.
    expect(html).not.toContain("DocumentWrite");
    expect(html).not.toContain("FileDeliver");
    expect(html).not.toContain("Updated task board");
    expect(html).not.toContain("actions in progress");
  });

  it("keeps completed public model progress rows out of the final transcript", () => {
    const html = renderToStaticMarkup(
      <MessageBubble
        role="assistant"
        botId="bot-1"
        content="최종 답변입니다."
        timestamp={1_800_000_000_000}
        activities={[
          {
            id: "llm:turn-1:heartbeat:30",
            label: "ModelProgress",
            status: "done",
            startedAt: 1_800_000_000_000,
            inputPreview: JSON.stringify({
              stage: "heartbeat",
              label: "Processing request",
              detail: "Updating the public progress log",
              elapsedMs: 30_000,
            }),
            outputPreview: "Still thinking (30s elapsed)",
          },
        ]}
      />,
    );

    expect(html).toContain("최종 답변입니다.");
    expect(html).not.toContain("Processing request");
    expect(html).not.toContain("30s elapsed");
    expect(html).not.toContain("ModelProgress");
    expect(html).not.toContain('data-agent-activity-row="true"');
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

  it("shows governed claim support status without raw claim extraction", () => {
    const researchEvidence = normalizeResearchEvidenceSnapshot({
      inspectedSources: [],
      capturedAt: 1_800_000_000_000,
      projectionMode: "structured_claims_only",
      claims: [{
        claimId: "claim-1",
        claimType: "numeric",
        supportStatus: "supported",
        claimText: "Private raw claim text must not render.",
        citationRefs: ["source_1_span_1"],
        evidenceRefs: ["evidence:sha256:1111111111111111111111111111111111111111111111111111111111111111"],
        rawEvidenceLedger: { private: true },
      }],
    });
    expect(researchEvidence).toBeDefined();

    const html = renderToStaticMarkup(
      <MessageBubble
        role="assistant"
        content="Revenue increased based on verified sources."
        timestamp={1_800_000_000_000}
        researchEvidence={researchEvidence}
      />,
    );

    expect(html).toContain("Structured claims");
    expect(html).toContain("supported");
    expect(html).toContain("numeric");
    expect(html).toContain('data-governed-claim-summary="true"');
    expect(html).not.toContain("evidence:sha256");
    expect(html).not.toContain("1111111111111111");
    expect(html).not.toContain("Private raw claim text");
    expect(html).not.toContain("rawEvidenceLedger");
  });

  it("does not render projection mode alone as evidence UI", () => {
    const html = renderToStaticMarkup(
      <MessageBubble
        role="assistant"
        content="Done."
        timestamp={1_800_000_000_000}
        researchEvidence={{
          inspectedSources: [],
          capturedAt: 1_800_000_000_000,
          projectionMode: "raw_text_allowed",
        }}
      />,
    );

    expect(html).not.toContain("Research evidence");
    expect(html).not.toContain("raw_text_allowed");
    expect(html).not.toContain("Raw text allowed");
  });

  it("renders persisted reasoning as a collapsible thinking block, separate from the answer", () => {
    const html = renderToStaticMarkup(
      <MessageBubble
        role="assistant"
        botId="bot-1"
        content="The answer is 42."
        thinkingContent="Let me compute 6 times 7 step by step."
        thinkingDuration={3}
        timestamp={1_800_000_000_000}
      />,
    );

    expect(html).toContain("Let me compute 6 times 7 step by step.");
    expect(html).toContain("Thought");
    expect(html).toContain("The answer is 42.");
  });

  it("renders the source-citation fail-open hedge as a distinguished callout", () => {
    const html = renderToStaticMarkup(
      <MessageBubble
        role="assistant"
        botId="bot-1"
        content={[
          "Revenue grew 40% year over year.",
          "",
          "> [!citation-hedge]",
          "> Contains unverified figures; no source was available for: Revenue grew 40%",
        ].join("\n")}
        timestamp={1_800_000_000_000}
      />,
    );

    // The hedge is styled as the muted callout, not plain answer prose.
    expect(html).toContain("citation-hedge-callout");
    expect(html).toContain("bg-amber-500");
    expect(html).toContain("Contains unverified figures");
    // The sentinel itself is stripped from the visible text.
    expect(html).not.toContain("[!citation-hedge]");
  });

  it("does NOT restyle a normal blockquote as a citation callout", () => {
    const html = renderToStaticMarkup(
      <MessageBubble
        role="assistant"
        botId="bot-1"
        content={"> A normal quote from a source document."}
        timestamp={1_800_000_000_000}
      />,
    );

    expect(html).not.toContain("citation-hedge-callout");
    expect(html).toContain("A normal quote from a source document.");
    expect(html).toContain("<blockquote>");
  });
});
