FROM python:3.12-slim

ARG CORE_AGENT_BUILD_SHA=""
ARG CORE_AGENT_IMAGE_REPO=""
ARG CORE_AGENT_IMAGE_TAG=""
ARG CORE_AGENT_EXPECTED_IMAGE_DIGEST=""

WORKDIR /app

COPY pyproject.toml README.md ./
COPY magi_agent/ ./magi_agent/

RUN python -m pip install --no-cache-dir --upgrade pip \
  && python -m pip install --no-cache-dir ".[cli,composio,providers]"

# Dedicated non-root runtime user. /app stays root-owned read-only (the
# runtime never writes it; hosted pods run readOnlyRootFilesystem with the
# PVC as the only writable mount). The home directory is writable so plain
# docker runs can read/write ~/.magi (e.g. config.toml). Hosted k8s pods
# keep enforcing their own securityContext (runAsNonRoot/fsGroup); this
# aligns the image default with that posture.
RUN groupadd --gid 10001 magi \
  && useradd --uid 10001 --gid 10001 --create-home --home-dir /home/magi \
       --shell /usr/sbin/nologin magi

ENV CORE_AGENT_PORT=8080 \
    CORE_AGENT_RUNTIME_ENGINE=adk-python \
    CORE_AGENT_BUILT_BUILD_SHA=${CORE_AGENT_BUILD_SHA} \
    CORE_AGENT_BUILT_IMAGE_REPO=${CORE_AGENT_IMAGE_REPO} \
    CORE_AGENT_BUILT_IMAGE_TAG=${CORE_AGENT_IMAGE_TAG} \
    CORE_AGENT_BUILT_IMAGE_DIGEST=${CORE_AGENT_EXPECTED_IMAGE_DIGEST}

EXPOSE 8080

USER magi

CMD ["python", "-m", "magi_agent"]
