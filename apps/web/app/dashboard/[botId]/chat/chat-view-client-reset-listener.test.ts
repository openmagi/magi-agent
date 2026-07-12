/**
 * U5 Test 21: storage-event reset-counter listener.
 *
 * The chat shell must register a "storage" event listener scoped to
 * `clawy:resetCounters:<botId>`. On a change from another tab, it must
 * (a) re-run `syncResetCounters` for display sync, and
 * (b) trigger an immediate fetch for the active channel.
 *
 * Because the test runs in a Node environment (no jsdom), we verify the
 * presence and shape of the wiring via source-string assertions -- the
 * same pattern used by chat-view-client-export.test.ts.
 */
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

describe("storage-event reset-counter listener (Test 21)", () => {
  const source = readFileSync(
    new URL("./chat-view-client.tsx", import.meta.url),
    "utf8",
  );

  it("registers a storage event listener", () => {
    expect(source).toContain('addEventListener("storage"');
  });

  it("scopes the listener to the resetCounters key for this botId", () => {
    // The key generator `RESET_COUNTERS_KEY = (botId) => \`clawy:resetCounters:${botId}\``
    // is in chat-store.ts; the listener must check event.key against it.
    expect(source).toContain("clawy:resetCounters:");
  });

  it("calls syncResetCounters inside the storage handler", () => {
    // syncResetCounters is already imported at line 20; verify it is invoked
    // inside the storage handler (not just at mount).
    // Strategy: find the useEffect block containing the storage listener and
    // confirm syncResetCounters appears within it.
    const storageListenerIdx = source.indexOf('addEventListener("storage"');
    expect(storageListenerIdx).toBeGreaterThan(-1);

    // Look at a 1500-char window centred on the addEventListener call
    // (the handler body is defined before the addEventListener line).
    const start = Math.max(0, storageListenerIdx - 1200);
    const block = source.slice(start, storageListenerIdx + 200);
    expect(block).toContain("syncResetCounters");
  });

  it("triggers a channel fetch inside the storage handler", () => {
    const storageListenerIdx = source.indexOf('addEventListener("storage"');
    expect(storageListenerIdx).toBeGreaterThan(-1);

    // The handler body is defined before the addEventListener call; widen
    // the search window backwards.
    const start = Math.max(0, storageListenerIdx - 1200);
    const block = source.slice(start, storageListenerIdx + 200);
    // The handler calls fetchChannelMessages for the active channel
    expect(block).toContain("fetchChannelMessages");
  });

  it("removes the listener on cleanup (useEffect return)", () => {
    expect(source).toContain('removeEventListener("storage"');
  });
});
