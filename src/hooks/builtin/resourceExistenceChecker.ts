/**
 * Resource existence checker — beforeCommit, priority 83.
 *
 * Blocks commits where the assistant claims specific file contents
 * without having read the file this turn. Pure heuristic — no LLM
 * call, zero cost, <1ms latency.
 *
 * Example: bot writes "DAILY_RUNBOOK_v3.md에 따르면 Actor는 Gemini
 * 2.5 Flash" without FileRead → blocked. Bot reads it first → pass.
 *
 * Retry budget: 1, then fail-open.
 * Toggle: `MAGI_RESOURCE_CHECK=off` disables globally.
 */

import type { RegisteredHook, HookContext } from "../types.js";
import type { TranscriptEntry } from "../../storage/Transcript.js";

const MAX_RETRIES = 1;

/** File extensions we recognise as workspace files. */
const CODE_EXTENSIONS = new Set([
  "md", "json", "ts", "tsx", "js", "jsx", "mjs", "cjs",
  "yaml", "yml", "toml", "ini", "cfg", "conf",
  "txt", "csv", "sql", "sh", "bash", "zsh",
  "py", "rb", "go", "rs", "java", "kt", "swift",
  "html", "css", "scss", "xml", "svg",
  "env", "lock", "log",
  "dockerfile",
]);

/** Tools whose presence means the bot DID read a file. */
const READ_TOOLS = new Set(["FileRead", "Grep", "Glob", "Bash"]);

/**
 * Extract file references from assistant text.
 * Returns deduplicated list of filenames/paths.
 */
export function extractFileReferences(text: string): string[] {
  const refs = new Set<string>();

  // Pattern 1: backtick-quoted file references (`file.ext`)
  const backtickRe = /`([\w\-./]+\.(\w{1,10}))`/g;
  let m: RegExpExecArray | null;
  while ((m = backtickRe.exec(text)) !== null) {
    const ext = m[2];
    const full = m[1];
    if (ext && full && CODE_EXTENSIONS.has(ext.toLowerCase())) {
      refs.add(full);
    }
  }

  // Pattern 2: bare file references (word boundaries)
  // Matches: DAILY_RUNBOOK_v3.md, src/config.ts, SOUL.md
  const bareRe = /(?:^|[\s(,`"'])(([\w\-]+\/)*[\w\-]+\.(\w{1,10}))(?=[\s),`"':;을를에의는이가]|$)/gm;
  while ((m = bareRe.exec(text)) !== null) {
    const ext = m[3];
    const full = m[1];
    if (ext && full && CODE_EXTENSIONS.has(ext.toLowerCase())) {
      refs.add(full);
    }
  }

  return [...refs];
}

const CONTENT_CLAIM_CLASSIFIER_PROMPT = `Does this text make a CONTENT CLAIM about a specific file — asserting what is IN the file, not just mentioning it?

CONTENT CLAIM (YES):
- "SOUL.md에 따르면 이 규칙이 있습니다" (according to SOUL.md, this rule exists)
- "config.ts contains the API endpoint"
- "이 파일의 내용은..." (the content of this file is...)

NOT A CONTENT CLAIM (NO):
- Just mentioning a filename without claiming its content
- "config.ts 파일을 수정해주세요" (please modify config.ts)
- "I'll check SOUL.md" (intent to read, not a content claim)

Reply ONLY: YES or NO`;

/**
 * Check whether the text makes a content claim about a specific file.
 * Deterministic patterns handle the common Korean/English cases; the
 * optional LLM classifier is only a fallback for ambiguous phrasing.
 */
export function hasContentClaim(
  filename: string,
  text: string,
  ctx?: HookContext,
): boolean | Promise<boolean> {
  if (!text || !filename) return false;
  // Quick check: filename must appear in text
  if (!text.includes(filename) && !text.includes(filename.replace(/\.[^.]+$/, ""))) return false;
  const deterministic = deterministicContentClaim(filename, text);
  if (deterministic !== null) return deterministic;
  if (!ctx?.llm) return false;

  return classifyContentClaimWithLlm(filename, text, ctx);
}

function deterministicContentClaim(filename: string, text: string): boolean | null {
  const escaped = escapeRegExp(filename);
  const stem = escapeRegExp(filename.replace(/\.[^.]+$/, ""));
  const fileRef = `(?:${escaped}|${stem})`;
  const claimRe = new RegExp(
    [
      `${fileRef}.{0,32}(?:에\\s*따르면|에\\s*의하면|에\\s*명시|내용(?:은|이)|포함|contains|states|says)`,
      `(?:according\\s+to|as\\s+stated\\s+in|in)\\s+${fileRef}`,
      `${fileRef}\\s+(?:contains|states|says|uses|defines|specifies)`,
    ].join("|"),
    "i",
  );
  if (claimRe.test(text)) return true;
  const nonClaimRe = new RegExp(
    [
      `${fileRef}.{0,32}(?:확인해|열어|읽어|봐|check|read|open)`,
      `(?:확인|열람|읽기|check|read|open).{0,32}${fileRef}`,
    ].join("|"),
    "i",
  );
  if (nonClaimRe.test(text)) return false;
  return null;
}

async function classifyContentClaimWithLlm(
  filename: string,
  text: string,
  ctx: HookContext,
): Promise<boolean> {
  try {
    let result = "";
    for await (const event of ctx.llm.stream({
      model: "claude-haiku-4-5",
      system: CONTENT_CLAIM_CLASSIFIER_PROMPT,
      messages: [{ role: "user", content: [{ type: "text", text: `File: ${filename}\nText: ${text.slice(0, 400)}` }] }],
      max_tokens: 10,
    })) {
      if (event.kind === "text_delta") result += event.delta;
    }
    return result.trim().toUpperCase().startsWith("YES");
  } catch {
    return false;
  }
}

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

const GENERIC_READ_CLAIM_PROMPT = `Does this text claim to have READ or CHECKED a file without naming which specific file?

YES examples: "파일을 확인해보니", "다시 읽어보니", "I checked the file", "확인 결과", "문서에 따르면"
NO examples: text that doesn't claim to have read anything, or names a specific file

Reply ONLY: YES or NO`;

/**
 * LLM-based: detect generic "I read the file" claims.
 */
export async function matchesGenericReadClaim(text: string, ctx?: HookContext): Promise<boolean> {
  if (!text) return false;
  if (!ctx?.llm) return false;

  try {
    let result = "";
    for await (const event of ctx.llm.stream({
      model: "claude-haiku-4-5",
      system: GENERIC_READ_CLAIM_PROMPT,
      messages: [{ role: "user", content: [{ type: "text", text: text.slice(0, 400) }] }],
      max_tokens: 10,
    })) {
      if (event.kind === "text_delta") result += event.delta;
    }
    return result.trim().toUpperCase().startsWith("YES");
  } catch {
    return false;
  }
}

export interface ResourceCheckAgent {
  readSessionTranscript(
    sessionKey: string,
  ): Promise<ReadonlyArray<TranscriptEntry> | null>;
}

export interface ResourceExistenceCheckerOptions {
  agent?: ResourceCheckAgent;
}

function isEnabled(): boolean {
  const raw = process.env.MAGI_RESOURCE_CHECK;
  if (raw === undefined || raw === null) return true;
  const v = raw.trim().toLowerCase();
  return v === "" || v === "on" || v === "true" || v === "1";
}

/**
 * Check if a file was read this turn by looking at tool_call entries.
 * Matches by filename (basename) — if FileRead("/workspace/foo/SOUL.md")
 * was called, reference to "SOUL.md" passes.
 */
function wasFileReadThisTurn(
  filename: string,
  transcript: ReadonlyArray<TranscriptEntry>,
  turnId: string,
): boolean {
  const baseFilename = filename.split("/").pop() ?? filename;
  for (const entry of transcript) {
    if (entry.kind !== "tool_call") continue;
    if (entry.turnId !== turnId) continue;
    if (!READ_TOOLS.has(entry.name)) continue;

    const input = entry.input as Record<string, unknown> | undefined;
    if (!input) continue;

    // FileRead: check file_path
    if (entry.name === "FileRead" && typeof input.file_path === "string") {
      const readBase = input.file_path.split("/").pop() ?? input.file_path;
      if (readBase === baseFilename || input.file_path.includes(filename)) {
        return true;
      }
    }

    // Grep: check path parameter
    if (entry.name === "Grep" && typeof input.path === "string") {
      const grepBase = input.path.split("/").pop() ?? input.path;
      if (grepBase === baseFilename || input.path.includes(filename)) {
        return true;
      }
    }

    // Glob: check pattern
    if (entry.name === "Glob" && typeof input.pattern === "string") {
      if (input.pattern.includes(baseFilename)) {
        return true;
      }
    }

    // Bash: check command for cat/head/tail/less + filename
    if (entry.name === "Bash" && typeof input.command === "string") {
      if (input.command.includes(baseFilename)) {
        return true;
      }
    }
  }
  return false;
}

export function makeResourceExistenceCheckerHook(
  opts: ResourceExistenceCheckerOptions = {},
): RegisteredHook<"beforeCommit"> {
  return {
    name: "builtin:resource-existence-checker",
    point: "beforeCommit",
    priority: 83,
    blocking: true,
    failOpen: true,
    timeoutMs: 5_000,
    handler: async ({ assistantText, toolCallCount, retryCount }, ctx: HookContext) => {
      try {
        if (!isEnabled()) return { action: "continue" };
        if (!assistantText || assistantText.trim().length === 0) {
          return { action: "continue" };
        }

        // Extract file references from the response
        const fileRefs = extractFileReferences(assistantText);
        if (fileRefs.length === 0) return { action: "continue" };

        // Find files with content claims (async)
        const filesWithClaims: string[] = [];
        for (const f of fileRefs) {
          if (await hasContentClaim(f, assistantText, ctx)) {
            filesWithClaims.push(f);
          }
        }
        if (filesWithClaims.length === 0) return { action: "continue" };

        // Get transcript
        let entries: ReadonlyArray<TranscriptEntry> | null = null;
        if (opts.agent) {
          try {
            entries = await opts.agent.readSessionTranscript(ctx.sessionKey);
          } catch (err) {
            ctx.log(
              "warn",
              "[resource-existence-checker] transcript read failed; failing open",
              { error: err instanceof Error ? err.message : String(err) },
            );
            return { action: "continue" };
          }
        }
        const source = entries ?? (ctx.transcript as ReadonlyArray<TranscriptEntry>);

        // Check each file with content claims
        const unreadFile = filesWithClaims.find(
          (f) => !wasFileReadThisTurn(f, source, ctx.turnId),
        );

        if (!unreadFile) {
          ctx.emit({
            type: "rule_check",
            ruleId: "resource-existence-checker",
            verdict: "ok",
            detail: `all referenced files were read this turn`,
          });
          return { action: "continue" };
        }

        // Unread file with content claim found
        if (retryCount >= MAX_RETRIES) {
          ctx.log(
            "warn",
            "[resource-existence-checker] retry budget exhausted; failing open",
            { unreadFile, retryCount },
          );
          ctx.emit({
            type: "rule_check",
            ruleId: "resource-existence-checker",
            verdict: "violation",
            detail: `retry exhausted for ${unreadFile}; failing open`,
          });
          return { action: "continue" };
        }

        ctx.log(
          "warn",
          "[resource-existence-checker] blocking: content claim without reading file",
          { unreadFile, retryCount },
        );
        ctx.emit({
          type: "rule_check",
          ruleId: "resource-existence-checker",
          verdict: "violation",
          detail: `claimed content of ${unreadFile} without reading; retryCount=${retryCount}`,
        });

        return {
          action: "block",
          reason: [
            `[RETRY:RESOURCE_CHECK] You referenced specific content from "${unreadFile}"`,
            "but did not read this file during the current turn. Memory-based",
            "claims about file contents are unreliable — the file may have",
            "changed or your recollection may be inaccurate.",
            "",
            "Before finalising this answer:",
            `1) FileRead the file "${unreadFile}" to get current contents.`,
            "2) Re-draft your answer based on what the file actually says.",
            "3) If the file doesn't exist, verify with Glob/Bash ls and state so.",
          ].join("\n"),
        };
      } catch (err) {
        ctx.log(
          "warn",
          "[resource-existence-checker] unexpected error; failing open",
          { error: err instanceof Error ? err.message : String(err) },
        );
        return { action: "continue" };
      }
    },
  };
}
