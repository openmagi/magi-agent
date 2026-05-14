export function isResearchProofBlockReason(reason: string): boolean {
  return /\[(?:RETRY|RULE):(?:CLAIM_CITATION|SOURCE_AUTHORITY|RESEARCH_PROOF)[^\]]*\]/iu.test(reason) ||
    /claim[-_\s]?citation|source[-_\s]?authority|research proof/iu.test(reason);
}

export const RESEARCH_PROOF_PUBLIC_FAILURE_TEXT =
  "I could not complete a source-verified final answer for this request. " +
  "Please retry with a narrower scope or ask me to continue from the inspected-source context.";

export function researchProofFailureNoticeText(_reason: string): string {
  return RESEARCH_PROOF_PUBLIC_FAILURE_TEXT;
}

export function publicResearchProofFailureReason(reason: string): string {
  return isResearchProofBlockReason(reason)
    ? RESEARCH_PROOF_PUBLIC_FAILURE_TEXT
    : reason;
}
