#!/bin/bash

# PostgreSQL Installation Script for macOS with Homebrew
# Installs PostgreSQL 18, pgvector extension, and sets up a test database

set -e  # Exit on error

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

echo -e "${YELLOW}PostgreSQL 18 Installation for macOS${NC}"

# Check if Homebrew is installed
if ! command -v brew &> /dev/null; then
    echo -e "${RED}Homebrew not found. Please install Homebrew first:${NC}"
    echo "  /bin/bash -c \"\$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)\""
    exit 1
fi

echo -e "${YELLOW}Step 1: Installing PostgreSQL 18...${NC}"
if brew list postgresql@18 &>/dev/null; then
    echo -e "${GREEN}✓ PostgreSQL 18 already installed${NC}"
else
    brew install postgresql@18
    echo -e "${GREEN}✓ PostgreSQL 18 installed${NC}"
fi

echo -e "${YELLOW}Step 2: Installing pgvector extension...${NC}"
if brew list pgvector &>/dev/null; then
    echo -e "${GREEN}✓ pgvector already installed${NC}"
else
    brew install pgvector
    echo -e "${GREEN}✓ pgvector installed${NC}"
fi

echo -e "${YELLOW}Step 3: Starting PostgreSQL service...${NC}"
brew services start postgresql@18 2>/dev/null || true
sleep 2
echo -e "${GREEN}✓ PostgreSQL service started${NC}"

echo -e "${YELLOW}Step 4: Initializing PostgreSQL database if needed...${NC}"
# Get PostgreSQL version and home
POSTGRES_HOME=$(brew --prefix postgresql@18)
POSTGRES_VERSION=$(psql --version | awk '{print $3}' | cut -d. -f1)

if [ ! -d "$POSTGRES_HOME/var" ]; then
    initdb -D "$POSTGRES_HOME/var" --encoding=UTF8 --locale=en_US.UTF-8
    echo -e "${GREEN}✓ PostgreSQL database initialized${NC}"
else
    echo -e "${GREEN}✓ PostgreSQL database already initialized${NC}"
fi

echo -e "${YELLOW}Step 5: Creating 'page_embeddings' database...${NC}"
# Get current user
CURRENT_USER=$(whoami)

# Create the database and extension
psql -d postgres -c "CREATE DATABASE page_embeddings OWNER $CURRENT_USER;" 2>/dev/null || echo "Database already exists"
psql -d page_embeddings -c "CREATE EXTENSION IF NOT EXISTS vector;" 2>/dev/null || true
echo -e "${GREEN}✓ Database created and pgvector extension enabled${NC}"

echo -e "${YELLOW}Step 6: Verifying installation...${NC}"
CURRENT_DB_USER=$(psql -d postgres -t -c "SELECT current_user;")
echo -e "${GREEN}✓ Current database user: $CURRENT_DB_USER${NC}"

echo -e "${YELLOW}Step 7: Checking PostgreSQL status...${NC}"
brew services list | grep postgresql
echo ""
echo -e "${GREEN}PostgreSQL Version: $POSTGRES_VERSION${NC}"

echo -e "${GREEN}✅ PostgreSQL installation complete!${NC}"
echo ""
echo -e "${YELLOW}Quick commands:${NC}"
echo "  Start PostgreSQL:   brew services start postgresql@18"
echo "  Stop PostgreSQL:    brew services stop postgresql@18"
echo "  Restart PostgreSQL: brew services restart postgresql@18"
echo "  Connect to DB:      psql -d page_embeddings"
echo "  Enable pgvector:    psql -d page_embeddings -c 'CREATE EXTENSION IF NOT EXISTS vector;'"
echo "  Service status:     brew services list"
echo ""
echo -e "${YELLOW}PostgreSQL location: $(brew --prefix postgresql@18)${NC}"
echo -e "${YELLOW}Data directory:      $(brew --prefix postgresql@18)/var${NC}"
