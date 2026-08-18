#!/usr/bin/env bash

set -e

# Colors for output
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

# Activate virtual environment
source .venv/bin/activate

echo -e "${YELLOW}Setting up PostgreSQL...${NC}"

if ! command -v brew &> /dev/null; then
    echo -e "${RED}Homebrew not found in PATH. Please install Homebrew first.${NC}"
    exit 1
fi

if ! command -v psql &> /dev/null; then
    echo -e "${RED}psql not found in PATH. Install PostgreSQL first.${NC}"
    exit 1
fi

brew services start postgresql@18 >/dev/null 2>&1 || true

if ! psql -d postgres -c "SELECT 1;" >/dev/null 2>&1; then
    echo -e "${RED}Unable to connect to local PostgreSQL.${NC}"
    exit 1
fi

if ! psql -d postgres -tAc "SELECT 1 FROM pg_roles WHERE rolname='postgres';" | grep -q 1; then
    echo -e "${YELLOW}Creating postgres role...${NC}"
    psql -d postgres -c "CREATE ROLE postgres WITH LOGIN SUPERUSER PASSWORD 'postgres';"
fi

if ! psql -d postgres -tAc "SELECT 1 FROM pg_database WHERE datname='page_embeddings';" | grep -q 1; then
    echo -e "${YELLOW}Creating page_embeddings database...${NC}"
    psql -d postgres -c "CREATE DATABASE page_embeddings OWNER postgres;"
fi

psql -d page_embeddings -c "ALTER DATABASE page_embeddings OWNER TO postgres;" >/dev/null
PGPASSWORD=postgres psql -U postgres -h localhost -d page_embeddings -c "CREATE EXTENSION IF NOT EXISTS vector;" >/dev/null
APP_DATABASE_URL="${APP_DATABASE_URL:-postgresql://postgres:postgres@localhost/page_embeddings}" \
    python models.py >/dev/null

echo -e "${GREEN}✓ PostgreSQL setup complete${NC}"

echo -e "${YELLOW}Setting up Ollama...${NC}"

# Check if ollama command exists
if ! command -v ollama &> /dev/null; then
    echo -e "${YELLOW}ollama not found in PATH. Please install ollama.${NC}"
    exit 1
fi

# Start ollama in background if not already running
if ! pgrep -x "ollama" > /dev/null; then
    echo -e "${YELLOW}Starting ollama server...${NC}"
    ollama serve &
    sleep 3  # Wait for ollama to start
fi

# Pull the required models
echo -e "${YELLOW}Ensuring models are available...${NC}"
ollama pull chatside-qwen3 2>/dev/null || ollama pull qwen3:8b
ollama pull mxbai-embed-large 2>/dev/null || true

echo -e "${GREEN}✓ Ollama setup complete${NC}"
echo -e "${YELLOW}Starting Chainlit app...${NC}"

# Start Chainlit with unbuffered output.
# Intentionally NOT setting DATABASE_URL here: Chainlit auto-enables its own
# Postgres-backed persistence layer (threads/steps/users tables) whenever
# DATABASE_URL is present in the environment. This app manages its own
# persistence (webpages/chunks) via APP_DATABASE_URL instead.
# Unset DATABASE_URL in case it leaked in from the calling shell's environment.
unset DATABASE_URL
APP_DATABASE_URL="${APP_DATABASE_URL:-postgresql://postgres:postgres@localhost/page_embeddings}" \
    python -u -m chainlit run app.py -h
