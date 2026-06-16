export interface StreamingDeltaBatch {
  textDelta: string;
  thinkingDelta: string;
}

interface StreamingDeltaBufferScheduler {
  schedule: (callback: () => void) => number;
  cancel: (handle: number) => void;
}

export interface StreamingDeltaBuffer {
  appendText: (delta: string) => void;
  appendThinking: (delta: string) => void;
  flush: () => void;
  clear: () => void;
  dispose: () => void;
}

const STREAM_DELTA_FLUSH_MS = 16;

const defaultScheduler: StreamingDeltaBufferScheduler = {
  schedule: (callback) => {
    if (typeof window === "undefined") {
      return setTimeout(callback, STREAM_DELTA_FLUSH_MS) as unknown as number;
    }
    return window.setTimeout(callback, STREAM_DELTA_FLUSH_MS);
  },
  cancel: (handle) => {
    if (typeof window === "undefined") {
      clearTimeout(handle as unknown as ReturnType<typeof setTimeout>);
      return;
    }
    window.clearTimeout(handle);
  },
};

export function createStreamingDeltaBuffer(
  onFlush: (batch: StreamingDeltaBatch) => void,
  scheduler: StreamingDeltaBufferScheduler = defaultScheduler,
): StreamingDeltaBuffer {
  let textDelta = "";
  let thinkingDelta = "";
  let scheduledHandle: number | null = null;

  const hasPending = () => textDelta.length > 0 || thinkingDelta.length > 0;

  const cancelScheduled = () => {
    if (scheduledHandle === null) return;
    scheduler.cancel(scheduledHandle);
    scheduledHandle = null;
  };

  const flush = () => {
    cancelScheduled();
    if (!hasPending()) return;
    const batch = { textDelta, thinkingDelta };
    textDelta = "";
    thinkingDelta = "";
    onFlush(batch);
  };

  const scheduleFlush = () => {
    if (scheduledHandle !== null) return;
    scheduledHandle = scheduler.schedule(flush);
  };

  const clear = () => {
    cancelScheduled();
    textDelta = "";
    thinkingDelta = "";
  };

  return {
    appendText(delta) {
      if (!delta) return;
      textDelta += delta;
      scheduleFlush();
    },
    appendThinking(delta) {
      if (!delta) return;
      thinkingDelta += delta;
      scheduleFlush();
    },
    flush,
    clear,
    dispose: clear,
  };
}
