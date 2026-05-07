# Magi Desktop App

Magi's current open-source desktop surface is the installable Magi App PWA.
Run the runtime locally, open `/app`, and install it from a supported browser to
get a desktop workbench backed by the same local HTTP/SSE runtime.

```bash
export MAGI_AGENT_SERVER_TOKEN=$(openssl rand -hex 24)
npx tsx src/cli/index.ts serve --port 8080
```

Then open:

```text
http://localhost:8080/app
```

The desktop PWA shares the web app's constraints:

- provider API keys stay in the runtime config, not in browser-readable app
  storage
- the browser only stores the runtime URL, server token, session key, and
  optional per-turn model override
- runtime state is loaded through documented `/v1/app/*` endpoints
- generated files, crons, background tasks, skills, and native tools are shown
  through local runtime inspection APIs

## Native Packaging Boundary

A signed native desktop package is a packaging layer around the same workbench,
not a different agent runtime. It should stay separate from hosted Magi Cloud
infrastructure unless an operator explicitly opts into managed services.

Open-source native packaging should include:

- a small Tauri, Electron, or platform WebView shell
- local runtime discovery and connection setup
- OS credential storage for local server tokens if needed
- clear settings for provider adapter choice and workspace root

It should not include:

- hosted Magi Cloud auth, billing, entitlements, or customer data contracts
- private download, signing, auto-update, or telemetry infrastructure
- managed social-browser credential broker flows
- production admin or operator backoffice routes

The PWA remains the default zero-dependency desktop path until native packaging
has a separate release plan and security review.
