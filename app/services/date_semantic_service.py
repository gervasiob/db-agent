from __future__ import annotations

import re
from typing import Any, Optional

from app.core.exceptions import InvalidInputError, SemanticAnalysisError
from app.core.logging import get_logger
from app.models.database import ColumnProfile, DatabaseMetadata, TableMetadata
from app.models.semantic import DateSemantic, DateSemanticRole

logger = get_logger(__name__)


DATE_TYPES = {"DATE", "TIMESTAMP", "TIMESTAMPTZ", "DATETIME", "TIME", "TIMESTAMP WITHOUT TIME ZONE", "TIMESTAMP WITH TIME ZONE"}


SEMANTIC_RULES: list[dict[str, Any]] = [
    {
        "role": DateSemanticRole.CREATED_AT,
        "name": "CREATED_AT",
        "patterns_en": [
            "created_at", "created_date", "created_on", "date_created",
            "inserted_at", "inserted_date", "insert_date",
            "creation_date", "dt_created", "createdts",
            "created_timestamp",
        ],
        "patterns_es": [
            "fecha_creacion", "fecha_alta", "fecha_creado",
            "fecha_insercion", "fecha_registro",
            "dt_creacion", "ts_creacion", "created_at",
            "fec_creacion", "fec_alta",
        ],
        "description": "Fecha y hora de creación del registro",
    },
    {
        "role": DateSemanticRole.UPDATED_AT,
        "name": "UPDATED_AT",
        "patterns_en": [
            "updated_at", "updated_date", "updated_on",
            "modified_at", "modified_date", "date_modified",
            "last_updated", "last_modified", "changed_at",
            "dt_updated", "updatedts",
        ],
        "patterns_es": [
            "fecha_modificacion", "fecha_actualizacion",
            "fecha_modificado", "ultima_modificacion",
            "actualizado_el", "dt_modificacion",
            "fec_modificacion", "fec_actualizacion",
        ],
        "description": "Fecha y hora de última actualización del registro",
    },
    {
        "role": DateSemanticRole.DELETED_AT,
        "name": "DELETED_AT",
        "patterns_en": [
            "deleted_at", "deleted_date", "deleted_on",
            "soft_deleted_at", "archived_at", "purged_at",
        ],
        "patterns_es": [
            "fecha_baja", "fecha_eliminacion", "fecha_borrado",
            "fecha_desactivacion", "eliminado_el",
            "fec_baja", "dt_baja",
        ],
        "description": "Fecha de borrado lógico o desactivación del registro",
    },
    {
        "role": DateSemanticRole.ORDER_DATE,
        "name": "ORDER_DATE",
        "patterns_en": [
            "order_date", "placed_at", "placed_date", "purchase_date",
            "order_placed", "sale_date", "sold_date", "transaction_date",
            "date_ordered",
        ],
        "patterns_es": [
            "fecha_pedido", "fecha_orden", "fecha_compra",
            "fecha_venta", "fecha_transaccion",
            "fec_pedido", "fec_orden", "fec_compra",
        ],
        "description": "Fecha en que se realizó el pedido/orden/compra",
    },
    {
        "role": DateSemanticRole.INVOICE_DATE,
        "name": "INVOICE_DATE",
        "patterns_en": [
            "invoice_date", "billed_at", "billing_date", "bill_date",
            "issue_date", "issued_at", "date_invoiced",
        ],
        "patterns_es": [
            "fecha_factura", "fecha_emision", "fecha_facturacion",
            "fecha_cobro", "fec_factura", "fec_emision",
            "fec_facturacion",
        ],
        "description": "Fecha de emisión o facturación",
    },
    {
        "role": DateSemanticRole.PAYMENT_DATE,
        "name": "PAYMENT_DATE",
        "patterns_en": [
            "payment_date", "paid_at", "paid_date", "paid_on",
            "collected_at", "collection_date", "date_paid",
            "settlement_date",
        ],
        "patterns_es": [
            "fecha_pago", "fecha_cobro", "fecha_pagado",
            "pagado_el", "cobrado_el", "fec_pago", "fec_cobro",
        ],
        "description": "Fecha en la que se efectuó o recibió el pago",
    },
    {
        "role": DateSemanticRole.HIRE_DATE,
        "name": "HIRE_DATE",
        "patterns_en": [
            "hire_date", "hired_at", "date_joined", "joined_at",
            "start_date", "start_dt", "employment_date", "onboarding_date",
        ],
        "patterns_es": [
            "fecha_contratacion", "fecha_ingreso", "fecha_alta_empresa",
            "fecha_inicio", "fecha_incorporacion",
            "fec_contratacion", "fec_ingreso", "fec_inicio",
        ],
        "description": "Fecha de contratación o ingreso a la organización",
    },
    {
        "role": DateSemanticRole.TERMINATION_DATE,
        "name": "TERMINATION_DATE",
        "patterns_en": [
            "termination_date", "terminated_at", "terminated_date",
            "end_date", "end_dt", "separation_date", "left_date",
            "departure_date", "exit_date", "resignation_date",
        ],
        "patterns_es": [
            "fecha_desvinculacion", "fecha_salida", "fecha_cese",
            "fecha_baja_empleado", "fecha_terminacion",
            "fec_baja_empleado", "fec_salida", "fec_cese",
        ],
        "description": "Fecha de terminación o desvinculación",
    },
    {
        "role": DateSemanticRole.BIRTH_DATE,
        "name": "BIRTH_DATE",
        "patterns_en": [
            "birth_date", "dob", "date_of_birth", "birthday",
            "born_date", "born_on",
        ],
        "patterns_es": [
            "fecha_nacimiento", "fecha_nac", "fecha_de_nacimiento",
            "cumpleaños", "cumpleanos",
            "fec_nacimiento", "fec_nac",
        ],
        "description": "Fecha de nacimiento",
    },
    {
        "role": DateSemanticRole.EVENT_DATE,
        "name": "EVENT_DATE",
        "patterns_en": [
            "event_date", "occurred_at", "event_time", "activity_date",
            "occurred_on", "happened_at", "log_date",
        ],
        "patterns_es": [
            "fecha_evento", "fecha_actividad", "fecha_ocurrencia",
            "fecha_suceso", "fec_evento", "fec_actividad",
        ],
        "description": "Fecha en la que ocurrió un evento o actividad",
    },
]


def _is_date_column_type(raw_type: str, col_type: Any) -> bool:
    if not raw_type and not col_type:
        return False
    raw_upper = (raw_type or "").upper()
    type_upper = (str(col_type) if col_type else "").upper()
    return any(t in raw_upper or t in type_upper for t in DATE_TYPES)


class DateSemanticService:
    def __init__(
        self,
        semantic_repository: Any = None,
        llm_enabled: bool = True,
    ) -> None:
        self.semantic_repository = semantic_repository
        self.llm_enabled = llm_enabled
        logger.info(
            "DateSemanticService initialized",
            extra={"llm_enabled": llm_enabled, "has_repository": semantic_repository is not None},
        )

    async def detect_date_semantics(
        self,
        metadata: DatabaseMetadata,
        table_analyses: list[Any] | None = None,
        profiles: dict[str, ColumnProfile] | None = None,
        samples: Any | None = None,
    ) -> list[DateSemantic]:
        if not metadata:
            raise InvalidInputError(
                detail="metadata is required for date semantic detection",
                field="metadata",
            )

        logger.info(
            "Starting date semantic detection",
            extra={"schema_count": len(metadata.schemas)},
        )

        all_tables = self._flatten_tables(metadata)
        results: list[DateSemantic] = []

        try:
            for table in all_tables:
                full_name = f"{table.schema_name}.{table.table_name}"
                table_name_lower = table.table_name.lower()
                for col in table.columns:
                    if not _is_date_column_type(col.raw_type, col.type):
                        heuristic_is_date = (
                            getattr(col, "heuristics", None)
                            and (col.heuristics.is_date or col.heuristics.is_timestamp)
                        )
                        if not heuristic_is_date:
                            continue
                    cname = col.name.lower()
                    matched_role: Optional[DateSemanticRole] = None
                    matched_desc = ""
                    confidence = 0.5
                    aliases: list[str] = []
                    for rule in SEMANTIC_RULES:
                        all_patterns = list(rule["patterns_en"]) + list(rule["patterns_es"])
                        for pat in all_patterns:
                            if cname == pat:
                                matched_role = rule["role"]
                                matched_desc = rule["description"]
                                confidence = 1.0
                                break
                            if cname.startswith(pat + "_") or cname.endswith("_" + pat):
                                matched_role = rule["role"]
                                matched_desc = rule["description"]
                                confidence = 0.9
                                break
                            if pat in cname:
                                if matched_role is None or confidence < 0.75:
                                    matched_role = rule["role"]
                                    matched_desc = rule["description"]
                                    confidence = 0.75
                        if confidence >= 0.9:
                            aliases = [
                                p
                                for p in list(rule["patterns_en"]) + list(rule["patterns_es"])
                                if p != cname
                            ][:6]
                            break

                    if matched_role is None:
                        table_hints = False
                        for rule in SEMANTIC_RULES:
                            all_patterns = list(rule["patterns_en"]) + list(rule["patterns_es"])
                            for pat in all_patterns:
                                clean = pat.replace("fecha_", "").replace("_date", "")
                                if len(clean) > 3 and (clean in table_name_lower):
                                    matched_role = rule["role"]
                                    matched_desc = f"{rule['description']} (inferred from table name {table.table_name})"
                                    confidence = 0.55
                                    table_hints = True
                                    break
                            if table_hints:
                                break

                    if matched_role is None:
                        matched_role = DateSemanticRole.OTHER
                        matched_desc = f"Columna de fecha genérica: {col.name}"
                        confidence = 0.4

                    ds = DateSemantic(
                        table_name=full_name,
                        column_name=col.name,
                        semantic_role=matched_role,
                        description=matched_desc,
                        aliases=list(dict.fromkeys(aliases)),
                        confidence=confidence,
                    )
                    results.append(ds)

        except InvalidInputError:
            raise
        except Exception as exc:
            logger.error(
                "Date semantic detection failed",
                exc_info=exc,
                extra={"error_type": type(exc).__name__},
            )
            raise SemanticAnalysisError(
                detail=f"Failed to detect date semantics: {exc!s}",
                extra={"stage": "date_semantic_detection"},
            ) from exc

        logger.info(
            "Date semantic detection completed",
            extra={"date_semantic_count": len(results)},
        )
        return results

    def _flatten_tables(self, metadata: DatabaseMetadata) -> list[TableMetadata]:
        tables: list[TableMetadata] = []
        for schema in metadata.schemas:
            for t in schema.tables or []:
                tables.append(t)
            for v in schema.views or []:
                tables.append(v)
        return tables

    async def persist_date_semantics(
        self,
        date_semantics: list[DateSemantic],
        **kwargs: Any,
    ) -> bool:
        if not self.semantic_repository:
            logger.warning("persist_date_semantics skipped: no semantic_repository configured")
            return False
        try:
            repo = self.semantic_repository
            if hasattr(repo, "persist_date_semantics"):
                await repo.persist_date_semantics(date_semantics=date_semantics, **kwargs)
            else:
                logger.debug(
                    "semantic_repository has no persist_date_semantics method; stored in memory only"
                )
            logger.info(
                "Persisted date semantics",
                extra={"date_semantic_count": len(date_semantics)},
            )
            return True
        except Exception as exc:
            logger.error(
                "Failed to persist date semantics",
                exc_info=exc,
                extra={"error_type": type(exc).__name__},
            )
            raise SemanticAnalysisError(
                detail=f"Failed to persist date semantics: {exc!s}",
                extra={"stage": "date_semantic_persistence"},
            ) from exc
