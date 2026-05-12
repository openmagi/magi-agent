import { describe, expect, it } from "vitest";
import { stripAnsi } from "./terminalUi.js";
import { TerminalSseWriter } from "./terminalWriter.js";

class MemoryOutput {
  value = "";

  write(chunk: string): boolean {
    this.value += chunk;
    return true;
  }
}

describe("TerminalSseWriter", () => {
  it("collapses thinking by default and labels assistant output", () => {
    const output = new MemoryOutput();
    const writer = new TerminalSseWriter({ output });

    writer.start();
    writer.agent({ type: "thinking_delta", delta: "private chain" });
    writer.agent({ type: "text_delta", delta: "Visible answer" });
    writer.end();

    const text = stripAnsi(output.value);
    expect(text).toContain("∴ Thinking");
    expect(text).not.toContain("private chain");
    expect(text).toContain("Magi");
    expect(text).toContain("Visible answer");
  });

  it("renders tool lifecycle as compact progress lines", () => {
    const output = new MemoryOutput();
    const writer = new TerminalSseWriter({ output });

    writer.agent({
      type: "tool_start",
      id: "tool-1",
      name: "FileWrite",
      input_preview: "workspace/report.md",
    });
    writer.agent({
      type: "tool_progress",
      id: "tool-1",
      label: "Writing file",
    });
    writer.agent({
      type: "tool_end",
      id: "tool-1",
      status: "ok",
      output_preview: "created",
      durationMs: 42,
    });

    const text = stripAnsi(output.value);
    expect(text).toContain("Running FileWrite");
    expect(text).toContain("workspace/report.md");
    expect(text).toContain("Writing file");
    expect(text).toContain("Done FileWrite");
    expect(text).not.toContain("[tool]");
  });

  it("renders public runtime trace without exposing raw event payloads", () => {
    const output = new MemoryOutput();
    const writer = new TerminalSseWriter({ output });

    writer.agent({
      type: "runtime_trace",
      turnId: "turn-1",
      phase: "verifier_blocked",
      severity: "warning",
      title: "Runtime verifier blocked completion",
      reasonCode: "ARTIFACT_DELIVERY_REQUIRED",
      requiredAction: "Deliver the requested artifact before answering.",
    });

    const text = stripAnsi(output.value);
    expect(text).toContain("Runtime verifier blocked completion");
    expect(text).toContain("Deliver the requested artifact before answering.");
    expect(text).not.toContain("ARTIFACT_DELIVERY_REQUIRED");
  });
});
