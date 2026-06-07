# API

`magi-agent serve` exposes the local runtime API and dashboard.

## Local dashboard

```bash
magi-agent serve --port 8080
open http://localhost:8080/dashboard
```

The dashboard is a local operator surface. It uses the same runtime process as
the API and should be protected before network exposure.

## Chat

The local dashboard uses the same runtime process. Depending on configuration,
chat endpoints can stream public events and answer deltas.

Streaming chat routes are feature-gated:

```bash
export MAGI_STREAMING_CHAT=1
```

Primary streaming routes:

```text
POST /v1/chat/stream
POST /v1/chat/control-response
POST /v1/chat/cancel
```

Use bearer auth with the runtime gateway token when the route requires
authorization:

```bash
curl -N http://localhost:8080/v1/chat/stream \
  -H "Authorization: Bearer $GATEWAY_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"sessionId":"local","messages":[{"role":"user","content":"Check runtime health."}]}'
```

The stream emits `event: agent` SSE frames and ends with `data: [DONE]`.
See [Streaming events](streaming-events.md) for public event classes and
sanitization rules.

## Health

Use health endpoints to confirm runtime status before debugging model behavior.

```bash
curl http://localhost:8080/health
curl http://localhost:8080/healthz
```

`/healthz` returns HTTP `503` when the runtime reports a non-ok readiness state.

## Tool Admin

Tool admin routes expose local tool metadata and stats for the dashboard:

```text
GET /v1/admin/tools
GET /v1/admin/tools/stats
GET /v1/admin/tools/{name}
```

Treat these as operator endpoints. Do not expose them publicly without auth,
network controls, and redaction review.

## Auth and Exposure

If you bind the server outside trusted localhost access, protect it with
`GATEWAY_TOKEN` and a trusted network boundary. Do not reuse model provider keys
as server tokens.
