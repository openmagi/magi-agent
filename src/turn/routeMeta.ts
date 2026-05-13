export type RouteMetaLanguage = "en" | "ko" | "ja" | "zh" | "es";

export type CanonicalRoute = "direct" | "subagent" | "subagent->gate" | "pipeline";
export type CanonicalComplexity = "simple" | "complex";

interface MetaField {
  key: string;
  value: string;
}

const ROUTE_META_TAG_RE = /^\[META\s*:\s*([\s\S]*?)\]$/i;

const ROUTE_ALIASES: Record<CanonicalRoute, readonly string[]> = {
  direct: ["direct", "directo", "직접", "바로", "直接", "直接处理", "直接処理"],
  subagent: ["subagent", "sub-agent", "서브에이전트", "하위에이전트", "サブエージェント", "子代理", "subagente"],
  "subagent->gate": [
    "subagent->gate",
    "subagent-gate",
    "서브에이전트->승인",
    "서브에이전트-승인",
    "하위에이전트->승인",
    "サブエージェント->承認",
    "子代理->审批",
    "subagente->aprobacion",
    "subagente->aprobación",
  ],
  pipeline: ["pipeline", "파이프라인", "パイプライン", "流水线", "canalizacion", "canalización"],
};

const COMPLEXITY_ALIASES: Record<CanonicalComplexity, readonly string[]> = {
  simple: ["simple", "단순", "간단", "簡単", "简单", "sencillo"],
  complex: ["complex", "복잡", "複雑", "复杂", "complejo"],
};

const ROUTE_LABELS: Record<RouteMetaLanguage, Record<CanonicalRoute, string>> = {
  en: {
    direct: "direct",
    subagent: "subagent",
    "subagent->gate": "subagent->gate",
    pipeline: "pipeline",
  },
  ko: {
    direct: "직접",
    subagent: "서브에이전트",
    "subagent->gate": "서브에이전트->승인",
    pipeline: "파이프라인",
  },
  ja: {
    direct: "直接",
    subagent: "サブエージェント",
    "subagent->gate": "サブエージェント->承認",
    pipeline: "パイプライン",
  },
  zh: {
    direct: "直接",
    subagent: "子代理",
    "subagent->gate": "子代理->审批",
    pipeline: "流水线",
  },
  es: {
    direct: "directo",
    subagent: "subagente",
    "subagent->gate": "subagente->aprobación",
    pipeline: "canalización",
  },
};

const COMPLEXITY_LABELS: Record<RouteMetaLanguage, Record<CanonicalComplexity, string>> = {
  en: { simple: "simple", complex: "complex" },
  ko: { simple: "단순", complex: "복잡" },
  ja: { simple: "簡単", complex: "複雑" },
  zh: { simple: "简单", complex: "复杂" },
  es: { simple: "simple", complex: "complejo" },
};

const INTENT_LABELS: Record<RouteMetaLanguage, Record<string, string>> = {
  en: {
    conversation: "conversation",
    question: "question",
    execution: "execution",
    research: "research",
  },
  ko: {
    conversation: "대화",
    question: "질문",
    execution: "실행",
    research: "리서치",
  },
  ja: {
    conversation: "会話",
    question: "質問",
    execution: "実行",
    research: "リサーチ",
  },
  zh: {
    conversation: "对话",
    question: "问题",
    execution: "执行",
    research: "研究",
  },
  es: {
    conversation: "conversación",
    question: "pregunta",
    execution: "ejecución",
    research: "investigación",
  },
};

const DOMAIN_LABELS: Record<RouteMetaLanguage, Record<string, string>> = {
  en: {
    daily: "daily",
    "document writing": "document writing",
    legal: "legal",
    research: "research",
    development: "development",
    "coding/testing": "coding/testing",
    "AI orchestration": "AI orchestration",
    "knowledge base": "knowledge base",
  },
  ko: {
    daily: "일상",
    "document writing": "문서작성",
    legal: "법률",
    research: "연구",
    development: "개발",
    "coding/testing": "코딩/실험",
    "AI orchestration": "AI오케스트레이션",
    "knowledge base": "지식베이스",
  },
  ja: {
    daily: "日常",
    "document writing": "文書作成",
    legal: "法務",
    research: "研究",
    development: "開発",
    "coding/testing": "コーディング/テスト",
    "AI orchestration": "AIオーケストレーション",
    "knowledge base": "ナレッジベース",
  },
  zh: {
    daily: "日常",
    "document writing": "文档写作",
    legal: "法律",
    research: "研究",
    development: "开发",
    "coding/testing": "编码/测试",
    "AI orchestration": "AI编排",
    "knowledge base": "知识库",
  },
  es: {
    daily: "diario",
    "document writing": "redacción de documentos",
    legal: "legal",
    research: "investigación",
    development: "desarrollo",
    "coding/testing": "programación/pruebas",
    "AI orchestration": "orquestación de IA",
    "knowledge base": "base de conocimiento",
  },
};

const INTENT_ALIASES: Record<string, readonly string[]> = {
  conversation: ["conversation", "chat", "대화", "会話", "对话", "conversación", "conversacion"],
  question: ["question", "질문", "質問", "问题", "pregunta"],
  execution: ["execution", "execute", "task", "실행", "수행", "実行", "执行", "ejecución", "ejecucion"],
  research: ["research", "리서치", "조사", "研究", "investigación", "investigacion"],
};

const DOMAIN_ALIASES: Record<string, readonly string[]> = {
  daily: ["daily", "casual", "일상", "日常", "diario"],
  "document writing": [
    "document writing",
    "docs",
    "writing",
    "문서작성",
    "문서 작성",
    "文書作成",
    "文档写作",
    "redacción de documentos",
    "redaccion de documentos",
  ],
  legal: ["legal", "law", "법률", "법무", "法務", "法律"],
  research: ["research", "연구", "조사", "研究", "investigación", "investigacion"],
  development: ["development", "coding", "code", "개발", "코딩", "開発", "开发", "desarrollo"],
  "coding/testing": [
    "coding/testing",
    "coding / testing",
    "coding test",
    "coding tests",
    "coding experiment",
    "coding experiments",
    "코딩/실험",
    "코딩 실험",
    "코딩/테스트",
  ],
  "AI orchestration": [
    "AI orchestration",
    "AI오케스트레이션",
    "AI 오케스트레이션",
    "에이아이 오케스트레이션",
  ],
  "knowledge base": ["knowledge base", "kb", "지식베이스", "ナレッジベース", "知识库", "base de conocimiento"],
};

export function inferRouteMetaLanguage(text: string): RouteMetaLanguage | null {
  const visible = text.replace(/\[META\s*:[^\]]*\]/gi, " ");
  if (/[\uac00-\ud7af]/.test(visible)) return "ko";
  if (/[\u3040-\u30ff]/.test(visible)) return "ja";
  if (/[\u4e00-\u9fff]/.test(visible)) return "zh";
  if (/[¿¡áéíóúñü]/i.test(visible)) return "es";
  if (/[A-Za-z]/.test(visible)) return "en";
  return null;
}

export function isRouteMetaTag(text: string): boolean {
  const parsed = parseMetaTagFields(text);
  if (!parsed) return false;
  return parsed.some((field) =>
    ["intent", "domain", "complexity", "route"].includes(field.key.toLowerCase()),
  );
}

export function localizeRouteMetaTag(
  tag: string,
  language: RouteMetaLanguage | null,
): string {
  if (!language) return tag;
  const fields = parseMetaTagFields(tag);
  if (!fields) return tag;
  return `[META: ${fields.map((field) => `${field.key}=${localizeMetaValue(field, language)}`).join(", ")}]`;
}

export function normalizeRouteValue(value: string | null | undefined): CanonicalRoute | null {
  return normalizeFromAliases(value, ROUTE_ALIASES);
}

export function normalizeComplexityValue(
  value: string | null | undefined,
): CanonicalComplexity | null {
  return normalizeFromAliases(value, COMPLEXITY_ALIASES);
}

function localizeMetaValue(field: MetaField, language: RouteMetaLanguage): string {
  const key = field.key.toLowerCase();
  if (key === "route") {
    const route = normalizeRouteValue(field.value);
    return route ? ROUTE_LABELS[language][route] : field.value;
  }
  if (key === "complexity") {
    const complexity = normalizeComplexityValue(field.value);
    return complexity ? COMPLEXITY_LABELS[language][complexity] : field.value;
  }
  if (key === "intent") {
    const intent = normalizeFromAliases(field.value, INTENT_ALIASES);
    return intent ? (INTENT_LABELS[language][intent] ?? field.value) : field.value;
  }
  if (key === "domain") {
    const domain = normalizeFromAliases(field.value, DOMAIN_ALIASES);
    return domain ? (DOMAIN_LABELS[language][domain] ?? field.value) : field.value;
  }
  return field.value;
}

function parseMetaTagFields(tag: string): MetaField[] | null {
  const match = ROUTE_META_TAG_RE.exec(tag.trim());
  if (!match) return null;
  const body = match[1] ?? "";
  const fields = body
    .split(",")
    .map((part) => {
      const eq = part.indexOf("=");
      if (eq === -1) return null;
      const key = part.slice(0, eq).trim();
      const value = part.slice(eq + 1).trim();
      return key && value ? { key, value } : null;
    })
    .filter((field): field is MetaField => field !== null);
  return fields.length > 0 ? fields : null;
}

function normalizeFromAliases<T extends string>(
  value: string | null | undefined,
  aliases: Record<T, readonly string[]>,
): T | null {
  if (!value) return null;
  const normalized = normalizeAlias(value);
  for (const [canonical, values] of Object.entries(aliases) as Array<[T, readonly string[]]>) {
    if (values.some((candidate) => normalizeAlias(candidate) === normalized)) {
      return canonical;
    }
  }
  return null;
}

function normalizeAlias(value: string): string {
  return value
    .trim()
    .toLowerCase()
    .normalize("NFKD")
    .replace(/[\u0300-\u036f]/g, "")
    .replace(/\s+/g, " ");
}
