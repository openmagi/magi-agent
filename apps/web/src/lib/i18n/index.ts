import { DEFAULT_LOCALE, type Locale } from "./types";

type ChatMessages = {
  chat: {
    openChannels: string;
    channelsTitle: string;
    resetSession: string;
    reset: string;
    dismiss: string;
    dropFilesToAttach: string;
    noChannelsTitle: string;
    noChannelsDescription: string;
    escAgainToStop: string;
    selectedKnowledgeSendsAfterRun: string;
    deleteMessagesTitle: string;
    deleteMessagesCount: string;
    deleteMessagesWarning: string;
    cancel: string;
    delete: string;
    messagesDeleted: string;
    undo: string;
    openTelegram: string;
  };
  botCard: {
    telegramBannerStart: string;
    telegramBannerConnect: string;
    provisioningOverlayTitle: string;
    provisioningOverlayDesc: string;
    telegramModalTitle: string;
    telegramModalSendStart: string;
    telegramModalSendStartDesc: string;
  };
  onboarding: {
    settingUp: string;
    provisioningStepCreatingResources: string;
    provisioningStepConfiguringBot: string;
    provisioningStepStartingServices: string;
    provisioningStepConnecting: string;
  };
};

const MESSAGES: ChatMessages = {
  chat: {
    openChannels: "Open channels",
    channelsTitle: "Channels",
    resetSession: "Reset session",
    reset: "Reset",
    dismiss: "Dismiss",
    dropFilesToAttach: "Drop files to attach",
    noChannelsTitle: "No channels",
    noChannelsDescription: "Create a channel to start chatting.",
    escAgainToStop: "ESC again to stop",
    selectedKnowledgeSendsAfterRun: "Selected knowledge is sent after the current run.",
    deleteMessagesTitle: "Delete messages",
    deleteMessagesCount: "{count} messages selected",
    deleteMessagesWarning: "This removes the selected messages from this device.",
    cancel: "Cancel",
    delete: "Delete",
    messagesDeleted: "Messages deleted",
    undo: "Undo",
    openTelegram: "Open @{username}",
  },
  botCard: {
    telegramBannerStart: "Open Telegram @{username} and send /start.",
    telegramBannerConnect: "Connect Telegram to use this bot from chat.",
    provisioningOverlayTitle: "Setting up bot",
    provisioningOverlayDesc: "The runtime is preparing your workspace.",
    telegramModalTitle: "Connect Telegram",
    telegramModalSendStart: "Send /start",
    telegramModalSendStartDesc: "Send {command} to connect this bot.",
  },
  onboarding: {
    settingUp: "Setting up",
    provisioningStepCreatingResources: "Creating resources",
    provisioningStepConfiguringBot: "Configuring bot",
    provisioningStepStartingServices: "Starting services",
    provisioningStepConnecting: "Connecting",
  },
};

function detectLocale(): Locale {
  const language =
    typeof navigator === "undefined" ? "" : navigator.language.toLowerCase();
  if (language.startsWith("ko")) return "ko";
  if (language.startsWith("ja")) return "ja";
  if (language.startsWith("zh")) return "zh";
  if (language.startsWith("es")) return "es";
  return DEFAULT_LOCALE;
}

export function useI18n(): { locale: Locale } {
  return { locale: detectLocale() };
}

export function useMessages(): ChatMessages {
  return MESSAGES;
}

export { DEFAULT_LOCALE, LOCALES, LOCALE_LABELS } from "./types";
export type { Locale } from "./types";
export type Messages = ChatMessages;
