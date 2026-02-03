# Podex - Podcast Media Index
# Run `just` to see all available commands

set dotenv-load := true

# Default recipe - show help
default:
    @just --list

# =============================================================================
# Development
# =============================================================================

# Install all dependencies (backend + frontend)
install: install-backend install-frontend

# Install backend dependencies
install-backend:
    @echo "Installing backend dependencies..."
    cd backend && uv sync --extra dev

# Install frontend dependencies
install-frontend:
    @echo "Installing frontend dependencies..."
    cd frontend && bun install

# Start both development servers
dev:
    @echo "Starting development servers..."
    just dev-backend & just dev-frontend

# Start backend development server
dev-backend:
    @echo "Starting backend server..."
    cd backend && uv run uvicorn podex.main:app --reload --host 0.0.0.0 --port 8000

# Start frontend development server
dev-frontend:
    @echo "Starting frontend server..."
    cd frontend && bun run dev

# =============================================================================
# Transcription Pipeline
# =============================================================================

# Transcribe with MLX-Whisper (Apple Silicon, fast, free)
[group('transcription')]
transcribe-mlx podcast="" episode_min="" episode_max="" limit="":
    #!/usr/bin/env bash
    echo "Transcribing with MLX-Whisper (large-v3)..."
    cd backend && \
    WHISPER_BACKEND=mlx_whisper \
    WHISPER_MODEL=large-v3 \
    TRANSCRIPT_PODCAST_SLUG="{{podcast}}" \
    TRANSCRIPT_EPISODE_MIN="{{episode_min}}" \
    TRANSCRIPT_EPISODE_MAX="{{episode_max}}" \
    TRANSCRIPT_LIMIT="{{limit}}" \
    uv run python scripts/ingest_transcripts_whisper.py

# Transcribe with faster-whisper (CPU, free, slower)
[group('transcription')]
transcribe-local model="base" podcast="" episode_min="" episode_max="" limit="":
    #!/usr/bin/env bash
    echo "Transcribing with faster-whisper ({{model}})..."
    cd backend && \
    WHISPER_BACKEND=faster_whisper \
    WHISPER_MODEL={{model}} \
    TRANSCRIPT_PODCAST_SLUG="{{podcast}}" \
    TRANSCRIPT_EPISODE_MIN="{{episode_min}}" \
    TRANSCRIPT_EPISODE_MAX="{{episode_max}}" \
    TRANSCRIPT_LIMIT="{{limit}}" \
    uv run python scripts/ingest_transcripts_whisper.py

# Transcribe with Groq API (fast, cheap ~$0.04/hour)
[group('transcription')]
transcribe-groq podcast="" episode_min="" episode_max="" limit="":
    #!/usr/bin/env bash
    echo "Transcribing with Groq API..."
    cd backend && \
    WHISPER_BACKEND=groq \
    TRANSCRIPT_PODCAST_SLUG="{{podcast}}" \
    TRANSCRIPT_EPISODE_MIN="{{episode_min}}" \
    TRANSCRIPT_EPISODE_MAX="{{episode_max}}" \
    TRANSCRIPT_LIMIT="{{limit}}" \
    uv run python scripts/ingest_transcripts_whisper.py

# Transcribe with OpenAI API (reliable, ~$0.36/hour)
[group('transcription')]
transcribe-openai podcast="" episode_min="" episode_max="" limit="":
    #!/usr/bin/env bash
    echo "Transcribing with OpenAI API..."
    cd backend && \
    WHISPER_BACKEND=openai \
    TRANSCRIPT_PODCAST_SLUG="{{podcast}}" \
    TRANSCRIPT_EPISODE_MIN="{{episode_min}}" \
    TRANSCRIPT_EPISODE_MAX="{{episode_max}}" \
    TRANSCRIPT_LIMIT="{{limit}}" \
    uv run python scripts/ingest_transcripts_whisper.py

# Transcribe JRE episode(s) with MLX - single episode or range
# Usage: just jre-transcribe 171        (single episode)
#        just jre-transcribe 171 180    (episodes 171-180)
# Note: Earliest JRE episode on YouTube is #171
[group('transcription')]
jre-transcribe episode episode_max="":
    #!/usr/bin/env bash
    if [ -z "{{episode_max}}" ]; then
        echo "Transcribing JRE episode {{episode}}..."
        just transcribe-mlx jre {{episode}} {{episode}}
    else
        echo "Transcribing JRE episodes {{episode}}-{{episode_max}}..."
        just transcribe-mlx jre {{episode}} {{episode_max}}
    fi

# =============================================================================
# Claude Processing (costs tokens)
# =============================================================================

# Clean up transcripts with Claude (fix proper nouns, terms)
[group('claude')]
cleanup-transcripts podcast="" episode_min="" episode_max="" limit="":
    #!/usr/bin/env bash
    echo "Cleaning transcripts with Claude..."
    cd backend && \
    CLEANUP_PODCAST_SLUG="{{podcast}}" \
    CLEANUP_EPISODE_MIN="{{episode_min}}" \
    CLEANUP_EPISODE_MAX="{{episode_max}}" \
    CLEANUP_LIMIT="{{limit}}" \
    uv run python scripts/cleanup_transcripts_llm.py

# Extract media mentions with Claude
[group('claude')]
extract-mentions podcast="" episode_min="" episode_max="" limit="":
    #!/usr/bin/env bash
    echo "Extracting media mentions with Claude..."
    cd backend && \
    EXTRACTION_PODCAST_SLUG="{{podcast}}" \
    EXTRACTION_EPISODE_MIN="{{episode_min}}" \
    EXTRACTION_EPISODE_MAX="{{episode_max}}" \
    EXTRACTION_LIMIT="{{limit}}" \
    uv run python scripts/extract_mentions_llm.py

# Enrich media with external data (cover art, ratings, etc.)
[group('claude')]
enrich-media limit="" type="" *args:
    #!/usr/bin/env bash
    echo "Enriching media with external data..."
    cd backend && uv run python scripts/enrich_media.py \
        ${limit:+--limit $limit} \
        ${type:+--type $type} \
        {{args}}

# Clean JRE episode(s) with Claude - single episode or range
# Usage: just jre-cleanup 171        (single episode)
#        just jre-cleanup 171 180    (episodes 171-180)
[group('claude')]
jre-cleanup episode episode_max="":
    #!/usr/bin/env bash
    if [ -z "{{episode_max}}" ]; then
        echo "Cleaning JRE episode {{episode}}..."
        just cleanup-transcripts jre {{episode}} {{episode}}
    else
        echo "Cleaning JRE episodes {{episode}}-{{episode_max}}..."
        just cleanup-transcripts jre {{episode}} {{episode_max}}
    fi

# =============================================================================
# Full Pipeline
# =============================================================================

# Process a single YouTube URL through the full pipeline
# Usage: just process-url "https://www.youtube.com/watch?v=VIDEO_ID"
#        just process-url "https://youtu.be/VIDEO_ID"
#        just process-url "VIDEO_ID"
#        just process-url "VIDEO_ID" --podcast jre
[group('pipeline')]
process-url url *args:
    cd backend && uv run python scripts/process_url.py "{{url}}" {{args}}

# Run full pipeline: transcribe + cleanup + extract (specify podcast and range)
[group('pipeline')]
pipeline podcast episode_min="" episode_max="":
    @echo "Running full pipeline for {{podcast}}..."
    just transcribe-mlx {{podcast}} {{episode_min}} {{episode_max}}
    just cleanup-transcripts {{podcast}} {{episode_min}} {{episode_max}}
    just extract-mentions

# Run full JRE pipeline - single episode or range
# Usage: just jre-pipeline 171        (single episode)
#        just jre-pipeline 171 180    (episodes 171-180)
[group('pipeline')]
jre-pipeline episode episode_max="":
    #!/usr/bin/env bash
    if [ -z "{{episode_max}}" ]; then
        echo "Running full pipeline for JRE episode {{episode}}..."
        just pipeline jre {{episode}} {{episode}}
    else
        echo "Running full pipeline for JRE episodes {{episode}}-{{episode_max}}..."
        just pipeline jre {{episode}} {{episode_max}}
    fi

# =============================================================================
# Scraping & Comparison
# =============================================================================

# Scrape transcripts from podscripts.co (saves to JSON)
[group('scraping')]
scrape-podscripts episode_min="" episode_max="" limit="":
    #!/usr/bin/env bash
    echo "Scraping from podscripts.co..."
    cd backend && \
    SCRAPE_EPISODE_MIN="{{episode_min}}" \
    SCRAPE_EPISODE_MAX="{{episode_max}}" \
    SCRAPE_LIMIT="{{limit}}" \
    uv run python scripts/scrape_podscripts.py

# Scrape and store transcripts in database
[group('scraping')]
scrape-podscripts-db episode_min="" episode_max="" limit="":
    #!/usr/bin/env bash
    echo "Scraping from podscripts.co and storing to DB..."
    cd backend && \
    SCRAPE_STORE_DB=true \
    SCRAPE_EPISODE_MIN="{{episode_min}}" \
    SCRAPE_EPISODE_MAX="{{episode_max}}" \
    SCRAPE_LIMIT="{{limit}}" \
    uv run python scripts/scrape_podscripts.py

# Scrape JRE episodes (shorthand)
[group('scraping')]
jre-scrape episode episode_max="":
    #!/usr/bin/env bash
    if [ -z "{{episode_max}}" ]; then
        just scrape-podscripts {{episode}} {{episode}}
    else
        just scrape-podscripts {{episode}} {{episode_max}}
    fi

# Compare transcripts from different sources
[group('scraping')]
compare-transcripts source_a="whisper" source_b="podscripts" episode_min="" episode_max="":
    #!/usr/bin/env bash
    echo "Comparing {{source_a}} vs {{source_b}} transcripts..."
    cd backend && \
    COMPARE_SOURCE_A="{{source_a}}" \
    COMPARE_SOURCE_B="{{source_b}}" \
    COMPARE_EPISODE_MIN="{{episode_min}}" \
    COMPARE_EPISODE_MAX="{{episode_max}}" \
    uv run python scripts/compare_transcripts.py

# Compare JRE transcripts (shorthand)
[group('scraping')]
jre-compare episode episode_max="":
    #!/usr/bin/env bash
    if [ -z "{{episode_max}}" ]; then
        just compare-transcripts whisper podscripts {{episode}} {{episode}}
    else
        just compare-transcripts whisper podscripts {{episode}} {{episode_max}}
    fi

# =============================================================================
# YouTube Ingestion
# =============================================================================

# Fetch episode metadata from YouTube
[group('ingestion')]
ingest-youtube:
    @echo "Fetching episode metadata from YouTube..."
    cd backend && uv run python scripts/ingest_youtube_episodes.py

# =============================================================================
# Podcast Management
# =============================================================================

# Browse podscripts.co catalog
[group('podcasts')]
browse-podscripts *args:
    cd backend && uv run python scripts/browse_podscripts.py {{args}}

# List all tracked podcasts
[group('podcasts')]
list-podcasts:
    cd backend && uv run python scripts/manage_podcasts.py list

# Add podcast to watchlist
# Examples:
#   just add-podcast --podscripts lex-fridman-podcast
#   just add-podcast --rss "https://feeds.example.com/podcast.rss"
#   just add-podcast --spotify 79CkJF3UJTHFV8Dse3Ez0P
[group('podcasts')]
add-podcast *args:
    cd backend && uv run python scripts/manage_podcasts.py add {{args}}

# Activate a watchlist podcast for processing
[group('podcasts')]
activate-podcast slug:
    cd backend && uv run python scripts/manage_podcasts.py activate {{slug}}

# Pause an active podcast
[group('podcasts')]
pause-podcast slug:
    cd backend && uv run python scripts/manage_podcasts.py pause {{slug}}

# Sync podcasts from config file
[group('podcasts')]
sync-podcasts:
    cd backend && uv run python scripts/manage_podcasts.py sync

# Export current podcasts to YAML
[group('podcasts')]
export-podcasts:
    cd backend && uv run python scripts/manage_podcasts.py export

# =============================================================================
# Discovery & Processing
# =============================================================================

# Discover new episodes for all active podcasts
[group('discovery')]
discover *args:
    cd backend && uv run python scripts/discover_episodes.py {{args}}

# Discover episodes for a specific podcast
[group('discovery')]
discover-podcast slug:
    cd backend && uv run python scripts/discover_episodes.py --podcast {{slug}}

# =============================================================================
# Status & Monitoring
# =============================================================================

# Show full status report for all podcasts
[group('status')]
status-all:
    cd backend && uv run python scripts/status_report.py

# Show transcription status for a podcast
[group('status')]
status podcast="":
    #!/usr/bin/env bash
    if [ -z "{{podcast}}" ]; then
        cd backend && uv run python scripts/status_report.py
    else
        cd backend && uv run python scripts/status_report.py --podcast {{podcast}}
    fi

# Show status for JRE
[group('status')]
status-jre:
    just status jre

# =============================================================================
# Search (Meilisearch)
# =============================================================================

# Sync database to Meilisearch (incremental)
[group('search')]
search-sync index="all":
    @echo "Syncing {{index}} to Meilisearch..."
    cd backend && uv run python scripts/sync_search.py --index {{index}}

# Full reindex of Meilisearch (deletes existing documents first)
[group('search')]
search-reindex index="all":
    @echo "Full reindex of {{index}} in Meilisearch..."
    cd backend && uv run python scripts/sync_search.py --index {{index}} --full

# Show Meilisearch index statistics
[group('search')]
search-stats:
    @echo "Meilisearch statistics..."
    cd backend && uv run python scripts/sync_search.py --stats

# =============================================================================
# Database
# =============================================================================

# Run database migrations
[group('database')]
db-migrate:
    @echo "Running database migrations..."
    cd backend && uv run alembic upgrade head

# Create new migration
[group('database')]
db-revision message:
    @echo "Creating new migration..."
    cd backend && uv run alembic revision --autogenerate -m "{{message}}"

# Reset database (WARNING: destroys data)
[group('database')]
db-reset:
    @echo "Resetting database..."
    rm -f backend/podex.db
    just db-migrate

# Start PostgreSQL container
[group('database')]
db-start:
    @echo "Starting PostgreSQL..."
    docker compose up -d db

# Stop PostgreSQL container
[group('database')]
db-stop:
    @echo "Stopping PostgreSQL..."
    docker compose stop db

# Connect to PostgreSQL shell
[group('database')]
db-shell:
    docker compose exec db psql -U podex -d podex

# =============================================================================
# Docker
# =============================================================================

# Build Docker images
[group('docker')]
docker-build:
    @echo "Building Docker images..."
    docker compose build

# Start Docker services
[group('docker')]
docker-up:
    @echo "Starting Docker services..."
    docker compose up -d

# Stop Docker services
[group('docker')]
docker-down:
    @echo "Stopping Docker services..."
    docker compose down

# Show Docker logs
[group('docker')]
docker-logs:
    docker compose logs -f

# Restart Docker services
[group('docker')]
docker-restart: docker-down docker-up

# Clean Docker resources
[group('docker')]
docker-clean:
    @echo "Cleaning Docker resources..."
    docker compose down -v --rmi local

# =============================================================================
# Testing
# =============================================================================

# Run all tests
[group('testing')]
test: test-backend test-frontend

# Run backend tests
[group('testing')]
test-backend:
    @echo "Running backend tests..."
    cd backend && uv run pytest -v

# Run frontend tests
[group('testing')]
test-frontend:
    @echo "Running frontend tests..."
    cd frontend && bun run test:run

# Run frontend tests with coverage
[group('testing')]
test-frontend-coverage:
    @echo "Running frontend tests with coverage..."
    cd frontend && bun run test:coverage

# Run frontend type check
[group('testing')]
check-frontend:
    @echo "Running frontend type check..."
    cd frontend && bun run check

# =============================================================================
# Code Quality
# =============================================================================

# Run all linters
[group('quality')]
lint: lint-backend lint-frontend

# Lint backend
[group('quality')]
lint-backend:
    @echo "Linting backend..."
    cd backend && uv run ruff check src/

# Lint frontend
[group('quality')]
lint-frontend:
    @echo "Linting frontend..."
    cd frontend && bun run check

# Format code
[group('quality')]
format:
    @echo "Formatting backend..."
    cd backend && uv run ruff format src/

# =============================================================================
# Build
# =============================================================================

# Build everything for production
[group('build')]
build: build-backend build-frontend

# Build backend
[group('build')]
build-backend:
    @echo "Building backend..."
    cd backend && uv build

# Build frontend
[group('build')]
build-frontend:
    @echo "Building frontend..."
    cd frontend && bun run build

# =============================================================================
# Cleanup
# =============================================================================

# Clean build artifacts
clean:
    @echo "Cleaning build artifacts..."
    rm -rf backend/dist backend/build backend/*.egg-info
    rm -rf frontend/dist frontend/.astro
    find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
    find . -type f -name "*.pyc" -delete 2>/dev/null || true

# Clean everything (including venv, node_modules, database)
clean-all: clean docker-clean
    rm -rf backend/.venv
    rm -rf frontend/node_modules
    rm -f backend/podex.db
