import { describe, it, expect } from "vitest";
import { createThinkTagSplitter } from "./think-tag-splitter";

function collect() {
  const visible: string[] = [];
  const thinking: string[] = [];
  const s = createThinkTagSplitter({
    onVisible: (t) => visible.push(t),
    onThinking: (t) => thinking.push(t),
  });
  return {
    s,
    visibleText: () => visible.join(""),
    thinkingText: () => thinking.join(""),
  };
}

describe("createThinkTagSplitter", () => {
  it("passes through plain text untouched (no tags)", () => {
    const c = collect();
    c.s.push("hello world");
    c.s.flush();
    expect(c.visibleText()).toBe("hello world");
    expect(c.thinkingText()).toBe("");
  });

  it("routes a single <think> block to thinking and keeps the answer visible", () => {
    const c = collect();
    c.s.push("<think>I should reason about this</think>The answer is 42.");
    c.s.flush();
    expect(c.visibleText()).toBe("The answer is 42.");
    expect(c.thinkingText()).toBe("I should reason about this");
  });

  it("handles a tag split across chunk boundaries", () => {
    const c = collect();
    c.s.push("<thi");
    c.s.push("nk>reasoning</thi");
    c.s.push("nk>final");
    c.s.flush();
    expect(c.visibleText()).toBe("final");
    expect(c.thinkingText()).toBe("reasoning");
  });

  it("streams thinking and visible deltas incrementally", () => {
    const c = collect();
    c.s.push("<think>step 1 ");
    c.s.push("step 2</think>ans");
    c.s.push("wer");
    c.s.flush();
    expect(c.thinkingText()).toBe("step 1 step 2");
    expect(c.visibleText()).toBe("answer");
  });

  it("is case-insensitive on the tag", () => {
    const c = collect();
    c.s.push("<THINK>r</THINK>v");
    c.s.flush();
    expect(c.thinkingText()).toBe("r");
    expect(c.visibleText()).toBe("v");
  });

  it("treats an unterminated <think> as thinking, flushing leftover", () => {
    const c = collect();
    c.s.push("<think>still reasoning");
    c.s.flush();
    expect(c.thinkingText()).toBe("still reasoning");
    expect(c.visibleText()).toBe("");
  });

  it("does not swallow a lone '<' that is not a tag", () => {
    const c = collect();
    c.s.push("a < b and c");
    c.s.flush();
    expect(c.visibleText()).toBe("a < b and c");
    expect(c.thinkingText()).toBe("");
  });
});
