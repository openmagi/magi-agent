export interface KnowledgeDocumentTreeFields {
  id: string;
  filename: string;
  parent_document_id?: string | null;
  sort_order?: number | null;
  source_external_id?: string | null;
  source_parent_external_id?: string | null;
}

export interface KnowledgeDocumentTreeNode<T extends KnowledgeDocumentTreeFields> {
  doc: T;
  children: Array<KnowledgeDocumentTreeNode<T>>;
}

export interface FlattenedKnowledgeDocument<T extends KnowledgeDocumentTreeFields> {
  doc: T;
  depth: number;
  hasChildren: boolean;
}

function compareDocs<T extends KnowledgeDocumentTreeFields>(a: T, b: T): number {
  const orderA = a.sort_order ?? 0;
  const orderB = b.sort_order ?? 0;
  if (orderA !== orderB) return orderA - orderB;
  return a.filename.localeCompare(b.filename);
}

function sortNodes<T extends KnowledgeDocumentTreeFields>(
  nodes: Array<KnowledgeDocumentTreeNode<T>>,
): Array<KnowledgeDocumentTreeNode<T>> {
  return nodes
    .sort((a, b) => compareDocs(a.doc, b.doc))
    .map((node) => ({ ...node, children: sortNodes(node.children) }));
}

export function buildKnowledgeDocumentTree<T extends KnowledgeDocumentTreeFields>(
  docs: T[],
): Array<KnowledgeDocumentTreeNode<T>> {
  const byId = new Map<string, KnowledgeDocumentTreeNode<T>>();
  const bySourceId = new Map<string, KnowledgeDocumentTreeNode<T>>();
  const roots: Array<KnowledgeDocumentTreeNode<T>> = [];

  for (const doc of docs) {
    const node = { doc, children: [] };
    byId.set(doc.id, node);
    if (doc.source_external_id) bySourceId.set(doc.source_external_id, node);
  }

  for (const node of byId.values()) {
    const doc = node.doc;
    const parent =
      (doc.parent_document_id ? byId.get(doc.parent_document_id) : undefined) ||
      (doc.source_parent_external_id ? bySourceId.get(doc.source_parent_external_id) : undefined);

    if (parent && parent !== node) parent.children.push(node);
    else roots.push(node);
  }

  return sortNodes(roots);
}

export function flattenKnowledgeDocumentTree<T extends KnowledgeDocumentTreeFields>(
  docs: T[],
  collapsedIds: Set<string> = new Set(),
): Array<FlattenedKnowledgeDocument<T>> {
  const flattened: Array<FlattenedKnowledgeDocument<T>> = [];

  function visit(node: KnowledgeDocumentTreeNode<T>, depth: number) {
    flattened.push({ doc: node.doc, depth, hasChildren: node.children.length > 0 });
    if (collapsedIds.has(node.doc.id)) return;
    for (const child of node.children) visit(child, depth + 1);
  }

  for (const root of buildKnowledgeDocumentTree(docs)) visit(root, 0);
  return flattened;
}
