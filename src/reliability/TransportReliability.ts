import { existsSync } from "node:fs";
import { basename } from "node:path";
import { readFile } from "node:fs/promises";
import type {
  ReliableRequestResult,
  ReliableRequestSpec,
  TransportClassificationInput,
  TransportFailureClass,
  TransportFailureVerdict,
  TransportPolicy,
} from "./transportTypes.js";

export const RELIABLE_REQUEST_HELPER_PATH = "/app/runtime/reliable-request.mjs";

export const DEFAULT_TRANSPORT_POLICY: TransportPolicy = {
  backoffSeconds: [0, 10, 30],
  maxAttempts: 3,
  timeoutMs: 600_000,
};

const TRANSIENT_HINTS = [
  "econnreset",
  "etimedout",
  "timed out",
  "timeout",
  "temporary unavailable",
  "temporarily unavailable",
  "service unavailable",
  "connection reset",
  "socket hang up",
  "eai_again",
];

const RATE_LIMIT_HINTS = ["rate limit", "too many requests", "retry later"];
const AUTH_HINTS = ["invalid token", "unauthorized", "authentication failed"];
const PERMISSION_HINTS = ["forbidden", "permission denied"];

function lower(value?: string | null): string {
  return (value ?? "").toLowerCase();
}

function parseRetryAfterSeconds(value?: string | null): number | null {
  if (!value) {
    return null;
  }
  if (/^\d+$/.test(value.trim())) {
    return Number.parseInt(value.trim(), 10);
  }
  const asDate = Date.parse(value);
  if (Number.isNaN(asDate)) {
    return null;
  }
  return Math.max(0, Math.ceil((asDate - Date.now()) / 1000));
}

function isHinted(text: string, hints: string[]): boolean {
  return hints.some((hint) => text.includes(hint));
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

export function transportReliabilityStatus(): {
  helperPath: string;
  helperExists: boolean;
  helperWired: boolean;
  enabled: boolean;
  defaultBackoffSeconds: number[];
  maxAttempts: number;
} {
  const helperPath = process.env.CORE_AGENT_RELIABLE_REQUEST_SCRIPT || RELIABLE_REQUEST_HELPER_PATH;
  const enabled = process.env.CORE_AGENT_TRANSPORT_RELIABILITY !== "off";
  const helperExists = existsSync(helperPath);
  return {
    helperPath,
    helperExists,
    helperWired: enabled && helperExists,
    enabled,
    defaultBackoffSeconds: [...DEFAULT_TRANSPORT_POLICY.backoffSeconds],
    maxAttempts: DEFAULT_TRANSPORT_POLICY.maxAttempts,
  };
}

export class TransportReliability {
  private readonly policy: TransportPolicy;

  constructor(policy: Partial<TransportPolicy> = {}) {
    this.policy = {
      ...DEFAULT_TRANSPORT_POLICY,
      ...policy,
      backoffSeconds: [...(policy.backoffSeconds ?? DEFAULT_TRANSPORT_POLICY.backoffSeconds)],
    };
  }

  static classifyFailure(input: TransportClassificationInput): TransportFailureVerdict {
    const retryAfterSeconds = parseRetryAfterSeconds(input.retryAfterHeader);
    const text = `${lower(input.responseText)} ${lower(input.errorMessage)}`.trim();
    const status = input.statusCode;

    if (status === 429 || isHinted(text, RATE_LIMIT_HINTS)) {
      return {
        classification: "rate_limited",
        retryable: true,
        retryAfterSeconds,
        statusCode: status,
        message: status ? `HTTP ${status} rate limited` : "rate limited",
      };
    }

    if (status === 401 || status === 407 || isHinted(text, AUTH_HINTS)) {
      return {
        classification: "auth",
        retryable: false,
        retryAfterSeconds,
        statusCode: status,
        message: status ? `HTTP ${status} authentication failure` : "authentication failure",
      };
    }

    if (status === 403 || isHinted(text, PERMISSION_HINTS)) {
      return {
        classification: "permission",
        retryable: false,
        retryAfterSeconds,
        statusCode: status,
        message: status ? `HTTP ${status} permission failure` : "permission failure",
      };
    }

    if (status === 404) {
      return {
        classification: "not_found",
        retryable: false,
        retryAfterSeconds,
        statusCode: status,
        message: "resource not found",
      };
    }

    if (
      status === 400 ||
      status === 409 ||
      status === 410 ||
      status === 413 ||
      status === 415 ||
      status === 422
    ) {
      return {
        classification: "input",
        retryable: false,
        retryAfterSeconds,
        statusCode: status,
        message: `HTTP ${status} invalid request`,
      };
    }

    if (
      status === 408 ||
      status === 425 ||
      status === 502 ||
      status === 503 ||
      status === 504 ||
      (typeof status === "number" && status >= 500)
    ) {
      return {
        classification: "transient",
        retryable: true,
        retryAfterSeconds,
        statusCode: status,
        message: `HTTP ${status} transient upstream failure`,
      };
    }

    if (isHinted(text, TRANSIENT_HINTS)) {
      return {
        classification: "transient",
        retryable: true,
        retryAfterSeconds,
        statusCode: status,
        message: "transient transport failure",
      };
    }

    return {
      classification: "fatal",
      retryable: false,
      retryAfterSeconds,
      statusCode: status,
      message: status ? `HTTP ${status} fatal failure` : "fatal transport failure",
    };
  }

  static nextDelaySeconds(input: {
    policy?: TransportPolicy;
    nextAttemptNumber: number;
    classification: TransportFailureClass;
    retryAfterSeconds?: number | null;
  }): number | null {
    const policy = input.policy ?? DEFAULT_TRANSPORT_POLICY;
    if (input.nextAttemptNumber > policy.maxAttempts) {
      return null;
    }
    if (input.classification === "rate_limited" && (input.retryAfterSeconds ?? 0) > 0) {
      return input.retryAfterSeconds ?? null;
    }
    const index = Math.max(0, Math.min(input.nextAttemptNumber - 1, policy.backoffSeconds.length - 1));
    return policy.backoffSeconds[index] ?? 0;
  }

  async request(spec: ReliableRequestSpec): Promise<ReliableRequestResult> {
    let lastFailure: TransportFailureVerdict | null = null;
    let lastBody = "";
    let lastTransportError: string | undefined;
    let lastHeaders: Record<string, string> | undefined;

    for (let attempt = 1; attempt <= this.policy.maxAttempts; attempt += 1) {
      if (attempt > 1 && lastFailure) {
        const delaySeconds = TransportReliability.nextDelaySeconds({
          policy: this.policy,
          nextAttemptNumber: attempt,
          classification: lastFailure.classification,
          retryAfterSeconds: lastFailure.retryAfterSeconds,
        });
        if ((delaySeconds ?? 0) > 0) {
          await sleep((delaySeconds ?? 0) * 1000);
        }
      }

      try {
        const response = await this.performFetch(spec);
        const body = await response.text();
        const headers = Object.fromEntries(response.headers.entries());
        if (response.ok) {
          return {
            ok: true,
            classification: "success",
            attemptCount: attempt,
            statusCode: response.status,
            body,
            headers,
          };
        }

        lastBody = body;
        lastHeaders = headers;
        lastFailure = TransportReliability.classifyFailure({
          statusCode: response.status,
          responseText: body,
          retryAfterHeader: response.headers.get("retry-after"),
        });

        if (lastFailure.retryable && attempt < this.policy.maxAttempts) {
          continue;
        }

        return {
          ok: false,
          classification: lastFailure.classification,
          attemptCount: attempt,
          statusCode: response.status,
          body,
          headers,
          message: lastFailure.message,
          retryAfterSeconds: lastFailure.retryAfterSeconds,
          retryExhausted: lastFailure.retryable && attempt >= this.policy.maxAttempts,
        };
      } catch (error) {
        lastTransportError = error instanceof Error ? error.message : String(error);
        lastFailure = TransportReliability.classifyFailure({
          errorMessage: lastTransportError,
        });
        if (lastFailure.retryable && attempt < this.policy.maxAttempts) {
          continue;
        }
        return {
          ok: false,
          classification: lastFailure.classification,
          attemptCount: attempt,
          message: lastFailure.message,
          retryAfterSeconds: lastFailure.retryAfterSeconds,
          retryExhausted: lastFailure.retryable && attempt >= this.policy.maxAttempts,
          transportError: lastTransportError,
          body: lastBody || undefined,
          headers: lastHeaders,
        };
      }
    }

    return {
      ok: false,
      classification: lastFailure?.classification ?? "fatal",
      attemptCount: this.policy.maxAttempts,
      message: lastFailure?.message ?? "transport request exhausted without a verdict",
      retryAfterSeconds: lastFailure?.retryAfterSeconds ?? null,
      retryExhausted: true,
      transportError: lastTransportError,
      body: lastBody || undefined,
      headers: lastHeaders,
    };
  }

  private async performFetch(spec: ReliableRequestSpec): Promise<Response> {
    const headers = new Headers(spec.headers ?? {});
    const init: RequestInit = {
      method: spec.method,
      headers,
      signal: AbortSignal.timeout(spec.timeoutMs ?? this.policy.timeoutMs),
    };

    if ((spec.formFields?.length ?? 0) > 0 || (spec.formFiles?.length ?? 0) > 0) {
      headers.delete("content-type");
      const form = new FormData();
      for (const field of spec.formFields ?? []) {
        form.append(field.name, field.value);
      }
      for (const file of spec.formFiles ?? []) {
        const blob = new Blob([await readFile(file.path)]);
        form.append(file.name, blob, file.filename ?? basename(file.path));
      }
      init.body = form;
    } else if (spec.bodyFile) {
      init.body = await readFile(spec.bodyFile);
    } else if (typeof spec.bodyText === "string") {
      init.body = spec.bodyText;
    }

    return fetch(spec.url, init);
  }
}
