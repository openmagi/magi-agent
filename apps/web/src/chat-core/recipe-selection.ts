export type ChatRecipeSelectionMode = "auto" | "session" | "this_turn";

export type ExplicitRecipeSelectionMode = "session" | "this_turn";

export interface PublicRecipeRef {
  recipeId: string;
  version?: string;
  digest?: string;
}

export interface ChatRecipeOption extends PublicRecipeRef {
  label?: string;
  description?: string;
  disabled?: boolean;
  reasonCode?: string;
}

export interface ExplicitRecipeSelectionRequest {
  explicitRecipeSelection: {
    mode: ExplicitRecipeSelectionMode;
    requiredRecipeRefs: PublicRecipeRef[];
    allowAdditionalAutoRecipes: true;
  };
}

const PUBLIC_ID_RE = /^[a-zA-Z0-9._:-]{1,160}$/;
const DIGEST_RE = /^sha256:[a-f0-9]{64}$/;
const UNSAFE_TEXT_RE =
  /api[._-]?key|auth|authorization|bearer|cookie|connector[._-]?token|google[._-]?adk|hidden[._-]?config|hidden[._-]?reasoning|model[._-]?output|private|prompt|raw|secret|session|token|tool[._-]?(?:args?|logs?|results?)|transcript/i;
const SECRET_SHAPE_RE =
  /(?:^|[^a-z0-9])(?:sk-[a-z0-9_-]{6,}|sk-proj-[a-z0-9_-]{6,}|github_pat_[a-z0-9_]{12,}|gh[pousr]_[a-z0-9_]{12,}|xox[abprs]-[a-z0-9-]{12,}|akia[0-9a-z]{16}|eyj[a-z0-9_-]{8,}\.[a-z0-9_-]{8,}\.[a-z0-9_-]{8,})(?:$|[^a-z0-9])/i;
const PRIVATE_PATH_RE =
  /(?:^|[^\w])(?:\/(?:Users|srv|var|etc|home|root|app|tmp)\/|[a-z]:\\|\.env\b|runtime\.sqlite\b)/i;

function record(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null;
}

function safePublicId(value: unknown): string | undefined {
  if (typeof value !== "string" || !PUBLIC_ID_RE.test(value)) return undefined;
  if (UNSAFE_TEXT_RE.test(value) || SECRET_SHAPE_RE.test(value)) return undefined;
  return value;
}

function safeDigest(value: unknown): string | undefined {
  return typeof value === "string" && DIGEST_RE.test(value) ? value : undefined;
}

function safePublicText(value: unknown, maxLength = 96): string | undefined {
  if (typeof value !== "string") return undefined;
  const trimmed = value.trim();
  if (!trimmed || trimmed.length > maxLength) return undefined;
  if (
    UNSAFE_TEXT_RE.test(trimmed) ||
    SECRET_SHAPE_RE.test(trimmed) ||
    PRIVATE_PATH_RE.test(trimmed)
  ) return undefined;
  return trimmed;
}

export function sanitizeRecipeRef(value: unknown): PublicRecipeRef | null {
  const item = record(value);
  if (!item) return null;
  const recipeId = safePublicId(item.recipeId);
  if (!recipeId) return null;
  const version = safePublicId(item.version);
  const digest = safeDigest(item.digest);
  return {
    recipeId,
    ...(version ? { version } : {}),
    ...(digest ? { digest } : {}),
  };
}

export function sanitizeChatRecipeOption(value: unknown): ChatRecipeOption | null {
  const item = record(value);
  if (!item) return null;
  const ref = sanitizeRecipeRef(item);
  if (!ref) return null;
  const label = safePublicText(item.label);
  const description = safePublicText(item.description, 180);
  const reasonCode = safePublicId(item.reasonCode);
  return {
    ...ref,
    ...(label ? { label } : {}),
    ...(description ? { description } : {}),
    ...(item.disabled === true ? { disabled: true } : {}),
    ...(reasonCode ? { reasonCode } : {}),
  };
}

export function buildExplicitRecipeSelection(
  mode: ChatRecipeSelectionMode,
  recipe: unknown,
): ExplicitRecipeSelectionRequest | undefined {
  if (mode === "auto") return undefined;
  const ref = sanitizeRecipeRef(recipe);
  if (!ref) return undefined;
  return {
    explicitRecipeSelection: {
      mode,
      requiredRecipeRefs: [ref],
      allowAdditionalAutoRecipes: true,
    },
  };
}

export function sanitizeExplicitRecipeSelection(
  value: unknown,
): ExplicitRecipeSelectionRequest["explicitRecipeSelection"] | undefined {
  const item = record(value);
  if (!item || (item.mode !== "session" && item.mode !== "this_turn")) return undefined;
  if (item.allowAdditionalAutoRecipes !== true) return undefined;
  if (!Array.isArray(item.requiredRecipeRefs)) return undefined;
  const requiredRecipeRefs: PublicRecipeRef[] = [];
  for (const recipe of item.requiredRecipeRefs) {
    const ref = sanitizeRecipeRef(recipe);
    if (!ref) return undefined;
    requiredRecipeRefs.push(ref);
  }
  if (requiredRecipeRefs.length === 0) return undefined;
  return {
    mode: item.mode,
    requiredRecipeRefs,
    allowAdditionalAutoRecipes: true,
  };
}

export const CHAT_RECIPE_OPTIONS_FIXTURE: ChatRecipeOption[] = [
  {
    recipeId: "openmagi.research",
    label: "Cited Source Preview",
    description: "Request the source-cited research recipe; runtime admission decides.",
  },
  {
    recipeId: "openmagi.document-review",
    label: "Office Draft Review",
    description: "Request the document drafting review recipe; runtime admission decides.",
  },
];
