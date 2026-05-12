import type { SourceLedgerRecord } from "../research/SourceLedger.js";

export interface ResearchProofFallbackInput {
  sources: readonly SourceLedgerRecord[];
}

export interface ResearchProofFallback {
  text: string;
  sourceCount: number;
}

export function buildResearchProofFallback(
  input: ResearchProofFallbackInput,
): ResearchProofFallback | null {
  void input;
  return null;
}
