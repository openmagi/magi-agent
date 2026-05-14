export interface StoredChannel {
  id?: string | null;
  name: string;
  display_name?: string | null;
  position?: number | null;
  category?: string | null;
  memory_mode?: "normal" | "read_only" | "incognito" | null;
  model_selection?: string | null;
  router_type?: string | null;
  created_at?: string | null;
}

export interface HistoryChannelRow {
  channel_name?: string | null;
  created_at?: string | null;
}

export interface DeletedChannelRow {
  channel_name?: string | null;
  client_msg_id?: string | null;
}

export interface VisibleChannel {
  id: string;
  name: string;
  display_name: string | null;
  position: number;
  category: string | null;
  memory_mode?: "normal" | "read_only" | "incognito" | null;
  model_selection?: string | null;
  router_type?: string | null;
  created_at: string;
}

const DEFAULT_CHANNELS = new Map<string, Omit<VisibleChannel, "id" | "name" | "created_at">>([
  ["general", { display_name: "General", position: 0, category: "General" }],
  ["random", { display_name: "Random", position: 1, category: "General" }],
  ["quick-memo", { display_name: "Quick Memo", position: 2, category: "General" }],
  ["news", { display_name: "News", position: 3, category: "Info" }],
  ["daily-update", { display_name: "Daily Update", position: 4, category: "Info" }],
  ["schedule", { display_name: "Schedule", position: 5, category: "Life" }],
  ["health", { display_name: "Health", position: 6, category: "Life" }],
  ["chores", { display_name: "Chores", position: 7, category: "Life" }],
  ["finance", { display_name: "Finance", position: 8, category: "Finance" }],
  ["shopping", { display_name: "Shopping", position: 9, category: "Finance" }],
  ["study", { display_name: "Study", position: 10, category: "Study" }],
  ["contacts", { display_name: "Contacts", position: 11, category: "People" }],
  ["todo-list", { display_name: "Todo List", position: 12, category: "Tasks" }],
  ["reminder", { display_name: "Reminder", position: 13, category: "Tasks" }],
]);

const CHANNEL_NAME_RE = /^[a-z0-9-]+$/;

export function mergeChannelsWithHistory(
  channels: readonly StoredChannel[],
  historyRows: readonly HistoryChannelRow[],
  deletedChannelRows: readonly DeletedChannelRow[] = [],
): VisibleChannel[] {
  const merged = new Map<string, VisibleChannel>();
  let maxPosition = -1;

  for (const channel of channels) {
    if (!CHANNEL_NAME_RE.test(channel.name)) continue;
    const position = typeof channel.position === "number" ? channel.position : 0;
    maxPosition = Math.max(maxPosition, position);
    merged.set(channel.name, {
      id: channel.id ?? `channel:${channel.name}`,
      name: channel.name,
      display_name: channel.display_name ?? null,
      position,
      category: channel.category ?? null,
      memory_mode: channel.memory_mode ?? null,
      model_selection: channel.model_selection ?? null,
      router_type: channel.router_type ?? null,
      created_at: channel.created_at ?? new Date(0).toISOString(),
    });
  }

  const deletedChannelNames = new Set<string>();
  for (const row of deletedChannelRows) {
    const name = row.channel_name;
    if (!name || !CHANNEL_NAME_RE.test(name)) continue;
    if (row.client_msg_id === null || row.client_msg_id === undefined) {
      deletedChannelNames.add(name);
    }
  }

  let nextRestoredPosition = maxPosition + 1;
  const seenHistory = new Set<string>();
  for (const row of historyRows) {
    const name = row.channel_name;
    if (
      !name ||
      !CHANNEL_NAME_RE.test(name) ||
      merged.has(name) ||
      seenHistory.has(name) ||
      deletedChannelNames.has(name)
    ) {
      continue;
    }
    seenHistory.add(name);

    const defaultChannel = DEFAULT_CHANNELS.get(name);
    const position = defaultChannel?.position ?? nextRestoredPosition++;
    merged.set(name, {
      id: `history:${name}`,
      name,
      display_name: defaultChannel?.display_name ?? name,
      position,
      category: defaultChannel?.category ?? "Restored",
      created_at: row.created_at ?? new Date(0).toISOString(),
      model_selection: null,
      router_type: null,
    });
  }

  return [...merged.values()].sort((a, b) => {
    if (a.position !== b.position) return a.position - b.position;
    return a.name.localeCompare(b.name);
  });
}
