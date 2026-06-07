import type {
  ChannelState,
  CitationGateStatus,
  InspectedSource,
  InspectedSourceKind,
  ResearchEvidenceSnapshot,
  ServerMessage,
} from "./types";

const INSPECTED_SOURCE_KINDS: readonly InspectedSourceKind[] = [
  "web_search",
  "web_fetch",
  "browser",
  "kb",
  "file",
  "external_repo",
  "external_doc",
  "subagent_result",
];

function recordFromUnknown(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null;
}

function stringField(record: Record<string, unknown> | null, key: string): string | null {
  const value = record?.[key];
  return typeof value === "string" ? value : null;
}

function numberField(record: Record<string, unknown> | null, key: string): number | null {
  const value = record?.[key];
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function stringArrayField(record: Record<string, unknown> | null, key: string): string[] | undefined {
  const value = record?.[key];
  if (!Array.isArray(value)) return undefined;
  const items = value.filter((item): item is string => typeof item === "string" && item.trim().length > 0);
  return items.length > 0 ? items : undefined;
}

export function normalizeInspectedSource(value: unknown): InspectedSource | null {
  const source = recordFromUnknown(value);
  const sourceId = stringField(source, "sourceId");
  const uri = stringField(source, "uri");
  if (!sourceId || !uri) return null;
  const rawKind = stringField(source, "kind");
  const kind = INSPECTED_SOURCE_KINDS.includes(rawKind as InspectedSourceKind)
    ? rawKind as InspectedSourceKind
    : "web_fetch";
  const inspectedAt = numberField(source, "inspectedAt");
  if (inspectedAt === null) return null;

  const parsed: InspectedSource = {
    sourceId,
    kind,
    uri,
    inspectedAt,
  };
  const turnId = stringField(source, "turnId");
  const toolName = stringField(source, "toolName");
  const toolUseId = stringField(source, "toolUseId");
  const title = stringField(source, "title");
  const contentHash = stringField(source, "contentHash");
  const contentType = stringField(source, "contentType");
  const trustTier = stringField(source, "trustTier");
  const snippets = stringArrayField(source, "snippets");
  if (turnId) parsed.turnId = turnId;
  if (toolName) parsed.toolName = toolName;
  if (toolUseId) parsed.toolUseId = toolUseId;
  if (title) parsed.title = title;
  if (contentHash) parsed.contentHash = contentHash;
  if (contentType) parsed.contentType = contentType;
  if (
    trustTier === "primary" ||
    trustTier === "official" ||
    trustTier === "secondary" ||
    trustTier === "unknown"
  ) {
    parsed.trustTier = trustTier;
  }
  if (snippets) parsed.snippets = snippets;
  return parsed;
}

export function normalizeCitationGateStatus(value: unknown): CitationGateStatus | null {
  const status = recordFromUnknown(value);
  if (stringField(status, "ruleId") !== "claim-citation-gate") return null;
  const verdict = stringField(status, "verdict");
  if (verdict !== "pending" && verdict !== "ok" && verdict !== "violation") return null;
  const checkedAt = numberField(status, "checkedAt");
  if (checkedAt === null) return null;
  const detail = stringField(status, "detail");
  return {
    ruleId: "claim-citation-gate",
    verdict,
    ...(detail ? { detail } : {}),
    checkedAt,
  };
}

export function normalizeResearchEvidenceSnapshot(value: unknown): ResearchEvidenceSnapshot | undefined {
  const evidence = recordFromUnknown(value);
  if (!evidence) return undefined;
  const inspectedSources = Array.isArray(evidence.inspectedSources)
    ? evidence.inspectedSources
        .map((source) => normalizeInspectedSource(source))
        .filter((source): source is InspectedSource => source !== null)
    : [];
  const citationGate = normalizeCitationGateStatus(evidence.citationGate);
  const capturedAt = numberField(evidence, "capturedAt") ?? Date.now();
  if (inspectedSources.length === 0 && !citationGate) return undefined;
  return {
    inspectedSources,
    ...(citationGate ? { citationGate } : {}),
    capturedAt,
  };
}

export function researchEvidenceFromChannelState(
  state: Pick<ChannelState, "inspectedSources" | "citationGate"> | null | undefined,
  now: () => number = Date.now,
): ResearchEvidenceSnapshot | undefined {
  const inspectedSources = state?.inspectedSources ?? [];
  const citationGate = state?.citationGate ?? null;
  if (inspectedSources.length === 0 && !citationGate) return undefined;
  return {
    inspectedSources,
    ...(citationGate ? { citationGate } : {}),
    capturedAt: now(),
  };
}

export function researchEvidenceFromServerMessage(
  message: Pick<ServerMessage, "researchEvidence" | "research_evidence">,
): ResearchEvidenceSnapshot | undefined {
  return normalizeResearchEvidenceSnapshot(message.researchEvidence ?? message.research_evidence);
}

const RESEARCH_EVIDENCE_MARKER_RE =
  /\n?\s*<!-- clawy:research-evidence:v1:[A-Za-z0-9_-]+ -->\s*$/;

export function stripResearchEvidenceMarker(content: string): string {
  return content.replace(RESEARCH_EVIDENCE_MARKER_RE, "");
}
