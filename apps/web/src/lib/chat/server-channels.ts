import { mergeChannelsWithHistory, type DeletedChannelRow, type VisibleChannel } from "./history-backed-channels";

interface SupabaseLike {
  from: (table: string) => SupabaseQuery;
}

interface SupabaseQuery extends PromiseLike<SupabaseQueryResult> {
  select: (columns: string) => SupabaseQuery;
  eq: (column: string, value: string) => SupabaseQuery;
  is: (column: string, value: null) => SupabaseQuery;
  order: (column: string, options?: { ascending?: boolean }) => SupabaseQuery;
  range: (from: number, to: number) => SupabaseQuery;
}

interface SupabaseQueryResult {
  data?: unknown[] | null;
  error?: { message: string } | null;
}

const PAGE_SIZE = 1000;
const APP_CHANNEL_SELECT = "id, name, display_name, position, category, memory_mode, model_selection, router_type, created_at";
const APP_CHANNEL_SELECT_LEGACY = "id, name, display_name, position, category, memory_mode, created_at";

async function fetchAllRows(
  supabase: SupabaseLike,
  table: "chat_messages" | "app_channel_messages" | "chat_message_deletions",
  botId: string,
): Promise<Array<{ channel_name?: string | null; created_at?: string | null; client_msg_id?: string | null }>> {
  const rows: Array<{ channel_name?: string | null; created_at?: string | null; client_msg_id?: string | null }> = [];

  for (let from = 0;; from += PAGE_SIZE) {
    let query = supabase
      .from(table)
      .select(table === "chat_message_deletions" ? "channel_name, client_msg_id, deleted_at" : "channel_name, created_at")
      .eq("bot_id", botId);
    if (table === "chat_message_deletions") {
      query = query.is("client_msg_id", null).order("deleted_at", { ascending: true });
    } else {
      query = query.order("created_at", { ascending: true });
    }
    const { data, error } = await query.range(from, from + PAGE_SIZE - 1);
    if (error) throw new Error(`${table}: ${error.message}`);
    rows.push(...((data ?? []) as Array<{ channel_name?: string | null; created_at?: string | null; client_msg_id?: string | null }>));
    if (!data || data.length < PAGE_SIZE) break;
  }

  return rows;
}

export async function listChannelsWithHistoryFallback(
  supabaseClient: unknown,
  botId: string,
): Promise<VisibleChannel[]> {
  const supabase = supabaseClient as SupabaseLike;
  const channelQuery = supabase
    .from("app_channels")
    .select(APP_CHANNEL_SELECT)
    .eq("bot_id", botId);
  let channelResult = await channelQuery.order("position", { ascending: true });

  if (
    channelResult.error &&
    /model_selection|router_type/i.test(channelResult.error.message)
  ) {
    const legacyChannelQuery = supabase
      .from("app_channels")
      .select(APP_CHANNEL_SELECT_LEGACY)
      .eq("bot_id", botId);
    channelResult = await legacyChannelQuery.order("position", { ascending: true });
  }

  if (channelResult.error) {
    throw new Error(`app_channels: ${channelResult.error.message}`);
  }

  const [chatRows, appRows, deletedRows] = await Promise.all([
    fetchAllRows(supabase, "chat_messages", botId),
    fetchAllRows(supabase, "app_channel_messages", botId),
    fetchAllRows(supabase, "chat_message_deletions", botId),
  ]);

  return mergeChannelsWithHistory(
    (channelResult.data ?? []) as Parameters<typeof mergeChannelsWithHistory>[0],
    [...chatRows, ...appRows],
    deletedRows as DeletedChannelRow[],
  );
}
