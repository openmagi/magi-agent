const LOG_LEVELS = { DEBUG: 0, INFO: 1, WARN: 2, ERROR: 3 } as const;
type LogLevel = keyof typeof LOG_LEVELS;

const SENSITIVE_KEY_RE = /token|secret|password|key|auth/i;

function resolveLogLevel(): LogLevel {
  const raw = (process.env.LOG_LEVEL ?? "INFO").toUpperCase().trim();
  if (raw in LOG_LEVELS) return raw as LogLevel;
  return "INFO";
}

function redactSensitiveFields(
  data: Record<string, unknown>,
): Record<string, unknown> {
  const result: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(data)) {
    if (SENSITIVE_KEY_RE.test(key)) {
      result[key] = "[REDACTED]";
    } else {
      result[key] = value;
    }
  }
  return result;
}

export interface Logger {
  debug(event: string, data?: Record<string, unknown>): void;
  info(event: string, data?: Record<string, unknown>): void;
  warn(event: string, data?: Record<string, unknown>): void;
  error(event: string, data?: Record<string, unknown>): void;
}

export function createLogger(component: string): Logger {
  const emit = (level: LogLevel, event: string, data?: Record<string, unknown>): void => {
    const threshold = resolveLogLevel();
    if (LOG_LEVELS[level] < LOG_LEVELS[threshold]) return;

    const entry: Record<string, unknown> = {
      ts: new Date().toISOString(),
      service: "magi-agent",
      component,
      level,
      event,
      ...(data ? redactSensitiveFields(data) : {}),
    };

    process.stdout.write(JSON.stringify(entry) + "\n");
  };

  return {
    debug: (event, data) => emit("DEBUG", event, data),
    info: (event, data) => emit("INFO", event, data),
    warn: (event, data) => emit("WARN", event, data),
    error: (event, data) => emit("ERROR", event, data),
  };
}
