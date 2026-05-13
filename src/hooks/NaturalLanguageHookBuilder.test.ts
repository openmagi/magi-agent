import { describe, it, expect } from "vitest";

import {
  buildHookFromNaturalLanguage,
  detectLanguage,
  sanitizeName,
  parseIntentResponse,
  generateHookCode,
  generateYamlConfig,
  generateFixtureYaml,
  type NLHookLLM,
  type ParsedIntent,
  type GeneratedHookConfig,
} from "./NaturalLanguageHookBuilder.js";

/* ------------------------------------------------------------------ */
/*  Mock LLM helper                                                    */
/* ------------------------------------------------------------------ */

function mockLLM(response: Record<string, unknown>): NLHookLLM {
  return {
    async complete(): Promise<string> {
      return JSON.stringify(response);
    },
  };
}

function makeMedicalIntent(): Record<string, unknown> {
  return {
    name: "drug-dosage-safety",
    category: "safety",
    hookPoint: "beforeCommit",
    blocking: true,
    description_en:
      "Block responses containing drug dosage outside safe ranges",
    checkLogic:
      "Inspect assistant text for drug dosage mentions and verify they fall within established safe ranges",
    isSimpleClassifier: true,
    classifierPrompt:
      "Classify whether the response contains drug dosage information outside established safe ranges.",
    classifierOutputFields: {
      has_unsafe_dosage: "boolean — true if unsafe dosage detected",
      drug_name: "string — name of the drug if detected",
    },
  };
}

function makeFinancialIntent(): Record<string, unknown> {
  return {
    name: "investment-advice-disclaimer",
    category: "compliance",
    hookPoint: "beforeCommit",
    blocking: false,
    description_en:
      "Warn when responses contain specific investment advice without disclaimer",
    checkLogic:
      "Check if assistant text contains investment advice and whether a disclaimer is present",
    isSimpleClassifier: true,
    classifierPrompt:
      "Classify whether the response contains specific investment advice without an appropriate disclaimer.",
    classifierOutputFields: {
      has_investment_advice: "boolean — true if investment advice present",
      has_disclaimer: "boolean — true if disclaimer present",
    },
  };
}

function makeToolGateIntent(): Record<string, unknown> {
  return {
    name: "block-rm-rf",
    category: "safety",
    hookPoint: "beforeToolUse",
    blocking: true,
    description_en: "Block dangerous rm -rf commands",
    checkLogic:
      "Check if a Bash tool call contains rm -rf with a root or broad path",
    isSimpleClassifier: false,
  };
}

function makeKoreanModerationIntent(): Record<string, unknown> {
  return {
    name: "profanity-filter-ko",
    category: "moderation",
    hookPoint: "beforeCommit",
    blocking: true,
    description_en: "Block responses containing Korean profanity",
    checkLogic:
      "Scan the assistant text for Korean profanity and vulgar expressions",
    isSimpleClassifier: true,
    classifierPrompt:
      "Classify whether the response contains Korean profanity or vulgar expressions.",
    classifierOutputFields: {
      has_profanity: "boolean — true if profanity detected",
      severity: "string — low/medium/high",
    },
  };
}

/* ------------------------------------------------------------------ */
/*  detectLanguage                                                     */
/* ------------------------------------------------------------------ */

describe("detectLanguage", () => {
  it("detects Korean text", () => {
    expect(detectLanguage("약물 용량이 안전 범위를 벗어나면")).toBe("ko");
  });

  it("detects English text", () => {
    expect(detectLanguage("Block responses with drug dosage")).toBe("en");
  });

  it("detects mixed text as Korean when Korean chars present", () => {
    expect(detectLanguage("투자 조언 investment advice")).toBe("ko");
  });

  it("returns en for empty string", () => {
    expect(detectLanguage("")).toBe("en");
  });
});

/* ------------------------------------------------------------------ */
/*  sanitizeName                                                       */
/* ------------------------------------------------------------------ */

describe("sanitizeName", () => {
  it("converts to kebab-case", () => {
    expect(sanitizeName("Drug Dosage Safety")).toBe("drug-dosage-safety");
  });

  it("removes non-alphanumeric characters", () => {
    expect(sanitizeName("block_rm_rf!!!")).toBe("block-rm-rf");
  });

  it("truncates to 40 chars", () => {
    const long = "a".repeat(50);
    expect(sanitizeName(long).length).toBeLessThanOrEqual(40);
  });

  it("collapses multiple dashes", () => {
    expect(sanitizeName("a---b---c")).toBe("a-b-c");
  });

  it("strips leading/trailing dashes", () => {
    expect(sanitizeName("--test--")).toBe("test");
  });
});

/* ------------------------------------------------------------------ */
/*  parseIntentResponse                                                */
/* ------------------------------------------------------------------ */

describe("parseIntentResponse", () => {
  it("parses a valid medical safety intent", () => {
    const intent = parseIntentResponse(JSON.stringify(makeMedicalIntent()));
    expect(intent.name).toBe("drug-dosage-safety");
    expect(intent.category).toBe("safety");
    expect(intent.hookPoint).toBe("beforeCommit");
    expect(intent.blocking).toBe(true);
    expect(intent.isSimpleClassifier).toBe(true);
    expect(intent.classifierPrompt).toBeTruthy();
    expect(intent.classifierOutputFields).toBeDefined();
  });

  it("parses a tool gate intent without classifier", () => {
    const intent = parseIntentResponse(JSON.stringify(makeToolGateIntent()));
    expect(intent.name).toBe("block-rm-rf");
    expect(intent.hookPoint).toBe("beforeToolUse");
    expect(intent.isSimpleClassifier).toBe(false);
    expect(intent.classifierPrompt).toBeUndefined();
  });

  it("handles markdown-fenced JSON", () => {
    const fenced = `\`\`\`json\n${JSON.stringify(makeMedicalIntent())}\n\`\`\``;
    const intent = parseIntentResponse(fenced);
    expect(intent.name).toBe("drug-dosage-safety");
  });

  it("defaults category on invalid value", () => {
    const data = { ...makeMedicalIntent(), category: "invalid" };
    const intent = parseIntentResponse(JSON.stringify(data));
    expect(intent.category).toBe("custom");
  });

  it("defaults hookPoint on invalid value", () => {
    const data = { ...makeMedicalIntent(), hookPoint: "nonexistent" };
    const intent = parseIntentResponse(JSON.stringify(data));
    expect(intent.hookPoint).toBe("beforeCommit");
  });

  it("throws on invalid JSON", () => {
    expect(() => parseIntentResponse("not json")).toThrow();
  });
});

/* ------------------------------------------------------------------ */
/*  generateHookCode                                                   */
/* ------------------------------------------------------------------ */

describe("generateHookCode", () => {
  it("generates valid TypeScript for a blocking safety hook", () => {
    const intent = parseIntentResponse(JSON.stringify(makeMedicalIntent()));
    const code = generateHookCode(intent);

    expect(code).toContain('name: "drug-dosage-safety"');
    expect(code).toContain('point: "beforeCommit"');
    expect(code).toContain("blocking: true");
    expect(code).toContain("priority: 10"); // safety = 10
    expect(code).toContain("export default hook");
    expect(code).toContain("RegisteredHook");
  });

  it("generates a non-blocking compliance hook", () => {
    const intent = parseIntentResponse(JSON.stringify(makeFinancialIntent()));
    const code = generateHookCode(intent);

    expect(code).toContain("blocking: false");
    expect(code).toContain("priority: 20"); // compliance = 20
    expect(code).toContain("failOpen: true");
  });

  it("generates a beforeToolUse hook", () => {
    const intent = parseIntentResponse(JSON.stringify(makeToolGateIntent()));
    const code = generateHookCode(intent);

    expect(code).toContain('point: "beforeToolUse"');
    expect(code).toContain("priority: 10"); // safety = 10
  });

  it("generates a moderation hook", () => {
    const intent = parseIntentResponse(
      JSON.stringify(makeKoreanModerationIntent()),
    );
    const code = generateHookCode(intent);

    expect(code).toContain("priority: 30"); // moderation = 30
    expect(code).toContain("blocking: true");
  });
});

/* ------------------------------------------------------------------ */
/*  generateYamlConfig                                                 */
/* ------------------------------------------------------------------ */

describe("generateYamlConfig", () => {
  it("generates YAML with hook overrides", () => {
    const intent = parseIntentResponse(JSON.stringify(makeMedicalIntent()));
    const yaml = generateYamlConfig(intent);

    expect(yaml).toContain("hooks:");
    expect(yaml).toContain("overrides:");
    expect(yaml).toContain("drug-dosage-safety:");
    expect(yaml).toContain("enabled: true");
    expect(yaml).toContain("priority: 10");
    expect(yaml).toContain("blocking: true");
  });

  it("includes classifier dimension when provided", () => {
    const intent = parseIntentResponse(JSON.stringify(makeMedicalIntent()));
    const dim = {
      name: "drug-dosage-safety",
      phase: "final_answer" as const,
      prompt: "Classify drug dosage safety",
      output_schema: { has_unsafe_dosage: "boolean" },
    };
    const yaml = generateYamlConfig(intent, dim);

    expect(yaml).toContain("classifier:");
    expect(yaml).toContain("custom_dimensions:");
    expect(yaml).toContain('phase: "final_answer"');
    expect(yaml).toContain("Classify drug dosage safety");
    expect(yaml).toContain("has_unsafe_dosage:");
  });

  it("omits classifier section when no dimension provided", () => {
    const intent = parseIntentResponse(JSON.stringify(makeToolGateIntent()));
    const yaml = generateYamlConfig(intent);

    expect(yaml).not.toContain("classifier:");
  });
});

/* ------------------------------------------------------------------ */
/*  generateFixtureYaml                                                */
/* ------------------------------------------------------------------ */

describe("generateFixtureYaml", () => {
  it("generates fixture for beforeCommit hook", () => {
    const intent = parseIntentResponse(JSON.stringify(makeMedicalIntent()));
    const fixture = generateFixtureYaml(intent);

    expect(fixture).toContain("drug-dosage-safety");
    expect(fixture).toContain('point: "beforeCommit"');
    expect(fixture).toContain("assistantText:");
    expect(fixture).toContain('action: "continue"');
  });

  it("generates fixture for beforeToolUse hook", () => {
    const intent = parseIntentResponse(JSON.stringify(makeToolGateIntent()));
    const fixture = generateFixtureYaml(intent);

    expect(fixture).toContain('point: "beforeToolUse"');
    expect(fixture).toContain("toolName:");
  });
});

/* ------------------------------------------------------------------ */
/*  buildHookFromNaturalLanguage (integration with mock LLM)           */
/* ------------------------------------------------------------------ */

describe("buildHookFromNaturalLanguage", () => {
  it("generates a complete config for a medical safety rule (English)", async () => {
    const llm = mockLLM(makeMedicalIntent());
    const config = await buildHookFromNaturalLanguage(
      {
        description:
          "Block responses containing drug dosage outside safe ranges",
      },
      llm,
    );

    expect(config.name).toBe("drug-dosage-safety");
    expect(config.point).toBe("beforeCommit");
    expect(config.priority).toBe(10);
    expect(config.blocking).toBe(true);
    expect(config.hookCode).toContain("RegisteredHook");
    expect(config.classifierDimension).toBeDefined();
    expect(config.classifierDimension?.phase).toBe("final_answer");
    expect(config.yamlConfig).toContain("hooks:");
    expect(config.fixtureYaml).toContain("drug-dosage-safety");
  });

  it("generates a config for a financial compliance rule", async () => {
    const llm = mockLLM(makeFinancialIntent());
    const config = await buildHookFromNaturalLanguage(
      {
        description:
          "Warn when responses contain investment advice without disclaimer",
      },
      llm,
    );

    expect(config.name).toBe("investment-advice-disclaimer");
    expect(config.blocking).toBe(false);
    expect(config.priority).toBe(20);
    expect(config.classifierDimension).toBeDefined();
  });

  it("generates a config for a Korean input", async () => {
    const llm = mockLLM(makeKoreanModerationIntent());
    const config = await buildHookFromNaturalLanguage(
      {
        description: "욕설이 포함된 응답을 차단해줘",
      },
      llm,
    );

    expect(config.name).toBe("profanity-filter-ko");
    expect(config.point).toBe("beforeCommit");
    expect(config.blocking).toBe(true);
    expect(config.priority).toBe(30);
  });

  it("generates a config without classifier for complex logic", async () => {
    const llm = mockLLM(makeToolGateIntent());
    const config = await buildHookFromNaturalLanguage(
      {
        description: "Block dangerous rm -rf commands",
      },
      llm,
    );

    expect(config.name).toBe("block-rm-rf");
    expect(config.point).toBe("beforeToolUse");
    expect(config.classifierDimension).toBeUndefined();
  });

  it("respects explicit language parameter", async () => {
    let receivedUser = "";
    const llm: NLHookLLM = {
      async complete(_system: string, user: string): Promise<string> {
        receivedUser = user;
        return JSON.stringify(makeMedicalIntent());
      },
    };

    await buildHookFromNaturalLanguage(
      { description: "test rule", language: "ko" },
      llm,
    );

    expect(receivedUser).toContain("Korean");
  });

  it("throws on LLM failure", async () => {
    const llm: NLHookLLM = {
      async complete(): Promise<string> {
        throw new Error("LLM unavailable");
      },
    };

    await expect(
      buildHookFromNaturalLanguage(
        { description: "test" },
        llm,
      ),
    ).rejects.toThrow("LLM unavailable");
  });

  it("throws on unparseable LLM response", async () => {
    const llm: NLHookLLM = {
      async complete(): Promise<string> {
        return "This is not JSON";
      },
    };

    await expect(
      buildHookFromNaturalLanguage(
        { description: "test" },
        llm,
      ),
    ).rejects.toThrow();
  });

  it("sets classifier phase to 'request' for beforeLLMCall hooks", async () => {
    const intent = {
      ...makeMedicalIntent(),
      hookPoint: "beforeLLMCall",
    };
    const llm = mockLLM(intent);
    const config = await buildHookFromNaturalLanguage(
      { description: "test" },
      llm,
    );

    expect(config.classifierDimension?.phase).toBe("request");
  });

  it("sets classifier phase to 'request' for beforeTurnStart hooks", async () => {
    const intent = {
      ...makeMedicalIntent(),
      hookPoint: "beforeTurnStart",
    };
    const llm = mockLLM(intent);
    const config = await buildHookFromNaturalLanguage(
      { description: "test" },
      llm,
    );

    expect(config.classifierDimension?.phase).toBe("request");
  });
});
