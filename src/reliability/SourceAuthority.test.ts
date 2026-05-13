import { describe, expect, it } from "vitest";
import {
  buildSourceAuthorityPromptBlock,
  detectCurrentTurnSourceKinds,
  resolveEffectiveLongTermMemoryPolicy,
} from "./SourceAuthority.js";

describe("SourceAuthority", () => {
  it("detects structured current-turn sources from prompt addenda and user blocks", () => {
    const kinds = detectCurrentTurnSourceKinds({
      system: [
        "<current-turn-source kind=\"selected_kb\" authority=\"L1\">",
        "<kb-context>selected KB body</kb-context>",
        "</current-turn-source>",
      ].join("\n"),
      userText:
        "<current-turn-source kind=\"attachment\" authority=\"L1\">\n[file]\n</current-turn-source>",
      hasImages: true,
    });

    expect(kinds).toEqual(["selected_kb", "attachment", "image"]);
  });

  it("downgrades long-term memory to background-only when current sources are authoritative", () => {
    const policy = resolveEffectiveLongTermMemoryPolicy({
      classifierPolicy: "normal",
      classifierCurrentSourcesAuthoritative: false,
      currentSourceKinds: ["selected_kb"],
    });

    expect(policy).toBe("background_only");
  });

  it("honors classifier-disabled memory even when no current source tag is present", () => {
    const policy = resolveEffectiveLongTermMemoryPolicy({
      classifierPolicy: "disabled",
      classifierCurrentSourcesAuthoritative: false,
      currentSourceKinds: [],
    });

    expect(policy).toBe("disabled");
  });

  it("renders a hidden source authority prompt contract", () => {
    const block = buildSourceAuthorityPromptBlock({
      turnId: "turn-1",
      currentSourceKinds: ["attachment"],
      longTermMemoryPolicy: "background_only",
      classifierReason: "Current attachment is the basis.",
    });

    expect(block).toContain('<source_authority_contract hidden="true"');
    expect(block).toContain("L0 latest_user_message");
    expect(block).toContain("L1 current_turn_sources");
    expect(block).toContain("long_term_memory_policy: background_only");
  });
});
