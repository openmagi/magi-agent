import { describe, expect, it } from "vitest";
import {
  EnsembleAnalyzer,
  createDefaultSecurityAnalyzers,
  type AnalysisContext,
  type AnalysisVerdict,
  type SecurityAnalyzer,
} from "./EnsembleAnalyzer.js";

function verdict(
  severity: AnalysisVerdict["severity"],
  analyzerName: string,
): AnalysisVerdict {
  return {
    severity,
    confidence: severity === "unknown" ? 0 : 1,
    reason: `${analyzerName} says ${severity}`,
    analyzerName,
  };
}

function analyzer(
  name: string,
  severity: AnalysisVerdict["severity"],
  delayMs = 0,
): SecurityAnalyzer {
  return {
    name,
    analyze: async () => {
      if (delayMs > 0) {
        await new Promise((resolve) => setTimeout(resolve, delayMs));
      }
      return verdict(severity, name);
    },
  };
}

const baseContext: AnalysisContext = {
  hookPoint: "beforeToolUse",
  content: "",
  toolName: "Bash",
  input: { command: "echo ok" },
};

describe("EnsembleAnalyzer", () => {
  it("returns pass when every analyzer passes", async () => {
    const ensemble = new EnsembleAnalyzer({
      analyzers: [analyzer("pattern", "pass"), analyzer("secret", "pass")],
    });

    const result = await ensemble.analyze(baseContext);

    expect(result.finalSeverity).toBe("pass");
    expect(result.errors).toEqual([]);
    expect(result.verdicts.map((v) => v.severity)).toEqual(["pass", "pass"]);
  });

  it("returns deny when any analyzer denies", async () => {
    const ensemble = new EnsembleAnalyzer({
      analyzers: [
        analyzer("pattern", "pass", 20),
        analyzer("policy", "deny", 5),
        analyzer("secret", "ask", 10),
      ],
    });

    const result = await ensemble.analyze(baseContext);

    expect(result.finalSeverity).toBe("deny");
    expect(result.verdicts.map((v) => v.analyzerName)).toEqual([
      "pattern",
      "policy",
      "secret",
    ]);
  });

  it("contributes deny and records an error when an analyzer throws", async () => {
    const ensemble = new EnsembleAnalyzer({
      analyzers: [
        analyzer("pattern", "pass"),
        {
          name: "broken",
          analyze: async () => {
            throw new Error("classifier unavailable");
          },
        },
      ],
    });

    const result = await ensemble.analyze(baseContext);

    expect(result.finalSeverity).toBe("deny");
    expect(result.errors).toEqual([
      { analyzerName: "broken", error: "classifier unavailable" },
    ]);
    expect(result.verdicts).toContainEqual({
      severity: "deny",
      confidence: 0,
      reason: "analyzer failed closed: classifier unavailable",
      analyzerName: "broken",
    });
  });

  it("propagates unknown when strict mode is enabled", async () => {
    const ensemble = new EnsembleAnalyzer({
      analyzers: [analyzer("pattern", "pass"), analyzer("llm", "unknown")],
      propagateUnknown: true,
    });

    const result = await ensemble.analyze(baseContext);

    expect(result.finalSeverity).toBe("unknown");
    expect(result.propagatedUnknown).toBe(true);
  });

  it("ignores unknown when strict mode is disabled and uses remaining verdicts", async () => {
    const ensemble = new EnsembleAnalyzer({
      analyzers: [analyzer("pattern", "ask"), analyzer("llm", "unknown")],
      propagateUnknown: false,
    });

    const result = await ensemble.analyze(baseContext);

    expect(result.finalSeverity).toBe("ask");
    expect(result.propagatedUnknown).toBe(false);
  });

  it("does not include the LLM analyzer unless MAGI_LLM_ANALYZER=1", () => {
    const previous = process.env.MAGI_LLM_ANALYZER;
    delete process.env.MAGI_LLM_ANALYZER;
    try {
      const analyzers = createDefaultSecurityAnalyzers({
        hookPoint: "beforeToolUse",
        workspaceRoot: "/tmp/workspace",
      });

      expect(analyzers.map((a) => a.name)).not.toContain("llm-analyzer");
    } finally {
      if (previous === undefined) {
        delete process.env.MAGI_LLM_ANALYZER;
      } else {
        process.env.MAGI_LLM_ANALYZER = previous;
      }
    }
  });

  it("treats timed-out analyzers as fail-closed errors", async () => {
    const ensemble = new EnsembleAnalyzer({
      analyzers: [analyzer("slow", "pass", 50)],
      timeoutMs: 5,
    });

    const result = await ensemble.analyze(baseContext);

    expect(result.finalSeverity).toBe("deny");
    expect(result.errors).toEqual([
      { analyzerName: "slow", error: "analyzer timeout after 5ms" },
    ]);
  });
});
