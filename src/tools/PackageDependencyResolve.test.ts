import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import type { ToolContext } from "../Tool.js";
import {
  makePackageDependencyResolveTool,
  type PackageDependencyRegistryRun,
  type PackageDependencyRegistryRunner,
} from "./PackageDependencyResolve.js";

const roots: string[] = [];

async function makeRoot(prefix: string): Promise<string> {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), prefix));
  roots.push(root);
  return root;
}

function ctx(workspaceRoot: string): ToolContext {
  return {
    botId: "bot-1",
    sessionKey: "s-1",
    turnId: "turn-1",
    workspaceRoot,
    askUser: async () => ({ selectedId: "ok" }),
    emitProgress: () => {},
    emitAgentEvent: () => {},
    abortSignal: AbortSignal.timeout(5_000),
    staging: {
      stageFileWrite: () => {},
      stageTranscriptAppend: () => {},
      stageAuditEvent: () => {},
    },
  };
}

interface RegistryCall {
  packageName: string;
  url: string;
  timeoutMs: number;
}

function fakeRegistryRunner(
  calls: RegistryCall[],
  body: Record<string, unknown>,
): PackageDependencyRegistryRunner {
  return async (run: PackageDependencyRegistryRun) => {
    calls.push({
      packageName: run.packageName,
      url: run.url,
      timeoutMs: run.timeoutMs,
    });
    return {
      statusCode: 200,
      url: run.url,
      body: JSON.stringify(body),
    };
  };
}

async function writeNpmProject(root: string): Promise<void> {
  await fs.writeFile(
    path.join(root, "package.json"),
    JSON.stringify(
      {
        name: "consumer",
        dependencies: {
          "@scope/pkg": "^1.2.0",
        },
        devDependencies: {
          "dev-only": "^0.1.0",
        },
      },
      null,
      2,
    ),
  );
  await fs.writeFile(
    path.join(root, "package-lock.json"),
    JSON.stringify(
      {
        name: "consumer",
        lockfileVersion: 3,
        packages: {
          "": {
            dependencies: {
              "@scope/pkg": "^1.2.0",
            },
          },
          "node_modules/@scope/pkg": {
            version: "1.2.3",
            resolved: "https://registry.npmjs.org/@scope/pkg/-/pkg-1.2.3.tgz",
          },
          "node_modules/dev-only": {
            version: "0.1.1",
          },
        },
      },
      null,
      2,
    ),
  );
}

afterEach(async () => {
  await Promise.all(
    roots.splice(0).map((root) => fs.rm(root, { recursive: true, force: true })),
  );
});

describe("PackageDependencyResolve", () => {
  it("resolves an npm dependency to locked version, registry metadata, and cache hints", async () => {
    const workspaceRoot = await makeRoot("package-dep-resolve-");
    await writeNpmProject(workspaceRoot);
    const registryCalls: RegistryCall[] = [];
    const tool = makePackageDependencyResolveTool({
      registryRunner: fakeRegistryRunner(registryCalls, {
        name: "@scope/pkg",
        "dist-tags": { latest: "1.2.4" },
        versions: {
          "1.2.3": {
            version: "1.2.3",
            repository: {
              type: "git",
              url: "git+https://github.com/example/pkg.git",
            },
            homepage: "https://docs.example.com/pkg",
          },
        },
      }),
    });

    const result = await tool.execute(
      { packageName: "@scope/pkg" },
      ctx(workspaceRoot),
    );

    expect(result.status).toBe("ok");
    expect(registryCalls).toEqual([
      {
        packageName: "@scope/pkg",
        url: "https://registry.npmjs.org/@scope%2fpkg",
        timeoutMs: 30_000,
      },
    ]);
    expect(result.output).toMatchObject({
      manager: "npm",
      packageName: "@scope/pkg",
      manifestPath: "package.json",
      lockfilePath: "package-lock.json",
      dependencyType: "dependencies",
      requestedRange: "^1.2.0",
      lockedVersion: "1.2.3",
      registryFetched: true,
      registry: {
        url: "https://registry.npmjs.org/@scope%2fpkg",
        version: "1.2.3",
      },
      repositoryUrl: "https://github.com/example/pkg.git",
      docsUrl: "https://docs.example.com/pkg",
      cacheHints: [
        {
          tool: "ExternalSourceCache",
          action: "ensure_repo",
          url: "https://github.com/example/pkg.git",
        },
        {
          tool: "ExternalSourceCache",
          action: "ensure_url",
          url: "https://docs.example.com/pkg",
          format: "markdown",
        },
      ],
    });
  });

  it("returns lockfile information without registry calls when registry lookup is disabled", async () => {
    const workspaceRoot = await makeRoot("package-dep-resolve-");
    await writeNpmProject(workspaceRoot);
    const registryCalls: RegistryCall[] = [];
    const tool = makePackageDependencyResolveTool({
      registryRunner: fakeRegistryRunner(registryCalls, {}),
    });

    const result = await tool.execute(
      { packageName: "@scope/pkg", includeRegistry: false },
      ctx(workspaceRoot),
    );

    expect(result.status).toBe("ok");
    expect(registryCalls).toEqual([]);
    expect(result.output).toMatchObject({
      packageName: "@scope/pkg",
      requestedRange: "^1.2.0",
      lockedVersion: "1.2.3",
      registryFetched: false,
      cacheHints: [],
    });
  });

  it("rejects manifest path escapes before reading files or calling the registry", async () => {
    const workspaceRoot = await makeRoot("package-dep-resolve-");
    await writeNpmProject(workspaceRoot);
    const registryCalls: RegistryCall[] = [];
    const tool = makePackageDependencyResolveTool({
      registryRunner: fakeRegistryRunner(registryCalls, {}),
    });

    const result = await tool.execute(
      { packageName: "@scope/pkg", manifestPath: "../package.json" },
      ctx(workspaceRoot),
    );

    expect(result.status).toBe("error");
    expect(result.errorCode).toBe("path_escape");
    expect(registryCalls).toEqual([]);
  });

  it("reports invalid lockfile JSON before calling the registry", async () => {
    const workspaceRoot = await makeRoot("package-dep-resolve-");
    await writeNpmProject(workspaceRoot);
    await fs.writeFile(path.join(workspaceRoot, "package-lock.json"), "{ not json");
    const registryCalls: RegistryCall[] = [];
    const tool = makePackageDependencyResolveTool({
      registryRunner: fakeRegistryRunner(registryCalls, {}),
    });

    const result = await tool.execute(
      { packageName: "@scope/pkg" },
      ctx(workspaceRoot),
    );

    expect(result.status).toBe("error");
    expect(result.errorCode).toBe("invalid_json");
    expect(result.errorMessage).toContain("package lockfile");
    expect(registryCalls).toEqual([]);
  });
});
