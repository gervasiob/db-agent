from __future__ import annotations

import re
from typing import Any, Optional

from app.core.exceptions import InvalidInputError, SemanticAnalysisError
from app.core.logging import get_logger
from app.models.database import DatabaseMetadata, TableMetadata
from app.models.semantic import (
    AggregationType,
    BusinessConcept,
    BusinessDomain,
    BusinessEntity,
    BusinessMetric,
)

logger = get_logger(__name__)


NUMERIC_COLUMN_RAW_TYPES = {
    "INTEGER", "BIGINT", "SMALLINT",
    "FLOAT", "NUMERIC", "DECIMAL", "REAL", "DOUBLE",
    "INT", "INT2", "INT4", "INT8", "FLOAT4", "FLOAT8",
    "MONEY",
}

MONETARY_KEYWORDS = [
    "total", "amount", "price", "cost", "subtotal", "tax", "iva",
    "discount", "fee", "shipping", "freight", "revenue", "income",
    "monto", "importe", "precio", "costo", "subtotal",
    "descuento", "impuesto", "envio", "ingreso",
]

COUNTABLE_ENTITY_KEYWORDS = {
    "employee": ["employee", "empleado", "trabajador", "colaborador", "staff"],
    "customer": ["customer", "cliente", "client", "consumidor", "usuario"],
    "order": ["order", "pedido", "orden", "compra"],
    "invoice": ["invoice", "factura", "comprobante", "recibo"],
    "product": ["product", "producto", "articulo", "item"],
    "payment": ["payment", "pago", "abono", "cobro"],
    "shipment": ["shipment", "envio", "despacho", "entrega"],
    "transaction": ["transaction", "transaccion", "movimiento"],
    "supplier": ["supplier", "proveedor", "vendor"],
}

METRIC_PRESETS: list[dict[str, Any]] = [
    {
        "code": "EMPLOYEE_COUNT",
        "name": "Employees",
        "aliases_en": ["Employees", "Total Employees", "Headcount", "Staff Count", "Workforce"],
        "aliases_es": ["Empleados", "Total Empleados", "Cantidad de Empleados", "Plantilla", "Personal"],
        "entity_key": "employee",
        "aggregation": AggregationType.COUNT,
        "source_column_hint": "id",
        "filter_active": True,
        "description": "Total number of employees (optionally filtered by active status)",
    },
    {
        "code": "ACTIVE_EMPLOYEE_COUNT",
        "name": "Active Employees",
        "aliases_en": ["Active Employees", "Active Headcount", "Current Employees", "Current Staff"],
        "aliases_es": ["Empleados Activos", "Plantilla Activa", "Personal Activo", "Empleados Vigentes"],
        "entity_key": "employee",
        "aggregation": AggregationType.COUNT,
        "source_column_hint": "id",
        "filter_active": True,
        "concept_code": "ACTIVE_EMPLOYEE",
        "description": "Number of active, non-terminated employees",
    },
    {
        "code": "CUSTOMER_COUNT",
        "name": "Customers",
        "aliases_en": ["Customers", "Total Customers", "Client Count"],
        "aliases_es": ["Clientes", "Total Clientes", "Cantidad de Clientes"],
        "entity_key": "customer",
        "aggregation": AggregationType.COUNT,
        "source_column_hint": "id",
        "filter_active": False,
        "description": "Total number of customers in the database",
    },
    {
        "code": "ACTIVE_CUSTOMER_COUNT",
        "name": "Active Customers",
        "aliases_en": ["Active Customers", "Active Clients", "Current Customers"],
        "aliases_es": ["Clientes Activos", "Clientes Vigentes", "Clientes Actuales"],
        "entity_key": "customer",
        "aggregation": AggregationType.COUNT_DISTINCT,
        "source_column_hint": "customer_id",
        "filter_active": True,
        "concept_code": "ACTIVE_CUSTOMER",
        "description": "Number of customers with active status",
    },
    {
        "code": "NEW_CUSTOMER_COUNT",
        "name": "New Customers",
        "aliases_en": ["New Customers", "New Signups", "Customer Signups"],
        "aliases_es": ["Nuevos Clientes", "Altas de Clientes", "Clientes Nuevos", "Registros Nuevos"],
        "entity_key": "customer",
        "aggregation": AggregationType.COUNT,
        "source_column_hint": "id",
        "date_column_hint": "created_at",
        "concept_code": "NEW_CUSTOMER",
        "description": "Count of new customers registered (typically grouped by period)",
    },
    {
        "code": "REVENUE",
        "name": "Revenue",
        "aliases_en": ["Revenue", "Sales Revenue", "Total Revenue", "Turnover", "Sales"],
        "aliases_es": ["Ingresos", "Facturación", "Ventas", "Ingresos Totales", "Facturado", "Facturacion"],
        "entity_key": "invoice",
        "aggregation": AggregationType.SUM,
        "source_column_hint": "total",
        "filter_paid": True,
        "concept_code": "COMPLETED_SALE",
        "description": "Total revenue from paid/completed invoices or orders",
    },
    {
        "code": "GROSS_SALES",
        "name": "Gross Sales",
        "aliases_en": ["Gross Sales", "Total Sales", "Gross Revenue"],
        "aliases_es": ["Ventas Brutas", "Ventas Totales", "Ingresos Brutos"],
        "entity_key": "invoice",
        "aggregation": AggregationType.SUM,
        "source_column_hint": "subtotal",
        "filter_paid": False,
        "description": "Total gross sales amount from all invoices (pre-discount/tax)",
    },
    {
        "code": "ORDER_COUNT",
        "name": "Orders",
        "aliases_en": ["Orders", "Total Orders", "Order Count", "Number of Orders"],
        "aliases_es": ["Pedidos", "Total Pedidos", "Cantidad de Pedidos", "Órdenes"],
        "entity_key": "order",
        "aggregation": AggregationType.COUNT,
        "source_column_hint": "id",
        "description": "Total number of orders placed",
    },
    {
        "code": "COMPLETED_ORDER_COUNT",
        "name": "Completed Orders",
        "aliases_en": ["Completed Orders", "Fulfilled Orders", "Successful Orders"],
        "aliases_es": ["Pedidos Completados", "Pedidos Cumplidos", "Pedidos Finalizados"],
        "entity_key": "order",
        "aggregation": AggregationType.COUNT,
        "source_column_hint": "id",
        "filter_completed": True,
        "concept_code": "COMPLETED_SALE",
        "description": "Number of completed/paid orders",
    },
    {
        "code": "AVERAGE_ORDER_VALUE",
        "name": "Average Order Value",
        "aliases_en": ["Average Order Value", "AOV", "Avg Order Value"],
        "aliases_es": ["Ticket Medio", "Valor Medio de Pedido", "Valor Promedio de Pedido", "Venta Promedio"],
        "entity_key": "invoice",
        "aggregation": AggregationType.AVG,
        "source_column_hint": "total",
        "filter_paid": True,
        "description": "Average value (total/amount) per completed invoice or order",
    },
    {
        "code": "PAYMENT_TOTAL",
        "name": "Payments",
        "aliases_en": ["Payments", "Total Payments", "Collected Amount", "Payments Total"],
        "aliases_es": ["Pagos", "Cobros", "Total Pagado", "Pagos Cobrados", "Importe Cobrado"],
        "entity_key": "payment",
        "aggregation": AggregationType.SUM,
        "source_column_hint": "amount",
        "description": "Total monetary amount of payments received/collected",
    },
    {
        "code": "INVOICE_COUNT",
        "name": "Invoices",
        "aliases_en": ["Invoices", "Total Invoices", "Invoice Count", "Bills"],
        "aliases_es": ["Facturas", "Total Facturas", "Cantidad de Facturas", "Comprobantes"],
        "entity_key": "invoice",
        "aggregation": AggregationType.COUNT,
        "source_column_hint": "id",
        "description": "Total number of invoices issued",
    },
    {
        "code": "PAID_INVOICE_COUNT",
        "name": "Paid Invoices",
        "aliases_en": ["Paid Invoices", "Collected Invoices", "Settled Invoices"],
        "aliases_es": ["Facturas Pagadas", "Facturas Cobradas", "Facturas Liquidadas"],
        "entity_key": "invoice",
        "aggregation": AggregationType.COUNT,
        "source_column_hint": "id",
        "filter_paid": True,
        "concept_code": "PAID_INVOICE",
        "description": "Number of invoices that have been paid",
    },
    {
        "code": "OVERDUE_INVOICE_AMOUNT",
        "name": "Overdue Amount",
        "aliases_en": ["Overdue Amount", "Overdue Balance", "Outstanding Amount"],
        "aliases_es": ["Monto Vencido", "Saldo Vencido", "Importe Impagado", "Monto Pendiente"],
        "entity_key": "invoice",
        "aggregation": AggregationType.SUM,
        "source_column_hint": "total",
        "filter_overdue": True,
        "concept_code": "OVERDUE_INVOICE",
        "description": "Total outstanding amount from overdue (past due) invoices",
    },
    {
        "code": "PRODUCT_COUNT",
        "name": "Products",
        "aliases_en": ["Products", "Total Products", "SKU Count", "Catalog Size"],
        "aliases_es": ["Productos", "Total Productos", "Artículos", "SKUs", "Tamaño de Catálogo"],
        "entity_key": "product",
        "aggregation": AggregationType.COUNT,
        "source_column_hint": "id",
        "description": "Total number of products or SKUs in the catalog",
    },
    {
        "code": "INVENTORY_QUANTITY",
        "name": "Stock Quantity",
        "aliases_en": ["Stock Quantity", "Inventory Quantity", "Units on Hand", "Total Stock"],
        "aliases_es": ["Cantidad en Stock", "Existencias", "Unidades en Stock", "Inventario Total"],
        "entity_key": "inventory",
        "aggregation": AggregationType.SUM,
        "source_column_hint": "quantity",
        "concept_code": "AVAILABLE_INVENTORY",
        "description": "Total number of units available in inventory/stock",
    },
]

STATUS_COLUMN_PATTERNS = [
    "status", "estado", "state", "estatus",
    "paid", "pagado", "paid_status",
    "active", "activo", "vigente",
    "completed", "completado", "cumplido",
    "closed", "cerrado",
]


def _snake_case_upper(text: str) -> str:
    text = text.strip()
    s1 = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", text)
    s2 = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s2)
    s3 = re.sub(r"[^a-zA-Z0-9]+", "_", s2)
    return s3.strip("_").upper() or "UNKNOWN"


def _is_numeric_type(raw_type: str) -> bool:
    if not raw_type:
        return False
    upper = raw_type.upper()
    return any(upper.startswith(t) for t in NUMERIC_COLUMN_RAW_TYPES)


class BusinessMetricService:
    def __init__(
        self,
        semantic_repository: Any = None,
        llm_enabled: bool = True,
    ) -> None:
        self.semantic_repository = semantic_repository
        self.llm_enabled = llm_enabled
        logger.info(
            "BusinessMetricService initialized",
            extra={"llm_enabled": llm_enabled, "has_repository": semantic_repository is not None},
        )

    async def detect_metrics(
        self,
        database_metadata: DatabaseMetadata,
        entities: list[BusinessEntity],
        concepts: list[BusinessConcept] | None = None,
        domains: list[BusinessDomain] | None = None,
        profiles: Any | None = None,
    ) -> list[BusinessMetric]:
        if not database_metadata:
            raise InvalidInputError(
                detail="database_metadata is required for metric detection",
                field="database_metadata",
            )

        logger.info(
            "Starting business metric detection",
            extra={"entity_count": len(entities or []), "concept_count": len(concepts or [])},
        )

        all_tables = self._flatten_tables(database_metadata)
        table_map: dict[str, TableMetadata] = {
            f"{t.schema_name}.{t.table_name}": t for t in all_tables
        }
        concepts_by_code = {c.code: c for c in concepts or []}

        metrics: list[BusinessMetric] = []

        try:
            entity_by_key: dict[str, list[BusinessEntity]] = {}
            for ent in entities or []:
                for canonical_key, kw_list in COUNTABLE_ENTITY_KEYWORDS.items():
                    haystack = " ".join(
                        [ent.name.lower(), ent.code.lower()]
                        + [a.lower() for a in ent.aliases]
                        + ent.tables
                    )
                    if any(kw in haystack for kw in kw_list):
                        entity_by_key.setdefault(canonical_key, []).append(ent)
                        break

            for preset in METRIC_PRESETS:
                entity_key = preset["entity_key"]
                matched_entities = entity_by_key.get(entity_key, [])
                if not matched_entities and entity_key != "inventory":
                    inventory_match = [
                        e
                        for e in entities or []
                        if "inventory" in " ".join(e.tables).lower()
                        or "inventario" in " ".join(e.tables).lower()
                        or "stock" in " ".join(e.tables).lower()
                    ]
                    if entity_key == "inventory" and inventory_match:
                        matched_entities = inventory_match
                    else:
                        continue

                source_tables: list[str] = []
                source_column: Optional[str] = None
                date_column: Optional[str] = None
                filter_condition: Optional[str] = None
                status_col_name: Optional[str] = None
                domain_code: Optional[str] = None

                for ent in matched_entities:
                    if ent.domain_code:
                        domain_code = ent.domain_code
                    for tname in ent.tables:
                        tbl = table_map.get(tname)
                        if tbl is None:
                            continue
                        source_tables.append(tname)
                        sc = self._pick_source_column(
                            tbl,
                            preset["aggregation"],
                            preset["source_column_hint"],
                        )
                        if sc and source_column is None:
                            source_column = sc
                        dc = self._pick_date_column(tbl)
                        if dc and date_column is None:
                            date_column = dc
                        stat_c = self._find_status_column(tbl)
                        if stat_c and status_col_name is None:
                            status_col_name = stat_c

                if not source_tables:
                    continue
                if source_column is None and preset["aggregation"] in {
                    AggregationType.SUM,
                    AggregationType.AVG,
                    AggregationType.MIN,
                    AggregationType.MAX,
                }:
                    continue

                if status_col_name:
                    filter_condition = self._build_filter_condition(
                        preset, status_col_name
                    )

                aliases = list(
                    dict.fromkeys(
                        preset["aliases_en"] + preset["aliases_es"] + [preset["name"]]
                    )
                )

                concept_code = preset.get("concept_code")
                if concept_code and concept_code in concepts_by_code:
                    concept = concepts_by_code[concept_code]
                    source_tables = list(dict.fromkeys(source_tables + concept.tables))

                entity_code = matched_entities[0].code if matched_entities else None

                confidence = 0.4
                evidence: list[str] = []
                if matched_entities:
                    confidence += 0.15
                    evidence.append(
                        f"Matched entity: {matched_entities[0].name}"
                    )
                if source_tables:
                    confidence += 0.1
                    evidence.append(
                        f"Source tables: {', '.join(source_tables[:2])}"
                    )
                if source_column:
                    confidence += 0.15
                    evidence.append(f"Source column: {source_column}")
                if date_column:
                    confidence += 0.05
                    evidence.append(f"Date column: {date_column}")
                if filter_condition:
                    confidence += 0.05
                    evidence.append(f"Filter: {filter_condition}")
                confidence = min(1.0, confidence)

                metric = BusinessMetric(
                    name=preset["name"],
                    code=preset["code"],
                    description=preset["description"],
                    aliases=[a for a in aliases if a.lower() != preset["name"].lower()],
                    domain_code=domain_code,
                    entity_code=entity_code,
                    source_tables=list(dict.fromkeys(source_tables)),
                    source_column=source_column,
                    aggregation=preset["aggregation"],
                    filter_condition=filter_condition,
                    date_column=date_column,
                    default_dimensions=[],
                    confidence=confidence,
                    evidence=evidence,
                )
                metrics.append(metric)

            metrics = [m for m in metrics if m.confidence >= 0.5]
            metrics.sort(key=lambda m: (-m.confidence, m.name))

        except InvalidInputError:
            raise
        except Exception as exc:
            logger.error(
                "Metric detection failed",
                exc_info=exc,
                extra={"error_type": type(exc).__name__},
            )
            raise SemanticAnalysisError(
                detail=f"Failed to detect business metrics: {exc!s}",
                extra={"stage": "metric_detection"},
            ) from exc

        logger.info(
            "Business metric detection completed",
            extra={"metric_count": len(metrics)},
        )
        return metrics

    def _flatten_tables(self, metadata: DatabaseMetadata) -> list[TableMetadata]:
        tables: list[TableMetadata] = []
        for schema in metadata.schemas:
            for t in schema.tables or []:
                tables.append(t)
            for v in schema.views or []:
                tables.append(v)
        return tables

    def _pick_source_column(
        self,
        table: TableMetadata,
        aggregation: AggregationType,
        hint: str,
    ) -> Optional[str]:
        if aggregation in {AggregationType.COUNT, AggregationType.COUNT_DISTINCT}:
            for col in table.columns:
                cname = col.name.lower()
                if hint and hint.lower() == cname:
                    return col.name
            for col in table.columns:
                if col.is_pk:
                    return col.name
            for col in table.columns:
                cname = col.name.lower()
                if cname in {"id", hint.lower()} or cname.endswith("_" + hint.lower()):
                    return col.name
            return None

        numeric_cols = [c for c in table.columns if _is_numeric_type(c.raw_type)]
        if not numeric_cols:
            return None

        hint_lower = (hint or "").lower()
        if hint_lower:
            for col in numeric_cols:
                cname = col.name.lower()
                if cname == hint_lower:
                    return col.name
            for col in numeric_cols:
                cname = col.name.lower()
                if hint_lower in cname:
                    return col.name

        for col in numeric_cols:
            cname = col.name.lower()
            if any(kw in cname for kw in MONETARY_KEYWORDS):
                return col.name

        return numeric_cols[0].name

    def _pick_date_column(self, table: TableMetadata) -> Optional[str]:
        for col in table.columns:
            ctype = (col.type or "").upper()
            if ctype not in {"DATE", "TIMESTAMP", "TIMESTAMPTZ", "DATETIME"}:
                continue
            cname = col.name.lower()
            if any(
                kw in cname
                for kw in [
                    "created", "creacion", "alta",
                    "order", "pedido",
                    "invoice", "factura",
                    "payment", "pago",
                    "date", "fecha",
                    "event", "evento",
                ]
            ):
                return col.name
        for col in table.columns:
            ctype = (col.type or "").upper()
            if ctype in {"DATE", "TIMESTAMP", "TIMESTAMPTZ", "DATETIME"}:
                return col.name
        return None

    def _find_status_column(self, table: TableMetadata) -> Optional[str]:
        for col in table.columns:
            cname = col.name.lower()
            if any(p in cname for p in STATUS_COLUMN_PATTERNS):
                return col.name
        return None

    def _build_filter_condition(
        self,
        preset: dict[str, Any],
        status_col: str,
    ) -> Optional[str]:
        col = status_col
        if preset.get("filter_paid"):
            return f"LOWER({col}) IN ('paid', 'pagado', 'completed', 'completado', 'settled', 'finalized')"
        if preset.get("filter_completed"):
            return f"LOWER({col}) IN ('completed', 'completado', 'paid', 'pagado', 'closed', 'cerrado')"
        if preset.get("filter_active"):
            return f"LOWER({col}) IN ('active', 'activo', 'vigente', 'current', 'actual', 'enabled', 'habilitado')"
        if preset.get("filter_overdue"):
            return f"LOWER({col}) NOT IN ('paid', 'cancelled') AND due_date < CURRENT_DATE"
        return None

    async def persist_metrics(
        self,
        metrics: list[BusinessMetric],
        **kwargs: Any,
    ) -> bool:
        if not self.semantic_repository:
            logger.warning("persist_metrics skipped: no semantic_repository configured")
            return False
        try:
            repo = self.semantic_repository
            if hasattr(repo, "persist_metrics"):
                await repo.persist_metrics(metrics=metrics, **kwargs)
            else:
                logger.debug(
                    "semantic_repository has no persist_metrics method; stored in memory only"
                )
            logger.info(
                "Persisted metrics",
                extra={"metric_count": len(metrics)},
            )
            return True
        except Exception as exc:
            logger.error(
                "Failed to persist metrics",
                exc_info=exc,
                extra={"error_type": type(exc).__name__},
            )
            raise SemanticAnalysisError(
                detail=f"Failed to persist metrics: {exc!s}",
                extra={"stage": "metric_persistence"},
            ) from exc
