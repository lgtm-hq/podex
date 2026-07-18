# syntax=docker/dockerfile:1

FROM oven/bun:1.2.19 AS builder

WORKDIR /app

COPY package.json bun.lock ./
RUN bun install --frozen-lockfile

COPY . .

ARG PUBLIC_API_URL=http://api:8000/api/v2
ENV PUBLIC_API_URL=${PUBLIC_API_URL}

RUN bun run build

FROM node:22-bookworm-slim AS runner

WORKDIR /app

ENV HOST=0.0.0.0 \
    PORT=4321 \
    NODE_ENV=production

COPY --from=builder /app/dist ./dist

RUN chown -R node:node /app

USER node

EXPOSE 4321

CMD ["node", "./dist/server/entry.mjs"]
