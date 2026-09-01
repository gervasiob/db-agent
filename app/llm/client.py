from __future__ import annotations

import time
from typing import Any, Optional, Type, TypeVar, Union

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage
from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.prompt_values import PromptValue
from langchain_core.runnables import Runnable
from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from pydantic import BaseModel, ValidationError
from tenacity import (
    RetryError,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_random_exponential,
)

from app.core.config import settings
from app.core.exceptions import LLMGenerationError, SemanticAnalysisError
from app.core.logging import get_logger

logger = get_logger(__name__)

T = TypeVar("T", bound=BaseModel)

_RATE_LIMIT_EXCEPTIONS: tuple[type[BaseException], ...] = ()
_TIMEOUT_EXCEPTIONS: tuple[type[BaseException], ...] = ()

try:
    from openai import APIConnectionError, APITimeoutError, RateLimitError

    _RATE_LIMIT_EXCEPTIONS = (RateLimitError,)
    _TIMEOUT_EXCEPTIONS = (APITimeoutError, APIConnectionError)
except ImportError:
    pass

_RETRYABLE_EXCEPTIONS = _RATE_LIMIT_EXCEPTIONS + _TIMEOUT_EXCEPTIONS


class ChatModelCache:
    _default_chat: Optional[ChatOpenAI] = None
    _default_embeddings: Optional[OpenAIEmbeddings] = None
    _chat_instances: dict[tuple[str, float, int], ChatOpenAI] = {}
    _embeddings_instances: dict[str, OpenAIEmbeddings] = {}

    @classmethod
    def get_default_chat(cls) -> ChatOpenAI:
        if cls._default_chat is None:
            cls._default_chat = get_chat_model(
                temperature=settings.OPENAI_TEMPERATURE,
                model=settings.OPENAI_MODEL,
            )
        return cls._default_chat

    @classmethod
    def get_default_embeddings(cls) -> OpenAIEmbeddings:
        if cls._default_embeddings is None:
            cls._default_embeddings = get_embeddings_model(
                model=settings.OPENAI_EMBEDDING_MODEL,
            )
        return cls._default_embeddings

    @classmethod
    def get_cached_chat(cls, model: str, temperature: float, max_tokens: int) -> ChatOpenAI:
        key = (model, temperature, max_tokens)
        if key not in cls._chat_instances:
            cls._chat_instances[key] = get_chat_model(
                temperature=temperature,
                model=model,
                max_tokens=max_tokens,
            )
        return cls._chat_instances[key]

    @classmethod
    def get_cached_embeddings(cls, model: str) -> OpenAIEmbeddings:
        if model not in cls._embeddings_instances:
            cls._embeddings_instances[model] = get_embeddings_model(model=model)
        return cls._embeddings_instances[model]

    @classmethod
    def clear(cls) -> None:
        cls._default_chat = None
        cls._default_embeddings = None
        cls._chat_instances.clear()
        cls._embeddings_instances.clear()


def get_chat_model(
    temperature: Optional[float] = None,
    model: Optional[str] = None,
    **kwargs: Any,
) -> ChatOpenAI:
    api_key = settings.OPENAI_API_KEY
    if not api_key:
        raise LLMGenerationError(
            detail="OPENAI_API_KEY is not configured. Please set it in your environment variables.",
            extra={"setting": "OPENAI_API_KEY"},
        )

    resolved_model = model or settings.OPENAI_MODEL
    resolved_temperature = temperature if temperature is not None else settings.OPENAI_TEMPERATURE
    resolved_max_tokens = kwargs.pop("max_tokens", settings.OPENAI_MAX_TOKENS)

    default_kwargs: dict[str, Any] = {
        "model": resolved_model,
        "temperature": resolved_temperature,
        "max_tokens": resolved_max_tokens,
        "api_key": api_key,
    }
    default_kwargs.update(kwargs)

    try:
        chat = ChatOpenAI(**default_kwargs)
        logger.debug(
            "Created ChatOpenAI instance",
            extra={"model": resolved_model, "temperature": resolved_temperature},
        )
        return chat
    except Exception as exc:
        raise LLMGenerationError(
            detail=f"Failed to initialize ChatOpenAI model {resolved_model}: {exc!s}",
            extra={"model": resolved_model, "error_type": type(exc).__name__},
        ) from exc


def get_embeddings_model(model: Optional[str] = None) -> OpenAIEmbeddings:
    api_key = settings.OPENAI_API_KEY
    if not api_key:
        raise LLMGenerationError(
            detail="OPENAI_API_KEY is not configured. Please set it in your environment variables.",
            extra={"setting": "OPENAI_API_KEY"},
        )

    resolved_model = model or settings.OPENAI_EMBEDDING_MODEL

    try:
        embeddings = OpenAIEmbeddings(
            model=resolved_model,
            api_key=api_key,
        )
        logger.debug(
            "Created OpenAIEmbeddings instance",
            extra={"model": resolved_model},
        )
        return embeddings
    except Exception as exc:
        raise LLMGenerationError(
            detail=f"Failed to initialize OpenAIEmbeddings model {resolved_model}: {exc!s}",
            extra={"model": resolved_model, "error_type": type(exc).__name__},
        ) from exc


async def async_generate(
    prompt_value: Union[PromptValue, list[BaseMessage]],
    *,
    structured_schema: Optional[Type[T]] = None,
    temperature: Optional[float] = None,
    extra_callbacks: Optional[list[BaseCallbackHandler]] = None,
    **llm_kwargs: Any,
) -> Union[Any, T]:
    chat_model = ChatModelCache.get_default_chat()
    if temperature is not None:
        chat_model = chat_model.bind(temperature=temperature)
    if llm_kwargs:
        chat_model = chat_model.bind(**llm_kwargs)

    messages: list[BaseMessage]
    if isinstance(prompt_value, PromptValue):
        messages = prompt_value.to_messages()
    else:
        messages = prompt_value

    invoke_kwargs: dict[str, Any] = {}
    if extra_callbacks:
        invoke_kwargs["config"] = {"callbacks": extra_callbacks}

    if structured_schema is not None:
        chain: Runnable = chat_model.with_structured_output(structured_schema)
        try:
            result = await chain.ainvoke(messages, **invoke_kwargs)
            return result
        except Exception as exc:
            raise LLMGenerationError(
                detail=f"Structured LLM generation failed: {exc!s}",
                extra={
                    "schema": structured_schema.__name__,
                    "error_type": type(exc).__name__,
                    "message_count": len(messages),
                },
            ) from exc
    else:
        try:
            result = await chat_model.ainvoke(messages, **invoke_kwargs)
            return result
        except Exception as exc:
            raise LLMGenerationError(
                detail=f"LLM generation failed: {exc!s}",
                extra={
                    "error_type": type(exc).__name__,
                    "message_count": len(messages),
                },
            ) from exc


async def async_generate_structured(
    messages: list[BaseMessage],
    pydantic_schema: Type[T],
    retries: int = 2,
    *,
    temperature: Optional[float] = None,
    extra_callbacks: Optional[list[BaseCallbackHandler]] = None,
) -> T:
    start_time = time.perf_counter()
    schema_name = pydantic_schema.__name__

    attempt = 0
    last_error: Optional[Exception] = None

    while attempt <= retries:
        attempt += 1
        try:
            retryable_generate = retry(
                wait=wait_random_exponential(min=1, max=30),
                stop=stop_after_attempt(3),
                retry=retry_if_exception_type(_RETRYABLE_EXCEPTIONS) if _RETRYABLE_EXCEPTIONS else (),
                reraise=True,
            )(_async_generate_structured_attempt)

            result = await retryable_generate(
                messages=messages,
                pydantic_schema=pydantic_schema,
                temperature=temperature,
                extra_callbacks=extra_callbacks,
                attempt=attempt,
            )

            elapsed_ms = int((time.perf_counter() - start_time) * 1000)
            logger.info(
                "Structured LLM generation succeeded",
                extra={
                    "schema": schema_name,
                    "attempt": attempt,
                    "elapsed_ms": elapsed_ms,
                },
            )
            return result

        except ValidationError as exc:
            last_error = exc
            elapsed_ms = int((time.perf_counter() - start_time) * 1000)
            logger.warning(
                "Pydantic validation error on structured output, retrying",
                extra={
                    "schema": schema_name,
                    "attempt": attempt,
                    "elapsed_ms": elapsed_ms,
                    "error_count": len(exc.errors()),
                    "errors": [e.get("msg", str(e)) for e in exc.errors()[:5]],
                },
            )
            if attempt <= retries:
                continue

        except RetryError as exc:
            last_error = exc
            logger.warning(
                "LLM retry exhausted for rate limit/timeout",
                extra={
                    "schema": schema_name,
                    "attempt": attempt,
                    "error_type": type(exc).__name__,
                },
            )

        except LLMGenerationError as exc:
            last_error = exc
            if attempt <= retries and _is_retryable_llm_error(exc):
                continue
            raise

        except Exception as exc:
            last_error = exc
            logger.error(
                "Unexpected error in structured LLM generation",
                extra={
                    "schema": schema_name,
                    "attempt": attempt,
                    "error_type": type(exc).__name__,
                },
                exc_info=exc,
            )
            raise

    elapsed_ms = int((time.perf_counter() - start_time) * 1000)
    raise SemanticAnalysisError(
        detail=f"Failed to generate valid structured output for schema {schema_name} after {retries + 1} attempts",
        extra={
            "schema": schema_name,
            "attempts": attempt,
            "elapsed_ms": elapsed_ms,
            "last_error": str(last_error) if last_error else None,
        },
    )


async def _async_generate_structured_attempt(
    messages: list[BaseMessage],
    pydantic_schema: Type[T],
    temperature: Optional[float],
    extra_callbacks: Optional[list[BaseCallbackHandler]],
    attempt: int,
) -> T:
    chat_model = ChatModelCache.get_default_chat()
    if temperature is not None:
        chat_model = chat_model.bind(temperature=temperature)

    invoke_kwargs: dict[str, Any] = {}
    if extra_callbacks:
        invoke_kwargs["config"] = {"callbacks": extra_callbacks}

    chain: Runnable = chat_model.with_structured_output(pydantic_schema)
    result = await chain.ainvoke(messages, **invoke_kwargs)

    if not isinstance(result, pydantic_schema):
        raise LLMGenerationError(
            detail=f"Structured output did not produce expected type {pydantic_schema.__name__}",
            extra={
                "schema": pydantic_schema.__name__,
                "actual_type": type(result).__name__,
                "attempt": attempt,
            },
        )

    return result


def _is_retryable_llm_error(exc: LLMGenerationError) -> bool:
    detail_lower = (exc.detail or "").lower()
    retryable_markers = (
        "rate limit",
        "ratelimit",
        "429",
        "timeout",
        "timed out",
        "connection",
        "503",
        "502",
        "temporarily unavailable",
    )
    return any(marker in detail_lower for marker in retryable_markers)


async def embed_documents(texts: list[str], *, model: Optional[str] = None) -> list[list[float]]:
    embeddings_model = (
        ChatModelCache.get_cached_embeddings(model) if model else ChatModelCache.get_default_embeddings()
    )
    try:
        vectors = await embeddings_model.aembed_documents(texts)
        logger.debug(
            "Embedded documents",
            extra={"count": len(texts), "model": getattr(embeddings_model, "model", None)},
        )
        return [list(v) for v in vectors]
    except Exception as exc:
        raise LLMGenerationError(
            detail=f"Document embedding failed: {exc!s}",
            extra={
                "count": len(texts),
                "model": model or settings.OPENAI_EMBEDDING_MODEL,
                "error_type": type(exc).__name__,
            },
        ) from exc


async def embed_query(text: str, *, model: Optional[str] = None) -> list[float]:
    embeddings_model = (
        ChatModelCache.get_cached_embeddings(model) if model else ChatModelCache.get_default_embeddings()
    )
    try:
        vector = await embeddings_model.aembed_query(text)
        logger.debug(
            "Embedded query",
            extra={"text_length": len(text), "model": getattr(embeddings_model, "model", None)},
        )
        return list(vector)
    except Exception as exc:
        raise LLMGenerationError(
            detail=f"Query embedding failed: {exc!s}",
            extra={
                "text_length": len(text),
                "model": model or settings.OPENAI_EMBEDDING_MODEL,
                "error_type": type(exc).__name__,
            },
        ) from exc


__all__ = [
    "ChatModelCache",
    "get_chat_model",
    "get_embeddings_model",
    "async_generate",
    "async_generate_structured",
    "embed_documents",
    "embed_query",
]
