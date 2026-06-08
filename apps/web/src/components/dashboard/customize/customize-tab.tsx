"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Activity,
  AlertTriangle,
  BookOpen,
  Brain,
  CheckCircle2,
  FileCheck,
  FlaskConical,
  Gauge,
  GitBranch,
  Hammer,
  PlugZap,
  RefreshCw,
  Route,
  Settings,
  ShieldCheck,
  Wrench,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { useAgentFetch } from "@/lib/local-api";

type JsonRecord = Record<string, unknown>;
type ToolSource = "builtin" | "skill" | "external";
type Tone = "blue" | "green" | "amber" | "red" | "purple" | "slate";

interface CustomizeRuntimeConsoleProps {
  botId: string;
}

interface ToolStats {
  calls: number;
  errors: number;
  avgDurationMs: number;
}

interface ToolMetadata {
  name: string;
  description: string;
  permission: string;
  kind: string;
  enabled: boolean;
  source: ToolSource;
  isConcurrencySafe: boolean;
  dangerous: boolean;
  tags: string[];
  stats: ToolStats;
}

interface SkillDirectoryItem {
  name: string;
  path: string;
  tags: string[];
  promptOnly: boolean;
  scriptBacked: boolean;
  runtimeHooks: number;
}

interface RuntimeHook {
  name: string;
  point: string;
  source: string;
  path: string;
}

interface RuntimeConfig {
  provider: string;
  model: string;
  workspace: string;
  apiKeySet: boolean;
  apiKeyEnvVar: string;
  baseUrl: string;
  contextWindow: string;
  maxOutputTokens: string;
}

interface RuntimeSnapshot {
  tools: ToolMetadata[];
  skills: SkillDirectoryItem[];
  hooks: RuntimeHook[];
  issueCount: number;
  config: RuntimeConfig | null;
}

interface CatalogEntry {
  id: string;
  title: string;
  description: string;
  surfaces: string[];
  source: string;
  icon: LucideIcon;
  tone: Tone;
}

export const FIRST_PARTY_RECIPES: CatalogEntry[] = [
  {
    id: "openmagi.research",
    title: "Research",
    description: "Source proof, claim graph, synthesis, cross-review, and final projection contracts.",
    surfaces: ["source-ledger", "claim-citation", "research-child-runner"],
    source: "docs/recipes.md",
    icon: BookOpen,
    tone: "blue",
  },
  {
    id: "coding.evidence_gate",
    title: "Coding Evidence Gate",
    description: "Read-before-edit, patch/diff/test evidence, stale rejection, and completion blocking.",
    surfaces: ["beforeCommit", "test evidence", "workspace scope"],
    source: "magi_agent/recipes/coding_evidence_gate.py",
    icon: FileCheck,
    tone: "green",
  },
  {
    id: "coding.mutation",
    title: "Coding Mutation",
    description: "File mutation policy, approval receipts, and deterministic workspace change evidence.",
    surfaces: ["ToolHost", "approval receipts", "GitDiff"],
    source: "magi_agent/recipes/coding_mutation.py",
    icon: Hammer,
    tone: "green",
  },
  {
    id: "automation.package_boundary",
    title: "General Automation",
    description: "Planning, files, browser inspection, office work, delivery receipts, and package boundaries.",
    surfaces: ["package manifest", "browser evidence", "delivery receipts"],
    source: "magi_agent/harness/general_automation",
    icon: PlugZap,
    tone: "purple",
  },
  {
    id: "memory.recall",
    title: "Memory Recall",
    description: "Recall authority, source-safe projection, compaction continuity, and memory source checks.",
    surfaces: ["memory-ledger", "beforeCompaction", "afterCompaction"],
    source: "magi_agent/recipes/first_party/memory_recall.py",
    icon: Brain,
    tone: "amber",
  },
  {
    id: "self_improvement.promotion",
    title: "Self Improvement",
    description: "Eval capture, review gates, promotion scope, rollback, and drift watch contracts.",
    surfaces: ["review gate", "rollback", "drift watch"],
    source: "magi_agent/self_improvement",
    icon: GitBranch,
    tone: "slate",
  },
];

export const HARNESS_PRESETS: CatalogEntry[] = [
  {
    id: "answer-quality",
    title: "Answer Quality",
    description: "Checks whether the response answered the user request before it can be committed.",
    surfaces: ["afterLLMCall", "beforeCommit"],
    source: "docs/harness-schema.md",
    icon: CheckCircle2,
    tone: "green",
  },
  {
    id: "fact-grounding",
    title: "Fact Grounding",
    description: "Requires important factual claims to be grounded in tool or source evidence.",
    surfaces: ["afterLLMCall", "source-ledger"],
    source: "docs/harness-schema.md",
    icon: BookOpen,
    tone: "blue",
  },
  {
    id: "deterministic-evidence",
    title: "Deterministic Evidence",
    description: "Pairs numbers, files, calculations, and test claims with deterministic receipts.",
    surfaces: ["afterToolUse", "afterLLMCall"],
    source: "docs/harness-schema.md",
    icon: Gauge,
    tone: "purple",
  },
  {
    id: "coding-verification",
    title: "Coding Verification",
    description: "Blocks false completion claims until requested code evidence exists.",
    surfaces: ["beforeCommit", "block-on-fail"],
    source: "docs/harness-schema.md",
    icon: FileCheck,
    tone: "green",
  },
  {
    id: "source-authority",
    title: "Source Authority",
    description: "Keeps research source receipts authoritative across synthesis and projection.",
    surfaces: ["afterToolUse", "afterLLMCall"],
    source: "docs/harness-schema.md",
    icon: ShieldCheck,
    tone: "blue",
  },
  {
    id: "hard-safety",
    title: "Hard Safety",
    description: "Dangerous patterns, path escape, secret exposure, git safety, and sealed-file checks.",
    surfaces: ["beforeToolUse", "beforeCommit", "fail-closed"],
    source: "docs/harness-schema.md",
    icon: AlertTriangle,
    tone: "red",
  },
];

export const PHASE_ROUTES: CatalogEntry[] = [
  {
    id: "beforeTurnStart",
    title: "Turn admission",
    description: "Normalize the request, bind session policy, and initialize runtime-only state.",
    surfaces: ["admission", "policy snapshot", "harness resolution"],
    source: "magi_agent/runtime/admission.py",
    icon: Route,
    tone: "slate",
  },
  {
    id: "beforeLLMCall",
    title: "Context packet",
    description: "Project allowed context, recipe contracts, and memory/source state into the model-visible packet.",
    surfaces: ["message_builder", "context_projection", "prompt_snapshot"],
    source: "magi_agent/runtime/message_builder.py",
    icon: Brain,
    tone: "blue",
  },
  {
    id: "beforeToolUse",
    title: "Tool boundary",
    description: "Approve, deny, replace, or block proposed tool calls before activity crosses ToolHost.",
    surfaces: ["ToolHost", "permission", "approval receipts"],
    source: "magi_agent/tools/core_toolhost.py",
    icon: Wrench,
    tone: "amber",
  },
  {
    id: "afterToolUse",
    title: "Evidence extraction",
    description: "Normalize tool output and attach source, file, calculation, delivery, or test receipts.",
    surfaces: ["evidence ledger", "tool receipts", "event projection"],
    source: "magi_agent/tools/event_projection.py",
    icon: FileCheck,
    tone: "green",
  },
  {
    id: "beforeCommit",
    title: "Commit boundary",
    description: "Run validators before final answer, artifact, child result, memory write, or external delivery projection.",
    surfaces: ["commit_boundary", "verifier_bus", "block plans"],
    source: "magi_agent/runtime/commit_boundary.py",
    icon: ShieldCheck,
    tone: "red",
  },
  {
    id: "afterCommit",
    title: "Public projection",
    description: "Emit public-safe output, audit metadata, and runtime events after accepted state transitions.",
    surfaces: ["projection_write_boundary", "runtime events", "audit"],
    source: "magi_agent/runtime/projection_write_boundary.py",
    icon: Activity,
    tone: "purple",
  },
];

export const REPAIR_CONTROLS: CatalogEntry[] = [
  {
    id: "repair_required",
    title: "Repair",
    description: "Gather missing evidence and retry when the verdict is block-ready and repair is allowed.",
    surfaces: ["repair_allowed", "retryMessage", "block plans"],
    source: "docs/repair-fallback.md",
    icon: RefreshCw,
    tone: "green",
  },
  {
    id: "escalate_required",
    title: "Escalate",
    description: "Ask for higher authority or human review when evidence cannot be repaired locally.",
    surfaces: ["escalation_allowed", "approval receipts"],
    source: "docs/repair-fallback.md",
    icon: ShieldCheck,
    tone: "amber",
  },
  {
    id: "block_intent",
    title: "Block intent",
    description: "Record the local block intent when hard enforcement is not enabled for that contract.",
    surfaces: ["block_ready_local_fake", "audit ledger"],
    source: "docs/repair-fallback.md",
    icon: AlertTriangle,
    tone: "red",
  },
  {
    id: "audit_missing",
    title: "Audit",
    description: "Log missing or failed evidence when a contract is configured for observation instead of blocking.",
    surfaces: ["audit", "reason codes", "policy state"],
    source: "docs/repair-fallback.md",
    icon: FlaskConical,
    tone: "purple",
  },
];

const EMPTY_SNAPSHOT: RuntimeSnapshot = {
  tools: [],
  skills: [],
  hooks: [],
  issueCount: 0,
  config: null,
};

const TONE_CLASSES: Record<Tone, { border: string; badge: string; icon: string; text: string }> = {
  blue: {
    border: "border-blue-500/20",
    badge: "border-blue-500/20 bg-blue-500/[0.08] text-blue-700",
    icon: "bg-blue-500/[0.10] text-blue-700",
    text: "text-blue-700",
  },
  green: {
    border: "border-emerald-500/20",
    badge: "border-emerald-500/20 bg-emerald-500/[0.08] text-emerald-700",
    icon: "bg-emerald-500/[0.10] text-emerald-700",
    text: "text-emerald-700",
  },
  amber: {
    border: "border-amber-500/25",
    badge: "border-amber-500/25 bg-amber-500/[0.10] text-amber-700",
    icon: "bg-amber-500/[0.12] text-amber-700",
    text: "text-amber-700",
  },
  red: {
    border: "border-red-500/20",
    badge: "border-red-500/20 bg-red-500/[0.08] text-red-600",
    icon: "bg-red-500/[0.10] text-red-600",
    text: "text-red-600",
  },
  purple: {
    border: "border-violet-500/20",
    badge: "border-violet-500/20 bg-violet-500/[0.08] text-violet-700",
    icon: "bg-violet-500/[0.10] text-violet-700",
    text: "text-violet-700",
  },
  slate: {
    border: "border-black/[0.08]",
    badge: "border-black/[0.08] bg-black/[0.035] text-secondary",
    icon: "bg-black/[0.05] text-secondary",
    text: "text-secondary",
  },
};

function isRecord(value: unknown): value is JsonRecord {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function asString(value: unknown, fallback = ""): string {
  return typeof value === "string" ? value : fallback;
}

function asNumber(value: unknown, fallback = 0): number {
  return typeof value === "number" && Number.isFinite(value) ? value : fallback;
}

function asBoolean(value: unknown, fallback = false): boolean {
  return typeof value === "boolean" ? value : fallback;
}

function asRecordArray(value: unknown): JsonRecord[] {
  return Array.isArray(value)
    ? value.filter((item): item is JsonRecord => isRecord(item))
    : [];
}

function asStringArray(value: unknown): string[] {
  return Array.isArray(value)
    ? value.filter((item): item is string => typeof item === "string")
    : [];
}

async function readJson(response: Response): Promise<JsonRecord> {
  const data: unknown = await response.json().catch(() => ({}));
  return isRecord(data) ? data : {};
}

function normalizeTool(tool: JsonRecord): ToolMetadata {
  const kind = asString(tool.kind, "core");
  const source: ToolSource = kind === "skill" ? "skill" : kind === "external" ? "external" : "builtin";
  const rawStats = isRecord(tool.stats) ? tool.stats : {};
  return {
    name: asString(tool.name, "unnamed-tool"),
    description: asString(tool.description),
    permission: asString(tool.permission, "read"),
    kind,
    enabled: tool.enabled !== false,
    source,
    isConcurrencySafe: tool.isConcurrencySafe !== false,
    dangerous: tool.dangerous === true,
    tags: asStringArray(tool.tags),
    stats: {
      calls: asNumber(rawStats.calls),
      errors: asNumber(rawStats.errors),
      avgDurationMs: asNumber(rawStats.avgDurationMs),
    },
  };
}

function normalizeSkill(skill: JsonRecord, index: number): SkillDirectoryItem {
  return {
    name: asString(skill.name, `skill-${index + 1}`),
    path: asString(skill.path, asString(skill.dir)),
    tags: asStringArray(skill.tags),
    promptOnly: skill.promptOnly === true,
    scriptBacked: skill.scriptBacked === true,
    runtimeHooks: Math.max(0, Math.floor(asNumber(skill.runtimeHooks))),
  };
}

function normalizeHook(hook: JsonRecord, index: number): RuntimeHook {
  return {
    name: asString(hook.name, asString(hook.skillName, `hook-${index + 1}`)),
    point: asString(hook.point, asString(hook.kind, "runtime")),
    source: asString(hook.source, asString(hook.skillName, "skill")),
    path: asString(hook.path, asString(hook.entry, asString(hook.command))),
  };
}

function normalizeConfig(data: JsonRecord): RuntimeConfig | null {
  const config = isRecord(data.config) ? data.config : {};
  const llm = isRecord(config.llm) ? config.llm : {};
  const capabilities = isRecord(llm.capabilities) ? llm.capabilities : {};
  return {
    provider: asString(llm.provider, "not configured"),
    model: asString(llm.model, "provider default"),
    workspace: asString(config.workspace, "./workspace"),
    apiKeySet: asBoolean(llm.apiKeySet),
    apiKeyEnvVar: asString(llm.apiKeyEnvVar),
    baseUrl: asString(llm.baseUrl),
    contextWindow: asNumber(capabilities.contextWindow) > 0 ? String(asNumber(capabilities.contextWindow)) : "provider default",
    maxOutputTokens: asNumber(capabilities.maxOutputTokens) > 0 ? String(asNumber(capabilities.maxOutputTokens)) : "provider default",
  };
}

function Badge({ children, tone = "slate" }: { children: React.ReactNode; tone?: Tone }) {
  return (
    <span className={`inline-flex items-center rounded-full border px-2.5 py-1 text-[11px] font-semibold ${TONE_CLASSES[tone].badge}`}>
      {children}
    </span>
  );
}

function EmptyState({ children }: { children: React.ReactNode }) {
  return (
    <div className="rounded-xl border border-dashed border-black/[0.10] bg-gray-50/80 px-4 py-8 text-center text-sm leading-6 text-secondary">
      {children}
    </div>
  );
}

function MetricTile({ label, value, detail, icon: Icon, tone }: { label: string; value: string | number; detail: string; icon: LucideIcon; tone: Tone }) {
  return (
    <div className={`rounded-xl border bg-white px-4 py-4 ${TONE_CLASSES[tone].border}`}>
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="text-[11px] font-semibold uppercase tracking-[0.14em] text-secondary/70">{label}</p>
          <p className="mt-1 text-2xl font-semibold text-foreground">{value}</p>
        </div>
        <span className={`flex h-9 w-9 items-center justify-center rounded-lg ${TONE_CLASSES[tone].icon}`}>
          <Icon className="h-4 w-4" />
        </span>
      </div>
      <p className="mt-3 text-xs leading-5 text-secondary">{detail}</p>
    </div>
  );
}

function SectionHeader({ eyebrow, title, description, icon: Icon }: { eyebrow: string; title: string; description: string; icon: LucideIcon }) {
  return (
    <div className="mb-4 flex items-start justify-between gap-4">
      <div className="min-w-0">
        <p className="text-[11px] font-semibold uppercase tracking-[0.16em] text-secondary/60">{eyebrow}</p>
        <h2 className="mt-1 text-lg font-semibold text-foreground">{title}</h2>
        <p className="mt-1 max-w-3xl text-sm leading-6 text-secondary">{description}</p>
      </div>
      <span className="hidden h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-black/[0.04] text-secondary sm:flex">
        <Icon className="h-5 w-5" />
      </span>
    </div>
  );
}

function CatalogGrid({ entries }: { entries: CatalogEntry[] }) {
  return (
    <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
      {entries.map((entry) => {
        const Icon = entry.icon;
        return (
          <article
            key={entry.id}
            className={`flex min-h-[220px] flex-col rounded-xl border bg-white px-4 py-4 ${TONE_CLASSES[entry.tone].border}`}
          >
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0">
                <p className="text-xs font-semibold text-secondary">{entry.id}</p>
                <h3 className="mt-1 text-base font-semibold text-foreground">{entry.title}</h3>
              </div>
              <span className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-lg ${TONE_CLASSES[entry.tone].icon}`}>
                <Icon className="h-4 w-4" />
              </span>
            </div>
            <p className="mt-3 text-sm leading-6 text-secondary">{entry.description}</p>
            <div className="mt-4 flex flex-wrap gap-2">
              {entry.surfaces.map((surface) => (
                <Badge key={surface} tone={entry.tone}>{surface}</Badge>
              ))}
            </div>
            <p className="mt-auto pt-4 text-xs text-secondary/75">{entry.source}</p>
          </article>
        );
      })}
    </div>
  );
}

function RuntimeConfigPanel({ config }: { config: RuntimeConfig | null }) {
  const rows = config
    ? [
        ["Provider", config.provider],
        ["Model", config.model],
        ["Workspace", config.workspace],
        ["API key", config.apiKeySet ? "configured locally" : config.apiKeyEnvVar || "not configured"],
        ["Base URL", config.baseUrl || "provider default"],
        ["Context", config.contextWindow],
        ["Output", config.maxOutputTokens],
      ]
    : [];

  return (
    <section className="rounded-2xl border border-black/[0.08] bg-white px-5 py-5">
      <SectionHeader
        eyebrow="Local config"
        title="Runtime provider and workspace"
        description="Read from /v1/app/config. Secrets stay local and are not returned to this view."
        icon={Settings}
      />
      {config ? (
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
          {rows.map(([label, value]) => (
            <div key={label} className="rounded-xl border border-black/[0.06] bg-gray-50/80 px-4 py-3">
              <p className="text-[11px] font-semibold uppercase tracking-[0.14em] text-secondary/60">{label}</p>
              <p className="mt-1 break-words text-sm font-semibold text-foreground">{value}</p>
            </div>
          ))}
        </div>
      ) : (
        <EmptyState>Runtime config is unavailable from /v1/app/config.</EmptyState>
      )}
    </section>
  );
}

function ToolsAndSkillsPanel({
  tools,
  skills,
  hooks,
  onReloadSkills,
  refreshing,
}: {
  tools: ToolMetadata[];
  skills: SkillDirectoryItem[];
  hooks: RuntimeHook[];
  onReloadSkills: () => void;
  refreshing: boolean;
}) {
  const enabledTools = tools.filter((tool) => tool.enabled);
  const dangerousTools = tools.filter((tool) => tool.dangerous);
  const skillHooks = skills.filter((skill) => skill.runtimeHooks > 0);

  return (
    <section className="rounded-2xl border border-black/[0.08] bg-white px-5 py-5">
      <div className="mb-4 flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <SectionHeader
          eyebrow="Tools and skills"
          title="Local runtime capability inventory"
          description="Tools come from /api/tools. Skills and runtime hooks come from /v1/app/skills."
          icon={Wrench}
        />
        <button
          type="button"
          onClick={onReloadSkills}
          disabled={refreshing}
          className="inline-flex min-h-[44px] cursor-pointer items-center justify-center gap-2 rounded-xl border border-black/10 bg-white px-4 py-2.5 text-sm font-semibold text-foreground transition-colors duration-200 hover:border-primary/35 hover:bg-gray-50 disabled:pointer-events-none disabled:opacity-45"
        >
          <RefreshCw className={`h-4 w-4 ${refreshing ? "animate-spin" : ""}`} />
          {refreshing ? "Reloading" : "Reload skills"}
        </button>
      </div>

      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <MetricTile label="Enabled tools" value={`${enabledTools.length}/${tools.length}`} detail={`${dangerousTools.length} require high-authority approval`} icon={Wrench} tone="amber" />
        <MetricTile label="Skills" value={skills.length} detail={`${skillHooks.length} advertise runtime hooks`} icon={BookOpen} tone="blue" />
        <MetricTile label="Runtime hooks" value={hooks.length} detail="Reported by loaded SKILL.md capabilities" icon={PlugZap} tone="purple" />
        <MetricTile label="Concurrency" value={tools.filter((tool) => tool.isConcurrencySafe).length} detail="Tools marked safe for parallel dispatch" icon={Activity} tone="green" />
      </div>

      <div className="mt-5 grid gap-5 lg:grid-cols-[minmax(0,1fr)_minmax(300px,380px)]">
        <div>
          <div className="mb-3 flex items-center justify-between gap-3">
            <h3 className="text-sm font-semibold text-foreground">ToolHost catalog</h3>
            <span className="text-xs font-semibold uppercase tracking-[0.14em] text-secondary/60">{tools.length} tools</span>
          </div>
          {tools.length === 0 ? (
            <EmptyState>No tools reported by /api/tools.</EmptyState>
          ) : (
            <div className="grid gap-2 md:grid-cols-2">
              {tools.slice(0, 12).map((tool) => (
                <div key={tool.name} className="rounded-xl border border-black/[0.06] bg-gray-50/70 px-3 py-3">
                  <div className="flex items-start justify-between gap-2">
                    <div className="min-w-0">
                      <p className="truncate text-sm font-semibold text-foreground">{tool.name}</p>
                      <p className="mt-1 line-clamp-2 text-xs leading-5 text-secondary">{tool.description || tool.kind}</p>
                    </div>
                    <Badge tone={tool.dangerous ? "red" : tool.source === "skill" ? "purple" : "slate"}>{tool.permission}</Badge>
                  </div>
                  <div className="mt-3 flex flex-wrap gap-2">
                    <Badge tone={tool.enabled ? "green" : "slate"}>{tool.enabled ? "enabled" : "disabled"}</Badge>
                    <Badge tone="slate">{tool.source}</Badge>
                    {tool.dangerous ? <Badge tone="red">approval</Badge> : null}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>

        <div>
          <div className="mb-3 flex items-center justify-between gap-3">
            <h3 className="text-sm font-semibold text-foreground">Runtime hooks</h3>
            <span className="text-xs font-semibold uppercase tracking-[0.14em] text-secondary/60">{hooks.length} hooks</span>
          </div>
          {hooks.length === 0 ? (
            <EmptyState>No runtime hooks reported.</EmptyState>
          ) : (
            <div className="space-y-2">
              {hooks.slice(0, 8).map((hook, index) => (
                <div key={`${hook.name}-${hook.point}-${index}`} className="rounded-xl border border-black/[0.06] bg-gray-50/70 px-3 py-3">
                  <div className="flex items-start justify-between gap-2">
                    <p className="min-w-0 truncate text-sm font-semibold text-foreground">{hook.name}</p>
                    <Badge tone="purple">{hook.point}</Badge>
                  </div>
                  {hook.path ? <p className="mt-1 truncate text-xs text-secondary">{hook.path}</p> : null}
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </section>
  );
}

function PolicyPanel() {
  return (
    <section className="rounded-2xl border border-black/[0.08] bg-white px-5 py-5">
      <SectionHeader
        eyebrow="Runtime policy"
        title="Recipes and harness presets"
        description="First-party runtime contracts are shown as local catalog data until a dedicated recipe endpoint is available."
        icon={ShieldCheck}
      />
      <div className="space-y-6">
        <div>
          <div className="mb-3 flex items-center justify-between gap-3">
            <h3 className="text-sm font-semibold text-foreground">Recipes</h3>
            <Badge tone="blue">{FIRST_PARTY_RECIPES.length} first-party</Badge>
          </div>
          <CatalogGrid entries={FIRST_PARTY_RECIPES} />
        </div>
        <div>
          <div className="mb-3 flex items-center justify-between gap-3">
            <h3 className="text-sm font-semibold text-foreground">Harness presets</h3>
            <Badge tone="green">{HARNESS_PRESETS.length} presets</Badge>
          </div>
          <CatalogGrid entries={HARNESS_PRESETS} />
        </div>
      </div>
    </section>
  );
}

function PhaseRoutingPanel() {
  return (
    <section className="rounded-2xl border border-black/[0.08] bg-white px-5 py-5">
      <SectionHeader
        eyebrow="Phase routing"
        title="ADK runtime boundaries"
        description="These phases describe where local policy, hooks, evidence, and public projection attach to the Python runtime."
        icon={Route}
      />
      <div className="grid gap-3 lg:grid-cols-2">
        {PHASE_ROUTES.map((route) => {
          const Icon = route.icon;
          return (
            <article key={route.id} className={`rounded-xl border bg-white px-4 py-4 ${TONE_CLASSES[route.tone].border}`}>
              <div className="flex items-start gap-3">
                <span className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-lg ${TONE_CLASSES[route.tone].icon}`}>
                  <Icon className="h-4 w-4" />
                </span>
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-2">
                    <h3 className="text-sm font-semibold text-foreground">{route.title}</h3>
                    <Badge tone={route.tone}>{route.id}</Badge>
                  </div>
                  <p className="mt-2 text-sm leading-6 text-secondary">{route.description}</p>
                  <div className="mt-3 flex flex-wrap gap-2">
                    {route.surfaces.map((surface) => (
                      <Badge key={surface} tone="slate">{surface}</Badge>
                    ))}
                  </div>
                  <p className="mt-3 text-xs text-secondary/75">{route.source}</p>
                </div>
              </div>
            </article>
          );
        })}
      </div>
    </section>
  );
}

function EvidenceRepairPanel() {
  return (
    <section className="rounded-2xl border border-black/[0.08] bg-white px-5 py-5">
      <SectionHeader
        eyebrow="Evidence & repair"
        title="Verification, fallback, and repair controls"
        description="Repair is represented as runtime policy state today. The explicit multi-step repair framework is still a future integration, so this panel exposes the current decision outcomes without hosted toggles."
        icon={FlaskConical}
      />
      <CatalogGrid entries={REPAIR_CONTROLS} />
    </section>
  );
}

export function CustomizeRuntimeConsole({ botId }: CustomizeRuntimeConsoleProps): React.JSX.Element {
  const agentFetch = useAgentFetch();
  const [snapshot, setSnapshot] = useState<RuntimeSnapshot>(EMPTY_SNAPSHOT);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [errors, setErrors] = useState<string[]>([]);

  const loadRuntimeSnapshot = useCallback(async () => {
    const nextErrors: string[] = [];
    const nextSnapshot: RuntimeSnapshot = { ...EMPTY_SNAPSHOT };

    const [toolsResponse, skillsResponse, configResponse] = await Promise.all([
      agentFetch("/api/tools").catch((err: unknown) => {
        nextErrors.push(err instanceof Error ? err.message : "Failed to load /api/tools");
        return null;
      }),
      agentFetch("/v1/app/skills").catch((err: unknown) => {
        nextErrors.push(err instanceof Error ? err.message : "Failed to load /v1/app/skills");
        return null;
      }),
      agentFetch("/v1/app/config").catch((err: unknown) => {
        nextErrors.push(err instanceof Error ? err.message : "Failed to load /v1/app/config");
        return null;
      }),
    ]);

    if (toolsResponse) {
      if (toolsResponse.ok) {
        const data = await readJson(toolsResponse);
        nextSnapshot.tools = asRecordArray(data.tools).map(normalizeTool);
      } else {
        nextErrors.push(`Failed to load /api/tools (${toolsResponse.status})`);
      }
    }

    if (skillsResponse) {
      if (skillsResponse.ok) {
        const data = await readJson(skillsResponse);
        nextSnapshot.skills = asRecordArray(data.loaded).map(normalizeSkill);
        nextSnapshot.hooks = asRecordArray(data.runtimeHooks).map(normalizeHook);
        nextSnapshot.issueCount = asNumber(data.issueCount, asRecordArray(data.issues).length);
      } else {
        nextErrors.push(`Failed to load /v1/app/skills (${skillsResponse.status})`);
      }
    }

    if (configResponse) {
      if (configResponse.ok) {
        nextSnapshot.config = normalizeConfig(await readJson(configResponse));
      } else {
        nextErrors.push(`Failed to load /v1/app/config (${configResponse.status})`);
      }
    }

    setSnapshot(nextSnapshot);
    setErrors(nextErrors);
  }, [agentFetch]);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    loadRuntimeSnapshot()
      .catch((err: unknown) => {
        if (!cancelled) setErrors([err instanceof Error ? err.message : "Failed to load local runtime snapshot"]);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [loadRuntimeSnapshot]);

  const reloadSkills = useCallback(async () => {
    setRefreshing(true);
    try {
      const response = await agentFetch("/v1/app/skills/reload", { method: "POST" });
      if (!response.ok) throw new Error(`Failed to reload /v1/app/skills (${response.status})`);
      await loadRuntimeSnapshot();
    } catch (err) {
      setErrors([err instanceof Error ? err.message : "Failed to reload skills"]);
    } finally {
      setRefreshing(false);
    }
  }, [agentFetch, loadRuntimeSnapshot]);

  const enabledToolCount = useMemo(
    () => snapshot.tools.filter((tool) => tool.enabled).length,
    [snapshot.tools],
  );
  const promptSkillCount = useMemo(
    () => snapshot.skills.filter((skill) => skill.promptOnly).length,
    [snapshot.skills],
  );
  const scriptSkillCount = useMemo(
    () => snapshot.skills.filter((skill) => skill.scriptBacked).length,
    [snapshot.skills],
  );

  return (
    <div className="max-w-7xl space-y-6 pb-20">
      <header className="rounded-2xl border border-black/[0.08] bg-white px-5 py-5">
        <div className="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
          <div className="min-w-0">
            <p className="text-[11px] font-semibold uppercase tracking-[0.18em] text-secondary/60">Customize</p>
            <h1 className="mt-2 text-2xl font-bold leading-tight text-foreground">Python ADK runtime console</h1>
            <p className="mt-2 max-w-3xl text-sm leading-6 text-secondary">
              Local runtime customization for recipes, harnesses, ToolHost capabilities, phase routing, and evidence policy.
            </p>
          </div>
          <div className="flex flex-wrap gap-2">
            <Badge tone="green">OSS local</Badge>
            <Badge tone="slate">route: {botId || "local"}</Badge>
            <Badge tone={loading ? "amber" : "blue"}>{loading ? "loading runtime" : "local APIs"}</Badge>
          </div>
        </div>
      </header>

      {errors.length > 0 ? (
        <div className="rounded-xl border border-amber-500/25 bg-amber-500/[0.08] px-4 py-3 text-sm leading-6 text-amber-800">
          <div className="font-semibold">Some local runtime surfaces are unavailable.</div>
          <div className="mt-1">{errors.join(" ")}</div>
        </div>
      ) : null}

      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <MetricTile label="Tools" value={`${enabledToolCount}/${snapshot.tools.length}`} detail="From the local ToolHost API" icon={Wrench} tone="amber" />
        <MetricTile label="Skills" value={snapshot.skills.length} detail={`${promptSkillCount} prompt, ${scriptSkillCount} script`} icon={BookOpen} tone="blue" />
        <MetricTile label="Hooks" value={snapshot.hooks.length} detail="Runtime hooks loaded by skills" icon={PlugZap} tone="purple" />
        <MetricTile label="Issues" value={snapshot.issueCount} detail="Skill metadata issues reported locally" icon={AlertTriangle} tone={snapshot.issueCount > 0 ? "red" : "green"} />
      </div>

      <RuntimeConfigPanel config={snapshot.config} />
      <PolicyPanel />
      <ToolsAndSkillsPanel
        tools={snapshot.tools}
        skills={snapshot.skills}
        hooks={snapshot.hooks}
        onReloadSkills={() => void reloadSkills()}
        refreshing={refreshing}
      />
      <PhaseRoutingPanel />
      <EvidenceRepairPanel />
    </div>
  );
}
