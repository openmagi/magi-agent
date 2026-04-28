import { describe, expect, it } from "vitest";
import http from "node:http";
import {
  DEFAULT_TRANSPORT_POLICY,
  TransportReliability,
  transportReliabilityStatus,
} from "./TransportReliability.js";

describe("TransportReliability classification", () => {
  it("classifies 503 as transient and retryable", () => {
    const verdict = TransportReliability.classifyFailure({
      statusCode: 503,
      responseText: '{"error":"temporary unavailable"}',
    });

    expect(verdict.classification).toBe("transient");
    expect(verdict.retryable).toBe(true);
  });

  it("classifies 429 as rate_limited and preserves retry-after", () => {
    const verdict = TransportReliability.classifyFailure({
      statusCode: 429,
      responseText: '{"error":"too many requests"}',
      retryAfterHeader: "17",
    });

    expect(verdict.classification).toBe("rate_limited");
    expect(verdict.retryable).toBe(true);
    expect(verdict.retryAfterSeconds).toBe(17);
  });

  it("classifies 401 as auth and non-retryable", () => {
    const verdict = TransportReliability.classifyFailure({
      statusCode: 401,
      responseText: '{"error":"invalid token"}',
    });

    expect(verdict.classification).toBe("auth");
    expect(verdict.retryable).toBe(false);
  });

  it("classifies ECONNRESET as transient", () => {
    const verdict = TransportReliability.classifyFailure({
      errorMessage: "read ECONNRESET while contacting upstream",
    });

    expect(verdict.classification).toBe("transient");
    expect(verdict.retryable).toBe(true);
  });
});

describe("TransportReliability retry planning", () => {
  it("uses the default bounded backoff schedule", () => {
    expect(TransportReliability.nextDelaySeconds({
      policy: DEFAULT_TRANSPORT_POLICY,
      nextAttemptNumber: 2,
      classification: "transient",
    })).toBe(10);
    expect(TransportReliability.nextDelaySeconds({
      policy: DEFAULT_TRANSPORT_POLICY,
      nextAttemptNumber: 3,
      classification: "transient",
    })).toBe(30);
  });

  it("prefers retry-after for rate limited responses", () => {
    expect(TransportReliability.nextDelaySeconds({
      policy: DEFAULT_TRANSPORT_POLICY,
      nextAttemptNumber: 2,
      classification: "rate_limited",
      retryAfterSeconds: 21,
    })).toBe(21);
  });
});

describe("TransportReliability request runner", () => {
  it("retries a transient 503 and eventually succeeds", async () => {
    let attempts = 0;
    const server = http.createServer((req, res) => {
      attempts += 1;
      if (req.url !== "/health") {
        res.writeHead(404);
        res.end("missing");
        return;
      }
      if (attempts < 3) {
        res.writeHead(503, { "content-type": "application/json" });
        res.end('{"error":"warming up"}');
        return;
      }
      res.writeHead(200, { "content-type": "application/json" });
      res.end('{"ok":true}');
    });

    await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", () => resolve()));
    const address = server.address();
    const port = typeof address === "object" && address ? address.port : 0;

    try {
      const result = await new TransportReliability({
        backoffSeconds: [0, 0, 0],
        maxAttempts: 3,
      }).request({
        method: "GET",
        url: `http://127.0.0.1:${port}/health`,
      });

      expect(result.ok).toBe(true);
      expect(result.attemptCount).toBe(3);
      expect(result.body).toBe('{"ok":true}');
      expect(attempts).toBe(3);
    } finally {
      await new Promise<void>((resolve, reject) => {
        server.close((err) => (err ? reject(err) : resolve()));
      });
    }
  });
});

describe("transportReliabilityStatus", () => {
  it("reports helper path and default backoff policy", () => {
    const status = transportReliabilityStatus();
    expect(status).toMatchObject({
      helperPath: "/app/runtime/reliable-request.mjs",
      helperExists: false,
      helperWired: false,
      enabled: true,
      defaultBackoffSeconds: [0, 10, 30],
      maxAttempts: 3,
    });
  });
});
