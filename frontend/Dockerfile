# syntax=docker/dockerfile:1.27@sha256:4edf897a3ffa55b89f906fc8cc78afdb3f1834cc9c7083565e611a8a7d5fe99e

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
FROM oven/bun:1.4.2@sha256:9114c058aeae42162ee16dd5084b95fe9473970bb6bcb5b232ab1630f0546895 AS builder

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
FROM node:24.21.0-trixie-slim@sha256:8ec5d7557396cfe32d21c3f9c13072355ceab22b584578ca4bb28af31120cffe AS runtime

WORKDIR /app

ENV HOST=0.0.0.0 \
    PORT=4321 \
    NODE_ENV=production

# slim-trixie snapshot ships libpcre2-8-0 10.46-1~deb13u2 and
# libssl3t64 3.5.7-1~deb13u2. CVE-2026-103111 / CVE-2026-84782 are
# fixed in trixie-security. Upgrade without exact version pins so the
# next security revision still builds, then assert the known-fixed
# floors. The runtime CMD is `node ./dist/server/entry.mjs` and never
# calls npm/npx/corepack, so drop those bundles and their HIGH CVEs.
COPY scripts/upgrade-min-debs.sh /tmp/upgrade-min-debs.sh
RUN /tmp/upgrade-min-debs.sh \
        libpcre2-8-0=10.46-1~deb13u3 \
        libssl3t64=3.5.7-1~deb13u3 \
    && rm -f /tmp/upgrade-min-debs.sh \
    && rm -rf \
        /usr/local/lib/node_modules/npm \
        /usr/local/lib/node_modules/corepack \
        /usr/local/bin/npm \
        /usr/local/bin/npx \
        /usr/local/bin/corepack

# `node:*-slim` ships a UID 1000 `node` user out of the box; use it directly.
# Copy the built bundle and node_modules the adapter needs at runtime.
COPY --from=builder --chown=node:node /app/dist ./dist
COPY --from=builder --chown=node:node /app/node_modules ./node_modules
COPY --from=builder --chown=node:node /app/package.json ./package.json

USER node

EXPOSE 4321

CMD ["node", "./dist/server/entry.mjs"]
