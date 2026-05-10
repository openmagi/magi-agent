export type SourceLedgerKind =
  | "web_search"
  | "web_fetch"
  | "browser"
  | "kb"
  | "file"
  | "external_repo"
  | "external_doc"
  | "subagent_result";

export type SourceTrustTier = "primary" | "official" | "secondary" | "unknown";

export interface SourceLedgerRecord {
  sourceId: string;
  turnId: string;
  toolName: string;
  toolUseId?: string;
  kind: SourceLedgerKind;
  uri: string;
  title?: string;
  contentHash?: string;
  contentType?: string;
  trustTier?: SourceTrustTier;
  snippets?: string[];
  inspectedAt: number;
  metadata?: Record<string, unknown>;
}

export type SourceLedgerInput = Omit<SourceLedgerRecord, "sourceId" | "inspectedAt"> & {
  inspectedAt?: number;
};

export class SourceLedgerStore {
  private readonly now: () => number;
  private readonly records: SourceLedgerRecord[] = [];

  constructor(opts: { now?: () => number } = {}) {
    this.now = opts.now ?? Date.now;
  }

  recordSource(input: SourceLedgerInput): SourceLedgerRecord {
    const record = this.copyRecord({
      ...input,
      sourceId: `src_${this.records.length + 1}`,
      inspectedAt: input.inspectedAt ?? this.now(),
    });
    this.records.push(record);
    return this.copyRecord(record);
  }

  snapshot(): SourceLedgerRecord[] {
    return this.records.map((record) => this.copyRecord(record));
  }

  sourcesForTurn(turnId: string): SourceLedgerRecord[] {
    const childTurnPrefix = `${turnId}::spawn::`;
    return this.records
      .filter((record) =>
        record.turnId === turnId || record.turnId.startsWith(childTurnPrefix)
      )
      .map((record) => this.copyRecord(record));
  }

  private copyRecord(record: SourceLedgerRecord): SourceLedgerRecord {
    return {
      ...record,
      ...(record.snippets ? { snippets: [...record.snippets] } : {}),
      ...(record.metadata ? { metadata: { ...record.metadata } } : {}),
    };
  }
}
