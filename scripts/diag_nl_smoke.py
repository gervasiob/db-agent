import asyncio
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx
from uuid import UUID

CONN_ID = UUID("543c2e06-25ad-485c-bcc5-0a6d65a10c7e")
BASE = "http://127.0.0.1:8001/api/v1"

async def main():
    async with httpx.AsyncClient(timeout=120) as client:
        print("=== /context/versions ===")
        r = await client.get(f"{BASE}/context/versions", params={"connection_id": str(CONN_ID)})
        print(r.status_code, r.text[:300])

        print("\n=== /database/knowledge-map (counts) ===")
        r = await client.get(f"{BASE}/database/knowledge-map", params={"connection_id": str(CONN_ID)})
        if r.status_code == 200:
            km = r.json()
            print(f"tables={len(km.get('tables', []))} domains={len(km.get('domains', []))} entities={len(km.get('entities', []))} concepts={len(km.get('concepts', []))} metrics={len(km.get('metrics', []))} dimensions={len(km.get('dimensions', []))} terminology={len(km.get('terminology', []))}")
        else:
            print(r.status_code, r.text[:300])

        for ep in ["domains", "entities", "concepts", "metrics", "dimensions", "terminology"]:
            print(f"\n=== /context/{ep} ===")
            r = await client.get(f"{BASE}/context/{ep}", params={"connection_id": str(CONN_ID)})
            if r.status_code == 200:
                data = r.json()
                count = len(data) if isinstance(data, list) else data.get("count") if isinstance(data, dict) else "?"
                print(f"HTTP 200 count={count}")
            else:
                print(r.status_code, r.text[:200])

        print("\n=== NL query: listar 10 clientes con mas ordenes ===")
        r = await client.post(
            f"{BASE}/query",
            json={
                "question": "listar los 10 clientes con mayor cantidad de ordenes, mostrando cantidad de ordenes por cliente",
                "connection_id": str(CONN_ID),
            },
        )
        print(f"status={r.status_code}")
        if r.status_code != 200:
            print(r.text[:1000])
            return
        qr = r.json()
        plan = qr.get("semantic_plan") or qr.get("plan") or {}
        print(f"plan.steps: {len(plan.get('steps', plan.get('semantic_steps', [])))}")
        sql = qr.get("sql", "") or qr.get("generated_sql", "")
        print(f"SQL len={len(sql)} preview: {sql[:400]}")
        data = qr.get("data", []) or qr.get("rows", []) or qr.get("results", [])
        print(f"data rows={len(data)} sample: {data[:3]}")
        answer = qr.get("short_answer") or qr.get("answer") or qr.get("natural_answer")
        print(f"answer: {str(answer)[:500]}")

if __name__ == "__main__":
    asyncio.run(main())
