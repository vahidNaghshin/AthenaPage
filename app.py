import chainlit as cl
from urllib.parse import unquote, urlparse, parse_qs
from contextvars import ContextVar
import importlib
from fastapi import Body
from chainlit.server import app as chainlit_app
from pathlib import Path

from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser
from langchain_ollama import ChatOllama
from dotenv import load_dotenv


# Load .env from both workspace root and app directory if present.
WORKSPACE_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(WORKSPACE_ROOT / ".env")
load_dotenv(Path(__file__).resolve().parent / ".env")


# Chainlit 2.3 context patching (required for compatibility)
cl_context_module = importlib.import_module("chainlit.context")
cl_step_module = importlib.import_module("chainlit.step")
cl_message_module = importlib.import_module("chainlit.message")

patched_local_steps = ContextVar("local_steps", default=None)
cl_context_module.local_steps = patched_local_steps
cl_step_module.local_steps = patched_local_steps
cl_message_module.local_steps = patched_local_steps


# Global context for page data from browser extension
LAST_PAGE_CONTEXT = {
    "page_url": "Unknown",
    "page_title": "Unknown",
    "page_text": "",
}


def read_query_param(params, key, default=""):
    """Extract and decode query parameter, handling list/str formats."""
    value = params.get(key, default)
    if isinstance(value, list):
        value = value[0] if value else default
    return unquote(str(value)) if value else default


def extract_params_from_referer() -> dict:
    """Parse query parameters from HTTP referer."""
    referer = cl.user_session.get("http_referer")
    if not referer:
        return {}
    try:
        parsed = urlparse(str(referer))
        return parse_qs(parsed.query)
    except Exception:
        return {}


def normalize_str(value, default="") -> str:
    """Normalize string value with fallback default."""
    return str(value).strip() or default if value else default


def format_history(history: list) -> str:
    """Format last 12 turns of conversation history."""
    lines = []
    for turn in history[-12:]:
        role = str(turn.get("role", "user"))
        content = str(turn.get("content", "")).strip()
        if content:
            lines.append(f"{role}: {content}")
    return "\n".join(lines)


@chainlit_app.post("/ext/context")
async def set_ext_context(payload: dict = Body(default_factory=dict)):
    """Receive and store page context from browser extension."""
    LAST_PAGE_CONTEXT["page_url"] = normalize_str(payload.get("page_url"), "Unknown")
    LAST_PAGE_CONTEXT["page_title"] = normalize_str(
        payload.get("page_title"), "Unknown"
    )
    LAST_PAGE_CONTEXT["page_text"] = normalize_str(payload.get("page_text"), "")
    
    return {
        "ok": True,
        "page_text_length": len(LAST_PAGE_CONTEXT["page_text"]),
    }


@cl.on_chat_start
async def on_start():
    """Initialize chat session with page context and LLM chain."""
    # Extract page context from query params or session storage
    params = cl.user_session.get("query_params") or {}
    if not params:
        params = extract_params_from_referer()

    page_url = read_query_param(params, "page_url", "Unknown")
    page_title = read_query_param(params, "page_title", "Unknown")
    page_text = read_query_param(params, "page_text", "")

    # Fallback to last stored context if no new context provided
    if not page_text:
        page_url = LAST_PAGE_CONTEXT.get("page_url", "Unknown")
        page_title = LAST_PAGE_CONTEXT.get("page_title", "Unknown")
        page_text = LAST_PAGE_CONTEXT.get("page_text", "")

    print(f"Page URL: {page_url}")
    print(f"Page Title: {page_title}")
    print(f"Page Text Length: {len(page_text)}")

    if not page_text:
        await cl.Message(content="⚠️ No page content received.").send()
        return

    # Build system prompt with page context
    system_prompt = f"""
You are a helpful assistant answering questions about a webpage.

Title: {page_title}
URL:   {page_url}

--- PAGE CONTENT START ---
{page_text}
--- PAGE CONTENT END ---

Rules:
- Answer ONLY from the page content above
- If the content is not included in the page but relevant, you may answer from your general knowledge
- If something isn't covered, say so honestly
- Be concise. Cite the page when helpful.
"""
    
    cl.user_session.set("system_prompt", system_prompt)

    # Initialize Ollama LLM with prompt template and chain
    llm = ChatOllama(model="chatside-qwen3")
    prompt = ChatPromptTemplate.from_template(
        """{system_prompt}

Conversation so far:
{history}

User question:
{input}"""
    )
    
    chain = prompt | llm | StrOutputParser()
    cl.user_session.set("llm_chain", chain)

    print("Chat session initialized successfully.")
    await cl.Message(
        content=f"✅ **{page_title}** loaded!\n\nAsk me anything about this page."
    ).send()


@cl.on_message
async def on_message(message: cl.Message):
    """Process user message and generate response."""
    system_prompt = cl.user_session.get("system_prompt", "")
    history = cl.user_session.get("history", [])

    # Add user message to history
    history.append({"role": "user", "content": message.content})
    history_text = format_history(history)
    cl.user_session.set("history", history)

    # Get LLM chain
    llm_chain = cl.user_session.get("llm_chain")
    if not llm_chain:
        await cl.Message(
            content="Model is not initialized. Start a new chat session."
        ).send()
        return

    # Generate response
    text = await llm_chain.ainvoke(
        {
            "input": message.content,
            "history": history_text,
            "system_prompt": system_prompt,
        }
    )

    # Add assistant response to history
    history.append({"role": "assistant", "content": text})
    cl.user_session.set("history", history)

    await cl.Message(content=text).send()
