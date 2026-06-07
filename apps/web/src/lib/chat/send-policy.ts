export type StreamingComposerMode = "queue" | "steer";
export type StreamingSendMode = "queue" | "inject";

interface StreamingSendPolicyInput {
  hasFiles: boolean;
  hasKbContext?: boolean;
  requestedMode?: StreamingComposerMode;
  /** @deprecated Use requestedMode: "steer". Kept for branch compatibility. */
  allowMidTurnInjection?: boolean;
}

export function canSteerMidTurn({
  hasFiles,
  hasKbContext = false,
}: Pick<StreamingSendPolicyInput, "hasFiles" | "hasKbContext">): boolean {
  return !hasFiles && !hasKbContext;
}

export function isStreamingComposerBlockedByQueue({
  queueFull,
  mode,
}: {
  queueFull: boolean;
  mode: StreamingComposerMode;
}): boolean {
  return queueFull && mode !== "steer";
}

export function getStreamingSendMode({
  hasFiles,
  hasKbContext = false,
  requestedMode,
  allowMidTurnInjection = false,
}: StreamingSendPolicyInput): StreamingSendMode {
  const wantsSteering = requestedMode === "steer" || (!requestedMode && allowMidTurnInjection);
  if (wantsSteering && canSteerMidTurn({ hasFiles, hasKbContext })) return "inject";
  return "queue";
}
