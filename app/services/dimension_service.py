from __future__ import annotations

import re
from typing import Any, Optional

from app.core.exceptions import InvalidInputError, SemanticAnalysisError
from app.core.logging import get_logger
from app.models.database import DatabaseMetadata, RelationshipMetadata, TableMetadata
from app.models.semantic import (
    BusinessDomain,
    BusinessEntity,
    Dimension,
    DimensionType,
)

logger = get_logger(__name__)


DATE_TYPES = {"DATE", "TIMESTAMP", "TIMESTAMPTZ", "DATETIME", "TIME"}

DIMENSION_PRESETS: list[dict[str, Any]] = [
    {
        "dimension_type": DimensionType.CATEGORICAL,
        "code": "COUNTRY",
        "name": "Country",
        "keywords": ["country", "pais", "país", "nation", "nacion"],
        "aliases_en": ["Country", "Nation", "State (Country)"],
        "aliases_es": ["País", "Nación", "Pais"],
        "description": "País o nación geográfica",
    },
    {
        "dimension_type": DimensionType.CATEGORICAL,
        "code": "REGION",
        "name": "Region",
        "keywords": ["region", "regi", "state", "province", "provincia", "estado", "zone", "zona"],
        "aliases_en": ["Region", "State", "Province", "Zone"],
        "aliases_es": ["Región", "Region", "Provincia", "Estado", "Zona"],
        "description": "Región geográfica, provincia o estado",
    },
    {
        "dimension_type": DimensionType.CATEGORICAL,
        "code": "CITY",
        "name": "City",
        "keywords": ["city", "ciudad", "town", "pueblo", "municipality", "municipio"],
        "aliases_en": ["City", "Town", "Municipality"],
        "aliases_es": ["Ciudad", "Pueblo", "Municipio"],
        "description": "Ciudad o localidad",
    },
    {
        "dimension_type": DimensionType.CATEGORICAL,
        "code": "DEPARTMENT",
        "name": "Department",
        "keywords": ["department", "departamento", "area", "division", "division", "unit", "unidad"],
        "aliases_en": ["Department", "Area", "Division", "Unit"],
        "aliases_es": ["Departamento", "Área", "Area", "División", "Unidad"],
        "description": "Departamento o área organizacional",
    },
    {
        "dimension_type": DimensionType.CATEGORICAL,
        "code": "PRODUCT",
        "name": "Product",
        "keywords": ["product", "producto", "sku", "item", "articulo", "article"],
        "aliases_en": ["Product", "SKU", "Item", "Article"],
        "aliases_es": ["Producto", "Artículo", "SKU", "Item"],
        "description": "Producto o artículo del catálogo",
    },
    {
        "dimension_type": DimensionType.CATEGORICAL,
        "code": "PRODUCT_CATEGORY",
        "name": "Product Category",
        "keywords": ["category", "categoria", "categoría", "familia", "family", "group", "grupo", "rubro"],
        "aliases_en": ["Category", "Product Category", "Family", "Group"],
        "aliases_es": ["Categoría", "Categoria", "Familia", "Grupo", "Rubro"],
        "description": "Categoría o familia de productos",
    },
    {
        "dimension_type": DimensionType.CATEGORICAL,
        "code": "CUSTOMER",
        "name": "Customer",
        "keywords": ["customer", "cliente", "client", "consumer", "consumidor", "account"],
        "aliases_en": ["Customer", "Client", "Consumer", "Account"],
        "aliases_es": ["Cliente", "Consumidor", "Cuenta"],
        "description": "Cliente o consumidor",
    },
    {
        "dimension_type": DimensionType.CATEGORICAL,
        "code": "EMPLOYEE",
        "name": "Employee",
        "keywords": ["employee", "empleado", "trabajador", "colaborador", "staff", "user", "usuario", "seller", "vendedor"],
        "aliases_en": ["Employee", "Staff", "Worker", "Seller", "User"],
        "aliases_es": ["Empleado", "Trabajador", "Colaborador", "Vendedor", "Usuario"],
        "description": "Empleado, vendedor o colaborador",
    },
    {
        "dimension_type": DimensionType.CATEGORICAL,
        "code": "STATUS",
        "name": "Status",
        "keywords": ["status", "estado", "state", "estatus"],
        "aliases_en": ["Status", "State"],
        "aliases_es": ["Estado", "Estatus", "Situación"],
        "description": "Estado o condición de un registro",
    },
    {
        "dimension_type": DimensionType.CATEGORICAL,
        "code": "TYPE",
        "name": "Type",
        "keywords": ["type", "tipo", "kind", "clase", "class", "mode", "modo"],
        "aliases_en": ["Type", "Kind", "Class", "Mode"],
        "aliases_es": ["Tipo", "Clase", "Modalidad", "Modo"],
        "description": "Tipo o clase de entidad",
    },
    {
        "dimension_type": DimensionType.ORGANIZATIONAL,
        "code": "COMPANY",
        "name": "Company",
        "keywords": ["company", "empresa", "organization", "organizacion", "organization", "corporation", "corporacion", "subsidiary", "sucursal"],
        "aliases_en": ["Company", "Organization", "Corporation", "Subsidiary", "Branch"],
        "aliases_es": ["Empresa", "Organización", "Organizacion", "Corporación", "Sucursal"],
        "description": "Empresa, organización o sucursal",
    },
    {
        "dimension_type": DimensionType.ORGANIZATIONAL,
        "code": "MANAGER",
        "name": "Manager",
        "keywords": ["manager", "gerente", "jefe", "boss", "supervisor", "leader", "lider", "director"],
        "aliases_en": ["Manager", "Supervisor", "Leader", "Director", "Boss"],
        "aliases_es": ["Gerente", "Jefe", "Supervisor", "Líder", "Lider", "Director"],
        "description": "Gerente, supervisor o líder (jerarquía organizacional)",
    },
    {
        "dimension_type": DimensionType.ORGANIZATIONAL,
        "code": "SITE",
        "name": "Site / Location",
        "keywords": ["site", "sede", "location", "locacion", "ubicacion", "branch", "oficina", "office", "warehouse", "almacen", "almacén", "deposito"],
        "aliases_en": ["Site", "Location", "Branch", "Office", "Warehouse"],
        "aliases_es": ["Sede", "Ubicación", "Ubicacion", "Sucursal", "Oficina", "Almacén", "Depósito"],
        "description": "Sede, ubicación física, oficina o almacén",
    },
    {
        "dimension_type": DimensionType.GEOGRAPHIC,
        "code": "ADDRESS",
        "name": "Address",
        "keywords": ["address", "direccion", "dirección", "street", "calle", "avenue", "avenida", "location", "ubicacion", "domicilio"],
        "aliases_en": ["Address", "Street", "Location"],
        "aliases_es": ["Dirección", "Domicilio", "Calle", "Ubicación"],
        "description": "Dirección geográfica completa",
    },
    {
        "dimension_type": DimensionType.TEMPORAL,
        "code": "ORDER_DATE",
        "name": "Order Date",
        "keywords": ["order_date", "fecha_pedido", "placed_at", "purchase_date", "fecha_compra"],
        "aliases_en": ["Order Date", "Purchase Date", "Placed At"],
        "aliases_es": ["Fecha de Pedido", "Fecha Pedido", "Fecha de Compra"],
        "description": "Fecha en que se realizó el pedido",
    },
    {
        "dimension_type": DimensionType.TEMPORAL,
        "code": "INVOICE_DATE",
        "name": "Invoice Date",
        "keywords": ["invoice_date", "fecha_factura", "billed_at", "billing_date", "fecha_facturacion", "fecha_emision"],
        "aliases_en": ["Invoice Date", "Billing Date", "Issue Date"],
        "aliases_es": ["Fecha de Factura", "Fecha Factura", "Fecha de Emisión"],
        "description": "Fecha de emisión de la factura",
    },
    {
        "dimension_type": DimensionType.TEMPORAL,
        "code": "PAYMENT_DATE",
        "name": "Payment Date",
        "keywords": ["payment_date", "fecha_pago", "paid_at", "collected_at"],
        "aliases_en": ["Payment Date", "Paid At", "Collection Date"],
        "aliases_es": ["Fecha de Pago", "Fecha Pago", "Fecha Cobro"],
        "description": "Fecha en que se realizó el pago/cobro",
    },
    {
        "dimension_type": DimensionType.TEMPORAL,
        "code": "CREATED_AT",
        "name": "Created Date",
        "keywords": ["created_at", "created_date", "fecha_creacion", "fecha_alta", "inserted_at", "date_created"],
        "aliases_en": ["Created At", "Creation Date", "Created On"],
        "aliases_es": ["Fecha de Creación", "Fecha Creación", "Fecha Alta"],
        "description": "Fecha de creación del registro",
    },
]


def _snake_case_upper(text: str) -> str:
    if not text:
        return "UNKNOWN"
    s0 = text.strip()
    s1 = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", s0)
    s2 = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s1)
    s3 = re.sub(r"[^a-zA-Z0-9]+", "_", s2)
    return s3.strip("_").upper() or "UNKNOWN"


def _split_schema_table(full: str) -> tuple[str, str]:
    if not full:
        return "", ""
    parts = full.split(".", 1)
    if len(parts) == 2:
        return parts[0], parts[1]
    return "", parts[0]


class DimensionService:
    def __init__(
        self,
        semantic_repository: Any = None,
        llm_enabled: bool = True,
    ) -> None:
        self.semantic_repository = semantic_repository
        self.llm_enabled = llm_enabled
        logger.info(
            "DimensionService initialized",
            extra={"llm_enabled": llm_enabled, "has_repository": semantic_repository is not None},
        )

    async def detect_dimensions(
        self,
        metadata: DatabaseMetadata,
        profiles: Any | None = None,
        entities: list[BusinessEntity] | None = None,
        domains: list[BusinessDomain] | None = None,
        relationships: list[RelationshipMetadata] | None = None,
    ) -> list[Dimension]:
        if not metadata:
            raise InvalidInputError(
                detail="metadata is required for dimension detection",
                field="metadata",
            )

        logger.info(
            "Starting dimension detection",
            extra={
                "schema_count": len(metadata.schemas),
                "entity_count": len(entities or []),
                "relationship_count": len(relationships or []),
            },
        )

        all_tables = self._flatten_tables(metadata)
        table_map: dict[str, TableMetadata] = {
            f"{t.schema_name}.{t.table_name}": t for t in all_tables
        }
        fk_relationships = relationships or metadata.relationships or []

        dimensions: list[Dimension] = []
        seen_codes: set[str] = set()

        try:
            for table in all_tables:
                full_name = f"{table.schema_name}.{table.table_name}"
                for col in table.columns:
                    cname = col.name.lower()
                    ctype = (col.type or "").upper()

                    matched: Optional[dict[str, Any]] = None
                    for preset in DIMENSION_PRESETS:
                        if any(kw.lower() in cname for kw in preset["keywords"]):
                            if (
                                preset["dimension_type"] == DimensionType.TEMPORAL
                                and ctype not in DATE_TYPES
                            ):
                                continue
                            matched = preset
                            break

                    if matched is None and ctype in DATE_TYPES:
                        matched = {
                            "dimension_type": DimensionType.TEMPORAL,
                            "code": _snake_case_upper(col.name + "_DATE"),
                            "name": col.name.replace("_", " ").title(),
                            "aliases_en": [col.name.replace("_", " ").title()],
                            "aliases_es": [col.name.replace("_", " ").title()],
                            "description": f"Fecha/tiempo columna {col.name}",
                        }

                    if matched is None:
                        continue

                    aliases = list(
                        dict.fromkeys(
                            list(matched.get("aliases_en", []))
                            + list(matched.get("aliases_es", []))
                        )
                    )
                    if not aliases or aliases[0].lower() == matched["name"].lower():
                        aliases = aliases[1:]

                    requires_joins: list[dict[str, Any]] = self._infer_required_joins(
                        full_name,
                        col.name,
                        fk_relationships,
                        table_map,
                    )

                    dim = Dimension(
                        name=matched["name"],
                        code=matched["code"],
                        description=matched.get("description", ""),
                        aliases=aliases,
                        dimension_type=matched["dimension_type"],
                        source_table=full_name,
                        source_column=col.name,
                        values=None,
                        requires_joins=requires_joins,
                        confidence=0.8 if matched["code"] != _snake_case_upper(col.name + "_DATE") else 0.6,
                    )
                    if dim.code in seen_codes:
                        existing = next(
                            (d for d in dimensions if d.code == dim.code), None
                        )
                        if existing and existing.confidence < dim.confidence:
                            continue
                    else:
                        seen_codes.add(dim.code)
                    dimensions.append(dim)

            dimensions = self._dedupe_dimensions(dimensions)

        except InvalidInputError:
            raise
        except Exception as exc:
            logger.error(
                "Dimension detection failed",
                exc_info=exc,
                extra={"error_type": type(exc).__name__},
            )
            raise SemanticAnalysisError(
                detail=f"Failed to detect dimensions: {exc!s}",
                extra={"stage": "dimension_detection"},
            ) from exc

        logger.info(
            "Dimension detection completed",
            extra={"dimension_count": len(dimensions)},
        )
        return dimensions

    def _flatten_tables(self, metadata: DatabaseMetadata) -> list[TableMetadata]:
        tables: list[TableMetadata] = []
        for schema in metadata.schemas:
            for t in schema.tables or []:
                tables.append(t)
            for v in schema.views or []:
                tables.append(v)
        return tables

    def _infer_required_joins(
        self,
        table_full: str,
        column_name: str,
        relationships: list[RelationshipMetadata],
        table_map: dict[str, TableMetadata],
    ) -> list[dict[str, Any]]:
        joins: list[dict[str, Any]] = []
        tbl = table_map.get(table_full)
        if tbl is None:
            return joins

        col = next((c for c in tbl.columns if c.name == column_name), None)
        if col is None:
            return joins

        if not col.is_fk or not col.fk_target_table:
            return joins

        target_full = col.fk_target_table
        target_table = table_map.get(target_full)
        if target_table is None:
            return joins

        name_col = None
        for tc in target_table.columns:
            tc_name = tc.name.lower()
            if any(kw in tc_name for kw in ["name", "nombre", "description", "descripcion", "title", "titulo", "label", "etiqueta"]):
                name_col = tc.name
                break
        if name_col is None:
            for tc in target_table.columns:
                if not tc.is_pk:
                    name_col = tc.name
                    break
        if name_col is None:
            name_col = col.fk_target_column or "id"

        join: dict[str, Any] = {
            "from_table": table_full,
            "from_column": column_name,
            "to_table": target_full,
            "to_column": col.fk_target_column or "id",
            "display_column": name_col,
            "description": f"Join {table_full}.{column_name} -> {target_full}.{col.fk_target_column or 'id'} to display {name_col}",
        }
        joins.append(join)

        return joins

    def _dedupe_dimensions(self, dims: list[Dimension]) -> list[Dimension]:
        by_code: dict[str, Dimension] = {}
        for d in dims:
            existing = by_code.get(d.code)
            if existing is None:
                by_code[d.code] = d
                continue
            if d.confidence > existing.confidence:
                by_code[d.code] = d
                continue
            if d.confidence == existing.confidence:
                existing.aliases = list(dict.fromkeys(existing.aliases + d.aliases))
                existing.requires_joins = list(
                    {
                        (
                            rj.get("from_table"),
                            rj.get("from_column"),
                            rj.get("to_table"),
                            rj.get("to_column"),
                        ): rj
                        for rj in existing.requires_joins + d.requires_joins
                    }.values()
                )
        out = list(by_code.values())
        out.sort(key=lambda d: (-d.confidence, d.name))
        return out

    async def persist_dimensions(
        self,
        dimensions: list[Dimension],
        **kwargs: Any,
    ) -> bool:
        if not self.semantic_repository:
            logger.warning("persist_dimensions skipped: no semantic_repository configured")
            return False
        try:
            repo = self.semantic_repository
            if hasattr(repo, "persist_dimensions"):
                await repo.persist_dimensions(dimensions=dimensions, **kwargs)
            else:
                logger.debug(
                    "semantic_repository has no persist_dimensions method; stored in memory only"
                )
            logger.info(
                "Persisted dimensions",
                extra={"dimension_count": len(dimensions)},
            )
            return True
        except Exception as exc:
            logger.error(
                "Failed to persist dimensions",
                exc_info=exc,
                extra={"error_type": type(exc).__name__},
            )
            raise SemanticAnalysisError(
                detail=f"Failed to persist dimensions: {exc!s}",
                extra={"stage": "dimension_persistence"},
            ) from exc
