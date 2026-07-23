#!/usr/bin/env bash

# Colors for output
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Activate virtual environment
source .venv/bin/activate

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

# Start Chainlit with unbuffered output
python -u -m chainlit run app.py -h
