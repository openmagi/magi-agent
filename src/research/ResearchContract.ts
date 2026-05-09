import type { SourceLedgerKind } from "./SourceLedger.js";

export type CitationCoverageStatus = "covered" | "missing" | "uncertain";

export interface ResearchTurnRecord {
  turnId: string;
  sourceSensitive: boolean;
  requiredSourceKinds: SourceLedgerKind[];
  startedAt: number;
}

export interface ResearchClaimInput {
  text: string;
  status: CitationCoverageStatus;
  sourceIds: string[];
}

export interface ResearchClaimRecord extends ResearchClaimInput {
  claimId: string;
  turnId: string;
  recordedAt: number;
}

export interface ResearchContractSnapshot {
  turns: ResearchTurnRecord[];
  claims: ResearchClaimRecord[];
}

export interface ResearchContractStoreOptions {
  now?: () => number;
}

const SOURCE_SENSITIVE_RE =
  /\b(?:latest|current|today|recent|now|news|price|version|release|github|api|docs?|documentation|web|research|source|verify|citation)\b|(?:최신|현재|오늘|최근|뉴스|가격|버전|릴리스|깃허브|문서|공식|출처|근거|검증|조사|리서치|인용)/i;

export function isSourceSensitiveResearchRequest(userMessage: string): boolean {
  return SOURCE_SENSITIVE_RE.test(userMessage);
}

function sourceRequirementsFor(userMessage: string): SourceLedgerKind[] {
  if (!isSourceSensitiveResearchRequest(userMessage)) return [];
  return ["web_search", "web_fetch"];
}

export class ResearchContractStore {
  private readonly now: () => number;
  private readonly turns = new Map<string, ResearchTurnRecord>();
  private readonly claims: ResearchClaimRecord[] = [];

  constructor(opts: ResearchContractStoreOptions = {}) {
    this.now = opts.now ?? Date.now;
  }

  startTurn(input: { turnId: string; userMessage: string }): ResearchTurnRecord {
    const requiredSourceKinds = sourceRequirementsFor(input.userMessage);
    const record: ResearchTurnRecord = {
      turnId: input.turnId,
      sourceSensitive: requiredSourceKinds.length > 0,
      requiredSourceKinds,
      startedAt: this.now(),
    };
    this.turns.set(input.turnId, this.copyTurn(record));
    return this.copyTurn(record);
  }

  turnFor(turnId: string): ResearchTurnRecord | null {
    const record = this.turns.get(turnId);
    return record ? this.copyTurn(record) : null;
  }

  recordCitationCoverage(
    turnId: string,
    claims: readonly ResearchClaimInput[],
  ): ResearchClaimRecord[] {
    const records = claims.map((claim) => {
      const record: ResearchClaimRecord = {
        claimId: `claim_${this.claims.length + 1}`,
        turnId,
        text: claim.text,
        status: claim.status,
        sourceIds: [...claim.sourceIds],
        recordedAt: this.now(),
      };
      this.claims.push(this.copyClaim(record));
      return this.copyClaim(record);
    });
    return records;
  }

  claimsForTurn(turnId: string): ResearchClaimRecord[] {
    return this.claims
      .filter((claim) => claim.turnId === turnId)
      .map((claim) => this.copyClaim(claim));
  }

  snapshot(): ResearchContractSnapshot {
    return {
      turns: [...this.turns.values()].map((turn) => this.copyTurn(turn)),
      claims: this.claims.map((claim) => this.copyClaim(claim)),
    };
  }

  private copyTurn(record: ResearchTurnRecord): ResearchTurnRecord {
    return {
      ...record,
      requiredSourceKinds: [...record.requiredSourceKinds],
    };
  }

  private copyClaim(record: ResearchClaimRecord): ResearchClaimRecord {
    return {
      ...record,
      sourceIds: [...record.sourceIds],
    };
  }
}
