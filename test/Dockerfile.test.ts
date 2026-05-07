import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const dockerfile = readFileSync(join(process.cwd(), "Dockerfile"), "utf8");

describe("core-agent Dockerfile", () => {
  it("uses a glibc-compatible runtime image for agent-browser", () => {
    expect(dockerfile).toMatch(/^FROM node:22-bookworm-slim AS builder$/m);
    expect(dockerfile).toMatch(/^FROM node:22-bookworm-slim$/m);
    expect(dockerfile).not.toMatch(/apk add/);
  });

  it("keeps bot shell dependencies and Magi bin PATH in the runtime image", () => {
    expect(dockerfile).toMatch(/apt-get install[\s\S]*bash[\s\S]*curl[\s\S]*git/);
    expect(dockerfile).toMatch(/apt-get install[\s\S]*python3[\s\S]*python3-lxml/);
    expect(dockerfile).toMatch(/apt-get install[\s\S]*fontconfig[\s\S]*fonts-noto-cjk/);
    expect(dockerfile).toMatch(/PATH=\/home\/ocuser\/\.magi\/bin:\/app\/node_modules\/\.bin/);
  });
});
