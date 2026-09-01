from __future__ import annotations

import re
from collections import defaultdict
from typing import Any, Optional

from app.core.exceptions import InvalidInputError, SemanticAnalysisError
from app.core.logging import get_logger
from app.models.database import DatabaseMetadata, TableMetadata
from app.models.semantic import BusinessDomain, BusinessEntity

logger = get_logger(__name__)


ENTITY_KEYWORDS: list[dict[str, Any]] = [
    {"singular": "employee", "plurals": ["employees"], "es": ["empleado", "empleados", "trabajador", "colaborador", "staff"]},
    {"singular": "customer", "plurals": ["customers"], "es": ["cliente", "clientes", "consumidor", "usuario"]},
    {"singular": "order", "plurals": ["orders"], "es": ["pedido", "pedidos", "orden", "ordenes", "compra"]},
    {"singular": "invoice", "plurals": ["invoices"], "es": ["factura", "facturas", "recibo", "comprobante"]},
    {"singular": "product", "plurals": ["products"], "es": ["producto", "productos", "articulo", "articulos", "item"]},
    {"singular": "department", "plurals": ["departments"], "es": ["departamento", "departamentos", "area", "sucursal"]},
    {"singular": "supplier", "plurals": ["suppliers"], "es": ["proveedor", "proveedores", "vendor"]},
    {"singular": "payment", "plurals": ["payments"], "es": ["pago", "pagos", "abono", "cobro"]},
    {"singular": "contract", "plurals": ["contracts"], "es": ["contrato", "contratos", "acuerdo"]},
    {"singular": "position", "plurals": ["positions"], "es": ["cargo", "cargos", "puesto", "puestos"]},
    {"singular": "user", "plurals": ["users"], "es": ["usuario", "usuarios"]},
    {"singular": "role", "plurals": ["roles"], "es": ["rol", "roles", "perfil"]},
    {"singular": "company", "plurals": ["companies"], "es": ["empresa", "empresas", "organizacion"]},
    {"singular": "site", "plurals": ["sites"], "es": ["sede", "sedes", "sitio", "locacion"]},
    {"singular": "warehouse", "plurals": ["warehouses"], "es": ["almacen", "almacenes", "deposito", "bodega"]},
    {"singular": "shipment", "plurals": ["shipments"], "es": ["envio", "envios", "despacho", "entrega"]},
    {"singular": "item", "plurals": ["items"], "es": ["linea", "lineas", "detalle", "item_line"]},
    {"singular": "transaction", "plurals": ["transactions"], "es": ["transaccion", "transacciones", "movimiento"]},
    {"singular": "account", "plurals": ["accounts"], "es": ["cuenta", "cuentas", "ledger"]},
    {"singular": "category", "plurals": ["categories"], "es": ["categoria", "categorias", "rubro", "familia"]},
    {"singular": "invoice_item", "plurals": ["invoice_items"], "es": ["factura_detalle", "factura_linea"]},
    {"singular": "order_item", "plurals": ["order_items"], "es": ["pedido_detalle", "pedido_linea"]},
    {"singular": "address", "plurals": ["addresses"], "es": ["direccion", "domicilio"]},
    {"singular": "contact", "plurals": ["contacts"], "es": ["contacto", "contactos"]},
]

STATUS_COLUMN_PATTERNS = [
    "status", "estado", "state", "estatus",
    "is_active", "active", "activo", "vigente",
    "deleted", "eliminado", "is_deleted", "soft_delete",
    "enabled", "habilitado", "disabled", "deshabilitado",
    "approved", "aprobado", "confirmed", "confirmado",
    "paid", "pagado", "paid_status",
]


def _snake_case_upper(text: str) -> str:
    text = text.strip()
    s1 = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", text)
    s2 = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s1)
    s3 = re.sub(r"[^a-zA-Z0-9]+", "_", s2)
    return s3.strip("_").upper() or "UNKNOWN"


def _singularize(name: str) -> str:
    n = name.lower()
    if n.endswith("ies") and len(n) > 3:
        return n[:-3] + "y"
    if n.endswith("ses") or n.endswith("xes") or n.endswith("zes"):
        return n[:-2]
    if n.endswith("s") and not n.endswith("ss") and len(n) > 3:
        return n[:-1]
    return n


def _pluralize(name: str) -> str:
    n = name.lower()
    if n.endswith("y") and len(n) > 1 and n[-2] not in "aeiou":
        return n[:-1] + "ies"
    if n.endswith("s") or n.endswith("x") or n.endswith("z"):
        return n + "es"
    return n + "s"


class BusinessEntityService:
    def __init__(
        self,
        semantic_repository: Any = None,
        llm_enabled: bool = True,
    ) -> None:
        self.semantic_repository = semantic_repository
        self.llm_enabled = llm_enabled
        logger.info(
            "BusinessEntityService initialized",
            extra={"llm_enabled": llm_enabled, "has_repository": semantic_repository is not None},
        )

    async def detect_entities(
        self,
        database_metadata: DatabaseMetadata,
        domains: list[BusinessDomain],
        table_analyses: list[Any] | None = None,
    ) -> list[BusinessEntity]:
        if not database_metadata:
            raise InvalidInputError(
                detail="database_metadata is required for entity detection",
                field="database_metadata",
            )

        logger.info(
            "Starting business entity detection",
            extra={
                "schema_count": len(database_metadata.schemas),
                "domain_count": len(domains or []),
            },
        )

        all_tables = self._flatten_tables(database_metadata)
        domain_by_table: dict[str, BusinessDomain] = {}
        for d in domains or []:
            for t in d.tables:
                domain_by_table[t] = d

        entity_groups: dict[str, dict[str, Any]] = {}

        try:
            for table in all_tables:
                full_name = f"{table.schema_name}.{table.table_name}"
                table_name = table.table_name
                entity_key = self._find_entity_key(table_name)
                if entity_key is None:
                    entity_key = self._extract_prefix_entity(table_name)

                if entity_key is None:
                    continue

                canonical = self._canonical_name_for_entity(entity_key)
                code = _snake_case_upper(canonical)

                if code not in entity_groups:
                    entity_groups[code] = {
                        "code": code,
                        "canonical": canonical,
                        "name": canonical.title(),
                        "tables": [],
                        "all_columns": [],
                        "domain_codes": set(),
                        "primary_candidates": [],
                        "key_candidates": set(),
                        "status_candidates": [],
                    }

                group = entity_groups[code]
                group["tables"].append((full_name, table))
                for col in table.columns:
                    group["all_columns"].append(col)
                    if col.is_pk:
                        group["key_candidates"].add(col.name)
                        group["primary_candidates"].append((full_name, col.name))
                    if self._column_looks_like_status(col.name):
                        group["status_candidates"].append((full_name, col.name))

                domain = domain_by_table.get(full_name)
                if domain:
                    group["domain_codes"].add(domain.code)

            entities: list[BusinessEntity] = []
            for code, group in entity_groups.items():
                tables_sorted = sorted(
                    group["tables"],
                    key=lambda pair: (
                        -1
                        if self._is_primary_table_candidate(pair[1].table_name, group["canonical"])
                        else 0,
                        -len(pair[1].primary_key or []),
                        pair[0],
                    ),
                )
                table_names = [t[0] for t in tables_sorted]
                primary_table = table_names[0] if table_names else None

                key_columns: list[str] = []
                if tables_sorted:
                    pk_cols = tables_sorted[0][1].primary_key
                    if pk_cols:
                        key_columns = list(pk_cols)
                    else:
                        id_cols = [
                            c.name
                            for c in tables_sorted[0][1].columns
                            if c.is_pk or c.name.lower() in {"id", f"{group['canonical']}_id"}
                        ]
                        key_columns = id_cols[:3]

                status_column: Optional[str] = None
                if group["status_candidates"]:
                    primary_full = primary_table or ""
                    primary_status = [
                        (fn, col) for fn, col in group["status_candidates"] if fn == primary_full
                    ]
                    target = primary_status[0] if primary_status else group["status_candidates"][0]
                    status_column = target[1]

                aliases = self._build_aliases(group["canonical"])
                description = (
                    f"Entidad de negocio {group['name']} compuesta por "
                    f"{len(table_names)} tabla(s): {', '.join(table_names[:3])}"
                    + ("..." if len(table_names) > 3 else "")
                )

                entity = BusinessEntity(
                    name=group["name"],
                    code=group["code"],
                    description=description,
                    aliases=aliases,
                    domain_code=next(iter(group["domain_codes"])) if group["domain_codes"] else None,
                    tables=table_names,
                    primary_table=primary_table,
                    key_columns=list(dict.fromkeys(key_columns)),
                    status_column=status_column,
                    confidence=min(
                        1.0,
                        0.5
                        + 0.1 * min(len(table_names), 4)
                        + (0.1 if status_column else 0.0)
                        + (0.1 if key_columns else 0.0),
                    ),
                    evidence=[
                        f"Tables grouped by prefix/keyword '{group['canonical']}'",
                        f"Primary table: {primary_table}",
                    ],
                )
                entities.append(entity)

            entities.sort(key=lambda e: (-len(e.tables), e.name))

        except InvalidInputError:
            raise
        except Exception as exc:
            logger.error(
                "Entity detection failed",
                exc_info=exc,
                extra={"error_type": type(exc).__name__},
            )
            raise SemanticAnalysisError(
                detail=f"Failed to detect business entities: {exc!s}",
                extra={"stage": "entity_detection"},
            ) from exc

        logger.info(
            "Business entity detection completed",
            extra={"entity_count": len(entities)},
        )
        return entities

    def _flatten_tables(self, metadata: DatabaseMetadata) -> list[TableMetadata]:
        tables: list[TableMetadata] = []
        for schema in metadata.schemas:
            for t in schema.tables or []:
                tables.append(t)
            for v in schema.views or []:
                tables.append(v)
        return tables

    def _find_entity_key(self, table_name: str) -> Optional[str]:
        name = table_name.lower()
        stripped = re.sub(r"[^a-z0-9]+", "_", name).strip("_")
        parts = stripped.split("_")

        for spec in ENTITY_KEYWORDS:
            candidates = [spec["singular"]] + list(spec["plurals"]) + list(spec["es"])
            candidates = [c.lower() for c in candidates]
            for cand in candidates:
                cand_parts = cand.split("_")
                if len(cand_parts) == 1 and cand in parts:
                    return spec["singular"]
                if cand in stripped or stripped.startswith(cand + "_") or stripped.endswith("_" + cand):
                    return spec["singular"]
        return None

    def _extract_prefix_entity(self, table_name: str) -> Optional[str]:
        name = table_name.lower()
        stripped = re.sub(r"[^a-z0-9]+", "_", name).strip("_")
        parts = stripped.split("_")
        if not parts:
            return None
        first = parts[0]
        if len(first) <= 2:
            return None
        if first in {"fact", "dim", "vw", "v", "tbl", "tmp"}:
            if len(parts) > 1:
                first = parts[1]
            else:
                return None
        if len(first) < 3:
            return None
        return _singularize(first)

    def _canonical_name_for_entity(self, key: str) -> str:
        key_lower = key.lower()
        for spec in ENTITY_KEYWORDS:
            candidates = [spec["singular"]] + list(spec["plurals"]) + list(spec["es"])
            if key_lower in {c.lower() for c in candidates}:
                return spec["singular"]
        return _singularize(key_lower)

    def _is_primary_table_candidate(self, table_name: str, canonical: str) -> bool:
        name = table_name.lower()
        stripped = re.sub(r"[^a-z0-9]+", "_", name).strip("_")
        singular = canonical.lower()
        plural = _pluralize(singular)
        if stripped in {singular, plural}:
            return True
        if stripped.startswith(f"{singular}_") or stripped.startswith(f"{plural}_"):
            remainder = stripped[len(singular) + 1 :] or stripped[len(plural) + 1 :]
            if not remainder:
                return True
            secondary = {"profile", "detail", "info", "data", "master", "main"}
            if remainder in secondary:
                return True
        return False

    def _column_looks_like_status(self, column_name: str) -> bool:
        col = column_name.lower()
        return any(p in col for p in STATUS_COLUMN_PATTERNS)

    def _build_aliases(self, canonical: str) -> list[str]:
        canonical_lower = canonical.lower()
        aliases: list[str] = []
        for spec in ENTITY_KEYWORDS:
            if spec["singular"] == canonical_lower:
                aliases.append(spec["singular"].title())
                aliases.extend([w.title() for w in spec["plurals"]])
                aliases.extend([w.title() for w in spec["es"]])
                break
        aliases.append(_pluralize(canonical).title())
        return list(dict.fromkeys([a for a in aliases if a and a.lower() != canonical_lower]))

    async def persist_entities(
        self,
        entities: list[BusinessEntity],
        **kwargs: Any,
    ) -> bool:
        if not self.semantic_repository:
            logger.warning("persist_entities skipped: no semantic_repository configured")
            return False
        try:
            repo = self.semantic_repository
            if hasattr(repo, "persist_entities"):
                await repo.persist_entities(entities=entities, **kwargs)
            else:
                logger.debug(
                    "semantic_repository has no persist_entities method; stored in memory only"
                )
            logger.info(
                "Persisted entities",
                extra={"entity_count": len(entities)},
            )
            return True
        except Exception as exc:
            logger.error(
                "Failed to persist entities",
                exc_info=exc,
                extra={"error_type": type(exc).__name__},
            )
            raise SemanticAnalysisError(
                detail=f"Failed to persist entities: {exc!s}",
                extra={"stage": "entity_persistence"},
            ) from exc
