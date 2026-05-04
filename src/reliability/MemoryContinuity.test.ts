import { describe, expect, it } from "vitest";
import {
  classifyMemoryContinuity,
  extractDistinctivePhrases,
  hasContinuationCue,
  shouldRetryStaleMemoryPromotion,
} from "./MemoryContinuity.js";

describe("MemoryContinuity", () => {
  it("detects Korean and English continuation cues", () => {
    expect(hasContinuationCue("아까 그 선택 문제 다시 보자")).toBe(true);
    expect(hasContinuationCue("continue the earlier naming decision")).toBe(true);
    expect(hasContinuationCue("SYNC 분량 지금 어느 정도야?")).toBe(false);
  });

  it("marks explicitly continued recalled topics active", () => {
    expect(classifyMemoryContinuity({
      latestUserText: "아까 이름 선택 문제 다시 보자",
      memoryText: "한국식 vs 일본식 이름 선택을 결정해야 한다.",
      source: "qmd",
    })).toBe("active");
  });

  it("does not mark broad project overlap active without continuation cue", () => {
    expect(classifyMemoryContinuity({
      latestUserText: "SYNC 분량 지금 어느 정도야?",
      memoryText: "SYNC 한국식 vs 일본식 이름 선택을 결정해야 한다.",
      source: "qmd",
    })).not.toBe("active");
  });

  it("defaults root memory to background unless continuation is explicit", () => {
    expect(classifyMemoryContinuity({
      latestUserText: "SYNC 분량 지금 어느 정도야?",
      memoryText: "Active context for the project",
      source: "root",
    })).toBe("background");
  });

  it("extracts Korean multi-token distinctive phrases", () => {
    expect(extractDistinctivePhrases("한국식 vs 일본식 이름 선택을 결정해야 한다."))
      .toContain("한국식 vs 일본식 이름 선택");
  });

  it("detects stale background memory promoted into a decision question", () => {
    const result = shouldRetryStaleMemoryPromotion({
      latestUserText: "SYNC 분량 지금 어느 정도야?",
      assistantText: "그런데 한국식 vs 일본식 이름 선택 문제는 어떻게 할까요?",
      records: [{
        turnId: "turn-test",
        source: "qmd",
        path: "memory/old.md",
        continuity: "background",
        distinctivePhrases: ["한국식 vs 일본식 이름 선택"],
      }],
    });
    expect(result.retry).toBe(true);
    expect(result.phrase).toBe("한국식 vs 일본식 이름 선택");
  });

  it("allows explicit callbacks to old topics", () => {
    const result = shouldRetryStaleMemoryPromotion({
      latestUserText: "아까 한국식 vs 일본식 이름 선택 문제 다시 보자",
      assistantText: "한국식 vs 일본식 이름 선택은 일본식이 더 자연스럽습니다.",
      records: [{
        turnId: "turn-test",
        source: "qmd",
        path: "memory/old.md",
        continuity: "background",
        distinctivePhrases: ["한국식 vs 일본식 이름 선택"],
      }],
    });
    expect(result.retry).toBe(false);
  });

  it("allows passive references that are not decision prompts", () => {
    const result = shouldRetryStaleMemoryPromotion({
      latestUserText: "SYNC 분량 지금 어느 정도야?",
      assistantText: "SYNC는 한국식 vs 일본식 이름 선택 논의도 있었지만, 현재 분량은 1-2장 수준입니다.",
      records: [{
        turnId: "turn-test",
        source: "qmd",
        path: "memory/old.md",
        continuity: "background",
        distinctivePhrases: ["한국식 vs 일본식 이름 선택"],
      }],
    });
    expect(result.retry).toBe(false);
  });
});
