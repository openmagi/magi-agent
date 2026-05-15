import { describe, expect, it } from "vitest";
import {
  BOT_HARD_DELETE_DATA_TABLES,
  BOT_HARD_DELETE_PRESERVED_TABLES,
  buildBotDeletionStoragePaths,
  buildBotTombstoneUpdate,
  isMissingBotHardDeleteTableError,
  shouldReprovisionForBotSettings,
} from "./bot-service";

describe("shouldReprovisionForBotSettings", () => {
  const activePlatformBot = {
    status: "active",
    model_selection: "sonnet",
    api_key_mode: "platform_credits",
    router_type: "standard",
    language: "auto",
    agent_rules: null,
  };

  it("does not reprovision active platform-credit bots for a model-only change", () => {
    expect(
      shouldReprovisionForBotSettings({
        botRow: activePlatformBot,
        updates: { model_selection: "opus" },
        body: { model_selection: "opus" },
      }),
    ).toBe(false);
  });

  it("keeps BYOK model changes on the reprovision path", () => {
    expect(
      shouldReprovisionForBotSettings({
        botRow: { ...activePlatformBot, api_key_mode: "byok" },
        updates: { model_selection: "opus" },
        body: { model_selection: "opus" },
      }),
    ).toBe(true);
  });

  it("does not reprovision active platform-credit bots when router shape changes", () => {
    expect(
      shouldReprovisionForBotSettings({
        botRow: activePlatformBot,
        updates: { router_type: "big_dic" },
        body: { router_type: "big_dic" },
      }),
    ).toBe(false);
  });
});

describe("bot tombstone hard delete boundaries", () => {
  it("deletes bot-owned data tables while preserving usage and audit references", () => {
    expect(BOT_HARD_DELETE_DATA_TABLES).toEqual(
      expect.arrayContaining([
        "chat_messages",
        "app_channel_messages",
        "push_messages",
        "chat_reset_counters",
        "chat_message_deletions",
        "chat_exports",
        "chat_attachments",
        "app_channels",
        "knowledge_documents",
        "knowledge_collections",
        "conversion_jobs",
        "consultation_artifacts",
        "consultation_jobs",
        "gateway_tokens",
        "bot_email_inboxes",
        "bot_x402_inboxes",
        "discord_bot_mappings",
        "bot_wallet_policies",
        "learned_skills",
        "skill_executions",
        "user_interactions",
        "sub_agents_cache",
      ]),
    );

    expect(BOT_HARD_DELETE_DATA_TABLES).not.toEqual(
      expect.arrayContaining([...BOT_HARD_DELETE_PRESERVED_TABLES]),
    );
    expect(BOT_HARD_DELETE_PRESERVED_TABLES).toEqual(
      expect.arrayContaining([
        "usage_logs",
        "credit_transactions",
        "service_usage_logs",
        "wallet_usage_logs",
        "stripe_webhook_events",
      ]),
    );
  });

  it("collects exact storage paths for bot-owned chat, KB, converter, and consultation data", () => {
    const botId = "186bf3d7-7d00-4c8b-86c9-c1734c66a1e4";

    const paths = buildBotDeletionStoragePaths({
      botId,
      chatAttachments: [
        { storage_path: `${botId}/general/a.txt` },
        { storage_path: "" },
        { storage_path: `${botId}/general/a.txt` },
      ],
      knowledgeOriginalFiles: [
        { name: "1700000000000_guide.pdf" },
        { name: `knowledge/${botId}/collection/notion_page.md` },
        { name: "" },
      ],
      conversionJobs: [
        {
          source_storage_path: `${botId}/converter/source.docx`,
          result_storage_path: `${botId}/converter/result.pdf`,
        },
      ],
      consultationJobs: [
        { source_storage_path: `${botId}/consultations/audio.m4a` },
      ],
      consultationArtifacts: [
        { storage_path: `${botId}/consultations/memo.md` },
      ],
    });

    expect(paths).toEqual([
      `${botId}/general/a.txt`,
      `knowledge/${botId}/1700000000000_guide.pdf`,
      `knowledge/${botId}/collection/notion_page.md`,
      `${botId}/converter/source.docx`,
      `${botId}/converter/result.pdf`,
      `${botId}/consultations/audio.m4a`,
      `${botId}/consultations/memo.md`,
    ]);
    expect(paths).not.toContain(`knowledge/${botId}/knowledge/${botId}/collection/notion_page.md`);
  });

  it("builds a credential-stripped deleted bot tombstone update", () => {
    const now = "2026-05-09T12:00:00.000Z";

    expect(buildBotTombstoneUpdate(now)).toMatchObject({
      status: "deleted",
      provisioning_step: "hard_delete_requested",
      error_message: null,
      health_status: "unknown",
      telegram_bot_token: null,
      telegram_bot_username: null,
      telegram_user_handle: null,
      telegram_owner_id: null,
      discord_bot_token: null,
      discord_bot_username: null,
      privy_wallet_id: null,
      privy_wallet_address: null,
      registry_agent_id: null,
      registry_tx_hash: null,
      agent_skill_md: null,
      agent_endpoint_url: null,
      storage_used_bytes: 0,
      kb_storage_used_bytes: 0,
      updated_at: now,
    });
  });

  it("treats Supabase schema-cache missing-table errors as skippable cleanup drift", () => {
    expect(
      isMissingBotHardDeleteTableError({
        code: "PGRST205",
        message:
          "Could not find the table 'public.discord_bot_mappings' in the schema cache",
      }),
    ).toBe(true);

    expect(
      isMissingBotHardDeleteTableError({
        message:
          "Could not find the table 'public.discord_bot_mappings' in the schema cache",
      }),
    ).toBe(true);

    expect(
      isMissingBotHardDeleteTableError({
        code: "42501",
        message: "permission denied for table chat_messages",
      }),
    ).toBe(false);
  });

  it("keeps Privy wallet identifiers on the tombstone when wallet deletion fails", () => {
    const now = "2026-05-09T12:00:00.000Z";

    expect(
      buildBotTombstoneUpdate(now, {
        unresolvedPrivyWallet: {
          id: "wallet-1",
          address: "0xabc",
          chain: "ethereum",
        },
        errorMessage: "Privy wallet cleanup failed",
      }),
    ).toMatchObject({
      status: "deleted",
      error_message: "Privy wallet cleanup failed",
      privy_wallet_id: "wallet-1",
      privy_wallet_address: "0xabc",
      privy_wallet_chain: "ethereum",
    });
  });
});
