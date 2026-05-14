import { describe, it, expect } from "vitest";
import {
  groundAgainstToolResults,
  detectUngroundedFileClaims,
} from "./factGroundingVerifier.js";
import type { DeterministicVerdict } from "./factGroundingVerifier.js";
import type { TranscriptEntry } from "../../storage/Transcript.js";

function makeToolPair(
  turnId: string,
  name: string,
  input: unknown,
  output: string,
): TranscriptEntry[] {
  const tuId = `tu-${Math.random().toString(36).slice(2)}`;
  return [
    { kind: "tool_call", ts: Date.now(), turnId, toolUseId: tuId, name, input },
    { kind: "tool_result", ts: Date.now(), turnId, toolUseId: tuId, status: "success", output },
  ];
}

describe("groundAgainstToolResults (hybrid Mode A)", () => {
  it("GROUNDED high-confidence when values match", () => {
    const transcript = makeToolPair(
      "turn-1", "FileRead", { path: "config.json" },
      '{"model": "gpt-4o", "temperature": 0.7}',
    );
    const result = groundAgainstToolResults(
      transcript, "turn-1",
      "config.json에 따르면 모델은 gpt-4o이고 temperature는 0.7입니다",
    );
    expect(result.verdict).toBe("GROUNDED");
    expect(result.confidence).toBe("high");
  });

  it("DISTORTED high-confidence on identifier mismatch", () => {
    const transcript = makeToolPair(
      "turn-1", "FileRead", { path: "config.json" },
      '{"model": "gemini-2.5-pro", "temperature": 0.3}',
    );
    const result = groundAgainstToolResults(
      transcript, "turn-1",
      "config.json에 따르면 모델은 GPT-4o이고 temperature는 0.7입니다",
    );
    expect(result.verdict).toBe("DISTORTED");
    expect(result.confidence).toBe("high");
  });

  it("GROUNDED high-confidence when no tool results", () => {
    const result = groundAgainstToolResults([], "turn-1", "some text");
    expect(result.verdict).toBe("GROUNDED");
    expect(result.confidence).toBe("high");
  });

  it("GROUNDED when assistant doesn't reference tool content", () => {
    const transcript = makeToolPair(
      "turn-1", "FileRead", { path: "data.json" },
      '{"count": 42}',
    );
    const result = groundAgainstToolResults(
      transcript, "turn-1",
      "React는 Virtual DOM을 사용합니다.",
    );
    expect(result.verdict).toBe("GROUNDED");
  });

  it("GROUNDED on numbers within ±1% tolerance", () => {
    const transcript = makeToolPair(
      "turn-1", "FileRead", { path: "stats.json" },
      '{"users": 1500, "revenue": 45000.50}',
    );
    const result = groundAgainstToolResults(
      transcript, "turn-1",
      "사용자는 1500명이고 매출은 45001달러입니다",
    );
    expect(result.verdict).toBe("GROUNDED");
  });

  it("DISTORTED high-confidence on significant number mismatch", () => {
    const transcript = makeToolPair(
      "turn-1", "FileRead", { path: "stats.json" },
      '{"users": 1500}',
    );
    const result = groundAgainstToolResults(
      transcript, "turn-1",
      "사용자는 3000명입니다",
    );
    expect(result.verdict).toBe("DISTORTED");
    expect(result.confidence).toBe("high");
  });

  it("low-confidence GROUNDED when values not referenced (needs LLM)", () => {
    const transcript = makeToolPair(
      "turn-1", "FileRead", { path: "config.json" },
      '{"model": "gemini-2.5-pro", "replicas": 3}',
    );
    const result = groundAgainstToolResults(
      transcript, "turn-1",
      "설정을 확인했습니다. 정상적으로 동작 중입니다.",
    );
    // Values not mentioned → deterministic can't confirm → low confidence
    expect(result.verdict).toBe("GROUNDED");
    expect(result.confidence).toBe("low");
  });
});

describe("detectUngroundedFileClaims (hybrid Mode B)", () => {
  it("GROUNDED high-confidence for general knowledge", () => {
    const result = detectUngroundedFileClaims(
      "React는 Virtual DOM을 사용해서 효율적으로 렌더링합니다",
    );
    expect(result.verdict).toBe("GROUNDED");
    expect(result.confidence).toBe("high");
  });

  it("FABRICATED high-confidence when claiming file read", () => {
    const result = detectUngroundedFileClaims(
      "파일을 읽어보니 이미지 리사이즈는 1200픽셀로 설정되어 있습니다",
    );
    expect(result.verdict).toBe("FABRICATED");
    expect(result.confidence).toBe("high");
  });

  it("FABRICATED high-confidence on config claims", () => {
    const result = detectUngroundedFileClaims(
      "The config uses GPT-4o with temperature 0.7",
    );
    expect(result.verdict).toBe("FABRICATED");
    expect(result.confidence).toBe("high");
  });

  it("GROUNDED high-confidence for honest uncertainty", () => {
    const result = detectUngroundedFileClaims(
      "확인해봐야 알 수 있을 것 같습니다. 파일을 읽어보겠습니다.",
    );
    expect(result.verdict).toBe("GROUNDED");
    expect(result.confidence).toBe("high");
  });

  it("FABRICATED high-confidence on script reference", () => {
    const result = detectUngroundedFileClaims(
      "스크립트에 따르면 3단계로 구성됩니다",
    );
    expect(result.verdict).toBe("FABRICATED");
    expect(result.confidence).toBe("high");
  });

  it("low-confidence GROUNDED when specific numbers without file claim", () => {
    const result = detectUngroundedFileClaims(
      "총 1500개의 엔트리가 있고 각각 45000바이트입니다",
    );
    expect(result.verdict).toBe("GROUNDED");
    expect(result.confidence).toBe("low");
  });
});
