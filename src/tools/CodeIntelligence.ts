import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import ts from "typescript";
import type { Tool, ToolContext, ToolResult } from "../Tool.js";
import { Workspace } from "../storage/Workspace.js";
import { errorResult } from "../util/toolResult.js";
import { parseCommandLine, StdioLspClient } from "./lspClient.js";

export type CodeIntelligenceAction =
  | "definition"
  | "references"
  | "hover"
  | "document_symbols"
  | "workspace_symbols"
  | "diagnostics"
  | "rename"
  | "code_actions";

export interface CodeIntelligenceInput {
  action: CodeIntelligenceAction;
  language?: "typescript" | "python" | "auto";
  projectPath?: string;
  file?: string;
  line?: number;
  column?: number;
  query?: string;
  newName?: string;
  maxResults?: number;
}

export interface CodeIntelligenceResult {
  name?: string;
  kind?: string;
  file: string;
  line: number;
  column: number;
  endLine?: number;
  endColumn?: number;
  startOffset?: number;
  length?: number;
  sourceText?: string;
  preview?: string;
  text?: string;
  newText?: string;
  editCount?: number;
  targetFiles?: string[];
  severity?: "error" | "warning" | "suggestion" | "message";
  code?: string;
}

export interface CodeIntelligenceOutput {
  action: CodeIntelligenceAction;
  language: "typescript" | "python";
  projectPath: string;
  results: CodeIntelligenceResult[];
  resultCount: number;
  truncated: boolean;
}

const INPUT_SCHEMA = {
  type: "object",
  properties: {
    action: {
      type: "string",
      enum: [
        "definition",
        "references",
        "hover",
        "document_symbols",
        "workspace_symbols",
        "diagnostics",
        "rename",
        "code_actions",
      ],
      description:
        "Semantic code-intelligence operation to run: definition, references, hover, rename, code_actions, document_symbols, workspace_symbols, or diagnostics.",
    },
    language: {
      type: "string",
      enum: ["typescript", "python", "auto"],
      description:
        "Language backend to use. Default/typescript uses the built-in TypeScript service. python uses the configured external Python LSP server.",
    },
    projectPath: {
      type: "string",
      description: "Workspace-relative TypeScript project root containing tsconfig.json. Default: workspace root.",
    },
    file: {
      type: "string",
      description:
        "Workspace-relative source file. Required for definition, references, hover, rename, code_actions, and document_symbols.",
    },
    line: {
      type: "integer",
      minimum: 1,
      description: "1-based line number. Required for definition, references, hover, rename, and code_actions.",
    },
    column: {
      type: "integer",
      minimum: 1,
      description: "1-based column number. Required for definition, references, hover, rename, and code_actions.",
    },
    query: {
      type: "string",
      description: "Symbol query. Required for workspace_symbols.",
    },
    newName: {
      type: "string",
      description: "Replacement identifier. Required for rename.",
    },
    maxResults: {
      type: "integer",
      minimum: 1,
      maximum: 200,
      description: "Maximum results to return. Default: 50.",
    },
  },
  required: ["action"],
} as const;

const ACTIONS = new Set<CodeIntelligenceAction>([
  "definition",
  "references",
  "hover",
  "document_symbols",
  "workspace_symbols",
  "diagnostics",
  "rename",
  "code_actions",
]);

interface TypeScriptProject {
  service: ts.LanguageService;
  projectRoot: string;
  projectPath: string;
  wsRoot: string;
}

export function makeCodeIntelligenceTool(
  workspaceRoot: string,
): Tool<CodeIntelligenceInput, CodeIntelligenceOutput> {
  const defaultWorkspace = new Workspace(workspaceRoot);
  return {
    name: "CodeIntelligence",
    description:
      "Run read-only semantic code intelligence for coding work: go to definition, find references, rename locations, code actions, hover/type info, document symbols, workspace symbols, and diagnostics. Uses the built-in TypeScript service by default and configured external LSP servers for other languages.",
    inputSchema: INPUT_SCHEMA,
    permission: "read",
    shouldDefer: true,
    kind: "core",
    mutatesWorkspace: false,
    isConcurrencySafe: true,
    validate(input) {
      if (!input || !ACTIONS.has(input.action)) {
        return "`action` must be one of: definition, references, hover, rename, code_actions, document_symbols, workspace_symbols, diagnostics";
      }
      if (
        input.language !== undefined &&
        input.language !== "typescript" &&
        input.language !== "python" &&
        input.language !== "auto"
      ) {
        return "`language` must be typescript, python, or auto";
      }
      if (input.projectPath !== undefined && typeof input.projectPath !== "string") {
        return "`projectPath` must be a string";
      }
      if (input.file !== undefined && typeof input.file !== "string") {
        return "`file` must be a string";
      }
      if (requiresPosition(input.action)) {
        if (!input.file) return "`file` is required for this action";
        if (!Number.isInteger(input.line) || Number(input.line) < 1) {
          return "`line` must be a positive integer for this action";
        }
        if (!Number.isInteger(input.column) || Number(input.column) < 1) {
          return "`column` must be a positive integer for this action";
        }
      }
      if (input.action === "document_symbols" && !input.file) {
        return "`file` is required for document_symbols";
      }
      if (input.action === "rename") {
        if (typeof input.newName !== "string" || input.newName.trim().length === 0) {
          return "`newName` is required for rename";
        }
        if (!/^[A-Za-z_$][0-9A-Za-z_$]*$/.test(input.newName.trim())) {
          return "`newName` must be a valid JavaScript/TypeScript identifier";
        }
      }
      if (
        input.action === "workspace_symbols" &&
        (typeof input.query !== "string" || input.query.trim().length === 0)
      ) {
        return "`query` is required for workspace_symbols";
      }
      if (
        input.maxResults !== undefined &&
        (!Number.isInteger(input.maxResults) ||
          input.maxResults < 1 ||
          input.maxResults > 200)
      ) {
        return "`maxResults` must be an integer in [1..200]";
      }
      return null;
    },
    async execute(
      input: CodeIntelligenceInput,
      ctx: ToolContext,
    ): Promise<ToolResult<CodeIntelligenceOutput>> {
      const start = Date.now();
      try {
        const ws = ctx.spawnWorkspace ?? defaultWorkspace;
        const language = resolveLanguage(input);
        if (language === "python") {
          return await runExternalLspAction(ws, input, language, start);
        }

        const project = loadTypeScriptProject(ws, input.projectPath ?? ".");
        const maxResults = Math.min(200, Math.max(1, input.maxResults ?? 50));
        const allResults = runAction(project, input);
        const results = allResults.slice(0, maxResults);
        return {
          status: "ok",
          output: {
            action: input.action,
            language: "typescript",
            projectPath: project.projectPath,
            results,
            resultCount: results.length,
            truncated: allResults.length > results.length,
          },
          metadata: {
            evidenceKind: "code_intelligence",
            action: input.action,
            language: "typescript",
            resultCount: results.length,
          },
          durationMs: Date.now() - start,
        };
      } catch (err) {
        return errorResult(err, start);
      }
    },
  };
}

function resolveLanguage(input: CodeIntelligenceInput): "typescript" | "python" {
  if (input.language === "python") return "python";
  if (input.language === "typescript" || input.language === undefined) return "typescript";
  if (input.file?.endsWith(".py") && pythonLspCommand().trim().length > 0) return "python";
  return "typescript";
}

function requiresPosition(action: CodeIntelligenceAction): boolean {
  return (
    action === "definition" ||
    action === "references" ||
    action === "hover" ||
    action === "rename" ||
    action === "code_actions"
  );
}

interface ExternalLspProject {
  client: StdioLspClient;
  language: "python";
  projectRoot: string;
  projectPath: string;
  wsRoot: string;
  diagnostics: LspDiagnostic[];
}

interface LspPosition {
  line: number;
  character: number;
}

interface LspRange {
  start: LspPosition;
  end: LspPosition;
}

interface LspLocation {
  uri: string;
  range: LspRange;
}

interface LspLocationLink {
  targetUri: string;
  targetRange: LspRange;
  targetSelectionRange?: LspRange;
}

interface LspDocumentSymbol {
  name: string;
  kind: number;
  range: LspRange;
  selectionRange?: LspRange;
  children?: LspDocumentSymbol[];
}

interface LspSymbolInformation {
  name: string;
  kind: number;
  location: LspLocation;
}

interface LspTextEdit {
  range: LspRange;
  newText: string;
}

interface LspTextDocumentEdit {
  textDocument: {
    uri: string;
  };
  edits: LspTextEdit[];
}

interface LspWorkspaceEdit {
  changes?: Record<string, LspTextEdit[]>;
  documentChanges?: LspTextDocumentEdit[];
}

interface LspCodeAction {
  title: string;
  kind?: string;
  edit?: LspWorkspaceEdit;
}

interface LspDiagnostic {
  range: LspRange;
  severity?: number;
  code?: string | number;
  message: string;
}

async function runExternalLspAction(
  ws: Workspace,
  input: CodeIntelligenceInput,
  language: "python",
  start: number,
): Promise<ToolResult<CodeIntelligenceOutput>> {
  const project = await loadExternalLspProject(ws, input.projectPath ?? ".", language);
  try {
    const maxResults = Math.min(200, Math.max(1, input.maxResults ?? 50));
    const allResults = await runLspAction(project, input);
    const results = allResults.slice(0, maxResults);
    return {
      status: "ok",
      output: {
        action: input.action,
        language,
        projectPath: project.projectPath,
        results,
        resultCount: results.length,
        truncated: allResults.length > results.length,
      },
      metadata: {
        evidenceKind: "code_intelligence",
        action: input.action,
        language,
        provider: "external_lsp",
        resultCount: results.length,
      },
      durationMs: Date.now() - start,
    };
  } finally {
    await project.client.shutdown().catch(() => project.client.dispose());
  }
}

async function loadExternalLspProject(
  ws: Workspace,
  projectPath: string,
  language: "python",
): Promise<ExternalLspProject> {
  const commandLine = pythonLspCommand().trim();
  if (!commandLine) {
    throw new Error(
      "Python CodeIntelligence requires MAGI_CODE_INTELLIGENCE_PYTHON_LSP_COMMAND to be configured",
    );
  }
  const projectRoot = ws.resolve(projectPath);
  const parsed = parseCommandLine(commandLine);
  const diagnostics: LspDiagnostic[] = [];
  const client = new StdioLspClient({
    command: parsed.command,
    args: parsed.args,
    cwd: projectRoot,
    onNotification(method, params) {
      if (method !== "textDocument/publishDiagnostics") return;
      const value = params as { diagnostics?: LspDiagnostic[] };
      diagnostics.splice(0, diagnostics.length, ...(value.diagnostics ?? []));
    },
  });

  await client.initialize(pathToFileURL(projectRoot).href);
  return {
    client,
    language,
    projectRoot,
    projectPath: workspaceRelative(ws.root, projectRoot),
    wsRoot: ws.root,
    diagnostics,
  };
}

function pythonLspCommand(): string {
  return process.env.MAGI_CODE_INTELLIGENCE_PYTHON_LSP_COMMAND ?? "";
}

async function runLspAction(
  project: ExternalLspProject,
  input: CodeIntelligenceInput,
): Promise<CodeIntelligenceResult[]> {
  switch (input.action) {
    case "definition":
      await openLspDocument(project, input.file ?? "");
      return lspDefinitions(project, input);
    case "references":
      await openLspDocument(project, input.file ?? "");
      return lspReferences(project, input);
    case "hover":
      await openLspDocument(project, input.file ?? "");
      return lspHover(project, input);
    case "rename":
      await openLspDocument(project, input.file ?? "");
      return lspRename(project, input);
    case "code_actions":
      await openLspDocument(project, input.file ?? "");
      return lspCodeActions(project, input);
    case "document_symbols":
      await openLspDocument(project, input.file ?? "");
      return lspDocumentSymbols(project, input.file ?? "");
    case "workspace_symbols":
      return lspWorkspaceSymbols(project, input.query ?? "");
    case "diagnostics":
      if (input.file) {
        await openLspDocument(project, input.file);
      }
      return project.diagnostics
        .map((diagnostic) => resultFromLspDiagnostic(project, input.file ?? "", diagnostic))
        .filter((item): item is CodeIntelligenceResult => item !== null);
  }
}

async function openLspDocument(project: ExternalLspProject, file: string): Promise<void> {
  const absFile = resolveWorkspaceFile(project.wsRoot, file);
  const text = ts.sys.readFile(absFile);
  if (text === undefined) {
    throw new Error(`File not found: ${file}`);
  }
  project.client.openTextDocument(pathToFileURL(absFile).href, project.language, 1, text);
}

function lspPosition(input: CodeIntelligenceInput): LspPosition {
  return {
    line: Math.max(0, Number(input.line) - 1),
    character: Math.max(0, Number(input.column) - 1),
  };
}

function lspTextDocumentPositionParams(project: ExternalLspProject, input: CodeIntelligenceInput) {
  const absFile = resolveWorkspaceFile(project.wsRoot, input.file ?? "");
  return {
    textDocument: { uri: pathToFileURL(absFile).href },
    position: lspPosition(input),
  };
}

async function lspDefinitions(
  project: ExternalLspProject,
  input: CodeIntelligenceInput,
): Promise<CodeIntelligenceResult[]> {
  const result = await project.client.request(
    "textDocument/definition",
    lspTextDocumentPositionParams(project, input),
  );
  return normalizeLspLocations(project, result);
}

async function lspReferences(
  project: ExternalLspProject,
  input: CodeIntelligenceInput,
): Promise<CodeIntelligenceResult[]> {
  const result = await project.client.request("textDocument/references", {
    ...lspTextDocumentPositionParams(project, input),
    context: { includeDeclaration: true },
  });
  return normalizeLspLocations(project, result);
}

async function lspHover(
  project: ExternalLspProject,
  input: CodeIntelligenceInput,
): Promise<CodeIntelligenceResult[]> {
  const result = (await project.client.request(
    "textDocument/hover",
    lspTextDocumentPositionParams(project, input),
  )) as { contents?: unknown; range?: LspRange } | null;
  if (!result) return [];
  const absFile = resolveWorkspaceFile(project.wsRoot, input.file ?? "");
  const range = result.range ?? { start: lspPosition(input), end: lspPosition(input) };
  const item = resultFromLspRange(project.wsRoot, pathToFileURL(absFile).href, range, {
    text: lspHoverText(result.contents),
  });
  return item ? [item] : [];
}

async function lspRename(
  project: ExternalLspProject,
  input: CodeIntelligenceInput,
): Promise<CodeIntelligenceResult[]> {
  const result = (await project.client.request("textDocument/rename", {
    ...lspTextDocumentPositionParams(project, input),
    newName: input.newName?.trim() ?? "",
  })) as LspWorkspaceEdit | null;
  return workspaceEditResults(project, result, {
    kind: "rename",
    text: input.newName?.trim() ?? "",
  });
}

async function lspCodeActions(
  project: ExternalLspProject,
  input: CodeIntelligenceInput,
): Promise<CodeIntelligenceResult[]> {
  const position = lspPosition(input);
  const result = await project.client.request("textDocument/codeAction", {
    textDocument: {
      uri: pathToFileURL(resolveWorkspaceFile(project.wsRoot, input.file ?? "")).href,
    },
    range: { start: position, end: position },
    context: { diagnostics: project.diagnostics },
  });
  if (!Array.isArray(result)) return [];
  return result
    .map((action) => lspCodeActionResult(project, action as LspCodeAction, input))
    .filter((item): item is CodeIntelligenceResult => item !== null);
}

async function lspDocumentSymbols(
  project: ExternalLspProject,
  file: string,
): Promise<CodeIntelligenceResult[]> {
  const absFile = resolveWorkspaceFile(project.wsRoot, file);
  const uri = pathToFileURL(absFile).href;
  const result = await project.client.request("textDocument/documentSymbol", {
    textDocument: { uri },
  });
  return normalizeLspDocumentSymbols(project, uri, result);
}

async function lspWorkspaceSymbols(
  project: ExternalLspProject,
  query: string,
): Promise<CodeIntelligenceResult[]> {
  const result = await project.client.request("workspace/symbol", { query });
  return normalizeLspSymbols(project, result);
}

function normalizeLspDocumentSymbols(
  project: ExternalLspProject,
  uri: string,
  result: unknown,
): CodeIntelligenceResult[] {
  if (!Array.isArray(result)) return [];
  const out: CodeIntelligenceResult[] = [];
  const visit = (symbol: LspDocumentSymbol): void => {
    const item = resultFromLspRange(project.wsRoot, uri, symbol.selectionRange ?? symbol.range, {
      name: symbol.name,
      kind: lspSymbolKind(symbol.kind),
    });
    if (item) out.push(item);
    for (const child of symbol.children ?? []) visit(child);
  };
  for (const value of result) {
    const symbol = value as Partial<LspDocumentSymbol>;
    if (typeof symbol.name === "string" && symbol.range) {
      visit(symbol as LspDocumentSymbol);
    }
  }
  return out;
}

function normalizeLspLocations(
  project: ExternalLspProject,
  result: unknown,
): CodeIntelligenceResult[] {
  const values = Array.isArray(result) ? result : result ? [result] : [];
  return values
    .map((value) => {
      const item = value as Partial<LspLocation & LspLocationLink>;
      if (typeof item.targetUri === "string" && item.targetRange) {
        return resultFromLspRange(project.wsRoot, item.targetUri, item.targetSelectionRange ?? item.targetRange);
      }
      if (typeof item.uri === "string" && item.range) {
        return resultFromLspRange(project.wsRoot, item.uri, item.range);
      }
      return null;
    })
    .filter((item): item is CodeIntelligenceResult => item !== null);
}

function normalizeLspSymbols(
  project: ExternalLspProject,
  result: unknown,
): CodeIntelligenceResult[] {
  if (!Array.isArray(result)) return [];
  const out: CodeIntelligenceResult[] = [];

  for (const value of result) {
    const maybeInfo = value as Partial<LspSymbolInformation>;
    if (maybeInfo.location && typeof maybeInfo.name === "string") {
      const item = resultFromLspRange(project.wsRoot, maybeInfo.location.uri, maybeInfo.location.range, {
        name: maybeInfo.name,
        kind: lspSymbolKind(Number(maybeInfo.kind)),
      });
      if (item) out.push(item);
    }
  }
  return out;
}

function workspaceEditResults(
  project: ExternalLspProject,
  edit: LspWorkspaceEdit | null,
  extra: Partial<CodeIntelligenceResult>,
): CodeIntelligenceResult[] {
  if (!edit) return [];
  const changes = lspTextEdits(edit);
  return changes
    .map((change) => resultFromLspRange(project.wsRoot, change.uri, change.edit.range, {
      ...extra,
      newText: change.edit.newText,
    }))
    .filter((item): item is CodeIntelligenceResult => item !== null);
}

function lspTextEdits(edit: LspWorkspaceEdit): Array<{ uri: string; edit: LspTextEdit }> {
  const out: Array<{ uri: string; edit: LspTextEdit }> = [];
  for (const [uri, edits] of Object.entries(edit.changes ?? {})) {
    for (const item of edits) out.push({ uri, edit: item });
  }
  for (const change of edit.documentChanges ?? []) {
    if (!("textDocument" in change) || !Array.isArray(change.edits)) continue;
    for (const item of change.edits) out.push({ uri: change.textDocument.uri, edit: item });
  }
  return out;
}

function lspCodeActionResult(
  project: ExternalLspProject,
  action: LspCodeAction,
  input: CodeIntelligenceInput,
): CodeIntelligenceResult | null {
  const edits = action.edit ? lspTextEdits(action.edit) : [];
  const first = edits[0] ?? null;
  const uri = first?.uri ?? pathToFileURL(resolveWorkspaceFile(project.wsRoot, input.file ?? "")).href;
  const position = lspPosition(input);
  const range = first?.edit.range ?? { start: position, end: position };
  const targetFiles = [
    ...new Set(
      edits
        .map((edit) => pathFromFileUri(edit.uri))
        .filter((file): file is string => file !== null && isInside(project.wsRoot, file))
        .map((file) => workspaceRelative(project.wsRoot, file)),
    ),
  ];
  return resultFromLspRange(project.wsRoot, uri, range, {
    name: action.kind,
    kind: "code_action",
    text: action.title,
    editCount: edits.length,
    targetFiles,
    ...(first ? { newText: first.edit.newText } : {}),
  });
}

function resultFromLspDiagnostic(
  project: ExternalLspProject,
  file: string,
  diagnostic: LspDiagnostic,
): CodeIntelligenceResult | null {
  const absFile = file ? resolveWorkspaceFile(project.wsRoot, file) : null;
  if (!absFile) return null;
  return resultFromLspRange(project.wsRoot, pathToFileURL(absFile).href, diagnostic.range, {
    severity: lspDiagnosticSeverity(diagnostic.severity),
    code: diagnostic.code === undefined ? undefined : String(diagnostic.code),
    text: diagnostic.message,
  });
}

function resultFromLspRange(
  wsRoot: string,
  uri: string,
  range: LspRange,
  extra: Partial<CodeIntelligenceResult> = {},
): CodeIntelligenceResult | null {
  const file = pathFromFileUri(uri);
  if (!file || !isInside(wsRoot, file)) return null;
  const content = ts.sys.readFile(file) ?? "";
  const startOffset = offsetFromLspPosition(content, range.start);
  const endOffset = offsetFromLspPosition(content, range.end);
  return {
    ...extra,
    file: workspaceRelative(wsRoot, file),
    line: range.start.line + 1,
    column: range.start.character + 1,
    endLine: range.end.line + 1,
    endColumn: range.end.character + 1,
    startOffset,
    length: Math.max(0, endOffset - startOffset),
    sourceText: content.slice(startOffset, endOffset),
    preview: linePreview(file, range.start.line + 1),
  };
}

function pathFromFileUri(uri: string): string | null {
  try {
    return fileURLToPath(uri);
  } catch {
    return null;
  }
}

function offsetFromLspPosition(content: string, position: LspPosition): number {
  const lines = content.split(/\n/);
  let offset = 0;
  for (let i = 0; i < Math.min(position.line, lines.length); i++) {
    offset += (lines[i] ?? "").length + 1;
  }
  const line = lines[position.line] ?? "";
  return offset + Math.min(position.character, line.replace(/\r$/, "").length);
}

function lspHoverText(contents: unknown): string {
  if (typeof contents === "string") return contents;
  if (Array.isArray(contents)) {
    return contents.map(lspHoverText).filter(Boolean).join("\n");
  }
  if (contents && typeof contents === "object") {
    const item = contents as { value?: unknown; language?: unknown };
    if (typeof item.value === "string") return item.value;
  }
  return "";
}

function lspDiagnosticSeverity(severity: number | undefined): CodeIntelligenceResult["severity"] {
  if (severity === 1) return "error";
  if (severity === 2) return "warning";
  if (severity === 3) return "message";
  if (severity === 4) return "suggestion";
  return "message";
}

function lspSymbolKind(kind: number): string {
  const names = [
    "",
    "File",
    "Module",
    "Namespace",
    "Package",
    "Class",
    "Method",
    "Property",
    "Field",
    "Constructor",
    "Enum",
    "Interface",
    "Function",
    "Variable",
    "Constant",
    "String",
    "Number",
    "Boolean",
    "Array",
    "Object",
    "Key",
    "Null",
    "EnumMember",
    "Struct",
    "Event",
    "Operator",
    "TypeParameter",
  ];
  return names[kind] ?? `SymbolKind${kind}`;
}

function loadTypeScriptProject(ws: Workspace, projectPath: string): TypeScriptProject {
  const projectRoot = ws.resolve(projectPath);
  const tsconfigPath = path.join(projectRoot, "tsconfig.json");
  const rawConfig = ts.readConfigFile(tsconfigPath, ts.sys.readFile);
  if (rawConfig.error) {
    throw new Error(
      `No readable tsconfig.json found at ${workspaceRelative(ws.root, tsconfigPath)}`,
    );
  }
  const parsed = ts.parseJsonConfigFileContent(
    rawConfig.config,
    ts.sys,
    projectRoot,
    undefined,
    tsconfigPath,
  );
  if (parsed.errors.length > 0) {
    const first = parsed.errors[0];
    throw new Error(
      first ? ts.flattenDiagnosticMessageText(first.messageText, "\n") : "tsconfig parse failed",
    );
  }

  const sourceFiles = parsed.fileNames.filter((file) => isInside(ws.root, file));
  const defaultLibDir = path.dirname(ts.getDefaultLibFilePath(parsed.options));
  const host: ts.LanguageServiceHost = {
    getCompilationSettings: () => parsed.options,
    getCurrentDirectory: () => projectRoot,
    getDefaultLibFileName: (options) => ts.getDefaultLibFilePath(options),
    getScriptFileNames: () => sourceFiles,
    getScriptVersion: () => "0",
    getScriptSnapshot(fileName) {
      if (!canReadForLanguageService(ws.root, defaultLibDir, fileName)) {
        return undefined;
      }
      const content = ts.sys.readFile(fileName);
      return content === undefined ? undefined : ts.ScriptSnapshot.fromString(content);
    },
    fileExists(fileName) {
      return (
        canReadForLanguageService(ws.root, defaultLibDir, fileName) &&
        ts.sys.fileExists(fileName)
      );
    },
    readFile(fileName) {
      return canReadForLanguageService(ws.root, defaultLibDir, fileName)
        ? ts.sys.readFile(fileName)
        : undefined;
    },
    readDirectory(rootDir, extensions, excludes, includes, depth) {
      if (!isInside(ws.root, rootDir)) return [];
      return ts.sys.readDirectory(rootDir, extensions, excludes, includes, depth);
    },
    directoryExists(dirName) {
      return (
        canReadForLanguageService(ws.root, defaultLibDir, dirName) &&
        (ts.sys.directoryExists?.(dirName) ?? false)
      );
    },
    getDirectories(dirName) {
      return canReadForLanguageService(ws.root, defaultLibDir, dirName)
        ? (ts.sys.getDirectories?.(dirName) ?? [])
        : [];
    },
  };

  return {
    service: ts.createLanguageService(host, ts.createDocumentRegistry()),
    projectRoot,
    projectPath: workspaceRelative(ws.root, projectRoot),
    wsRoot: ws.root,
  };
}

function runAction(
  project: TypeScriptProject,
  input: CodeIntelligenceInput,
): CodeIntelligenceResult[] {
  switch (input.action) {
    case "definition":
      return definitionAtPosition(project, input);
    case "references":
      return referencesAtPosition(project, input);
    case "hover":
      return hoverAtPosition(project, input);
    case "rename":
      return renameLocationsAtPosition(project, input);
    case "code_actions":
      return codeActionsAtPosition(project, input);
    case "document_symbols":
      return documentSymbols(project, input.file ?? "");
    case "workspace_symbols":
      return workspaceSymbols(project, input.query ?? "");
    case "diagnostics":
      return diagnostics(project);
  }
}

function sourcePosition(
  project: TypeScriptProject,
  input: CodeIntelligenceInput,
): { file: string; position: number } {
  const file = resolveWorkspaceFile(project.wsRoot, input.file ?? "");
  const program = project.service.getProgram();
  const source = program?.getSourceFile(file);
  if (!source) {
    throw new Error(`File is not part of the TypeScript project: ${input.file ?? ""}`);
  }
  const line = Number(input.line);
  const column = Number(input.column);
  if (line < 1 || line > source.getLineStarts().length) {
    throw new Error(`Line ${line} is outside ${input.file ?? ""}`);
  }
  return {
    file,
    position: source.getPositionOfLineAndCharacter(line - 1, column - 1),
  };
}

function definitionAtPosition(
  project: TypeScriptProject,
  input: CodeIntelligenceInput,
): CodeIntelligenceResult[] {
  const { file, position } = sourcePosition(project, input);
  return (project.service.getDefinitionAtPosition(file, position) ?? [])
    .map((info) => resultFromSpan(project, info.fileName, info.textSpan, {
      name: info.name,
      kind: info.kind,
    }))
    .filter((item): item is CodeIntelligenceResult => item !== null);
}

function referencesAtPosition(
  project: TypeScriptProject,
  input: CodeIntelligenceInput,
): CodeIntelligenceResult[] {
  const { file, position } = sourcePosition(project, input);
  const references = project.service.findReferences(file, position) ?? [];
  return references
    .flatMap((group) => group.references)
    .map((ref) => resultFromSpan(project, ref.fileName, ref.textSpan))
    .filter((item): item is CodeIntelligenceResult => item !== null);
}

function hoverAtPosition(
  project: TypeScriptProject,
  input: CodeIntelligenceInput,
): CodeIntelligenceResult[] {
  const { file, position } = sourcePosition(project, input);
  const info = project.service.getQuickInfoAtPosition(file, position);
  if (!info) return [];
  const text = [
    ts.displayPartsToString(info.displayParts ?? []),
    ts.displayPartsToString(info.documentation ?? []),
  ]
    .filter((part) => part.trim().length > 0)
    .join("\n");
  const result = resultFromSpan(project, file, info.textSpan, { text });
  return result ? [result] : [];
}

function renameLocationsAtPosition(
  project: TypeScriptProject,
  input: CodeIntelligenceInput,
): CodeIntelligenceResult[] {
  const { file, position } = sourcePosition(project, input);
  const newName = input.newName?.trim() ?? "";
  return (project.service.findRenameLocations(file, position, false, false, true) ?? [])
    .map((location) => resultFromSpan(project, location.fileName, location.textSpan, {
      kind: "rename",
      text: newName,
      newText: `${location.prefixText ?? ""}${newName}${location.suffixText ?? ""}`,
    }))
    .filter((item): item is CodeIntelligenceResult => item !== null);
}

function codeActionsAtPosition(
  project: TypeScriptProject,
  input: CodeIntelligenceInput,
): CodeIntelligenceResult[] {
  const { file, position } = sourcePosition(project, input);
  const diagnosticsAtPoint = diagnosticsForFile(project, file).filter((diagnostic) => {
    if (diagnostic.start === undefined) return false;
    const end = diagnostic.start + (diagnostic.length ?? 0);
    return position >= diagnostic.start && position <= Math.max(diagnostic.start, end);
  });
  const seen = new Set<string>();
  const results: CodeIntelligenceResult[] = [];

  for (const diagnostic of diagnosticsAtPoint) {
    if (diagnostic.start === undefined) continue;
    const fixes = project.service.getCodeFixesAtPosition(
      file,
      diagnostic.start,
      diagnostic.start + (diagnostic.length ?? 0),
      [diagnostic.code],
      {},
      {},
    );
    for (const fix of fixes) {
      const changes = workspaceTextChanges(project, fix);
      const change = changes[0] ?? null;
      const fileName = change?.fileName ?? file;
      const textSpan = change?.textChange.span ?? {
        start: diagnostic.start,
        length: diagnostic.length ?? 0,
      };
      const key = `${fix.fixName}\0${fileName}\0${textSpan.start}\0${fix.description}`;
      if (seen.has(key)) continue;
      seen.add(key);
      const result = resultFromSpan(project, fileName, textSpan, {
        name: fix.fixName,
        kind: "code_action",
        text: fix.description,
        ...(change ? { newText: change.textChange.newText } : {}),
        editCount: changes.length,
        targetFiles: [
          ...new Set(
            changes.map((item) => workspaceRelative(project.wsRoot, item.fileName)),
          ),
        ],
      });
      if (result) results.push(result);
    }
  }

  return results;
}

function diagnosticsForFile(project: TypeScriptProject, file: string): ts.Diagnostic[] {
  const program = project.service.getProgram();
  const source = program?.getSourceFile(file);
  if (!source) return [];
  return [
    ...project.service.getSyntacticDiagnostics(file),
    ...project.service.getSemanticDiagnostics(file),
    ...project.service.getSuggestionDiagnostics(file),
  ];
}

function workspaceTextChanges(
  project: TypeScriptProject,
  fix: ts.CodeFixAction,
): Array<{ fileName: string; textChange: ts.TextChange }> {
  const changes: Array<{ fileName: string; textChange: ts.TextChange }> = [];
  for (const change of fix.changes) {
    if (!isInside(project.wsRoot, change.fileName)) continue;
    for (const textChange of change.textChanges) {
      changes.push({ fileName: change.fileName, textChange });
    }
  }
  return changes;
}

function documentSymbols(project: TypeScriptProject, file: string): CodeIntelligenceResult[] {
  const absFile = resolveWorkspaceFile(project.wsRoot, file);
  const tree = project.service.getNavigationTree(absFile);
  const results: CodeIntelligenceResult[] = [];
  const visit = (item: ts.NavigationTree): void => {
    if (item.kind !== "script") {
      const result = resultFromSpan(project, absFile, item.spans[0], {
        name: item.text,
        kind: item.kind,
      });
      if (result) results.push(result);
    }
    for (const child of item.childItems ?? []) visit(child);
  };
  visit(tree);
  return results;
}

function workspaceSymbols(project: TypeScriptProject, query: string): CodeIntelligenceResult[] {
  return project.service
    .getNavigateToItems(query, 200, undefined, false, true)
    .map((item) => resultFromSpan(project, item.fileName, item.textSpan, {
      name: item.name,
      kind: item.kind,
    }))
    .filter((item): item is CodeIntelligenceResult => item !== null);
}

function diagnostics(project: TypeScriptProject): CodeIntelligenceResult[] {
  const program = project.service.getProgram();
  if (!program) return [];
  return ts
    .getPreEmitDiagnostics(program)
    .map((diagnostic) => resultFromDiagnostic(project, diagnostic))
    .filter((item): item is CodeIntelligenceResult => item !== null);
}

function resultFromDiagnostic(
  project: TypeScriptProject,
  diagnostic: ts.Diagnostic,
): CodeIntelligenceResult | null {
  if (!diagnostic.file || diagnostic.start === undefined) return null;
  if (!isInside(project.wsRoot, diagnostic.file.fileName)) return null;
  const pos = diagnostic.file.getLineAndCharacterOfPosition(diagnostic.start);
  const message = ts.flattenDiagnosticMessageText(diagnostic.messageText, "\n");
  return {
    file: workspaceRelative(project.wsRoot, diagnostic.file.fileName),
    line: pos.line + 1,
    column: pos.character + 1,
    severity: diagnosticCategory(diagnostic.category),
    code: `TS${diagnostic.code}`,
    text: message,
    preview: linePreview(diagnostic.file.fileName, pos.line + 1),
  };
}

function resultFromSpan(
  project: TypeScriptProject,
  fileName: string,
  textSpan: ts.TextSpan | undefined,
  extra: Partial<CodeIntelligenceResult> = {},
): CodeIntelligenceResult | null {
  if (!textSpan || !isInside(project.wsRoot, fileName)) return null;
  const program = project.service.getProgram();
  const source = program?.getSourceFile(fileName);
  if (!source) return null;
  const start = source.getLineAndCharacterOfPosition(textSpan.start);
  const end = source.getLineAndCharacterOfPosition(textSpan.start + textSpan.length);
  return {
    ...extra,
    file: workspaceRelative(project.wsRoot, fileName),
    line: start.line + 1,
    column: start.character + 1,
    endLine: end.line + 1,
    endColumn: end.character + 1,
    startOffset: textSpan.start,
    length: textSpan.length,
    sourceText: source.text.slice(textSpan.start, textSpan.start + textSpan.length),
    preview: linePreview(fileName, start.line + 1),
  };
}

function diagnosticCategory(category: ts.DiagnosticCategory): CodeIntelligenceResult["severity"] {
  if (category === ts.DiagnosticCategory.Error) return "error";
  if (category === ts.DiagnosticCategory.Warning) return "warning";
  if (category === ts.DiagnosticCategory.Suggestion) return "suggestion";
  return "message";
}

function linePreview(fileName: string, line: number): string {
  const content = ts.sys.readFile(fileName);
  return content?.split(/\r?\n/)[line - 1]?.trim() ?? "";
}

function resolveWorkspaceFile(wsRoot: string, file: string): string {
  const full = path.resolve(wsRoot, file);
  if (!isInside(wsRoot, full)) {
    throw new Error(`Path outside workspace: ${file}`);
  }
  return full;
}

function canReadForLanguageService(
  wsRoot: string,
  defaultLibDir: string,
  fileName: string,
): boolean {
  return isInside(wsRoot, fileName) || isInside(defaultLibDir, fileName);
}

function isInside(root: string, target: string): boolean {
  const rel = path.relative(path.resolve(root), path.resolve(target));
  return rel === "" || (!rel.startsWith("..") && !path.isAbsolute(rel));
}

function workspaceRelative(root: string, target: string): string {
  const rel = path.relative(path.resolve(root), path.resolve(target));
  return rel.length === 0 ? "." : rel.split(path.sep).join("/");
}
