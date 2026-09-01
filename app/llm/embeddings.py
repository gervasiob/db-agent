from __future__ import annotations

import math
from typing import Any, Optional

from app.core.logging import get_logger
from app.llm.client import embed_documents, embed_query

logger = get_logger(__name__)


async def async_embed_texts(
    texts: list[str],
    batch_size: int = 100,
    *,
    model: Optional[str] = None,
) -> list[list[float]]:
    if not texts:
        return []

    total = len(texts)
    all_vectors: list[list[float]] = []

    for start in range(0, total, batch_size):
        end = min(start + batch_size, total)
        batch = texts[start:end]
        logger.debug(
            "Embedding batch",
            extra={"batch_start": start, "batch_end": end, "batch_size": len(batch), "total": total},
        )
        batch_vectors = await embed_documents(batch, model=model)
        all_vectors.extend(batch_vectors)

    if len(all_vectors) != total:
        logger.warning(
            "Embedding count mismatch",
            extra={"expected": total, "actual": len(all_vectors)},
        )

    return all_vectors


async def async_embed_query(
    text: str,
    *,
    model: Optional[str] = None,
) -> list[float]:
    if not text or not text.strip():
        raise ValueError("Cannot embed empty text")
    return await embed_query(text, model=model)


def build_document_for_embedding(
    type: str,
    name: str,
    description: str,
    aliases: Optional[list[str]] = None,
    tables: Optional[list[str]] = None,
    columns: Optional[list[str]] = None,
) -> str:
    parts: list[str] = []

    parts.append(f"Type: {type}")
    parts.append(f"Name: {name}")

    if aliases:
        cleaned_aliases = [a.strip() for a in aliases if a and a.strip()]
        if cleaned_aliases:
            parts.append(f"Aliases: {', '.join(cleaned_aliases)}")

    if description and description.strip():
        parts.append(f"Desc: {description.strip()}")

    if tables:
        cleaned_tables = [t.strip() for t in tables if t and t.strip()]
        if cleaned_tables:
            parts.append(f"Tables: {', '.join(cleaned_tables)}")

    if columns:
        cleaned_columns = [c.strip() for c in columns if c and c.strip()]
        if cleaned_columns:
            parts.append(f"Cols: {', '.join(cleaned_columns)}")

    return " | ".join(parts)


def normalize_vector(v: list[float]) -> list[float]:
    if not v:
        return []

    squared_sum = 0.0
    for x in v:
        squared_sum += x * x

    norm = math.sqrt(squared_sum)
    if norm == 0.0:
        return [0.0] * len(v)

    return [x / norm for x in v]


def compute_cosine_similarity(a: list[float], b: list[float]) -> float:
    if not a or not b:
        return 0.0
    if len(a) != len(b):
        logger.warning(
            "Vector length mismatch for cosine similarity",
            extra={"len_a": len(a), "len_b": len(b)},
        )
        return 0.0

    dot_product = 0.0
    norm_a_sq = 0.0
    norm_b_sq = 0.0

    for i in range(len(a)):
        ai = a[i]
        bi = b[i]
        dot_product += ai * bi
        norm_a_sq += ai * ai
        norm_b_sq += bi * bi

    norm_a = math.sqrt(norm_a_sq)
    norm_b = math.sqrt(norm_b_sq)

    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0

    cosine = dot_product / (norm_a * norm_b)
    return max(-1.0, min(1.0, cosine))


__all__ = [
    "async_embed_texts",
    "async_embed_query",
    "build_document_for_embedding",
    "normalize_vector",
    "compute_cosine_similarity",
]
