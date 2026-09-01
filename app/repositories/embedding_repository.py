from __future__ import annotations

from typing import Any, Optional
from uuid import UUID

from sqlalchemy import and_, delete, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.orm import DatabaseEmbedding as ORMEmbedding

logger = get_logger(__name__)


class EmbeddingRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def upsert_embedding(
        self,
        context_version_id: UUID,
        content_type: str,
        content_id: UUID | str,
        content_text: str,
        embedding: list[float],
        metadata: Optional[dict[str, Any]] = None,
    ) -> None:
        content_id_str = str(content_id)
        existing_stmt = (
            select(ORMEmbedding)
            .where(
                and_(
                    ORMEmbedding.context_version_id == context_version_id,
                    ORMEmbedding.content_type == content_type,
                    ORMEmbedding.content_id == content_id_str,
                )
            )
        )
        result = await self.session.execute(existing_stmt)
        existing = result.scalar_one_or_none()

        if existing is not None:
            existing.content_text = content_text
            existing.embedding = embedding
            existing.embedding_metadata = metadata
            logger.debug(
                "Updated existing embedding",
                extra={
                    "context_version_id": str(context_version_id),
                    "content_type": content_type,
                    "content_id": content_id_str,
                },
            )
        else:
            orm_emb = ORMEmbedding(
                context_version_id=context_version_id,
                content_type=content_type,
                content_id=content_id_str,
                content_text=content_text,
                embedding=embedding,
                embedding_metadata=metadata,
            )
            self.session.add(orm_emb)
            logger.debug(
                "Inserted new embedding",
                extra={
                    "context_version_id": str(context_version_id),
                    "content_type": content_type,
                    "content_id": content_id_str,
                },
            )
        await self.session.flush()

    async def bulk_upsert(
        self,
        rows: list[dict[str, Any]],
    ) -> None:
        if not rows:
            return
        for row in rows:
            await self.upsert_embedding(
                context_version_id=row["context_version_id"],
                content_type=row["content_type"],
                content_id=row["content_id"],
                content_text=row.get("content_text", ""),
                embedding=row["embedding"],
                metadata=row.get("metadata"),
            )
        logger.info(
            "Bulk upserted embeddings",
            extra={"count": len(rows)},
        )

    async def cosine_search(
        self,
        query_embedding: list[float],
        top_k: int,
        context_version_id: Optional[UUID] = None,
        content_types: Optional[list[str]] = None,
    ) -> list[tuple[str, str, Optional[str], Optional[dict[str, Any]], float]]:
        if top_k <= 0:
            return []

        vector_param = f"[{', '.join(str(float(v)) for v in query_embedding)}]"

        sql = text(
            """
            SELECT
                content_type,
                content_id,
                content_text,
                embedding_metadata,
                1 - (embedding <=> CAST(:query_vec AS vector(1536))) AS score
            FROM database_embeddings
            WHERE embedding IS NOT NULL
            """
        )

        where_clauses: list[str] = []
        params: dict[str, Any] = {"query_vec": vector_param, "top_k": top_k}

        if context_version_id is not None:
            where_clauses.append("context_version_id = :context_version_id")
            params["context_version_id"] = context_version_id

        if content_types:
            placeholders = []
            for i, ct in enumerate(content_types):
                key = f"ct_{i}"
                placeholders.append(f":{key}")
                params[key] = ct
            where_clauses.append(f"content_type IN ({', '.join(placeholders)})")

        if where_clauses:
            sql_text = str(sql)
            sql_text += " AND " + " AND ".join(where_clauses)
            sql_text += " ORDER BY score DESC LIMIT :top_k"
            sql = text(sql_text)
        else:
            sql_text = str(sql)
            sql_text += " ORDER BY score DESC LIMIT :top_k"
            sql = text(sql_text)

        result = await self.session.execute(sql, params)
        rows = result.fetchall()
        output: list[tuple[str, str, Optional[str], Optional[dict[str, Any]], float]] = []
        for row in rows:
            score = float(row[4]) if row[4] is not None else 0.0
            output.append(
                (
                    str(row[0]),
                    str(row[1]),
                    row[2] if row[2] is not None else None,
                    row[3] if row[3] is not None else None,
                    score,
                )
            )
        logger.debug(
            "Cosine embedding search completed",
            extra={
                "top_k": top_k,
                "results": len(output),
                "context_version_id": (
                    str(context_version_id) if context_version_id else None
                ),
                "content_types": content_types,
            },
        )
        return output

    async def delete_by_context(self, context_version_id: UUID) -> int:
        stmt = delete(ORMEmbedding).where(
            ORMEmbedding.context_version_id == context_version_id
        )
        result = await self.session.execute(stmt)
        deleted = int(result.rowcount or 0)
        await self.session.flush()
        logger.info(
            "Deleted embeddings by context",
            extra={
                "context_version_id": str(context_version_id),
                "deleted": deleted,
            },
        )
        return deleted

    async def count_for_context(self, context_version_id: UUID) -> int:
        stmt = (
            select(func.count(ORMEmbedding.id))
            .where(ORMEmbedding.context_version_id == context_version_id)
        )
        result = await self.session.execute(stmt)
        count = result.scalar_one_or_none()
        value = int(count) if count is not None else 0
        logger.debug(
            "Counted embeddings for context",
            extra={
                "context_version_id": str(context_version_id),
                "count": value,
            },
        )
        return value
