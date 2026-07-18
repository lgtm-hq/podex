# syntax=docker/dockerfile:1.7@sha256:a57df69d0ea827fb7266491f2813635de6f17269be881f696fbfdf2d83dda33e

# =============================================================================
# Podex backend — production image
# -----------------------------------------------------------------------------
# Multi-stage uv-based build. Stage 1 assembles the frozen virtualenv from
# uv.lock (no dev group, no editable install of first-party sources); stage 2
# copies just the resolved venv + application source onto a slim runtime.
#
# Base images are tag+digest pinned so Renovate can update both together
# (matches org convention). Build context is `backend/` (see docker-ci.yml).
# Migrations are NOT run at build or start time — operators run
# `alembic upgrade head` out-of-band before rolling the new image.
# =============================================================================

# -----------------------------------------------------------------------------
# Stage 1: builder — resolve dependencies with uv into /app/.venv
# -----------------------------------------------------------------------------
FROM python:3.12-slim-bookworm@sha256:d50fb7611f86d04a3b0471b46d7557818d88983fc3136726336b2a4c657aa30b AS builder

# Copy the uv static binaries in from the official uv image (no apt install).
# hadolint ignore=DL3022
COPY --from=ghcr.io/astral-sh/uv:0.11.29@sha256:eb2843a1e56fd9e30c7276ce1a52cba86e64c7b385f5e3279a0e08e02dd058fc /uv /uvx /usr/local/bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    UV_PYTHON_DOWNLOADS=never \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Install project dependencies first (cache-friendly): the layer only busts
# when pyproject.toml / uv.lock change. `--no-install-project` skips the
# first-party package so app source changes don't invalidate this layer.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

# Copy the application source and install the project itself (non-editable so
# the runtime does not need the source tree to import `podex`; we still copy
# `src/podex` and `alembic/` below so operators can invoke alembic via
# `docker exec` if they want to).
COPY src ./src
COPY alembic.ini ./
COPY alembic ./alembic
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable

# -----------------------------------------------------------------------------
# Stage 2: runtime — slim Python + non-root user, no build tooling
# -----------------------------------------------------------------------------
FROM python:3.12-slim-bookworm@sha256:d50fb7611f86d04a3b0471b46d7557818d88983fc3136726336b2a4c657aa30b AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:${PATH}" \
    PODEX_DATABASE_URL="sqlite:///./podex.db"

# Non-root system user for the app. `--no-log-init` avoids a 32 GiB sparse
# lastlog file on some base images (well-documented useradd quirk).
# /app is chowned so the sqlite fallback (`sqlite:///./podex.db`) and any
# other cwd-relative writes work when the container runs as `podex`.
RUN groupadd --system --gid 1001 podex \
    && useradd --system --uid 1001 --gid podex --home-dir /app --no-log-init --shell /usr/sbin/nologin podex \
    && install -d -o podex -g podex /app

WORKDIR /app

# Copy the resolved venv and runtime code from the builder. Ownership is set
# at copy time so we don't need a follow-up recursive chown layer.
COPY --from=builder --chown=podex:podex /app/.venv /app/.venv
COPY --from=builder --chown=podex:podex /app/alembic.ini /app/alembic.ini
COPY --from=builder --chown=podex:podex /app/alembic /app/alembic
COPY --from=builder --chown=podex:podex /app/src /app/src

USER podex

EXPOSE 8000

# Uvicorn from the resolved venv (PATH-first). Migrations are intentionally
# NOT invoked here — see file header.
CMD ["uvicorn", "podex.main:app", "--host", "0.0.0.0", "--port", "8000"]
