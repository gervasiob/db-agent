from __future__ import annotations

import re
from abc import ABC, abstractmethod
from typing import Any, Optional

from app.core.exceptions import DatabaseConnectionError, DatabaseDiscoveryError
from app.core.logging import get_logger
from app.database.connection import DatabaseConfig
from app.models.database import (
    ColumnHeuristicFlags,
    ColumnMetadata,
    ColumnProfile,
    ColumnType,
    ConstraintMetadata,
    ConstraintType,
    DatabaseMetadata,
    IndexMetadata,
    RelationshipMetadata,
    RelationshipType,
    SchemaMetadata,
    TableMetadata,
    TableType,
)


logger = get_logger(__name__)


class DatabaseAdapter(ABC):
    config: DatabaseConfig

    def __init__(self, config: DatabaseConfig) -> None:
        self.config = config
        self._is_connected: bool = False

    @abstractmethod
    async def test_connection(
        self,
    ) -> tuple[bool, dict[str, Any], Optional[str]]: ...

    @abstractmethod
    async def connect(self) -> None: ...

    @abstractmethod
    async def close(self) -> None: ...

    @abstractmethod
    async def get_schemas(self) -> list[str]: ...

    @abstractmethod
    async def get_tables(self, schema: str) -> list[tuple[str, str]]: ...

    @abstractmethod
    async def get_columns(self, schema: str, table: str) -> list[dict[str, Any]]: ...

    @abstractmethod
    async def get_primary_keys(self, schema: str, table: str) -> list[str]: ...

    @abstractmethod
    async def get_foreign_keys(self, schema: str, table: str) -> list[dict[str, Any]]: ...

    @abstractmethod
    async def get_indexes(self, schema: str, table: str) -> list[dict[str, Any]]: ...

    @abstractmethod
    async def get_constraints(self, schema: str, table: str) -> list[dict[str, Any]]: ...

    @abstractmethod
    async def get_table_comment(self, schema: str, table: str) -> Optional[str]: ...

    @abstractmethod
    async def get_estimated_row_count(self, schema: str, table: str) -> int: ...

    @abstractmethod
    async def execute_raw(
        self,
        query: str,
        params: Optional[dict[str, Any]] = None,
        timeout: Optional[int] = None,
    ) -> tuple[list[str], list[dict[str, Any]]]: ...

    @abstractmethod
    async def execute_readonly_sql(
        self,
        sql: str,
        timeout: Optional[int] = None,
        max_rows: int = 1000,
    ) -> tuple[list[str], list[dict[str, Any]], bool]: ...

    @abstractmethod
    async def sample_rows(
        self,
        schema: str,
        table: str,
        order_by_col: Optional[str] = None,
        direction: str = "DESC",
        limit: int = 50,
        exclude_cols: Optional[list[str]] = None,
    ) -> tuple[list[str], list[dict[str, Any]]]: ...

    @abstractmethod
    async def profile_column(
        self,
        schema: str,
        table: str,
        column: str,
        approx_mode: bool = True,
    ) -> ColumnProfile: ...

    @abstractmethod
    def map_column_type(self, raw_type_str: str) -> ColumnType: ...

    @classmethod
    def by_database_type(cls, database_type: str) -> type["DatabaseAdapter"]:
        from app.database.adapters.postgresql import PostgreSQLAdapter

        registry: dict[str, type[DatabaseAdapter]] = {
            "postgresql": PostgreSQLAdapter,
            "sqlserver": SQLServerAdapter,
            "mysql": MySQLAdapter,
        }
        adapter_cls = registry.get(database_type.lower())
        if adapter_cls is None:
            raise DatabaseDiscoveryError(
                detail=f"Unsupported database type: {database_type}",
                extra={"database_type": database_type, "supported": sorted(registry.keys())},
            )
        return adapter_cls

    def column_heuristics(
        self,
        columns_info: list[dict[str, Any]],
    ) -> dict[str, ColumnHeuristicFlags]:
        result: dict[str, ColumnHeuristicFlags] = {}
        id_patterns = re.compile(r"^(id|uuid|.*_id|.*_uuid|.*_pk)$", re.IGNORECASE)
        name_patterns = re.compile(r"^(name|nombre|title|titulo|label|etiqueta|display_name|full_name|nombre_completo)$", re.IGNORECASE)
        desc_patterns = re.compile(r"^(description|descripcion|desc|detalle|detail|notes|notas|comment|comentario|content|contenido|body|cuerpo)$", re.IGNORECASE)
        status_patterns = re.compile(r"^(status|estado|state|estado_.*|status_.*|is_.*|has_.*|enabled|disabled|active|inactive|activo|inactivo)$", re.IGNORECASE)
        category_patterns = re.compile(r"^(category|categoria|type|tipo|class|clase|kind|group|grupo)$", re.IGNORECASE)
        date_patterns = re.compile(r"^(date|fecha|day|dia|month|mes|year|anio|year|period|periodo)$", re.IGNORECASE)
        ts_patterns = re.compile(r"^(timestamp|datetime|fecha_hora|fecha_hora_.*|time.*)$", re.IGNORECASE)
        created_at_patterns = re.compile(r"^(created_at|created_at_.*|created|fecha_creacion|fecha_creado|date_created|inserted_at|created_date)$", re.IGNORECASE)
        updated_at_patterns = re.compile(r"^(updated_at|updated_at_.*|updated|fecha_actualizacion|fecha_modificado|date_updated|modified_at|last_modified|modified_date)$", re.IGNORECASE)
        audit_patterns = re.compile(r"^(created_by|modified_by|updated_by|deleted_by|usuario_.*|user_.*|creado_por|modificado_por|actualizado_por|ip_address|ip.*)$", re.IGNORECASE)
        soft_del_patterns = re.compile(r"^(deleted_at|deleted|is_deleted|is_removed|fecha_eliminacion|eliminado|removed_at)$", re.IGNORECASE)
        monetary_patterns = re.compile(r"^(amount|monto|price|precio|cost|costo|total|subtotal|tax|impuesto|iva|discount|descuento|fee|tarifa|rate|tasa|saldo|balance|revenue|ingreso|profit|ganancia|value|valor)$", re.IGNORECASE)
        email_patterns = re.compile(r"^(email|e_mail|correo|correo_electronico|mail)$", re.IGNORECASE)
        phone_patterns = re.compile(r"^(phone|telefono|tel|mobile|celular|cell|whatsapp|contact_number)$", re.IGNORECASE)
        address_patterns = re.compile(r"^(address|direccion|street|calle|city|ciudad|state|provincia|country|pais|zip|postal|codigo_postal|cp|location|ubicacion|latitude|longitude|lat|lng)$", re.IGNORECASE)

        for col_info in columns_info:
            col_name = str(col_info.get("name", "")).lower()
            raw_type = str(col_info.get("raw_type", "")).lower()
            col_type = col_info.get("type")

            is_id = False
            is_name = False
            is_description = False
            is_status = False
            is_category = False
            is_date = False
            is_timestamp = False
            is_monetary = False
            is_created_at = False
            is_updated_at = False
            is_audit = False
            is_soft_delete = False
            is_email = False
            is_phone = False
            is_address = False

            if id_patterns.match(col_name) or col_name.endswith("_id") or col_name.endswith("_uuid"):
                is_id = True
            if name_patterns.match(col_name):
                is_name = True
            if desc_patterns.match(col_name):
                is_description = True
            if status_patterns.match(col_name) or col_type in {ColumnType.BOOLEAN}:
                is_status = True
            if category_patterns.match(col_name):
                is_category = True
            if created_at_patterns.match(col_name):
                is_created_at = True
                is_timestamp = True
            if updated_at_patterns.match(col_name):
                is_updated_at = True
                is_timestamp = True
            if audit_patterns.match(col_name):
                is_audit = True
            if soft_del_patterns.match(col_name):
                is_soft_delete = True
            if monetary_patterns.match(col_name) and col_type in {ColumnType.NUMERIC, ColumnType.FLOAT, ColumnType.INTEGER, ColumnType.BIGINT}:
                is_monetary = True
            if date_patterns.match(col_name) or col_type in {ColumnType.DATE}:
                is_date = True
            if ts_patterns.match(col_name) or col_type in {ColumnType.TIMESTAMP, ColumnType.TIMESTAMPTZ}:
                is_timestamp = True
            if email_patterns.match(col_name):
                is_email = True
            if phone_patterns.match(col_name):
                is_phone = True
            if address_patterns.match(col_name):
                is_address = True

            if "int" in raw_type and col_name in {"id"}:
                is_id = True
            if "bool" in raw_type and (col_name.startswith("is_") or col_name.startswith("has_")):
                is_status = True

            result[col_info.get("name", "")] = ColumnHeuristicFlags(
                is_id=is_id,
                is_name=is_name,
                is_description=is_description,
                is_status=is_status,
                is_category=is_category,
                is_date=is_date,
                is_timestamp=is_timestamp,
                is_monetary=is_monetary,
                is_created_at=is_created_at,
                is_updated_at=is_updated_at,
                is_audit=is_audit,
                is_soft_delete=is_soft_delete,
                is_email=is_email,
                is_phone=is_phone,
                is_address=is_address,
            )
        return result

    async def extract_schema(self) -> DatabaseMetadata:
        try:
            schemas_list = await self.get_schemas()
            schema_metadatas: list[SchemaMetadata] = []
            all_relationships: list[RelationshipMetadata] = []
            total_rows = 0

            for schema_name in schemas_list:
                tables_meta: list[TableMetadata] = []
                views_meta: list[TableMetadata] = []
                table_items = await self.get_tables(schema_name)

                for table_name, table_type_raw in table_items:
                    cols_raw = await self.get_columns(schema_name, table_name)
                    heuristics_map = self.column_heuristics(cols_raw)
                    pks = await self.get_primary_keys(schema_name, table_name)
                    fks_raw = await self.get_foreign_keys(schema_name, table_name)
                    idx_raw = await self.get_indexes(schema_name, table_name)
                    cons_raw = await self.get_constraints(schema_name, table_name)
                    comment = await self.get_table_comment(schema_name, table_name)
                    row_est = await self.get_estimated_row_count(schema_name, table_name)
                    total_rows += max(0, row_est)

                    columns: list[ColumnMetadata] = []
                    for col_raw in cols_raw:
                        col_name = col_raw["name"]
                        heur_flags = heuristics_map.get(col_name, ColumnHeuristicFlags())
                        fk_target = None
                        fk_target_col = None
                        for fk in fks_raw:
                            if col_name in fk.get("constrained_columns", []):
                                fk_target = f"{fk.get('referred_schema', '')}.{fk.get('referred_table', '')}"
                                cols = fk.get("referred_columns", [])
                                idx_in = list(fk.get("constrained_columns", [])).index(col_name)
                                if idx_in < len(cols):
                                    fk_target_col = cols[idx_in]
                                break
                        columns.append(
                            ColumnMetadata(
                                name=col_name,
                                type=self.map_column_type(col_raw.get("raw_type", "")),
                                raw_type=col_raw.get("raw_type", ""),
                                nullable=bool(col_raw.get("nullable", True)),
                                default_value=col_raw.get("default_value"),
                                character_maximum_length=col_raw.get("character_maximum_length"),
                                numeric_precision=col_raw.get("numeric_precision"),
                                numeric_scale=col_raw.get("numeric_scale"),
                                ordinal_position=int(col_raw.get("ordinal_position") or 0),
                                comment=col_raw.get("comment"),
                                is_pk=col_name in pks,
                                is_fk=fk_target is not None,
                                fk_target_table=fk_target,
                                fk_target_column=fk_target_col,
                                enum_values=col_raw.get("enum_values"),
                                heuristics=heur_flags,
                            )
                        )

                    indexes: list[IndexMetadata] = []
                    for idx in idx_raw:
                        indexes.append(
                            IndexMetadata(
                                name=idx.get("name", ""),
                                columns=list(idx.get("column_names", [])),
                                is_unique=bool(idx.get("unique")),
                                is_primary=bool(idx.get("is_primary")),
                            )
                        )

                    constraints: list[ConstraintMetadata] = []
                    for c in cons_raw:
                        c_type_raw = c.get("type", "")
                        try:
                            c_type = ConstraintType(c_type_raw)
                        except Exception:
                            c_type = ConstraintType.CHECK
                        constraints.append(
                            ConstraintMetadata(
                                name=c.get("name", ""),
                                type=c_type,
                                columns=list(c.get("columns", [])),
                                definition=c.get("definition"),
                            )
                        )

                    table_meta = TableMetadata(
                        schema_name=schema_name,
                        table_name=table_name,
                        table_type=TableType(table_type_raw),
                        row_count_estimate=max(0, row_est),
                        comment=comment,
                        columns=columns,
                        primary_key=list(pks),
                        indexes=indexes,
                        constraints=constraints,
                        sample_rows=None,
                        column_profiles=None,
                    )
                    if table_type_raw == "TABLE":
                        tables_meta.append(table_meta)
                    else:
                        views_meta.append(table_meta)

                    for fk in fks_raw:
                        src_cols = list(fk.get("constrained_columns", []))
                        tgt_cols = list(fk.get("referred_columns", []))
                        if not src_cols or not tgt_cols:
                            continue
                        rel = RelationshipMetadata(
                            source_schema=schema_name,
                            source_table=table_name,
                            source_columns=src_cols,
                            target_schema=fk.get("referred_schema", ""),
                            target_table=fk.get("referred_table", ""),
                            target_columns=tgt_cols,
                            relationship_type=RelationshipType.MANY_TO_ONE,
                            inferred=False,
                            confidence=1.0,
                            evidence=None,
                        )
                        all_relationships.append(rel)

                schema_metadatas.append(
                    SchemaMetadata(
                        schema_name=schema_name,
                        tables=tables_meta,
                        views=views_meta,
                    )
                )

            return DatabaseMetadata(
                database_type=self.config.database_type,
                database_name=self.config.database_name,
                schemas=schema_metadatas,
                relationships=all_relationships,
                estimated_total_rows=total_rows,
            )
        except Exception as exc:
            logger.error(
                "extract_schema failed",
                extra={"db_type": self.config.database_type, "host": self.config.host},
                exc_info=exc,
            )
            raise DatabaseDiscoveryError(
                detail=f"Schema extraction failed: {exc!s}",
                extra={"database": self.config.database_name, "host": self.config.host},
            ) from exc


class SQLServerAdapter(DatabaseAdapter):
    async def test_connection(
        self,
    ) -> tuple[bool, dict[str, Any], Optional[str]]:
        raise NotImplementedError("SQLServer adapter not yet implemented")

    async def connect(self) -> None:
        raise NotImplementedError("SQLServer adapter not yet implemented")

    async def close(self) -> None:
        raise NotImplementedError("SQLServer adapter not yet implemented")

    async def get_schemas(self) -> list[str]:
        raise NotImplementedError("SQLServer adapter not yet implemented")

    async def get_tables(self, schema: str) -> list[tuple[str, str]]:
        raise NotImplementedError("SQLServer adapter not yet implemented")

    async def get_columns(self, schema: str, table: str) -> list[dict[str, Any]]:
        raise NotImplementedError("SQLServer adapter not yet implemented")

    async def get_primary_keys(self, schema: str, table: str) -> list[str]:
        raise NotImplementedError("SQLServer adapter not yet implemented")

    async def get_foreign_keys(self, schema: str, table: str) -> list[dict[str, Any]]:
        raise NotImplementedError("SQLServer adapter not yet implemented")

    async def get_indexes(self, schema: str, table: str) -> list[dict[str, Any]]:
        raise NotImplementedError("SQLServer adapter not yet implemented")

    async def get_constraints(self, schema: str, table: str) -> list[dict[str, Any]]:
        raise NotImplementedError("SQLServer adapter not yet implemented")

    async def get_table_comment(self, schema: str, table: str) -> Optional[str]:
        raise NotImplementedError("SQLServer adapter not yet implemented")

    async def get_estimated_row_count(self, schema: str, table: str) -> int:
        raise NotImplementedError("SQLServer adapter not yet implemented")

    async def execute_raw(
        self,
        query: str,
        params: Optional[dict[str, Any]] = None,
        timeout: Optional[int] = None,
    ) -> tuple[list[str], list[dict[str, Any]]]:
        raise NotImplementedError("SQLServer adapter not yet implemented")

    async def execute_readonly_sql(
        self,
        sql: str,
        timeout: Optional[int] = None,
        max_rows: int = 1000,
    ) -> tuple[list[str], list[dict[str, Any]], bool]:
        raise NotImplementedError("SQLServer adapter not yet implemented")

    async def sample_rows(
        self,
        schema: str,
        table: str,
        order_by_col: Optional[str] = None,
        direction: str = "DESC",
        limit: int = 50,
        exclude_cols: Optional[list[str]] = None,
    ) -> tuple[list[str], list[dict[str, Any]]]:
        raise NotImplementedError("SQLServer adapter not yet implemented")

    async def profile_column(
        self,
        schema: str,
        table: str,
        column: str,
        approx_mode: bool = True,
    ) -> ColumnProfile:
        raise NotImplementedError("SQLServer adapter not yet implemented")

    def map_column_type(self, raw_type_str: str) -> ColumnType:
        raise NotImplementedError("SQLServer adapter not yet implemented")


class MySQLAdapter(DatabaseAdapter):
    async def test_connection(
        self,
    ) -> tuple[bool, dict[str, Any], Optional[str]]:
        raise NotImplementedError("MySQL adapter not yet implemented")

    async def connect(self) -> None:
        raise NotImplementedError("MySQL adapter not yet implemented")

    async def close(self) -> None:
        raise NotImplementedError("MySQL adapter not yet implemented")

    async def get_schemas(self) -> list[str]:
        raise NotImplementedError("MySQL adapter not yet implemented")

    async def get_tables(self, schema: str) -> list[tuple[str, str]]:
        raise NotImplementedError("MySQL adapter not yet implemented")

    async def get_columns(self, schema: str, table: str) -> list[dict[str, Any]]:
        raise NotImplementedError("MySQL adapter not yet implemented")

    async def get_primary_keys(self, schema: str, table: str) -> list[str]:
        raise NotImplementedError("MySQL adapter not yet implemented")

    async def get_foreign_keys(self, schema: str, table: str) -> list[dict[str, Any]]:
        raise NotImplementedError("MySQL adapter not yet implemented")

    async def get_indexes(self, schema: str, table: str) -> list[dict[str, Any]]:
        raise NotImplementedError("MySQL adapter not yet implemented")

    async def get_constraints(self, schema: str, table: str) -> list[dict[str, Any]]:
        raise NotImplementedError("MySQL adapter not yet implemented")

    async def get_table_comment(self, schema: str, table: str) -> Optional[str]:
        raise NotImplementedError("MySQL adapter not yet implemented")

    async def get_estimated_row_count(self, schema: str, table: str) -> int:
        raise NotImplementedError("MySQL adapter not yet implemented")

    async def execute_raw(
        self,
        query: str,
        params: Optional[dict[str, Any]] = None,
        timeout: Optional[int] = None,
    ) -> tuple[list[str], list[dict[str, Any]]]:
        raise NotImplementedError("MySQL adapter not yet implemented")

    async def execute_readonly_sql(
        self,
        sql: str,
        timeout: Optional[int] = None,
        max_rows: int = 1000,
    ) -> tuple[list[str], list[dict[str, Any]], bool]:
        raise NotImplementedError("MySQL adapter not yet implemented")

    async def sample_rows(
        self,
        schema: str,
        table: str,
        order_by_col: Optional[str] = None,
        direction: str = "DESC",
        limit: int = 50,
        exclude_cols: Optional[list[str]] = None,
    ) -> tuple[list[str], list[dict[str, Any]]]:
        raise NotImplementedError("MySQL adapter not yet implemented")

    async def profile_column(
        self,
        schema: str,
        table: str,
        column: str,
        approx_mode: bool = True,
    ) -> ColumnProfile:
        raise NotImplementedError("MySQL adapter not yet implemented")

    def map_column_type(self, raw_type_str: str) -> ColumnType:
        raise NotImplementedError("MySQL adapter not yet implemented")
