import asyncio
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.repositories.context_repository import ContextRepository
from app.database.session import init_metadata_db, get_async_session_factory
from uuid import UUID
from app.models.context import DatabaseKnowledgeMap, RelationshipGraph

CONN_ID = UUID("956323d6-3844-4240-93cb-addc5441712d")

async def main():
    await init_metadata_db()
    repo = ContextRepository()
    sf = get_async_session_factory()
    async with sf() as sess:
        print(f"[1] Get latest context for {CONN_ID}")
        km = await repo.get_latest_context_version(sess, CONN_ID)
        print(f"type={type(km)}")
        if km is None:
            print("NONE (not found)")
            return
        print(f"id={km.id} version={km.context_version} status={km.status}")
        print(f"relationship_graph raw repr before construct: {type(km.relationship_graph)} {km.relationship_graph!r}")
        data = km.model_dump(mode="json")
        print(f"dump relationship_graph = {data['relationship_graph']!r}")
        try:
            km2 = DatabaseKnowledgeMap.model_validate(km.model_dump())
            print("re-validate OK")
            print(f"rg type={type(km2.relationship_graph)}")
        except Exception as e:
            print(f"VALIDATE FAIL: {e!s}")

if __name__ == "__main__":
    asyncio.run(main())
