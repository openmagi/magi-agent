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
});
