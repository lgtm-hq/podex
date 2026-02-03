FROM python:3.11-slim

WORKDIR /app

# Install system dependencies and uv
# hadolint ignore=DL3008,DL3013
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir uv

# Copy all application code first (needed for editable install)
COPY . .

# Create data directory, install dependencies, and make start script executable
RUN mkdir -p /app/data \
    && uv pip install --system -e . \
    && chmod +x /app/scripts/start.sh

# Expose port
EXPOSE 8000

CMD ["/app/scripts/start.sh"]
