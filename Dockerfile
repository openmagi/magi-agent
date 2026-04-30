# Clawy Core Agent — standalone agent runtime.
# See docs/plans/2026-04-19-clawy-core-agent-design.md §3.2 / §9.5.
# Runs inside each bot pod on :8080.

FROM node:22-alpine AS builder

WORKDIR /build

COPY package.json tsconfig.json ./
RUN npm install --include=dev

COPY src/ ./src/
RUN npm run build


FROM node:22-alpine

# Runtime deps: bash+curl for skill scripts, git for memory repos,
# python3 for bot-authored inline calcs (admin-bot 2026-04-20 POS trace
# exited 127 on `python3` — skills use py for numerical fallback).
# No Chromium (browser work lives in the browser-worker service).
RUN apk add --no-cache bash curl git ca-certificates python3 py3-pip

WORKDIR /app

COPY package.json ./
RUN npm install --omit=dev && npm cache clean --force

COPY --from=builder /build/dist ./dist

# Bundled superpowers skills — see
# docs/plans/2026-04-20-superpowers-plugin-design.md. Resolved by
# Agent.ts via `$CORE_AGENT_SUPERPOWERS_DIR` (default `<cwd>/skills/superpowers`,
# which is `/app/skills/superpowers` inside the image).
COPY skills/ ./skills/

# Workspace mount point.
# `/workspace` symlink keeps legacy skills/scripts (and bot-written memory
# files) that hardcode `/workspace/...` working — see
# docs/notes/2026-04-20-core-agent-workspace-symlink-handoff.md.
RUN adduser -D -h /home/ocuser ocuser && \
    mkdir -p /home/ocuser/.clawy/workspace && \
    ln -s /home/ocuser/.clawy/workspace /workspace && \
    chown -R ocuser:ocuser /home/ocuser /workspace

USER ocuser

# `/home/ocuser/.clawy/bin` on PATH lets bare-name skill calls
# (e.g. `kb-search.sh`, `agent-run.sh`, `integration.sh`) work the same
# way they do in existing Clawy workspaces.
ENV NODE_ENV=production \
    CORE_AGENT_PORT=8080 \
    CORE_AGENT_WORKSPACE=/home/ocuser/.clawy/workspace \
    PATH=/home/ocuser/.clawy/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

EXPOSE 8080

CMD ["node", "dist/index.js"]
