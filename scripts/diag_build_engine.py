from __future__ import annotations

import asyncio
import sys

sys.path.insert(0, ".")

from app.repositories.database_repository import DatabaseConnectionRepository
from app.services.database_connection_service import DatabaseConnectionService
from app.database.session import get_async_session_factory, init_metadata_db
from app.database.connection import create_engine_from_config
from app.database.connection import DatabaseConfig


async def main():
    await init_metadata_db(verify_connection=True)
    connection_service = DatabaseConnectionService(DatabaseConnectionRepository())
    sf = get_async_session_factory()
    async with sf() as session:
        lst = await connection_service.list(session)
        print("Connections:")
        for c in lst:
            print(f" - id={c.id} host={c.host} port={c.port} user={c.username} db={c.database_name} status={c.status}")
        if not lst:
            print("No connections!")
            return
        chosen = lst[0]
        cid = chosen.id
        print("\nUsing CID =", cid)
        # Discover_schema path: get(decrypt_password=True) + build_engine_for
        meta = await connection_service.get(session, cid, decrypt_password=True)
        if meta is None:
            print("NOT FOUND")
            return
        print("meta.encrypted_password type:", type(meta.encrypted_password), "len:", len(meta.encrypted_password) if meta.encrypted_password else 0)
        print("Decoded password preview:", meta.encrypted_password.decode("utf-8", errors="replace") if meta.encrypted_password else "NONE")
        print("meta.host =", meta.host, "port =", meta.port, "user =", meta.username, "db =", meta.database_name, "schema =", meta.schema)

        print("\n[1] Running connection_service.build_engine_for(meta, pool_size=3, max_overflow=8, pool_recycle=1200)...")
        try:
            engine, cfg = await connection_service.build_engine_for(
                meta, pool_size=3, max_overflow=8, pool_recycle=1200
            )
            print(" OK build_engine_for -> engine =", engine)
            print(" engine.url =", str(engine.url))
            from sqlalchemy import text
            async with engine.connect() as conn:
                r = await conn.execute(text("SELECT current_database(), current_user, inet_server_addr(), inet_server_port(), version()"))
                print(" DB verify row:", r.one())
            await engine.dispose()
        except Exception as e:
            print(" FAIL build_engine_for:", type(e).__name__, str(e)[:500])

        print("\n[2] Direct DBConfig mirror: DatabaseConfig(host=127.0.0.1, port=5433, username=postgres, password=admin, db=lecatex)")
        cfg2 = DatabaseConfig(
            database_type="postgresql",
            host="127.0.0.1",
            port=5433,
            database_name="lecatex",
            username="postgres",
            password="admin",
            schema=meta.schema or "public",
        )
        try:
            engine2 = await create_engine_from_config(cfg2, pool_size=1, max_overflow=2, pool_recycle=1200)
            print(" OK direct create_engine_from_config -> engine =", engine2)
            from sqlalchemy import text
            async with engine2.connect() as conn:
                r = await conn.execute(text("SELECT current_database(), current_user"))
                print(" DB verify row:", r.one())
            await engine2.dispose()
        except Exception as e:
            print(" FAIL direct create_engine_from_config:", type(e).__name__, str(e)[:500])


if __name__ == "__main__":
    asyncio.run(main())
