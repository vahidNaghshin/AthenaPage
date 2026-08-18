import chainlit as cl
from urllib.parse import unquote, urlparse, parse_qs
from contextvars import ContextVar
import importlib
import functools
import math
from fastapi import Body
from chainlit.server import app as chainlit_app
from pathlib import Path
import uuid
from datetime import datetime, timezone

from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser
from langchain_ollama import ChatOllama, OllamaEmbeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter
from dotenv import load_dotenv
from chainlit.action import Action as ChainlitAction
from typing import Optional

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

SUMMARY_TRIGGER_TURNS = 18
SUMMARY_TRIGGER_TOKENS = 5000
RECENT_TURNS_TO_KEEP = 8

MODEL_EMBEDDING_DIM = 1024  # mxbai-embed-large native dimension


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
    """Format turns of conversation history."""
    lines = []
    for turn in history:
        role = str(turn.get("role", "user"))
        content = str(turn.get("content", "")).strip()
        if content:
            lines.append(f"{role}: {content}")
    return "\n".join(lines)


def estimate_tokens(text: str) -> int:
    """Approximate token count using a fast character-based heuristic."""
    if not text:
        return 0
    return max(1, len(text) // 4)


def format_history_with_summary(history: list, summary: str) -> str:
    """Build the model history block using long-term summary plus recent turns."""
    recent_history = format_history(history)
    if summary:
        return (
            "Conversation summary (older turns):\n"
            f"{summary}\n\n"
            "Recent conversation turns:\n"
            f"{recent_history}"
        )
    return recent_history


async def summarize_history(old_turns: list, existing_summary: str = "") -> str:
    """Summarize old conversation turns into compact memory text."""
    transcript = format_history(old_turns)
    if not transcript:
        return existing_summary

    summary_prompt = f"""
You are maintaining compact long-term memory for an assistant conversation.

Current memory summary:
{existing_summary or "(none)"}

Older conversation turns to compress:
{transcript}

Write an updated memory summary that keeps durable facts, user preferences, decisions,
open tasks, and constraints. Keep it concise and factual. Use plain text.
"""

    summary_llm = ChatOllama(model="chatside-qwen3")
    try:
        result = await summary_llm.ainvoke(summary_prompt)
        content = getattr(result, "content", result)
        if isinstance(content, list):
            content = "\n".join(str(item) for item in content)
        return str(content).strip() or existing_summary
    except Exception as e:
        print(f"Error summarizing history: {e}")
        return existing_summary


async def maybe_rollup_history(history: list, existing_summary: str) -> tuple[list, str, bool]:
    """Summarize older turns when history grows past context thresholds."""
    history_text = format_history(history)
    exceeds_turn_limit = len(history) > SUMMARY_TRIGGER_TURNS
    exceeds_token_limit = estimate_tokens(history_text) > SUMMARY_TRIGGER_TOKENS

    if not (exceeds_turn_limit or exceeds_token_limit):
        return history, existing_summary, False

    if len(history) <= RECENT_TURNS_TO_KEEP:
        return history, existing_summary, False

    old_turns = history[:-RECENT_TURNS_TO_KEEP]
    recent_turns = history[-RECENT_TURNS_TO_KEEP:]
    updated_summary = await summarize_history(old_turns, existing_summary)
    return recent_turns, updated_summary, True


def parse_page_answer_flag(text: str) -> tuple[bool, str]:
    """Read the page-answerability flag from the first line of the model output."""
    normalized = (text or "").lstrip()
    if normalized.startswith("ANSWERED_FROM_PAGE: yes"):
        body = normalized.split("\n", 1)[1].strip() if "\n" in normalized else ""
        return True, body
    if normalized.startswith("ANSWERED_FROM_PAGE: no"):
        body = normalized.split("\n", 1)[1].strip() if "\n" in normalized else ""
        return False, body
    return True, normalized.strip()


async def send_message_actions(message_id: str) -> None:
    """Attach add/delete actions to an existing message."""
    actions = [
        ChainlitAction("add", {"action": "add"}, "Add", "Save webpage to database"),
        ChainlitAction(
            "delete", {"action": "delete"}, "Delete", "Delete webpage from database"
        ),
    ]
    for action in actions:
        await action.send(for_id=message_id)


async def chunk_content(
    text: str, chunk_size: int = 512, overlap: int = 64
) -> list[dict]:
    """Split content into token-based chunks for RAG retrieval."""
    splitter = RecursiveCharacterTextSplitter.from_tiktoken_encoder(
        model_name="gpt2",
        chunk_size=chunk_size,
        chunk_overlap=overlap,
        separators=["\n\n", "\n", " ", ""],
    )
    chunks = splitter.split_text(text)
    return [
        {
            "content": chunk,
            "index": i,
            "total_chunks": len(chunks),
            "char_length": len(chunk),
            "is_first": i == 0,
            "is_last": i == len(chunks) - 1,
        }
        for i, chunk in enumerate(chunks)
    ]



async def generate_embeddings(
    text: str, embeddings_model: OllamaEmbeddings
) -> Optional[list[float]]:
    """Generate embeddings normalized to DB dimension requirements."""
    try:
        embedding = await embeddings_model.aembed_query(text)

        # Validate expected native model dimension.
        if len(embedding) != MODEL_EMBEDDING_DIM:
            raise ValueError(
                f"Unexpected embedding dimension: {len(embedding)}, "
                f"expected {MODEL_EMBEDDING_DIM}"
            )

        return embedding

    except Exception as e:
        print(f"Error generating embedding: {e}")
        return None  # Return None so caller can handle failure explicitly


def cosine_similarity(vec_a: list[float], vec_b: list[float]) -> float:
    """Compute cosine similarity between two vectors."""
    if not vec_a or not vec_b or len(vec_a) != len(vec_b):
        return 0.0

    dot_product = sum(a * b for a, b in zip(vec_a, vec_b))
    norm_a = math.sqrt(sum(a * a for a in vec_a))
    norm_b = math.sqrt(sum(b * b for b in vec_b))

    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0

    return dot_product / (norm_a * norm_b)


async def is_page_context_relevant(
    question: str,
    page_text: str,
    embeddings_model: OllamaEmbeddings,
    threshold: float = 0.8,
) -> bool:
    """Estimate whether the currently scraped page text is relevant to the query."""
    if not page_text or len(page_text.strip()) < 200:
        return False

    # Compare question to a bounded page excerpt to keep retrieval latency stable.
    page_excerpt = page_text[:4000]
    question_embedding = await generate_embeddings(question, embeddings_model)
    page_embedding = await generate_embeddings(page_excerpt, embeddings_model)
    if question_embedding is None or page_embedding is None:
        return False
    similarity = cosine_similarity(question_embedding, page_embedding)
    print(f"Page relevance similarity: {similarity:.4f}")
    return similarity >= threshold


async def retrieve_relevant_chunks_from_db(
    question: str,
    embeddings_model: OllamaEmbeddings,
    top_k: int = 4,
    max_distance: float = 0.60,
) -> list[dict]:
    """Retrieve the most relevant chunks from pgvector for fallback context."""
    session = get_db_session()
    try:
        question_embedding = await generate_embeddings(question, embeddings_model)
        if question_embedding is None:
            return []
        distance_expr = Chunk.embedding.cosine_distance(question_embedding)

        rows = (
            session.query(
                Chunk.content,
                Chunk.chunk_index,
                Webpage.title,
                Webpage.url,
                distance_expr.label("distance"),
            )
            .join(Webpage, Webpage.id == Chunk.webpage_id)
            .order_by(distance_expr.asc())
            .limit(top_k)
            .all()
        )

        results = []
        for row in rows:
            distance = float(row.distance) if row.distance is not None else 1.0
            if distance <= max_distance:
                results.append(
                    {
                        "content": row.content,
                        "chunk_index": row.chunk_index,
                        "title": row.title or "Untitled",
                        "url": row.url or "Unknown",
                        "distance": distance,
                    }
                )

        return results
    except Exception as e:
        print(f"Error retrieving chunks from database: {e}")
        return []
    finally:
        session.close()


def format_db_context(chunks: list[dict]) -> str:
    """Format retrieved chunks into a compact context section for prompting."""
    lines = []
    for i, chunk in enumerate(chunks, start=1):
        lines.append(
            f"[DB Chunk {i}]\n"
            f"Title: {chunk['title']}\n"
            f"URL: {chunk['url']}\n"
            f"Text: {chunk['content']}"
        )
    return "\n\n".join(lines)


async def generate_summary(text: str, title: str, llm_chain) -> str:
    """Generate summary of webpage content using the LLM."""
    try:

        summary_prompt = f"""/no_think
Summarize the following webpage content in 2-3 sentences.
Title: {title}
Content:
{text[:20000]}...

Summary:"""
        summary = await llm_chain.ainvoke(
            {
                "input": summary_prompt,
                "history": "",
                "system_prompt": "You are a webpage summarizer. Return exactly 2-3 sentences. \
                    Focus on the main topic, key points, and purpose of the page. \
                        No preamble, no bullet points, just plain sentences.",
            }
        )
        return summary.strip()
    except Exception as e:
        print(f"Error generating summary: {e}")
        return "Summary generation failed"


async def save_webpage_to_db(
    url: str,
    title: str,
    content: str,
    summary: str,
    page_context: dict,
    embeddings_model: OllamaEmbeddings,
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
        webpage.last_visited_at = datetime.now(timezone.utc)

        session.query(Chunk).filter(Chunk.webpage_id == webpage.id).delete(
            synchronize_session=False
        )

        # Generate chunks
        chunks_data = await chunk_content(content, chunk_size=512, overlap=64)

        # Create chunk records with embeddings
        for chunk_info in chunks_data:
            embedding = await generate_embeddings(
                chunk_info["content"], embeddings_model
            )
            if embedding is None:
                continue
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
- Do not use general knowledge in the first pass
- If something isn't covered, say so honestly
- Be concise. Cite the page when helpful.
- Start every response with exactly one flag line:
    ANSWERED_FROM_PAGE: yes
    ANSWERED_FROM_PAGE: no
- Use ANSWERED_FROM_PAGE: yes only when the answer is supported by the page content above.
- Use ANSWERED_FROM_PAGE: no when the page content does not contain enough information to answer the query.
- After the flag line, provide the answer body only.

"""

    cl.user_session.set("system_prompt", system_prompt)
    cl.user_session.set("history_summary", "")

    # Initialize Ollama LLM with prompt template and chain
    llm = ChatOllama(model="chatside-qwen3")
    embeddings_model = OllamaEmbeddings(model="mxbai-embed-large")
    prompt = ChatPromptTemplate.from_template(
        """{system_prompt}

Conversation so far:
{history}

User question:
{input}"""
    )

    chain = prompt | llm | StrOutputParser()
    cl.user_session.set("llm_chain", chain)
    cl.user_session.set("embeddings_model", embeddings_model)

    print("Chat session initialized successfully.")


    # From a single user's perspective, async/await behaves exactly like normal sequential code.
    welcome_message = await cl.Message(
        content=f"✅ **{page_title}** loaded!\n\nAsk me anything about this page."
    ).send()
    await send_message_actions(welcome_message.id)


@cl.on_message
async def on_message(message: cl.Message):
    """Process user message and generate response."""
    system_prompt = cl.user_session.get("system_prompt", "")
    history = cl.user_session.get("history", [])
    history_summary = cl.user_session.get("history_summary", "")

    history_text = format_history_with_summary(history, history_summary)

    # Get LLM chain
    llm_chain = cl.user_session.get("llm_chain")
    embeddings_model = cl.user_session.get("embeddings_model")
    if not llm_chain:
        await cl.Message(
            content="Model is not initialized. Start a new chat session."
        ).send()
        return
    if not embeddings_model:
        await cl.Message(
            content="Embedding model is not initialized. Start a new chat session."
        ).send()
        return

    first_pass_text = await llm_chain.ainvoke(
        {
            "input": message.content,
            "history": history_text,
            "system_prompt": system_prompt,
        }
    )
    answered_from_page, text = parse_page_answer_flag(first_pass_text)

    if not answered_from_page:
        db_chunks = await retrieve_relevant_chunks_from_db(
            message.content, embeddings_model, top_k=4
        )
        if db_chunks:
            db_context = format_db_context(db_chunks)
            effective_system_prompt = (
                f"{system_prompt}\n\n"
                "--- RETRIEVED DATABASE CONTEXT START ---\n"
                f"{db_context}\n"
                "--- RETRIEVED DATABASE CONTEXT END ---\n\n"
                "The first-pass flag showed that the current page does not answer the "
                "query. Use the retrieved database context above as the primary source. "
                "If the database context answers the question, start your response with "
                "ANSWERED_FROM_PAGE: no and then provide the answer body. If it still "
                "does not answer the question, start your response with ANSWERED_FROM_PAGE: no "
                "and state that neither the page nor saved knowledge contains the answer."
            )

            best_chunk = db_chunks[0]
            await cl.Message(
                content=(
                    "ℹ️ The page content does not answer this question. "
                    f"I retrieved related content from saved pages (best match: "
                    f"{best_chunk['title']} | distance={best_chunk['distance']:.3f})."
                )
            ).send()

            second_pass_text = await llm_chain.ainvoke(
                {
                    "input": message.content,
                    "history": history_text,
                    "system_prompt": effective_system_prompt,
                }
            )
            _, text = parse_page_answer_flag(second_pass_text)
        else:
            text = (
                text
                or "The current page does not contain enough information, and no relevant saved knowledge was found in the database."
            )

    # Add user message to history
    history.append({"role": "user", "content": message.content})
    # Add assistant response to history
    history.append({"role": "assistant", "content": text})
    history, history_summary, did_rollup = await maybe_rollup_history(
        history, history_summary
    )
    if did_rollup:
        print("History rollup completed after assistant response.")

    cl.user_session.set("history", history)
    cl.user_session.set("history_summary", history_summary)

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
        embeddings_model = cl.user_session.get("embeddings_model")

        if not page_text:
            await cl.Message(content="❌ No page content available to save.").send()
            return
        if not embeddings_model:
            await cl.Message(
                content="❌ Embedding model is not initialized. Start a new chat session."
            ).send()
            return

        # Show progress
        await cl.Message(content="⏳ Generating summary...").send()
        summary = await generate_summary(page_text, page_title, llm_chain)

        await cl.Message(
            content="⏳ Chunking content and generating embeddings..."
        ).send()
        webpage_id = await save_webpage_to_db(
            url=page_url,
            title=page_title,
            content=page_text,
            summary=summary,
            page_context={"description": ""},
            embeddings_model=embeddings_model,
        )

        # Store webpage_id in session for potential delete operation
        cl.user_session.set("last_webpage_id", webpage_id)

        await cl.Message(
            content=f"✅ **Webpage saved successfully!**\n\n**Summary:** {summary}\n\n**ID:** {webpage_id}"
        ).send()
    except Exception as e:
        await cl.Message(content=f"❌ Error saving webpage: {str(e)}").send()


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
        await cl.Message(content=f"❌ Error deleting webpage: {str(e)}").send()
