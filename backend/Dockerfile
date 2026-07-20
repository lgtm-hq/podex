# syntax=docker/dockerfile:1.25@sha256:0adf442eae370b6087e08edc7c50b552d80ddf261576f4ebd6421006b2461f12

# =============================================================================
# Podex backend — production image
# -----------------------------------------------------------------------------
# Multi-stage uv-based build. Stage 1 assembles the frozen virtualenv from
# uv.lock (no dev group, no editable install of first-party sources); stage 2
# copies just the resolved venv + application source onto a slim runtime.
#
# Base images are tag+digest pinned so Renovate can update both together
# (matches org convention). Build context is `backend/` (see docker-ci.yml).
# Migrations are NOT run at build time. At start time the scripts/start.sh
# entrypoint runs `alembic upgrade head` only when AUTO_MIGRATE=1 (or "true")
# is set by the deployment environment; by default operators still run
# migrations out-of-band before rolling the new image.
# =============================================================================

# -----------------------------------------------------------------------------
# Stage 1: builder — resolve dependencies with uv into /app/.venv
# -----------------------------------------------------------------------------
FROM python:3.14-slim-bookworm@sha256:86f975aca15cf04a40b399eebede9aea7c82eae084d1f1a0a6ef6bcaae871a30 AS builder

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
FROM python:3.14-slim-bookworm@sha256:86f975aca15cf04a40b399eebede9aea7c82eae084d1f1a0a6ef6bcaae871a30 AS runtime

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

# Drop the base image's system pip: the app runs from the uv-built venv and
# never installs packages at runtime, and the bundled pip trips the tag-publish
# Trivy gate whenever it lags a fixable CVE (#220).
RUN python -m pip uninstall --yes pip

WORKDIR /app

# Copy the resolved venv and runtime code from the builder. Ownership is set
# at copy time so we don't need a follow-up recursive chown layer.
COPY --from=builder --chown=podex:podex /app/.venv /app/.venv
COPY --from=builder --chown=podex:podex /app/alembic.ini /app/alembic.ini
COPY --from=builder --chown=podex:podex /app/alembic /app/alembic
COPY --from=builder --chown=podex:podex /app/src /app/src

# Entrypoint comes from the build context (not the builder stage) — it is not
# part of the installed package. The executable bit is tracked by git.
COPY --chown=podex:podex scripts/start.sh /app/scripts/start.sh

USER podex

EXPOSE 8000

# start.sh optionally runs migrations (AUTO_MIGRATE=1) then exec's CMD.
# Default CMD serves the API via uvicorn from the resolved venv (PATH-first);
# the scheduler deployable overrides CMD with `python -m podex.scheduler_runner`
# and shares the same entrypoint contract.
ENTRYPOINT ["/app/scripts/start.sh"]
CMD ["uvicorn", "podex.main:app", "--host", "0.0.0.0", "--port", "8000"]
