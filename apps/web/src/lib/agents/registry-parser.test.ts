import { parseAgentRegistry } from "./registry-parser";

describe("parseAgentRegistry", () => {
  it("returns empty registry for empty string", () => {
    const result = parseAgentRegistry("");
    expect(result.activeAgents).toEqual([]);
    expect(result.archivedAgents).toEqual([]);
    expect(result.usedSlots).toBe(0);
    expect(result.maxSlots).toBe(8);
  });

  it("parses initial empty registry", () => {
    const md = `# Agent Registry

## Active Agents (0/8 slots)

(no specialists created yet)

## Archived Agents

(none)`;
    const result = parseAgentRegistry(md);
    expect(result.activeAgents).toEqual([]);
    expect(result.archivedAgents).toEqual([]);
    expect(result.usedSlots).toBe(0);
    expect(result.maxSlots).toBe(8);
  });

  it("parses single active agent", () => {
    const md = `# Agent Registry

## Active Agents (1/8 slots)

### monitor
- **Purpose:** Financial tracking, wallet balance
- **Workspace:** /home/ocuser/.openclaw/specialists/monitor
- **Session:** ca06a180-838a-49e4-ad9d-70dfe083d0a8
- **Created:** 2026-02-25
- **Last Used:** 2026-02-25
- **Turn Count:** 12

## Archived Agents

(none)`;
    const result = parseAgentRegistry(md);
    expect(result.usedSlots).toBe(1);
    expect(result.maxSlots).toBe(8);
    expect(result.activeAgents).toHaveLength(1);
    expect(result.activeAgents[0]).toEqual({
      name: "monitor",
      purpose: "Financial tracking, wallet balance",
      created: "2026-02-25",
      lastUsed: "2026-02-25",
      turnCount: 12,
    });
    expect(result.archivedAgents).toEqual([]);
  });

  it("parses multiple active and archived agents", () => {
    const md = `# Agent Registry

## Active Agents (3/8 slots)

### monitor
- **Purpose:** Financial tracking
- **Workspace:** /home/ocuser/.openclaw/specialists/monitor
- **Session:** abc123
- **Created:** 2026-02-20
- **Last Used:** 2026-02-25
- **Turn Count:** 45

### researcher
- **Purpose:** Market intelligence specialist
- **Workspace:** /home/ocuser/.openclaw/specialists/researcher
- **Session:** def456
- **Created:** 2026-02-21
- **Last Used:** 2026-02-24
- **Turn Count:** 30

### coder
- **Purpose:** Code generation and review
- **Workspace:** /home/ocuser/.openclaw/specialists/coder
- **Session:** ghi789
- **Created:** 2026-02-22
- **Last Used:** 2026-02-25
- **Turn Count:** 8

## Archived Agents

### crypto-researcher
- **Archived:** 2026-03-10
- **Archive Path:** ~/.openclaw/archive/crypto-researcher-2026-03-10-142530.tar.gz
- **Reason:** Project completed
- **Original Purpose:** Crypto market analysis

### old-monitor
- **Archived:** 2026-03-05
- **Archive Path:** ~/.openclaw/archive/old-monitor-2026-03-05.tar.gz
- **Reason:** Replaced by new monitor`;
    const result = parseAgentRegistry(md);
    expect(result.usedSlots).toBe(3);
    expect(result.maxSlots).toBe(8);
    expect(result.activeAgents).toHaveLength(3);
    expect(result.activeAgents[0].name).toBe("monitor");
    expect(result.activeAgents[1].name).toBe("researcher");
    expect(result.activeAgents[2].name).toBe("coder");
    expect(result.activeAgents[0].turnCount).toBe(45);
    expect(result.archivedAgents).toHaveLength(2);
    expect(result.archivedAgents[0]).toEqual({
      name: "crypto-researcher",
      archivedDate: "2026-03-10",
      reason: "Project completed",
    });
    expect(result.archivedAgents[1].name).toBe("old-monitor");
  });

  it("handles malformed input gracefully", () => {
    const md = `# Agent Registry

## Active Agents (2/8 slots)

### incomplete-agent
- **Purpose:** Something useful

### no-fields-agent

## Archived Agents`;
    const result = parseAgentRegistry(md);
    expect(result.usedSlots).toBe(2);
    expect(result.activeAgents).toHaveLength(2);
    expect(result.activeAgents[0].name).toBe("incomplete-agent");
    expect(result.activeAgents[0].purpose).toBe("Something useful");
    expect(result.activeAgents[0].turnCount).toBe(0);
    expect(result.activeAgents[0].created).toBe("");
    expect(result.activeAgents[1].name).toBe("no-fields-agent");
    expect(result.activeAgents[1].purpose).toBe("");
  });
});
