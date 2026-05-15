import { describe, expect, it } from "vitest";
import {
  TOSS_POS_HARNESS_RULE,
  parseHarnessRulesFromAgentConfig,
  renderAgentConfigWithHarnessRules,
  serializeHarnessRulesYaml,
  validateHarnessRuleDrafts,
  type AdminHarnessRuleDraft,
} from "./harness-rules";

describe("admin harness rule config helpers", () => {
  it("serializes structured tool-input match rules into agent.config.yaml harness_rules", () => {
    expect(serializeHarnessRulesYaml([TOSS_POS_HARNESS_RULE])).toContain(
      'input_command_matches: "integration\\\\.sh\\\\s+[\'\\"]?tossplace/my-merchants"',
    );
    expect(serializeHarnessRulesYaml([TOSS_POS_HARNESS_RULE])).toContain(
      'user_message_matches: "(토스|토스플레이스|POS).*(연결|연동|해제|등록|매장)"',
    );
  });

  it("replaces only the top-level harness_rules block while preserving other config", () => {
    const existing = [
      "model: gpt-5.5",
      "harness_rules:",
      "  - id: old-rule",
      "    trigger: beforeCommit",
      "limits:",
      "  max_turns: 20",
      "",
    ].join("\n");

    const next = renderAgentConfigWithHarnessRules(existing, [TOSS_POS_HARNESS_RULE]);

    expect(next).toContain("model: gpt-5.5");
    expect(next).toContain("limits:\n  max_turns: 20");
    expect(next).not.toContain("old-rule");
    expect(next).toContain("tossplace-merchant-grounding");
  });

  it("parses existing structured harness rules back into drafts", () => {
    const config = renderAgentConfigWithHarnessRules("", [TOSS_POS_HARNESS_RULE]);

    expect(parseHarnessRulesFromAgentConfig(config)).toEqual([
      expect.objectContaining({
        id: "tossplace-merchant-grounding",
        trigger: "beforeCommit",
        enforcement: "block_on_fail",
        toolName: "Bash",
        inputPath: "command",
        inputPattern: "integration\\.sh\\s+['\"]?tossplace/my-merchants",
      }),
    ]);
  });

  it("replaces empty inline harness_rules blocks", () => {
    const next = renderAgentConfigWithHarnessRules(
      "model: gpt-5.5\nharness_rules: []\n",
      [TOSS_POS_HARNESS_RULE],
    );

    expect(next).not.toContain("harness_rules: []");
    expect(next).toContain("model: gpt-5.5");
    expect(next).toContain("tossplace-merchant-grounding");
  });

  it("rejects regex patterns that the runtime will ignore", () => {
    const bad: AdminHarnessRuleDraft = {
      ...TOSS_POS_HARNESS_RULE,
      userMessageMatches: "(a+)+$",
    };

    expect(validateHarnessRuleDrafts([bad])).toContainEqual(
      expect.objectContaining({
        field: "userMessageMatches",
        message: "user_message_matches regex is too complex",
      }),
    );
  });
});
