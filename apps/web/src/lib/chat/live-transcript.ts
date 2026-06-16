import type {
  ChannelState,
  ChatResponseLanguage,
  LiveTranscriptItem,
  LiveTranscriptWorkItem,
} from "@/chat-core";
import { deriveWorkConsoleRows } from "./work-console";
import type { WorkConsoleRow } from "./work-console";

const MAX_LIVE_TRANSCRIPT_ITEMS = 120;
const LIVE_TRANSCRIPT_WORK_ROW_LIMIT = 6;

function trimLiveTranscript(items: LiveTranscriptItem[]): LiveTranscriptItem[] {
  return items.slice(-MAX_LIVE_TRANSCRIPT_ITEMS);
}

export function appendLiveTranscriptText(
  items: LiveTranscriptItem[] | undefined,
  content: string,
  receivedAt = Date.now(),
): LiveTranscriptItem[] {
  if (!content) return items ?? [];
  const next = [...(items ?? [])];
  const last = next[next.length - 1];
  if (last?.kind === "text") {
    next[next.length - 1] = {
      ...last,
      content: `${last.content}${content}`,
      receivedAt,
    };
  } else {
    next.push({
      id: `text:${receivedAt}:${Math.random().toString(36).slice(2, 8)}`,
      kind: "text",
      content,
      receivedAt,
    });
  }
  return trimLiveTranscript(next);
}

export function replaceLiveTranscriptText(
  content: string,
  receivedAt = Date.now(),
): LiveTranscriptItem[] {
  if (!content) return [];
  return [{
    id: `text:${receivedAt}:replace`,
    kind: "text",
    content,
    receivedAt,
  }];
}

export function upsertLiveTranscriptWorkRows(
  items: LiveTranscriptItem[] | undefined,
  rows: WorkConsoleRow[],
  receivedAt = Date.now(),
): LiveTranscriptItem[] {
  if (rows.length === 0) return items ?? [];
  const next = [...(items ?? [])];
  const indexByRowId = new Map<string, number>();
  next.forEach((item, index) => {
    if (item.kind === "work") indexByRowId.set(item.rowId, index);
  });

  for (const row of rows) {
    const existingIndex = indexByRowId.get(row.id);
    if (existingIndex !== undefined) {
      const existing = next[existingIndex];
      if (existing.kind !== "work") continue;
      const updated: LiveTranscriptWorkItem = {
        ...existing,
        group: row.group,
        label: row.label,
        status: row.status,
      };
      if (row.detail) updated.detail = row.detail;
      else delete updated.detail;
      if (row.snippet) updated.snippet = row.snippet;
      else delete updated.snippet;
      if (row.meta) updated.meta = row.meta;
      else delete updated.meta;
      next[existingIndex] = updated;
      continue;
    }

    next.push({
      id: `work:${row.id}:${receivedAt}`,
      kind: "work",
      rowId: row.id,
      group: row.group,
      label: row.label,
      ...(row.detail ? { detail: row.detail } : {}),
      ...(row.snippet ? { snippet: row.snippet } : {}),
      status: row.status,
      ...(row.meta ? { meta: row.meta } : {}),
      receivedAt,
    });
    indexByRowId.set(row.id, next.length - 1);
  }

  return trimLiveTranscript(next);
}

export function liveTranscriptRowsForState(
  channelState: ChannelState,
  language?: ChatResponseLanguage,
): WorkConsoleRow[] {
  const rows = deriveWorkConsoleRows({
    channelState,
    queuedMessages: [],
    controlRequests: [],
    uiLanguage: language,
  });
  const selected: WorkConsoleRow[] = [];
  const appendRows = (items: WorkConsoleRow[]) => {
    for (const item of items) {
      if (selected.length >= LIVE_TRANSCRIPT_WORK_ROW_LIMIT) break;
      selected.push(item);
    }
  };

  appendRows(
    rows
      .filter(
        (row) =>
          row.group === "subagent" &&
          (row.status === "running" || row.status === "waiting"),
      )
      .slice(-2),
  );
  appendRows(
    rows
      .filter((row) => row.group === "trace" && row.status !== "info")
      .slice(-2),
  );
  appendRows(rows.filter((row) => row.group === "tool").slice(-LIVE_TRANSCRIPT_WORK_ROW_LIMIT));
  appendRows(
    rows
      .filter((row) => row.group === "task" && (row.status === "running" || row.status === "done"))
      .slice(-2),
  );

  if (selected.length === 0) {
    appendRows(rows.filter((row) => row.group === "status" && row.id !== "idle"));
  }

  return selected.slice(0, LIVE_TRANSCRIPT_WORK_ROW_LIMIT);
}
