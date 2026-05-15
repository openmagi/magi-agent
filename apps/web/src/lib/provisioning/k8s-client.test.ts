import { describe, it, expect } from "vitest";
import {
  BOT_NAMESPACE_LABEL_KEY,
  BOT_NAMESPACE_LABEL_VALUE,
  verifyBotNamespaceLabel,
} from "./k8s-client";

describe("verifyBotNamespaceLabel", () => {
  it("accepts label map with clawy-bot=true", () => {
    expect(
      verifyBotNamespaceLabel("clawy-bot-abc", {
        [BOT_NAMESPACE_LABEL_KEY]: BOT_NAMESPACE_LABEL_VALUE,
      }),
    ).toBeNull();
  });

  it("accepts label map with clawy-bot=true plus unrelated labels", () => {
    expect(
      verifyBotNamespaceLabel("clawy-bot-abc", {
        [BOT_NAMESPACE_LABEL_KEY]: BOT_NAMESPACE_LABEL_VALUE,
        "kubernetes.io/metadata.name": "clawy-bot-abc",
        owner: "kevin",
      }),
    ).toBeNull();
  });

  it("rejects empty label map", () => {
    const err = verifyBotNamespaceLabel("clawy-bot-abc", {});
    expect(err).not.toBeNull();
    expect(err).toContain("clawy-bot-abc");
    expect(err).toContain("clawy-bot=true");
    expect(err).toContain("NetworkPolicy");
  });

  it("rejects undefined label map", () => {
    const err = verifyBotNamespaceLabel("clawy-bot-abc", undefined);
    expect(err).not.toBeNull();
    expect(err).toContain("clawy-bot-abc");
  });

  it("rejects null label map", () => {
    const err = verifyBotNamespaceLabel("clawy-bot-abc", null);
    expect(err).not.toBeNull();
    expect(err).toContain("clawy-bot-abc");
  });

  it("rejects label map with clawy-bot=false", () => {
    const err = verifyBotNamespaceLabel("clawy-bot-abc", {
      [BOT_NAMESPACE_LABEL_KEY]: "false",
    });
    expect(err).not.toBeNull();
    expect(err).toContain("clawy-bot=true");
  });

  it("rejects label map with wrong-cased value", () => {
    // K8s labels are case-sensitive; "True" != "true"
    const err = verifyBotNamespaceLabel("clawy-bot-abc", {
      [BOT_NAMESPACE_LABEL_KEY]: "True",
    });
    expect(err).not.toBeNull();
  });

  it("rejects label map with other labels but missing clawy-bot", () => {
    const err = verifyBotNamespaceLabel("clawy-bot-abc", {
      "kubernetes.io/metadata.name": "clawy-bot-abc",
      owner: "kevin",
    });
    expect(err).not.toBeNull();
    expect(err).toContain("kevin"); // error includes got-labels context
  });

  it("exports the expected constants", () => {
    expect(BOT_NAMESPACE_LABEL_KEY).toBe("clawy-bot");
    expect(BOT_NAMESPACE_LABEL_VALUE).toBe("true");
  });
});
