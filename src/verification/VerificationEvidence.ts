import type { TranscriptEntry } from "../storage/Transcript.js";

export interface EvidenceItem {
  tool: string;
  status?: string;
  input?: unknown;
  output?: string;
  isError?: boolean;
}

export interface EvidenceClassification {
  work: boolean;
  verification: boolean;
  documentVerification: boolean;
  tools: string[];
  verificationCommands: string[];
}

const COMPLETION_PATTERNS: readonly RegExp[] = [
  /(?:수정|고쳤|해결|완료|반영|구현|적용|배포).{0,24}(?:했|했습니다|됐|되었습니다|끝났)/u,
  /(?:테스트|빌드|린트|검증|확인).{0,18}(?:통과|성공|완료|문제\s*없)/u,
  /\b(?:fixed|completed|done|implemented|resolved|deployed|verified)\b/i,
  /\b(?:tests?|build|lint|checks?|verification)\s+(?:pass(?:es|ed|ing)?|succeeded|completed)\b/i,
];

const NON_VERIFIED_PATTERNS: readonly RegExp[] = [
  /(?:검증|확인|테스트|빌드|린트).{0,24}(?:못|않|안\s*했|불가|미실행|실패)/u,
  /(?:미검증|검증\s*전|확인\s*전)/u,
  /\b(?:not verified|unverified|unable to verify|could not verify)\b/i,
  /\b(?:did not|didn't|haven't|have not)\s+(?:run|verify|test|check|build)\b/i,
  /\b(?:tests?|build|lint|checks?)\s+(?:failed|did not pass)\b/i,
];

const VERIFY_COMMAND_PATTERNS: readonly RegExp[] = [
  /\bnpm\s+(?:run\s+)?(?:test|lint|build|qa|typecheck)\b/i,
  /\b(?:pnpm|yarn|bun)\s+(?:test|lint|build|qa|typecheck)\b/i,
  /\b(?:vitest|jest|mocha|pytest|ruff|mypy)\b/i,
  /\bgo\s+test\b/i,
  /\bcargo\s+(?:test|check|clippy)\b/i,
  /\b(?:tsc|eslint)\b/i,
  /\bnode\s+--check\b/i,
  /\bbash\s+-n\b/i,
  /\b(?:make|just)\s+(?:test|check|verify|lint|build|qa)\b/i,
  /\bcurl\b.{0,160}\b(?:health|ready|live|status|smoke)\b/i,
  /\b(?:unzip|zipinfo)\s+(?:-t\b|.*\.(?:docx|xlsx|pptx|hwpx)\b)/i,
  /\b(?:file|xmllint)\b.{0,160}\.(?:docx|xlsx|pptx|hwpx|html|pdf)\b/i,
];

const WORK_EVIDENCE_TOOLS = new Set([
  "FileWrite",
  "FileEdit",
  "DocumentWrite",
  "SpreadsheetWrite",
  "FileDeliver",
  "Bash",
  "SpawnAgent",
  "Task",
  "CommitCheckpoint",
]);

const DOCUMENT_VERIFICATION_TOOLS = new Set([
  "DocumentPreview",
  "DocumentRender",
  "DocumentRead",
  "SpreadsheetPreview",
  "SpreadsheetRead",
  "HtmlPreview",
  "PdfPreview",
  "ArtifactPreview",
]);

const EXPLICIT_VERIFIER_TOOLS = new Set([
  "PlanVerifier",
  "Verification",
  "VerificationResult",
  "ArtifactVerify",
  "Clock",
  "DateRange",
  "Calculation",
  "TestRun",
  "DeterministicEvidenceVerifier",
]);

const SOURCE_VERIFICATION_TOOLS = new Set([
  "WebFetch",
  "WebSearch",
  "web-search",
  "web_search",
]);

export function matchesCompletionClaim(text: string): boolean {
  if (!text || !text.trim()) return false;
  if (NON_VERIFIED_PATTERNS.some((p) => p.test(text))) return false;
  return COMPLETION_PATTERNS.some((p) => p.test(text));
}

export function shouldBlockClaim(
  text: string,
  evidence: ReadonlyArray<EvidenceItem>,
): boolean {
  if (!matchesCompletionClaim(text)) return false;
  return !classifyEvidence(evidence).verification;
}

export function classifyEvidence(
  evidence: ReadonlyArray<EvidenceItem>,
): EvidenceClassification {
  const tools: string[] = [];
  const verificationCommands: string[] = [];
  let work = false;
  let verification = false;
  let documentVerification = false;

  for (const item of evidence) {
    if (!isSuccessful(item)) continue;
    tools.push(item.tool);

    const command = commandFromInput(item.input);
    const commandVerifies = command.length > 0 && isVerificationCommand(command);
    const documentToolVerifies = DOCUMENT_VERIFICATION_TOOLS.has(item.tool);
    const explicitVerifier = EXPLICIT_VERIFIER_TOOLS.has(item.tool);
    const sourceVerifier = SOURCE_VERIFICATION_TOOLS.has(item.tool);

    if (WORK_EVIDENCE_TOOLS.has(item.tool) && !commandVerifies) {
      work = true;
    }
    if (commandVerifies || documentToolVerifies || explicitVerifier || sourceVerifier) {
      verification = true;
      if (command.length > 0) verificationCommands.push(command);
    }
    if (documentToolVerifies || isDocumentVerificationCommand(command)) {
      documentVerification = true;
    }
  }

  return {
    work,
    verification,
    documentVerification,
    tools: [...new Set(tools)],
    verificationCommands,
  };
}

export function transcriptEvidenceForTurn(
  transcript: ReadonlyArray<TranscriptEntry>,
  turnId: string,
): EvidenceItem[] {
  const results = new Map<string, Extract<TranscriptEntry, { kind: "tool_result" }>>();
  for (const entry of transcript) {
    if (entry.kind === "tool_result" && entry.turnId === turnId) {
      results.set(entry.toolUseId, entry);
    }
  }

  const out: EvidenceItem[] = [];
  for (const entry of transcript) {
    if (entry.kind !== "tool_call" || entry.turnId !== turnId) continue;
    const result = results.get(entry.toolUseId);
    if (!result) continue;
    out.push({
      tool: entry.name,
      input: entry.input,
      status: result.status,
      output: result.output,
      isError: result.isError,
    });
  }
  return out;
}

function isSuccessful(item: EvidenceItem): boolean {
  if (item.isError === true) return false;
  if (!item.status) return true;
  return item.status === "ok" || item.status === "success" || item.status === "completed";
}

function commandFromInput(input: unknown): string {
  if (!input || typeof input !== "object") return "";
  const cmd = (input as Record<string, unknown>).command;
  return typeof cmd === "string" ? cmd : "";
}

function isVerificationCommand(command: string): boolean {
  return VERIFY_COMMAND_PATTERNS.some((p) => p.test(command));
}

function isDocumentVerificationCommand(command: string): boolean {
  return /\.(?:docx|xlsx|pptx|hwpx|html|pdf)\b/i.test(command) &&
    /\b(?:preview|render|inspect|validate|open|unzip|zipinfo|file|xmllint|playwright|screenshot)\b/i.test(command);
}
