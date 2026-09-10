from __future__ import annotations

import hashlib
from typing import Any, Optional

import sqlglot
from sqlglot import exp, parse

from app.core.exceptions import SQLValidationError
from app.core.logging import get_logger
from app.models.database import TableMetadata
from app.models.query import ValidatedSQLQuery

logger = get_logger(__name__)

_DANGEROUS_FUNCTION_PATTERNS: tuple[str, ...] = (
    "pg_",
    "set_config",
    "current_setting",
    "pg_sleep",
    "pg_sleep_for",
    "pg_sleep_until",
    "nextval",
    "currval",
    "setval",
    "lastval",
    "lo_",
    "pg_terminate_backend",
    "pg_cancel_backend",
    "pg_reload_conf",
    "pg_rotate_logfile",
    "pg_start_backup",
    "pg_stop_backup",
    "pg_create_restore_point",
    "pg_switch_wal",
    "pg_checkpoint",
    "create_",
    "drop_",
    "alter_",
    "grant_",
    "revoke_",
    "truncate_",
    "vacuum",
    "analyze",
    "reindex",
    "cluster",
)

_BLOCKED_SCHEMAS: frozenset[str] = frozenset({"pg_internal", "pg_toast_temp_1", "pg_toast_temp_2"})

_DANGEROUS_COMMENT_KEYWORDS: tuple[str, ...] = (
    "drop", "alter", "create", "truncate", "insert", "update", "delete",
    "grant", "revoke", "copy", "vacuum", "execute", "call", "set",
)


def _strip_comments(sql: str) -> str:
    import re
    out_lines: list[str] = []
    for line in sql.splitlines(keepends=True):
        out_lines.append(re.sub(r"--.*$", "", line, flags=re.MULTILINE))
    no_single = "".join(out_lines)
    no_block = re.sub(r"/\*.*?\*/", "", no_single, flags=re.DOTALL)
    return no_block


def _contains_dangerous_comment(sql: str) -> Optional[str]:
    import re
    lower_sql = sql.lower()
    for match in re.findall(r"--.*?$|/\*.*?\*/", lower_sql, flags=re.MULTILINE | re.DOTALL):
        for kw in _DANGEROUS_COMMENT_KEYWORDS:
            if re.search(rf"\b{kw}\b", match):
                return kw
    return None


_DIALECT_ALIASES: dict[str, str] = {
    "postgres": "postgresql",
    "pg": "postgresql",
    "postgresql": "postgresql",
    "sqlserver": "sqlserver",
    "mssql": "sqlserver",
    "tsql": "sqlserver",
    "mysql": "mysql",
}


def _normalize_dialect(dialect: str) -> str:
    d = dialect.lower()
    if d in _DIALECT_ALIASES:
        return _DIALECT_ALIASES[d]
    return "postgresql"

def _maybe_exp(*names: str) -> tuple[type[exp.Expression], ...]:
    result: list[type[exp.Expression]] = []
    for n in names:
        t = getattr(exp, n, None)
        if t is not None:
            result.append(t)
    return tuple(result)


_DML_NODE_TYPES: tuple[type[exp.Expression], ...] = _maybe_exp(
    "Insert", "Update", "Delete", "Upsert", "Merge", "Load",
)

_DDL_NODE_TYPES: tuple[type[exp.Expression], ...] = _maybe_exp(
    "Create", "Drop", "Alter", "Truncate", "RenameTable", "RenameColumn",
)

_CONTROL_NODE_TYPES: tuple[type[exp.Expression], ...] = _maybe_exp(
    "Grant", "Revoke", "Command", "Execute", "Call", "Copy", "Vacuum",
    "Set", "Do", "Procedure", "Lock",
)


class SqlValidator:
    def __init__(self) -> None:
        self._dangerous_function_patterns = _DANGEROUS_FUNCTION_PATTERNS
        self._blocked_schemas = _BLOCKED_SCHEMAS

    def validate_read_only(
        self,
        sql: str,
        dialect: str = "postgresql",
        allowed_schemas: Optional[list[str]] = None,
        allowed_tables: Optional[list[str]] = None,
    ) -> ValidatedSQLQuery:
        if not sql or not sql.strip():
            raise SQLValidationError(
                reason="SQL query is empty",
                raw_sql=sql,
            )

        bad_kw = _contains_dangerous_comment(sql)
        if bad_kw is not None:
            raise SQLValidationError(
                reason=f"Comment contains forbidden keyword '{bad_kw}'",
                raw_sql=sql,
                extra={"forbidden_in_comment": bad_kw},
            )

        cleaned_sql = _strip_comments(sql)
        stripped_sql = cleaned_sql.strip().rstrip(";")
        if ";" in stripped_sql:
            raise SQLValidationError(
                reason="Multiple statements separated by semicolons are not allowed",
                raw_sql=sql,
            )

        target_dialect = _normalize_dialect(dialect)
        if target_dialect == "postgresql":
            sqlglot_dialect = "postgres"
        elif target_dialect == "sqlserver":
            sqlglot_dialect = "tsql"
        else:
            sqlglot_dialect = target_dialect

        parsed_statements = self._parse_statements(cleaned_sql, sqlglot_dialect)
        if len(parsed_statements) != 1:
            raise SQLValidationError(
                reason=f"Expected exactly 1 statement, found {len(parsed_statements)}",
                raw_sql=sql,
                extra={"statement_count": len(parsed_statements)},
            )

        statement = parsed_statements[0]
        self._validate_statement_type(statement, sql)
        self._validate_no_side_effects(statement, sql)
        tables_refs = self._collect_referenced_tables(statement)
        columns_refs = self._collect_referenced_columns(statement)
        cte_aliases = self._collect_cte_aliases(statement)
        self._validate_allowed_tables(tables_refs, allowed_tables, sql, cte_aliases=cte_aliases)
        self._validate_allowed_schemas(tables_refs, allowed_schemas, sql)
        self._validate_no_blocked_schemas(tables_refs, sql)
        validated_sql = statement.sql(dialect=sqlglot_dialect)
        ast_signature = self._compute_ast_signature(statement)
        return ValidatedSQLQuery(
            validated_sql=validated_sql,
            dialect=target_dialect,  # type: ignore[arg-type]
            tables=[self._table_qualified_name(t) for t in tables_refs],
            columns=[c.sql() for c in columns_refs],
            ast_signature=ast_signature,
            is_readonly=True,
        )

    def validate_columns_exist(
        self,
        validated: ValidatedSQLQuery,
        tables_metadata: list[TableMetadata],
    ) -> None:
        valid_columns_raw: set[str] = set()
        valid_table_names: set[str] = set()
        for tm in tables_metadata:
            table_qualified = f"{tm.schema_name}.{tm.table_name}"
            valid_table_names.add(tm.table_name.lower())
            valid_table_names.add(table_qualified.lower())
            for col in tm.columns:
                c_raw = col.name.strip() if isinstance(col.name, str) else col.name
                valid_columns_raw.add(str(c_raw).lower())

        def _normalize(x: Any) -> str:
            s = str(x) if not isinstance(x, str) else x
            s = s.strip()
            s = s.strip("`").strip('"').strip("'")
            return s.lower()

        try:
            parsed = next(
                iter(
                    self._parse_statements(
                        validated.validated_sql,
                        "postgres" if validated.dialect == "postgresql" else ("tsql" if validated.dialect == "sqlserver" else (validated.dialect or "postgres")),
                    )
                ),
                None,
            )
        except Exception:
            parsed = None
        aggregate_alias_names: set[str] = set()
        output_alias_names: set[str] = set()
        if parsed is not None:
            try:
                if isinstance(parsed, exp.Select):
                    exprs = list(parsed.expressions or [])
                    for ex in exprs:
                        alias_node = getattr(ex, "alias", None)
                        alias_str = None
                        if alias_node is not None:
                            if isinstance(alias_node, exp.Identifier):
                                alias_str = alias_node.this
                            else:
                                alias_str = str(alias_node)
                        if alias_str:
                            if isinstance(ex, (exp.AggFunc, exp.If, exp.Case, exp.Anonymous, exp.Paren)):
                                aggregate_alias_names.add(_normalize(alias_str))
                            output_alias_names.add(_normalize(alias_str))
            except Exception:
                pass

        referenced_columns = set(validated.columns)
        unknown_columns: list[str] = []
        SAFE_SYSTEM_SCHEMAS_LOWER = {"information_schema", "pg_catalog", "pg_toast", "sys"}
        SAFE_SYSTEM_PREFIX_LOWER = {"pg_", "gp_", "spt_", "MSsnapshot", "MSmerge", "sysx", "dtproperties"}

        def _is_system_table_ref(col_str: str) -> bool:
            raw = str(col_str).strip().strip("`\"'").lower()
            if not raw:
                return False
            if "." in raw:
                head = raw.split(".", 1)[0]
                if head in SAFE_SYSTEM_SCHEMAS_LOWER:
                    return True
            for pref in SAFE_SYSTEM_PREFIX_LOWER:
                if raw.startswith(pref) or (
                    "." in raw and raw.split(".")[-2].startswith(pref)
                ):
                    return True
            return False

        system_table_refs: set[str] = set()
        for t in getattr(validated, "tables", []) or []:
            tnorm = str(t).strip().strip("`\"'").lower()
            if not tnorm:
                continue
            sys = False
            if "." in tnorm and tnorm.split(".", 1)[0] in SAFE_SYSTEM_SCHEMAS_LOWER:
                sys = True
            for pref in SAFE_SYSTEM_PREFIX_LOWER:
                if tnorm.startswith(pref) or (
                    "." in tnorm and tnorm.rsplit(".", 1)[0].endswith("." + pref[:-1] + pref[-1])
                ):
                    sys = True
                if "." in tnorm and tnorm.split(".")[-1].startswith(pref):
                    sys = True
            if sys:
                system_table_refs.add(tnorm)
                bare = tnorm.rsplit(".", 1)[-1]
                if bare:
                    system_table_refs.add(bare)
                alias = (validated.table_aliases or {}).get(tnorm) or None
                alias2 = (validated.table_aliases or {}).get(bare) or None
                if alias:
                    system_table_refs.add(str(alias).strip().lower())
                if alias2:
                    system_table_refs.add(str(alias2).strip().lower())

        try:
            parsed_q = next(
                iter(
                    self._parse_statements(
                        validated.validated_sql,
                        "postgres" if validated.dialect == "postgresql" else ("tsql" if validated.dialect == "sqlserver" else (validated.dialect or "postgres")),
                    )
                ),
                None,
            )
        except Exception:
            parsed_q = None
        if parsed_q is not None:
            try:
                for tnode in self._collect_referenced_tables(parsed_q):
                    schema_part = getattr(tnode, "db", None) or getattr(tnode, "catalog", None)
                    tname = getattr(tnode, "name", None) or ""
                    alias_node = getattr(tnode, "alias", None)
                    alias_str: Optional[str] = None
                    if alias_node is not None:
                        if isinstance(alias_node, exp.Identifier):
                            alias_str = alias_node.this
                        else:
                            alias_str = str(alias_node)
                    schema_str = str(schema_part).strip().lower() if schema_part is not None else ""
                    tname_str = str(tname).strip().lower()
                    is_sys = schema_str in SAFE_SYSTEM_SCHEMAS_LOWER or any(
                        tname_str.startswith(p) for p in SAFE_SYSTEM_PREFIX_LOWER
                    )
                    if is_sys:
                        if schema_str and tname_str: system_table_refs.add(f"{schema_str}.{tname_str}")
                        if tname_str: system_table_refs.add(tname_str)
                        if alias_str: system_table_refs.add(str(alias_str).strip().lower())
            except Exception:
                pass

        def _col_belongs_to_system_table(col_str: str) -> bool:
            raw = str(col_str).strip().strip("`\"'")
            if "." not in raw:
                return False
            parts = raw.split(".")
            prefix = ".".join(parts[:-1]).lower()
            if prefix in system_table_refs:
                return True
            # CTE / alias not tracked: allow any column of system schemas
            head = parts[0].lower()
            if head in SAFE_SYSTEM_SCHEMAS_LOWER:
                return True
            return False

        for col in referenced_columns:
            if col in (validated.columns or []):
                pass
            col_raw = str(col)
            col_norm = _normalize(col_raw)
            if col_norm in valid_columns_raw:
                continue
            if _is_system_table_ref(col_raw) or _col_belongs_to_system_table(col_raw):
                continue
            if "." in col_raw:
                dot_idx = col_raw.rfind(".")
                table_part = col_raw[:dot_idx]
                col_part = col_raw[dot_idx + 1 :]
                table_norm = _normalize(table_part)
                col_part_norm = _normalize(col_part)
                table_only = table_norm
                if table_only in valid_table_names and col_part_norm in valid_columns_raw:
                    continue
                if col_part_norm in valid_columns_raw:
                    continue
                if col_part_norm in aggregate_alias_names:
                    continue
            if col_norm in aggregate_alias_names:
                continue
            if col_norm in output_alias_names:
                continue
            if col_norm in valid_columns_raw:
                continue
            unknown_columns.append(col)

        if unknown_columns:
            raise SQLValidationError(
                reason=f"Referenced columns do not exist in allowed tables: {sorted(unknown_columns)[:10]}",
                raw_sql=validated.validated_sql,
                extra={
                    "unknown_columns": sorted(unknown_columns),
                    "allowed_tables": [t.table_name for t in tables_metadata],
                },
            )

    def _parse_statements(self, sql: str, dialect: str) -> list[exp.Expression]:
        try:
            result = parse(sql, read=dialect)
        except Exception as exc:
            raise SQLValidationError(
                reason=f"Failed to parse SQL: {exc!s}",
                raw_sql=sql,
                extra={"dialect": dialect},
            ) from exc
        return [stmt for stmt in result if stmt is not None]

    def _validate_statement_type(self, statement: exp.Expression, raw_sql: str) -> None:
        if isinstance(statement, exp.Select):
            self._walk_for_forbidden_nodes(statement, raw_sql)
            return

        if isinstance(statement, (exp.Union, exp.Intersect, exp.Except)):
            for node in statement.find_all(exp.Select):
                self._walk_for_forbidden_nodes(node, raw_sql)
            return

        if isinstance(statement, exp.With):
            for cte in statement.expressions:
                if isinstance(cte, exp.CTE):
                    cte_body = cte.this
                    if isinstance(cte_body, exp.Select):
                        self._walk_for_forbidden_nodes(cte_body, raw_sql)
                    else:
                        raise SQLValidationError(
                            reason=f"CTE contains non-SELECT statement: {type(cte_body).__name__}",
                            raw_sql=raw_sql,
                            extra={"cte_type": type(cte_body).__name__},
                        )
            final_query = statement.this
            if isinstance(final_query, exp.Select):
                self._walk_for_forbidden_nodes(final_query, raw_sql)
            else:
                raise SQLValidationError(
                    reason=f"WITH clause wraps non-SELECT statement: {type(final_query).__name__}",
                    raw_sql=raw_sql,
                    extra={"wrapped_type": type(final_query).__name__},
                )
            return

        raise SQLValidationError(
            reason=f"Disallowed statement type: {type(statement).__name__}. Only SELECT statements are permitted.",
            raw_sql=raw_sql,
            extra={"statement_type": type(statement).__name__},
        )

    def _walk_for_forbidden_nodes(self, select_node: exp.Select, raw_sql: str) -> None:
        dml_types = _DML_NODE_TYPES
        ddl_types = _DDL_NODE_TYPES
        ctrl_types = _CONTROL_NODE_TYPES
        for node in select_node.walk():
            if isinstance(node, dml_types):
                raise SQLValidationError(
                    reason=f"DML operation {type(node).__name__} detected. Only READ-ONLY SELECT allowed.",
                    raw_sql=raw_sql,
                    extra={"node_type": type(node).__name__},
                )
            if isinstance(node, ddl_types):
                raise SQLValidationError(
                    reason=f"DDL operation {type(node).__name__} detected. Schema modification is forbidden.",
                    raw_sql=raw_sql,
                    extra={"node_type": type(node).__name__},
                )
            if isinstance(node, ctrl_types):
                raise SQLValidationError(
                    reason=f"Control operation {type(node).__name__} detected. Not permitted in read-only mode.",
                    raw_sql=raw_sql,
                    extra={"node_type": type(node).__name__},
                )

    def _validate_no_side_effects(self, statement: exp.Expression, raw_sql: str) -> None:
        for node in statement.walk():
            if isinstance(node, exp.Anonymous):
                func_name = (node.this or "") if isinstance(node.this, str) else str(node.this)
                func_name_lower = func_name.lower()
                for pattern in self._dangerous_function_patterns:
                    if pattern in func_name_lower:
                        raise SQLValidationError(
                            reason=f"Potentially dangerous function call detected: {func_name!r}.",
                            raw_sql=raw_sql,
                            extra={"function": func_name},
                        )
            if isinstance(node, exp.Func):
                func_class_name = type(node).__name__.lower()
                for pattern in self._dangerous_function_patterns:
                    if pattern in func_class_name:
                        raise SQLValidationError(
                            reason=f"Potentially dangerous function class: {type(node).__name__!r}.",
                            raw_sql=raw_sql,
                            extra={"function_class": type(node).__name__},
                        )

    def _collect_referenced_tables(self, statement: exp.Expression) -> list[exp.Table]:
        tables: list[exp.Table] = []
        seen: set[str] = set()
        for node in statement.walk():
            if isinstance(node, exp.Table):
                key = node.sql()
                if key not in seen:
                    seen.add(key)
                    tables.append(node)
        return tables

    def _collect_cte_aliases(self, statement: exp.Expression) -> set[str]:
        aliases: set[str] = set()
        try:
            ctes = statement.find_all(exp.CTE)
            for cte in ctes:
                alias = getattr(cte, "alias", None) or getattr(cte, "name", None)
                if alias:
                    if isinstance(alias, exp.Identifier):
                        aliases.add(alias.this.lower())
                    else:
                        aliases.add(str(alias).lower())
        except Exception:
            pass
        return aliases

    def _collect_referenced_columns(self, statement: exp.Expression) -> list[exp.Column]:
        columns: list[exp.Column] = []
        seen: set[str] = set()
        for node in statement.walk():
            if isinstance(node, exp.Column):
                key = node.sql()
                if key not in seen:
                    seen.add(key)
                    columns.append(node)
        return columns

    def _table_qualified_name(self, table_exp: exp.Table) -> str:
        schema_part = getattr(table_exp, "db", None) or getattr(table_exp, "catalog", None)
        name_part = table_exp.name
        if schema_part is not None:
            return f"{schema_part}.{name_part}"
        return name_part

    def _validate_allowed_tables(
        self,
        tables: list[exp.Table],
        allowed_tables: Optional[list[str]],
        raw_sql: str,
        *,
        cte_aliases: Optional[set[str]] = None,
    ) -> None:
        if allowed_tables is None:
            return
        cte_set = {a.lower() for a in (cte_aliases or set())}
        allowed_set_lower = {t.lower() for t in allowed_tables}
        SAFE_SYSTEM_SCHEMAS_LOWER = {"information_schema", "pg_catalog", "pg_toast", "sys"}
        SAFE_SYSTEM_PREFIX_LOWER = {"pg_", "gp_", "spt_", "MSsnapshot", "MSmerge", "sysx", "dtproperties"}
        for t in tables:
            qualified = self._table_qualified_name(t)
            qualified_lower = qualified.lower()
            name_lower = t.name.lower() if getattr(t, "name", None) else ""
            schema_lower = (t.db or "").lower() if getattr(t, "db", None) else ""
            if name_lower in cte_set:
                continue
            if (
                schema_lower in SAFE_SYSTEM_SCHEMAS_LOWER
                or any(name_lower.startswith(pref) for pref in SAFE_SYSTEM_PREFIX_LOWER)
            ):
                continue
            if qualified_lower not in allowed_set_lower and name_lower not in allowed_set_lower:
                raise SQLValidationError(
                    reason=f"Table {t.sql()!r} is not in the allowed tables list for this query.",
                    raw_sql=raw_sql,
                    extra={
                        "referenced_table": t.sql(),
                        "allowed_tables_count": len(allowed_tables),
                    },
                )

    def _validate_allowed_schemas(
        self,
        tables: list[exp.Table],
        allowed_schemas: Optional[list[str]],
        raw_sql: str,
    ) -> None:
        if allowed_schemas is None:
            return
        allowed_set_lower = {s.lower() for s in allowed_schemas}
        SAFE_SYSTEM_SCHEMAS_LOWER = {"information_schema", "pg_catalog", "pg_toast", "sys"}
        for t in tables:
            schema_part = getattr(t, "db", None) or getattr(t, "catalog", None)
            if schema_part is None or str(schema_part).strip() == "":
                continue
            schema_str = str(schema_part)
            schema_lower = schema_str.lower()
            if schema_lower in SAFE_SYSTEM_SCHEMAS_LOWER:
                continue
            if schema_lower not in allowed_set_lower:
                raise SQLValidationError(
                    reason=f"Schema '{schema_str}' is not in the allowed schemas list.",
                    raw_sql=raw_sql,
                    extra={
                        "referenced_schema": schema_str,
                        "allowed_schemas": allowed_schemas,
                    },
                )

    def _validate_no_blocked_schemas(self, tables: list[exp.Table], raw_sql: str) -> None:
        for t in tables:
            schema_part = getattr(t, "db", None) or getattr(t, "catalog", None)
            if schema_part is None:
                continue
            schema_str = str(schema_part).lower()
            if schema_str in self._blocked_schemas:
                raise SQLValidationError(
                    reason=f"Access to system schema '{schema_str}' is forbidden.",
                    raw_sql=raw_sql,
                    extra={"blocked_schema": schema_str},
                )

    def _compute_ast_signature(self, statement: exp.Expression) -> str:
        normalized = statement.sql(dialect="postgres", pretty=False)
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


__all__ = ["SqlValidator"]
