# API

`magi-agent serve` exposes the local runtime API and dashboard.

## Local dashboard

```bash
magi-agent serve --port 8080
open http://localhost:8080/dashboard
```

## Chat

The local dashboard uses the same runtime process. Depending on configuration,
chat endpoints can stream public events and answer deltas.

## Health

Use health endpoints to confirm runtime status before debugging model behavior.

```bash
curl http://localhost:8080/health
curl http://localhost:8080/healthz
```

If you bind the server outside trusted localhost access, protect it with
`MAGI_AGENT_SERVER_TOKEN`.

