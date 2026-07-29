import chainlit as cl
from urllib.parse import unquote, urlparse, parse_qs
from contextvars import ContextVar
import importlib
import functools
from fastapi import Body
from chainlit.server import app as chainlit_app
from pathlib import Path
import uuid
from datetime import datetime

from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser
from langchain_ollama import ChatOllama, OllamaEmbeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter
from dotenv import load_dotenv
from chainlit.action import Action as ChainlitAction

from models import Webpage, Chunk, get_db_session


# Load .env from both workspace root and app directory if present.
WORKSPACE_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(WORKSPACE_ROOT / ".env")
load_dotenv(Path(__file__).resolve().parent / ".env")


# Chainlit 2.3 context patching (required for compatibility)
cl_context_module = importlib.import_module("chainlit.context")
cl_step_module = importlib.import_module("chainlit.step")
cl_message_module = importlib.import_module("chainlit.message")
cl_socket_module = importlib.import_module("chainlit.socket")

patched_local_steps = ContextVar("local_steps", default=None)
cl_context_module.local_steps = patched_local_steps
cl_step_module.local_steps = patched_local_steps
cl_message_module.local_steps = patched_local_steps


original_connect_handler = cl_socket_module.sio.handlers["/"]["connect"]


@functools.wraps(original_connect_handler)
async def patched_connect_handler(sid, environ, auth=None):
    normalized_auth = auth or {}
    normalized_auth.setdefault("sessionId", str(uuid.uuid4()))
    normalized_auth.setdefault("clientType", "webapp")
    return await original_connect_handler(sid, environ, normalized_auth)


cl_socket_module.sio.handlers["/"]["connect"] = patched_connect_handler


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


def normalize_page_url(value: str) -> str:
    """Extract the raw URL if the value arrives as a markdown link."""
    normalized = normalize_str(value)
    if normalized.startswith("[") and "](" in normalized and normalized.endswith(")"):
        _, link_target = normalized.split("](", 1)
        return link_target[:-1].strip()
    return normalized


def format_history(history: list) -> str:
    """Format last 12 turns of conversation history."""
    lines = []
    for turn in history[-12:]:
        role = str(turn.get("role", "user"))
        content = str(turn.get("content", "")).strip()
        if content:
            lines.append(f"{role}: {content}")
    return "\n".join(lines)


async def send_message_actions(message_id: str) -> None:
    """Attach add/delete actions to an existing message."""
    actions = [
        ChainlitAction("add", {"action": "add"}, "Add", "Save webpage to database"),
        ChainlitAction("delete", {"action": "delete"}, "Delete", "Delete webpage from database"),
    ]
    for action in actions:
        await action.send(for_id=message_id)


async def chunk_content(text: str, chunk_size: int = 800, overlap: int = 100) -> list[dict]:
    """Split content into chunks using LangChain with token-based splitting."""
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=overlap,
        separators=["\n\n", "\n", " ", ""],
    )
    chunks = splitter.split_text(text)
    return [{"content": chunk, "index": i} for i, chunk in enumerate(chunks)]


async def generate_embeddings(text: str) -> list:
    """Generate embeddings using Ollama embedding model compatible with qwen3."""
    try:
        embeddings_model = OllamaEmbeddings(model="mxbai-embed-large")
        embedding = await embeddings_model.aembed_query(text)
        # Pad to 1536 if necessary (mxbai-embed-large produces 1024)
        if len(embedding) < 1536:
            embedding = embedding + [0.0] * (1536 - len(embedding))
        return embedding[:1536]
    except Exception as e:
        print(f"Error generating embedding: {e}")
        return [0.0] * 1536


async def generate_summary(text: str, title: str, llm_chain) -> str:
    """Generate summary of webpage content using the LLM."""
    try:
        summary_prompt = f"""Summarize the following webpage content in 2-3 sentences.
Title: {title}
Content:
{text[:2000]}...

Summary:"""
        summary = await llm_chain.ainvoke(
            {
                "input": summary_prompt,
                "history": "",
                "system_prompt": "You are a helpful summarizer. Provide concise summaries.",
            }
        )
        return summary.strip()
    except Exception as e:
        print(f"Error generating summary: {e}")
        return "Summary generation failed"


async def save_webpage_to_db(
    url: str, title: str, content: str, summary: str, page_context: dict
) -> str:
    """Save webpage and chunks to database with embeddings."""
    session = get_db_session()
    try:
        normalized_url = normalize_page_url(url)
        webpage = session.query(Webpage).filter(Webpage.url == normalized_url).first()

        if webpage is None:
            webpage = Webpage(url=normalized_url)
            session.add(webpage)
            session.flush()

        webpage.title = title
        webpage.description = page_context.get("description", "")
        webpage.author = page_context.get("author")
        webpage.language = page_context.get("language")
        webpage.domain = page_context.get("domain")
        webpage.favicon_url = page_context.get("favicon_url")
        webpage.screenshot_url = page_context.get("screenshot_url")
        webpage.raw_content = content
        webpage.llm_summary = summary
        webpage.word_count = len(content.split())
        webpage.status = "processed"
        webpage.last_visited_at = datetime.utcnow()

        session.query(Chunk).filter(Chunk.webpage_id == webpage.id).delete(
            synchronize_session=False
        )
        
        # Generate chunks
        chunks_data = await chunk_content(content, chunk_size=800, overlap=100)
        
        # Create chunk records with embeddings
        for chunk_info in chunks_data:
            embedding = await generate_embeddings(chunk_info["content"])
            chunk = Chunk(
                webpage_id=webpage.id,
                chunk_index=chunk_info["index"],
                content=chunk_info["content"],
                embedding=embedding,
                token_count=len(chunk_info["content"].split()),
                chunk_type="content",
            )
            session.add(chunk)
        
        webpage.is_chunked = True
        session.commit()
        return str(webpage.id)
    except Exception as e:
        session.rollback()
        print(f"Error saving webpage: {e}")
        raise
    finally:
        session.close()


async def delete_webpage_from_db(webpage_id: str) -> bool:
    """Delete webpage and associated chunks from database."""
    try:
        session = get_db_session()
        webpage = session.query(Webpage).filter(Webpage.id == webpage_id).first()
        if webpage:
            session.delete(webpage)
            session.commit()
            session.close()
            return True
        session.close()
        return False
    except Exception as e:
        print(f"Error deleting webpage: {e}")
        return False


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
    
    # Store page context in session for database operations
    cl.user_session.set("page_url", page_url)
    cl.user_session.set("page_title", page_title)
    cl.user_session.set("page_text", page_text)

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
    
    welcome_message = await cl.Message(
        content=f"✅ **{page_title}** loaded!\n\nAsk me anything about this page."
    ).send()
    await send_message_actions(welcome_message.id)


@cl.on_message
async def on_message(message: cl.Message):
    """Process user message and generate response."""
    system_prompt = cl.user_session.get("system_prompt", "")
    history = cl.user_session.get("history", [])
    page_url = cl.user_session.get("page_url", "Unknown")
    page_title = cl.user_session.get("page_title", "Unknown")
    page_text = cl.user_session.get("page_text", "")

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


        \
        {
            "input": message.content,
            "history": history_text,
            "system_prompt": system_prompt,
        }
    )

    # Add assistant response to history
    history.append({"role": "assistant", "content": text})
    cl.user_session.set("history", history)

    response_message = await cl.Message(content=text).send()
    await send_message_actions(response_message.id)


@cl.action_callback("add")
async def handle_add_webpage(action: cl.Action):
    """Handle adding webpage to database."""
    try:
        page_url = cl.user_session.get("page_url", "Unknown")
        page_title = cl.user_session.get("page_title", "Unknown")
        page_text = cl.user_session.get("page_text", "")
        llm_chain = cl.user_session.get("llm_chain")
        
        if not page_text:
            await cl.Message(content="❌ No page content available to save.").send()
            return
        
        # Show progress
        await cl.Message(content="⏳ Generating summary...").send()
        summary = await generate_summary(page_text, page_title, llm_chain)
        
        await cl.Message(content="⏳ Chunking content and generating embeddings...").send()
        webpage_id = await save_webpage_to_db(
            url=page_url,
            title=page_title,
            content=page_text,
            summary=summary,
            page_context={"description": ""},
        )
        
        # Store webpage_id in session for potential delete operation
        cl.user_session.set("last_webpage_id", webpage_id)
        
        await cl.Message(
            content=f"✅ **Webpage saved successfully!**\n\n**Summary:** {summary}\n\n**ID:** {webpage_id}"
        ).send()
    except Exception as e:
        await cl.Message(
            content=f"❌ Error saving webpage: {str(e)}"
        ).send()


@cl.action_callback("delete")
async def handle_delete_webpage(action: cl.Action):
    """Handle deleting webpage from database."""
    try:
        webpage_id = cl.user_session.get("last_webpage_id")
        
        if not webpage_id:
            await cl.Message(
                content="❌ No webpage to delete. Save a webpage first using the 'add' button."
            ).send()
            return
        
        await cl.Message(content="⏳ Deleting webpage...").send()
        success = await delete_webpage_from_db(webpage_id)
        
        if success:
            cl.user_session.set("last_webpage_id", None)
            await cl.Message(
                content=f"✅ **Webpage deleted successfully!** (ID: {webpage_id})"
            ).send()
        else:
            await cl.Message(
                content=f"❌ Webpage not found or already deleted (ID: {webpage_id})"
            ).send()
    except Exception as e:
        await cl.Message(
            content=f"❌ Error deleting webpage: {str(e)}"
        ).send()
