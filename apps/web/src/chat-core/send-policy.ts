export type StreamingSendMode = "queue" | "inject";

interface StreamingSendPolicyInput {
  hasFiles: boolean;
  hasKbContext?: boolean;
}

export function canInjectMidTurn({
  hasFiles,
  hasKbContext = false,
}: Pick<StreamingSendPolicyInput, "hasFiles" | "hasKbContext">): boolean {
  return !hasFiles && !hasKbContext;
}

export function isStreamingComposerBlockedByQueue({
  queueFull,
  canAttemptInject,
}: {
  queueFull: boolean;
  canAttemptInject: boolean;
}): boolean {
  return queueFull && !canAttemptInject;
}

export function getStreamingSendMode({
  hasFiles,
  hasKbContext = false,
}: StreamingSendPolicyInput): StreamingSendMode {
  return canInjectMidTurn({ hasFiles, hasKbContext }) ? "inject" : "queue";
}
