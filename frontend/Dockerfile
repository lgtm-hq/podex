FROM oven/bun:1 AS builder

WORKDIR /app

# Accept build arg for API URL
ARG PUBLIC_API_URL=http://backend:8000/api/v1
ENV PUBLIC_API_URL=${PUBLIC_API_URL}

# Copy package files
COPY package.json bun.lockb* ./

# Install dependencies
RUN bun install

# Copy application code
COPY . .

# Build the application
RUN bun run build

# Production stage
FROM oven/bun:1-slim

WORKDIR /app

# Copy built application and dependencies
COPY --from=builder /app/dist ./dist
COPY --from=builder /app/node_modules ./node_modules
COPY --from=builder /app/package.json ./

# Expose port
EXPOSE 4321

# Environment variables for the SSR server
ENV HOST=0.0.0.0
ENV PORT=4321

# Run the standalone SSR server
CMD ["bun", "dist/server/entry.mjs"]
