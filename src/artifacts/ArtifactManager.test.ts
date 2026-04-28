/**
 * ArtifactManager — T4-20 tests. LLMClient is stubbed; Haiku failure path
 * is exercised explicitly via a throwing stub.
 */

import { describe, it, expect, beforeEach, afterEach } from "vitest";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { ArtifactManager, type ArtifactMeta } from "./ArtifactManager.js";
import type { LLMClient, LLMEvent, LLMStreamRequest } from "../transport/LLMClient.js";

class OkStubLLM {
  async *stream(req: LLMStreamRequest): AsyncGenerator<LLMEvent, void, void> {
    void req;
    yield { kind: "text_delta", blockIndex: 0, delta: "First line summary\nSecond line" };
    yield { kind: "message_end", stopReason: "end_turn", usage: { inputTokens: 1, outputTokens: 1 } };
  }
}

class L2StubLLM {
  async *stream(req: LLMStreamRequest): AsyncGenerator<LLMEvent, void, void> {
    const system: string = typeof req.system === "string" ? req.system : "";
    if (system.includes("two lines")) {
      yield { kind: "text_delta", blockIndex: 0, delta: "Line one\nLine two" };
    } else {
      yield {
        kind: "text_delta",
        blockIndex: 0,
        delta:
          "---\nkind: report\ntitle: Sample\noutput_schema:\n  - field: summary\n    type: string\n---",
      };
    }
    yield { kind: "message_end", stopReason: "end_turn", usage: { inputTokens: 1, outputTokens: 1 } };
  }
}

class ThrowingLLM {
  async *stream(_req: LLMStreamRequest): AsyncGenerator<LLMEvent, void, void> {
    void _req;
    throw new Error("haiku down");
    // eslint-disable-next-line no-unreachable
    yield { kind: "message_end", stopReason: "end_turn", usage: { inputTokens: 0, outputTokens: 0 } };
  }
}

describe("ArtifactManager", () => {
  let root: string;
  beforeEach(async () => {
    root = await fs.mkdtemp(path.join(os.tmpdir(), "artifact-mgr-"));
  });
  afterEach(async () => {
    await fs.rm(root, { recursive: true, force: true });
  });

  it("create writes L0 + L1 + L2 + index entry", async () => {
    const mgr = new ArtifactManager(root, new L2StubLLM() as unknown as LLMClient);
    const meta = await mgr.create({
      kind: "report",
      title: "Q1 Financial Report",
      content: "Line one\nLine two\nLine three",
    });
    expect(meta.artifactId).toMatch(/^[0-9A-Z]{26}$/);
    expect(meta.slug).toBe("q1-financial-report");
    const dir = path.join(root, "artifacts", meta.artifactId);
    const files = await fs.readdir(dir);
    expect(files).toContain(`${meta.slug}.md`);
    expect(files).toContain(`${meta.slug}.overview.md`);
    expect(files).toContain(`${meta.slug}.abstract.md`);
    const index = JSON.parse(await fs.readFile(path.join(root, "artifacts", "index.json"), "utf8"));
    expect(index).toHaveLength(1);
    expect(index[0].artifactId).toBe(meta.artifactId);
  });

  it("readL0 returns full content", async () => {
    const mgr = new ArtifactManager(root, new OkStubLLM() as unknown as LLMClient);
    const content = "Full\nbody\nof the artifact.";
    const meta = await mgr.create({ kind: "doc", title: "Spec", content });
    expect(await mgr.readL0(meta.artifactId)).toBe(content);
  });

  it("readL1 returns 2-line summary", async () => {
    const mgr = new ArtifactManager(root, new L2StubLLM() as unknown as LLMClient);
    const meta = await mgr.create({ kind: "doc", title: "X", content: "alpha\nbeta\ngamma" });
    const l1 = await mgr.readL1(meta.artifactId);
    expect(l1.split("\n").length).toBeLessThanOrEqual(2);
    expect(l1).toContain("Line");
  });

  it("readL2 returns structured frontmatter", async () => {
    const mgr = new ArtifactManager(root, new L2StubLLM() as unknown as LLMClient);
    const meta = await mgr.create({ kind: "report", title: "Y", content: "body" });
    const l2 = await mgr.readL2(meta.artifactId);
    expect(l2.startsWith("---")).toBe(true);
    expect(l2.trimEnd().endsWith("---")).toBe(true);
    expect(l2).toContain("kind:");
  });

  it("update regenerates L1 + L2 + bumps updatedAt", async () => {
    const mgr = new ArtifactManager(root, new L2StubLLM() as unknown as LLMClient);
    const meta = await mgr.create({ kind: "doc", title: "Z", content: "v1" });
    await new Promise((r) => setTimeout(r, 5));
    const updated = await mgr.update(meta.artifactId, "v2 longer content replacing v1");
    expect(updated.updatedAt).toBeGreaterThanOrEqual(meta.updatedAt);
    expect(updated.sizeBytes).toBe(Buffer.byteLength("v2 longer content replacing v1", "utf8"));
    expect(await mgr.readL0(meta.artifactId)).toBe("v2 longer content replacing v1");
  });

  it("delete removes dir + drops index entry", async () => {
    const mgr = new ArtifactManager(root, new OkStubLLM() as unknown as LLMClient);
    const meta = await mgr.create({ kind: "doc", title: "gone", content: "x" });
    await mgr.delete(meta.artifactId);
    const list = await mgr.list();
    expect(list.find((m) => m.artifactId === meta.artifactId)).toBeUndefined();
    const exists = await fs
      .access(path.join(root, "artifacts", meta.artifactId))
      .then(() => true)
      .catch(() => false);
    expect(exists).toBe(false);
  });

  it("haiku failure → deterministic fallback for L1 + L2", async () => {
    const mgr = new ArtifactManager(root, new ThrowingLLM() as unknown as LLMClient);
    const meta = await mgr.create({
      kind: "doc",
      title: "Fallback Case",
      content: "Opening sentence.\nFollow-up line.\nMore body.",
    });
    const l1 = await mgr.readL1(meta.artifactId);
    expect(l1).toContain("Opening sentence");
    const l2 = await mgr.readL2(meta.artifactId);
    expect(l2).toContain("kind: doc");
    expect(l2).toContain("title: Fallback Case");
  });

  it("listArtifactsForContext produces a fenced block within budget", async () => {
    const mgr = new ArtifactManager(root, new L2StubLLM() as unknown as LLMClient);
    await mgr.create({ kind: "report", title: "One", content: "body one" });
    await mgr.create({ kind: "report", title: "Two", content: "body two" });
    const block = await mgr.listArtifactsForContext(5000);
    expect(block).toContain('<artifact-index tier="mixed">');
    expect(block).toContain("</artifact-index>");
    expect(block.length).toBeLessThan(5000);
  });

  it("listArtifactsForContext returns empty when budget too small", async () => {
    const mgr = new ArtifactManager(root, new OkStubLLM() as unknown as LLMClient);
    await mgr.create({ kind: "doc", title: "x", content: "body" });
    expect(await mgr.listArtifactsForContext(50)).toBe("");
  });

  // ── Spawn artifact handoff (2026-04-20) ────────────────────────────
  describe("importFromDir — spawn child handoff", () => {
    let childRoot: string;
    beforeEach(async () => {
      childRoot = await fs.mkdtemp(path.join(os.tmpdir(), "artifact-child-"));
    });
    afterEach(async () => {
      await fs.rm(childRoot, { recursive: true, force: true });
    });

    it("copies L0/L1/L2 files and appends to parent index", async () => {
      const childMgr = new ArtifactManager(childRoot, new OkStubLLM() as unknown as LLMClient);
      const childMeta = await childMgr.create({
        kind: "report",
        title: "Group 4 of 5",
        content: "113 skills, detailed findings here.",
      });

      const parentMgr = new ArtifactManager(root);
      const imported = await parentMgr.importFromDir(childRoot, { spawnTaskId: "spawn_t1" });
      expect(imported).toHaveLength(1);

      const parentList = await parentMgr.list();
      const entry = parentList.find((m) => m.artifactId === imported[0]!.artifactId);
      expect(entry).toBeDefined();

      const parentDir = path.join(root, "artifacts", imported[0]!.artifactId);
      const files = await fs.readdir(parentDir);
      expect(files).toContain(`${childMeta.slug}.md`);
      expect(files).toContain(`${childMeta.slug}.overview.md`);
      expect(files).toContain(`${childMeta.slug}.abstract.md`);
      const l0 = await parentMgr.readL0(imported[0]!.artifactId);
      expect(l0).toContain("113 skills");
    });

    it("tags imported entries with spawnTaskId", async () => {
      const childMgr = new ArtifactManager(childRoot, new OkStubLLM() as unknown as LLMClient);
      await childMgr.create({ kind: "doc", title: "hello", content: "hi" });

      const parentMgr = new ArtifactManager(root);
      const imported = await parentMgr.importFromDir(childRoot, { spawnTaskId: "spawn_xyz" });
      expect(imported[0]!.spawnTaskId).toBe("spawn_xyz");

      const parentList = await parentMgr.list();
      expect(parentList[0]!.spawnTaskId).toBe("spawn_xyz");
    });

    it("on artifactId collision mints a new id and sets importedFromArtifactId", async () => {
      const parentMgr = new ArtifactManager(root, new OkStubLLM() as unknown as LLMClient);
      const parentMeta = await parentMgr.create({
        kind: "doc",
        title: "parent owns",
        content: "original parent",
      });

      // Write a child index.json that deliberately reuses parentMeta.artifactId.
      const childArtifactsDir = path.join(childRoot, "artifacts");
      const childDir = path.join(childArtifactsDir, parentMeta.artifactId);
      await fs.mkdir(childDir, { recursive: true });
      const slug = "collision";
      await fs.writeFile(path.join(childDir, `${slug}.md`), "child body");
      await fs.writeFile(path.join(childDir, `${slug}.overview.md`), "child L1");
      await fs.writeFile(path.join(childDir, `${slug}.abstract.md`), "---\nkind: doc\n---");
      const childIdx: ArtifactMeta[] = [
        {
          artifactId: parentMeta.artifactId,
          kind: "doc",
          title: "child clone",
          slug,
          path: `artifacts/${parentMeta.artifactId}/${slug}.md`,
          sizeBytes: 10,
          createdAt: Date.now(),
          updatedAt: Date.now(),
        },
      ];
      await fs.writeFile(
        path.join(childArtifactsDir, "index.json"),
        JSON.stringify(childIdx),
      );

      const imported = await parentMgr.importFromDir(childRoot, { spawnTaskId: "spawn_col" });
      expect(imported).toHaveLength(1);
      expect(imported[0]!.artifactId).not.toBe(parentMeta.artifactId);
      expect(imported[0]!.importedFromArtifactId).toBe(parentMeta.artifactId);

      // Parent's original artifact untouched.
      expect(await parentMgr.readL0(parentMeta.artifactId)).toBe("original parent");
      // Imported copy readable under the new id.
      expect(await parentMgr.readL0(imported[0]!.artifactId)).toBe("child body");
    });

    it("missing child index.json returns empty array", async () => {
      const parentMgr = new ArtifactManager(root);
      const imported = await parentMgr.importFromDir(childRoot, { spawnTaskId: "spawn_empty" });
      expect(imported).toEqual([]);
      expect(await parentMgr.list()).toEqual([]);
    });

    it("round-trip: two artifacts produced by child visible to parent.list()", async () => {
      const childMgr = new ArtifactManager(childRoot, new OkStubLLM() as unknown as LLMClient);
      await childMgr.create({ kind: "report", title: "alpha", content: "a" });
      await childMgr.create({ kind: "report", title: "beta", content: "b" });

      const parentMgr = new ArtifactManager(root);
      const imported = await parentMgr.importFromDir(childRoot, { spawnTaskId: "spawn_rt" });
      expect(imported).toHaveLength(2);

      const parentList = await parentMgr.list();
      expect(parentList).toHaveLength(2);
      expect(parentList.map((m) => m.title).sort()).toEqual(["alpha", "beta"]);
      // Every imported entry got tagged.
      for (const m of parentList) {
        expect(m.spawnTaskId).toBe("spawn_rt");
      }
    });

    it("malformed child index.json returns empty array", async () => {
      await fs.mkdir(path.join(childRoot, "artifacts"), { recursive: true });
      await fs.writeFile(
        path.join(childRoot, "artifacts", "index.json"),
        "{not valid json",
      );
      const parentMgr = new ArtifactManager(root);
      const imported = await parentMgr.importFromDir(childRoot, { spawnTaskId: "spawn_bad" });
      expect(imported).toEqual([]);
    });

    it("rejects child artifactIds that traverse out of artifact roots", async () => {
      const childArtifactsDir = path.join(childRoot, "artifacts");
      const escapedChildDir = path.join(childRoot, "SOUL");
      await fs.mkdir(escapedChildDir, { recursive: true });
      await fs.writeFile(path.join(escapedChildDir, "escape.md"), "malicious");
      await fs.writeFile(path.join(escapedChildDir, "escape.overview.md"), "malicious");
      await fs.writeFile(path.join(escapedChildDir, "escape.abstract.md"), "---\nkind: doc\n---");
      await fs.mkdir(childArtifactsDir, { recursive: true });
      await fs.writeFile(
        path.join(childArtifactsDir, "index.json"),
        JSON.stringify([
          {
            artifactId: "../SOUL",
            kind: "doc",
            title: "escape",
            slug: "escape",
            path: "artifacts/../SOUL/escape.md",
            sizeBytes: 9,
            createdAt: Date.now(),
            updatedAt: Date.now(),
          },
        ]),
      );

      const parentMgr = new ArtifactManager(root);
      const imported = await parentMgr.importFromDir(childRoot, { spawnTaskId: "spawn_escape" });

      expect(imported).toEqual([]);
      await expect(fs.access(path.join(root, "SOUL"))).rejects.toMatchObject({
        code: "ENOENT",
      });
      expect(await parentMgr.list()).toEqual([]);
    });

    it("rejects child slugs that traverse out of an artifact directory", async () => {
      const artifactId = "01ARZ3NDEKTSV4RRFFQ69G5FAV";
      const childArtifactsDir = path.join(childRoot, "artifacts");
      const childDir = path.join(childArtifactsDir, artifactId);
      await fs.mkdir(childDir, { recursive: true });
      await fs.writeFile(path.join(childArtifactsDir, "parent-owned.md"), "malicious");
      await fs.writeFile(
        path.join(childArtifactsDir, "index.json"),
        JSON.stringify([
          {
            artifactId,
            kind: "doc",
            title: "bad slug",
            slug: "../parent-owned",
            path: `artifacts/${artifactId}/../parent-owned.md`,
            sizeBytes: 9,
            createdAt: Date.now(),
            updatedAt: Date.now(),
          },
        ]),
      );

      const parentMgr = new ArtifactManager(root);
      const imported = await parentMgr.importFromDir(childRoot, { spawnTaskId: "spawn_slug" });

      expect(imported).toEqual([]);
      await expect(
        fs.access(path.join(root, "artifacts", "parent-owned.md")),
      ).rejects.toMatchObject({ code: "ENOENT" });
      expect(await parentMgr.list()).toEqual([]);
    });

    it("rejects child artifact files whose realpath escapes the child artifact root", async () => {
      const artifactId = "01ARZ3NDEKTSV4RRFFQ69G5FAW";
      const slug = "leak";
      const childArtifactsDir = path.join(childRoot, "artifacts");
      const childDir = path.join(childArtifactsDir, artifactId);
      await fs.mkdir(childDir, { recursive: true });
      await fs.writeFile(path.join(root, "SOUL.md"), "parent secret", "utf8");
      await fs.symlink(path.join(root, "SOUL.md"), path.join(childDir, `${slug}.md`));
      await fs.writeFile(path.join(childDir, `${slug}.overview.md`), "preview");
      await fs.writeFile(path.join(childDir, `${slug}.abstract.md`), "---\nkind: doc\n---");
      await fs.writeFile(
        path.join(childArtifactsDir, "index.json"),
        JSON.stringify([
          {
            artifactId,
            kind: "doc",
            title: "leak",
            slug,
            path: `artifacts/${artifactId}/${slug}.md`,
            sizeBytes: 13,
            createdAt: Date.now(),
            updatedAt: Date.now(),
          },
        ]),
      );

      const parentMgr = new ArtifactManager(root);
      const imported = await parentMgr.importFromDir(childRoot, { spawnTaskId: "spawn_link" });

      expect(imported).toEqual([]);
      expect(await parentMgr.list()).toEqual([]);
    });

    it("preserves existing parent artifacts when importing", async () => {
      const parentMgr = new ArtifactManager(root, new OkStubLLM() as unknown as LLMClient);
      await parentMgr.create({ kind: "doc", title: "parent-made", content: "pm" });

      const childMgr = new ArtifactManager(childRoot, new OkStubLLM() as unknown as LLMClient);
      await childMgr.create({ kind: "doc", title: "child-made", content: "cm" });

      await parentMgr.importFromDir(childRoot, { spawnTaskId: "spawn_pre" });
      const parentList = await parentMgr.list();
      expect(parentList.map((m) => m.title).sort()).toEqual(["child-made", "parent-made"]);
    });
  });
});
