# SQL Server (MSSQL) Support Implementation Plan

## Repository Research

Conclusiones clave de la investigación del repositorio:

1. **Tipos de BD ya están predefinidos**: El proyecto YA contemplaba `sqlserver` en el `_DATABASE_TYPE = Literal["postgresql", "sqlserver", "mysql"]` en [connection.py#L19](file:///c:/Users/Gervasio/Documents/trae_projects/db_agent_tp_austral/app/database/connection.py#L19-L31) con default port 1433 y driver `mssql+aioodbc`.
2. **Dialectos sqlglot mapeados**: `_DIALECT_ALIASES` en `sql_validator.py` tiene sqlserver/mssql/tsql → "sqlserver", y `sql_validation_service.py` + `sql_execution_service.py` mapean `"sqlserver"` → dialecto sqlglot `"tsql"`.
3. **Registry de adapters (dos lugares)**:
   - [adapters/base.py#L114-L129](file:///c:/Users/Gervasio/Documents/trae_projects/db_agent_tp_austral/app/database/adapters/base.py#L114-L129): `by_database_type` referencia `SQLServerAdapter` pero NO EXISTE el módulo.
   - [adapters/__init__.py#L10-L24](file:///c:/Users/Gervasio/Documents/trae_projects/db_agent_tp_austral/app/database/adapters/__init__.py#L10-L24): `adapter_factory` solo tiene entrada `"postgresql"`. Falta sqlserver.
4. **UI bloqueada**: [ui.html#L112-L120](file:///c:/Users/Gervasio/Documents/trae_projects/db_agent_tp_austral/app/ui/templates/ui.html#L112-L120) `<option value="sqlserver" disabled>SQL Server (proximamente)</option>`. Default conn state usa port 5432 postgres, schema public (sqlserver default schema is dbo).
5. **Dependencies**: `pyproject.toml` solo tiene `"asyncpg>=0.30.0"`. Faltan `aioodbc>=0.5.0` (async wrapper for pyodbc) y `pyodbc>=5.1.0` (driver ODBC Python binding).
6. **Bloques de switch/case por database_type (hoy incompletos para sqlserver)**:
   - [connection.py build_connection_string](file:///c:/Users/Gervasio/Documents/trae_projects/db_agent_tp_austral/app/database/connection.py#L140-L167): solo tiene query params mysql + postgresql. Falta sqlserver (DRIVER=ODBC Driver 18 for SQL Server, Encrypt, TrustServerCertificate).
   - [connection.py build_connect_args](file:///c:/Users/Gervasio/Documents/trae_projects/db_agent_tp_austral/app/database/connection.py#L172-L182): solo postgres (statement_cache_size, server_settings search_path) + mysql charset. Falta sqlserver.
   - [database_repository.py test_connection](file:///c:/Users/Gervasio/Documents/trae_projects/db_agent_tp_austral/app/repositories/database_repository.py#L209-L252): YA tiene if postgres, elif mysql, **elif sqlserver placeholder** (hay que completarlo con `@@VERSION`, `DB_NAME()` etc).
   - [sql_execution_service.py _set_session_statement_timeout](file:///c:/Users/Gervasio/Documents/trae_projects/db_agent_tp_austral/app/services/sql_execution_service.py#L150-L165): solo postgres (SET statement_timeout + SET TRANSACTION READ ONLY). Falta sqlserver equivalente (SET LOCK_TIMEOUT, SET TRANSACTION ISOLATION LEVEL READ UNCOMMITTED / READ COMMITTED SNAPSHOT).
7. **Metadata discovery (INFORMATION_SCHEMA)**: El adapter `PostgreSQLAdapter` en [postgresql.py](file:///c:/Users/Gervasio/Documents/trae_projects/db_agent_tp_austral/app/database/adapters/postgresql.py) implementa `get_schemas/get_tables/get_columns/get_primary_keys/get_foreign_keys/sample_rows/get_table_row_count/_PG_TYPE_MAP`. Para SQL Server hay que replicar los mismos métodos pero usando `INFORMATION_SCHEMA` compatible MSSQL + `sys.*` (sys.foreign_keys, sys.foreign_key_columns, sys.indexes, sys.tables, sys.schemas) + `TOP N` en vez de `LIMIT N`, y tipo de dato mapping `_MSSQL_TYPE_MAP`.
8. **Validación AST SQL**: Allowlist system schemas en sql_validator.py ya incluye `information_schema/pg_catalog/pg_toast`, pero SQL Server usa `INFORMATION_SCHEMA` (case insensitive, igual) + `sys` schema → **AGREGAR `sys` a los safe system schemas + system prefixes** (mssql tiene tablas como `sys.tables`, `sys.columns`, `sys.databases`, `spt_values` etc).

## Files and Modules

| Path | Cambio esperado |
|------|-----------------|
| `pyproject.toml` | Agregar deps `aioodbc>=0.5.0`, `pyodbc>=5.1.0` bajo `[project].dependencies` |
| `app/database/adapters/sqlserver.py` | **NUEVO ARCHIVO**: `class SQLServerAdapter(DatabaseAdapter)` con: `_MSSQL_TYPE_MAP`, `_get_engine`, `get_schemas`, `get_tables`, `get_columns`, `get_primary_keys`, `get_foreign_keys`, `sample_rows`, `get_table_row_count`, y todos los métodos abstractos de `DatabaseAdapter` con queries INFORMATION_SCHEMA/SYS compatibles MSSQL. |
| `app/database/adapters/base.py` | No hay cambios salvo imports lazy existentes; solo asegurarse lazy import no falle. |
| `app/database/adapters/__init__.py` | Expandir `adapter_factory` registry: agregar `"sqlserver": SQLServerAdapter` (lazy import). |
| `app/database/connection.py` | (1) `build_connection_string`: agregar query params sqlserver: `DRIVER=ODBC+Driver+18+for+SQL+Server`, `Encrypt=yes`, `TrustServerCertificate=yes` (configurable via ssl_mode), connect_timeout. (2) `build_connect_args`: if sqlserver → preparar connect_args ODBC si hace falta (trusted_connection, MARS_Connection, etc). (3) `_DEFAULT_PORTS / _ASYNC_DRIVERS` ya están, no tocar. |
| `app/repositories/database_repository.py` | Completar `elif config.database_type == "sqlserver"` del método `test_connection`: ejecutar `SELECT @@VERSION`, `DB_NAME()`, `SUSER_SNAME()`, `@@SERVERNAME` y devolver dict con `database_version`, `database_name`, `current_user`, `server_host`. |
| `app/services/sql_execution_service.py` | Expandir `_set_session_statement_timeout`: agregar `elif config.database_type == "sqlserver":` → `SET LOCK_TIMEOUT = {timeout_ms}`; `SET TRANSACTION ISOLATION LEVEL READ UNCOMMITTED` (o READ COMMITTED SNAPSHOT, RO por defecto). |
| `app/security/sql_validator.py` | (1) Agregar `sys` schema a `SAFE_SYSTEM_SCHEMAS_LOWER` (línea ~240). (2) Agregar `"spt_"` a `SAFE_SYSTEM_PREFIX_LOWER` (tablas internas). Asegurarse que columns allowlist reconozca tablas `sys.*` como system. |
| `app/ui/templates/ui.html` | (1) Quitar `disabled` al `<option value="sqlserver">` (quitar "proximamente"). (2) Actualizar `conn` default Alpine state: agregar watcher on `database_type` → si cambia a sqlserver → port 1433, schema `dbo`, ssl_mode `prefer`/`disable`. (3) Badge `slice(0,3)` muestra "pos" para postgres, "sql" para sqlserver, "mys" para mysql (opcional mejora de UX). |
| `README.md` | Agregar sección "SQL Server Prerrequisito ODBC Driver 18 for SQL Server" + instrucciones instalación Windows / Ubuntu 24.04 LTS / macOS + ejemplo connection string para `DatabaseConnectionRequest` MSSQL. |
| `.env.example` | Comentario ejemplo `# Ejemplo SQL Server target: no en METADATA_DB_URL (metadata es Postgres), sino como conexión via UI: host=SQLSRV01.corp.local, port=1433, database_name=erp, username=erp_ro, password=..., schema=dbo, database_type=sqlserver, ssl_mode=require` |
| `tests/unit/test_sql_validator.py` | Agregar 2 tests: sql TSQL `SELECT TOP 10 * FROM dbo.users;` validate_read_only passes; sql TSQL `SELECT c.name FROM sys.columns c` passes (allow sys schema). |

## Implementation Steps (dependency order)

1. **Agregar dependencias**: editar `pyproject.toml`, agregar `"aioodbc>=0.5.0"` y `"pyodbc>=5.1.0"` a `[project].dependencies`. Luego `uv sync --dev` para instalar localmente y validar que compile pyodbc contra ODBC (en Windows suele traer wheels prebuilt; en Linux apt necesita `unixodbc unixodbc-dev`).
2. **Implementar `SQLServerAdapter`**: crear `app/database/adapters/sqlserver.py` replicando la interfaz de `PostgreSQLAdapter`, pero:
   - `_MSSQL_TYPE_MAP` con: int/INTEGER, bigint/BIGINT, smallint/SMALLINT, tinyint/SMALLINT, bit/BOOLEAN, decimal/NUMERIC, numeric/NUMERIC, money/NUMERIC, smallmoney/NUMERIC, float/FLOAT, real/FLOAT, char/CHAR, nchar/CHAR, varchar/VARCHAR, nvarchar/TEXT, text/TEXT, ntext/TEXT, date/DATE, time/TIME, datetime/TIMESTAMP, datetime2/TIMESTAMP, smalldatetime/TIMESTAMP, datetimeoffset/TIMESTAMPTZ, binary/BYTEA, varbinary/BYTEA, image/BYTEA, uniqueidentifier/UUID, xml/TEXT, json/JSON.
   - `get_schemas`: `SELECT s.name AS schema_name FROM sys.schemas s WHERE s.name NOT IN ('sys', 'INFORMATION_SCHEMA', 'guest', 'db_owner', ...) ORDER BY s.name`.
   - `get_tables`: `SELECT TABLE_SCHEMA, TABLE_NAME FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_TYPE = 'BASE TABLE' AND TABLE_SCHEMA NOT IN (...)`.
   - `get_columns`: `SELECT TABLE_SCHEMA, TABLE_NAME, COLUMN_NAME, ORDINAL_POSITION, DATA_TYPE, CHARACTER_MAXIMUM_LENGTH, NUMERIC_PRECISION, NUMERIC_SCALE, IS_NULLABLE FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_SCHEMA = @schema AND TABLE_NAME = @table` (con binds).
   - `get_primary_keys`: `SELECT s.name AS schema_name, t.name AS table_name, c.name AS column_name, ic.key_ordinal AS ordinal_position FROM sys.tables t JOIN sys.schemas s ON t.schema_id = s.schema_id JOIN sys.indexes i ON t.object_id = i.object_id AND i.is_primary_key = 1 JOIN sys.index_columns ic ON i.object_id = ic.object_id AND i.index_id = ic.index_id JOIN sys.columns c ON ic.object_id = c.object_id AND ic.column_id = c.column_id ORDER BY s.name, t.name, ic.key_ordinal`.
   - `get_foreign_keys`: via `sys.foreign_keys`, `sys.foreign_key_columns`, `sys.tables`, `sys.columns`, `sys.schemas` → devolver `parent_schema, parent_table, parent_column, child_schema, child_table, child_column, fk_name`.
   - `sample_rows(schema, table, n)`: `SELECT TOP {n} * FROM [{schema}].[{table}] WITH (NOLOCK);` (NOLOCK para no bloquear en RO transaction; IMPORTANTE: usar identifiers escapados con SQL Server brackets, NO `"identifier"` double quotes a menos que QUOTED_IDENTIFIER ON, que SQLAlchemy suele setear).
   - `get_table_row_count(schema, table)`: `SELECT SUM(p.rows) AS row_count FROM sys.tables t JOIN sys.schemas s ON t.schema_id = s.schema_id JOIN sys.partitions p ON t.object_id = p.object_id AND p.index_id IN (0,1) WHERE s.name = ? AND t.name = ? GROUP BY t.object_id` (statistics, approx, sin bloqueo).
   - `_get_engine`: igual a PostgreSQLAdapter pero con `execution_options={"schema_translate_map": {None: config.schema or "dbo"}, "isolation_level": "READ COMMITTED SNAPSHOT"}` o `READ COMMITTED` según support del target.
3. **Actualizar registries adapters**:
   - `adapters/__init__.py::adapter_factory()` → agregar `"sqlserver": SQLServerAdapter` con `from app.database.adapters.sqlserver import SQLServerAdapter` lazy.
   - Verificar que `adapters/base.py::by_database_type()` lazy import falle bien: agregar `try/except ImportError` para que si falta pyodbc/aioodbc, tire un error explicativo "Missing optional dep aioodbc for SQL Server support, install via uv pip install aioodbc pyodbc" en vez de un NameError raro.
4. **Extender `build_connection_string` + `build_connect_args` para MSSQL** (connection.py):
   - Query params por default para sqlserver si no vienen en `config.extra` (campo que podemos agregar como Optional? — Mejor: usar `ssl_mode` field como lo hace Postgres: `ssl_mode=require` → `Encrypt=yes;TrustServerCertificate=no`; `prefer`/`disable` → `Encrypt=yes;TrustServerCertificate=yes`. También agregar `ODBC Driver 18 for SQL Server` como driver default, pero permitir override via un parámetro que armamos en connection string como `DRIVER=...`.
   - Importante: `mssql+aioodbc://user:pass@host:port/dbname?DRIVER=ODBC+Driver+18+for+SQL+Server&Encrypt=yes&TrustServerCertificate=yes` — exactamente el formato que espera SQLAlchemy 2.0 dialect `mssql` + async driver `aioodbc`.
5. **Completar `test_connection` sqlserver branch**: en database_repository.py línea ~239 (placeholder `elif sqlserver`) ejecutar queries `SELECT @@VERSION, DB_NAME(), SUSER_SNAME(), @@SERVERNAME, @@LANGID`; devolver el dict con los mismos keys que postgres (database_version, database_name, current_user, server_host, server_version_major etc) para que UI Connection Test Badge se vea igual.
6. **Session SETUP sqlserver**: en sql_execution_service.py `_set_session_statement_timeout`, agregar bloque:
   ```python
   elif config.database_type == "sqlserver":
       timeout_ms = int(timeout_seconds * 1000)
       try: await session.execute(text(f"SET LOCK_TIMEOUT = {timeout_ms}"))
       except Exception: pass
       try: await session.execute(text("SET TRANSACTION ISOLATION LEVEL READ UNCOMMITTED"))
       except Exception: pass
       # snapshot isolation si está activado el target:
       # try: await session.execute(text("SET TRANSACTION ISOLATION LEVEL SNAPSHOT"))
   ```
7. **Safe schemas SQL Server**: en sql_validator.py `validate_columns_exist` y `_validate_allowed_schemas`/`_validate_allowed_tables` — agregar `"sys"` a `SAFE_SYSTEM_SCHEMAS_LOWER` (línea ~240 col 47 en código actual) y `"sys."` o `"sys"` es suficiente. También los prefixes `spt_` / `MSsnapshot` / `MSmerge` / `sysx` / `dtproperties`. Agregar `"sys"` a `_BLOCKED_SCHEMAS`? NO — NO bloquear sys e INFORMATION_SCHEMA para preguntas metadata-only (como el fix de postgres que hicimos la vez pasada para "resumen del esquema"). Mantener igual que postgres info_schema (allow).
8. **UI fixes**:
   - Línea ~116: `<option value="sqlserver">SQL Server</option>` (sin disabled, sin "(proximamente)").
   - Actualizar `conn` default Alpine JS: agregar método `onChangeDatabaseType()` o `.watch` en `conn.database_type`: `if newVal == 'sqlserver': conn.port=1433; conn.schema='dbo'; conn.ssl_mode='disable'; if (!conn.username) conn.username='sa';`; similar para mysql si querés pero no está en scope.
   - Badge `slice(0,3)`: por ahora deja igual pero el texto que muestra `x-text="c.database_type?.slice(0,3) || 'pg'"` → sqlserver devuelve "sql" lo cual es OK, postgres → "pos" es aceptable, o mejorar a mapping explicit pero no bloqueante.
9. **Docs + README**: agregar sección antes de "Execution Local" llamada "SQL Server Support (optional)" → instrucciones:
   - **Windows**: `winget install Microsoft.ODBC.Driver.18.SQLServer`; o bajar instalador oficial Microsoft.
   - **Ubuntu 22.04/24.04**: `curl https://packages.microsoft.com/keys/microsoft.asc | sudo tee /etc/apt/trusted.gpg.d/microsoft.asc && curl https://packages.microsoft.com/config/ubuntu/$(lsb_release -rs)/prod.list | sudo tee /etc/apt/sources.list.d/mssql-release.list && sudo apt-get update && sudo ACCEPT_EULA=Y apt-get install -y msodbcsql18 unixodbc unixodbc-dev`
   - **macOS**: `brew install unixodbc microsoft/mssql-release/msodbcsql18`
   - Ejemplo JSON request para crear conexión MSSQL vía `POST /api/v1/database/connect`: `{"database_type":"sqlserver", "host":"sqlsrv01.corp.local", "port":1433, "database_name":"erp_prod", "username":"erp_ro", "password":"SuperSecreto123", "schema":"dbo", "ssl_mode":"disable"}`.
   - Nota: `mssql+aioodbc` NO necesita `pgvector` en el target (solo metadata Postgres). El target SQL Server no requiere extensions (solo usuario con `GRANT SELECT ON SCHEMA :: dbo TO erp_ro;` y `VIEW SERVER STATE` / `VIEW DEFINITION` opcionales para metadata más rica).
10. **Tests unit validador TSQ**: `tests/unit/test_sql_validator.py` agregar casos:
    - `SELECT TOP 10 Id, Name FROM dbo.Customers ORDER BY CreatedAt DESC;` → validate_read_only OK (permitido, readonly).
    - `SELECT schema_name(t.schema_id) AS schema_name, t.name AS table_name, c.name AS column_name FROM sys.tables t JOIN sys.columns c ON t.object_id = c.object_id;` → OK (sys schema permitido).
    - `DELETE FROM dbo.Users;` → bloqueado SQLValidationError.

## Dependencies and Considerations

1. **ODBC Driver Name es string CASE SENSITIVE en el connection string**: Default forzar `"ODBC Driver 18 for SQL Server"` (exactamente el nombre que Microsoft usa). Permitir override via un `odbc_driver` extra field o query param, pero fallback hardcodeado a 18 que es LTS 2023–2030+.
2. **Windows Integrated Auth (SSPI/Trusted_Connection=yes)**: para caso on-prem AD, usuarios Windows se conectan sin user/pass (Trusted_Connection=Yes). Agregar detección: si `username` es empty/null y database_type es sqlserver, appendear `Trusted_Connection=yes` al connection string query params y NO incluir user:pass en la URL raw.
3. **SQL Azure (PaaS)**: Encrypt=yes implícito; TrustServerCertificate NO; ssl_mode="require" por default. El driver actual ODBC 18 ya defaultea Encrypt=yes, lo que rompe conexiones a SQL Server on-prem self-signed cert. Por eso ssl_mode="disable" → Encrypt=optional / TrustServerCertificate=yes para on-prem.
4. **IDENTITY / SEQUENCE columns en sample rows**: `SELECT TOP N * FROM dbo.X WITH (NOLOCK)` no trae issues; para tablas temporal o memory-optimized es válido.
5. **Foreign Keys SQL Server**: una FK con N columnas devuelve N rows en sys.foreign_key_columns; hay que agrupar por constraint_object_id y ordenar por constraint_column_id. Devolver `fk_name`, `ordinal_position`, etc para que el pipeline de relaciones construya el grafo igual que postgres.
6. **Dialecto sqlglot TSQ**: TOP/ORDER BY/OFFSET/FETCH NEXT: LLM a veces escribe `OFFSET 10 ROWS FETCH NEXT 10 ROWS ONLY`; sqlglot lo soporta. El `apply_row_limit` usando dialecto `tsql` debería mapear bien.
7. **Permisos mínimos target MSSQL**: rol `db_datareader` + `VIEW DEFINITION` (permite ver FK/indices via sys.*). Si no tienen VIEW DEFINITION, el metadata discovery falla en PKs/FKs. Agregar warning en logs y continuar sin relaciones (igual que fallbacks ya existentes en Discovery pipeline).
8. **`schema_translate_map` para dbo default**: igual que postgres (si la config schema = "dbo"), queries sin schema van a dbo. Si schema = None default "dbo" en _ASYNC_DRIVERS query params → NO, no afecta url; afecta `config.schema` en `build_engine_from_config execution_options`.
9. **Testing en local sin SQL Server real**: Unit tests del adapter son unitarios; no requieren SQL Server vivo. Para integracion, habilitar `--mssql-container` con Testcontainers `mcr.microsoft.com/mssql/server:2022-latest` (License: Developer/EULA aceptado). Pero fuera de scope inmediato; lo podemos agregar opcionalmente pero no bloquea la feature.

## Validation

1. **Python import check**: `uv run python -c "from app.database.adapters.sqlserver import SQLServerAdapter; from app.database.adapters import adapter_factory; print(adapter_factory('sqlserver'))"` → no ImportError, imprime clase.
2. **SqlValidator TSQ smoke**: `uv run pytest -v tests/unit/test_sql_validator.py -k mssql` → pass 2 tests nuevos.
3. **Service smoke**: `uv run pytest -v tests/unit/test_services_smoke.py` → pass todos (no regression).
4. **Build engine test (si hay SQL Server reachable)**: probar `POST /api/v1/database/connect` + `POST /api/v1/database/connections/test` → 200, devuelve version + current_user.
5. **Schema discovery smoke (si SQL Server reachable)**: `POST /api/v1/database/discover` → SchemaDiscovery pasa (tables, columns, pks, fks non-empty).
6. **NL query smoke (si SQL Server reachable)**: `"cuáles son las 10 órdenes más recientes?"` → 200, SQL usa `TOP 10`, dialecto TSQL valida, se ejecuta con SET LOCK_TIMEOUT, devuelve rows.
7. **UI**: Seleccionar "SQL Server" en el dropdown → port cambia a 1433, schema → dbo. Completar credenciales → "Guardar y Conectar" → status 200. "Descubrir" → progress.
8. **Diagnóstico offline**: agregar en `scripts/` un script `diag_mssql_conn.py` (opcional) para debuggear ODBC drivers instalados: `uv run python -c "import pyodbc; print(pyodbc.drivers())"` → debe imprimir al menos `'ODBC Driver 18 for SQL Server'`.

## Risks

| Riesgo | Handling / Fallback |
|--------|---------------------|
| No tienen ODBC Driver 18 instalado en el server host, falla pyodbc/aioodbc import | Wrap import `aioodbc` dentro del `SQLServerAdapter._get_engine()` → si falla, levantar `DatabaseConnectionError` con mensaje claro: `Falta instalar ODBC Driver 18 for SQL Server en el host. Comando Ubuntu: sudo ACCEPT_EULA=Y apt install msodbcsql18. Windows: winget install Microsoft.ODBC.Driver.18.SQLServer`. Agregar chequeo en startup opcional. |
| Windows `DRIVER=ODBC Driver 18 for SQL Server` tiene espacio, al urlencode queda mal | Buildear el connection string con `urllib.parse.quote_plus("ODBC Driver 18 for SQL Server")` dentro de `build_connection_string` para SQL Server (no el default de urllib de SA? SQLAlchemy a veces lo arregla, pero para evitarlo, asegurarse). |
| On-prem SQL Server SSL self-signed, TrustServerCertificate por default = NO → falla conexión TLS | `ssl_mode=disable` default en UI cuando es sqlserver? o agregar un field `trust_server_certificate` booleano. Para MVP: `ssl_mode=disable` → `Encrypt=yes;TrustServerCertificate=yes`; `require` → `Encrypt=yes;TrustServerCertificate=no`. |
| Tablas SQL Server `sys.*` se bloquean en validación | Agregadas a `SAFE_SYSTEM_SCHEMAS_LOWER` y allowlist de columnas. Verificado en tests unit. |
| `SET TRANSACTION ISOLATION LEVEL READ UNCOMMITTED` no se soporta en Always On AG con readable secondary? | En vez de READ UNCOMMITTED, `READ COMMITTED SNAPSHOT` o nada; fallback try/except cada SET y continuar sin romper query. |
| `aioodbc` no se encuentra la dependencia en algunas plataformas (ej: pyodbc necesita compilar en alpine musl libc) → falla uv sync | En README agregar nota: Alpine NO soportado para MSSQL (glibc required). Si es Alpine, recomendar usar `debian:bookworm-slim` o `ubuntu:24.04` Docker image. |
