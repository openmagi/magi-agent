// Public API of the chat-core module (OSS source of truth).
// Pure, framework-agnostic. No react/next/supabase/privy/@-app imports.
// Populated as files are ported. Vendored to clawy/src/lib/chat-core.
export * from "./accepted-injections";
export * from "./agent-activity";
export * from "./assistant-dedupe";
export * from "./attachment-marker";
export * from "./attachments";
export * from "./build-channel-export";
export * from "./build-plaintext-persist-rows";
export * from "./channel-i18n";
export * from "./channel-memory-mode";
export * from "./channel-model-selection";
export * from "./channel-navigation";
export * from "./channel-order";
export * from "./channel-runtime-cache";
export * from "./chat-store";
export * from "./clipboard-images";
export * from "./control-questions";
export * from "./e2ee";
export * from "./empty-response";
export * from "./export";
export * from "./file-drop";
export * from "./history-backed-channels";
export * from "./history-envelope";
export * from "./history-merge";
export * from "./kb-context-marker";
export * from "./kb-send";
export * from "./kb-uploads";
export * from "./live-run";
export * from "./live-soft-wrap";
export * from "./live-transcript";
export * from "./load-channel-history";
export * from "./local-cancel-suppression";
export * from "./message-copy";
export * from "./message-language";
export * from "./message-order";
export * from "./mission-ledger-events";
export * from "./mission-work-queue";
export * from "./missions";
export * from "./model-context";
export * from "./openmagi-determinism-state";
export * from "./openmagi-runtime-events";
export * from "./plaintext-sentinel";
export * from "./public-tool-preview";
export * from "./queue-constants";
export * from "./recipe-selection";
export * from "./research-evidence";
// `reset-counter` and `chat-store` both export `getResetCounter`,
// `getResetBoundaryTimestamp`, and `syncResetCounters`. The reset-counter copies
// are local helpers ("local to this surface") consumed only inside that module;
// the public, consumer-facing implementations live in `./chat-store`. Re-export
// reset-counter's unique members only so the public names resolve to chat-store.
export {
  buildResetSessionKey,
  buildResetDivider,
  type IncrementResetCounterOptions,
  type SyncResetCountersOptions,
  incrementResetCounter,
} from "./reset-counter";
export * from "./response-usage";
export * from "./send-policy";
export * from "./server-channels";
export * from "./server-reconcile";
// `stream-chat-reducer` re-exports all members except `ControlRequestState`,
// which collides with the type of the same name in `./types`. The reducer's
// `ControlRequestState` interface remains importable directly from
// `./stream-chat-reducer` (matches its direct-import consumers).
export {
  type ToolCardState,
  type TurnPhaseState,
  type TerminalState,
  type ActivityItem,
  type StreamChatState,
  initialStreamChatState,
  beginStreamChatTurn,
  foldRuntimeEvent,
  foldRuntimeEvents,
} from "./stream-chat-reducer";
export * from "./stream-state-to-channel-state";
export * from "./user-history-persistence";
export * from "./work-console";
export * from "./work-state";
export * from "./streaming-delta-buffer";
export * from "./think-tag-splitter";
export * from "./types";
export * from "./visible-content";
export * from "./work-console-motion";
