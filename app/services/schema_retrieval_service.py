from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from typing import Any, Optional

from app.core.config import settings
from app.core.exceptions import InvalidInputError, SchemaRetrievalError
from app.core.logging import get_logger
from app.llm.embeddings import (
    async_embed_query,
    compute_cosine_similarity,
    normalize_vector,
)
from app.models.context import (
    DatabaseKnowledgeMap,
    MatchType,
    RetrievalContext,
    RetrievalMatch,
)
from app.models.database import RelationshipMetadata, TableMetadata
from app.models.semantic import (
    BusinessConcept,
    BusinessEntity,
    BusinessMetric,
    Dimension,
    RelationshipEdge,
    RelationshipEdgeType,
)

logger = get_logger(__name__)


WORD_TOKEN_RE = re.compile(r"[a-záéíóúñ0-9]+", re.IGNORECASE)


@dataclass
class _ScoredItem:
    key: str
    obj: Any
    score: float
    match_type: MatchType


def _tokenize(text: str) -> list[str]:
    if not text:
        return []
    return [m.group().lower() for m in WORD_TOKEN_RE.finditer(text or "")]


def _bm25_like_score(
    query_tokens: list[str],
    doc_tokens: list[str],
    doc_freq: Counter[str],
    total_docs: int,
    *,
    k1: float = 1.5,
    b: float = 0.75,
    avg_dl: float = 12.0,
) -> float:
    if not query_tokens or not doc_tokens:
        return 0.0
    doc_len = max(1, len(doc_tokens))
    token_counts = Counter(doc_tokens)
    score = 0.0
    for qt in set(query_tokens):
        if qt not in token_counts:
            continue
        tf = token_counts[qt]
        df = doc_freq.get(qt, 1)
        idf = math.log(max(1.0, (total_docs - df + 0.5) / (df + 0.5) + 1.0))
        denom = tf + k1 * (1.0 - b + b * (doc_len / max(1.0, avg_dl)))
        score += idf * ((tf * (k1 + 1.0)) / denom)
    return score


class SchemaRetrievalService:
    def __init__(
        self,
        embedding_repository: Any = None,
        top_k: Optional[int] = None,
        graph_service: Any = None,
    ) -> None:
        self.embedding_repository = embedding_repository
        self.graph_service = graph_service
        self.default_top_k = top_k if top_k is not None else settings.SCHEMA_RETRIEVAL_TOP_K
        logger.info(
            "SchemaRetrievalService initialized",
            extra={
                "has_embedding_repo": embedding_repository is not None,
                "has_graph_service": graph_service is not None,
                "default_top_k": self.default_top_k,
            },
        )

    async def retrieve_context(
        self,
        question: str,
        knowledge_map: DatabaseKnowledgeMap,
        top_k: Optional[int] = None,
        embedding_enabled: Optional[bool] = None,
    ) -> RetrievalContext:
        if not question or not question.strip():
            raise InvalidInputError(
                detail="question is required for schema retrieval",
                field="question",
            )
        if not knowledge_map:
            raise InvalidInputError(
                detail="knowledge_map is required for schema retrieval",
                field="knowledge_map",
            )

        k = top_k if top_k is not None else self.default_top_k
        q_lower = question.strip().lower()
        logger.info(
            "Starting schema retrieval",
            extra={
                "question_len": len(question),
                "top_k": k,
                "embedding_enabled": embedding_enabled,
            },
        )

        try:
            query_tokens = _tokenize(question)
            query_set = set(query_tokens)

            doc_corpus: list[tuple[MatchType, str, Any, list[str], str, str, list[str]]] = []
            for m in knowledge_map.metrics or []:
                doc_corpus.append(self._build_doc(MatchType.METRIC, m, m.code, m.name, m.description, m.aliases, m.source_tables))
            for c in knowledge_map.concepts or []:
                doc_corpus.append(self._build_doc(MatchType.CONCEPT, c, c.code, c.name, c.description, c.aliases, c.tables))
            for e in knowledge_map.entities or []:
                doc_corpus.append(self._build_doc(MatchType.ENTITY, e, e.code, e.name, e.description, e.aliases, e.tables))
            for d in knowledge_map.domains or []:
                doc_corpus.append(self._build_doc(MatchType.DOMAIN, d, d.code, d.name, d.description, d.aliases, d.tables))
            for dim in knowledge_map.dimensions or []:
                tbls = [dim.source_table] if dim.source_table else []
                doc_corpus.append(self._build_doc(MatchType.TABLE if False else MatchType.DIMENSION, dim, dim.code, dim.name, dim.description, dim.aliases, tbls))
            for t in knowledge_map.tables or []:
                full_name = f"{t.schema_name}.{t.table_name}"
                col_names = [c.name for c in t.columns]
                aliases = [t.comment] if t.comment else []
                doc_corpus.append(self._build_doc(MatchType.TABLE, t, full_name, full_name, t.comment or f"Table {full_name}", aliases, [full_name], extra_terms=col_names))
            for term in knowledge_map.terminology or []:
                doc_corpus.append(self._build_doc(MatchType.TERMINOLOGY, term, term.target_code or term.term, term.term, term.canonical_form, list(term.aliases) + [term.canonical_form], []))

            total_docs = max(1, len(doc_corpus))
            doc_freq: Counter[str] = Counter()
            all_token_lists: list[list[str]] = []
            for _mt, _key, _obj, tokens, _n, _d, _t in doc_corpus:
                for tok in set(tokens):
                    doc_freq[tok] += 1
                all_token_lists.append(tokens)
            avg_dl = sum(len(t) for t in all_token_lists) / total_docs if total_docs else 10.0

            lexical_scores: list[_ScoredItem] = []
            for i, (mt, key, obj, tokens, _n, _d, _t) in enumerate(doc_corpus):
                bm = _bm25_like_score(
                    query_tokens,
                    tokens,
                    doc_freq,
                    total_docs,
                    avg_dl=avg_dl,
                )
                overlap = len(query_set & set(tokens))
                jaccard = overlap / max(1.0, len(query_set | set(tokens)))
                score = 0.65 * (bm / max(1.0, bm + 2.0)) + 0.35 * jaccard
                lexical_scores.append(_ScoredItem(key=key, obj=obj, score=score, match_type=mt))

            lexical_scores.sort(key=lambda s: -s.score)

            vector_scores: list[_ScoredItem] = []
            use_embeddings = embedding_enabled if embedding_enabled is not None else (
                self.embedding_repository is not None and settings.OPENAI_API_KEY is not None
            )
            if use_embeddings:
                try:
                    query_vector_raw = await async_embed_query(question)
                    query_vector = normalize_vector(query_vector_raw)
                    if self.embedding_repository and hasattr(self.embedding_repository, "cosine_search"):
                        try:
                            raw_hits = await self.embedding_repository.cosine_search(
                                vector=query_vector,
                                top_k=k * 2,
                            )
                            for hit in raw_hits or []:
                                target_code = getattr(hit, "target_code", None) or getattr(hit, "code", None)
                                score = float(getattr(hit, "score", 0.0))
                                match_type_raw = getattr(hit, "match_type", None) or getattr(hit, "type", None)
                                mt = MatchType(str(match_type_raw).upper()) if match_type_raw else MatchType.OTHER
                                vector_scores.append(
                                    _ScoredItem(
                                        key=str(target_code or getattr(hit, "id", "")),
                                        obj=hit,
                                        score=score,
                                        match_type=mt,
                                    )
                                )
                        except Exception as exc:
                            logger.warning(
                                "Embedding repository cosine_search failed, fallback to lexical-only",
                                extra={"error": str(exc)},
                            )
                    if not vector_scores:
                        vector_scores = self._score_without_pgvector(
                            query_vector,
                            doc_corpus,
                            top_n=max(5, k * 2),
                        )
                except Exception as exc:
                    logger.warning(
                        "Embedding path failed, falling back to lexical only",
                        extra={"error": str(exc)},
                    )
                    vector_scores = []
            else:
                logger.debug("Embedding retrieval disabled or unavailable")

            combined_by_key: dict[tuple[MatchType, str], _ScoredItem] = {}
            for s in lexical_scores:
                kk = (s.match_type, s.key)
                existing = combined_by_key.get(kk)
                if existing is None:
                    combined_by_key[kk] = _ScoredItem(
                        key=s.key,
                        obj=s.obj,
                        score=s.score * 0.55,
                        match_type=s.match_type,
                    )
                else:
                    existing.score = max(existing.score, s.score * 0.55)

            for s in vector_scores:
                kk = (s.match_type, s.key)
                existing = combined_by_key.get(kk)
                if existing is None:
                    combined_by_key[kk] = _ScoredItem(
                        key=s.key,
                        obj=s.obj,
                        score=s.score * 0.75,
                        match_type=s.match_type,
                    )
                else:
                    existing.score = existing.score + s.score * 0.75

            ranked: list[_ScoredItem] = sorted(
                combined_by_key.values(),
                key=lambda x: -x.score,
            )

            type_weights = {
                MatchType.METRIC: 1.10,
                MatchType.CONCEPT: 1.05,
                MatchType.ENTITY: 1.02,
                MatchType.DOMAIN: 0.95,
                MatchType.TABLE: 1.00,
                MatchType.COLUMN: 0.90,
                MatchType.DIMENSION: 0.92,
                MatchType.TERMINOLOGY: 0.85,
            }
            for s in ranked:
                s.score *= type_weights.get(s.match_type, 1.0)
            ranked.sort(key=lambda x: -x.score)

            top_matches: list[RetrievalMatch] = []
            matched_table_names: set[str] = set()
            matched_entity_codes: set[str] = set()
            matched_metric_codes: set[str] = set()
            matched_concept_codes: set[str] = set()
            matched_dim_codes: set[str] = set()
            matched_domain_codes: set[str] = set()

            for scored in ranked[: max(k * 2, 12)]:
                rm = self._to_retrieval_match(scored)
                if rm is None:
                    continue
                top_matches.append(rm)
                for t in rm.tables or []:
                    matched_table_names.add(t)
                if scored.match_type == MatchType.ENTITY:
                    matched_entity_codes.add(scored.key)
                elif scored.match_type == MatchType.METRIC:
                    matched_metric_codes.add(scored.key)
                elif scored.match_type == MatchType.CONCEPT:
                    matched_concept_codes.add(scored.key)
                elif scored.match_type == MatchType.DIMENSION:
                    matched_dim_codes.add(scored.key)
                elif scored.match_type == MatchType.DOMAIN:
                    matched_domain_codes.add(scored.key)

            graph = knowledge_map.relationship_graph
            if graph and self.graph_service is not None and hasattr(self.graph_service, "expand_related_tables"):
                seed = list(matched_table_names)
                try:
                    expanded = self.graph_service.expand_related_tables(
                        graph,
                        seed_tables=seed,
                        max_hops=2,
                    )
                    for t in expanded or []:
                        matched_table_names.add(t)
                except Exception as exc:
                    logger.warning(
                        "Graph expansion failed",
                        extra={"error": str(exc)},
                    )
            elif graph and graph.edges:
                adjacency: dict[str, set[str]] = {}
                for edge in graph.edges:
                    adjacency.setdefault(edge.source_table, set()).add(edge.target_table)
                    adjacency.setdefault(edge.target_table, set()).add(edge.source_table)
                hop1: set[str] = set(matched_table_names)
                for _ in range(2):
                    additions: set[str] = set()
                    for t in hop1:
                        additions |= adjacency.get(t, set())
                    if not additions - hop1:
                        break
                    hop1 |= additions
                matched_table_names |= hop1

            relevant_tables: list[TableMetadata] = []
            for t in knowledge_map.tables or []:
                full_name = f"{t.schema_name}.{t.table_name}"
                if full_name in matched_table_names:
                    relevant_tables.append(t)

            relevant_entity_codes = set(matched_entity_codes)
            for rm in top_matches:
                if rm.match_type in {MatchType.METRIC, MatchType.CONCEPT} and rm.code:
                    for ent in knowledge_map.entities or []:
                        if ent.code and rm.code and (ent.code in rm.code or rm.code.endswith("_" + ent.code) or rm.code.startswith(ent.code + "_")):
                            relevant_entity_codes.add(ent.code)
            relevant_entities = [
                e for e in knowledge_map.entities or [] if e.code in relevant_entity_codes
            ]

            relevant_concepts: list[BusinessConcept] = []
            for c in knowledge_map.concepts or []:
                if c.code in matched_concept_codes:
                    relevant_concepts.append(c)
                elif c.entity_code and c.entity_code in relevant_entity_codes and c.code in {"ACTIVE_EMPLOYEE", "ACTIVE_CUSTOMER", "OPEN_ORDER", "COMPLETED_SALE", "PAID_INVOICE", "AVAILABLE_INVENTORY"}:
                    relevant_concepts.append(c)

            relevant_metrics: list[BusinessMetric] = [
                m for m in knowledge_map.metrics or [] if m.code in matched_metric_codes
            ]
            relevant_dimensions: list[Dimension] = [
                d for d in knowledge_map.dimensions or [] if d.code in matched_dim_codes
            ]

            if not relevant_metrics and q_lower:
                metric_keywords = {
                    "total": 1, "suma": 1, "sum": 1, "ingresos": 1, "revenue": 1,
                    "promedio": 1, "average": 1, "avg": 1, "media": 1,
                    "cantidad": 1, "count": 1, "numero": 1, "número": 1, "números": 1, "cuantos": 1, "cuántos": 1,
                    "maximo": 1, "máximo": 1, "max": 1,
                    "minimo": 1, "mínimo": 1, "min": 1,
                    "ventas": 1, "venta": 1, "sales": 1,
                    "pagos": 1, "pago": 1, "payments": 1,
                    "facturacion": 1, "facturación": 1, "billing": 1,
                }
                q_tokens = {t.lower() for t in query_tokens}
                if q_tokens & set(metric_keywords.keys()):
                    for m in knowledge_map.metrics or []:
                        m_tokens = set(_tokenize(m.name) + _tokenize(" ".join(m.aliases or [])))
                        if m_tokens & q_tokens and len(relevant_metrics) < 4:
                            relevant_metrics.append(m)

            relevant_relationships: list[RelationshipMetadata] = []
            relevant_table_set = {f"{t.schema_name}.{t.table_name}" for t in relevant_tables}
            for rel in knowledge_map.relationships or []:
                src = f"{rel.source_schema}.{rel.source_table}"
                tgt = f"{rel.target_schema}.{rel.target_table}"
                if src in relevant_table_set and tgt in relevant_table_set:
                    relevant_relationships.append(rel)

            join_paths: list[dict[str, Any]] = []
            if graph and self.graph_service is not None and hasattr(self.graph_service, "find_join_path"):
                table_list = list(relevant_table_set)
                if len(table_list) >= 2:
                    for i, src in enumerate(table_list[:4]):
                        for tgt in table_list[i + 1 : 5]:
                            try:
                                path: list[RelationshipEdge] = self.graph_service.find_join_path(
                                    graph,
                                    from_tables=[src],
                                    to_tables=[tgt],
                                    max_hops=4,
                                )
                                if path:
                                    join_paths.append(
                                        {
                                            "from": src,
                                            "to": tgt,
                                            "edges": [
                                                {
                                                    "source_table": e.source_table,
                                                    "source_column": e.source_column,
                                                    "target_table": e.target_table,
                                                    "target_column": e.target_column,
                                                    "type": e.edge_type.value
                                                    if isinstance(e.edge_type, RelationshipEdgeType)
                                                    else str(e.edge_type),
                                                    "confidence": e.confidence,
                                                }
                                                for e in path
                                            ],
                                            "hops": len(path),
                                        }
                                    )
                            except Exception as exc:
                                logger.debug(
                                    "Join path failed",
                                    extra={"from": src, "to": tgt, "error": str(exc)},
                                )
                                continue
            if not join_paths and relevant_relationships:
                for rel in relevant_relationships[:6]:
                    join_paths.append(
                        {
                            "from": f"{rel.source_schema}.{rel.source_table}",
                            "to": f"{rel.target_schema}.{rel.target_table}",
                            "edges": [
                                {
                                    "source_table": f"{rel.source_schema}.{rel.source_table}",
                                    "source_column": rel.source_columns[0] if rel.source_columns else "",
                                    "target_table": f"{rel.target_schema}.{rel.target_table}",
                                    "target_column": rel.target_columns[0] if rel.target_columns else "",
                                    "type": "FK",
                                    "confidence": rel.confidence,
                                }
                            ],
                            "hops": 1,
                            "direct": True,
                        }
                    )

            final_matches = top_matches[: max(k, 6)]
            sample_enabled = settings.LLM_INCLUDE_SAMPLE_DATA

            ctx = RetrievalContext(
                question=question,
                matches=final_matches,
                relevant_tables=relevant_tables,
                relevant_relationships=relevant_relationships,
                relevant_concepts=relevant_concepts,
                relevant_metrics=relevant_metrics,
                relevant_entities=relevant_entities,
                relevant_dimensions=relevant_dimensions,
                join_paths=join_paths,
                sample_rows_enabled=sample_enabled,
            )

            logger.info(
                "Schema retrieval completed",
                extra={
                    "matches": len(ctx.matches),
                    "tables": len(ctx.relevant_tables),
                    "relationships": len(ctx.relevant_relationships),
                    "concepts": len(ctx.relevant_concepts),
                    "metrics": len(ctx.relevant_metrics),
                    "entities": len(ctx.relevant_entities),
                    "dimensions": len(ctx.relevant_dimensions),
                    "join_paths": len(ctx.join_paths),
                },
            )
            return ctx

        except InvalidInputError:
            raise
        except Exception as exc:
            logger.error(
                "Schema retrieval failed",
                exc_info=exc,
                extra={"error_type": type(exc).__name__, "question_len": len(question or "")},
            )
            raise SchemaRetrievalError(
                detail=f"Failed to retrieve schema context: {exc!s}",
                extra={"question_preview": (question or "")[:120]},
            ) from exc

    def _build_doc(
        self,
        match_type: MatchType,
        obj: Any,
        key: str,
        name: str,
        description: str,
        aliases: list[str] | None,
        tables: list[str] | None,
        extra_terms: list[str] | None = None,
    ) -> tuple[MatchType, str, Any, list[str], str, str, list[str]]:
        parts: list[str] = [
            match_type.value.lower(),
            name or "",
            description or "",
        ]
        parts.extend(aliases or [])
        parts.extend(tables or [])
        if extra_terms:
            parts.extend(extra_terms)
        tokens = _tokenize(" ".join(p for p in parts if p))
        return (match_type, str(key or name), obj, tokens, name or key, description or "", list(tables or []))

    def _score_without_pgvector(
        self,
        query_vector: list[float],
        doc_corpus: list[tuple[MatchType, str, Any, list[str], str, str, list[str]]],
        *,
        top_n: int,
    ) -> list[_ScoredItem]:
        return []

    def _to_retrieval_match(self, scored: _ScoredItem) -> Optional[RetrievalMatch]:
        obj = scored.obj
        try:
            if isinstance(obj, BusinessMetric):
                return RetrievalMatch(
                    match_type=MatchType.METRIC,
                    code=obj.code,
                    name=obj.name,
                    description=obj.description or "",
                    aliases=list(obj.aliases or []),
                    tables=list(obj.source_tables or []),
                    confidence=min(1.0, max(0.0, scored.score)),
                )
            if isinstance(obj, BusinessConcept):
                return RetrievalMatch(
                    match_type=MatchType.CONCEPT,
                    code=obj.code,
                    name=obj.name,
                    description=obj.description or "",
                    aliases=list(obj.aliases or []),
                    tables=list(obj.tables or []),
                    confidence=min(1.0, max(0.0, scored.score)),
                )
            if isinstance(obj, BusinessEntity):
                return RetrievalMatch(
                    match_type=MatchType.ENTITY,
                    code=obj.code,
                    name=obj.name,
                    description=obj.description or "",
                    aliases=list(obj.aliases or []),
                    tables=list(obj.tables or []),
                    confidence=min(1.0, max(0.0, scored.score)),
                )
            from app.models.semantic import BusinessDomain as BD
            if isinstance(obj, BD):
                return RetrievalMatch(
                    match_type=MatchType.DOMAIN,
                    code=obj.code,
                    name=obj.name,
                    description=obj.description or "",
                    aliases=list(obj.aliases or []),
                    tables=list(obj.tables or []),
                    confidence=min(1.0, max(0.0, scored.score)),
                )
            if isinstance(obj, Dimension):
                return RetrievalMatch(
                    match_type=MatchType.DIMENSION,
                    code=obj.code,
                    name=obj.name,
                    description=obj.description or "",
                    aliases=list(obj.aliases or []),
                    tables=[obj.source_table] if obj.source_table else [],
                    confidence=min(1.0, max(0.0, scored.score)),
                )
            if isinstance(obj, TableMetadata):
                full_name = f"{obj.schema_name}.{obj.table_name}"
                return RetrievalMatch(
                    match_type=MatchType.TABLE,
                    code=full_name,
                    name=full_name,
                    description=obj.comment or f"Database table {full_name} with {len(obj.columns)} columns",
                    aliases=[obj.comment] if obj.comment else [],
                    tables=[full_name],
                    confidence=min(1.0, max(0.0, scored.score)),
                )
            from app.models.semantic import TerminologyEntry as TE
            if isinstance(obj, TE):
                return RetrievalMatch(
                    match_type=MatchType.TERMINOLOGY,
                    code=obj.target_code or obj.term,
                    name=obj.term,
                    description=obj.canonical_form or "",
                    aliases=list(obj.aliases or []),
                    tables=[],
                    confidence=min(1.0, max(0.0, scored.score)),
                )
            return RetrievalMatch(
                match_type=scored.match_type,
                code=scored.key,
                name=str(getattr(obj, "name", scored.key)),
                description=str(getattr(obj, "description", "")),
                aliases=list(getattr(obj, "aliases", []) or []),
                tables=list(getattr(obj, "tables", []) or []),
                confidence=min(1.0, max(0.0, scored.score)),
            )
        except Exception as exc:
            logger.debug(
                "Could not build RetrievalMatch from scored item",
                extra={"key": scored.key, "match_type": scored.match_type.value, "error": str(exc)},
            )
            return None
