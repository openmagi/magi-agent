import { describe, expect, it } from "vitest";
import { derivePublicToolPreview } from "./public-tool-preview";

describe("derivePublicToolPreview", () => {
  it("summarizes FileWrite with a plain-language document action", () => {
    const preview = derivePublicToolPreview({
      label: "FileWrite",
      inputPreview: JSON.stringify(
        {
          path: "book/FINAL_MANUSCRIPT.md",
          content: "Line one\nLine two\nLine three",
        },
        null,
        2,
      ),
    });

    expect(preview).toEqual({
      action: "Creating document",
      target: "book/FINAL_MANUSCRIPT.md",
      snippet: "Line one\nLine two\nLine three",
    });
  });

  it("summarizes FileEdit with a plain-language document action", () => {
    const preview = derivePublicToolPreview({
      label: "FileEdit",
      inputPreview: JSON.stringify({
        file_path: "docs/report.md",
        old_string: "old paragraph that may be very long",
        new_string: "new paragraph that may be very long",
      }),
    });

    expect(preview).toEqual({
      action: "Updating document",
      target: "docs/report.md",
      snippet: "Replace: old paragraph that may be very long -> new paragraph that may be very long",
    });
  });

  it("summarizes FileRead and common commands without exposing raw scripts", () => {
    expect(
      derivePublicToolPreview({
        label: "FileRead",
        inputPreview: '{"path":"src/app/page.tsx"}',
      }),
    ).toEqual({
      action: "Reviewing code file",
      target: "src/app/page.tsx",
    });

    expect(
      derivePublicToolPreview({
        label: "Bash",
        inputPreview: '{"command":"npm test -- src/lib/chat/public-tool-preview.test.ts"}',
      }),
    ).toEqual({
      action: "Checking the work",
      target: "Running tests",
    });
  });

  it("does not expose raw FileRead result JSON or file hashes", () => {
    const preview = derivePublicToolPreview({
      label: "FileRead",
      outputPreview: JSON.stringify({
        path: "skills/pos-sales/SKILL.md",
        fileSha256: "not-a-real-file-sha",
        contentSha256: "not-a-real-content-sha",
        content: "---\nname: pos-sales\ndescription: Sales assistant",
      }),
    });

    expect(preview).toEqual({
      action: "Reviewing document",
      target: "skills/pos-sales/SKILL.md",
    });
    expect(preview?.snippet).toBeUndefined();
    expect(JSON.stringify(preview)).not.toContain("fileSha256");
    expect(JSON.stringify(preview)).not.toContain("contentSha256");
    expect(JSON.stringify(preview)).not.toContain("Sales assistant");
  });

  it("summarizes document commands in user-facing language", () => {
    expect(
      derivePublicToolPreview({
        label: "Bash",
        inputPreview: JSON.stringify({
          command: "pandoc report.md -o report.pdf",
        }),
      }),
    ).toEqual({
      action: "Creating PDF document",
      target: "report.pdf",
    });

    expect(
      derivePublicToolPreview({
        label: "Bash",
        inputPreview: JSON.stringify({
          command: "cat book/intro.md book/body.md > book/FINAL_MANUSCRIPT.md",
        }),
      }),
    ).toEqual({
      action: "Combining document sections",
      target: "book/FINAL_MANUSCRIPT.md",
    });
  });

  it("summarizes command output without dumping raw result JSON", () => {
    const preview = derivePublicToolPreview({
      label: "Bash",
      inputPreview: JSON.stringify({
        command: "ls reports",
      }),
      outputPreview: JSON.stringify({
        exitCode: 0,
        stdout: "final-report.pdf\nsummary.md\n",
        stderr: "",
        durationMs: 12,
      }),
    });

    expect(preview).toEqual({
      action: "Bash",
      target: "ls reports",
      snippet: "final-report.pdf\nsummary.md",
    });
    expect(JSON.stringify(preview)).not.toContain("exitCode");
  });

  it("does not expose JSON blobs embedded inside command stdout", () => {
    const preview = derivePublicToolPreview({
      label: "Bash",
      inputPreview: JSON.stringify({
        command: "cat merchants.json",
      }),
      outputPreview: JSON.stringify({
        exitCode: 0,
        stdout:
          '{"count":4,"merchants":[{"merchantId":"439438","merchantName":"Canary test store"}]}',
        stderr: "",
        durationMs: 12,
      }),
    });

    expect(preview?.action).toBe("Bash");
    expect(preview?.target).toBe("cat merchants.json");
    expect(preview?.snippet).toBeUndefined();
    expect(JSON.stringify(preview)).not.toContain("merchantId");
  });

  it("summarizes browser session output without exposing transport internals", () => {
    expect(
      derivePublicToolPreview({
        label: "Browser",
        outputPreview: JSON.stringify({
          action: "create_session",
          sessionId: "browser-session-fixture",
          cdpEndpoint: "ws://browser-worker.clawy-system:9222/cdp/browser-session-fixture",
        }),
      }),
    ).toEqual({
      action: "Opening browser",
      target: "Starting browser session",
    });

    expect(
      derivePublicToolPreview({
        label: "Browser",
        outputPreview: JSON.stringify({
          action: "scrape",
          sessionId: "browser-session-fixture",
          cdpEndpoint: "ws://browser-worker.clawy-system:9222/cdp/browser-session-fixture/page",
          status: "error",
          error: "page closed",
        }),
      }),
    ).toEqual({
      action: "Browser step failed",
      snippet: "page closed",
    });
  });

  it("localizes browser session output without exposing transport internals", () => {
    expect(
      derivePublicToolPreview({
        label: "Browser",
        language: "ko",
        outputPreview: JSON.stringify({
          action: "create_session",
          sessionId: "browser-session-fixture",
          cdpEndpoint: "ws://browser-worker.clawy-system:9222/cdp/browser-session-fixture",
        }),
      }),
    ).toEqual({
      action: "브라우저 여는 중",
      target: "브라우저 세션 시작 중",
    });

    expect(
      derivePublicToolPreview({
        label: "Browser",
        language: "ko",
        outputPreview: JSON.stringify({
          action: "scrape",
          url: "https://example.com/report",
        }),
      }),
    ).toEqual({
      action: "페이지 읽는 중",
      target: "https://example.com/report",
    });
  });

  it("turns permission denials into plain-language output", () => {
    const preview = derivePublicToolPreview({
      label: "Bash",
      inputPreview: JSON.stringify({
        command: "cat intro.md body.md > final.md",
      }),
      outputPreview: "permission denied: complex shell requires explicit approval",
    });

    expect(preview).toEqual({
      action: "Combining document sections",
      target: "final.md",
      snippet: "Needs permission to continue",
    });
  });

  it("summarizes SpawnAgent requests as helper assignments without dumping the full JSON payload", () => {
    const preview = derivePublicToolPreview({
      label: "SpawnAgent",
      inputPreview: JSON.stringify({
        persona: "skills-auditor-batch-5-documents",
        prompt:
          "## Skills Deep Audit - Batch 5: 문서/번역 스킬 (6개)\n\n**목표:** 문서 처리/변환/번역의 실제 작동성 검증\n\n### 대상:\n\n1. document-reader",
      }),
    });

    expect(preview).toEqual({
      action: "Assigning helper",
      target:
        "Skills Deep Audit - Batch 5: 문서/번역 스킬 (6개)\n목표: 문서 처리/변환/번역의 실제 작동성 검증",
    });
    expect(JSON.stringify(preview)).not.toContain('"persona"');
    expect(JSON.stringify(preview)).not.toContain('"prompt"');
    expect(JSON.stringify(preview)).not.toContain("skills-auditor-batch-5-documents");
  });

  it("summarizes truncated SpawnAgent prompt prefixes instead of showing only a generic assignment", () => {
    const preview = derivePublicToolPreview({
      label: "SpawnAgent",
      inputPreview:
        '{"persona":"skeptic-partner","prompt":"You are the SKEPTIC PARTNER.\\n\\nTask: Review Naeoe Distillery TIPS LP investment materials and identify market, financial, and legal risks.\\n\\nUse only the provided context...',
    });

    expect(preview).toEqual({
      action: "Assigning helper",
      target:
        "Task: Review Naeoe Distillery TIPS LP investment materials and identify market, financial, and legal risks.",
    });
    expect(JSON.stringify(preview)).not.toContain("skeptic-partner");
    expect(JSON.stringify(preview)).not.toContain("SKEPTIC PARTNER");
  });

  it("summarizes SpawnAgent result JSON without exposing raw runtime fields", () => {
    const preview = derivePublicToolPreview({
      label: "SpawnAgent",
      inputPreview: JSON.stringify({
        persona: "calculator-gpt",
        prompt: "Calculate 1 + 1. Respond with only the numeric result.",
      }),
      outputPreview: JSON.stringify({
        taskId: "spawn_motzdgo9_mzbmo5cy",
        status: "ok",
        finalText:
          "MODEL: gpt-5.5-pro\nRESULT: 2\nREASONING: Deterministic sum of 1 and 1 via Calculation tool yields 2.",
        toolCallCount: 1,
        attempts: 1,
        artifacts: [],
        spawnDir: "/home/ocuser/.openclaw/spawns/spawn_motzdgo9_mzbmo5cy",
      }),
    });

    expect(preview).toEqual({
      action: "Helper reported result",
      target: "Calculate 1 + 1. Respond with only the numeric result.",
      snippet:
        "Result: 2\nReason: Deterministic sum of 1 and 1 via Calculation tool yields 2.",
    });
    expect(JSON.stringify(preview)).not.toContain("taskId");
    expect(JSON.stringify(preview)).not.toContain("spawnDir");
    expect(JSON.stringify(preview)).not.toContain("artifacts");
    expect(JSON.stringify(preview)).not.toContain("MODEL:");
  });

  it("does not fall back to raw SpawnAgent JSON when no prompt summary is available", () => {
    const preview = derivePublicToolPreview({
      label: "SpawnAgent",
      outputPreview: JSON.stringify({
        taskId: "spawn_abc",
        status: "ok",
        finalText: "Finished reviewing the document.",
        spawnDir: "/tmp/spawn_abc",
      }),
    });

    expect(preview).toEqual({
      action: "Helper reported result",
      snippet: "Finished reviewing the document.",
    });
    expect(JSON.stringify(preview)).not.toContain("spawn_abc");
    expect(JSON.stringify(preview)).not.toContain("taskId");
  });

  it("summarizes Calculation output without exposing raw JSON", () => {
    const preview = derivePublicToolPreview({
      label: "Calculation",
      outputPreview: JSON.stringify({
        operation: "sum",
        field: "v",
        result: 2,
        rowCount: 2,
        numericCount: 2,
        ignoredCount: 0,
        sum: 2,
      }),
    });

    expect(preview).toEqual({
      action: "Calculated total",
      target: "2 rows checked",
      snippet: "Result: 2",
    });
    expect(JSON.stringify(preview)).not.toContain('"operation"');
    expect(JSON.stringify(preview)).not.toContain('"numericCount"');
    expect(JSON.stringify(preview)).not.toContain('"sum"');
  });

  it("summarizes grouped Calculation output without dumping object braces", () => {
    const preview = derivePublicToolPreview({
      label: "Calculation",
      outputPreview: JSON.stringify({
        operation: "group_by_sum",
        field: "amount",
        groupBy: "category",
        result: { food: 12, travel: 20 },
        rowCount: 3,
        numericCount: 3,
        ignoredCount: 0,
      }),
    });

    expect(preview).toEqual({
      action: "Calculated grouped totals",
      target: "3 rows checked",
      snippet: "Results:\nfood: 12\ntravel: 20",
    });
    expect(JSON.stringify(preview)).not.toContain('"result"');
    expect(preview?.snippet).not.toContain("{");
  });

  it("summarizes time and date range tool JSON without exposing runtime fields", () => {
    expect(
      derivePublicToolPreview({
        label: "Clock",
        outputPreview: JSON.stringify({
          timestampMs: 1_778_064_000_000,
          iso: "2026-05-06T12:00:00.000Z",
          timezone: "America/New_York",
          localDate: "2026-05-06",
          localTime: "08:00:00",
        }),
      }),
    ).toEqual({
      action: "Checked current time",
      target: "America/New_York",
      snippet: "2026-05-06 08:00:00",
    });

    expect(
      derivePublicToolPreview({
        label: "DateRange",
        outputPreview: JSON.stringify({
          mode: "last_n_days",
          startDate: "2026-04-30",
          endDate: "2026-05-06",
          dayCount: 7,
          timezone: "America/New_York",
          inclusiveEnd: true,
        }),
      }),
    ).toEqual({
      action: "Calculated date range",
      target: "2026-04-30 to 2026-05-06",
      snippet: "7 days · America/New_York",
    });
  });

  it("summarizes generated document and spreadsheet outputs without artifact internals", () => {
    expect(
      derivePublicToolPreview({
        label: "DocumentWrite",
        outputPreview: JSON.stringify({
          artifactId: "art_doc_123",
          workspacePath: "reports/audit-report.docx",
          filename: "audit-report.docx",
        }),
      }),
    ).toEqual({
      action: "Created document",
      target: "audit-report.docx",
      snippet: "reports/audit-report.docx",
    });

    expect(
      derivePublicToolPreview({
        label: "SpreadsheetWrite",
        outputPreview: JSON.stringify({
          artifactId: "art_sheet_123",
          workspacePath: "reports/budget.xlsx",
          filename: "budget.xlsx",
        }),
      }),
    ).toEqual({
      action: "Created spreadsheet",
      target: "budget.xlsx",
      snippet: "reports/budget.xlsx",
    });
  });

  it("summarizes artifact outputs without exposing artifact IDs or raw metadata", () => {
    expect(
      derivePublicToolPreview({
        label: "ArtifactCreate",
        outputPreview: JSON.stringify({
          artifactId: "art_123",
          meta: {
            title: "Audit Report",
            kind: "report",
            artifactId: "art_123",
          },
        }),
      }),
    ).toEqual({
      action: "Created artifact",
      target: "Audit Report",
      snippet: "report",
    });

    expect(
      derivePublicToolPreview({
        label: "ArtifactRead",
        outputPreview: JSON.stringify({
          content: "Summary line\nSecond line",
          meta: {
            title: "Audit Report",
            kind: "report",
          },
          tier: "L1",
        }),
      }),
    ).toEqual({
      action: "Read artifact",
      target: "Audit Report",
      snippet: "Summary line\nSecond line",
    });
  });

  it("falls back to plain key facts for unknown structured JSON instead of raw JSON", () => {
    const preview = derivePublicToolPreview({
      label: "Revenue",
      outputPreview: JSON.stringify({
        status: "ok",
        count: 3,
        workspacePath: "reports/revenue.csv",
        filename: "revenue.csv",
        internalId: "rev_123",
      }),
    });

    expect(preview).toEqual({
      action: "Revenue",
      target: "revenue.csv",
      snippet: "Status: ok\nCount: 3\nPath: reports/revenue.csv",
    });
    expect(preview?.snippet).not.toContain("{");
    expect(JSON.stringify(preview)).not.toContain("internalId");
    expect(JSON.stringify(preview)).not.toContain("rev_123");
  });

  it("does not expose raw JSON arrays for unknown tool output", () => {
    const preview = derivePublicToolPreview({
      label: "CustomTool",
      outputPreview: JSON.stringify([
        { id: "internal-1", sessionId: "session-secret", status: "ok" },
      ]),
    });

    expect(preview).toEqual({
      action: "CustomTool",
      target: "Processing tool result",
    });
    expect(JSON.stringify(preview)).not.toContain("internal-1");
    expect(JSON.stringify(preview)).not.toContain("session-secret");
  });

  it("does not expose truncated JSON-looking tool previews", () => {
    expect(
      derivePublicToolPreview({
        label: "CodeWorkspace",
        outputPreview:
          '{"path":"code/naeoe-tips-ic/CONTEXT.md","fileSha256":"not-a-real-file-sha","contentSha256":"not-a-real-content-sha","content":"# 내외디스털리 TIPS LP 투자 심사...',
      }),
    ).toEqual({
      action: "Reviewing document",
      target: "code/naeoe-tips-ic/CONTEXT.md",
    });

    expect(
      derivePublicToolPreview({
        label: "Browser",
        outputPreview:
          '{"action":"create_session","sessionId":"browser-session-fixture","cdpEndpoint":"ws://browser-worker.clawy-system:9222/cdp/browser-session-fixture...',
      }),
    ).toEqual({
      action: "Opening browser",
      target: "Starting browser session",
    });

    expect(
      derivePublicToolPreview({
        label: "TaskGet",
        outputPreview:
          '{"taskId":"spawn_mow6u189_6midf1sy","parentTurnId":"01KR2G4K8Y9XB18C35YND10H8F","sessionKey":"agent:main:app:ch-moi9105m:24","persona":"skeptic-partner","prompt":"You are the SKEPTIC PARTNER...',
      }),
    ).toEqual({
      action: "Checking helper progress",
      target: "Waiting for helper update",
    });
  });

  it("summarizes search and task board structures without dumping payloads", () => {
    expect(
      derivePublicToolPreview({
        label: "WebSearch",
        inputPreview: JSON.stringify({ query: "latest market data" }),
        outputPreview: JSON.stringify({
          results: [
            { title: "First result", url: "https://example.test/1" },
            { title: "Second result", url: "https://example.test/2" },
          ],
        }),
      }),
    ).toEqual({
      action: "Searching the web",
      target: "latest market data",
      snippet: "2 results",
    });

    expect(
      derivePublicToolPreview({
        label: "TaskBoard",
        outputPreview: JSON.stringify({
          tasks: [
            { title: "Research evidence", status: "completed" },
            { title: "Draft answer", status: "in_progress" },
          ],
        }),
      }),
    ).toEqual({
      action: "Updated task list",
      target: "1/2 tasks complete",
      snippet: "Now: Draft answer",
    });
  });

  it("renders heartbeat model progress as continuing work instead of repeating the same thinking label", () => {
    const preview = derivePublicToolPreview({
      label: "ModelProgress",
      inputPreview: JSON.stringify({
        stage: "heartbeat",
        label: "Still working",
        detail: "Waiting for the next runtime update",
        elapsedMs: 40_000,
      }),
      outputPreview: "Still thinking (40s elapsed)",
    });

    expect(preview?.action).toBe("Still working");
    expect(preview?.target).toBe("40s elapsed");
    expect(preview?.snippet).toContain("Waiting for the next runtime update");
  });

  it("renders specific public heartbeat labels when the client can infer the current stage", () => {
    const preview = derivePublicToolPreview({
      label: "ModelProgress",
      inputPreview: JSON.stringify({
        stage: "heartbeat",
        label: "자료 읽는 중",
        detail: "공개 진행 로그를 갱신하고 있습니다",
        elapsedMs: 30_000,
      }),
      outputPreview: "Still thinking (30s elapsed)",
    });

    expect(preview?.action).toBe("자료 읽는 중");
    expect(preview?.target).toBe("30s elapsed");
  });

  it("renders public activity heartbeat labels for long-running tools", () => {
    const preview = derivePublicToolPreview({
      label: "ActivityProgress",
      inputPreview: JSON.stringify({
        stage: "heartbeat",
        label: "자료 읽는 중",
        target: "workspace/stock-framework-2026-05/CONTEXT.md",
        detail: "FileRead",
        elapsedMs: 40_000,
      }),
      outputPreview: "Still running (40s elapsed)",
      language: "ko",
    });

    expect(preview).toEqual({
      action: "자료 읽는 중",
      target: "40초째 작업 중",
      snippet: "workspace/stock-framework-2026-05/CONTEXT.md\nFileRead",
    });
  });

  it("redacts secret-looking values and bounds snippets", () => {
    const preview = derivePublicToolPreview({
      label: "Bash",
      inputPreview: JSON.stringify({
        command: "curl -H 'Authorization: Bearer sk-secret-token' https://example.test",
      }),
      outputPreview: `token=ghp_supersecret ${"x".repeat(500)}`,
    });

    expect(JSON.stringify(preview)).not.toContain("sk-secret-token");
    expect(JSON.stringify(preview)).not.toContain("ghp_supersecret");
    expect(preview?.target).toBe("Running network request");
    expect(preview?.snippet?.length).toBeLessThanOrEqual(240);
  });
});
