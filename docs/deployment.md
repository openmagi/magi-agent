# Deployment

Deploy Magi Agent as a Python runtime with explicit configuration, secrets, and
storage.

## Deployment Modes

Start local, then widen authority deliberately:

1. Homebrew local server for personal use.
2. Source checkout for development and contribution.
3. Container or service deployment for a controlled self-hosted environment.

For OSS operators, the important contract is the same in every mode: explicit
config, scoped secrets, durable state, health checks, and rollback.

## Deployment checklist

- Pin the runtime image or package version.
- Set provider credentials through a secret manager.
- Mount workspace and durable state where required.
- Keep external tool authority explicit.
- Expose only the endpoints needed by your surface.
- Monitor health, event output, and evidence records.
- Verify rollback before broadening authority.

## Local first

Run the local dashboard and focused tests before deploying. A deployment should
not be considered ready until the exact enabled tools, model path, and evidence
requirements have been verified.

```bash
magi-agent serve --port 8080
open http://localhost:8080/dashboard
curl http://localhost:8080/healthz
```

## Container Notes

The repository includes a `Dockerfile` for container builds. A containerized
deployment should provide:

- runtime environment variables;
- provider and integration secrets;
- a workspace volume when local files or artifacts must persist;
- network policy for the dashboard/API;
- logs and health probes.

Do not bake secrets into images.

## Network Exposure

If the dashboard or API is reachable outside localhost:

- set a strong `GATEWAY_TOKEN`;
- terminate TLS at a trusted proxy or platform layer;
- restrict who can reach operator endpoints;
- keep admin/tool routes behind auth;
- verify redaction before sending events to shared logs.

## Rollback

Keep a rollback path before enabling high-authority tools. A useful rollback
plan names the previous package or image version, the state that must persist,
and any feature flags that can be disabled without redeploying.
