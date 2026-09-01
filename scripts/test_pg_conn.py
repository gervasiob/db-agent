from __future__ import annotations

import asyncio
import traceback

import asyncpg


async def main() -> None:
    cfg = {
        "host": "127.0.0.1",
        "port": 5433,
        "user": "postgres",
        "password": "admin",
        "database": "lecatex",
        "timeout": 10,
        "command_timeout": 15,
    }
    print("Connecting to 127.0.0.1:5433 db=lecatex user=postgres...")
    try:
        conn = await asyncio.wait_for(asyncpg.connect(**cfg), timeout=12)
    except Exception as e:
        print("[FAIL] asyncpg could not connect:", type(e).__name__, str(e)[:500])
        traceback.print_exc(limit=2)
        return

    try:
        v = await conn.fetchval("SELECT version()")
        print("[OK] Connected. version:", str(v)[:80])

        schemas = await conn.fetch(
            """
            SELECT schema_name
            FROM information_schema.schemata
            WHERE schema_name NOT IN ('pg_catalog','information_schema')
            ORDER BY 1
            """
        )
        print("Schemas:", [r["schema_name"] for r in schemas])

        tables = await conn.fetch(
            """
            SELECT table_schema, table_name, table_type
            FROM information_schema.tables
            WHERE table_schema NOT IN ('pg_catalog','information_schema')
            ORDER BY 1, 2
            LIMIT 200
            """
        )
        print("Tables/views count:", len(tables))
        for t in tables[:30]:
            print("  -", f"{t['table_schema']}.{t['table_name']} [{t['table_type']}]")

        # Check pgvector extension installed (needed by metadata DB; also useful for target)
        ext = await conn.fetch(
            "SELECT extname, extversion FROM pg_extension WHERE extname='vector'"
        )
        print("pgvector installed in target lecatex?", bool(ext), ([dict(r) for r in ext] if ext else ""))
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
