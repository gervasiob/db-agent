from __future__ import annotations

import asyncio
import traceback

import asyncpg


async def create_metadata_db() -> None:
    # First connect to maintenance DB ("postgres") to create db_agent_metadata
    cfg_maintenance = {
        "host": "127.0.0.1",
        "port": 5433,
        "user": "postgres",
        "password": "admin",
        "database": "postgres",
    }
    print("[1/4] Connecting to maintenance DB 'postgres'...")
    try:
        conn = await asyncpg.connect(**cfg_maintenance)
    except Exception as e:
        print("   fallback connecting to 'template1' because 'postgres' DB missing? err:", type(e).__name__, str(e)[:200])
        cfg_maintenance["database"] = "template1"
        conn = await asyncpg.connect(**cfg_maintenance)

    await conn.execute("COMMIT")
    exists = await conn.fetchval(
        "SELECT 1 FROM pg_database WHERE datname=$1", "db_agent_metadata"
    )
    if not exists:
        print(f"   Creating database db_agent_metadata ...")
        try:
            await conn.execute("CREATE DATABASE db_agent_metadata")
            print("   OK created.")
        except Exception as e:
            print("   Warning (may already exist?):", type(e).__name__, str(e)[:300])
    else:
        print("   db_agent_metadata already exists.")
    await conn.close()

    # Now connect to db_agent_metadata and CREATE EXTENSION vector
    cfg_meta = {
        "host": "127.0.0.1",
        "port": 5433,
        "user": "postgres",
        "password": "admin",
        "database": "db_agent_metadata",
    }
    print("\n[2/4] Enabling pgvector extension on db_agent_metadata ...")
    conn2 = await asyncpg.connect(**cfg_meta)
    try:
        await conn2.execute("CREATE EXTENSION IF NOT EXISTS vector")
        row = await conn2.fetchrow("SELECT extname, extversion FROM pg_extension WHERE extname='vector'")
        print("   installed version:", dict(row) if row else "NOT INSTALLED")
    finally:
        await conn2.close()

    # Also enable vector on target lecatex (so target could eventually serve as metadata too)
    print("\n[3/4] Enabling pgvector extension on target lecatex ...")
    cfg_target = {
        "host": "127.0.0.1", "port": 5433, "user": "postgres", "password": "admin",
        "database": "lecatex",
    }
    conn3 = await asyncpg.connect(**cfg_target)
    try:
        await conn3.execute("CREATE EXTENSION IF NOT EXISTS vector")
        row = await conn3.fetchrow("SELECT extname, extversion FROM pg_extension WHERE extname='vector'")
        print("   installed version:", dict(row) if row else "NOT INSTALLED")
    finally:
        await conn3.close()

    # Also ensure the app-specific DB schema inside db_agent_metadata won't hit issues:
    # make sure "public" schema is available (it is by default)
    print("\n[4/4] Listing schemas on db_agent_metadata:")
    conn4 = await asyncpg.connect(**cfg_meta)
    try:
        rows = await conn4.fetch(
            "SELECT schema_name FROM information_schema.schemata ORDER BY 1"
        )
        for r in rows[:20]:
            print("  -", r["schema_name"])
    finally:
        await conn4.close()


if __name__ == "__main__":
    try:
        asyncio.run(create_metadata_db())
        print("\n✅ Setup OK. Metadata DB ready.")
    except Exception as e:
        traceback.print_exc()
        print("\n❌ Setup failed:", e)
