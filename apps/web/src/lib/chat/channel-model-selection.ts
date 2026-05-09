export interface ChannelModelSelection {
  modelSelection: string;
  routerType: string;
}

export const DEFAULT_CHANNEL_MODEL_SELECTION: ChannelModelSelection = {
  modelSelection: "magi_smart_routing",
  routerType: "standard",
};

const STORAGE_KEY = (botId: string) => `magi:channelModelSelections:${botId}`;

const MODEL_SELECTION_TO_RUNTIME_MODEL: Record<string, string> = {
  haiku: "anthropic/claude-haiku-4-5",
  sonnet: "anthropic/claude-sonnet-4-6",
  opus: "anthropic/claude-opus-4-7",
  smart_routing: "anthropic/claude-sonnet-4-6",
  gpt_smart_routing: "openai/gpt-5.4-mini",
  gpt_5_nano: "openai/gpt-5.4-nano",
  gpt_5_mini: "openai/gpt-5.4-mini",
  gpt_5_1: "openai/gpt-5.4-mini",
  gpt_5_4: "openai/gpt-5.5",
  gpt_5_5: "openai/gpt-5.5",
  gpt_5_5_pro: "openai/gpt-5.5-pro",
  codex: "openai-codex/gpt-5.5",
  kimi_k2_5: "fireworks/kimi-k2p6",
  minimax_m2_5: "fireworks/minimax-m2p7",
  minimax_m2_7: "fireworks/minimax-m2p7",
  gemini_3_1_flash: "google/gemini-3.1-flash-lite-preview",
  gemini_3_1_flash_lite: "google/gemini-3.1-flash-lite-preview",
  gemini_3_1_pro: "google/gemini-3.1-pro-preview",
  local_gemma_fast: "local/gemma-fast",
  local_gemma_max: "local/gemma-max",
  local_qwen_uncensored: "local/qwen-uncensored",
};

function readSelections(botId: string): Record<string, ChannelModelSelection> {
  if (typeof localStorage === "undefined") return {};
  try {
    const raw = localStorage.getItem(STORAGE_KEY(botId));
    if (!raw) return {};
    const parsed = JSON.parse(raw) as Record<string, ChannelModelSelection>;
    return parsed && typeof parsed === "object" ? parsed : {};
  } catch {
    return {};
  }
}

function normalizeSelection(
  value: ChannelModelSelection | null | undefined,
  fallback: ChannelModelSelection,
): ChannelModelSelection {
  if (!value || typeof value.modelSelection !== "string") return fallback;
  return {
    modelSelection: value.modelSelection,
    routerType: typeof value.routerType === "string" ? value.routerType : fallback.routerType,
  };
}

export function getChannelModelSelection(
  botId: string,
  channelName: string,
  fallback: ChannelModelSelection,
): ChannelModelSelection {
  const selections = readSelections(botId);
  return normalizeSelection(selections[channelName], fallback);
}

export function setChannelModelSelection(
  botId: string,
  channelName: string,
  selection: ChannelModelSelection,
): void {
  if (typeof localStorage === "undefined") return;
  try {
    const selections = readSelections(botId);
    selections[channelName] = normalizeSelection(selection, DEFAULT_CHANNEL_MODEL_SELECTION);
    localStorage.setItem(STORAGE_KEY(botId), JSON.stringify(selections));
  } catch {
    // Ignore storage quota and private-mode failures; the in-memory picker state still updates.
  }
}

export function channelModelSelectionToRuntimeModel(selection: ChannelModelSelection): string {
  if (selection.modelSelection === "magi_smart_routing") {
    return selection.routerType === "big_dic"
      ? "big-dic-router/auto"
      : "magi-smart-router/auto";
  }
  return MODEL_SELECTION_TO_RUNTIME_MODEL[selection.modelSelection] ?? selection.modelSelection;
}
