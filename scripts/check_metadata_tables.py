from __future__ import annotations

import asyncio

import asyncpg


async def main() -> None:
    cfg = {
        "host": "127.0.0.1",
        "port": 5433,
        "user": "postgres",
        "password": "admin",
        "database": "db_agent_metadata",
    }
    conn = await asyncpg.connect(**cfg)
    try:
        rows = await conn.fetch(
            "SELECT table_name FROM information_schema.tables WHERE table_schema='public' ORDER BY 1"
        )
        print("Tables in db_agent_metadata.public:", len(rows))
        # Also count alembic_version
        alembic = await conn.fetchval("SELECT version_num FROM alembic_version LIMIT 1")
        print("Alembic version:", alembic)
        for r in rows:
            print("  -", r["table_name"])
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
