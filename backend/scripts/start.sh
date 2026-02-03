#!/bin/bash
set -e

# Run seed if database doesn't exist
if [ ! -f /app/data/podex.db ]; then
	echo "Seeding database..."
	python scripts/seed_mock_data.py
fi

# Start server
exec uvicorn podex.main:app --host 0.0.0.0 --port 8000
