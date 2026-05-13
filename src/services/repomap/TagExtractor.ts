import type { Tag, SupportedLanguage } from "./types.js";

// eslint-disable-next-line @typescript-eslint/no-explicit-any
let ParserClass: any;
let parserReady: Promise<void> | null = null;
// eslint-disable-next-line @typescript-eslint/no-explicit-any
const languageParsers = new Map<SupportedLanguage, any>();

async function ensureInit(): Promise<void> {
  if (!parserReady) {
    parserReady = (async () => {
      const mod = await import("web-tree-sitter");
      ParserClass = mod.default ?? mod;
      if (typeof ParserClass.init === "function") {
        await ParserClass.init();
      }
    })();
  }
  await parserReady;
}

function resolveWasmPath(lang: SupportedLanguage): string {
  const wasmName = lang === "typescript" ? "tree-sitter-typescript" : `tree-sitter-${lang}`;
  try {
    return require.resolve(`tree-sitter-wasms/out/${wasmName}.wasm`);
  } catch {
    const path = require("node:path");
    return path.join(__dirname, `../../../node_modules/tree-sitter-wasms/out/${wasmName}.wasm`);
  }
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
async function getParser(lang: SupportedLanguage): Promise<any> {
  await ensureInit();
  let parser = languageParsers.get(lang);
  if (parser) return parser;

  const wasmPath = resolveWasmPath(lang);
  const language = await ParserClass.Language.load(wasmPath);
  parser = new ParserClass();
  parser.setLanguage(language);
  languageParsers.set(lang, parser);
  return parser;
}

interface TreeSitterNode {
  type: string;
  text: string;
  startPosition: { row: number; column: number };
  childCount: number;
  child(index: number): TreeSitterNode | null;
  children: TreeSitterNode[];
  namedChildren: TreeSitterNode[];
  firstNamedChild: TreeSitterNode | null;
}

const TS_JS_DEF_TYPES = new Set([
  "function_declaration",
  "class_declaration",
  "interface_declaration",
  "type_alias_declaration",
  "enum_declaration",
  "lexical_declaration",
  "variable_declaration",
  "method_definition",
  "public_field_definition",
]);

const TS_JS_REF_TYPES = new Set([
  "identifier",
  "property_identifier",
  "type_identifier",
]);

const PYTHON_DEF_TYPES = new Set([
  "function_definition",
  "class_definition",
]);

const PYTHON_REF_TYPES = new Set([
  "identifier",
]);

const JS_KEYWORDS = new Set([
  "if", "else", "for", "while", "do", "switch", "case", "break", "continue",
  "return", "throw", "try", "catch", "finally", "new", "delete", "typeof",
  "instanceof", "void", "in", "of", "true", "false", "null", "undefined",
  "let", "const", "var", "function", "class", "extends", "super", "this",
  "import", "export", "default", "from", "as", "async", "await", "yield",
  "static", "get", "set", "constructor", "type", "interface", "enum",
  "implements", "declare", "abstract", "readonly", "private", "protected",
  "public", "module", "namespace", "require",
]);

const PY_KEYWORDS = new Set([
  "if", "else", "elif", "for", "while", "break", "continue", "return",
  "def", "class", "import", "from", "as", "try", "except", "finally",
  "raise", "with", "yield", "lambda", "pass", "True", "False", "None",
  "and", "or", "not", "in", "is", "global", "nonlocal", "assert", "del",
  "self", "cls",
]);

function extractNameFromDeclaration(node: TreeSitterNode): string | null {
  for (const child of node.namedChildren) {
    if (
      child.type === "identifier" ||
      child.type === "type_identifier" ||
      child.type === "property_identifier"
    ) {
      return child.text;
    }
    if (child.type === "variable_declarator") {
      const nameNode = child.firstNamedChild;
      if (nameNode && (nameNode.type === "identifier" || nameNode.type === "type_identifier")) {
        return nameNode.text;
      }
    }
  }
  return null;
}

function extractTsJsTags(root: TreeSitterNode, file: string, language: string): Tag[] {
  const tags: Tag[] = [];
  const defNames = new Set<string>();

  function walkDefs(node: TreeSitterNode): void {
    if (TS_JS_DEF_TYPES.has(node.type)) {
      const name = extractNameFromDeclaration(node);
      if (name && !JS_KEYWORDS.has(name) && name.length > 1) {
        tags.push({ file, name, kind: "def", line: node.startPosition.row + 1, language });
        defNames.add(name);
      }
    }
    for (let i = 0; i < node.childCount; i++) {
      const child = node.child(i);
      if (child) walkDefs(child);
    }
  }

  function walkRefs(node: TreeSitterNode): void {
    if (TS_JS_REF_TYPES.has(node.type) && !defNames.has(node.text)) {
      const name = node.text;
      if (!JS_KEYWORDS.has(name) && name.length > 1) {
        tags.push({ file, name, kind: "ref", line: node.startPosition.row + 1, language });
      }
    }
    for (let i = 0; i < node.childCount; i++) {
      const child = node.child(i);
      if (child) walkRefs(child);
    }
  }

  walkDefs(root);
  walkRefs(root);
  return tags;
}

function extractPythonTags(root: TreeSitterNode, file: string, language: string): Tag[] {
  const tags: Tag[] = [];
  const defNames = new Set<string>();

  function walkDefs(node: TreeSitterNode): void {
    if (PYTHON_DEF_TYPES.has(node.type)) {
      const nameNode = node.namedChildren.find((c) => c.type === "identifier");
      if (nameNode && !PY_KEYWORDS.has(nameNode.text) && nameNode.text.length > 1) {
        tags.push({ file, name: nameNode.text, kind: "def", line: nameNode.startPosition.row + 1, language });
        defNames.add(nameNode.text);
      }
    }
    if (node.type === "assignment") {
      const left = node.namedChildren[0];
      if (left?.type === "identifier" && !PY_KEYWORDS.has(left.text) && left.text.length > 1) {
        tags.push({ file, name: left.text, kind: "def", line: left.startPosition.row + 1, language });
        defNames.add(left.text);
      }
    }
    for (let i = 0; i < node.childCount; i++) {
      const child = node.child(i);
      if (child) walkDefs(child);
    }
  }

  function walkRefs(node: TreeSitterNode): void {
    if (PYTHON_REF_TYPES.has(node.type) && !defNames.has(node.text)) {
      const name = node.text;
      if (!PY_KEYWORDS.has(name) && name.length > 1) {
        tags.push({ file, name, kind: "ref", line: node.startPosition.row + 1, language });
      }
    }
    for (let i = 0; i < node.childCount; i++) {
      const child = node.child(i);
      if (child) walkRefs(child);
    }
  }

  walkDefs(root);
  walkRefs(root);
  return tags;
}

export async function extractTags(source: string, file: string, language: SupportedLanguage): Promise<Tag[]> {
  const parser = await getParser(language);
  const tree = parser.parse(source);
  const root = tree.rootNode as unknown as TreeSitterNode;

  if (language === "python") {
    return extractPythonTags(root, file, language);
  }
  return extractTsJsTags(root, file, language);
}

export function _resetParsers(): void {
  languageParsers.clear();
  parserReady = null;
}
