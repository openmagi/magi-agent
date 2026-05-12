import fs from "node:fs/promises";
import { execFile } from "node:child_process";
import { createRequire } from "node:module";
import path from "node:path";
import { promisify } from "node:util";
import type { Tool, ToolContext, ToolResult } from "../Tool.js";
import { Workspace } from "../storage/Workspace.js";
import type * as ts from "typescript";

const execFileAsync = promisify(execFile);
const requireFromHere = createRequire(__filename);

export interface CodeDiagnosticsInput {
  action?: "diagnostics" | "references" | "rename" | "codeActions" | "workspaceSymbols";
  projectPath?: string;
  maxDiagnostics?: number;
  file?: string;
  line?: number;
  column?: number;
  newName?: string;
  query?: string;
  maxResults?: number;
}

export interface CodeDiagnostic {
  file: string;
  line: number;
  column: number;
  severity: "error" | "warning";
  code: string;
  message: string;
}

export interface CodeLocation {
  file: string;
  line: number;
  column: number;
  endLine: number;
  endColumn: number;
  preview: string;
}

export interface CodeTextChange extends CodeLocation {
  oldText: string;
  newText: string;
}

export interface TypeScriptProjectOutputBase {
  cwd: string;
  checker: "typescript";
}

export interface CodeDiagnosticsReportOutput extends TypeScriptProjectOutputBase {
  action: "diagnostics";
  passed: boolean;
  exitCode: number;
  diagnosticCount: number;
  diagnostics: CodeDiagnostic[];
  raw: string;
  truncated: boolean;
}

export interface CodeReferencesOutput extends TypeScriptProjectOutputBase {
  action: "references";
  file: string;
  line: number;
  column: number;
  referenceCount: number;
  references: CodeLocation[];
}

export interface CodeRenameOutput extends TypeScriptProjectOutputBase {
  action: "rename";
  file: string;
  line: number;
  column: number;
  canRename: boolean;
  displayName?: string;
  fullDisplayName?: string;
  kind?: string;
  triggerSpan?: CodeLocation;
  localizedErrorMessage?: string;
  locationCount: number;
  locations: CodeLocation[];
  changes: CodeTextChange[];
}

export interface CodeActionChange {
  file: string;
  line: number;
  column: number;
  endLine: number;
  endColumn: number;
  newText: string;
}

export interface CodeActionSummary {
  description: string;
  fixName: string;
  fixId?: string;
  changes: CodeActionChange[];
}

export interface CodeActionsOutput extends TypeScriptProjectOutputBase {
  action: "codeActions";
  file: string;
  line?: number;
  column?: number;
  diagnosticCount: number;
  diagnostics: CodeDiagnostic[];
  codeActionCount: number;
  actions: CodeActionSummary[];
}

export interface CodeWorkspaceSymbol {
  name: string;
  kind: string;
  file: string;
  line: number;
  column: number;
  containerName?: string;
}

export interface CodeWorkspaceSymbolsOutput extends TypeScriptProjectOutputBase {
  action: "workspaceSymbols";
  query: string;
  symbolCount: number;
  symbols: CodeWorkspaceSymbol[];
}

export type CodeDiagnosticsOutput =
  | CodeDiagnosticsReportOutput
  | CodeReferencesOutput
  | CodeRenameOutput
  | CodeActionsOutput
  | CodeWorkspaceSymbolsOutput;

const INPUT_SCHEMA = {
  type: "object",
  properties: {
    action: {
      type: "string",
      enum: ["diagnostics", "references", "rename", "codeActions", "workspaceSymbols"],
      description:
        "Operation to run. Default diagnostics. Language-service actions require a TypeScript project.",
    },
    projectPath: {
      type: "string",
      description: "Workspace-relative project directory. Default: workspace root.",
    },
    maxDiagnostics: {
      type: "integer",
      minimum: 1,
      maximum: 200,
      description: "Maximum diagnostics to return. Default: 50.",
    },
    file: {
      type: "string",
      description:
        "Project-relative or workspace-relative file for references, rename, and codeActions.",
    },
    line: {
      type: "integer",
      minimum: 1,
      description: "1-based line number for position-based language-service actions.",
    },
    column: {
      type: "integer",
      minimum: 1,
      description: "1-based column number for position-based language-service actions.",
    },
    newName: {
      type: "string",
      description: "Optional replacement name for rename preview.",
    },
    query: {
      type: "string",
      description: "Workspace symbol query. Empty string returns broad symbol matches.",
    },
    maxResults: {
      type: "integer",
      minimum: 1,
      maximum: 500,
      description: "Maximum language-service results to return. Default: 100.",
    },
  },
} as const;

export function makeCodeDiagnosticsTool(
  workspaceRoot: string,
): Tool<CodeDiagnosticsInput, CodeDiagnosticsOutput> {
  const workspace = new Workspace(workspaceRoot);
  return {
    name: "CodeDiagnostics",
    description:
      "Run deterministic TypeScript project diagnostics and language-service navigation. Supports diagnostics, references, rename preview, code actions, and workspace symbols.",
    inputSchema: INPUT_SCHEMA,
    permission: "execute",
    kind: "core",
    mutatesWorkspace: false,
    validate(input) {
      if (!input) return null;
      const action = input.action ?? "diagnostics";
      if (!["diagnostics", "references", "rename", "codeActions", "workspaceSymbols"].includes(action)) {
        return "`action` must be diagnostics, references, rename, codeActions, or workspaceSymbols";
      }
      if (
        input.projectPath !== undefined &&
        typeof input.projectPath !== "string"
      ) {
        return "`projectPath` must be a string";
      }
      if (
        input.maxDiagnostics !== undefined &&
        (!Number.isInteger(input.maxDiagnostics) ||
          input.maxDiagnostics < 1 ||
          input.maxDiagnostics > 200)
      ) {
        return "`maxDiagnostics` must be an integer in [1..200]";
      }
      if (
        input.maxResults !== undefined &&
        (!Number.isInteger(input.maxResults) ||
          input.maxResults < 1 ||
          input.maxResults > 500)
      ) {
        return "`maxResults` must be an integer in [1..500]";
      }
      if (input.file !== undefined && typeof input.file !== "string") {
        return "`file` must be a string";
      }
      if (
        input.line !== undefined &&
        (!Number.isInteger(input.line) || input.line < 1)
      ) {
        return "`line` must be a positive integer";
      }
      if (
        input.column !== undefined &&
        (!Number.isInteger(input.column) || input.column < 1)
      ) {
        return "`column` must be a positive integer";
      }
      if (input.newName !== undefined && typeof input.newName !== "string") {
        return "`newName` must be a string";
      }
      if (input.query !== undefined && typeof input.query !== "string") {
        return "`query` must be a string";
      }
      if (
        (action === "references" || action === "rename") &&
        (!input.file || !input.line || !input.column)
      ) {
        return "`file`, `line`, and `column` are required for references and rename";
      }
      if (action === "codeActions" && !input.file) {
        return "`file` is required for codeActions";
      }
      return null;
    },
    async execute(
      input: CodeDiagnosticsInput,
      _ctx: ToolContext,
    ): Promise<ToolResult<CodeDiagnosticsOutput>> {
      const start = Date.now();
      try {
        const cwd = workspace.resolve(input.projectPath ?? ".");
        const action = input.action ?? "diagnostics";
        const tsconfig = path.join(cwd, "tsconfig.json");
        try {
          await fs.access(tsconfig);
        } catch {
          return {
            status: "error",
            errorCode: "no_tsconfig",
            errorMessage: `No tsconfig.json found at ${relativeToWorkspace(workspaceRoot, tsconfig)}`,
            durationMs: Date.now() - start,
          };
        }

        if (action !== "diagnostics") {
          const language = await createTypeScriptLanguageService(workspaceRoot, cwd);
          const output = runLanguageServiceAction(language, input);
          return {
            status: "ok",
            output,
            metadata: {
              evidenceKind: "code_intelligence",
              checker: "typescript",
              action,
              ...(output.action === "references"
                ? { referenceCount: output.referenceCount }
                : {}),
              ...(output.action === "rename"
                ? { canRename: output.canRename, locationCount: output.locationCount }
                : {}),
              ...(output.action === "codeActions"
                ? { codeActionCount: output.codeActionCount }
                : {}),
              ...(output.action === "workspaceSymbols"
                ? { symbolCount: output.symbolCount }
                : {}),
            },
            durationMs: Date.now() - start,
          };
        }

        const tsc = await resolveTscBinary();
        const { exitCode, combined } = await runTsc(tsc, cwd);
        const maxDiagnostics = input.maxDiagnostics ?? 50;
        const diagnostics = parseTypeScriptDiagnostics(combined, cwd).slice(
          0,
          maxDiagnostics,
        );
        const output: CodeDiagnosticsOutput = {
          action: "diagnostics",
          cwd: relativeToWorkspace(workspaceRoot, cwd),
          checker: "typescript",
          passed: exitCode === 0,
          exitCode,
          diagnosticCount: diagnostics.length,
          diagnostics,
          raw: combined.slice(0, 64 * 1024),
          truncated: combined.length > 64 * 1024,
        };
        return {
          status: "ok",
          output,
          metadata: {
            evidenceKind: "diagnostics",
            checker: "typescript",
            passed: output.passed,
            diagnosticCount: output.diagnosticCount,
            diagnostics: output.diagnostics,
          },
          durationMs: Date.now() - start,
        };
      } catch (err) {
        return {
          status: "error",
          errorCode: "diagnostics_failed",
          errorMessage: err instanceof Error ? err.message : String(err),
          durationMs: Date.now() - start,
        };
      }
    },
  };
}

type TypeScriptModule = typeof ts;

interface TypeScriptLanguageServiceContext {
  ts: TypeScriptModule;
  workspaceRoot: string;
  cwd: string;
  service: ts.LanguageService;
}

async function createTypeScriptLanguageService(
  workspaceRoot: string,
  cwd: string,
): Promise<TypeScriptLanguageServiceContext> {
  const typescript = await loadTypeScript(cwd);
  const tsconfig = path.join(cwd, "tsconfig.json");
  const config = typescript.readConfigFile(tsconfig, typescript.sys.readFile);
  if (config.error) {
    throw new Error(flattenTsMessage(typescript, config.error.messageText));
  }
  const parsed = typescript.parseJsonConfigFileContent(
    config.config,
    typescript.sys,
    cwd,
  );
  if (parsed.errors.length > 0) {
    throw new Error(flattenTsMessage(typescript, parsed.errors[0]!.messageText));
  }

  const scriptFileNames = parsed.fileNames
    .map((file) => path.resolve(file))
    .filter((file) => !file.endsWith(".d.ts"));
  const host: ts.LanguageServiceHost = {
    getCompilationSettings: () => parsed.options,
    getScriptFileNames: () => scriptFileNames,
    getScriptVersion: () => "0",
    getScriptSnapshot: (fileName) => {
      if (!typescript.sys.fileExists(fileName)) return undefined;
      const content = typescript.sys.readFile(fileName);
      return content === undefined
        ? undefined
        : typescript.ScriptSnapshot.fromString(content);
    },
    getCurrentDirectory: () => cwd,
    getDefaultLibFileName: (options) => typescript.getDefaultLibFilePath(options),
    fileExists: typescript.sys.fileExists,
    readFile: typescript.sys.readFile,
    readDirectory: typescript.sys.readDirectory,
    directoryExists: typescript.sys.directoryExists,
    getDirectories: typescript.sys.getDirectories,
    realpath: typescript.sys.realpath,
  };

  return {
    ts: typescript,
    workspaceRoot,
    cwd,
    service: typescript.createLanguageService(host),
  };
}

async function loadTypeScript(cwd: string): Promise<TypeScriptModule> {
  const localRequire = createRequire(path.join(cwd, "package.json"));
  const loaders = [
    () => localRequire("typescript") as TypeScriptModule,
    () => requireFromHere("typescript") as TypeScriptModule,
  ];
  for (const load of loaders) {
    try {
      return load();
    } catch {
      /* try next */
    }
  }
  throw new Error(
    "TypeScript language service is unavailable; install typescript in the project or bundle it with core-agent",
  );
}

function runLanguageServiceAction(
  language: TypeScriptLanguageServiceContext,
  input: CodeDiagnosticsInput,
): Exclude<CodeDiagnosticsOutput, CodeDiagnosticsReportOutput> {
  switch (input.action) {
    case "references":
      return getReferences(language, input);
    case "rename":
      return getRenamePreview(language, input);
    case "codeActions":
      return getCodeActions(language, input);
    case "workspaceSymbols":
      return getWorkspaceSymbols(language, input);
    default:
      throw new Error(`unsupported language-service action: ${input.action}`);
  }
}

function getReferences(
  language: TypeScriptLanguageServiceContext,
  input: CodeDiagnosticsInput,
): CodeReferencesOutput {
  const file = resolveProjectFile(language, required(input.file, "file"));
  const sourceFile = getSourceFile(language, file);
  const line = required(input.line, "line");
  const column = required(input.column, "column");
  const position = positionFromLineColumn(sourceFile, line, column);
  const references = (language.service.getReferencesAtPosition(file, position) ?? [])
    .map((reference) => locationFromTextSpan(language, reference.fileName, reference.textSpan))
    .sort(compareLocations)
    .slice(0, input.maxResults ?? 100);
  return {
    cwd: relativeToWorkspace(language.workspaceRoot, language.cwd),
    checker: "typescript",
    action: "references",
    file: relativeToWorkspace(language.cwd, file),
    line,
    column,
    referenceCount: references.length,
    references,
  };
}

function getRenamePreview(
  language: TypeScriptLanguageServiceContext,
  input: CodeDiagnosticsInput,
): CodeRenameOutput {
  const file = resolveProjectFile(language, required(input.file, "file"));
  const sourceFile = getSourceFile(language, file);
  const line = required(input.line, "line");
  const column = required(input.column, "column");
  const position = positionFromLineColumn(sourceFile, line, column);
  const renameInfo = language.service.getRenameInfo(file, position, {});
  const locations = renameInfo.canRename
    ? (language.service.findRenameLocations(file, position, false, false, false) ?? [])
        .map((location) => locationFromTextSpan(language, location.fileName, location.textSpan))
        .sort(compareLocations)
        .slice(0, input.maxResults ?? 100)
    : [];
  return {
    cwd: relativeToWorkspace(language.workspaceRoot, language.cwd),
    checker: "typescript",
    action: "rename",
    file: relativeToWorkspace(language.cwd, file),
    line,
    column,
    canRename: renameInfo.canRename,
    ...(renameInfo.canRename
      ? {
          displayName: renameInfo.displayName,
          fullDisplayName: renameInfo.fullDisplayName,
          kind: renameInfo.kind,
          triggerSpan: renameInfo.triggerSpan
            ? locationFromTextSpan(language, file, renameInfo.triggerSpan)
            : undefined,
        }
      : { localizedErrorMessage: renameInfo.localizedErrorMessage }),
    locationCount: locations.length,
    locations,
    changes: input.newName
      ? locations.map((location) => ({
          ...location,
          oldText: textFromLocation(language, location),
          newText: input.newName!,
        }))
      : [],
  };
}

function getCodeActions(
  language: TypeScriptLanguageServiceContext,
  input: CodeDiagnosticsInput,
): CodeActionsOutput {
  const file = resolveProjectFile(language, required(input.file, "file"));
  const sourceFile = getSourceFile(language, file);
  const allDiagnostics = [
    ...language.service.getSyntacticDiagnostics(file),
    ...language.service.getSemanticDiagnostics(file),
    ...language.service.getSuggestionDiagnostics(file),
  ];
  const selectedDiagnostics = selectDiagnosticsAtPosition(
    sourceFile,
    allDiagnostics,
    input.line,
    input.column,
  );
  const codeFixes = new Map<string, CodeActionSummary>();
  for (const diagnostic of selectedDiagnostics) {
    const start = diagnostic.start ?? 0;
    const end = start + (diagnostic.length ?? 0);
    const fixes = language.service.getCodeFixesAtPosition(
      file,
      start,
      end,
      [diagnostic.code],
      {},
      {},
    );
    for (const fix of fixes) {
      const key = `${fix.fixName}:${fix.description}`;
      if (!codeFixes.has(key)) {
        codeFixes.set(key, {
          description: fix.description,
          fixName: fix.fixName,
          ...(typeof fix.fixId === "string" ? { fixId: fix.fixId } : {}),
          changes: fix.changes.flatMap((change) =>
            change.textChanges.map((textChange) => ({
              ...rangeFromTextSpan(language, change.fileName, textChange.span),
              newText: textChange.newText,
            })),
          ),
        });
      }
    }
  }

  const diagnostics = selectedDiagnostics
    .map((diagnostic) => diagnosticToCodeDiagnostic(language, file, diagnostic))
    .slice(0, input.maxDiagnostics ?? 50);
  const actions = [...codeFixes.values()].slice(0, input.maxResults ?? 100);
  return {
    cwd: relativeToWorkspace(language.workspaceRoot, language.cwd),
    checker: "typescript",
    action: "codeActions",
    file: relativeToWorkspace(language.cwd, file),
    ...(input.line ? { line: input.line } : {}),
    ...(input.column ? { column: input.column } : {}),
    diagnosticCount: diagnostics.length,
    diagnostics,
    codeActionCount: actions.length,
    actions,
  };
}

function getWorkspaceSymbols(
  language: TypeScriptLanguageServiceContext,
  input: CodeDiagnosticsInput,
): CodeWorkspaceSymbolsOutput {
  const query = input.query ?? "";
  const maxResults = input.maxResults ?? 100;
  const symbols = language.service
    .getNavigateToItems(query, maxResults, undefined, true, true)
    .filter((item) => Boolean(item.fileName))
    .map((item) => {
      const location = symbolLineColumn(language, item.fileName!, item.textSpan, item.name);
      return {
        name: item.name,
        kind: item.kind,
        file: relativeToWorkspace(language.cwd, item.fileName!),
        line: location.line,
        column: location.column,
        ...(item.containerName ? { containerName: item.containerName } : {}),
      };
    })
    .filter((item) => !item.file.startsWith(".."))
    .sort((a, b) =>
      `${a.file}:${a.line}:${a.column}:${a.name}`.localeCompare(
        `${b.file}:${b.line}:${b.column}:${b.name}`,
      ),
    )
    .slice(0, maxResults);
  return {
    cwd: relativeToWorkspace(language.workspaceRoot, language.cwd),
    checker: "typescript",
    action: "workspaceSymbols",
    query,
    symbolCount: symbols.length,
    symbols,
  };
}

async function resolveTscBinary(): Promise<string> {
  const candidates = [
    process.env.TSC_BIN,
    path.join(process.cwd(), "node_modules", ".bin", "tsc"),
    "tsc",
  ].filter((candidate): candidate is string => Boolean(candidate));
  for (const candidate of candidates) {
    if (candidate === "tsc") return candidate;
    try {
      await fs.access(candidate);
      return candidate;
    } catch {
      /* try next */
    }
  }
  return "tsc";
}

async function runTsc(
  tsc: string,
  cwd: string,
): Promise<{ exitCode: number; combined: string }> {
  try {
    const { stdout, stderr } = await execFileAsync(
      tsc,
      ["--noEmit", "--pretty", "false"],
      {
        cwd,
        timeout: 120_000,
        maxBuffer: 10 * 1024 * 1024,
      },
    );
    return { exitCode: 0, combined: `${stdout}${stderr}` };
  } catch (err) {
    const e = err as NodeJS.ErrnoException & {
      stdout?: string;
      stderr?: string;
      code?: number | string;
    };
    const code = typeof e.code === "number" ? e.code : 1;
    return { exitCode: code, combined: `${e.stdout ?? ""}${e.stderr ?? ""}` };
  }
}

function parseTypeScriptDiagnostics(raw: string, cwd: string): CodeDiagnostic[] {
  const diagnostics: CodeDiagnostic[] = [];
  const cwdResolved = path.resolve(cwd);
  const re = /^(.+?)\((\d+),(\d+)\):\s+(error|warning)\s+(TS\d+):\s+(.+)$/;
  for (const line of raw.split(/\r?\n/)) {
    const match = re.exec(line.trim());
    if (!match) continue;
    const file = path.resolve(cwdResolved, match[1]!);
    diagnostics.push({
      file: relativeToWorkspace(cwdResolved, file),
      line: Number.parseInt(match[2]!, 10),
      column: Number.parseInt(match[3]!, 10),
      severity: match[4] as "error" | "warning",
      code: match[5]!,
      message: match[6]!,
    });
  }
  return diagnostics;
}

function selectDiagnosticsAtPosition(
  sourceFile: ts.SourceFile,
  diagnostics: readonly ts.Diagnostic[],
  line?: number,
  column?: number,
): readonly ts.Diagnostic[] {
  if (!line || !column) return diagnostics;
  const position = positionFromLineColumn(sourceFile, line, column);
  const exact = diagnostics.filter((diagnostic) => {
    const start = diagnostic.start ?? 0;
    const end = start + (diagnostic.length ?? 0);
    return position >= start && position <= end;
  });
  if (exact.length > 0) return exact;
  return diagnostics.filter((diagnostic) => {
    const start = diagnostic.start ?? 0;
    const location = sourceFile.getLineAndCharacterOfPosition(start);
    return location.line + 1 === line;
  });
}

function diagnosticToCodeDiagnostic(
  language: TypeScriptLanguageServiceContext,
  fallbackFile: string,
  diagnostic: ts.Diagnostic,
): CodeDiagnostic {
  const file = diagnostic.file?.fileName ?? fallbackFile;
  const sourceFile = getSourceFile(language, file);
  const start = diagnostic.start ?? 0;
  const location = sourceFile.getLineAndCharacterOfPosition(start);
  return {
    file: relativeToWorkspace(language.cwd, file),
    line: location.line + 1,
    column: location.character + 1,
    severity:
      diagnostic.category === language.ts.DiagnosticCategory.Warning ? "warning" : "error",
    code: `TS${diagnostic.code}`,
    message: flattenTsMessage(language.ts, diagnostic.messageText),
  };
}

function resolveProjectFile(
  language: TypeScriptLanguageServiceContext,
  fileInput: string,
): string {
  const projectRelative = path.resolve(language.cwd, fileInput);
  if (isInside(language.cwd, projectRelative)) {
    if (language.ts.sys.fileExists(projectRelative)) return projectRelative;
  }
  const workspaceRelative = path.resolve(language.workspaceRoot, fileInput);
  if (
    isInside(language.workspaceRoot, workspaceRelative) &&
    language.ts.sys.fileExists(workspaceRelative)
  ) {
    return workspaceRelative;
  }
  if (isInside(language.cwd, projectRelative)) return projectRelative;
  throw new Error(`file escapes project: ${fileInput}`);
}

function getSourceFile(
  language: TypeScriptLanguageServiceContext,
  file: string,
): ts.SourceFile {
  const sourceFile = language.service.getProgram()?.getSourceFile(file);
  if (sourceFile) return sourceFile;
  const content = language.ts.sys.readFile(file);
  if (content === undefined) throw new Error(`source file not found: ${file}`);
  return language.ts.createSourceFile(
    file,
    content,
    language.ts.ScriptTarget.Latest,
    true,
  );
}

function positionFromLineColumn(
  sourceFile: ts.SourceFile,
  line: number,
  column: number,
): number {
  return sourceFile.getPositionOfLineAndCharacter(line - 1, column - 1);
}

function locationFromTextSpan(
  language: TypeScriptLanguageServiceContext,
  file: string,
  textSpan: ts.TextSpan,
): CodeLocation {
  const range = rangeFromTextSpan(language, file, textSpan);
  return {
    ...range,
    preview: previewLine(language, file, textSpan.start),
  };
}

function rangeFromTextSpan(
  language: TypeScriptLanguageServiceContext,
  file: string,
  textSpan: ts.TextSpan,
): Omit<CodeLocation, "preview"> {
  const sourceFile = getSourceFile(language, file);
  const start = sourceFile.getLineAndCharacterOfPosition(textSpan.start);
  const end = sourceFile.getLineAndCharacterOfPosition(
    textSpan.start + textSpan.length,
  );
  return {
    file: relativeToWorkspace(language.cwd, file),
    line: start.line + 1,
    column: start.character + 1,
    endLine: end.line + 1,
    endColumn: end.character + 1,
  };
}

function symbolLineColumn(
  language: TypeScriptLanguageServiceContext,
  file: string,
  textSpan: ts.TextSpan,
  name: string,
): { line: number; column: number } {
  const sourceFile = getSourceFile(language, file);
  const start = sourceFile.getLineAndCharacterOfPosition(textSpan.start);
  const lineText = sourceFile.text.split(/\r?\n/)[start.line] ?? "";
  const nameOffset = lineText.indexOf(name, start.character);
  return {
    line: start.line + 1,
    column: (nameOffset >= 0 ? nameOffset : start.character) + 1,
  };
}

function previewLine(
  language: TypeScriptLanguageServiceContext,
  file: string,
  position: number,
): string {
  const sourceFile = getSourceFile(language, file);
  const location = sourceFile.getLineAndCharacterOfPosition(position);
  return sourceFile.text.split(/\r?\n/)[location.line]?.trim() ?? "";
}

function textFromLocation(
  language: TypeScriptLanguageServiceContext,
  location: CodeLocation,
): string {
  const file = path.resolve(language.cwd, location.file);
  const sourceFile = getSourceFile(language, file);
  const start = positionFromLineColumn(sourceFile, location.line, location.column);
  const end = positionFromLineColumn(sourceFile, location.endLine, location.endColumn);
  return sourceFile.text.slice(start, end);
}

function compareLocations(a: CodeLocation, b: CodeLocation): number {
  return `${a.file}:${a.line}:${a.column}`.localeCompare(`${b.file}:${b.line}:${b.column}`);
}

function flattenTsMessage(
  typescript: TypeScriptModule,
  message: string | ts.DiagnosticMessageChain,
): string {
  return typescript.flattenDiagnosticMessageText(message, "\n");
}

function required<T>(value: T | undefined, name: string): T {
  if (value === undefined) throw new Error(`${name} is required`);
  return value;
}

function isInside(root: string, target: string): boolean {
  const relative = path.relative(path.resolve(root), path.resolve(target));
  return relative === "" || (!relative.startsWith("..") && !path.isAbsolute(relative));
}

function relativeToWorkspace(root: string, target: string): string {
  const rel = path.relative(path.resolve(root), path.resolve(target));
  return rel.length === 0 ? "." : rel.split(path.sep).join("/");
}
