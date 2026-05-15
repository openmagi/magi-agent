import { NextResponse } from "next/server";
// eslint-disable-next-line @typescript-eslint/no-explicit-any
type AdminClient = any;

interface SupabaseResult<T> {
  data: T | null;
  error?: { message?: string } | null;
}

export interface PersonalKnowledgeBotRow {
  id: string;
  org_id?: string | null;
  kb_storage_used_bytes?: number | null;
}

export interface PersonalKnowledgeCollectionRow {
  id: string;
  name: string;
  bot_id: string;
  document_count?: number | null;
  total_chunks?: number | null;
  created_at: string;
  scope?: string | null;
  error_count?: number | null;
}

export interface ResolvedPersonalKnowledgeScope {
  userId: string;
  botIds: string[];
  ownerBotId: string;
  ownerOrgId: string | null;
  storageUsedBytes: number;
}

export interface PersonalKnowledgeCollectionTarget extends ResolvedPersonalKnowledgeScope {
  botId: string;
  collectionId: string;
  collectionName: string;
}

export type PersonalKnowledgeResult<T> =
  | { ok: true; data: T }
  | { ok: false; response: NextResponse };

async function executeSupabase<T>(query: unknown): Promise<SupabaseResult<T>> {
  return (await (query as PromiseLike<SupabaseResult<T>>));
}

function createdAtMs(value: string | null | undefined): number {
  const parsed = Date.parse(value ?? "");
  return Number.isFinite(parsed) ? parsed : 0;
}

export function resolveAccessiblePersonalKnowledgeOrgId(
  botOrgId: string | null | undefined,
  userOrgId: string | null | undefined,
): string | null {
  return botOrgId || userOrgId || null;
}

export async function listPersonalKnowledgeBots(
  supabase: AdminClient,
  userId: string,
): Promise<PersonalKnowledgeBotRow[]> {
  const { data } = await executeSupabase<PersonalKnowledgeBotRow[]>(
    supabase
      .from("bots")
      .select("id, org_id, kb_storage_used_bytes")
      .eq("user_id", userId)
      .neq("status", "deleted")
      .order("created_at", { ascending: false }),
  );
  return data ?? [];
}

export async function resolveUserKnowledgeOrgId(
  supabase: AdminClient,
  userId: string,
): Promise<string | null> {
  const { data } = await executeSupabase<Array<{ org_id: string | null }>>(
    supabase
      .from("org_members")
      .select("org_id")
      .eq("user_id", userId)
      .limit(1),
  );

  return data?.find((membership) => membership.org_id)?.org_id ?? null;
}

export async function resolvePersonalKnowledgeScope(
  supabase: AdminClient,
  userId: string,
  requestedBotId?: string | null,
): Promise<PersonalKnowledgeResult<ResolvedPersonalKnowledgeScope>> {
  const bots = await listPersonalKnowledgeBots(supabase, userId);
  if (bots.length === 0) {
    return {
      ok: false,
      response: NextResponse.json({ error: "no bots for this user" }, { status: 400 }),
    };
  }

  const ownerBot = requestedBotId
    ? bots.find((bot) => bot.id === requestedBotId)
    : bots[0];

  if (!ownerBot) {
    return {
      ok: false,
      response: NextResponse.json({ error: "bot not found" }, { status: 404 }),
    };
  }

  const userOrgId = ownerBot.org_id ? null : await resolveUserKnowledgeOrgId(supabase, userId);

  return {
    ok: true,
    data: {
      userId,
      botIds: bots.map((bot) => bot.id),
      ownerBotId: ownerBot.id,
      ownerOrgId: resolveAccessiblePersonalKnowledgeOrgId(ownerBot.org_id, userOrgId),
      storageUsedBytes: bots.reduce((sum, bot) => sum + (bot.kb_storage_used_bytes ?? 0), 0),
    },
  };
}

export function mergePersonalKnowledgeCollections(
  collections: PersonalKnowledgeCollectionRow[],
): PersonalKnowledgeCollectionRow[] {
  const grouped = new Map<string, PersonalKnowledgeCollectionRow>();

  for (const collection of collections) {
    const existing = grouped.get(collection.name);
    if (!existing) {
      grouped.set(collection.name, {
        ...collection,
        id: `personal:${collection.name}`,
        scope: "personal",
        document_count: collection.document_count ?? 0,
        total_chunks: collection.total_chunks ?? 0,
        error_count: collection.error_count ?? 0,
      });
      continue;
    }

    grouped.set(collection.name, {
      ...existing,
      created_at: createdAtMs(collection.created_at) > createdAtMs(existing.created_at)
        ? collection.created_at
        : existing.created_at,
      document_count: (existing.document_count ?? 0) + (collection.document_count ?? 0),
      total_chunks: (existing.total_chunks ?? 0) + (collection.total_chunks ?? 0),
      error_count: (existing.error_count ?? 0) + (collection.error_count ?? 0),
    });
  }

  return Array.from(grouped.values()).sort((a, b) => {
    const byCreated = createdAtMs(b.created_at) - createdAtMs(a.created_at);
    if (byCreated !== 0) return byCreated;
    return a.name.localeCompare(b.name);
  });
}

export async function findPersonalKnowledgeCollectionsByName(
  supabase: AdminClient,
  botIds: string[],
  collectionName: string,
): Promise<PersonalKnowledgeCollectionRow[]> {
  if (botIds.length === 0) return [];

  const { data } = await executeSupabase<PersonalKnowledgeCollectionRow[]>(
    supabase
      .from("knowledge_collections")
      .select("id, name, bot_id, document_count, total_chunks, created_at, scope")
      .in("bot_id", botIds)
      .eq("name", collectionName)
      .order("created_at", { ascending: false }),
  );
  return data ?? [];
}

export async function resolvePersonalKnowledgeCollectionTarget(
  supabase: AdminClient,
  userId: string,
  collectionName: string,
  requestedBotId?: string | null,
): Promise<PersonalKnowledgeResult<PersonalKnowledgeCollectionTarget>> {
  const scope = await resolvePersonalKnowledgeScope(supabase, userId, requestedBotId);
  if (!scope.ok) return scope;

  const collections = await findPersonalKnowledgeCollectionsByName(
    supabase,
    scope.data.botIds,
    collectionName,
  );

  if (collections.length === 0) {
    return {
      ok: false,
      response: NextResponse.json({ error: "Collection not found" }, { status: 404 }),
    };
  }

  const collection =
    collections.find((row) => row.bot_id === scope.data.ownerBotId) ??
    collections[0];

  return {
    ok: true,
    data: {
      ...scope.data,
      botId: collection.bot_id,
      collectionId: collection.id,
      collectionName: collection.name,
    },
  };
}
