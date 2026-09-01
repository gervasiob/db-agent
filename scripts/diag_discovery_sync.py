import asyncio
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.agents.database_agent import DatabaseAgent
from app.database.session import init_metadata_db, get_async_session_factory
from app.core.config import settings
from uuid import UUID

CONN_ID = UUID("956323d6-3844-4240-93cb-addc5441712d")

async def main():
    await init_metadata_db()
    agent = DatabaseAgent(session_factory=get_async_session_factory())
    print(f"[1] Running discovery SYNC (no BG) for {CONN_ID}")
    session_factory = get_async_session_factory()
    async with session_factory() as session:
        req = await agent._connection_request_from_stored(session, CONN_ID)
        print(f"[2] Got request host={req.host} port={req.port} db={req.database_name} user={req.username}")
    try:
        kmap, ctx_status = await agent.discovery_pipeline.run(CONN_ID, req, force_refresh=True)
        print(f"[3] FINISHED! ctx_status={ctx_status}")
        print(f"[4] kmap tables={len(kmap.tables)} domains={len(kmap.domains)} entities={len(kmap.entities)} concepts={len(kmap.concepts)} metrics={len(kmap.metrics)} dimensions={len(kmap.dimensions)}")
    except Exception as e:
        import traceback
        print("[FAIL]")
        traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())
