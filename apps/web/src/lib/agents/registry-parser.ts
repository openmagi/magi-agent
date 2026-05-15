export interface SpecialistInfo {
  name: string;
  purpose: string;
  created: string;
  lastUsed: string;
  turnCount: number;
}

export interface ArchivedAgentInfo {
  name: string;
  archivedDate: string;
  reason: string;
}

export interface AgentRegistryData {
  activeAgents: SpecialistInfo[];
  archivedAgents: ArchivedAgentInfo[];
  usedSlots: number;
  maxSlots: number;
}

function extractField(block: string, key: string): string {
  const regex = new RegExp(`-\\s*\\*\\*${key}:\\*\\*\\s*(.+)`, "i");
  const match = block.match(regex);
  return match ? match[1].trim() : "";
}

function parseActiveBlock(block: string): SpecialistInfo {
  const nameMatch = block.match(/^###\s+(.+)/m);
  return {
    name: nameMatch ? nameMatch[1].trim() : "",
    purpose: extractField(block, "Purpose"),
    created: extractField(block, "Created"),
    lastUsed: extractField(block, "Last Used"),
    turnCount: parseInt(extractField(block, "Turn Count") || "0", 10) || 0,
  };
}

function parseArchivedBlock(block: string): ArchivedAgentInfo {
  const nameMatch = block.match(/^###\s+(.+)/m);
  return {
    name: nameMatch ? nameMatch[1].trim() : "",
    archivedDate: extractField(block, "Archived"),
    reason: extractField(block, "Reason"),
  };
}

function splitByH3(section: string): string[] {
  const blocks: string[] = [];
  const lines = section.split("\n");
  let current: string[] = [];

  for (const line of lines) {
    if (line.startsWith("### ")) {
      if (current.length > 0) blocks.push(current.join("\n"));
      current = [line];
    } else {
      current.push(line);
    }
  }
  if (current.length > 0 && current.some((l) => l.startsWith("### "))) {
    blocks.push(current.join("\n"));
  }

  return blocks;
}

export function parseAgentRegistry(markdown: string): AgentRegistryData {
  const DEFAULT_MAX_SLOTS = 8;

  if (!markdown.trim()) {
    return { activeAgents: [], archivedAgents: [], usedSlots: 0, maxSlots: DEFAULT_MAX_SLOTS };
  }

  // Extract slot counts from "## Active Agents (X/Y slots)"
  const slotsMatch = markdown.match(/##\s+Active Agents\s*\((\d+)\/(\d+)\s*slots?\)/i);
  const usedSlots = slotsMatch ? parseInt(slotsMatch[1], 10) : 0;
  const maxSlots = slotsMatch ? parseInt(slotsMatch[2], 10) : DEFAULT_MAX_SLOTS;

  // Split into active and archived sections
  const activeSectionMatch = markdown.match(/##\s+Active Agents[^\n]*\n([\s\S]*?)(?=##\s+Archived Agents|$)/i);
  const archivedSectionMatch = markdown.match(/##\s+Archived Agents[^\n]*\n([\s\S]*?)$/i);

  const activeSection = activeSectionMatch ? activeSectionMatch[1] : "";
  const archivedSection = archivedSectionMatch ? archivedSectionMatch[1] : "";

  const activeAgents = splitByH3(activeSection)
    .map(parseActiveBlock)
    .filter((a) => a.name);

  const archivedAgents = splitByH3(archivedSection)
    .map(parseArchivedBlock)
    .filter((a) => a.name);

  return { activeAgents, archivedAgents, usedSlots, maxSlots };
}
