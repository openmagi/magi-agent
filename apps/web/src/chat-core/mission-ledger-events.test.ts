import { describe, expect, it } from "vitest";
import {
  OPEN_MISSION_LEDGER_EVENT,
  dispatchOpenMissionLedgerEvent,
  readOpenMissionLedgerEvent,
} from "./mission-ledger-events";

describe("mission ledger browser events", () => {
  it("reads a mission focus request from a custom browser event", () => {
    const event = new CustomEvent(OPEN_MISSION_LEDGER_EVENT, {
      detail: { missionId: "mission-1" },
    });

    expect(readOpenMissionLedgerEvent(event)).toEqual({ missionId: "mission-1" });
  });

  it("ignores events without a usable mission id", () => {
    expect(readOpenMissionLedgerEvent(new Event("click"))).toBeNull();
    expect(readOpenMissionLedgerEvent(new CustomEvent(OPEN_MISSION_LEDGER_EVENT, {
      detail: { missionId: " " },
    }))).toBeNull();
  });

  it("ignores private or token-like mission ids from browser events", () => {
    const unsafeMissionIds = [
      "/Users/kevin/.openmagi/session.json",
      "file:///private/tmp/mission",
      "session:agent:app:general",
      "Bearer sk-test-secret-token",
      "ghp_privateMissionToken",
      "raw_prompt_payload",
    ];

    for (const missionId of unsafeMissionIds) {
      expect(readOpenMissionLedgerEvent(new CustomEvent(OPEN_MISSION_LEDGER_EVENT, {
        detail: { missionId },
      }))).toBeNull();
    }
  });

  it("does not dispatch unsafe mission ids", () => {
    const originalWindow = (globalThis as typeof globalThis & { window?: unknown }).window;
    const target = new EventTarget();
    Object.defineProperty(globalThis, "window", {
      configurable: true,
      value: {
        addEventListener: target.addEventListener.bind(target),
        removeEventListener: target.removeEventListener.bind(target),
        dispatchEvent: target.dispatchEvent.bind(target),
      },
    });
    const received: string[] = [];
    const onOpenMissionLedger = (event: Event) => {
      const detail = readOpenMissionLedgerEvent(event);
      if (detail) received.push(detail.missionId);
    };

    window.addEventListener(OPEN_MISSION_LEDGER_EVENT, onOpenMissionLedger);
    try {
      dispatchOpenMissionLedgerEvent(" mission:daily-report ");
      dispatchOpenMissionLedgerEvent("/Users/kevin/.openmagi/session.json");
      dispatchOpenMissionLedgerEvent("Bearer sk-test-secret-token");
    } finally {
      window.removeEventListener(OPEN_MISSION_LEDGER_EVENT, onOpenMissionLedger);
      if (originalWindow === undefined) {
        Reflect.deleteProperty(globalThis, "window");
      } else {
        Object.defineProperty(globalThis, "window", {
          configurable: true,
          value: originalWindow,
        });
      }
    }

    expect(received).toEqual(["mission:daily-report"]);
  });
});
