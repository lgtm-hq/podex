#!/bin/bash
set -e

if [ "${AUTO_MIGRATE:-false}" = "true" ]; then
	echo "Running database migrations..."
	alembic upgrade head
fi

if [ "${SEED_MOCK_DATA:-false}" = "true" ]; then
	echo "Seeding mock data..."
	python scripts/seed_mock_data.py
fi

# Start server
exec uvicorn podex.main:app --host 0.0.0.0 --port 8000
