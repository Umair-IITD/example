#!/bin/bash
# run_local.sh
echo "🚀 Starting KwikID AI Ingest Service locally..."

# Check if .env exists
if [ ! -f .env ]; then
    echo "⚠️  .env file not found. Copying from .env.example..."
    cp .env.example .env
fi

# Run uvicorn
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
