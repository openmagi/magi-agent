import { describe, expect, it } from "vitest";
import {
  OPEN_MISSION_LEDGER_EVENT,
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
});
