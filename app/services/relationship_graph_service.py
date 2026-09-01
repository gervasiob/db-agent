from __future__ import annotations

import re
from collections import deque
from typing import Any, Optional

from app.core.exceptions import InvalidInputError, SemanticAnalysisError
from app.core.logging import get_logger
from app.models.database import DatabaseMetadata, RelationshipMetadata, TableMetadata
from app.models.semantic import RelationshipEdge, RelationshipEdgeType, RelationshipGraph

logger = get_logger(__name__)


def _full_table(schema: str, table: str) -> str:
    s = (schema or "").strip()
    t = (table or "").strip()
    if s:
        return f"{s}.{t}"
    return t


def _split_full(full: str) -> tuple[str, str]:
    if not full:
        return "", ""
    parts = full.split(".", 1)
    if len(parts) == 2:
        return parts[0], parts[1]
    return "", parts[0]


class RelationshipGraphService:
    def __init__(
        self,
        semantic_repository: Any = None,
        enable_inferred_edges: bool = True,
        inferred_confidence: float = 0.6,
    ) -> None:
        self.semantic_repository = semantic_repository
        self.enable_inferred_edges = enable_inferred_edges
        self.inferred_confidence = max(0.0, min(1.0, inferred_confidence))
        logger.info(
            "RelationshipGraphService initialized",
            extra={
                "enable_inferred_edges": enable_inferred_edges,
                "inferred_confidence": self.inferred_confidence,
                "has_repository": semantic_repository is not None,
            },
        )

    async def build_graph(
        self,
        metadata: DatabaseMetadata,
        relationships: list[RelationshipMetadata] | None = None,
    ) -> RelationshipGraph:
        if not metadata:
            raise InvalidInputError(
                detail="metadata is required to build relationship graph",
                field="metadata",
            )

        logger.info(
            "Building relationship graph",
            extra={
                "schema_count": len(metadata.schemas),
                "input_relationships": len(relationships or []),
                "metadata_relationships": len(metadata.relationships or []),
                "infer_enabled": self.enable_inferred_edges,
            },
        )

        all_tables: list[TableMetadata] = []
        for schema in metadata.schemas:
            all_tables.extend(schema.tables or [])
            all_tables.extend(schema.views or [])

        nodes: list[str] = [
            _full_table(t.schema_name, t.table_name) for t in all_tables
        ]
        nodes = list(dict.fromkeys(nodes))

        edges: list[RelationshipEdge] = []
        seen_edges: set[tuple[str, str, str, str]] = set()

        try:
            rels = list(relationships or []) + list(metadata.relationships or [])
            for idx, rel in enumerate(rels):
                src_full = _full_table(rel.source_schema, rel.source_table)
                tgt_full = _full_table(rel.target_schema, rel.target_table)
                if src_full not in nodes or tgt_full not in nodes:
                    continue
                src_cols = rel.source_columns or [""]
                tgt_cols = rel.target_columns or [""]
                length = min(len(src_cols), len(tgt_cols))
                if length == 0:
                    length = 1
                for i in range(length):
                    sc = src_cols[i] if i < len(src_cols) else src_cols[0] if src_cols else ""
                    tc = tgt_cols[i] if i < len(tgt_cols) else tgt_cols[0] if tgt_cols else ""
                    key = (src_full, sc, tgt_full, tc)
                    if key in seen_edges:
                        continue
                    seen_edges.add(key)
                    edge = RelationshipEdge(
                        id=f"fk-{idx}-{i}",
                        source_table=src_full,
                        source_column=sc,
                        target_table=tgt_full,
                        target_column=tc,
                        edge_type=RelationshipEdgeType.INFERRED if rel.inferred else RelationshipEdgeType.FK,
                        confidence=max(0.0, min(1.0, rel.confidence)) if rel.confidence else (0.6 if rel.inferred else 1.0),
                    )
                    edges.append(edge)
                    rev_key = (tgt_full, tc, src_full, sc)
                    if rev_key not in seen_edges:
                        seen_edges.add(rev_key)
                        edges.append(
                            RelationshipEdge(
                                id=f"fk-{idx}-{i}-rev",
                                source_table=tgt_full,
                                source_column=tc,
                                target_table=src_full,
                                target_column=sc,
                                edge_type=RelationshipEdgeType.INFERRED if rel.inferred else RelationshipEdgeType.FK,
                                confidence=edge.confidence,
                            )
                        )

            if self.enable_inferred_edges:
                inferred_edges = self._infer_edges_by_column_pattern(all_tables, seen_edges)
                edges.extend(inferred_edges)
                logger.debug(
                    "Inferred heuristic edges",
                    extra={"inferred_edge_count": len(inferred_edges)},
                )

            graph = RelationshipGraph(nodes=nodes, edges=edges)
            logger.info(
                "Relationship graph built",
                extra={
                    "node_count": len(graph.nodes),
                    "edge_count": len(graph.edges),
                    "fk_edge_count": sum(1 for e in graph.edges if e.edge_type == RelationshipEdgeType.FK),
                    "inferred_edge_count": sum(1 for e in graph.edges if e.edge_type == RelationshipEdgeType.INFERRED),
                },
            )
            return graph

        except InvalidInputError:
            raise
        except Exception as exc:
            logger.error(
                "Failed to build relationship graph",
                exc_info=exc,
                extra={"error_type": type(exc).__name__},
            )
            raise SemanticAnalysisError(
                detail=f"Failed to build relationship graph: {exc!s}",
                extra={"stage": "relationship_graph_build"},
            ) from exc

    def _infer_edges_by_column_pattern(
        self,
        tables: list[TableMetadata],
        existing: set[tuple[str, str, str, str]],
    ) -> list[RelationshipEdge]:
        table_map: dict[str, TableMetadata] = {}
        for t in tables:
            full = _full_table(t.schema_name, t.table_name)
            table_map[full] = t

        table_name_to_full: dict[str, str] = {}
        for full, t in table_map.items():
            name = t.table_name.lower()
            table_name_to_full.setdefault(name, full)
            plural = name + "s"
            table_name_to_full.setdefault(plural, full)
            if plural.endswith("ies"):
                singular = plural[:-3] + "y"
                table_name_to_full.setdefault(singular, full)
            elif plural.endswith("es") and len(plural) > 3:
                singular = plural[:-2]
                table_name_to_full.setdefault(singular, full)
            elif plural.endswith("s") and len(plural) > 2:
                singular = plural[:-1]
                table_name_to_full.setdefault(singular, full)

        edges: list[RelationshipEdge] = []
        for full, tbl in table_map.items():
            for col in tbl.columns:
                cname = col.name.lower()
                if not cname.endswith("_id"):
                    continue
                candidate = cname[:-3]
                if not candidate:
                    continue
                target_full: Optional[str] = None
                if candidate in table_name_to_full:
                    target_full = table_name_to_full[candidate]
                if target_full is None:
                    for tn, tf in table_name_to_full.items():
                        if candidate == tn or tn.startswith(candidate + "_"):
                            target_full = tf
                            break
                if target_full is None or target_full == full:
                    continue

                target_table = table_map.get(target_full)
                if target_table is None:
                    continue
                target_pk = None
                for tc in target_table.primary_key:
                    if tc.lower() in {"id", candidate + "_id"}:
                        target_pk = tc
                        break
                if target_pk is None:
                    for tc in target_table.columns:
                        if tc.is_pk and tc.name.lower() in {"id", candidate + "_id"}:
                            target_pk = tc.name
                            break
                if target_pk is None:
                    target_pk = "id"

                fwd_key = (full, col.name, target_full, target_pk)
                if fwd_key in existing:
                    continue
                existing.add(fwd_key)
                existing.add((target_full, target_pk, full, col.name))

                edge = RelationshipEdge(
                    id=f"inf-{len(edges)}-fwd",
                    source_table=full,
                    source_column=col.name,
                    target_table=target_full,
                    target_column=target_pk,
                    edge_type=RelationshipEdgeType.INFERRED,
                    confidence=self.inferred_confidence,
                )
                edges.append(edge)
                edges.append(
                    RelationshipEdge(
                        id=f"inf-{len(edges) - 1}-rev",
                        source_table=target_full,
                        source_column=target_pk,
                        target_table=full,
                        target_column=col.name,
                        edge_type=RelationshipEdgeType.INFERRED,
                        confidence=self.inferred_confidence,
                    )
                )
        return edges

    def find_join_path(
        self,
        graph: RelationshipGraph,
        from_tables: list[str],
        to_tables: list[str],
        *,
        max_hops: int = 5,
        min_confidence: float = 0.3,
    ) -> list[RelationshipEdge]:
        if not graph:
            return []
        if not from_tables or not to_tables:
            return []

        from_set = {t for t in from_tables if t in graph.nodes}
        to_set = {t for t in to_tables if t in graph.nodes}
        if not from_set or not to_set:
            return []

        adjacency: dict[str, list[tuple[str, RelationshipEdge]]] = {}
        for edge in graph.edges:
            if edge.confidence < min_confidence:
                continue
            adjacency.setdefault(edge.source_table, []).append(
                (edge.target_table, edge)
            )

        best_path_edges: list[RelationshipEdge] | None = None
        best_hops = max_hops + 1

        for start in from_set:
            if start in to_set:
                return []

            visited: dict[str, tuple[Optional[str], Optional[RelationshipEdge]]] = {
                start: (None, None)
            }
            queue: deque[tuple[str, int]] = deque([(start, 0)])
            found_target: Optional[str] = None
            while queue:
                current, hops = queue.popleft()
                if current in to_set:
                    found_target = current
                    best_hops = hops
                    break
                if hops >= max_hops or hops >= best_hops:
                    continue
                for neighbor, edge in adjacency.get(current, []):
                    if neighbor in visited:
                        continue
                    visited[neighbor] = (current, edge)
                    queue.append((neighbor, hops + 1))

            if found_target is None:
                continue

            path_edges: list[RelationshipEdge] = []
            cursor = found_target
            while cursor != start:
                prev, edge = visited.get(cursor, (None, None))
                if prev is None or edge is None:
                    break
                path_edges.append(edge)
                cursor = prev
            path_edges.reverse()

            if best_path_edges is None or len(path_edges) < len(best_path_edges):
                best_path_edges = path_edges

        return list(best_path_edges or [])

    def expand_related_tables(
        self,
        graph: RelationshipGraph,
        seed_tables: list[str],
        max_hops: int = 2,
        *,
        min_confidence: float = 0.4,
    ) -> list[str]:
        if not graph:
            return list(seed_tables or [])
        if not seed_tables:
            return []

        seed_set = {t for t in seed_tables if t in graph.nodes}
        if not seed_set:
            return list(seed_tables or [])

        adjacency: dict[str, list[tuple[str, RelationshipEdge]]] = {}
        for edge in graph.edges:
            if edge.confidence < min_confidence:
                continue
            adjacency.setdefault(edge.source_table, []).append(
                (edge.target_table, edge)
            )

        reached: dict[str, int] = {t: 0 for t in seed_set}
        queue: deque[tuple[str, int]] = deque([(t, 0) for t in seed_set])

        while queue:
            current, hops = queue.popleft()
            if hops >= max_hops:
                continue
            for neighbor, _edge in adjacency.get(current, []):
                if neighbor in reached:
                    continue
                reached[neighbor] = hops + 1
                queue.append((neighbor, hops + 1))

        ordered = sorted(reached.items(), key=lambda item: (item[1], item[0]))
        return [node for node, _h in ordered]

    async def persist_graph(
        self,
        graph: RelationshipGraph,
        **kwargs: Any,
    ) -> bool:
        if not self.semantic_repository:
            logger.warning("persist_graph skipped: no semantic_repository configured")
            return False
        try:
            repo = self.semantic_repository
            if hasattr(repo, "persist_relationships"):
                from app.models.database import RelationshipMetadata as RM, RelationshipType as RT
                rels: list[RM] = []
                for e in graph.edges:
                    if e.edge_type == RelationshipEdgeType.JOIN_PATH:
                        continue
                    s_schema, s_table = _split_full(e.source_table)
                    t_schema, t_table = _split_full(e.target_table)
                    inferred = e.edge_type == RelationshipEdgeType.INFERRED
                    rels.append(
                        RM(
                            source_schema=s_schema,
                            source_table=s_table,
                            source_columns=[e.source_column] if e.source_column else [],
                            target_schema=t_schema,
                            target_table=t_table,
                            target_columns=[e.target_column] if e.target_column else [],
                            relationship_type=RT.MANY_TO_ONE,
                            inferred=inferred,
                            confidence=e.confidence,
                            evidence=f"Graph edge: {e.source_table}.{e.source_column} -> {e.target_table}.{e.target_column}",
                        )
                    )
                await repo.persist_relationships(relationships=rels, **kwargs)
            else:
                logger.debug(
                    "semantic_repository has no persist_relationships method; stored in memory only"
                )
            logger.info(
                "Persisted graph relationships",
                extra={"edge_count": len(graph.edges)},
            )
            return True
        except Exception as exc:
            logger.error(
                "Failed to persist graph",
                exc_info=exc,
                extra={"error_type": type(exc).__name__},
            )
            raise SemanticAnalysisError(
                detail=f"Failed to persist relationship graph: {exc!s}",
                extra={"stage": "relationship_graph_persistence"},
            ) from exc
