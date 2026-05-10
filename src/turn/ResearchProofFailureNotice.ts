const RESEARCH_PROOF_RULE_RE =
  /\[(?:RETRY|RULE):(?:CLAIM_CITATION|SOURCE_AUTHORITY|RESEARCH_PROOF)[^\]]*\]/i;

export function isResearchProofBlockReason(reason: string): boolean {
  return (
    RESEARCH_PROOF_RULE_RE.test(reason) ||
    /\b(?:claim[-_\s]?citation|source[-_\s]?authority|research[-_\s]?proof)\b/i.test(reason)
  );
}

export function researchProofFailureNoticeText(reason: string): string {
  return [
    "The research proof verifier blocked the draft before it could be sent.",
    "",
    "The answer was not delivered because it still needed inspected-source citations or stronger source evidence.",
    "",
    `Verifier reason: ${reason}`,
    "",
    "Please retry after the agent inspects sources and cites each concrete claim.",
  ].join("\n");
}
