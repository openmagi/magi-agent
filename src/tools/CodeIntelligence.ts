import path from "node:path";
import ts from "typescript";
import type { Tool, ToolContext, ToolResult } from "../Tool.js";
import { Workspace } from "../storage/Workspace.js";
import { errorResult } from "../util/toolResult.js";

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
  language: "typescript";
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
        "Semantic TypeScript operation to run: definition, references, hover, rename, code_actions, document_symbols, workspace_symbols, or diagnostics.",
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
      "Run read-only semantic TypeScript code intelligence for coding work: go to definition, find references, rename locations, code actions, hover/type info, document symbols, workspace symbols, and compiler diagnostics. Prefer this over broad text search when navigating existing TypeScript code.",
    inputSchema: INPUT_SCHEMA,
    permission: "read",
    kind: "core",
    mutatesWorkspace: false,
    isConcurrencySafe: true,
    validate(input) {
      if (!input || !ACTIONS.has(input.action)) {
        return "`action` must be one of: definition, references, hover, rename, code_actions, document_symbols, workspace_symbols, diagnostics";
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

function requiresPosition(action: CodeIntelligenceAction): boolean {
  return (
    action === "definition" ||
    action === "references" ||
    action === "hover" ||
    action === "rename" ||
    action === "code_actions"
  );
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
