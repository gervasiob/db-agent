# DB Agent TP Austral

Conversational Analytics Platform — Semantic Layer over PostgreSQL with FastAPI, LangChain and OpenAI. Ask questions in natural language, get analytical answers with auditable SQL, full lineage and defense-in-depth read-only security.

---

## Architecture

```
┌──────────────────────────────────────────────────────────────────────────┐
│                              FastAPI App                                  │
│  ┌─────────────────┐   ┌─────────────────────────────────────────────┐   │
│  │  Middleware     │   │           Lifespan                         │   │
│  │  - Request ID   │──▶│  - init_metadata_db (Postgres + pgvector)  │   │
│  │  - Process time │   │  - setup_logging (JSON/text)                │   │
│  │  - X-Headers    │   │  - DatabaseAgent singleton → app.state      │   │
│  └─────────────────┘   │  - dispose_metadata_db on shutdown         │   │
│                        └─────────────────────────────────────────────┘   │
│  ┌─────────────────────────────────────────────────────────────────────┐ │
│  │                         Exception Handlers                          │ │
│  │  DBAgentException · StarletteHTTP · Pydantic Validation · catchall │ │
│  └─────────────────────────────────────────────────────────────────────┘ │
│  ┌─────────────────────────────────────────────────────────────────────┐ │
│  │                        API v1 Routers                               │ │
│  │  /health   /database   /discovery   /query   /context              │ │
│  └─────────────────────────────────────────────────────────────────────┘ │
│  ┌──────────────────────────────────────────────────────────────────────┐│
│  │                     DatabaseAgent Orchestrator                       ││
│  │  Wires: Connections · Schema Inspector · Discovery Pipeline ·       ││
│  │  Semantic Layer · SQL Gen · SQL Validation · SQL Exec · Answers    ││
│  └──────────────────────────────────────────────────────────────────────┘│
└──────────────────────────────────────────────────────────────────────────┘
          │                                   │
          ▼                                   ▼
┌──────────────────────┐        ┌────────────────────────────┐
│  Metadata DB         │        │  Target Business DB       │
│  (PostgreSQL)        │        │  (PostgreSQL / MySQL /     │
│  - connections       │        │   SQL Server stubs)        │
│  - context versions  │        │                            │
│  - semantic entities │        │  read-only role only      │
│  - embeddings        │        │  statement_timeout · RO   │
│  - pgvector          │        │                            │
└──────────────────────┘        └────────────────────────────┘
```

### Discovery Flow

1. `POST /database/test-connection` validates credentials through adapter-specific test.
2. `POST /database/connect` tests + encrypts password (Fernet) + saves `DatabaseConnectionMetadata`.
3. `POST /database/discover` spawns async task (`DatabaseDiscoveryPipeline`):
   - Connect → Schema introspection (schemas, tables, cols, PKs, FKs, indexes, comments, enum values).
   - Row sampling (with blob exclusion, configurable `DATABASE_SAMPLE_ROWS`).
   - Column profiling (null rate, distinct approx, distribution via `pg_stats`).
   - PII classification per column + sample-data sanitization before any LLM call.
   - Semantic analysis: domains, entities, concepts, metrics, dimensions, date roles, terminology, join paths.
   - Persist `DatabaseContextVersion` + associated tables + embeddings.
4. Client polls `GET /database/discovery/status` until `READY`.

### Query Flow

1. `POST /query {question, connection_id}` → `QueryPipeline`:
   - Intent classification (DATABASE_QUERY / CLARIFICATION / EXPLANATION / UNSUPPORTED).
   - Semantic retrieval over context: match `question` → domains · entities · concepts · metrics · dims · tables · columns using vector + keyword hybrid.
   - Produce `SemanticQueryPlan` (intent · entity refs · metric refs · filters · time range).
   - Prompt LLM → `SQLQueryPlan` with tables, joins, CTEs, where, group-by, order, limit hints.
   - **SqlValidator** enforces read-only AST walk, allowed tables/schemas, no `pg_catalog` / `information_schema`, no dangerous functions (`pg_sleep`, `set_config`, `vacuum`, `copy`, `grant` …).
   - Apply `MAX_QUERY_ROWS` + `QUERY_TIMEOUT_SECONDS`.
   - Execute on a **read-only transaction** with `statement_timeout`.
   - Row-level PII sanitization before returning.
   - `QueryAnswerService` summarizes results into long/short answers with disclaimers.

### SQL Security (Defense in Depth)

```
┌─ 1) Application-level — SqlValidator (AST walk with sqlglot)
│     • Only SELECT / UNION / INTERSECT / EXCEPT / WITH
│     • No DML (INSERT/UPDATE/DELETE/UPSERT/MERGE/LOAD)
│     • No DDL (CREATE/DROP/ALTER/TRUNCATE/RENAME)
│     • No control (GRANT/REVOKE/EXEC/CALL/COPY/VACUUM/SET/DO/LOCK)
│     • No dangerous functions (pg_*, pg_sleep, set_config, nextval…)
│     • Block pg_catalog / information_schema
│     • Whitelist allowed tables + schemas
│     • Column existence check against schema metadata
│
├─ 2) Target user enforcement (PostgreSQL ROLE)
│     • NOINHERIT LOGIN PASSWORD
│     • GRANT CONNECT ON DATABASE …
│     • GRANT USAGE ON SCHEMA …
│     • GRANT SELECT ON ALL TABLES IN SCHEMA …  (future: column-level ACL)
│     • ALTER DEFAULT PRIVILEGES …
│
└─ 3) Runtime transaction guard
      • SET TRANSACTION READ ONLY
      • statement_timeout (QUERY_TIMEOUT_SECONDS)
      • LIMIT MAX_QUERY_ROWS + 1 truncation sentinel
      • Rollback instead of commit (no writes possible even if validation failed)
```

---

## Stack

| Layer | Libraries |
|---|---|
| Web framework | `fastapi>=0.115`, `uvicorn[standard]`, `starlette`, `jinja2` |
| Validation / Config | `pydantic>=2.9`, `pydantic-settings>=2.5` |
| ORM / DB | `sqlalchemy>=2`, `asyncpg`, `pgvector` (metadata) |
| Migrations | `alembic` |
| LLM | `langchain`, `langchain-openai`, `langchain-core`, `openai` |
| SQL safety | `sqlglot` (AST validation) |
| Observability | Structured JSON logging w/ request_id, process-time headers |
| Secrets | `cryptography` (Fernet) for stored connection passwords |
| Phone / PII | `phonenumbers`, regex-based email/card/DNI/CUIL/CPF/CNPJ detectors |
| HTTP/Utils | `httpx`, `tenacity`, `orjson`, `python-multipart` |
| Tests | `pytest`, `pytest-asyncio`, `pytest-cov`, `pytest-mock`, `faker`, `testcontainers[postgresql]` (opt) |
| Quality | `ruff`, `mypy` |

---

## Environment Variables

Copy `.env.example` to `.env`:

| Variable | Default | Description |
|---|---|---|
| `APP_ENV` | `development` | Runtime environment tag |
| `APP_NAME` | `DB-Agent-TP-Austral` | Service name for logs & docs |
| `APP_VERSION` | `0.1.0` | Semver reported in health/docs |
| `DEBUG` | `false` | Verbose SQLAlchemy echo + dev niceties |
| `OPENAI_API_KEY` | `None` | **Required** for LLM features |
| `OPENAI_MODEL` | `gpt-5-mini` | Generation model |
| `OPENAI_EMBEDDING_MODEL` | `text-embedding-3-small` | Embedding model for semantic retrieval |
| `OPENAI_TEMPERATURE` | `0.1` | Sampling temperature |
| `OPENAI_MAX_TOKENS` | `4000` | Max output tokens |
| `METADATA_DB_URL` | `postgresql+asyncpg://postgres:postgres@localhost:5432/db_agent_metadata` | Metadata DB |
| `METADATA_DB_SCHEMA` | `public` | Metadata schema |
| `METADATA_ENCRYPTION_KEY` | `None` | **Required** (Fernet key) — use `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` |
| `DATABASE_SAMPLE_ROWS` | `5` | Rows sampled per table for LLM context |
| `MAX_QUERY_ROWS` | `500` | Hard cap on returned rows |
| `QUERY_TIMEOUT_SECONDS` | `15` | Statement timeout enforced on target DB |
| `SQL_RETRY_COUNT` | `2` | Self-correction retries on SQL error |
| `SCHEMA_RETRIEVAL_TOP_K` | `8` | Relevant tables retrieved per query |
| `LLM_INCLUDE_SAMPLE_DATA` | `true` | Feed sampled rows into LLM prompt |
| `LLM_SANITIZE_SAMPLE_DATA` | `true` | Strip PII before LLM |
| `LOG_LEVEL` | `INFO` | Python log level |
| `LOG_FORMAT` | `json` | `json` or `text` |

---

## Installation

```bash
# 1) Install uv (one time)
curl -LsSf https://astral.sh/uv/install.sh | sh     # Linux / macOS
# or Windows:
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"

# 2) Sync project dependencies (main + dev extras)
uv sync --dev
```

---

## PostgreSQL Setup

### 1) Install pgvector

Metadata DB requires the `pgvector` extension.

```bash
# Debian/Ubuntu
sudo apt-get install -y postgresql-16-pgvector

# macOS (Homebrew)
brew install pgvector

# or build from source per https://github.com/pgvector/pgvector
```

### 2) Create the Metadata Database

```sql
CREATE DATABASE db_agent_metadata;
\c db_agent_metadata
CREATE EXTENSION IF NOT EXISTS vector;
CREATE SCHEMA IF NOT EXISTS public;
```

### 3) Create a Read-Only User on the *target* (business) Database

This is the user credentials you will POST to `/database/connect`. **Never** use the superuser for the target connection.

```sql
-- Connect to your BUSINESS database (NOT the metadata db)
\c your_business_db

CREATE ROLE db_agent_ro NOINHERIT LOGIN PASSWORD 'use_a_strong_password_here';

GRANT CONNECT ON DATABASE your_business_db TO db_agent_ro;
GRANT USAGE ON SCHEMA public TO db_agent_ro;

-- Existing tables
GRANT SELECT ON ALL TABLES IN SCHEMA public TO db_agent_ro;

-- Any future tables created in this schema
ALTER DEFAULT PRIVILEGES IN SCHEMA public
   GRANT SELECT ON TABLES TO db_agent_ro;

-- Optional: restrict statement timeout server-side for this role
ALTER ROLE db_agent_ro SET statement_timeout = '15s';
ALTER ROLE db_agent_ro SET idle_in_transaction_session_timeout = '30s';
```

---

## Migrations

```bash
uv run alembic upgrade head
```

New migration:

```bash
uv run alembic revision --autogenerate -m "describe change"
uv run alembic upgrade head
```

---

## Run

```bash
uv run uvicorn app.main:app --reload --port 8000
```

Interactive docs:
- Swagger UI:  http://localhost:8000/docs
- ReDoc:     http://localhost:8000/redoc
- OpenAPI:   http://localhost:8000/openapi.json

Root: http://localhost:8000/

---

## API Endpoints Summary

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/` | App banner, version, links |
| `GET` | `/api/v1/health` | Full status incl. metadata DB + LLM key |
| `GET` | `/api/v1/health/ready` | Readiness (metadata connected = ready) |
| `GET` | `/api/v1/health/live` | Simple liveness |
| `POST` | `/api/v1/database/test-connection` | Validate credentials, return `{ok, masked_url, version}` |
| `POST` | `/api/v1/database/connect` | Test + save encrypted credentials, return `{connection_id, status}` |
| `GET` | `/api/v1/database/schema?connection_id=UUID` | Full schema introspection |
| `GET` | `/api/v1/database/connections` | Saved connections (no secrets) |
| `DELETE` | `/api/v1/database/connections/{id}` | Remove a connection |
| `POST` | `/api/v1/database/discover` | Trigger async discovery → `{discovery_started, status}` |
| `GET` | `/api/v1/database/discovery/status?connection_id=UUID` | Poll progress (`IDLE → CONNECTING → DISCOVERING → … → READY`) |
| `POST` | `/api/v1/database/refresh-context?connection_id=UUID` | Force re-discover |
| `GET` | `/api/v1/database/knowledge-map?connection_id=UUID` | Full built knowledge map |
| `GET` | `/api/v1/database/context?connection_id=UUID` | Lightweight summary (counts, tables) |
| `POST` | `/api/v1/query` | `{question, connection_id}` → `QueryResponse` |
| `POST` | `/api/v1/query/explain` | Explain plan without executing |
| `GET` | `/api/v1/context/versions?connection_id=UUID` | Context versions list |
| `GET` | `/api/v1/context/domains` | Business domains |
| `GET` | `/api/v1/context/entities` | Business entities |
| `GET` | `/api/v1/context/concepts` | Business concepts |
| `GET` | `/api/v1/context/metrics` | Business metrics |
| `GET` | `/api/v1/context/dimensions` | Dimensions |
| `GET` | `/api/v1/context/terminology?q=` | Glossary search |

---

## Example cURL Requests

### 1) Test Connection

```bash
curl -sS http://localhost:8000/api/v1/database/test-connection \
  -H "Content-Type: application/json" \
  -d '{
    "database_type": "postgresql",
    "host": "localhost",
    "port": 5432,
    "database_name": "your_business_db",
    "username": "db_agent_ro",
    "password": "use_a_strong_password_here",
    "schema": "public",
    "ssl_mode": "disable"
  }'
```

### 2) Connect (Persist Credentials)

```bash
curl -sS http://localhost:8000/api/v1/database/connect \
  -H "Content-Type: application/json" \
  -d '{
    "database_type": "postgresql",
    "host": "localhost",
    "port": 5432,
    "database_name": "your_business_db",
    "username": "db_agent_ro",
    "password": "use_a_strong_password_here",
    "schema": "public"
  }'
# => {"connection_id":"<UUID>","status":"CONNECTED"}
```

### 3) Trigger Discovery

```bash
CONN_ID="<the-UUID-above>"

curl -sS http://localhost:8000/api/v1/database/discover \
  -H "Content-Type: application/json" \
  -d "{\"connection_id\":\"$CONN_ID\",\"force\":false}"
```

### 4) Ask a Question

```bash
curl -sS http://localhost:8000/api/v1/query \
  -H "Content-Type: application/json" \
  -d "{
    \"question\": \"How many active employees hired in the last 30 days?\",
    \"connection_id\": \"$CONN_ID\"
  }" | jq .
```

Typical response:

```json
{
  "question": "How many active employees hired in the last 30 days?",
  "answer": "Según los datos actuales…",
  "short_answer": "42 active employees hired in the last 30 days.",
  "semantic_plan": { ... },
  "sql": "SELECT count(*) FROM employees WHERE status='ACTIVE' AND hire_date >= NOW() - INTERVAL '30 days'",
  "columns": ["count"],
  "data": [{"count": 42}],
  "row_count": 1,
  "execution_time_ms": 412,
  "request_id": "abc123…",
  "disclaimers": ["Data based on current DB snapshot", "…"]
}
```

---

## Semantic Layer Explanation

The semantic layer is a persisted, versioned model of the business meaning of each schema object.

| Artifact | Purpose |
|---|---|
| **BusinessDomain** | Top-level grouping (HR, Sales, Finance, Inventory) |
| **BusinessEntity** | Real-world things (Employee, Customer, Order, Product) linked to primary tables, key columns, status columns |
| **BusinessConcept** | Filter-like semantic definitions (`ActiveEmployee = status='ACTIVE' AND deleted_at IS NULL`) |
| **BusinessMetric** | Aggregations over tables/columns (`Revenue = SUM(orders.total)`) |
| **Dimension** | GROUP BY axes with explicit joins required (`Date`, `Region`, `ProductCategory`) |
| **DateSemantic** | Role tagging (`CREATED_AT`, `HIRE_DATE`, `ORDER_DATE`) so time filters map to the right column |
| **ColumnSemantic** | Column-level business name, PII level, links to entity/domain/concept |
| **TerminologyEntry** | Glossary: aliases, synonyms (e.g. `turnover ↔ revenue ↔ ventas brutas`) |
| **RelationshipGraph** | FK + inferred join edges for path-finding between unrelated entities |
| **EnumValue** | Detected enumeration columns and their allowed values |

Each built context gets a `context_version` (semver + short id), `schema_hash`, `llm_model`, `embedding_model`, so answers are fully reproducible and audit-ready.

---

## SQL Security Explanation + Read-Only User Instruction

- **Always connect to the target DB via the `db_agent_ro` role you created above.** Even if SqlValidator had a 0-day, the PostgreSQL role itself cannot write.
- `NOINHERIT` is intentional: prevents the role from accumulating privileges via group membership.
- `statement_timeout` at both role level *and* per-query prevents runaway queries.
- `SET TRANSACTION READ ONLY` + `ROLLBACK` instead of `COMMIT` means there is no write path even at the protocol level.
- The SqlValidator additionally blocks `pg_catalog` / `information_schema` exploration to mitigate data-exfiltration attacks via system catalogues, and blocks `pg_sleep` style resource-exhaustion functions.

Passwords for saved connections are never stored in plaintext. They are encrypted at rest with **Fernet** (AES-128-CBC + HMAC-SHA256) using `METADATA_ENCRYPTION_KEY` and associated fields (username, per-row salt), then written as `encrypted_password` bytes.

---

## Tests

```bash
# All tests
uv run pytest -v

# With coverage
uv run pytest -v --cov=app --cov-report=term-missing

# Unit only
uv run pytest -v tests/unit

# Integration (requires docker + testcontainers; skips automatically if missing)
uv run pytest -v tests/integration
```

Key test modules:

- `tests/unit/test_sql_validator.py` — AST validator on ALLOWED / BLOCKED lists, multi-statement rejection, system-schema block, unknown-table detection.
- `tests/unit/test_data_sanitization.py` — email, phone, credit card (Luhn-checked), DNI/CUIL/CPF/CNPJ document patterns, password/token redaction, column PII classification.
- `tests/unit/test_services_smoke.py` — imports every service module, instantiates validator, sanitizer, connection service, context service, agent facade — confirms zero import/constructor bombs with no network.
- `tests/integration/test_security_sql.py` — optional, uses `testcontainers[postgresql]`: runs allowed patterns against a real Postgres, then runs blocked patterns through actual `db_agent_ro` role to confirm database-level enforcement.

---

## Known Limitations

- **Database adapters**: PostgreSQL is fully implemented. The SQL Server (`SQLServerAdapter`) and MySQL (`MySQLAdapter`) classes are explicit `NotImplementedError` stubs in `app/database/adapters/base.py:379` and `app/database/adapters/base.py:496`. Their wireframe exists to enable clean incremental rollout; production usage against those backends requires implementing the adapter contract (15 abstract methods + `map_column_type`).
- Semantic analysis LLM prompts and self-correction loops are pluggable via `app/llm/prompts/` but default to a conservative, deterministic pipeline.
- Vector retrieval currently stores embeddings in the metadata database; a dedicated vector store can be added by replacing the `Embeddings` repository without API changes.
- The `DatabaseDiscoveryPipeline` today runs as an in-process `asyncio.create_task(...)`. For very large schemas or many concurrent discoveries, replace with a Celery / Temporal / Arq worker and expose the `discovery_status` endpoint against the same task backend.

---

## Contributing Hints

1. **Start from tests**. `tests/unit/test_sql_validator.py` + `tests/unit/test_data_sanitization.py` are pure logic — no infrastructure, no DB, no LLM calls. Great for first contributions.
2. **Adapter contract** is the single source of truth for target-database support. Add a database by extending `DatabaseAdapter` in `app/database/adapters/`, then register it in `by_database_type()`.
3. **Add a semantic enrichment step** by implementing a new subclass in `app/services/` and calling it from `DatabaseAgent._run_discovery(...)` between `SAMPLING → PROFILING → SEMANTIC_ANALYZING`.
4. **Use `RequestIdFilter` + contextvars** for logs. Every handler inherits `X-Request-Id` auto-generated if missing; always pass it downstream.
5. **Never log raw values** — use `sanitize_log_value()` from `app.core.security` or the `DataSanitizationService` before logging any user/business data.
6. **Never concatenate SQL**. Even in internal utilities, use SQLAlchemy `text(:param)` + bound parameters or the `sqlglot` AST builders.
7. **Run quality gate** before pushing:
   ```bash
   uv run ruff check app tests
   uv run mypy app
   uv run pytest -v tests/unit
   ```
