export type TransportFailureClass =
  | "transient"
  | "rate_limited"
  | "auth"
  | "permission"
  | "input"
  | "not_found"
  | "fatal";

export interface TransportPolicy {
  backoffSeconds: number[];
  maxAttempts: number;
  timeoutMs: number;
}

export interface TransportClassificationInput {
  statusCode?: number;
  responseText?: string;
  errorMessage?: string;
  retryAfterHeader?: string | null;
}

export interface TransportFailureVerdict {
  classification: TransportFailureClass;
  retryable: boolean;
  retryAfterSeconds: number | null;
  message: string;
  statusCode?: number;
}

export interface ReliableRequestSpec {
  method: string;
  url: string;
  headers?: Record<string, string>;
  bodyFile?: string;
  bodyText?: string;
  formFields?: Array<{ name: string; value: string }>;
  formFiles?: Array<{ name: string; path: string; filename?: string }>;
  timeoutMs?: number;
}

export interface ReliableRequestResult {
  ok: boolean;
  classification: TransportFailureClass | "success";
  attemptCount: number;
  statusCode?: number;
  body?: string;
  message?: string;
  retryAfterSeconds?: number | null;
  retryExhausted?: boolean;
  transportError?: string;
  headers?: Record<string, string>;
}
