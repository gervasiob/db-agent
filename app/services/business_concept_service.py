from __future__ import annotations

import re
from typing import Any, Optional

from app.core.exceptions import InvalidInputError, SemanticAnalysisError
from app.core.logging import get_logger
from app.models.database import ColumnProfile, DatabaseMetadata, TableMetadata
from app.models.semantic import BusinessConcept, BusinessDomain, BusinessEntity, BusinessMetric

logger = get_logger(__name__)


STATUS_VALUES_ACTIVE = {
    "active", "activo", "vigente", "current", "actual",
    "enabled", "habilitado", "alive", "1", "true", "yes", "si", "s", "y",
    "open", "abierto", "pending", "pendiente", "in_progress", "en_progreso",
    "paid", "pagado", "completed", "completado", "done", "hecho",
}

STATUS_VALUES_INACTIVE = {
    "inactive", "inactivo", "terminated", "terminado",
    "closed", "cerrado", "cancelled", "cancelado", "canceled",
    "deleted", "eliminado", "suspended", "suspendido",
    "refunded", "reembolsado", "returned", "devuelto",
    "0", "false", "no", "n",
}

CONCEPT_PRESETS: list[dict[str, Any]] = [
    {
        "code": "ACTIVE_EMPLOYEE",
        "name": "Active Employee",
        "aliases_en": ["Active Employee", "Current Employee", "Active Staff", "Current Staff"],
        "aliases_es": ["Empleado Activo", "Empleado Vigente", "Empleado Actual", "Plantilla Activa", "Personal Activo"],
        "entity_keywords": ["employee", "empleado", "trabajador", "colaborador", "staff"],
        "condition_semantic": "Employee status is active and not terminated",
        "is_filter": True,
        "is_computed": False,
    },
    {
        "code": "TERMINATED_EMPLOYEE",
        "name": "Terminated Employee",
        "aliases_en": ["Terminated Employee", "Former Employee", "Ex-Employee", "Inactive Employee"],
        "aliases_es": ["Empleado Desvinculado", "Empleado Cesado", "Empleado Inactivo", "Ex-Empleado", "Baja de Empleado"],
        "entity_keywords": ["employee", "empleado", "trabajador"],
        "condition_semantic": "Employee status is terminated/inactive",
        "is_filter": True,
        "is_computed": False,
    },
    {
        "code": "ACTIVE_CUSTOMER",
        "name": "Active Customer",
        "aliases_en": ["Active Customer", "Current Customer", "Active Client"],
        "aliases_es": ["Cliente Activo", "Cliente Vigente", "Cliente Actual"],
        "entity_keywords": ["customer", "cliente", "client", "consumer", "consumidor"],
        "condition_semantic": "Customer is active, enabled, not deleted",
        "is_filter": True,
        "is_computed": False,
    },
    {
        "code": "NEW_CUSTOMER",
        "name": "New Customer",
        "aliases_en": ["New Customer", "New Signup", "Recently Created Customer"],
        "aliases_es": ["Nuevo Cliente", "Cliente Nuevo", "Altas de Clientes", "Nuevos Registros"],
        "entity_keywords": ["customer", "cliente", "client", "user", "usuario"],
        "condition_semantic": "Customer created recently (typically within period)",
        "is_filter": True,
        "is_computed": True,
    },
    {
        "code": "OPEN_ORDER",
        "name": "Open Order",
        "aliases_en": ["Open Order", "Pending Order", "Outstanding Order", "Unfulfilled Order"],
        "aliases_es": ["Pedido Abierto", "Pedido Pendiente", "Órdenes Pendientes", "Pedidos sin Cerrar"],
        "entity_keywords": ["order", "pedido", "orden"],
        "condition_semantic": "Order is open, pending, not closed/cancelled",
        "is_filter": True,
        "is_computed": False,
    },
    {
        "code": "COMPLETED_SALE",
        "name": "Completed Sale",
        "aliases_en": ["Completed Sale", "Successful Sale", "Finalized Sale", "Paid Order"],
        "aliases_es": ["Venta Completada", "Venta Exitosa", "Venta Finalizada", "Venta Pagada"],
        "entity_keywords": ["order", "pedido", "sale", "venta", "invoice", "factura"],
        "condition_semantic": "Order/invoice paid, completed and not cancelled",
        "is_filter": True,
        "is_computed": False,
    },
    {
        "code": "AVAILABLE_INVENTORY",
        "name": "Available Inventory",
        "aliases_en": ["Available Inventory", "In Stock", "On Hand", "Available Stock"],
        "aliases_es": ["Inventario Disponible", "Stock Disponible", "Existencia", "Unidades en Stock"],
        "entity_keywords": ["inventory", "inventario", "stock", "warehouse", "almacen"],
        "condition_semantic": "Inventory quantity > 0, not reserved, not expired",
        "is_filter": True,
        "is_computed": True,
    },
    {
        "code": "CANCELLED_INVOICE",
        "name": "Cancelled Invoice",
        "aliases_en": ["Cancelled Invoice", "Canceled Invoice", "Void Invoice", "Annulled Invoice"],
        "aliases_es": ["Factura Anulada", "Factura Cancelada", "Comprobante Anulado"],
        "entity_keywords": ["invoice", "factura", "bill", "recibo"],
        "condition_semantic": "Invoice status is cancelled/voided",
        "is_filter": True,
        "is_computed": False,
    },
    {
        "code": "PAID_INVOICE",
        "name": "Paid Invoice",
        "aliases_en": ["Paid Invoice", "Settled Invoice", "Collected Invoice"],
        "aliases_es": ["Factura Pagada", "Factura Cobrada", "Factura Liquidada"],
        "entity_keywords": ["invoice", "factura", "bill"],
        "condition_semantic": "Invoice is fully paid and not cancelled",
        "is_filter": True,
        "is_computed": False,
    },
    {
        "code": "OVERDUE_INVOICE",
        "name": "Overdue Invoice",
        "aliases_en": ["Overdue Invoice", "Past Due Invoice", "Unpaid Invoice"],
        "aliases_es": ["Factura Vencida", "Factura Pendiente de Pago", "Factura Impagada"],
        "entity_keywords": ["invoice", "factura", "bill"],
        "condition_semantic": "Invoice unpaid and due_date has passed",
        "is_filter": True,
        "is_computed": True,
    },
    {
        "code": "REVENUE",
        "name": "Revenue",
        "aliases_en": ["Revenue", "Sales Revenue", "Turnover", "Total Sales"],
        "aliases_es": ["Ingresos", "Facturación", "Ventas", "Ingresos Totales", "Facturado"],
        "entity_keywords": ["invoice", "factura", "order", "pedido", "sale", "venta"],
        "condition_semantic": "Total billed amount from paid/completed invoices/orders",
        "is_filter": False,
        "is_computed": True,
    },
    {
        "code": "GROSS_PROFIT",
        "name": "Gross Profit",
        "aliases_en": ["Gross Profit", "GP", "Gross Margin"],
        "aliases_es": ["Ganancia Bruta", "Margen Bruto", "Utilidad Bruta"],
        "entity_keywords": ["invoice", "factura", "order", "pedido"],
        "condition_semantic": "Revenue minus cost of goods sold",
        "is_filter": False,
        "is_computed": True,
    },
]

STATUS_COLUMN_PATTERNS = [
    "status", "estado", "state", "estatus",
    "is_active", "active", "activo",
    "deleted", "eliminado", "is_deleted",
    "enabled", "habilitado", "disabled", "deshabilitado",
    "approved", "aprobado", "confirmed", "confirmado",
    "paid", "pagado", "paid_status",
    "cancelled", "cancelado", "canceled",
    "terminated", "desvinculado", "cesado",
    "closed", "cerrado", "fulfilled", "cumplido",
]

DATE_CREATED_PATTERNS = [
    "created_at", "created_date", "fecha_creacion", "fecha_alta",
    "inserted_at", "date_created",
    "joined_at", "date_joined", "fecha_ingreso",
]

DATE_END_PATTERNS = [
    "terminated_at", "termination_date", "fecha_desvinculacion",
    "end_date", "end_dt", "fecha_fin",
    "deleted_at", "fecha_baja",
]


def _snake_case_upper(text: str) -> str:
    text = text.strip()
    s1 = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", text)
    s2 = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s1)
    s3 = re.sub(r"[^a-zA-Z0-9]+", "_", s2)
    return s3.strip("_").upper() or "UNKNOWN"


class BusinessConceptService:
    def __init__(
        self,
        semantic_repository: Any = None,
        llm_enabled: bool = True,
    ) -> None:
        self.semantic_repository = semantic_repository
        self.llm_enabled = llm_enabled
        logger.info(
            "BusinessConceptService initialized",
            extra={"llm_enabled": llm_enabled, "has_repository": semantic_repository is not None},
        )

    async def detect_concepts(
        self,
        database_metadata: DatabaseMetadata,
        entities: list[BusinessEntity],
        domains: list[BusinessDomain],
        table_analyses: list[Any] | None = None,
        metrics: list[BusinessMetric] | None = None,
        columns_profiles: dict[str, ColumnProfile] | None = None,
    ) -> list[BusinessConcept]:
        if not database_metadata:
            raise InvalidInputError(
                detail="database_metadata is required for concept detection",
                field="database_metadata",
            )

        logger.info(
            "Starting business concept detection",
            extra={
                "entity_count": len(entities or []),
                "metric_count": len(metrics or []),
            },
        )

        all_tables = self._flatten_tables(database_metadata)
        table_map: dict[str, TableMetadata] = {
            f"{t.schema_name}.{t.table_name}": t for t in all_tables
        }

        entity_by_code: dict[str, BusinessEntity] = {e.code: e for e in entities or []}
        domain_by_code: dict[str, BusinessDomain] = {d.code: d for d in domains or []}

        concepts: list[BusinessConcept] = []

        try:
            for preset in CONCEPT_PRESETS:
                matched_entities = self._match_entities(
                    preset["entity_keywords"], entities or []
                )
                matched_tables: list[str] = []
                source_columns: list[str] = []
                status_column_name: Optional[str] = None
                has_status_enum = False
                for ent in matched_entities:
                    for tname in ent.tables:
                        tbl = table_map.get(tname)
                        if tbl is None:
                            continue
                        matched_tables.append(tname)
                        sc = self._find_status_column(tbl)
                        if sc:
                            status_column_name = sc
                            source_columns.append(sc)
                            if tbl.column_profiles and sc in tbl.column_profiles:
                                cp = tbl.column_profiles[sc]
                                if cp.sample_distinct_values:
                                    values_lower = {
                                        str(v).lower() for v in cp.sample_distinct_values
                                    }
                                    has_status_enum = bool(
                                        values_lower & STATUS_VALUES_ACTIVE
                                        or values_lower & STATUS_VALUES_INACTIVE
                                    )

                if not matched_entities and not matched_tables:
                    all_names = " ".join(
                        tn.lower() + " " + (table_map[tn].comment or "").lower()
                        for tn in table_map
                    )
                    if not any(kw.lower() in all_names for kw in preset["entity_keywords"]):
                        continue

                aliases = list(
                    dict.fromkeys(
                        preset["aliases_en"] + preset["aliases_es"] + [preset["name"]]
                    )
                )

                entity_code = matched_entities[0].code if matched_entities else None
                domain_code = None
                if entity_code and entity_code in entity_by_code:
                    domain_code = entity_by_code[entity_code].domain_code
                if not domain_code and matched_entities:
                    domain_code = matched_entities[0].domain_code

                condition_sql: Optional[str] = None
                if status_column_name:
                    condition_sql = self._suggest_condition_sql(
                        preset["code"], status_column_name, has_status_enum
                    )

                confidence = 0.4
                evidence: list[str] = []
                if matched_entities:
                    confidence += 0.2
                    evidence.append(
                        f"Matched entities: {', '.join(e.name for e in matched_entities[:2])}"
                    )
                if matched_tables:
                    confidence += 0.1
                    evidence.append(
                        f"Linked tables: {', '.join(matched_tables[:3])}"
                    )
                if status_column_name:
                    confidence += 0.1
                    evidence.append(f"Found status column: {status_column_name}")
                if has_status_enum:
                    confidence += 0.1
                    evidence.append("Status column values include active/inactive patterns")
                if condition_sql:
                    confidence += 0.05
                    evidence.append(f"Suggested SQL: {condition_sql}")

                confidence = min(1.0, confidence)

                concept = BusinessConcept(
                    name=preset["name"],
                    code=preset["code"],
                    description=preset["condition_semantic"],
                    aliases=[a for a in aliases if a.lower() != preset["name"].lower()],
                    entity_code=entity_code,
                    domain_code=domain_code,
                    tables=list(dict.fromkeys(matched_tables)),
                    source_columns=list(dict.fromkeys(source_columns)),
                    condition_semantic=preset["condition_semantic"],
                    condition_sql=condition_sql,
                    value_expression=None,
                    is_filter=bool(preset["is_filter"]),
                    is_computed=bool(preset["is_computed"]),
                    confidence=confidence,
                    evidence=evidence,
                )
                concepts.append(concept)

            concepts = [c for c in concepts if c.confidence >= 0.5]
            concepts.sort(key=lambda c: (-c.confidence, c.name))

        except InvalidInputError:
            raise
        except Exception as exc:
            logger.error(
                "Concept detection failed",
                exc_info=exc,
                extra={"error_type": type(exc).__name__},
            )
            raise SemanticAnalysisError(
                detail=f"Failed to detect business concepts: {exc!s}",
                extra={"stage": "concept_detection"},
            ) from exc

        logger.info(
            "Business concept detection completed",
            extra={"concept_count": len(concepts)},
        )
        return concepts

    def _flatten_tables(self, metadata: DatabaseMetadata) -> list[TableMetadata]:
        tables: list[TableMetadata] = []
        for schema in metadata.schemas:
            for t in schema.tables or []:
                tables.append(t)
            for v in schema.views or []:
                tables.append(v)
        return tables

    def _match_entities(
        self,
        keywords: list[str],
        entities: list[BusinessEntity],
    ) -> list[BusinessEntity]:
        matched: list[BusinessEntity] = []
        for ent in entities:
            haystack_parts = [
                ent.name.lower(),
                ent.code.lower(),
                " ".join(ent.aliases or []).lower(),
                " ".join(ent.tables or []).lower(),
            ]
            haystack = " | ".join(haystack_parts)
            if any(kw.lower() in haystack for kw in keywords):
                matched.append(ent)
        return matched

    def _find_status_column(self, table: TableMetadata) -> Optional[str]:
        for col in table.columns:
            cname = col.name.lower()
            if any(p in cname for p in STATUS_COLUMN_PATTERNS):
                return col.name
        return None

    def _suggest_condition_sql(
        self,
        concept_code: str,
        status_col: str,
        has_known_values: bool,
    ) -> str:
        col = status_col
        code = concept_code

        if code in {"ACTIVE_EMPLOYEE", "ACTIVE_CUSTOMER"}:
            if has_known_values:
                return f"LOWER({col}) IN ('active', 'activo', 'vigente', 'current', 'actual', 'enabled', 'habilitado')"
            return f"{col} IS NOT NULL AND {col} != 'INACTIVE' AND {col} != 'DELETED'"

        if code in {"TERMINATED_EMPLOYEE"}:
            if has_known_values:
                return f"LOWER({col}) IN ('terminated', 'inactive', 'desvinculado', 'cesado', 'eliminado', 'deleted', 'inactivo')"
            return f"LOWER({col}) LIKE '%terminated%' OR LOWER({col}) LIKE '%inactive%'"

        if code in {"OPEN_ORDER"}:
            if has_known_values:
                return f"LOWER({col}) IN ('open', 'abierto', 'pending', 'pendiente', 'in_progress', 'en_progreso', 'processing')"
            return f"LOWER({col}) NOT IN ('closed', 'cancelled', 'completed')"

        if code in {"COMPLETED_SALE", "PAID_INVOICE"}:
            if has_known_values:
                return f"LOWER({col}) IN ('paid', 'pagado', 'completed', 'completado', 'settled', 'finalized', 'pagada')"
            return f"LOWER({col}) IN ('paid', 'completed')"

        if code in {"CANCELLED_INVOICE"}:
            if has_known_values:
                return f"LOWER({col}) IN ('cancelled', 'canceled', 'anulado', 'anulada', 'void', 'deleted', 'eliminado')"
            return f"LOWER({col}) LIKE '%cancel%' OR LOWER({col}) LIKE '%anul%'"

        if code in {"OVERDUE_INVOICE"}:
            return f"LOWER({col}) NOT IN ('paid', 'cancelled') AND due_date < CURRENT_DATE"

        if code in {"AVAILABLE_INVENTORY"}:
            return f"quantity > 0 AND LOWER({col}) NOT IN ('reserved', 'expired')"

        if col:
            return f"{col} IS NOT NULL"
        return "1 = 1"

    async def persist_concepts(
        self,
        concepts: list[BusinessConcept],
        **kwargs: Any,
    ) -> bool:
        if not self.semantic_repository:
            logger.warning("persist_concepts skipped: no semantic_repository configured")
            return False
        try:
            repo = self.semantic_repository
            if hasattr(repo, "persist_concepts"):
                await repo.persist_concepts(concepts=concepts, **kwargs)
            else:
                logger.debug(
                    "semantic_repository has no persist_concepts method; stored in memory only"
                )
            logger.info(
                "Persisted concepts",
                extra={"concept_count": len(concepts)},
            )
            return True
        except Exception as exc:
            logger.error(
                "Failed to persist concepts",
                exc_info=exc,
                extra={"error_type": type(exc).__name__},
            )
            raise SemanticAnalysisError(
                detail=f"Failed to persist concepts: {exc!s}",
                extra={"stage": "concept_persistence"},
            ) from exc
