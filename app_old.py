import chainlit as cl
from urllib.parse import unquote, urlparse, parse_qs
from contextvars import ContextVar
import importlib
from fastapi import Body
from chainlit.server import app as chainlit_app
import os
from pathlib import Path
from typing import Optional, Tuple

import boto3
import chainlit as cl
from langchain_aws import ChatBedrockConverse
from langchain_core.output_parsers import StrOutputParser
from langchain_ollama import ChatOllama

try:
    from langchain.prompts import ChatPromptTemplate  # type: ignore
except ImportError:
    from langchain_core.prompts import ChatPromptTemplate

try:
    from langchain_community.chat_models import init_model  # type: ignore
except ImportError:
    init_model = None

try:
    from langchain.schema import StrOutputParser as LegacyStrOutputParser  # type: ignore
except ImportError:
    LegacyStrOutputParser = StrOutputParser

from botocore.exceptions import NoCredentialsError, ProfileNotFound
from dotenv import load_dotenv


# Load .env from both workspace root and app directory if present.
WORKSPACE_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(WORKSPACE_ROOT / ".env")
load_dotenv(Path(__file__).resolve().parent / ".env")


def get_runtime_config() -> Tuple[Optional[str], str, Optional[str]]:
    aws_profile = os.getenv("AWS_PROFILE")
    aws_region = os.getenv("AWS_REGION", "us-west-2")
    model_id = os.getenv("BEDROCK_MODEL_ID")
    return aws_profile, aws_region, model_id


def create_aws_session(aws_profile: Optional[str]) -> boto3.Session:
    try:
        return (
            boto3.Session(profile_name=aws_profile) if aws_profile else boto3.Session()
        )
    except ProfileNotFound:
        print(
            f"AWS profile '{aws_profile}' was not found. Falling back to default AWS credential chain."
        )
        return boto3.Session()


cl_context_module = importlib.import_module("chainlit.context")
cl_step_module = importlib.import_module("chainlit.step")
cl_message_module = importlib.import_module("chainlit.message")

# Chainlit 2.3 can raise LookupError for local_steps in some async contexts.
# Replace it with a defaulted ContextVar so .get() is always safe.
patched_local_steps = ContextVar("local_steps", default=None)
cl_context_module.local_steps = patched_local_steps
cl_step_module.local_steps = patched_local_steps
cl_message_module.local_steps = patched_local_steps

LAST_PAGE_CONTEXT = {
    "page_url": "Unknown",
    "page_title": "Unknown",
    "page_text": "",
}


def ensure_local_steps_context() -> None:
    # Explicitly seed per-task context to avoid Chainlit internals using an unset var.
    if patched_local_steps.get() is None:
        patched_local_steps.set([])


def read_query_param(params, key, default=""):
    value = params.get(key, default)

    # Chainlit/session middleware may store query values as either list[str] or str.
    if isinstance(value, list):
        value = value[0] if value else default

    if value is None:
        value = default

    return unquote(str(value))


def extract_params_from_referer() -> dict:
    referer = cl.user_session.get("http_referer")
    if not referer:
        return {}

    try:
        parsed = urlparse(str(referer))
        return parse_qs(parsed.query)
    except Exception:
        return {}


def normalize_str(value, default="") -> str:
    if value is None:
        return default
    return str(value).strip() or default


def format_history(history: list) -> str:
    lines = []
    for turn in history[-12:]:
        role = str(turn.get("role", "user"))
        content = str(turn.get("content", "")).strip()
        if content:
            lines.append(f"{role}: {content}")
    return "\n".join(lines)


@chainlit_app.post("/ext/context")
async def set_ext_context(payload: dict = Body(default_factory=dict)):
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
    ensure_local_steps_context()

    # Depending on Chainlit version, query params may not be exposed directly.
    # Fallback to parsing session http_referer, which contains iframe URL query params.
    params = cl.user_session.get("query_params") or {}
    if not params:
        params = extract_params_from_referer()

    page_url = read_query_param(params, "page_url", "Unknown")
    page_title = read_query_param(params, "page_title", "Unknown")
    page_text = read_query_param(params, "page_text", "")

    if not page_text:
        page_url = LAST_PAGE_CONTEXT.get("page_url", "Unknown")
        page_title = LAST_PAGE_CONTEXT.get("page_title", "Unknown")
        page_text = LAST_PAGE_CONTEXT.get("page_text", "")

    print(f"Page URL: {page_url}")
    print(f"Page Title: {page_title}")
    print(f"Page Text Length: {len(page_text)}")
    print(f"HTTP Referer: {cl.user_session.get('http_referer')}")

    if not page_text:
        await cl.Message(content="⚠️ No page content received.").send()
        return

    # Store context for the whole session
    cl.user_session.set(
        "system_prompt",
        f"""
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
                """,
    )

    # aws_profile, aws_region, model_id = get_runtime_config()
    # print("Initializing AWS Bedrock runtime configuration.")

    # if not model_id:
    #     await cl.Message(
    #         content="BEDROCK_MODEL_ID is not set. Add it to your environment before starting chat."
    #     ).send()
    #     return

    # session = create_aws_session(aws_profile)

    # 1. Initialize the Ollama model
    llm = ChatOllama(model="chatside-qwen3")

    # if init_model is not None:
    #     llm = init_model(
    #         model=model_id,
    #         model_provider="bedrock_converse",
    #         region_name=aws_region,
    #         profile_name=aws_profile if aws_profile else None,
    #     )
    # else:
    #     llm = ChatBedrockConverse(
    #         model=model_id,
    #         region_name=aws_region,
    #         credentials_profile_name=aws_profile if aws_profile else None,
    #     )

    # prompt = ChatPromptTemplate.from_template(
    #     """{system_prompt}

    #         Conversation so far:
    #         {history}

    #         User question:
    #         {input}""")

    prompt = ChatPromptTemplate.from_template(
            """{system_prompt}

Conversation so far:
{history}

User question:
{input}""")
    output_parser = LegacyStrOutputParser()

    # Use the pipe operator pattern (recommended for LangChain 0.1+)
    chain = prompt | llm | output_parser

    cl.user_session.set("llm_chain", chain)

    # try:
    #     identity = session.client(service_name="sts").get_caller_identity()
    #     if identity.get("Arn"):
    #         print("AWS credentials validated via STS.")
    # except NoCredentialsError:
    #     await cl.Message(
    #         content="AWS credentials were not found. Configure your profile before sending prompts."
    #     ).send()
    #     return

    print("Chat session initialized successfully.")

    await cl.Message(
        content=f"✅ **{page_title}** loaded!\n\nAsk me anything about this page."
    ).send()


@cl.on_message
async def on_message(message: cl.Message):
    
    ensure_local_steps_context()

    system_prompt = cl.user_session.get("system_prompt", "")
    history = cl.user_session.get("history", [])

    history.append({"role": "user", "content": message.content})
    history_text = format_history(history)

    

    cl.user_session.set("history", history)

    llm_chain = cl.user_session.get("llm_chain")

    if not llm_chain:
        await cl.Message(
            content="Model is not initialized. Start a new chat session."
        ).send()
        return

    try:
        # Use ainvoke with the pipe operator chain
        text = await llm_chain.ainvoke(
            {
                "input": message.content,
                "history": history_text,
                "system_prompt": system_prompt
            }
        )
    except NoCredentialsError:
        await cl.Message(
            content="AWS credentials are missing. Please configure AWS auth."
        ).send()
        return

    history.append({"role": "assistant", "content": text})
    cl.user_session.set("history", history)

    await cl.Message(content=text).send()
