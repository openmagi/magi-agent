import type { Tag, Edge } from "./types.js";

export class DependencyGraph {
  private outEdges = new Map<string, Map<string, number>>();
  private inEdges = new Map<string, Map<string, number>>();
  private allFiles = new Set<string>();

  static build(tags: Tag[]): DependencyGraph {
    const graph = new DependencyGraph();

    const defIndex = new Map<string, Set<string>>();
    for (const tag of tags) {
      graph.allFiles.add(tag.file);
      if (tag.kind === "def") {
        let files = defIndex.get(tag.name);
        if (!files) {
          files = new Set();
          defIndex.set(tag.name, files);
        }
        files.add(tag.file);
      }
    }

    for (const tag of tags) {
      if (tag.kind !== "ref") continue;
      const defs = defIndex.get(tag.name);
      if (!defs) continue;
      for (const defFile of defs) {
        if (defFile === tag.file) continue;
        graph.addEdge(tag.file, defFile);
      }
    }

    return graph;
  }

  private addEdge(from: string, to: string): void {
    let outMap = this.outEdges.get(from);
    if (!outMap) {
      outMap = new Map();
      this.outEdges.set(from, outMap);
    }
    outMap.set(to, (outMap.get(to) ?? 0) + 1);

    let inMap = this.inEdges.get(to);
    if (!inMap) {
      inMap = new Map();
      this.inEdges.set(to, inMap);
    }
    inMap.set(from, (inMap.get(from) ?? 0) + 1);
  }

  getEdges(): Edge[] {
    const edges: Edge[] = [];
    for (const [from, targets] of this.outEdges) {
      for (const [to, weight] of targets) {
        edges.push({ from, to, weight });
      }
    }
    return edges;
  }

  getFiles(): Set<string> {
    return new Set(this.allFiles);
  }

  getOutDegree(file: string): number {
    const outMap = this.outEdges.get(file);
    if (!outMap) return 0;
    let total = 0;
    for (const w of outMap.values()) total += w;
    return total;
  }

  getIncomingEdges(file: string): Edge[] {
    const inMap = this.inEdges.get(file);
    if (!inMap) return [];
    const edges: Edge[] = [];
    for (const [from, weight] of inMap) {
      edges.push({ from, to: file, weight });
    }
    return edges;
  }
}
