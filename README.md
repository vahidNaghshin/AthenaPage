# ChatSide: Browser Extension Chat Assistant

A Chainlit-powered browser extension that answers questions about any webpage using a local Ollama LLM.

## Features

- **Local-first**: Runs entirely on your machine using Ollama
- **Browser integrated**: Chrome extension adds a chat panel to any webpage
- **Context-aware**: Automatically captures and analyzes page content
- **Privacy-focused**: No data sent to external APIs

## Architecture

- `app.py`: Chainlit backend with Ollama integration
- `my_extensions/`: Chrome extension (manifest, content script, styling)
- `Modelfile`: Custom Ollama model configuration for optimal QA performance
- `chainlit.md`: Welcome message and documentation
- `run_chainlit.sh`: One-command startup script

## Prerequisites

- **Python**: 3.9+ (tested on 3.9, 3.11)
- **Ollama**: Installed and runnable (download from [ollama.ai](https://ollama.ai))
- **Chrome/Chromium**: For the browser extension
- **4GB+ RAM**: For running qwen3:8b model

## Quick Start

### 1. Create Virtual Environment

```bash
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate
```

### 2. Install Dependencies

```bash
pip install -r requirement.txt
```

### 3. Create Ollama Model (Optional but Recommended)

This creates an optimized model with custom parameters:

```bash
ollama create chatside-qwen3 -f Modelfile
```

If you skip this, the app falls back to `qwen3:8b`.

### 4. Run the App

**Easiest way** (automated setup):

```bash
chmod +x run_chainlit.sh
./run_chainlit.sh
```

This script automatically:
- Activates the venv
- Starts Ollama server
- Pulls required models (`chatside-qwen3`, `mxbai-embed-large`)
- Launches Chainlit on `http://localhost:8000`

**Manual way**:

```bash
# Terminal 1: Start Ollama
ollama serve

# Terminal 2: Run Chainlit
source .venv/bin/activate
python -u -m chainlit run app.py
```

### 5. Load the Chrome Extension

1. Open Chrome → `chrome://extensions`
2. Enable "Developer mode" (top right)
3. Click "Load unpacked"
4. Select the `my_extensions` folder
5. Visit any webpage and click the **Ask This Page** button

## How It Works

```
Browser Extension          Chainlit Backend       Ollama
   |                            |                  |
   +---POST /ext/context-----→  |                  |
   |  (page URL, title, text)    |                  |
   |                            |--pulls model---→ |
   |  ←---chat response------  |  |  qwen3:8b     |
   |     (grounded in page)     |  |               |
   +                            |←-- embeddings --+
```

1. Extension captures visible page text
2. POSTs context to `/ext/context` endpoint
3. Chainlit initializes LLM chain with page context
4. User questions are answered using the captured page content
5. Embedding model used for semantic search (optional)

## Models

### Chat Model: `qwen3:8b`
- 8B parameter language model
- Quantized to 4-bit (Q4_K) for efficiency
- ~5GB memory footprint
- Fast inference on M1 Max (8-12 tokens/sec)

### Embedding Model: `mxbai-embed-large`
- Used for optional semantic search
- 1024-dimension embeddings
- ~300MB footprint

## Environment Variables

The app loads `.env` from:
- Project root
- App directory

No AWS credentials needed—everything runs locally!

## File Structure

```
chatside/
├── app.py                 # Chainlit backend
├── Modelfile              # Ollama model config
├── run_chainlit.sh        # Startup script
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

### Ollama not starting

```bash
# Check if ollama is installed
which ollama

# Start manually
ollama serve
```

### Models won't pull

```bash
# Pull manually
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
- ~8-12 tokens/second sustained
- Suitable for interactive Q&A

**GPU acceleration**:
- Metal GPU on macOS: Automatic
- NVIDIA: Ensure CUDA drivers installed
- AMD: Use ROCm backend

## Architecture Notes

The app is model-agnostic. To use a different LLM:

1. Update `app.py` line 135:
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

