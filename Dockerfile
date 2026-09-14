# syntax=docker/dockerfile:1

# One image: the frontend is built here and served statically by FastAPI, so
# there is one port and one container (DESIGN.md, Architecture).
ARG NODE_IMAGE=node:24-trixie-slim
ARG PYTHON_IMAGE=python:3.13-slim-trixie
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.12.13

# The `claude` CLI is the one runtime dependency, and its stream-json output
# shape is a parsing dependency — an unpinned upgrade is a breaking change
# (DESIGN.md, Assumptions). npm's `stable` dist-tag at the time of writing;
# bump deliberately, never via @latest.
ARG CLAUDE_CODE_VERSION=2.1.236
ARG PNPM_VERSION=12.4.1

FROM ${NODE_IMAGE} AS nodedist
FROM ${UV_IMAGE} AS uvdist

# ---- web: build the SPA ----------------------------------------------------
FROM ${NODE_IMAGE} AS web
ARG PNPM_VERSION
RUN npm install -g pnpm@${PNPM_VERSION} && npm cache clean --force
WORKDIR /build

# Manifests first: a source edit must not re-resolve the dependency tree.
COPY frontend/package.json frontend/pnpm-lock.yaml frontend/pnpm-workspace.yaml ./
RUN pnpm install --frozen-lockfile

COPY frontend/ ./
RUN pnpm build

# ---- runtime: python + node (for the CLI) + the built bundle ---------------
FROM ${PYTHON_IMAGE} AS runtime

# git: the agent operates on git worktrees, and the CLI shells out to it.
# ripgrep: the CLI's search tool. openssh-client: git over ssh for push (the
# keypair is generated into the agent-home volume later, stage 4).
# ca-certificates, curl: TLS and script-based installs.
RUN apt-get update && apt-get install -y --no-install-recommends \
      bash ca-certificates curl git ripgrep openssh-client procps tini \
    && rm -rf /var/lib/apt/lists/*

# Node comes in as a pinned artefact from the official image rather than as the
# base: this is a Python service that happens to host an npm-distributed CLI.
COPY --from=nodedist /usr/local /usr/local
COPY --from=uvdist /uv /usr/local/bin/uv

ARG CLAUDE_CODE_VERSION
RUN npm install -g @anthropic-ai/claude-code@${CLAUDE_CODE_VERSION} \
    && npm cache clean --force \
    && claude --version

# The container runs as the host user's uid/gid, or bind mounts are unwritable
# and git refuses /workspace as dubiously owned. A host GID this common (macOS
# "staff" is 20, which Debian gives to "dialout") may already be claimed, so an
# existing group is renamed rather than fought with.
ARG FACTOR_IA_UID=1000
ARG FACTOR_IA_GID=1000
RUN if getent group "$FACTOR_IA_GID" >/dev/null; then \
      groupmod -n factoria "$(getent group "$FACTOR_IA_GID" | cut -d: -f1)"; \
    else \
      groupadd -g "$FACTOR_IA_GID" factoria; \
    fi \
    && if getent passwd "$FACTOR_IA_UID" >/dev/null; then \
         userdel -f "$(getent passwd "$FACTOR_IA_UID" | cut -d: -f1)"; \
       fi \
    && useradd -u "$FACTOR_IA_UID" -g "$FACTOR_IA_GID" -m -d /home/factoria -s /bin/bash factoria

# Where things live, stated by the image rather than inferred at runtime:
#   /app             code and the built bundle
#   /home/factoria   every provider CLI's config and auth — the agent-home volume
#   /workspace       the one surface shared with the host
#   /data            projects registry, run history, event logs
#
# NOTHING THAT AN IMAGE REBUILD MUST BE ABLE TO REPLACE MAY LIVE UNDER
# /home/factoria. A named volume mounts there and shadows the image's copy from
# the first mount onward, freezing anything baked in. That is why the venv is
# /app/.venv and the CLI is a root-owned npm global under /usr/local.
ENV CLAUDE_CONFIG_DIR=/home/factoria/.claude
ENV FACTOR_IA_WORKSPACE=/workspace
ENV FACTOR_IA_DATA=/data
ENV FACTOR_IA_STATIC=/app/static
# The CLI is pinned in the image and runs as non-root: auto-update cannot write
# the global npm prefix and would only produce noise.
ENV DISABLE_AUTOUPDATER=1
# uv installs into this venv rather than a cache dir under $HOME (see above).
ENV UV_PROJECT_ENVIRONMENT=/app/.venv
ENV UV_LINK_MODE=copy
ENV PATH=/app/.venv/bin:$PATH
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Manifests first again, for the same caching reason.
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --locked --no-install-project --no-dev

COPY backend/factoria ./factoria
COPY --from=web /build/dist ./static

# Mount points exist in the image and are owned by the runtime user, because
# Docker seeds a named volume from the image directory on first mount,
# ownership included — an empty mount point would arrive root-owned.
RUN mkdir -p /workspace /data /home/factoria/.claude \
    && chown -R factoria:factoria /app /data /home/factoria

USER factoria
EXPOSE 8000

# tini as a fallback reaper for a bare `docker run`; compose sets `init: true`
# for the same reason — the CLI's children outlive it otherwise.
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["uvicorn", "factoria.main:app", "--host", "0.0.0.0", "--port", "8000"]
