import { describe, it, expect } from "vitest";
import { RepetitionDetector } from "./RepetitionDetector.js";

describe("RepetitionDetector", () => {
  it("does not trigger on normal text", () => {
    const d = new RepetitionDetector({ checkInterval: 1 });
    const result = d.feed(
      "안녕하세요. 오늘 회의에서 논의된 내용을 정리해드리겠습니다. " +
      "첫 번째로 예산 계획에 대해 이야기했고, 두 번째로 인력 배치를 논의했습니다. " +
      "세 번째로는 마케팅 전략에 대한 브레인스토밍을 진행했습니다.",
    );
    expect(result.detected).toBe(false);
  });

  it("detects repeated sentence pattern (Korean)", () => {
    const d = new RepetitionDetector({ checkInterval: 1 });
    const repeated = "사장님, KB에 직접 파일 업로드 기능이 없어요. document-reader 스킬로 업로드하는 것 같습니다. 확인하겠습니다.";
    // Feed the same sentence 4 times.
    const text = (repeated + " ").repeat(4);
    const result = d.feed(text);
    expect(result.detected).toBe(true);
    expect(result.count).toBeGreaterThanOrEqual(3);
  });

  it("detects repeated substring pattern", () => {
    const d = new RepetitionDetector({ checkInterval: 1 });
    const chunk = "This is a long enough repeated pattern that should be detected by the sliding window algorithm. ";
    const text = chunk.repeat(4);
    const result = d.feed(text);
    expect(result.detected).toBe(true);
  });

  it("does not trigger on short repeated patterns", () => {
    const d = new RepetitionDetector({ checkInterval: 1, minPatternLen: 40 });
    // "네. " is only 3 chars — should NOT trigger
    const result = d.feed("네. 네. 네. 네. 네. 네. 네. 네. 네. 네. ");
    expect(result.detected).toBe(false);
  });

  it("detects repetition fed incrementally", () => {
    const d = new RepetitionDetector({ checkInterval: 1 });
    const sentence = "확인하겠습니다. document-reader 스킬에서 업로드 기능을 찾아보겠습니다. ";
    let lastResult = d.feed(sentence);
    for (let i = 1; i < 5; i++) {
      lastResult = d.feed(sentence);
    }
    // After 5 feeds of the same sentence, should detect
    expect(lastResult.detected).toBe(true);
  });

  it("respects repeatThreshold config", () => {
    const d = new RepetitionDetector({
      checkInterval: 1,
      repeatThreshold: 5,
    });
    const chunk = "A fairly long pattern that we want to repeat many times to test threshold config. ";
    // 4 repeats — should NOT trigger with threshold=5
    let result = d.feed(chunk.repeat(4));
    expect(result.detected).toBe(false);
    // Feed one more — should trigger
    result = d.feed(chunk);
    expect(result.detected).toBe(true);
  });

  it("resets state correctly", () => {
    const d = new RepetitionDetector({ checkInterval: 1 });
    const chunk = "This repeated pattern is long enough to trigger the detector after three or more occurrences. ";
    d.feed(chunk.repeat(4));
    d.reset();
    const result = d.feed("Fresh normal text that should not trigger anything at all.");
    expect(result.detected).toBe(false);
    expect(d.getText()).toBe("Fresh normal text that should not trigger anything at all.");
  });

  it("getText returns accumulated text", () => {
    const d = new RepetitionDetector();
    d.feed("Hello ");
    d.feed("world");
    expect(d.getText()).toBe("Hello world");
  });

  it("handles empty deltas gracefully", () => {
    const d = new RepetitionDetector({ checkInterval: 1 });
    const result = d.feed("");
    expect(result.detected).toBe(false);
  });

  it("does not false-positive on similar but different sentences", () => {
    const d = new RepetitionDetector({ checkInterval: 1 });
    const text = [
      "KB에 파일 업로드 기능을 찾아보겠습니다. SKILL.md를 확인합니다.",
      "KB에 파일 업로드하는 다른 방법을 찾아보겠습니다. EXECUTION-TOOLS.md를 확인합니다.",
      "KB API를 통해 직접 업로드할 수 있는지 확인하겠습니다. integration.sh를 확인합니다.",
      "document-reader 스킬의 업로드 기능을 살펴보겠습니다. 해당 스킬 파일을 읽어봅니다.",
    ].join(" ");
    const result = d.feed(text);
    expect(result.detected).toBe(false);
  });

  it("detects real-world degeneration pattern from screenshot", () => {
    const d = new RepetitionDetector({ checkInterval: 1 });
    // Real pattern from the incident: bot repeating the same sentence
    // with minor variations that still match.
    const degenerateText =
      "사장님, KB에 직접 파일 업로드 기능이 없어요. `document-reader` 스킬로 업로드하는 것 같습니다. 확인하겠습니다." +
      "사장님, KB에 직접 파일 업로드 기능이 없어요. `document-reader` 스킬로 업로드하는 것 같습니다. 확인하겠습니다." +
      "사장님, KB에 직접 파일 업로드 기능이 없어요. `document-reader` 스킬로 업로드하는 것 같습니다. 확인하겠습니다." +
      "사장님, KB에 직접 파일 업로드 기능이 없어요.";
    const result = d.feed(degenerateText);
    expect(result.detected).toBe(true);
    expect(result.count).toBeGreaterThanOrEqual(3);
  });
});
