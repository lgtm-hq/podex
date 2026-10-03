# syntax=docker/dockerfile:1.27@sha256:4edf897a3ffa55b89f906fc8cc78afdb3f1834cc9c7083565e611a8a7d5fe99e

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
FROM python:3.14-slim-trixie@sha256:0741d101873c12ab927e6f8653feb8862b9bd58771177acb1b885b95141f91b4 AS builder

# Copy the uv static binaries in from the official uv image (no apt install).
# hadolint ignore=DL3022
COPY --from=ghcr.io/astral-sh/uv:0.12.23@sha256:61d393e44e249f2e4b526b6c7ddcecce245946826e608e11c93ad4f5bba55b21 /uv /uvx /usr/local/bin/

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
FROM python:3.14-slim-trixie@sha256:0741d101873c12ab927e6f8653feb8862b9bd58771177acb1b885b95141f91b4 AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:${PATH}" \
    PODEX_DATABASE__URL="sqlite:///./podex.db"

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

# slim-trixie snapshot ships libpcre2-8-0 10.46-1~deb13u2; CVE-2026-103111
# is fixed in 10.46-1~deb13u3 from trixie-security. Upgrade without an
# exact version pin so the next security revision still builds, then
# assert the known-fixed floor.
COPY scripts/upgrade-min-debs.sh /tmp/upgrade-min-debs.sh
RUN /tmp/upgrade-min-debs.sh libpcre2-8-0=10.46-1~deb13u3 \
    && rm -f /tmp/upgrade-min-debs.sh

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
