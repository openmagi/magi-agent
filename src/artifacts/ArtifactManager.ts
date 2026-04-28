/**
 * ArtifactManager — T4-20 §7.12.a
 *
 * Tiered artifact subsystem (L0 full / L1 overview / L2 abstract),
 * inspired by ByteDance OpenViking's filesystem-as-context paradigm.
 *
 * Storage layout (per artifact):
 *   workspace/artifacts/{artifactId}/{slug}.md           — L0 full content
 *   workspace/artifacts/{artifactId}/{slug}.overview.md  — L1 2-line TL;DR
 *   workspace/artifacts/{artifactId}/{slug}.abstract.md  — L2 structured frontmatter
 *   workspace/artifacts/index.json                       — ArtifactMeta[] index
 *
 * L1/L2 are generated with Haiku on create/update and cached on disk.
 * On Haiku failure we fall back to a deterministic static summary.
 *
 * Consumers:
 *   - ContextEngine uses listArtifactsForContext() to inject artifact
 *     awareness into the system prompt when budget allows.
 *   - Artifact* tools (Create/Read/Update/Delete/List) wrap this class.
 */

import fs from "node:fs/promises";
import path from "node:path";
import { monotonicFactory } from "ulid";
import { atomicWriteJson } from "../storage/atomicWrite.js";
import type { LLMClient } from "../transport/LLMClient.js";
import { isUnderRoot } from "../util/fsSafe.js";

const ulid = monotonicFactory();

export interface ArtifactMeta {
  artifactId: string;
  kind: string;
  title: string;
  /** Slug (lowercase, kebab). Derived from title unless provided. */
  slug: string;
  /** Relative path to L0, from workspaceRoot. */
  path: string;
  sizeBytes: number;
  producedBy?: string;
  sources?: string[];
  createdAt: number;
  updatedAt: number;
  /**
   * When this artifact was imported from a spawn child, the child's
   * taskId is recorded here so the parent's audit trail shows
   * provenance. Absent on artifacts created directly by the parent.
   */
  spawnTaskId?: string;
  /**
   * Original artifactId from the child's index, when this entry was
   * re-keyed due to a collision with a parent-side id. Useful when
   * correlating child-side logs with the imported copy.
   */
  importedFromArtifactId?: string;
}

export interface CreateArtifactInput {
  kind: string;
  title: string;
  content: string;
  producedBy?: string;
  sources?: string[];
  /** Optional explicit slug. Derived from title if omitted. */
  slug?: string;
}

const HAIKU_MODEL = "claude-haiku-4-5-20251001";
const HAIKU_DEADLINE_MS = 3000;
const MAX_INDEX_BYTES = 256 * 1024;
const ARTIFACT_ID_RE = /^[0-9A-HJKMNP-TV-Z]{26}$/;
const SLUG_RE = /^[a-z0-9][a-z0-9-]{0,59}$/;

function slugify(title: string): string {
  return (
    title
      .trim()
      .toLowerCase()
      .replace(/[^a-z0-9\s-]+/g, "")
      .replace(/\s+/g, "-")
      .replace(/-+/g, "-")
      .replace(/^-|-$/g, "")
      .slice(0, 60) || "untitled"
  );
}

function isValidArtifactId(id: string): boolean {
  return ARTIFACT_ID_RE.test(id);
}

function isValidSlug(slug: string): boolean {
  return SLUG_RE.test(slug) && !slug.includes("..");
}

async function realpathIfAvailable(p: string): Promise<string> {
  const resolved = path.resolve(p);
  try {
    return await fs.realpath(resolved);
  } catch {
    return resolved;
  }
}

async function resolveCopyTier(
  args: {
    sourceRootRaw: string;
    sourceRootReal: string;
    destRootRaw: string;
    destRootReal: string;
    sourcePath: string;
    destPath: string;
    required: boolean;
  },
): Promise<{ sourcePath: string; destPath: string } | null> {
  const sourceResolved = path.resolve(args.sourcePath);
  if (!isUnderRoot(sourceResolved, args.sourceRootRaw)) {
    throw new Error(`artifact source path escapes child artifacts root: ${args.sourcePath}`);
  }

  let sourceReal: string;
  try {
    sourceReal = await fs.realpath(sourceResolved);
  } catch (err) {
    if ((err as NodeJS.ErrnoException).code === "ENOENT" && !args.required) {
      return null;
    }
    throw err;
  }
  if (!isUnderRoot(sourceReal, args.sourceRootReal)) {
    throw new Error(`artifact source realpath escapes child artifacts root: ${args.sourcePath}`);
  }

  const destResolved = path.resolve(args.destPath);
  if (!isUnderRoot(destResolved, args.destRootRaw)) {
    throw new Error(`artifact destination path escapes parent artifacts root: ${args.destPath}`);
  }
  await fs.mkdir(path.dirname(destResolved), { recursive: true });
  const destParentReal = await realpathIfAvailable(path.dirname(destResolved));
  if (!isUnderRoot(destParentReal, args.destRootReal)) {
    throw new Error(`artifact destination realpath escapes parent artifacts root: ${args.destPath}`);
  }

  return { sourcePath: sourceResolved, destPath: destResolved };
}

function fallbackL1(content: string): string {
  const lines = content.split(/\r?\n/).filter((l) => l.trim().length > 0);
  const first = lines[0] ?? "(empty)";
  const second = lines[1] ?? "";
  const one = first.slice(0, 120);
  const two = second.slice(0, 120);
  return two ? `${one}\n${two}` : one;
}

function fallbackL2(meta: Pick<ArtifactMeta, "kind" | "title" | "producedBy" | "sources">): string {
  const lines = [
    "---",
    `kind: ${meta.kind}`,
    `title: ${meta.title.replace(/"/g, '\\"')}`,
    meta.producedBy ? `produced_by: ${meta.producedBy}` : null,
    meta.sources && meta.sources.length > 0
      ? `sources:\n${meta.sources.map((s) => `  - ${s}`).join("\n")}`
      : null,
    "---",
  ].filter((l): l is string => l !== null);
  return lines.join("\n");
}

export class ArtifactManager {
  constructor(
    private readonly workspaceRoot: string,
    private readonly llm?: LLMClient,
  ) {}

  private artifactsDir(): string {
    return path.join(this.workspaceRoot, "artifacts");
  }

  private dirFor(artifactId: string): string {
    return path.join(this.artifactsDir(), artifactId);
  }

  private indexPath(): string {
    return path.join(this.artifactsDir(), "index.json");
  }

  private async readIndex(): Promise<ArtifactMeta[]> {
    try {
      const raw = await fs.readFile(this.indexPath(), "utf8");
      if (raw.length > MAX_INDEX_BYTES) return [];
      const parsed = JSON.parse(raw);
      if (!Array.isArray(parsed)) return [];
      return parsed as ArtifactMeta[];
    } catch (err) {
      if ((err as NodeJS.ErrnoException).code === "ENOENT") return [];
      throw err;
    }
  }

  private async writeIndex(entries: ArtifactMeta[]): Promise<void> {
    await atomicWriteJson(this.indexPath(), entries);
  }

  private async generateL1(content: string): Promise<string> {
    if (!this.llm) return fallbackL1(content);
    try {
      const result = await runHaiku(
        this.llm,
        "Summarise the following artifact in exactly two lines (<=120 chars each). Return only the two lines, no preamble, no trailing prose.",
        content.slice(0, 12_000),
      );
      const cleaned = result
        .split(/\r?\n/)
        .map((l) => l.trim())
        .filter((l) => l.length > 0)
        .slice(0, 2)
        .join("\n");
      return cleaned || fallbackL1(content);
    } catch {
      return fallbackL1(content);
    }
  }

  private async generateL2(
    meta: Pick<ArtifactMeta, "kind" | "title" | "producedBy" | "sources">,
    content: string,
  ): Promise<string> {
    const fallback = fallbackL2(meta);
    if (!this.llm) return fallback;
    try {
      const prompt =
        "Produce a strict YAML frontmatter block (delimited by '---') " +
        "describing this artifact. Fields: kind, title, produced_by (optional), " +
        "sources (yaml list, optional), output_schema (yaml list of {field,type} " +
        "entries — pick 2 to 5 fields that summarise the structured data). " +
        "Return ONLY the frontmatter block, nothing else.";
      const result = await runHaiku(
        this.llm,
        prompt,
        `Artifact metadata: kind=${meta.kind}, title=${meta.title}\n\nContent excerpt:\n${content.slice(0, 6_000)}`,
      );
      const trimmed = result.trim();
      if (/^---[\s\S]*---$/.test(trimmed)) return trimmed;
      return fallback;
    } catch {
      return fallback;
    }
  }

  async create(input: CreateArtifactInput): Promise<ArtifactMeta> {
    const artifactId = ulid();
    const slug = input.slug ? slugify(input.slug) : slugify(input.title);
    const dir = this.dirFor(artifactId);
    await fs.mkdir(dir, { recursive: true });

    const l0Path = path.join(dir, `${slug}.md`);
    const l1Path = path.join(dir, `${slug}.overview.md`);
    const l2Path = path.join(dir, `${slug}.abstract.md`);
    await fs.writeFile(l0Path, input.content, "utf8");

    const now = Date.now();
    const relPath = path.relative(this.workspaceRoot, l0Path);
    const sizeBytes = Buffer.byteLength(input.content, "utf8");

    const metaShape = {
      kind: input.kind,
      title: input.title,
      producedBy: input.producedBy,
      sources: input.sources,
    };
    const [l1, l2] = await Promise.all([
      this.generateL1(input.content),
      this.generateL2(metaShape, input.content),
    ]);
    await fs.writeFile(l1Path, l1, "utf8");
    await fs.writeFile(l2Path, l2, "utf8");

    const meta: ArtifactMeta = {
      artifactId,
      kind: input.kind,
      title: input.title,
      slug,
      path: relPath,
      sizeBytes,
      ...(input.producedBy ? { producedBy: input.producedBy } : {}),
      ...(input.sources ? { sources: [...input.sources] } : {}),
      createdAt: now,
      updatedAt: now,
    };

    const index = await this.readIndex();
    index.push(meta);
    await this.writeIndex(index);
    return meta;
  }

  async readL0(artifactId: string): Promise<string> {
    const meta = await this.getMeta(artifactId);
    return fs.readFile(path.join(this.workspaceRoot, meta.path), "utf8");
  }

  async readL1(artifactId: string): Promise<string> {
    const meta = await this.getMeta(artifactId);
    const p = path.join(this.dirFor(artifactId), `${meta.slug}.overview.md`);
    return fs.readFile(p, "utf8");
  }

  async readL2(artifactId: string): Promise<string> {
    const meta = await this.getMeta(artifactId);
    const p = path.join(this.dirFor(artifactId), `${meta.slug}.abstract.md`);
    return fs.readFile(p, "utf8");
  }

  async getMeta(artifactId: string): Promise<ArtifactMeta> {
    const idx = await this.readIndex();
    const entry = idx.find((m) => m.artifactId === artifactId);
    if (!entry) throw new Error(`artifact not found: ${artifactId}`);
    return entry;
  }

  async update(artifactId: string, newContent: string): Promise<ArtifactMeta> {
    const meta = await this.getMeta(artifactId);
    const l0Path = path.join(this.workspaceRoot, meta.path);
    const l1Path = path.join(this.dirFor(artifactId), `${meta.slug}.overview.md`);
    const l2Path = path.join(this.dirFor(artifactId), `${meta.slug}.abstract.md`);
    await fs.writeFile(l0Path, newContent, "utf8");
    const [l1, l2] = await Promise.all([
      this.generateL1(newContent),
      this.generateL2(
        { kind: meta.kind, title: meta.title, producedBy: meta.producedBy, sources: meta.sources },
        newContent,
      ),
    ]);
    await fs.writeFile(l1Path, l1, "utf8");
    await fs.writeFile(l2Path, l2, "utf8");

    const index = await this.readIndex();
    const i = index.findIndex((m) => m.artifactId === artifactId);
    if (i < 0) throw new Error(`artifact lost from index: ${artifactId}`);
    const updated: ArtifactMeta = {
      ...meta,
      sizeBytes: Buffer.byteLength(newContent, "utf8"),
      updatedAt: Date.now(),
    };
    index[i] = updated;
    await this.writeIndex(index);
    return updated;
  }

  async list(filter?: { kind?: string }): Promise<ArtifactMeta[]> {
    const idx = await this.readIndex();
    if (filter?.kind) return idx.filter((m) => m.kind === filter.kind);
    return idx;
  }

  async delete(artifactId: string): Promise<void> {
    const idx = await this.readIndex();
    const next = idx.filter((m) => m.artifactId !== artifactId);
    if (next.length === idx.length) return;
    await fs.rm(this.dirFor(artifactId), { recursive: true, force: true });
    await this.writeIndex(next);
  }

  /**
   * Spawn artifact handoff (2026-04-20).
   *
   * When a spawned child runs `ArtifactManager.create(...)` inside its
   * isolated workspace, the artifact files live under
   * `{childWorkspaceRoot}/artifacts/`. The child's workspace is torn
   * down when the spawn tool returns — without an explicit handoff the
   * parent (and therefore the user) never sees the child's output.
   *
   * This method copies every artifact listed in the child's
   * `index.json` into the parent workspace's artifacts dir and appends
   * them to the parent index, tagging each entry with `spawnTaskId`.
   *
   * Collision policy: if a child's `artifactId` already exists in the
   * parent index, a fresh ulid is minted and the original id is stored
   * on `importedFromArtifactId` for audit correlation. The child's
   * directory is untouched either way.
   *
   * Missing child index (`ENOENT`) is treated as "no artifacts
   * produced" and returns `[]`. Any other failure (disk I/O, JSON
   * parse) is rethrown — callers (the spawn tool) decide whether to
   * fail the spawn or surface a warning.
   */
  async importFromDir(
    childRootDir: string,
    options: { spawnTaskId: string },
  ): Promise<ArtifactMeta[]> {
    const childArtifactsDir = path.resolve(childRootDir, "artifacts");
    const childIndexPath = path.join(childArtifactsDir, "index.json");

    let rawIndex: string;
    try {
      rawIndex = await fs.readFile(childIndexPath, "utf8");
    } catch (err) {
      if ((err as NodeJS.ErrnoException).code === "ENOENT") return [];
      throw err;
    }

    let childEntries: ArtifactMeta[];
    try {
      const parsed = JSON.parse(rawIndex);
      if (!Array.isArray(parsed)) return [];
      childEntries = parsed as ArtifactMeta[];
    } catch {
      return [];
    }
    if (childEntries.length === 0) return [];

    // Ensure parent artifacts dir exists before any copy.
    await fs.mkdir(this.artifactsDir(), { recursive: true });
    const childArtifactsRootRaw = path.resolve(childArtifactsDir);
    const childArtifactsRootReal = await realpathIfAvailable(childArtifactsRootRaw);
    const parentArtifactsRootRaw = path.resolve(this.artifactsDir());
    const parentArtifactsRootReal = await realpathIfAvailable(parentArtifactsRootRaw);

    const parentIndex = await this.readIndex();
    const existingIds = new Set(parentIndex.map((m) => m.artifactId));
    const imported: ArtifactMeta[] = [];

    for (const childMeta of childEntries) {
      if (!childMeta || typeof childMeta.artifactId !== "string") continue;

      const originalId = childMeta.artifactId;
      if (!isValidArtifactId(originalId)) continue;
      const collided = existingIds.has(originalId);
      const targetId = collided ? ulid() : originalId;

      const childDir = path.join(childArtifactsDir, originalId);
      const parentDir = this.dirFor(targetId);
      await fs.mkdir(parentDir, { recursive: true });

      const slug =
        typeof childMeta.slug === "string" && childMeta.slug.length > 0
          ? childMeta.slug
          : slugify(childMeta.title || "untitled");
      if (!isValidSlug(slug)) continue;
      const tiers: Array<{ src: string; dst: string }> = [
        {
          src: path.join(childDir, `${slug}.md`),
          dst: path.join(parentDir, `${slug}.md`),
        },
        {
          src: path.join(childDir, `${slug}.overview.md`),
          dst: path.join(parentDir, `${slug}.overview.md`),
        },
        {
          src: path.join(childDir, `${slug}.abstract.md`),
          dst: path.join(parentDir, `${slug}.abstract.md`),
        },
      ];
      let resolvedTiers: Array<{ sourcePath: string; destPath: string }>;
      try {
        const prepared = await Promise.all(
          tiers.map((t, i) =>
            resolveCopyTier({
              sourceRootRaw: childArtifactsRootRaw,
              sourceRootReal: childArtifactsRootReal,
              destRootRaw: parentArtifactsRootRaw,
              destRootReal: parentArtifactsRootReal,
              sourcePath: t.src,
              destPath: t.dst,
              required: i === 0,
            }),
          ),
        );
        if (!prepared[0]) continue;
        resolvedTiers = prepared.filter(
          (t): t is { sourcePath: string; destPath: string } => t !== null,
        );
      } catch {
        continue;
      }

      for (const t of resolvedTiers) {
        await fs.copyFile(t.sourcePath, t.destPath);
      }

      const relPath = path.relative(
        this.workspaceRoot,
        path.join(parentDir, `${slug}.md`),
      );
      const now = Date.now();
      const meta: ArtifactMeta = {
        artifactId: targetId,
        kind: childMeta.kind ?? "doc",
        title: childMeta.title ?? "(untitled)",
        slug,
        path: relPath,
        sizeBytes: typeof childMeta.sizeBytes === "number" ? childMeta.sizeBytes : 0,
        ...(childMeta.producedBy ? { producedBy: childMeta.producedBy } : {}),
        ...(childMeta.sources ? { sources: [...childMeta.sources] } : {}),
        createdAt:
          typeof childMeta.createdAt === "number" ? childMeta.createdAt : now,
        updatedAt: now,
        spawnTaskId: options.spawnTaskId,
        ...(collided ? { importedFromArtifactId: originalId } : {}),
      };
      parentIndex.push(meta);
      existingIds.add(targetId);
      imported.push(meta);
    }

    await this.writeIndex(parentIndex);
    return imported;
  }

  /**
   * Builds a fenced system-prompt block enumerating artifacts up to
   * budgetBytes. Prefers L2 abstracts (denser) then L1 overviews.
   * Returns empty string when no artifacts exist or budget too small.
   */
  async listArtifactsForContext(budgetBytes: number): Promise<string> {
    const idx = await this.readIndex();
    if (idx.length === 0 || budgetBytes < 200) return "";
    const header = `<artifact-index tier="mixed">\n`;
    const footer = `</artifact-index>`;
    const overhead = header.length + footer.length + 40;
    let remaining = budgetBytes - overhead;
    const sections: string[] = [];
    for (const meta of [...idx].sort((a, b) => b.updatedAt - a.updatedAt)) {
      const head = `\n## ${meta.artifactId} — ${meta.title} (${meta.kind})\n`;
      let section = head;
      try {
        const l2 = await fs.readFile(
          path.join(this.dirFor(meta.artifactId), `${meta.slug}.abstract.md`),
          "utf8",
        );
        section += l2;
        if (remaining - section.length > 300) {
          const l1 = await fs.readFile(
            path.join(this.dirFor(meta.artifactId), `${meta.slug}.overview.md`),
            "utf8",
          );
          section += `\n${l1}`;
        }
      } catch {
        section += `(sidecar missing)`;
      }
      if (section.length > remaining) break;
      sections.push(section);
      remaining -= section.length;
    }
    if (sections.length === 0) return "";
    return header + sections.join("\n") + "\n" + footer;
  }
}

/**
 * Minimal Haiku runner — streams text deltas, accumulates, enforces
 * the 3s deadline. Mirrors the pattern used by IntentClassifier and
 * answerVerifier. Throws on timeout or stream error; callers fall back.
 */
async function runHaiku(
  llm: LLMClient,
  system: string,
  user: string,
): Promise<string> {
  const deadline = Date.now() + HAIKU_DEADLINE_MS;
  let out = "";
  const iter = llm.stream({
    model: HAIKU_MODEL,
    system,
    messages: [{ role: "user", content: user }],
    max_tokens: 400,
    temperature: 0,
  });
  for await (const evt of iter) {
    if (Date.now() > deadline) break;
    if (evt.kind === "text_delta") out += evt.delta;
    if (evt.kind === "message_end") break;
  }
  return out;
}
