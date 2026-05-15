import { describe, it, expect } from "vitest";
import { AGENT_REGISTRY_ADDRESS } from "./agent-registry";

describe("agent-registry", () => {
  it("should have the correct ERC-8004 registry address", () => {
    expect(AGENT_REGISTRY_ADDRESS).toBe(
      "0x8004A169FB4a3325136EB29fA0ceB6D2e539a432",
    );
  });

  it("should export the registry address as a valid Ethereum address", () => {
    expect(AGENT_REGISTRY_ADDRESS).toMatch(/^0x[a-fA-F0-9]{40}$/);
  });
});
