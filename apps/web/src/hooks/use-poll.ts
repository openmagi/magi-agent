"use client";

import { useEffect, useRef } from "react";

/**
 * Generic polling hook — calls `callback` every `intervalMs` milliseconds
 * while `enabled` is true. Cleans up on disable or unmount.
 */
export function usePoll(
  callback: () => Promise<void> | void,
  intervalMs: number,
  enabled: boolean
): void {
  const savedCallback = useRef(callback);

  // Keep callback ref current without restarting the interval
  useEffect(() => {
    savedCallback.current = callback;
  }, [callback]);

  useEffect(() => {
    if (!enabled) return;

    const id = setInterval(() => {
      savedCallback.current();
    }, intervalMs);

    return () => clearInterval(id);
  }, [intervalMs, enabled]);
}
