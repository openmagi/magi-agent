import { readFileSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

describe("BotStatusCard", () => {
  it("does not render retired overview sections", () => {
    const source = readFileSync(path.join(process.cwd(), "src/components/dashboard/bot-status-card.tsx"), "utf8");

    expect(source).not.toContain("<SpecialistCommands");
    expect(source).not.toContain("<TrySuggestions");
    expect(source).not.toContain("<AgentMailX402Section");
    expect(source).not.toContain("<AgentCardSection");
    expect(source).not.toContain('from "./agentmail-x402-section"');
    expect(source).not.toContain('from "./agentcard-section"');
    expect(source).toContain("<AgentWalletSection");
  });

  it("does not render retired OpenClaw version update controls", () => {
    const source = readFileSync(path.join(process.cwd(), "src/components/dashboard/bot-status-card.tsx"), "utf8");

    expect(source).not.toContain("/check-update");
    expect(source).not.toContain("t.botCard.version");
    expect(source).not.toContain("t.botCard.checkForUpdates");
    expect(source).not.toContain("t.botCard.updateToVersion");
    expect(source).not.toContain("t.botCard.updateAvailableTag");
  });

  it("does not render bot-level model settings in the overview card", () => {
    const source = readFileSync(path.join(process.cwd(), "src/components/dashboard/bot-status-card.tsx"), "utf8");

    expect(source).not.toContain("getRouterDisplayName");
    expect(source).not.toContain("t.botCard.model");
    expect(source).not.toContain("bot.model_selection");
  });

  it("links from the overview card to the bot-scoped CLI guide", () => {
    const source = readFileSync(path.join(process.cwd(), "src/components/dashboard/bot-status-card.tsx"), "utf8");

    expect(source).toContain("`/dashboard/${bot.id}/cli`");
    expect(source).toContain("t.dashboard.cli");
  });
});
