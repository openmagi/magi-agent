export interface ChannelModelSelection {
  modelSelection: string;
  routerType: string;
}

export interface ChannelModelPreferenceRecord {
  model_selection?: string | null;
  router_type?: string | null;
  modelSelection?: string | null;
  routerType?: string | null;
}

/**
 * App context passed to chat-core helpers so they stay runtime-agnostic.
 * Hosted callers may omit it (defaults to "hosted" behavior). OSS local web
 * callers pass `{ runtime: "oss-local" }` so the resolver doesn't emit
 * hosted-only smart-router ids (`clawy-smart-router/auto`, `big-dic-router/auto`)
 * the OSS runtime can't serve.
 */
export interface ChannelModelAppContext {
  runtime?: "hosted" | "oss-local";
}

/**
 * Sentinel selection value meaning "no concrete model chosen yet". Hosted
 * resolves it to its smart-router; OSS callers must substitute the first
 * available configured-provider model before sending.
 */
export const UNRESOLVED_MODEL_SENTINEL = "clawy_smart_routing";

export const DEFAULT_CHANNEL_MODEL_SELECTION: ChannelModelSelection = {
  modelSelection: UNRESOLVED_MODEL_SENTINEL,
  routerType: "standard",
};

const STORAGE_KEY = (botId: string) => `clawy:channelModelSelections:${botId}`;

const MODEL_SELECTION_TO_RUNTIME_MODEL: Record<string, string> = {
  haiku: "anthropic/claude-haiku-4-5",
  sonnet: "anthropic/claude-sonnet-5",
  opus: "anthropic/claude-opus-4-8",
  smart_routing: "anthropic/claude-sonnet-5",
  gpt_smart_routing: "openai/gpt-5.4-mini",
  gpt_5_nano: "openai/gpt-5.4-nano",
  gpt_5_mini: "openai/gpt-5.4-mini",
  gpt_5_1: "openai/gpt-5.4-mini",
  gpt_5_4: "openai/gpt-5.5",
  gpt_5_5: "openai/gpt-5.5",
  gpt_5_5_pro: "openai/gpt-5.5-pro",
  codex: "openai-codex/gpt-5.5",
  kimi_k2_5: "fireworks/kimi-k2p6",
  kimi_k2_7_code: "fireworks/kimi-k2p7-code",
  glm_5_2: "fireworks/glm-5p2",
  minimax_m2_5: "fireworks/minimax-m2p7",
  minimax_m2_7: "fireworks/minimax-m2p7",
  gemini_3_5_flash: "google/gemini-3.5-flash",
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

export function channelModelSelectionToRuntimeModel(
  selection: ChannelModelSelection,
  appContext?: ChannelModelAppContext,
): string {
  if (selection.modelSelection === UNRESOLVED_MODEL_SENTINEL) {
    // OSS runtime has no smart router — return an empty string so the caller
    // can choose whether to substitute (picker auto-selects the first
    // configured-provider model) or block the send until the user picks.
    // Hosted (default) keeps its smart-router routing.
    if (appContext?.runtime === "oss-local") return "";
    return selection.routerType === "big_dic"
      ? "big-dic-router/auto"
      : "clawy-smart-router/auto";
  }
  return MODEL_SELECTION_TO_RUNTIME_MODEL[selection.modelSelection] ?? selection.modelSelection;
}

export function channelModelSelectionFromChannel(
  channel: ChannelModelPreferenceRecord | null | undefined,
): ChannelModelSelection | null {
  if (!channel) return null;
  const modelSelection =
    typeof channel.model_selection === "string"
      ? channel.model_selection
      : typeof channel.modelSelection === "string"
        ? channel.modelSelection
        : null;
  if (!modelSelection) return null;
  const routerType =
    typeof channel.router_type === "string"
      ? channel.router_type
      : typeof channel.routerType === "string"
        ? channel.routerType
        : DEFAULT_CHANNEL_MODEL_SELECTION.routerType;
  return { modelSelection, routerType };
}
