import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { SettingsForm } from "./settings-form";
import type { BotSettingsData } from "@/types/entities";
import { LOCAL_LLM_MODEL_OPTIONS, isLocalLlmEnabledPlan } from "@/lib/models/local-llm";

const textProxy = new Proxy<Record<string, string>>(
  {},
  {
    get: (_target, prop) => (typeof prop === "string" ? prop : ""),
  },
);

const settingsPageMessages = {
  ...textProxy,
  resetBot: "Reset Bot",
  resetBotDescription: "Clear chat history and runtime memory while preserving KB, settings, integrations, and wallet.",
  deleteBot: "Delete Bot",
  deleteBotDescription: "Permanently delete this bot's runtime, chat, KB, integrations, and wallet policy. Historical usage remains available.",
  deletedBotReadOnly: "Deleted Bot",
  deletedBotReadOnlyDescription: "This bot is deleted. Historical usage remains available, but settings and runtime actions are disabled.",
  viewDeletedBotUsage: "View Usage",
};

vi.mock("next/dynamic", () => ({
  default: () => () => null,
}));

vi.mock("@/components/dashboard/agent-rules-section", () => ({
  AgentRulesSection: () => null,
}));

vi.mock("@/hooks/use-auth-fetch", () => ({
  useAuthFetch: () => vi.fn(),
}));

vi.mock("@/lib/analytics", () => ({
  trackSettingsSave: vi.fn(),
}));

vi.mock("@/lib/i18n", () => ({
  useMessages: () => ({
    accountDeletion: textProxy,
    agentRegistry: textProxy,
    apiKeys: textProxy,
    errors: textProxy,
    onboarding: textProxy,
    settingsPage: settingsPageMessages,
  }),
}));

const bot: BotSettingsData = {
  id: "bot_123",
  name: "OpenMagi_Bot",
  status: "active",
  model_selection: "smart_routing",
  router_type: "standard",
  api_key_mode: "platform_credits",
  bot_purpose: null,
  purpose_preset: "professional",
  telegram_bot_username: null,
  language: "auto",
  agent_skill_md: null,
  agent_rules: null,
  registry_agent_id: null,
  privy_wallet_address: null,
  has_anthropic_key: false,
  has_fireworks_key: false,
  has_openai_key: false,
  has_gemini_key: false,
  has_codex_token: false,
  disabled_skills: [],
};

describe("SettingsForm", () => {
  it("renders bot settings with the reset control and danger zone", () => {
    const html = renderToStaticMarkup(<SettingsForm bot={bot} />);
    expect(html).toContain("Reset Bot");
    expect(html).toContain("Clear chat history");
    expect(html).toContain("Delete Bot");
    expect(html).toContain("Historical usage remains available");
  });

  it("renders deleted bots as read-only historical usage targets", () => {
    const html = renderToStaticMarkup(<SettingsForm bot={{ ...bot, status: "deleted" }} />);

    expect(html).toContain("Deleted Bot");
    expect(html).toContain("settings and runtime actions are disabled");
    expect(html).toContain("View Usage");
    expect(html).not.toContain("Permanently delete this bot");
  });

  it("renders account-only settings with delete controls", () => {
    expect(() => renderToStaticMarkup(<SettingsForm bot={null} />)).not.toThrow();
  });

  it("keeps local beta model options available for Max users without exposing host details", () => {
    const html = renderToStaticMarkup(<SettingsForm bot={bot} subscriptionPlan="max" />);
    expect(isLocalLlmEnabledPlan("max")).toBe(true);
    expect(LOCAL_LLM_MODEL_OPTIONS.map((model) => model.label)).toEqual([
      "Gemma 4 Fast (beta)",
      "Gemma 4 Max (beta)",
      "Qwen 3.5 Uncensored (beta)",
    ]);
    expect(html).not.toContain("Mac Studio");
  });

  it("hides Mac Studio local models for Pro users", () => {
    const html = renderToStaticMarkup(<SettingsForm bot={bot} subscriptionPlan="pro" />);
    expect(html).not.toContain("Gemma 4 Fast (beta)");
    expect(html).not.toContain("Gemma 4 Max (beta)");
    expect(html).not.toContain("Qwen 3.5 Uncensored (beta)");
  });

  it("shows the selected standard router without rendering every model in the closed picker", () => {
    const html = renderToStaticMarkup(
      <SettingsForm
        bot={{ ...bot, model_selection: "clawy_smart_routing", router_type: "standard" }}
        subscriptionPlan="pro"
      />,
    );

    expect(html).toContain("Standard Router");
    expect(html).not.toContain("Claude Haiku 4.5");
    expect(html).not.toContain("GPT-5.5 Pro");
  });

  it("shows the selected advanced model in the closed picker", () => {
    const html = renderToStaticMarkup(
      <SettingsForm
        bot={{ ...bot, model_selection: "opus", router_type: "standard" }}
        subscriptionPlan="pro"
      />,
    );

    expect(html).toContain("Custom model");
    expect(html).toContain("Claude Opus 4.6");
    expect(html).not.toContain("GPT-5.5");
  });

  it("does not expose legacy smart-router sentinels as advanced model options", () => {
    const html = renderToStaticMarkup(
      <SettingsForm
        bot={{ ...bot, model_selection: "smart_routing", router_type: "standard" }}
        subscriptionPlan="pro"
      />,
    );

    expect(html).toContain("Custom model");
    expect(html).toContain("Claude Opus 4.6");
    expect(html).not.toContain("smart_routing");
  });
});
