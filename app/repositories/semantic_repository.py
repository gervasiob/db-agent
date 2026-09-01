from __future__ import annotations

from typing import Any, Optional
from uuid import UUID

from sqlalchemy import and_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.database import RelationshipMetadata, RelationshipType
from app.models.orm import (
    DatabaseBusinessConcept as ORMConcept,
    DatabaseBusinessDomain as ORMDomain,
    DatabaseBusinessEntity as ORMEntity,
    DatabaseColumnMetadata as ORMColumn,
    DatabaseDimension as ORMDimension,
    DatabaseMetric as ORMMetric,
    DatabaseRelationship as ORMRelationship,
    DatabaseTableMetadata as ORMTable,
    DatabaseTerminology as ORMTerminology,
)
from app.models.semantic import (
    AggregationType,
    BusinessConcept,
    BusinessDomain,
    BusinessEntity,
    BusinessMetric,
    ColumnSemantic,
    DateSemantic,
    DateSemanticRole,
    Dimension,
    DimensionType,
    PIILevel,
    TerminologyEntry,
    TerminologyEntryType,
)
from app.repositories.context_repository import (
    _aliases_json_to_list,
    _list_to_aliases_json,
    _list_to_tables_json,
    _split_schema_table,
    _tables_json_to_list,
)

logger = get_logger(__name__)


class SemanticRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    @staticmethod
    def _orm_domain_to_pydantic(orm_dom: ORMDomain) -> BusinessDomain:
        return BusinessDomain(
            id=orm_dom.id,
            name=orm_dom.name,
            code=orm_dom.code,
            description=orm_dom.description or "",
            aliases=_aliases_json_to_list(orm_dom.aliases),
            tables=[],
            confidence=1.0,
            evidence=None,
        )

    @staticmethod
    def _orm_entity_to_pydantic(orm_ent: ORMEntity) -> BusinessEntity:
        return BusinessEntity(
            id=orm_ent.id,
            name=orm_ent.name,
            code=orm_ent.code,
            description=orm_ent.description or "",
            aliases=_aliases_json_to_list(orm_ent.aliases),
            domain_code=orm_ent.domain_code,
            tables=_tables_json_to_list(orm_ent.tables),
            primary_table=None,
            key_columns=[],
            status_column=None,
            confidence=1.0,
            evidence=None,
        )

    @staticmethod
    def _orm_concept_to_pydantic(orm_con: ORMConcept) -> BusinessConcept:
        return BusinessConcept(
            id=orm_con.id,
            name=orm_con.name,
            code=orm_con.code,
            description=orm_con.description or "",
            aliases=_aliases_json_to_list(orm_con.aliases),
            entity_code=orm_con.entity_code,
            domain_code=None,
            tables=_tables_json_to_list(orm_con.tables),
            source_columns=[],
            condition_semantic=orm_con.condition_semantic,
            condition_sql=orm_con.condition_sql,
            value_expression=None,
            is_filter=False,
            is_computed=False,
            confidence=1.0,
            evidence=None,
        )

    @staticmethod
    def _orm_metric_to_pydantic(orm_met: ORMMetric) -> BusinessMetric:
        return BusinessMetric(
            id=orm_met.id,
            name=orm_met.name,
            code=orm_met.code,
            description=orm_met.description or "",
            aliases=_aliases_json_to_list(orm_met.aliases),
            domain_code=None,
            entity_code=None,
            source_tables=_tables_json_to_list(orm_met.source_tables),
            source_column=orm_met.source_column,
            aggregation=AggregationType(orm_met.aggregation)
            if orm_met.aggregation and orm_met.aggregation in {a.value for a in AggregationType}
            else AggregationType.NONE,
            filter_condition=orm_met.filter_condition,
            date_column=orm_met.date_column,
            default_dimensions=[],
            confidence=1.0,
            evidence=None,
        )

    @staticmethod
    def _orm_dimension_to_pydantic(orm_dim: ORMDimension) -> Dimension:
        return Dimension(
            id=orm_dim.id,
            name=orm_dim.name,
            code=orm_dim.code,
            description=orm_dim.description or "",
            aliases=[],
            dimension_type=DimensionType(orm_dim.dimension_type)
            if orm_dim.dimension_type and orm_dim.dimension_type in {d.value for d in DimensionType}
            else DimensionType.CATEGORICAL,
            source_table=orm_dim.source_table,
            source_column=orm_dim.source_column,
            values=None,
            requires_joins=(
                list(orm_dim.requires_joins)
                if isinstance(orm_dim.requires_joins, list)
                else []
            ),
            confidence=1.0,
        )

    @staticmethod
    def _orm_terminology_to_pydantic(orm_term: ORMTerminology) -> TerminologyEntry:
        return TerminologyEntry(
            id=orm_term.id,
            term=orm_term.term,
            canonical_form=orm_term.canonical_form,
            aliases=_aliases_json_to_list(orm_term.aliases),
            type=TerminologyEntryType(orm_term.type)
            if orm_term.type and orm_term.type in {t.value for t in TerminologyEntryType}
            else TerminologyEntryType.OTHER,
            target_code=orm_term.target_code,
            confidence=1.0,
        )

    @staticmethod
    def _orm_relationship_to_pydantic(orm_rel: ORMRelationship) -> RelationshipMetadata:
        source_schema, source_table = _split_schema_table(orm_rel.source_table)
        target_schema, target_table = _split_schema_table(orm_rel.target_table)
        return RelationshipMetadata(
            source_schema=source_schema,
            source_table=source_table,
            source_columns=[orm_rel.source_column] if orm_rel.source_column else [],
            target_schema=target_schema,
            target_table=target_table,
            target_columns=[orm_rel.target_column] if orm_rel.target_column else [],
            relationship_type=RelationshipType(orm_rel.type)
            if orm_rel.type in {r.value for r in RelationshipType}
            else RelationshipType.MANY_TO_ONE,
            inferred=orm_rel.inferred,
            confidence=orm_rel.confidence or 1.0,
            evidence=None,
        )

    async def save_domain(
        self,
        domain: BusinessDomain,
        context_version_id: UUID,
    ) -> BusinessDomain:
        orm_dom = ORMDomain(
            context_version_id=context_version_id,
            name=domain.name,
            code=domain.code,
            description=domain.description,
            aliases=_list_to_aliases_json(domain.aliases),
            embedding=None,
        )
        self.session.add(orm_dom)
        await self.session.flush()
        logger.debug(
            "Saved business domain",
            extra={
                "context_version_id": str(context_version_id),
                "domain_code": domain.code,
            },
        )
        return self._orm_domain_to_pydantic(orm_dom)

    async def save_bulk_domains(
        self,
        domains: list[BusinessDomain],
        context_version_id: UUID,
    ) -> list[BusinessDomain]:
        saved: list[BusinessDomain] = []
        for domain in domains:
            saved.append(await self.save_domain(domain, context_version_id))
        logger.info(
            "Saved bulk business domains",
            extra={
                "context_version_id": str(context_version_id),
                "count": len(domains),
            },
        )
        return saved

    async def save_entity(
        self,
        entity: BusinessEntity,
        context_version_id: UUID,
    ) -> BusinessEntity:
        orm_ent = ORMEntity(
            context_version_id=context_version_id,
            name=entity.name,
            code=entity.code,
            description=entity.description,
            domain_code=entity.domain_code,
            aliases=_list_to_aliases_json(entity.aliases),
            tables=_list_to_tables_json(entity.tables),
            embedding=None,
        )
        self.session.add(orm_ent)
        await self.session.flush()
        logger.debug(
            "Saved business entity",
            extra={
                "context_version_id": str(context_version_id),
                "entity_code": entity.code,
            },
        )
        return self._orm_entity_to_pydantic(orm_ent)

    async def save_bulk_entities(
        self,
        entities: list[BusinessEntity],
        context_version_id: UUID,
    ) -> list[BusinessEntity]:
        saved: list[BusinessEntity] = []
        for entity in entities:
            saved.append(await self.save_entity(entity, context_version_id))
        logger.info(
            "Saved bulk business entities",
            extra={
                "context_version_id": str(context_version_id),
                "count": len(entities),
            },
        )
        return saved

    async def save_concept(
        self,
        concept: BusinessConcept,
        context_version_id: UUID,
    ) -> BusinessConcept:
        orm_con = ORMConcept(
            context_version_id=context_version_id,
            name=concept.name,
            code=concept.code,
            description=concept.description,
            aliases=_list_to_aliases_json(concept.aliases),
            entity_code=concept.entity_code,
            tables=_list_to_tables_json(concept.tables),
            condition_semantic=concept.condition_semantic,
            condition_sql=concept.condition_sql,
            embedding=None,
        )
        self.session.add(orm_con)
        await self.session.flush()
        logger.debug(
            "Saved business concept",
            extra={
                "context_version_id": str(context_version_id),
                "concept_code": concept.code,
            },
        )
        return self._orm_concept_to_pydantic(orm_con)

    async def save_bulk_concepts(
        self,
        concepts: list[BusinessConcept],
        context_version_id: UUID,
    ) -> list[BusinessConcept]:
        saved: list[BusinessConcept] = []
        for concept in concepts:
            saved.append(await self.save_concept(concept, context_version_id))
        logger.info(
            "Saved bulk business concepts",
            extra={
                "context_version_id": str(context_version_id),
                "count": len(concepts),
            },
        )
        return saved

    async def save_metric(
        self,
        metric: BusinessMetric,
        context_version_id: UUID,
    ) -> BusinessMetric:
        orm_met = ORMMetric(
            context_version_id=context_version_id,
            name=metric.name,
            code=metric.code,
            description=metric.description,
            aliases=_list_to_aliases_json(metric.aliases),
            source_tables=_list_to_tables_json(metric.source_tables),
            source_column=metric.source_column,
            aggregation=metric.aggregation.value
            if isinstance(metric.aggregation, AggregationType)
            else str(metric.aggregation),
            filter_condition=metric.filter_condition,
            date_column=metric.date_column,
            embedding=None,
        )
        self.session.add(orm_met)
        await self.session.flush()
        logger.debug(
            "Saved business metric",
            extra={
                "context_version_id": str(context_version_id),
                "metric_code": metric.code,
            },
        )
        return self._orm_metric_to_pydantic(orm_met)

    async def save_bulk_metrics(
        self,
        metrics: list[BusinessMetric],
        context_version_id: UUID,
    ) -> list[BusinessMetric]:
        saved: list[BusinessMetric] = []
        for metric in metrics:
            saved.append(await self.save_metric(metric, context_version_id))
        logger.info(
            "Saved bulk business metrics",
            extra={
                "context_version_id": str(context_version_id),
                "count": len(metrics),
            },
        )
        return saved

    async def save_dimension(
        self,
        dimension: Dimension,
        context_version_id: UUID,
    ) -> Dimension:
        orm_dim = ORMDimension(
            context_version_id=context_version_id,
            name=dimension.name,
            code=dimension.code,
            description=dimension.description,
            dimension_type=dimension.dimension_type.value
            if isinstance(dimension.dimension_type, DimensionType)
            else str(dimension.dimension_type),
            source_table=dimension.source_table,
            source_column=dimension.source_column,
            requires_joins=list(dimension.requires_joins) if dimension.requires_joins else None,
            embedding=None,
        )
        self.session.add(orm_dim)
        await self.session.flush()
        logger.debug(
            "Saved dimension",
            extra={
                "context_version_id": str(context_version_id),
                "dimension_code": dimension.code,
            },
        )
        return self._orm_dimension_to_pydantic(orm_dim)

    async def save_bulk_dimensions(
        self,
        dimensions: list[Dimension],
        context_version_id: UUID,
    ) -> list[Dimension]:
        saved: list[Dimension] = []
        for dimension in dimensions:
            saved.append(await self.save_dimension(dimension, context_version_id))
        logger.info(
            "Saved bulk dimensions",
            extra={
                "context_version_id": str(context_version_id),
                "count": len(dimensions),
            },
        )
        return saved

    async def save_terminology_entry(
        self,
        entry: TerminologyEntry,
        context_version_id: UUID,
    ) -> TerminologyEntry:
        orm_term = ORMTerminology(
            context_version_id=context_version_id,
            term=entry.term,
            canonical_form=entry.canonical_form,
            aliases=_list_to_aliases_json(entry.aliases),
            type=entry.type.value if isinstance(entry.type, TerminologyEntryType) else str(entry.type),
            target_code=entry.target_code,
            embedding=None,
        )
        self.session.add(orm_term)
        await self.session.flush()
        logger.debug(
            "Saved terminology entry",
            extra={
                "context_version_id": str(context_version_id),
                "term": entry.term,
            },
        )
        return self._orm_terminology_to_pydantic(orm_term)

    async def save_bulk_terminology(
        self,
        entries: list[TerminologyEntry],
        context_version_id: UUID,
    ) -> list[TerminologyEntry]:
        saved: list[TerminologyEntry] = []
        for entry in entries:
            saved.append(await self.save_terminology_entry(entry, context_version_id))
        logger.info(
            "Saved bulk terminology",
            extra={
                "context_version_id": str(context_version_id),
                "count": len(entries),
            },
        )
        return saved

    async def save_relationship(
        self,
        edge: RelationshipMetadata,
        context_version_id: UUID,
    ) -> RelationshipMetadata:
        source_full = (
            f"{edge.source_schema}.{edge.source_table}"
            if edge.source_schema
            else edge.source_table
        )
        target_full = (
            f"{edge.target_schema}.{edge.target_table}"
            if edge.target_schema
            else edge.target_table
        )
        source_col = edge.source_columns[0] if edge.source_columns else ""
        target_col = edge.target_columns[0] if edge.target_columns else ""
        orm_rel = ORMRelationship(
            context_version_id=context_version_id,
            source_table=source_full,
            source_column=source_col,
            target_table=target_full,
            target_column=target_col,
            type=edge.relationship_type.value
            if isinstance(edge.relationship_type, RelationshipType)
            else str(edge.relationship_type),
            inferred=edge.inferred,
            confidence=edge.confidence,
        )
        self.session.add(orm_rel)
        await self.session.flush()
        logger.debug(
            "Saved relationship",
            extra={
                "context_version_id": str(context_version_id),
                "source": source_full,
                "target": target_full,
            },
        )
        return self._orm_relationship_to_pydantic(orm_rel)

    async def save_bulk_relationships(
        self,
        edges: list[RelationshipMetadata],
        context_version_id: UUID,
    ) -> list[RelationshipMetadata]:
        saved: list[RelationshipMetadata] = []
        for edge in edges:
            saved.append(await self.save_relationship(edge, context_version_id))
        logger.info(
            "Saved bulk relationships",
            extra={
                "context_version_id": str(context_version_id),
                "count": len(edges),
            },
        )
        return saved

    async def save_date_semantics(
        self,
        semantics: list[DateSemantic],
        context_version_id: UUID,
    ) -> None:
        if not semantics:
            return
        table_stmt = (
            select(ORMTable)
            .where(ORMTable.context_version_id == context_version_id)
        )
        table_result = await self.session.execute(table_stmt)
        orm_tables = table_result.scalars().all()
        table_by_full: dict[str, ORMTable] = {}
        for t in orm_tables:
            full = f"{t.schema_name}.{t.table_name}"
            table_by_full[full] = t
            table_by_full[t.table_name] = t

        col_map: dict[tuple[UUID, str], ORMColumn] = {}
        table_ids = [t.id for t in orm_tables]
        if table_ids:
            col_stmt = select(ORMColumn).where(ORMColumn.table_id.in_(table_ids))
            col_result = await self.session.execute(col_stmt)
            for c in col_result.scalars().all():
                col_map[(c.table_id, c.column_name)] = c

        updated_count = 0
        for ds in semantics:
            table = table_by_full.get(ds.table_name)
            if table is None:
                schema, tbl = _split_schema_table(ds.table_name)
                alt = f"{schema}.{tbl}"
                table = table_by_full.get(alt) or table_by_full.get(tbl)
            if table is None:
                continue
            col = col_map.get((table.id, ds.column_name))
            if col is None:
                continue
            role_value = (
                ds.semantic_role.value
                if isinstance(ds.semantic_role, DateSemanticRole)
                else str(ds.semantic_role)
            )
            col.semantic_role = role_value
            updated_count += 1

        await self.session.flush()
        logger.info(
            "Saved date semantics into columns_metadata.semantic_role",
            extra={
                "context_version_id": str(context_version_id),
                "updated": updated_count,
                "total": len(semantics),
            },
        )

    async def save_column_semantics(
        self,
        semantics: list[ColumnSemantic],
        context_version_id: UUID,
    ) -> None:
        if not semantics:
            return
        table_stmt = (
            select(ORMTable)
            .where(ORMTable.context_version_id == context_version_id)
        )
        table_result = await self.session.execute(table_stmt)
        orm_tables = table_result.scalars().all()
        table_by_key: dict[tuple[str, str], ORMTable] = {}
        for t in orm_tables:
            table_by_key[(t.schema_name, t.table_name)] = t

        updated_count = 0
        for cs in semantics:
            orm_table = table_by_key.get((cs.schema_name, cs.table_name))
            if orm_table is None:
                continue
            col_stmt = (
                select(ORMColumn)
                .where(
                    and_(
                        ORMColumn.table_id == orm_table.id,
                        ORMColumn.column_name == cs.column_name,
                    )
                )
            )
            col_result = await self.session.execute(col_stmt)
            orm_col = col_result.scalar_one_or_none()
            if orm_col is None:
                continue
            pii_value = (
                cs.pii_level.value
                if isinstance(cs.pii_level, PIILevel)
                else str(cs.pii_level)
            )
            orm_col.semantic_role = pii_value
            if cs.description and not orm_col.comment:
                orm_col.comment = cs.description
            updated_count += 1

        await self.session.flush()
        logger.info(
            "Saved column semantics",
            extra={
                "context_version_id": str(context_version_id),
                "updated": updated_count,
                "total": len(semantics),
            },
        )

    async def get_domains(self, context_version_id: UUID) -> list[BusinessDomain]:
        stmt = (
            select(ORMDomain)
            .where(ORMDomain.context_version_id == context_version_id)
            .order_by(ORMDomain.name)
        )
        result = await self.session.execute(stmt)
        orm_objects = result.scalars().all()
        return [self._orm_domain_to_pydantic(o) for o in orm_objects]

    async def get_entities(self, context_version_id: UUID) -> list[BusinessEntity]:
        stmt = (
            select(ORMEntity)
            .where(ORMEntity.context_version_id == context_version_id)
            .order_by(ORMEntity.name)
        )
        result = await self.session.execute(stmt)
        orm_objects = result.scalars().all()
        return [self._orm_entity_to_pydantic(o) for o in orm_objects]

    async def get_concepts(self, context_version_id: UUID) -> list[BusinessConcept]:
        stmt = (
            select(ORMConcept)
            .where(ORMConcept.context_version_id == context_version_id)
            .order_by(ORMConcept.name)
        )
        result = await self.session.execute(stmt)
        orm_objects = result.scalars().all()
        return [self._orm_concept_to_pydantic(o) for o in orm_objects]

    async def get_metrics(self, context_version_id: UUID) -> list[BusinessMetric]:
        stmt = (
            select(ORMMetric)
            .where(ORMMetric.context_version_id == context_version_id)
            .order_by(ORMMetric.name)
        )
        result = await self.session.execute(stmt)
        orm_objects = result.scalars().all()
        return [self._orm_metric_to_pydantic(o) for o in orm_objects]

    async def get_dimensions(self, context_version_id: UUID) -> list[Dimension]:
        stmt = (
            select(ORMDimension)
            .where(ORMDimension.context_version_id == context_version_id)
            .order_by(ORMDimension.name)
        )
        result = await self.session.execute(stmt)
        orm_objects = result.scalars().all()
        return [self._orm_dimension_to_pydantic(o) for o in orm_objects]

    async def get_terminology(self, context_version_id: UUID) -> list[TerminologyEntry]:
        stmt = (
            select(ORMTerminology)
            .where(ORMTerminology.context_version_id == context_version_id)
            .order_by(ORMTerminology.term)
        )
        result = await self.session.execute(stmt)
        orm_objects = result.scalars().all()
        return [self._orm_terminology_to_pydantic(o) for o in orm_objects]

    async def get_relationships(self, context_version_id: UUID) -> list[RelationshipMetadata]:
        stmt = (
            select(ORMRelationship)
            .where(ORMRelationship.context_version_id == context_version_id)
        )
        result = await self.session.execute(stmt)
        orm_objects = result.scalars().all()
        return [self._orm_relationship_to_pydantic(o) for o in orm_objects]

    async def get_all_semantic(
        self,
        context_version_id: UUID,
    ) -> dict[str, Any]:
        domains = await self.get_domains(context_version_id)
        entities = await self.get_entities(context_version_id)
        concepts = await self.get_concepts(context_version_id)
        metrics = await self.get_metrics(context_version_id)
        dimensions = await self.get_dimensions(context_version_id)
        terminology = await self.get_terminology(context_version_id)
        relationships = await self.get_relationships(context_version_id)
        return {
            "domains": domains,
            "entities": entities,
            "concepts": concepts,
            "metrics": metrics,
            "dimensions": dimensions,
            "terminology": terminology,
            "relationships": relationships,
        }
