# syntax=docker/dockerfile:1.7@sha256:a57df69d0ea827fb7266491f2813635de6f17269be881f696fbfdf2d83dda33e

# =============================================================================
# Podex frontend — production image
# -----------------------------------------------------------------------------
# Multi-stage build:
#   Stage 1 (bun): install locked deps and produce the Astro Node standalone
#                  bundle (`dist/server/entry.mjs`).
#   Stage 2 (node): slim runtime that serves the built bundle as the built-in
#                   `node` user.
#
# `PUBLIC_API_URL` is baked in at build time (Astro/Vite `PUBLIC_*` vars are
# statically inlined into the client bundle during `astro build`). Compose /
# k8s override at build time via `--build-arg PUBLIC_API_URL=...`; the runtime
# fallback below is only for `docker run` sanity.
# =============================================================================

# -----------------------------------------------------------------------------
# Stage 1: builder — bun install + astro build
# -----------------------------------------------------------------------------
FROM oven/bun:1.3.14@sha256:e10577f0db68676a7024391c6e5cb4b879ebd17188ab750cf10024a6d700e5c4 AS builder

WORKDIR /app

# Install dependencies first for cache reuse. Renovate keeps bun.lock in sync
# with package.json; --frozen-lockfile fails the build if they diverge.
COPY package.json bun.lock ./
RUN --mount=type=cache,target=/root/.bun/install/cache,sharing=locked \
    bun install --frozen-lockfile

# Baked-in public API URL. Overridden per-environment at build time. The
# default targets a same-host backend so `docker run` still starts cleanly
# for smoke tests.
ARG PUBLIC_API_URL="http://localhost:8000/api/v2"
ENV PUBLIC_API_URL=${PUBLIC_API_URL} \
    NODE_ENV=production

COPY . .

# `bun run build` == `astro check && astro build`, which emits the node
# standalone server bundle at dist/server/entry.mjs.
RUN bun run build

# -----------------------------------------------------------------------------
# Stage 2: runtime — slim Node LTS, non-root
# -----------------------------------------------------------------------------
FROM node:22.20.0-bookworm-slim@sha256:b21fe589dfbe5cc39365d0544b9be3f1f33f55f3c86c87a76ff65a02f8f5848e AS runtime

WORKDIR /app

ENV HOST=0.0.0.0 \
    PORT=4321 \
    NODE_ENV=production

# `node:*-slim` ships a UID 1000 `node` user out of the box; use it directly.
# Copy the built bundle and node_modules the adapter needs at runtime.
COPY --from=builder --chown=node:node /app/dist ./dist
COPY --from=builder --chown=node:node /app/node_modules ./node_modules
COPY --from=builder --chown=node:node /app/package.json ./package.json

USER node

EXPOSE 4321

CMD ["node", "./dist/server/entry.mjs"]
