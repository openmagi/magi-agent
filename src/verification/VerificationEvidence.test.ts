import { describe, expect, it } from "vitest";
import {
  classifyEvidence,
  shouldBlockClaim,
  transcriptEvidenceForTurn,
} from "./VerificationEvidence.js";
import type { TranscriptEntry } from "../storage/Transcript.js";

describe("VerificationEvidence", () => {
  it("separates work evidence from verification evidence", () => {
    expect(classifyEvidence([{ tool: "FileEdit", status: "ok" }]).verification).toBe(false);
    expect(
      classifyEvidence([
        { tool: "Bash", input: { command: "npm test" }, status: "ok" },
      ]).verification,
    ).toBe(true);
  });

  it("blocks pass/fixed claims backed only by file edits", () => {
    const fileEditOnlyEvidence = [{ tool: "FileEdit", status: "ok" }];
    expect(shouldBlockClaim("tests pass", fileEditOnlyEvidence)).toBe(true);
    expect(shouldBlockClaim("changed file but not verified", fileEditOnlyEvidence)).toBe(false);
  });

  it("classifies child worktree apply as workspace mutation evidence", () => {
    expect(
      classifyEvidence([
        {
          tool: "SpawnWorktreeApply",
          status: "ok",
          metadata: { changedFiles: ["src/feature.ts"] },
        },
      ]),
    ).toMatchObject({ work: true, verification: false });
  });

  it("classifies benchmark reports as verification evidence", () => {
    expect(
      classifyEvidence([
        {
          tool: "CodingBenchmark",
          status: "ok",
          metadata: { evidenceKind: "benchmark_report" },
        },
      ]),
    ).toMatchObject({ verification: true });
  });

  it("classifies common verification commands and document render checks", () => {
    expect(
      classifyEvidence([
        { tool: "Bash", input: { command: "pnpm test && npm run build" }, status: "ok" },
      ]),
    ).toMatchObject({ work: false, verification: true });
    expect(
      classifyEvidence([
        { tool: "DocumentPreview", input: { path: "report.docx" }, status: "ok" },
      ]),
    ).toMatchObject({ verification: true, documentVerification: true });
  });

  it("treats native web search as same-turn verification evidence for source-sensitive claims", () => {
    expect(
      classifyEvidence([
        { tool: "WebSearch", input: { query: "latest pricing" }, status: "ok" },
      ]),
    ).toMatchObject({ verification: true });
    expect(
      shouldBlockClaim("검색해서 확인했습니다.", [
        { tool: "web-search", input: { query: "latest pricing" }, status: "ok" },
      ]),
    ).toBe(false);
  });

  it("treats native deterministic tools as verification evidence for exactness claims", () => {
    expect(
      classifyEvidence([
        { tool: "Clock", input: { timezone: "Asia/Seoul" }, status: "ok" },
        {
          tool: "Calculation",
          input: { operation: "average", field: "revenue" },
          status: "ok",
        },
      ]),
    ).toMatchObject({ verification: true });
  });

  it("builds same-turn evidence from transcript tool calls/results", () => {
    const transcript: TranscriptEntry[] = [
      {
        kind: "tool_call",
        ts: 1,
        turnId: "turn-1",
        toolUseId: "tool-1",
        name: "FileEdit",
        input: { path: "src/a.ts" },
      },
      {
        kind: "tool_result",
        ts: 2,
        turnId: "turn-1",
        toolUseId: "tool-1",
        status: "ok",
      },
      {
        kind: "tool_call",
        ts: 3,
        turnId: "turn-1",
        toolUseId: "tool-2",
        name: "Bash",
        input: { command: "npm run lint" },
      },
      {
        kind: "tool_result",
        ts: 4,
        turnId: "turn-1",
        toolUseId: "tool-2",
        status: "ok",
      },
    ];
    expect(classifyEvidence(transcriptEvidenceForTurn(transcript, "turn-1"))).toMatchObject({
      work: true,
      verification: true,
    });
  });

  it("preserves structured tool-result metadata in transcript evidence", () => {
    const transcript: TranscriptEntry[] = [
      {
        kind: "tool_call",
        ts: 1,
        turnId: "turn-1",
        toolUseId: "tool-1",
        name: "FileEdit",
        input: { path: "src/a.ts" },
      },
      {
        kind: "tool_result",
        ts: 2,
        turnId: "turn-1",
        toolUseId: "tool-1",
        status: "ok",
        metadata: {
          evidenceKind: "patch",
          changedFiles: ["src/a.ts"],
        },
      },
    ];

    expect(transcriptEvidenceForTurn(transcript, "turn-1")).toEqual([
      expect.objectContaining({
        tool: "FileEdit",
        metadata: {
          evidenceKind: "patch",
          changedFiles: ["src/a.ts"],
        },
      }),
    ]);
  });
});
