import { readFileSync } from "node:fs";
import { join } from "node:path";
import { parse } from "yaml";
import { describe, expect, it } from "vitest";

const composeText = readFileSync(join(process.cwd(), "docker-compose.yml"), "utf8");
const envExample = readFileSync(join(process.cwd(), ".env.example"), "utf8");
const compose = parse(composeText) as {
  services?: Record<string, {
    build?: unknown;
    image?: unknown;
    ports?: string[];
    env_file?: string[];
    environment?: Record<string, string>;
    volumes?: string[];
  }>;
  volumes?: Record<string, unknown>;
};

describe("self-host Docker Compose", () => {
  it("runs a local magi-agent service on port 8080 with the example env file", () => {
    const service = compose.services?.["magi-agent"];
    expect(service).toBeDefined();
    expect(service?.build).toBe(".");
    expect(service?.ports).toContain("8080:8080");
    expect(service?.env_file).toContain(".env");
  });

  it("persists runtime workspace state and mounts editable local config", () => {
    const service = compose.services?.["magi-agent"];
    expect(service?.volumes).toContain("magi-workspace:/home/ocuser/.magi/workspace");
    expect(service?.volumes).toContain("./magi-agent.yaml:/app/magi-agent.yaml");
    expect(compose.volumes).toHaveProperty("magi-workspace");
  });

  it("documents no-auth local OpenAI-compatible model defaults", () => {
    expect(envExample).toContain("MAGI_AGENT_SERVER_TOKEN=");
    expect(envExample).toContain("OPENAI_BASE_URL=http://host.docker.internal:11434/v1");
    expect(envExample).toContain("CORE_AGENT_ROUTING_MODE=direct");
    expect(envExample).not.toMatch(/sk-[A-Za-z0-9]/);
  });
});
