# ChatSide: Browser Extension Chat Assistant with RAG

A Chainlit-powered browser extension that answers questions about any webpage
using a local Ollama LLM, and now persists pages to a PostgreSQL vector store
so they can be retrieved semantically across sessions.

## Features

- **Local-first**: Runs entirely on your machine using Ollama and PostgreSQL
- **Browser integrated**: Chrome extension adds a chat panel to any webpage
- **Context-aware**: Automatically captures and analyzes page content
- **RAG pipeline**: Saved pages are chunked, embedded, and stored in pgvector for semantic retrieval
- **Persistent memory**: Add or delete any page from the knowledge base with in-chat buttons
- **Privacy-focused**: No data sent to external APIs

## Architecture

- `app.py`: Chainlit backend — chat, RAG save/delete, and action buttons
- `models.py`: SQLAlchemy ORM models for `webpages` and `chunks` tables
- `my_extensions/`: Chrome extension (manifest, content script, styling)
- `Modelfile`: Custom Ollama model configuration for optimal QA performance
- `chainlit.md`: Welcome message
- `run_chainlit.sh`: One-command startup script (starts Postgres, Ollama, and Chainlit)

## Prerequisites

- **Python**: 3.9+ (tested on 3.9, 3.11)
- **Ollama**: Installed and runnable (download from [ollama.ai](https://ollama.ai))
- **PostgreSQL 18**: Installed via Homebrew (`brew install postgresql@18`)
- **pgvector extension**: Installed via Homebrew (`brew install pgvector`)
- **Chrome/Chromium**: For the browser extension
- **8GB+ RAM**: For running qwen3:8b model plus Postgres

## Quick Start

### 1. Create Virtual Environment

```bash
python -m venv .venv
source .venv/bin/activate
```

### 2. Install Dependencies

```bash
pip install -r requirement.txt
```

### 3. Install and Start PostgreSQL (macOS)

```bash
brew install postgresql@18 pgvector
brew services start postgresql@18
```

The startup script handles creating the `postgres` role, `page_embeddings`
database, and enabling the `vector` extension automatically on first run.

### 4. Create Ollama Model (Optional but Recommended)

```bash
ollama create chatside-qwen3 -f Modelfile
```

If you skip this, the app falls back to `qwen3:8b`.

### 5. Run the App

**Easiest way** (automated setup):

```bash
chmod +x run_chainlit.sh
./run_chainlit.sh
```

This script automatically:
- Starts the Homebrew PostgreSQL service
- Creates the `postgres` role and `page_embeddings` database if missing
- Enables the `pgvector` extension
- Runs `models.py` to create the `webpages` and `chunks` tables
- Starts the Ollama server
- Pulls required models (`chatside-qwen3`, `mxbai-embed-large`)
- Launches Chainlit on `http://localhost:8000`

**Manual way**:

```bash
# Terminal 1: Start Postgres
brew services start postgresql@18

# Terminal 2: Start Ollama
ollama serve

# Terminal 3: Run Chainlit
source .venv/bin/activate
export DATABASE_URL=postgresql://postgres:postgres@localhost/page_embeddings
python models.py            # create tables on first run
python -u -m chainlit run app.py -h
```

### 6. Load the Chrome Extension

1. Open Chrome → `chrome://extensions`
2. Enable "Developer mode" (top right)
3. Click "Load unpacked"
4. Select the `my_extensions` folder
5. Visit any webpage and click the **Ask This Page** button

## RAG Pipeline

### Saving a Page

Click the **Add** button that appears on any chat message to store the current
page in the knowledge base. The pipeline:

1. Generates a 2–3 sentence LLM summary using `chatside-qwen3`
2. Splits the raw page text into chunks of **800 tokens** with **100-token overlap**
   using LangChain's `RecursiveCharacterTextSplitter`
3. Generates a **1536-dimensional embedding** per chunk using `mxbai-embed-large`
   (1024-dim output zero-padded to 1536 to match the schema)
4. Inserts/updates one row in `webpages` and replaces all rows in `chunks`
5. Re-saving the same URL updates the existing row instead of failing

### Deleting a Page

Click the **Delete** button to remove the current page and all its chunks from
the database (cascaded).

### Database Schema

```
webpages
  id              UUID  PRIMARY KEY
  url             TEXT  UNIQUE NOT NULL
  title           TEXT
  description     TEXT
  author          TEXT
  language        TEXT
  domain          TEXT
  raw_content     TEXT
  llm_summary     TEXT
  word_count      INTEGER
  is_chunked      BOOLEAN
  status          TEXT  (pending | processed | failed)
  created_at      TIMESTAMPTZ
  updated_at      TIMESTAMPTZ
  last_visited_at TIMESTAMPTZ

chunks
  id              UUID  PRIMARY KEY
  webpage_id      UUID  FK → webpages(id) ON DELETE CASCADE
  chunk_index     INTEGER
  content         TEXT
  embedding       vector(1536)
  token_count     INTEGER
  chunk_type      TEXT  (content | summary | raw | heading)
  created_at      TIMESTAMPTZ
```

## How It Works

```
Browser Extension        Chainlit Backend            PostgreSQL / Ollama
   |                          |                             |
   +--POST /ext/context----→  |                             |
   |  (url, title, text)       |                             |
   |                          |---chat (qwen3:8b)----------→|
   |  ←--chat response------  |                             |
   |                          |                             |
   | [Add button click]        |                             |
   +--action: add----------→  |--summarise (qwen3:8b)-----→|
                               |--chunk + embed (mxbai)----→|
                               |--upsert webpages/chunks---→|
   | [Delete button click]     |                             |
   +--action: delete-------→  |--DELETE webpages row------→|
```

## Models

### Chat Model: `chatside-qwen3` (based on `qwen3:8b`)
- 8B parameter language model, custom context window (8192 tokens)
- Temperature 0.3 for factual, page-grounded answers
- ~5GB memory footprint

### Embedding Model: `mxbai-embed-large`
- Used for chunk embeddings stored in pgvector
- 1024-dimension output (zero-padded to 1536 in the DB schema)
- ~300MB footprint

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `DATABASE_URL` | `postgresql://postgres:postgres@localhost/page_embeddings` | SQLAlchemy-compatible Postgres URL |

The app also loads `.env` from the project root and app directory if present.

## File Structure

```
chatside/
├── app.py                 # Chainlit backend + RAG save/delete pipeline
├── models.py              # SQLAlchemy ORM (webpages + chunks)
├── Modelfile              # Ollama model config (chatside-qwen3)
├── run_chainlit.sh        # Startup script (Postgres + Ollama + Chainlit)
├── install_postgres_macos.sh  # One-time Homebrew Postgres setup
├── requirement.txt        # Python dependencies
├── README.md              # This file
├── chainlit.md            # Welcome message
├── .env                   # (optional) Local config
└── my_extensions/         # Chrome extension
    ├── manifest.json
    ├── content.js
    ├── content.css
    └── icons/
```

## Troubleshooting

### PostgreSQL connection error (`role "postgres" does not exist`)

The `run_chainlit.sh` script creates the `postgres` role automatically. If
running manually, provision it once:

```bash
psql -d postgres -c "CREATE ROLE postgres WITH LOGIN SUPERUSER PASSWORD 'postgres';"
psql -d postgres -c "CREATE DATABASE page_embeddings OWNER postgres;"
psql -d page_embeddings -c "CREATE EXTENSION IF NOT EXISTS vector;"
```

### Ollama not starting

```bash
which ollama
ollama serve
```

### Models won't pull

```bash
ollama pull qwen3:8b
ollama pull mxbai-embed-large
```

### Extension can't connect

- Ensure Chainlit is running on `http://localhost:8000`
- Check browser console (F12) for connection errors
- Verify extension permissions in `my_extensions/manifest.json`

### Slow responses

- Q4 quantization trades ~5% accuracy for 4x speed
- For higher quality: use full precision (requires ~32GB RAM)
- Smaller models: `neural-chat:7b` or `mistral:7b` (~3GB)

## Performance Notes

**M1 Max (16GB)**:
- ~15 seconds first request (model loads)
- ~8–12 tokens/second chat generation
- Embedding generation: ~100ms per chunk (mxbai-embed-large)
- Suitable for interactive Q&A and page ingestion

**GPU acceleration**:
- Metal GPU on macOS: Automatic via Ollama
- NVIDIA: Ensure CUDA drivers installed
- AMD: Use ROCm backend

## Architecture Notes

The app is model-agnostic. To use a different LLM:

1. Update `app.py`:
   ```python
   llm = ChatOllama(model="your-model-name")
   ```

2. To use remote APIs (Claude, GPT, etc.):
   ```python
   # Instead of ChatOllama:
   from langchain_anthropic import ChatAnthropic
   llm = ChatAnthropic(model="claude-3-sonnet")
   ```

## License

MIT

## Support

For issues, check:
- Ollama documentation: https://ollama.ai
- Chainlit docs: https://docs.chainlit.io
- LangChain docs: https://python.langchain.com

