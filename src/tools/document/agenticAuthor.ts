import { execFile as execFileCb } from "node:child_process";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { promisify } from "node:util";
import type { ToolContext } from "../../Tool.js";
import type {
  LLMClient,
  LLMContentBlock,
  LLMMessage,
  LLMToolDef,
} from "../../transport/LLMClient.js";
import { StubSseWriter } from "../../transport/SseWriter.js";
import { readOne } from "../../turn/LLMStreamReader.js";
import { inspectDocx, validateDocxMarkdownRender } from "./docxQuality.js";
import { HWPX_RUNTIME_ROOT, type HwpxTemplate } from "./hwpxDriver.js";

const execFile = promisify(execFileCb);

export type AgenticDocumentFormat = "docx" | "hwpx";

export interface AgenticDocumentWriterInput {
  format: AgenticDocumentFormat;
  mode: "create" | "edit";
  title: string;
  filename: string;
  absPath: string;
  workspaceRoot: string;
  sourceMarkdown: string;
  template?: HwpxTemplate;
  referencePath?: string;
  ctx: ToolContext;
}

export interface AgenticDocumentWriteResult {
  mode: "agentic";
  turns: number;
  toolCallCount: number;
  model?: string;
}

export type AgenticDocumentWriter = (
  input: AgenticDocumentWriterInput,
) => Promise<AgenticDocumentWriteResult>;

export interface AgenticDocumentAuthorDeps {
  llm: LLMClient;
  resolveModel?: () => Promise<string>;
  fallbackModel?: string;
  maxTurns?: number;
  nodePath?: string;
}

const AGENTIC_TOOLS: LLMToolDef[] = [
  {
    name: "write_file",
    description: "Write UTF-8 content to a file in the isolated document authoring directory.",
    input_schema: {
      type: "object",
      properties: {
        filename: { type: "string" },
        content: { type: "string" },
      },
      required: ["filename", "content"],
      additionalProperties: false,
    },
  },
  {
    name: "read_file",
    description: "Read a UTF-8 file from the isolated document authoring directory.",
    input_schema: {
      type: "object",
      properties: {
        filename: { type: "string" },
      },
      required: ["filename"],
      additionalProperties: false,
    },
  },
  {
    name: "run_command",
    description:
      "Run an allowlisted command in the isolated document authoring directory. Allowed commands: node, python3, ls, file, wc, head, cat. No shell.",
    input_schema: {
      type: "object",
      properties: {
        command: { type: "string" },
        args: { type: "array", items: { type: "string" } },
      },
      required: ["command", "args"],
      additionalProperties: false,
    },
  },
];

function resolveJobPath(jobDir: string, filename: string): string {
  const normalized = path.normalize(filename).replace(/^\/+/, "");
  const resolved = path.resolve(jobDir, normalized);
  const root = path.resolve(jobDir);
  if (!resolved.startsWith(root + path.sep) && resolved !== root) {
    throw new Error(`path escapes document authoring directory: ${filename}`);
  }
  return resolved;
}

function assertSafeArg(jobDir: string, arg: string): void {
  if (arg.includes("\0")) {
    throw new Error("command argument contains NUL byte");
  }
  if (!path.isAbsolute(arg)) {
    const normalized = path.normalize(arg);
    if (normalized === ".." || normalized.startsWith(`..${path.sep}`)) {
      throw new Error(`relative command argument escapes job directory: ${arg}`);
    }
    return;
  }

  const resolved = path.resolve(arg);
  const jobRoot = path.resolve(jobDir);
  const hwpxRoot = path.resolve(HWPX_RUNTIME_ROOT);
  if (
    resolved === jobRoot ||
    resolved.startsWith(jobRoot + path.sep) ||
    resolved === hwpxRoot ||
    resolved.startsWith(hwpxRoot + path.sep)
  ) {
    return;
  }
  throw new Error(`absolute command argument is outside allowed roots: ${arg}`);
}

async function runCommand(
  jobDir: string,
  nodePath: string,
  command: string,
  args: string[],
  signal: AbortSignal,
): Promise<string> {
  const allowed = new Set(["node", "python3", "ls", "file", "wc", "head", "cat"]);
  if (!allowed.has(command)) {
    throw new Error(`command not allowed: ${command}`);
  }
  if (!Array.isArray(args)) {
    throw new Error("run_command.args must be an array");
  }
  if ((command === "python3" || command === "node") && args.some((arg) => arg === "-c" || arg === "-e" || arg === "-m")) {
    throw new Error(`${command} inline execution flags are not allowed`);
  }
  for (const arg of args) {
    assertSafeArg(jobDir, arg);
  }

  const { stdout, stderr } = await execFile(command, args, {
    cwd: jobDir,
    timeout: 90_000,
    maxBuffer: 256 * 1024,
    signal,
    env: {
      ...process.env,
      NODE_PATH: nodePath,
      HWPX_RUNTIME_ROOT,
      HOME: os.tmpdir(),
    },
  });

  const output = [stdout, stderr].filter((part) => part.trim().length > 0).join("\n");
  return output.slice(0, 12_000) || "(no output)";
}

function systemPromptFor(
  format: AgenticDocumentFormat,
  outputName: string,
  template?: HwpxTemplate,
  hasReference = false,
): string {
  if (format === "hwpx") {
    if (hasReference) {
      return `You are an expert HWPX (Korean Hancom Office) document author.

Create a polished HWPX edit from the provided source content while preserving the reference HWPX layout. This is an agentic writing loop: analyze, write XML, build, inspect errors, and retry until the output file exists and page drift is guarded.

Reference-first workflow:
- A reference file is available in the working directory as reference.hwpx.
- FIRST analyze and extract the reference XML:
  python3 ${path.join(HWPX_RUNTIME_ROOT, "scripts", "analyze_template.py")} reference.hwpx --extract-header ref_header.xml --extract-section ref_section.xml
- Preserve the reference structure as much as possible: header styles, charPrIDRef, paraPrIDRef, borderFillIDRef, tables, cell spans, margins, paragraph order, and section settings.
- Write section0.xml by editing the extracted ref_section.xml structure. Change only the user-requested content.
- Build with the extracted header:
  python3 ${path.join(HWPX_RUNTIME_ROOT, "scripts", "build_hwpx.py")} --header ref_header.xml --section section0.xml --output ${outputName}
- Validate:
  python3 ${path.join(HWPX_RUNTIME_ROOT, "scripts", "validate.py")} ${outputName}
- Run the page drift guard:
  python3 ${path.join(HWPX_RUNTIME_ROOT, "scripts", "page_guard.py")} --reference reference.hwpx --output ${outputName}
- If build, validation, source coverage, or page_guard.py fails, read the error and fix section0.xml.
- The runtime independently re-runs validate.py, source content coverage checks, and page_guard.py after your tool calls. Placeholder files, fake ZIP headers, template-only packages, or unvalidated packages will be rejected.
- Never stop after describing the plan. The final output file must exist and page_guard.py must pass.`;
    }

    const effectiveTemplate = template ?? "report";
    const templateArgs = effectiveTemplate === "base" ? "" : `--template ${effectiveTemplate} `;

    return `You are an expert HWPX (Korean Hancom Office) document author.

Create a polished HWPX document from the provided source content. This is an agentic writing loop: write files, build, inspect errors, and retry until the output file exists.

Rules:
- Read source.md and starter_section0.xml before writing the final section.
- FIRST write a complete section0.xml with all source content represented. Use starter_section0.xml as the skeleton so section/page settings stay valid.
- Preserve headings, bullets, tables, Korean text, and document hierarchy.
- Use the bundled HWPX builder. Preferred command:
  python3 ${path.join(HWPX_RUNTIME_ROOT, "scripts", "build_hwpx.py")} ${templateArgs}--section section0.xml --output ${outputName}
- Then validate:
  python3 ${path.join(HWPX_RUNTIME_ROOT, "scripts", "validate.py")} ${outputName}
- If build or validation fails, read the error and fix section0.xml.
- The runtime independently re-runs validate.py and source content coverage checks after your tool calls. Placeholder files, fake ZIP headers, template-only packages, or unvalidated packages will be rejected.
- Never stop after describing the plan. The final output file must exist.`;
  }

  return `You are an expert DOCX document author.

Create a polished editable DOCX from the provided source content. This is an agentic writing loop: write code, run it, inspect errors, and retry until the output file exists.

Rules:
- FIRST write a complete Node.js CommonJS script, for example build-docx.cjs.
- The script must use the installed docx npm package and save ${outputName}.
- Use rich Word structures when useful: headings, paragraph spacing, tables, bullet lists, bold text, and Korean/CJK font settings.
- Use font "Noto Sans CJK KR" or "맑은 고딕" for Korean text.
- Run the script with: node build-docx.cjs
- If it fails, read the error and fix the script.
- Never stop after describing the plan. The final output file must exist.`;
}

function userPrompt(input: AgenticDocumentWriterInput, outputName: string): string {
  return [
    `Mode: ${input.mode}`,
    `Title: ${input.title}`,
    `Requested filename: ${input.filename}`,
    `Required output file in the working directory: ${outputName}`,
    ...(input.referencePath ? ["Reference HWPX file available: reference.hwpx"] : []),
    "",
    "Source content:",
    "```markdown",
    input.sourceMarkdown,
    "```",
    "",
    "Start now. Use tool calls to create the output file, verify it exists, and then stop.",
  ].join("\n");
}

async function executeToolUse(
  jobDir: string,
  nodePath: string,
  toolUse: Extract<LLMContentBlock, { type: "tool_use" }>,
  signal: AbortSignal,
): Promise<{ tool_use_id: string; content: string; is_error?: boolean }> {
  try {
    const input = toolUse.input && typeof toolUse.input === "object"
      ? toolUse.input as Record<string, unknown>
      : {};
    if (toolUse.name === "write_file") {
      const filename = typeof input.filename === "string" ? input.filename : "";
      const content = typeof input.content === "string" ? input.content : "";
      if (!filename) throw new Error("write_file.filename is required");
      const resolved = resolveJobPath(jobDir, filename);
      await fs.mkdir(path.dirname(resolved), { recursive: true });
      await fs.writeFile(resolved, content, "utf8");
      return {
        tool_use_id: toolUse.id,
        content: `Written ${filename} (${Buffer.byteLength(content, "utf8")} bytes)`,
      };
    }
    if (toolUse.name === "read_file") {
      const filename = typeof input.filename === "string" ? input.filename : "";
      if (!filename) throw new Error("read_file.filename is required");
      const content = await fs.readFile(resolveJobPath(jobDir, filename), "utf8");
      return { tool_use_id: toolUse.id, content: content.slice(0, 12_000) };
    }
    if (toolUse.name === "run_command") {
      const command = typeof input.command === "string" ? input.command : "";
      const args = Array.isArray(input.args) ? input.args.map(String) : [];
      const output = await runCommand(jobDir, nodePath, command, args, signal);
      return { tool_use_id: toolUse.id, content: output };
    }
    return {
      tool_use_id: toolUse.id,
      content: `unknown tool: ${toolUse.name}`,
      is_error: true,
    };
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    return {
      tool_use_id: toolUse.id,
      content: `Error: ${message}`,
      is_error: true,
    };
  }
}

async function prepareHwpxWorkspace(jobDir: string, template?: HwpxTemplate): Promise<void> {
  const effectiveTemplate = template ?? "report";
  const sectionSource = effectiveTemplate === "base"
    ? path.join(HWPX_RUNTIME_ROOT, "templates", "base", "Contents", "section0.xml")
    : path.join(HWPX_RUNTIME_ROOT, "templates", effectiveTemplate, "section0.xml");
  const headerSource = effectiveTemplate === "base"
    ? path.join(HWPX_RUNTIME_ROOT, "templates", "base", "Contents", "header.xml")
    : path.join(HWPX_RUNTIME_ROOT, "templates", effectiveTemplate, "header.xml");

  await fs.copyFile(sectionSource, path.join(jobDir, "starter_section0.xml"));
  await fs.copyFile(headerSource, path.join(jobDir, "template_header.xml"));
  await fs.writeFile(
    path.join(jobDir, "HWPX_AUTHORING.md"),
    [
      "# HWPX Authoring Workspace",
      "",
      "- `source.md`: user source content.",
      "- `starter_section0.xml`: valid section skeleton for this template.",
      "- `template_header.xml`: style ids and paragraph/character properties for this template.",
      "- Write `section0.xml`, then build `output.hwpx` with the bundled build script.",
      "- Always run `validate.py output.hwpx`; the runtime also checks output text against `source.md`.",
      "- Reference edits also require `page_guard.py`.",
    ].join("\n"),
    "utf8",
  );
}

export async function writeDocumentAgentically(
  input: AgenticDocumentWriterInput,
  deps: AgenticDocumentAuthorDeps,
): Promise<AgenticDocumentWriteResult> {
  const jobDir = await fs.mkdtemp(path.join(os.tmpdir(), "clawy-document-author-"));
  const outputName = `output.${input.format}`;
  const outputPath = path.join(jobDir, outputName);
  const nodePath = deps.nodePath ?? path.resolve(process.cwd(), "node_modules");
  const maxTurns = deps.maxTurns ?? (input.format === "hwpx" ? 25 : 20);
  const model = deps.resolveModel
    ? await deps.resolveModel()
    : deps.fallbackModel;
  const effectiveModel = model ?? "claude-sonnet-4-6";

  try {
    const sourcePath = path.join(jobDir, "source.md");
    await fs.writeFile(sourcePath, input.sourceMarkdown, "utf8");
    if (input.format === "hwpx") {
      await prepareHwpxWorkspace(jobDir, input.template);
    }
    if (input.referencePath) {
      await fs.copyFile(input.referencePath, path.join(jobDir, "reference.hwpx"));
    }
    const referenceInJob = input.referencePath ? path.join(jobDir, "reference.hwpx") : undefined;
    const validationOptions = {
      referencePath: referenceInJob,
      sourcePath: input.format === "hwpx" ? sourcePath : undefined,
      sourceMarkdown: input.sourceMarkdown,
      title: input.title,
    };

    const messages: LLMMessage[] = [
      { role: "user", content: userPrompt(input, outputName) },
    ];
    const system = systemPromptFor(input.format, outputName, input.template, Boolean(input.referencePath));
    const sse = new StubSseWriter();
    let toolCallCount = 0;
    let turnsCompleted = 0;

    for (let turn = 0; turn < maxTurns; turn += 1) {
      input.ctx.emitProgress({
        label: `Agentic document authoring turn ${turn + 1}`,
        percent: Math.min(85, 10 + Math.round((turn / maxTurns) * 75)),
      });
      const { blocks, stopReason } = await readOne(
        {
          llm: deps.llm,
          model: effectiveModel,
          sse,
          abortSignal: input.ctx.abortSignal,
          onError: () => {
            /* readOne throws after surfacing the error */
          },
        },
        system,
        messages,
        AGENTIC_TOOLS,
        { thinkingOverride: { type: "disabled" } },
      );
      turnsCompleted = turn + 1;

      const toolUses = blocks.filter(
        (block): block is Extract<LLMContentBlock, { type: "tool_use" }> => block.type === "tool_use",
      );
      if (toolUses.length === 0 || stopReason !== "tool_use") {
        const validationError = await validateOutput(input.format, outputPath, validationOptions);
        if (!validationError) break;
        messages.push({ role: "assistant", content: blocks });
        messages.push({
          role: "user",
          content:
            `The output file is not ready: ${validationError}. Use write_file and run_command now to create a valid ${input.format.toUpperCase()} file. Do not narrate.`,
        });
        continue;
      }

      toolCallCount += toolUses.length;
      messages.push({ role: "assistant", content: blocks });
      const results = [];
      for (const toolUse of toolUses) {
        results.push(await executeToolUse(jobDir, nodePath, toolUse, input.ctx.abortSignal));
      }
      const validationError = await validateOutput(input.format, outputPath, validationOptions);
      messages.push({
        role: "user",
        content: [
          ...results.map((result) => ({
            type: "tool_result" as const,
            tool_use_id: result.tool_use_id,
            content: result.content,
            ...(result.is_error ? { is_error: true as const } : {}),
          })),
          ...(validationError
            ? [{
                type: "text" as const,
                text: `The output file is not ready: ${validationError}. Fix the document generation and retry.`,
              }]
            : []),
        ],
      });

      if (!validationError && results.some((result) => !result.is_error)) {
        break;
      }
      if (messages.length > 9) {
        messages.splice(1, messages.length - 9);
      }
    }

    const validationError = await validateOutput(input.format, outputPath, validationOptions);
    if (validationError) {
      throw new Error(`agentic document author did not produce a valid output file: ${validationError}`);
    }
    await fs.mkdir(path.dirname(input.absPath), { recursive: true });
    await fs.copyFile(outputPath, input.absPath);
    return { mode: "agentic", turns: turnsCompleted, toolCallCount, model: effectiveModel };
  } finally {
    await fs.rm(jobDir, { recursive: true, force: true });
  }
}

async function validateOutput(
  format: AgenticDocumentFormat,
  filePath: string,
  options: {
    referencePath?: string;
    sourcePath?: string;
    sourceMarkdown?: string;
    title?: string;
  } = {},
): Promise<string | null> {
  try {
    const stat = await fs.stat(filePath);
    if (!stat.isFile() || stat.size === 0) {
      return "output file is missing or empty";
    }
    const handle = await fs.open(filePath, "r");
    try {
      const header = Buffer.alloc(2);
      await handle.read(header, 0, header.length, 0);
      if (header.toString("utf8") !== "PK") {
        return `${format.toUpperCase()} output must be a ZIP-based document with a PK header`;
      }
    } finally {
      await handle.close();
    }

    if (format === "docx") {
      const markdownError = validateDocxMarkdownRender(
        await inspectDocx(filePath),
        options.sourceMarkdown ?? "",
      );
      if (markdownError) return markdownError;
    }

    if (format === "hwpx") {
      const validateScript = path.join(HWPX_RUNTIME_ROOT, "scripts", "validate.py");
      try {
        await execFile("python3", [validateScript, filePath], {
          cwd: HWPX_RUNTIME_ROOT,
          timeout: 90_000,
          maxBuffer: 256 * 1024,
        });
      } catch (error) {
        const stderr = typeof (error as { stderr?: unknown }).stderr === "string"
          ? (error as { stderr: string }).stderr
          : "";
        const stdout = typeof (error as { stdout?: unknown }).stdout === "string"
          ? (error as { stdout: string }).stdout
          : "";
        const message = [stderr, stdout]
          .map((part) => part.trim())
          .filter(Boolean)
          .join("\n");
        return message || `validate.py failed for ${filePath}`;
      }

      if (options.sourcePath) {
        const contentGuardScript = path.join(HWPX_RUNTIME_ROOT, "scripts", "content_guard.py");
        try {
          await execFile("python3", [
            contentGuardScript,
            "--source",
            options.sourcePath,
            "--output",
            filePath,
            "--title",
            options.title ?? "",
          ], {
            cwd: HWPX_RUNTIME_ROOT,
            timeout: 90_000,
            maxBuffer: 256 * 1024,
          });
        } catch (error) {
          const stdout = typeof (error as { stdout?: unknown }).stdout === "string"
            ? (error as { stdout: string }).stdout
            : "";
          const stderr = typeof (error as { stderr?: unknown }).stderr === "string"
            ? (error as { stderr: string }).stderr
            : "";
          const message = [stdout, stderr]
            .map((part) => part.trim())
            .filter(Boolean)
            .join("\n");
          return message || `content_guard.py failed for ${filePath}`;
        }
      }

      if (options.referencePath) {
        const pageGuardScript = path.join(HWPX_RUNTIME_ROOT, "scripts", "page_guard.py");
        try {
          await execFile("python3", [pageGuardScript, "--reference", options.referencePath, "--output", filePath], {
            cwd: HWPX_RUNTIME_ROOT,
            timeout: 90_000,
            maxBuffer: 256 * 1024,
          });
        } catch (error) {
          const stdout = typeof (error as { stdout?: unknown }).stdout === "string"
            ? (error as { stdout: string }).stdout
            : "";
          const stderr = typeof (error as { stderr?: unknown }).stderr === "string"
            ? (error as { stderr: string }).stderr
            : "";
          const message = [stdout, stderr]
            .map((part) => part.trim())
            .filter(Boolean)
            .join("\n");
          return message || `page_guard.py failed for ${filePath}`;
        }
      }
    }

    return null;
  } catch (error) {
    const code = (error as NodeJS.ErrnoException).code;
    if (code === "ENOENT") return "output file does not exist";
    const message = error instanceof Error ? error.message : String(error);
    return `cannot inspect output file: ${message}`;
  }
}
