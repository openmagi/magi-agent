# Magi Desktop

This is a small Tauri v2 shell for the open-source Magi App. It does not bundle
Magi Cloud services, hosted auth, billing, or private update infrastructure. The
desktop window connects to a local Magi runtime at:

```text
http://127.0.0.1:8080/app
```

## Prerequisites

- Node.js 22+
- Rust stable and Cargo
- Tauri platform dependencies for your OS
- A running local Magi runtime

## Local Development

From the repository root:

```bash
npm install
npm --prefix apps/desktop install
npm --prefix apps/desktop run check
export MAGI_AGENT_SERVER_TOKEN=$(openssl rand -hex 24)
npx tsx src/cli/index.ts serve --port 8080
```

Then, in another terminal:

```bash
npm run desktop:dev
```

## Build An Installer

Keep the local runtime URL in `src-tauri/tauri.conf.json` pointed at the port
you want the packaged shell to use, then run:

```bash
npm --prefix apps/desktop install
npm run desktop:build
```

The output location is managed by Tauri under `apps/desktop/src-tauri/target`.
Users can change provider choice, local model server, and workspace path in
`magi-agent.yaml`; the desktop package only wraps the local web workbench.
