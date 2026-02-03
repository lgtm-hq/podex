# Podex - Podcast Media Index

A web application that indexes and tracks all media (books, movies, studies, etc.) mentioned in podcast episodes, starting with the Joe Rogan Experience.

## Features

- **Searchable Database**: Find books, movies, documentaries, and other media mentioned in podcasts
- **Timestamp Links**: Jump directly to the moment in the episode where media was discussed
- **Statistics**: Track most-mentioned media, trends, and analytics
- **Theme Support**: 9 themes via turbo-themes (Catppuccin, Dracula, GitHub, Bulma)
- **Responsive Design**: Works on desktop and mobile

## Tech Stack

### Frontend

- **Astro** - Static site generation with partial hydration
- **React** - Interactive components (islands)
- **Tailwind CSS** - Utility-first styling
- **TypeScript** - Type safety
- **@turbocoder13/turbo-themes** - Theme system
- **Bun** - JavaScript runtime & package manager

### Backend

- **Python 3.11+** - Runtime
- **FastAPI** - REST API framework
- **SQLAlchemy** - ORM
- **SQLite** - Database (upgradeable to PostgreSQL)
- **Pydantic** - Data validation
- **Alembic** - Database migrations
- **uv** - Python package manager

## Quick Start

### Using Make (Recommended)

```bash
# Install dependencies
make install

# Seed database and start both servers
make seed
make dev
```

### Using Docker (Production-like)

```bash
# Build and start all services
make docker-build
make docker-up

# View logs
make docker-logs

# Stop services
make docker-down
```

### Manual Setup

#### Backend Setup

```bash
cd backend
uv sync
uv run python scripts/seed_mock_data.py
uv run uvicorn podex.main:app --reload
```

#### Frontend Setup

```bash
cd frontend
bun install
bun run dev
```

## Makefile Commands

```bash
make help           # Show all available commands

# Development
make install        # Install all dependencies
make dev            # Start both dev servers
make dev-backend    # Start backend only
make dev-frontend   # Start frontend only
make seed           # Seed database with mock data

# Docker
make docker-build   # Build Docker images
make docker-up      # Start services
make docker-down    # Stop services
make docker-logs    # View logs
make docker-clean   # Remove volumes and images

# Quality
make test           # Run all tests
make lint           # Run linters
make format         # Format code

# Database
make db-migrate     # Run migrations
make db-reset       # Reset and reseed database
```

## Project Structure

```text
podex/
├── backend/
│   ├── src/podex/
│   │   ├── api/          # API endpoints
│   │   ├── models/       # SQLAlchemy models
│   │   ├── schemas/      # Pydantic schemas
│   │   └── main.py       # FastAPI app
│   ├── scripts/          # Utility scripts
│   └── tests/            # Backend tests
│
├── frontend/
│   └── src/
│       ├── components/   # Astro & React components
│       ├── layouts/      # Page layouts
│       ├── lib/          # API client & types
│       ├── pages/        # Route pages
│       └── styles/       # Global CSS
│
├── data/
│   └── mock/             # Mock data JSON files
│
├── Makefile
├── docker-compose.yml
└── README.md
```

## API Endpoints

| Method | Endpoint                           | Description                |
| ------ | ---------------------------------- | -------------------------- |
| GET    | `/api/v1/podcasts`                 | List all podcasts          |
| GET    | `/api/v1/podcasts/{slug}`          | Get podcast by slug        |
| GET    | `/api/v1/podcasts/{slug}/episodes` | Get podcast episodes       |
| GET    | `/api/v1/episodes`                 | List all episodes          |
| GET    | `/api/v1/episodes/{id}`            | Get episode by ID          |
| GET    | `/api/v1/episodes/{id}/mentions`   | Get mentions in episode    |
| GET    | `/api/v1/media`                    | List all media (paginated) |
| GET    | `/api/v1/media/{id}`               | Get media detail           |
| GET    | `/api/v1/media/search?q=`          | Search media               |
| GET    | `/api/v1/media/top`                | Top mentioned media        |
| GET    | `/api/v1/stats/overview`           | Overall statistics         |
| GET    | `/api/v1/stats/by-type`            | Stats by media type        |

## Themes

Podex includes 9 themes from turbo-themes:

- **Bulma**: Light, Dark
- **Catppuccin**: Latte, Frappé, Macchiato, Mocha
- **Dracula**: Dark
- **GitHub**: Light, Dark

Use the theme selector in the header to switch themes.

## Mock Data

The project includes realistic mock data:

- 1 podcast (Joe Rogan Experience)
- 50 episodes
- 100 media items (books, movies, documentaries, TV shows)
- 210 mentions with timestamps and context

## Development

### Running Tests

```bash
make test
# or individually
make test-backend
make test-frontend
```

### Database Migrations

```bash
make db-migrate              # Run migrations
make db-revision             # Create new migration
make db-reset                # Reset and reseed
```

### Code Quality

```bash
make lint    # Run linters
make format  # Format code
```

## Environment Variables

### Backend (.env)

```bash
DATABASE_URL=sqlite:///./podex.db
DEBUG=false
CORS_ORIGINS=["http://localhost:4321"]
```

### Frontend (.env)

```bash
PUBLIC_API_URL=http://localhost:8000/api/v1
```

## License

MIT
