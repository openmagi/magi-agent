# Magi Core Agent — standalone agent runtime.
# See docs/plans/2026-04-19-magi-core-agent-design.md §3.2 / §9.5.
# Runs inside each bot pod on :8080.

FROM node:22-bookworm-slim AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    git \
  && rm -rf /var/lib/apt/lists/*

WORKDIR /build

COPY package.json package-lock.json tsconfig.json ./
RUN NODE_LLAMA_CPP_SKIP_DOWNLOAD=true npm ci --include=dev

COPY src/ ./src/
RUN npm run build


FROM node:22-bookworm-slim

ARG CORE_AGENT_BUILD_SHA=""
ARG CORE_AGENT_IMAGE_REPO=""
ARG CORE_AGENT_IMAGE_TAG=""
ARG CORE_AGENT_EXPECTED_IMAGE_DIGEST=""

# Runtime deps: bash+curl for skill scripts, git for memory repos,
# python3 for bot-authored inline calcs (admin-bot 2026-04-20 POS trace
# exited 127 on `python3` — skills use py for numerical fallback).
# fontconfig + Noto CJK keep bot-authored Korean document render paths honest.
# No Chromium (browser work lives in the browser-worker service). Debian/glibc
# is intentional: the agent-browser release binary is glibc-linked.
RUN apt-get update && apt-get install -y --no-install-recommends \
    bash \
    ca-certificates \
    curl \
    fontconfig \
    fonts-noto-cjk \
    git \
    libreoffice-writer \
    python3 \
    python3-lxml \
    python3-pip \
  && fc-cache -f \
  && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY package.json package-lock.json ./
RUN NODE_LLAMA_CPP_SKIP_DOWNLOAD=true npm ci --omit=dev && npm cache clean --force

COPY --from=builder /build/dist ./dist
COPY runtime/ ./runtime/
COPY apps/web/ ./apps/web/

# Bundled superpowers skills — see
# docs/plans/2026-04-20-superpowers-plugin-design.md. Resolved by
# Agent.ts via `$CORE_AGENT_SUPERPOWERS_DIR` (default `<cwd>/skills/superpowers`,
# which is `/app/skills/superpowers` inside the image).
COPY skills/ ./skills/

# Workspace mount point.
# `/workspace` symlink keeps legacy skills/scripts (and bot-written memory
# files) that hardcode `/workspace/...` working — see
# docs/notes/2026-04-20-core-agent-workspace-symlink-handoff.md.
RUN useradd --create-home --home-dir /home/ocuser --shell /bin/sh ocuser && \
    mkdir -p /home/ocuser/.magi/workspace && \
    ln -s /home/ocuser/.magi/workspace /workspace && \
    chown -R ocuser:ocuser /home/ocuser /workspace

USER ocuser

# `/home/ocuser/.magi/bin` on PATH lets bare-name skill calls
# (e.g. `kb-search.sh`, `agent-run.sh`, `integration.sh`) work the same
# way they do in existing Magi workspaces.
ENV NODE_ENV=production \
    CORE_AGENT_PORT=8080 \
    CORE_AGENT_WORKSPACE=/home/ocuser/.magi/workspace \
    CORE_AGENT_BUILT_BUILD_SHA=${CORE_AGENT_BUILD_SHA} \
    CORE_AGENT_BUILT_IMAGE_REPO=${CORE_AGENT_IMAGE_REPO} \
    CORE_AGENT_BUILT_IMAGE_TAG=${CORE_AGENT_IMAGE_TAG} \
    CORE_AGENT_BUILT_IMAGE_DIGEST=${CORE_AGENT_EXPECTED_IMAGE_DIGEST} \
    PATH=/home/ocuser/.magi/bin:/app/node_modules/.bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

EXPOSE 8080

CMD ["node", "dist/index.js"]
