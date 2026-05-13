import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { createLogger } from "./logger.js";

describe("createLogger", () => {
  let stdoutSpy: ReturnType<typeof vi.spyOn>;

  beforeEach(() => {
    stdoutSpy = vi.spyOn(process.stdout, "write").mockReturnValue(true);
  });

  afterEach(() => {
    stdoutSpy.mockRestore();
    delete process.env.LOG_LEVEL;
  });

  it("emits structured JSON to stdout", () => {
    const logger = createLogger("TestComponent");
    logger.info("test_event", { model: "opus" });

    expect(stdoutSpy).toHaveBeenCalledOnce();
    const output = stdoutSpy.mock.calls[0]![0] as string;
    const parsed = JSON.parse(output.trim());
    expect(parsed.service).toBe("magi-agent");
    expect(parsed.component).toBe("TestComponent");
    expect(parsed.level).toBe("INFO");
    expect(parsed.event).toBe("test_event");
    expect(parsed.model).toBe("opus");
    expect(parsed.ts).toBeDefined();
  });

  it("redacts sensitive fields", () => {
    const logger = createLogger("Secure");
    logger.info("auth_check", { apiToken: "secret123", username: "alice" });

    const output = stdoutSpy.mock.calls[0]![0] as string;
    const parsed = JSON.parse(output.trim());
    expect(parsed.apiToken).toBe("[REDACTED]");
    expect(parsed.username).toBe("alice");
  });

  it("respects LOG_LEVEL env to suppress lower levels", () => {
    process.env.LOG_LEVEL = "WARN";
    const logger = createLogger("Test");

    logger.debug("debug_event");
    logger.info("info_event");
    expect(stdoutSpy).not.toHaveBeenCalled();

    logger.warn("warn_event");
    expect(stdoutSpy).toHaveBeenCalledOnce();
  });

  it("defaults to INFO level", () => {
    const logger = createLogger("Test");

    logger.debug("should_be_suppressed");
    expect(stdoutSpy).not.toHaveBeenCalled();

    logger.info("should_emit");
    expect(stdoutSpy).toHaveBeenCalledOnce();
  });

  it("handles missing data parameter", () => {
    const logger = createLogger("Test");
    logger.info("bare_event");

    const output = stdoutSpy.mock.calls[0]![0] as string;
    const parsed = JSON.parse(output.trim());
    expect(parsed.event).toBe("bare_event");
  });

  it("emits error level logs", () => {
    const logger = createLogger("Test");
    logger.error("crash", { code: 500 });

    const output = stdoutSpy.mock.calls[0]![0] as string;
    const parsed = JSON.parse(output.trim());
    expect(parsed.level).toBe("ERROR");
    expect(parsed.code).toBe(500);
  });
});
