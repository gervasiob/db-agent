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

## Prerequisitos (antes de instalar)

| Componente | Version mínima | ¿Por qué? |
|---|---|---|
| Python | **3.12** | `pyproject.toml:5` → `requires-python = ">=3.12,<3.13"`. No funciona con 3.13 por restricción de dependencias |
| PostgreSQL | ≥ **14**, recomendado ≥ **16** | Metadata DB usa `pgvector`; también hay targets de negocio |
| Extensión `pgvector` | ≥ **0.7** | `CREATE EXTENSION IF NOT EXISTS vector` en **ambas** bases: metadata + target (si querés semantic retrieval sobre datos target) |
| `uv` (gestor de paquetes) | ≥ 0.4 | Reemplaza a `pip`/`pip-tools`/`venv`; instala `pyproject.toml` |
| OpenAI | API key válida | Generación SQL + embeddings semantic retrieval |
| CPU / RAM | 1 vCPU / 1 GB RAM mínimo | Para 1–2 conexiones y esquemas de <200 tablas. Discovery inicial ~40–90 s (GPT-5-mini + ~12 LLM calls por schema pequeño). Para schemas >500 tablas usar ≥ 2 vCPU / 2 GB RAM |
| Shell | Linux/macOS: `bash`; Windows: PowerShell 7 o CMD | Algunos scripts de deploy son bash, pero el core de la app es multiplataforma. |

---

## Environment Variables (`.env`)

Ubicación esperada por pydantic-settings: raíz del proyecto (al lado de `pyproject.toml`).  
Archivo fuente de ejemplo: [.env.example](file:///c:/Users/Gervasio/Documents/trae_projects/db_agent_tp_austral/.env.example).  
Carga efectiva en: [app/core/config.py](file:///c:/Users/Gervasio/Documents/trae_projects/db_agent_tp_austral/app/core/config.py#L6-L43).

### Obligatorias

| Variable | Valor default | ¿Qué poner? |
|---|---|---|
| `OPENAI_API_KEY` | `None` | **Obligatoria**. Key de OpenAI (o compatible OpenAI-compatible `/v1` base url, si usas proxy). |
| `METADATA_ENCRYPTION_KEY` | `None` | **Obligatoria**. Key Fernet (AES-128-CBC + HMAC-SHA256). **Generarla UNA vez y guardarla de forma segura**; si la perdés no recuperás las contraseñas de conexiones guardadas. Generar: `uv run python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` |

### Casi obligatorias (recomendado cambiarlas)

| Variable | Default | Descripción |
|---|---|---|
| `METADATA_DB_URL` | `postgresql+asyncpg://postgres:postgres@localhost:5432/db_agent_metadata` | Connection string **metadata** (PostgreSQL donde se guardan conexiones, contexto, embeddings). Driver `asyncpg` obligatorio. |
| `METADATA_DB_SCHEMA` | `public` | Schema metadata (no cambiar a menos que tengas multi-tenant metadata en otra schema). |
| `APP_ENV` | `development` | `development` / `staging` / `production` |
| `DEBUG` | `false` | Pone `true` **solo** en dev: SQLAlchemy echo + logging verbose. En NUNCA production porque loggea queries y puede exfiltrar datos en logs. |
| `LOG_FORMAT` | `json` | `json` (prod, para Datadog/Grafana/ELK/CloudWatch) o `text` (dev, legible). |
| `LOG_LEVEL` | `INFO` | `DEBUG / INFO / WARNING / ERROR / CRITICAL` |
| `APP_NAME` / `APP_VERSION` | `DB-Agent-TP-Austral` / `0.1.0` | Nombre/versión en logs, health, UI header y OpenAPI docs. |

### Modelos y sampling LLM

| Variable | Default |
|---|---|
| `OPENAI_MODEL` | `gpt-5-mini` | Modelo generación (SQL + semantic). Alternativa menor costo: `gpt-4o-mini`. |
| `OPENAI_EMBEDDING_MODEL` | `text-embedding-3-small` | Embeddings. 512-dim default, barato. |
| `OPENAI_TEMPERATURE` | `0.1` | Bajo para SQL determinista, sin creatividad. |
| `OPENAI_MAX_TOKENS` | `4000` | Max tokens output. Aumentar si tu schema es muy grande (≥300 columnas o joins complejos). |

### Guardrails del pipeline

| Variable | Default | Descripción |
|---|---|---|
| `DATABASE_SAMPLE_ROWS` | `5` | Filas muestreadas x tabla para contexto LLM. Más = mejor LLM, más tokens y mayor riesgo PII → `LLM_SANITIZE_SAMPLE_DATA=true` lo sanitiza. |
| `MAX_QUERY_ROWS` | `500` | Hard CAP de filas devueltas al cliente. Incluso si el usuario pide `LIMIT 999999`, el pipeline appendea un truncamiento `LIMIT MAX_QUERY_ROWS + 1` (sentinel +1 para detectar truncamiento). |
| `QUERY_TIMEOUT_SECONDS` | `15` | `statement_timeout` en la transacción target. Aumentar si tenés analytics sobre tablas >10M rows sin índices. |
| `SQL_RETRY_COUNT` | `2` | Intentos de auto-corrección LLM si el SQL falla en validación AST / execution. 2 ⇒ 3 intentos totales. |
| `SCHEMA_RETRIEVAL_TOP_K` | `8` | Tablas recuperadas vía hybrid (vector + BM25-like) por pregunta. Para schemas grandes poner 12–16. |
| `LLM_INCLUDE_SAMPLE_DATA` | `true` | Meter rows sampled en el prompt? Mejora joins correctos, pero si la data es sensible apagar. |
| `LLM_SANITIZE_SAMPLE_DATA` | `true` | Quita emails, teléfonos, DNI/CUIL, tarjetas, tokens de rows sampled ANTES de mandar a LLM. DEJAR en `true` en producción. |

### Ejemplo `.env` completo (DESARROLLO local Windows / Linux / macOS)

```dotenv
APP_ENV=development
APP_NAME=DB-Agent-TP-Austral
APP_VERSION=0.1.0
DEBUG=false

# -----------------------------
# Obligatorias (llenar SIEMPRE)
# -----------------------------
OPENAI_API_KEY=sk-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
METADATA_ENCRYPTION_KEY=abcdefgHIJKLMNOPQRSTUVWXYZ1234567890abcdefg=

# -----------------------------
# Metadata DB
# -----------------------------
METADATA_DB_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/db_agent_metadata
METADATA_DB_SCHEMA=public

# -----------------------------
# LLM
# -----------------------------
OPENAI_MODEL=gpt-5-mini
OPENAI_EMBEDDING_MODEL=text-embedding-3-small
OPENAI_TEMPERATURE=0.1
OPENAI_MAX_TOKENS=4000

# -----------------------------
# Pipeline + guardrails
# -----------------------------
DATABASE_SAMPLE_ROWS=5
MAX_QUERY_ROWS=500
QUERY_TIMEOUT_SECONDS=15
SQL_RETRY_COUNT=2
SCHEMA_RETRIEVAL_TOP_K=8
LLM_INCLUDE_SAMPLE_DATA=true
LLM_SANITIZE_SAMPLE_DATA=true

# -----------------------------
# Logging
# -----------------------------
LOG_LEVEL=INFO
LOG_FORMAT=text
```

> ⚠️ **Importante (production)**: Guardar `METADATA_ENCRYPTION_KEY` y `OPENAI_API_KEY` en **secrets manager** del cloud (AWS Secrets Manager, GCP Secret Manager, Azure Key Vault, Doppler, Infisical, SOPS). **NUNCA** commitear el `.env` con valores reales a Git (ya está en `.gitignore` por defecto, pero verificar).

---

## 1) Ejecución Local (desarrollo)

Paso a paso estándar después de clonar el repo:

```bash
# 1. Instalar uv (solo 1 vez por máquina)
# Linux / macOS
curl -LsSf https://astral.sh/uv/install.sh | sh
# Windows PowerShell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"

# 2. Sincronizar venv + dependencies (main + dev)
uv sync --dev

# 3. Copiar .env.example a .env y completar:
cp .env.example .env
# Ahora edita .env y completa OPENAI_API_KEY y METADATA_ENCRYPTION_KEY

# 4. Asegurarse que PostgreSQL tenga la metadata db con pgvector
psql -h localhost -U postgres -c "CREATE DATABASE db_agent_metadata;"
psql -h localhost -U postgres -d db_agent_metadata -c "CREATE EXTENSION IF NOT EXISTS vector;"

# 5. Alembic migrations (metadata DB)
uv run alembic upgrade head

# 6. Correr uvicorn modo DEV (auto-reload)
# En algunos Windows el puerto 8000 se suele quedar en TIME_WAIT/huérfano; si pasa, usar 8001
uv run uvicorn app.main:app --reload --port 8001
```

Abrir:
- UI 3 pestañas (Conexión / Conocimiento / Chat): `http://127.0.0.1:8001/ui`
- Swagger: `http://127.0.0.1:8001/docs`
- Redoc: `http://127.0.0.1:8001/redoc`
- OpenAPI JSON: `http://127.0.0.1:8001/openapi.json`
- Health completo: `http://127.0.0.1:8001/api/v1/health`

---

## 2) Deploy en servidor (producción)

Hay 3 opciones comunes (en orden de mantenimiento creciente):

---

### Opción A — VPS Linux (Ubuntu / Debian / RHEL) con **`uvicorn + systemd + nginx`** (más usual)

#### Paso 1 — Preparar servidor

```bash
# --- Como root ---
apt-get update -y && apt-get upgrade -y
apt-get install -y build-essential curl wget git ca-certificates gnupg lsb-release sudo ufw software-properties-common
timedatectl set-timezone America/Argentina/Buenos_Aires   # o tu timezone

# PostgreSQL 16 (ejemplo para Ubuntu 22.04/24.04 LTS)
sudo sh -c 'echo "deb https://apt.postgresql.org/pub/repos/apt $(lsb_release -cs)-pgdg main" > /etc/apt/sources.list.d/pgdg.list'
wget --quiet -O - https://www.postgresql.org/media/keys/ACCC4CF8.asc | sudo apt-key add -
apt-get update -y
apt-get install -y postgresql-16 postgresql-contrib postgresql-16-pgvector
systemctl enable --now postgresql

# Nginx
apt-get install -y nginx
systemctl enable --now nginx

# Firewall mínimo
ufw default deny incoming
ufw default allow outgoing
ufw allow 22/tcp
ufw allow 80/tcp
ufw allow 443/tcp
ufw --force enable
```

#### Paso 2 — Crear usuario de servicio

```bash
sudo useradd --create-home --shell /bin/bash dbagent
sudo usermod -aG sudo dbagent
sudo su - dbagent
```

#### Paso 3 — Clonar repo y preparar secrets

```bash
cd /home/dbagent
git clone git@github.com:tu-org/db-agent-tp-austral.git   # o https://...
cd db-agent-tp-austral

# Instalar uv para el usuario
curl -LsSf https://astral.sh/uv/install.sh | sh
. $HOME/.local/bin/env   # o cerrar sesión y volver a entrar

# Sincronizar SOLO dependencias de runtime (no dev) en production
uv sync    # sin --dev

# Copiar plantilla env; COMPLETAR con secrets reales
cp .env.example .env
chmod 600 .env
# Editá .env y poné:
#   - OPENAI_API_KEY con key real de producción
#   - METADATA_ENCRYPTION_KEY generado UNA vez (ejecutar: uv run python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")
#   - METADATA_DB_URL con user/password del servicio postgres metadata (NO postgres superuser)
#   - DEBUG=false
#   - LOG_FORMAT=json
#   - APP_ENV=production
```

#### Paso 4 — Crear metadata DB + pgvector + usuario target RO

```bash
# --- Como usuario postgres ---
sudo -u postgres psql
```

```sql
-- Metadata db
CREATE DATABASE db_agent_metadata;
\c db_agent_metadata
CREATE EXTENSION IF NOT EXISTS vector;
CREATE ROLE db_agent_meta NOINHERIT LOGIN PASSWORD 'CAMBIAME_password_seguro';
GRANT CONNECT ON DATABASE db_agent_metadata TO db_agent_meta;
GRANT ALL ON SCHEMA public TO db_agent_meta;  -- necesita crear tablas/extensions en migrations
ALTER ROLE db_agent_meta SET search_path TO public;

-- Target (business) DB db ejemplo: lecatex
\c lecatex
CREATE ROLE db_agent_ro NOINHERIT LOGIN PASSWORD 'CAMBIAME_otro_password_seguro';
GRANT CONNECT ON DATABASE lecatex TO db_agent_ro;
GRANT USAGE ON SCHEMA public TO db_agent_ro;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO db_agent_ro;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO db_agent_ro;
ALTER ROLE db_agent_ro SET statement_timeout = '15s';
ALTER ROLE db_agent_ro SET idle_in_transaction_session_timeout = '30s';
```

Ahora en el `.env` cambiar `METADATA_DB_URL` a:
```
METADATA_DB_URL=postgresql+asyncpg://db_agent_meta:password_seguro@127.0.0.1:5432/db_agent_metadata
```

#### Paso 5 — Correr migrations

```bash
cd /home/dbagent/db-agent-tp-austral
# Opcional: actualizar alembic.ini para coincidir el string de metadata db
uv run alembic upgrade head
```

#### Paso 6 — Systemd unit para uvicorn (producción — 4 workers)

Crear `/etc/systemd/system/dbagent.service` como root:

```ini
[Unit]
Description=DB Agent TP Austral - Uvicorn ASGI
After=network.target postgresql.service

[Service]
Type=simple
User=dbagent
Group=dbagent
WorkingDirectory=/home/dbagent/db-agent-tp-austral
Environment=PATH=/home/dbagent/.local/bin:/home/dbagent/db-agent-tp-austral/.venv/bin:/usr/local/bin:/usr/bin:/bin
# Cargar env desde el .env del proyecto (alternativa 1 de secrets; alternativa 2: usar EnvironmentFile=... con 600 root)
EnvironmentFile=-/home/dbagent/db-agent-tp-austral/.env
# Workers = ( 2 * CPUs ) + 1. Para 1 vCPU = 2 workers; 2 vCPU = 4 workers
ExecStart=/home/dbagent/.local/bin/uv run uvicorn app.main:app \
    --host 127.0.0.1 \
    --port 8000 \
    --workers 4 \
    --loop uvloop \
    --http httptools \
    --backlog 1024 \
    --timeout-keep-alive 30 \
    --log-level info
# Recargar workers después de procesar N requests (evita memory leaks):
# ExecStart=... --max-requests 2000 --max-requests-jitter 200
Restart=always
RestartSec=3
StandardOutput=journal
StandardError=journal
SyslogIdentifier=dbagent
LimitNOFILE=65536
LimitNPROC=16384

# Seguridad hardening en systemd (reduces attack surface de 0-days)
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=read-only
ReadWritePaths=/home/dbagent/db-agent-tp-austral
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true
PrivateDevices=true
RestrictRealtime=true
MemoryDenyWriteExecute=true
LockPersonality=true

[Install]
WantedBy=multi-user.target
```

Habilitar y arrancar:

```bash
sudo systemctl daemon-reload
sudo systemctl enable dbagent.service
sudo systemctl restart dbagent.service
sudo systemctl status dbagent.service
```

Logs en vivo:
```bash
sudo journalctl -u dbagent.service -f --output=cat   # -o verbose si querés metadata
```

#### Paso 7 — Nginx reverse proxy + Let's Encrypt (HTTPS)

Crear `/etc/nginx/sites-available/dbagent.conf`:

```nginx
server {
    listen 80;
    server_name db-agent.tu-dominio.com.ar;

    location = /.well-known/acme-challenge/ {
        root /var/www/certbot;
        try_files $uri =404;
    }

    location / {
        return 301 https://$host$request_uri;
    }
}
server {
    listen 443 ssl http2;
    server_name db-agent.tu-dominio.com.ar;

    ssl_certificate /etc/letsencrypt/live/db-agent.tu-dominio.com.ar/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/db-agent.tu-dominio.com.ar/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_prefer_server_ciphers on;
    ssl_ciphers "ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-RSA-AES128-GCM-SHA256:ECDHE-ECDSA-AES256-GCM-SHA384:ECDHE-RSA-AES256-GCM-SHA384";
    ssl_session_cache shared:SSL:10m;
    ssl_session_timeout 10m;

    # Client body size: la app recibe JSONs de hasta ~1 MB; 10 MB es safe
    client_max_body_size 10m;
    client_body_timeout 60s;
    client_header_timeout 30s;

    # Timeouts ASGI (para LLM calls lentos, hasta 120s)
    proxy_connect_timeout 10s;
    proxy_send_timeout 180s;
    proxy_read_timeout 180s;

    gzip on;
    gzip_types text/plain text/css application/json application/javascript text/javascript text/xml application/xml;
    gzip_min_length 1024;

    location / {
        proxy_set_header Host $http_host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Forwarded-Host $host;
        proxy_set_header X-Forwarded-Port $server_port;

        # SSE / polling discovery status (no caché, no buffering, keep alive)
        proxy_http_version 1.1;
        proxy_set_header Connection "";
        proxy_buffering off;
        proxy_cache off;
        chunked_transfer_encoding off;

        proxy_pass http://127.0.0.1:8000;
    }
}
```

Activar:

```bash
sudo mkdir -p /var/www/certbot
sudo ln -sf /etc/nginx/sites-available/dbagent.conf /etc/nginx/sites-enabled/dbagent.conf
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t
sudo systemctl restart nginx

# Obtener certificado Let's Encrypt (si el dominio apunta a la VPS en DNS)
sudo apt-get install -y certbot python3-certbot-nginx
sudo certbot --nginx -d db-agent.tu-dominio.com.ar --non-interactive --agree-tos -m tu-correo@dominio.com.ar
```

#### Paso 8 — Smoke test de producción

```bash
# Health (devuelve 200 si metadata DB OK)
curl -sS https://db-agent.tu-dominio.com.ar/api/v1/health | jq .

# Smoke chat - sin necesidad de UI
# Primero registras una conexión target:
curl -sS https://db-agent.tu-dominio.com.ar/api/v1/database/connect \
  -H "Content-Type: application/json" \
  -d '{
    "database_type": "postgresql",
    "host": "127.0.0.1",
    "port": 5432,
    "database_name": "lecatex",
    "username": "db_agent_ro",
    "password": "CAMBIAME_otro_password_seguro",
    "schema": "public"
  }'
# guardas el connection_id UUID, luego:
curl -sS -X POST https://db-agent.tu-dominio.com.ar/api/v1/database/discover \
  -H "Content-Type: application/json" \
  -d '{"connection_id": "<UUID>", "force": false}'
# poll status...
```

---

### Opción B — Deploy en contenedor (Docker / Podman)

> El proyecto no trae un `Dockerfile` pre-armado por defecto. Ejemplo mínimo recomendado:

Crea `Dockerfile` en la raíz del repo:

```dockerfile
# syntax=docker/dockerfile:1.7
FROM python:3.12-slim-bookworm AS builder
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy \
    POETRY_NO_INTERACTION=1
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates build-essential git \
    && rm -rf /var/lib/apt/lists/*
# Instalar uv globalmente
RUN curl -LsSf https://astral.sh/uv/install.sh | sh && mv /root/.local/bin/uv /usr/local/bin/uv
COPY pyproject.toml uv.lock* README.md alembic.ini ./
COPY app ./app
COPY alembic ./alembic
# Runtime dependencies only (no dev)
RUN uv sync --no-dev --frozen

FROM python:3.12-slim-bookworm AS runtime
WORKDIR /app
RUN groupadd --gid 10001 app && useradd --uid 10001 --gid 10001 --shell /bin/bash --home-dir /app app
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates tzdata curl \
    && rm -rf /var/lib/apt/lists/*
COPY --from=builder /app /app
ENV VIRTUAL_ENV=/app/.venv PATH="/app/.venv/bin:$PATH"
RUN chown -R app:app /app
USER app
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/api/v1/health/ready || exit 1
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", \
     "--workers", "2", "--loop", "uvloop", "--http", "httptools", \
     "--log-level", "info", "--timeout-keep-alive", "30", \
     "--proxy-headers", "--forwarded-allow-ips", "*"]
```

Luego `docker buildx build -t db-agent-tp-austral:v0.1.0 .` y ejecutar:

```bash
# Cargar .env como environment file (nunca con --build-arg de secrets)
docker run --name dbagent \
  -p 8000:8000 \
  --restart unless-stopped \
  --memory="1g" --cpus="1.0" \
  --read-only --tmpfs /tmp:rw,size=128m \
  --mount type=tmpfs,destination=/app/.venv/lib/python3.12/site-packages,readonly=false \
  --env-file /etc/dbagent/.env \
  db-agent-tp-austral:v0.1.0
```

> Nota: antes del `CMD` de uvicorn, para aplicar migrations automáticamente al container startup, podés reemplazar el `CMD` por un script bash tipo `entrypoint.sh` con `uv run alembic upgrade head && exec "$@"`.

---

### Opción C — PaaS (Vercel no; recomendados: Render / Fly.io / Railway / Heroku)

- **No usar Vercel serverless para discovery**: el discovery es un task async ~40s que supera lambda timeouts. Mejor cualquier runtime long-running (web service).
- Steps comunes:
  1. Conectás el repo.
  2. Setear variables de entorno en la plataforma (incluyendo `OPENAI_API_KEY` y `METADATA_ENCRYPTION_KEY`).
  3. Build command: `pip install uv && uv sync --no-dev --frozen && uv run alembic upgrade head` (o build separate + release command).
  4. Start command: `uv run uvicorn app.main:app --host 0.0.0.0 --port $PORT --workers 2 --proxy-headers`.
  5. Proporcionar una PostgreSQL **externa** con `pgvector` (ej: Supabase, Neon, Aiven, Render Postgres con pgvector). La `METADATA_DB_URL` tiene que apuntar ahí.

---

## 3) Cheatsheet operativo (daily)

```bash
# Levantar dev server
uv run uvicorn app.main:app --reload --port 8001

# Levantar production server (1 worker, sin reload)
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 4 --log-level info

# Aplicar migrations
uv run alembic upgrade head

# Generar nueva migration
uv run alembic revision --autogenerate -m "add table x field y"

# Correr tests unit
uv run pytest -v tests/unit

# Regenerar Fernet key (nunca correr en prod después del deploy inicial si tenés conexiones guardadas!)
uv run python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

---

## 4) URLs públicas (después de levantar)

| Recurso | URL local dev | URL producción ejemplo |
|---|---|---|
| UI 3 tabs (Conexión / Mapa / Chat) | `http://127.0.0.1:8001/ui` | `https://db-agent.tu-dominio.com.ar/ui` |
| Home banner | `http://127.0.0.1:8001/` | `https://db-agent.tu-dominio.com.ar/` |
| Health completo | `http://127.0.0.1:8001/api/v1/health` | `.../api/v1/health` |
| Health readiness | `http://127.0.0.1:8001/api/v1/health/ready` | `.../api/v1/health/ready` |
| OpenAPI (Swagger) | `http://127.0.0.1:8001/docs` | `.../docs` |
| Redoc | `http://127.0.0.1:8001/redoc` | `.../redoc` |

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
