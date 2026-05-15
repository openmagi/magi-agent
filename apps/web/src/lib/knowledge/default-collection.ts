export const DEFAULT_CHAT_UPLOAD_COLLECTION = "Downloads";

interface CollectionRow {
  id: string;
  name: string;
}

interface QueryResult<T> {
  data: T | null;
  error: {
    code?: string;
    message?: string;
  } | null;
}

interface CollectionSelectBuilder {
  eq(field: string, value: unknown): CollectionSelectBuilder;
  maybeSingle(): Promise<QueryResult<CollectionRow>>;
}

interface CollectionInsertBuilder {
  select(columns?: string): {
    single(): Promise<QueryResult<CollectionRow>>;
  };
}

interface KnowledgeCollectionsClient {
  from(table: "knowledge_collections"): {
    select(columns?: string): CollectionSelectBuilder;
    insert(values: Record<string, unknown>): CollectionInsertBuilder;
  };
}

function isDuplicateInsertError(error: QueryResult<CollectionRow>["error"]): boolean {
  const message = error?.message?.toLowerCase() ?? "";
  return error?.code === "23505" || message.includes("duplicate");
}

export async function ensurePersonalDefaultCollection(
  supabase: KnowledgeCollectionsClient,
  botId: string,
  name = DEFAULT_CHAT_UPLOAD_COLLECTION,
): Promise<CollectionRow> {
  const existing = await supabase
    .from("knowledge_collections")
    .select("id,name")
    .eq("bot_id", botId)
    .eq("name", name)
    .maybeSingle();

  if (existing.data) {
    return existing.data;
  }

  const inserted = await supabase
    .from("knowledge_collections")
    .insert({
      bot_id: botId,
      name,
    })
    .select("id,name")
    .single();

  if (inserted.data) {
    return inserted.data;
  }

  if (isDuplicateInsertError(inserted.error)) {
    const retry = await supabase
      .from("knowledge_collections")
      .select("id,name")
      .eq("bot_id", botId)
      .eq("name", name)
      .maybeSingle();

    if (retry.data) {
      return retry.data;
    }
  }

  throw new Error(inserted.error?.message || "Failed to ensure default collection");
}
