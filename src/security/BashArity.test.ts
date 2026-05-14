import { describe, it, expect } from "vitest";
import { prefix, semanticPrefix, semanticPattern, tokenize, splitSegments } from "./BashArity.js";
import type { CommandSegment } from "./BashArity.js";

describe("BashArity.tokenize", () => {
  it("splits on unquoted whitespace", () => {
    expect(tokenize("git push origin")).toEqual(["git", "push", "origin"]);
  });

  it("handles single-quoted strings", () => {
    expect(tokenize("echo 'hello world'")).toEqual(["echo", "hello world"]);
  });

  it("handles double-quoted strings", () => {
    expect(tokenize('echo "hello world"')).toEqual(["echo", "hello world"]);
  });

  it("handles backslash escapes", () => {
    expect(tokenize("echo hello\\ world")).toEqual(["echo", "hello world"]);
  });

  it("returns empty array for empty string", () => {
    expect(tokenize("")).toEqual([]);
  });
});

describe("BashArity.splitSegments", () => {
  it("splits on pipe", () => {
    const segs = splitSegments("cat file.txt | grep pattern");
    expect(segs).toHaveLength(2);
    expect(segs[0]?.command).toBe("cat");
    expect(segs[1]?.command).toBe("grep");
  });

  it("splits on &&", () => {
    const segs = splitSegments("npm test && npm run build");
    expect(segs).toHaveLength(2);
  });

  it("splits on ||", () => {
    const segs = splitSegments("test -f a || echo missing");
    expect(segs).toHaveLength(2);
  });

  it("splits on ;", () => {
    const segs = splitSegments("echo a; echo b");
    expect(segs).toHaveLength(2);
  });

  it("returns empty for empty string", () => {
    expect(splitSegments("")).toEqual([]);
    expect(splitSegments("   ")).toEqual([]);
  });

  it("respects quotes around connectors", () => {
    const segs = splitSegments("echo 'hello | world'");
    expect(segs).toHaveLength(1);
    expect(segs[0]?.args).toContain("hello | world");
  });
});

describe("BashArity.prefix", () => {
  it("returns empty array for empty tokens", () => {
    expect(prefix([])).toEqual([]);
  });

  it("returns arity-2 prefix for git commands", () => {
    expect(prefix(["git", "push", "origin", "main"])).toEqual(["git", "push"]);
    expect(prefix(["git", "checkout", "-b", "feat"])).toEqual(["git", "checkout"]);
    expect(prefix(["git", "reset", "--hard"])).toEqual(["git", "reset"]);
  });

  it("returns arity-3 prefix for npm run", () => {
    expect(prefix(["npm", "run", "dev"])).toEqual(["npm", "run", "dev"]);
    expect(prefix(["npm", "run", "build", "--prod"])).toEqual(["npm", "run", "build"]);
  });

  it("returns arity-2 prefix for npm (non-run)", () => {
    expect(prefix(["npm", "install", "lodash"])).toEqual(["npm", "install"]);
    expect(prefix(["npm", "test"])).toEqual(["npm", "test"]);
  });

  it("returns arity-3 for docker compose", () => {
    expect(prefix(["docker", "compose", "up", "-d"])).toEqual(["docker", "compose", "up"]);
  });

  it("returns arity-2 for docker (non-compose)", () => {
    expect(prefix(["docker", "run", "alpine"])).toEqual(["docker", "run"]);
  });

  it("returns arity-2 for kubectl", () => {
    expect(prefix(["kubectl", "apply", "-f", "deploy.yaml"])).toEqual(["kubectl", "apply"]);
    expect(prefix(["kubectl", "delete", "pod", "my-pod"])).toEqual(["kubectl", "delete"]);
  });

  it("returns arity-2 for sudo", () => {
    expect(prefix(["sudo", "rm", "-rf", "/"])).toEqual(["sudo", "rm"]);
  });

  it("returns arity-1 for simple commands", () => {
    expect(prefix(["rm", "-rf", "node_modules"])).toEqual(["rm"]);
    expect(prefix(["curl", "https://example.com"])).toEqual(["curl"]);
    expect(prefix(["chmod", "755", "script.sh"])).toEqual(["chmod"]);
  });

  it("returns arity-1 for unknown commands", () => {
    expect(prefix(["mycommand", "arg1", "arg2"])).toEqual(["mycommand"]);
    expect(prefix(["foo"])).toEqual(["foo"]);
  });

  it("handles single-token git gracefully", () => {
    expect(prefix(["git"])).toEqual(["git"]);
  });

  it("handles pnpm run", () => {
    expect(prefix(["pnpm", "run", "lint"])).toEqual(["pnpm", "run", "lint"]);
    expect(prefix(["pnpm", "install"])).toEqual(["pnpm", "install"]);
  });

  it("handles deno", () => {
    expect(prefix(["deno", "run", "main.ts"])).toEqual(["deno", "run"]);
  });
});

describe("BashArity.semanticPrefix", () => {
  it("extracts semantic prefix from raw command string", () => {
    expect(semanticPrefix("git push origin main")).toBe("git push");
    expect(semanticPrefix("npm run dev")).toBe("npm run dev");
    expect(semanticPrefix("rm -rf node_modules")).toBe("rm");
  });

  it("returns empty string for empty command", () => {
    expect(semanticPrefix("")).toBe("");
    expect(semanticPrefix("   ")).toBe("");
  });

  it("uses first segment only for piped commands", () => {
    expect(semanticPrefix("cat file.txt | grep pattern")).toBe("cat");
  });

  it("handles quoted arguments", () => {
    expect(semanticPrefix('git commit -m "hello world"')).toBe("git commit");
  });
});

describe("BashArity.semanticPattern", () => {
  it("produces pattern with wildcard suffix", () => {
    const seg: CommandSegment = {
      command: "git",
      args: ["push", "origin"],
      raw: "git push origin",
    };
    expect(semanticPattern(seg)).toBe("git push *");
  });

  it("produces pattern for simple commands", () => {
    const seg: CommandSegment = {
      command: "rm",
      args: ["-rf", "node_modules"],
      raw: "rm -rf node_modules",
    };
    expect(semanticPattern(seg)).toBe("rm *");
  });

  it("handles npm run pattern", () => {
    const seg: CommandSegment = {
      command: "npm",
      args: ["run", "build"],
      raw: "npm run build",
    };
    expect(semanticPattern(seg)).toBe("npm run build *");
  });
});
