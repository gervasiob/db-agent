from __future__ import annotations

import asyncio
import json
import sys
import time

import httpx

BASE = "http://127.0.0.1:8000/api/v1"
NEW_BODY = {
    "database_type": "postgresql",
    "host": "127.0.0.1",
    "port": 5433,
    "database_name": "lecatex",
    "username": "postgres",
    "password": "admin",
    "schema": "public",
    "ssl_mode": None,
}


async def main() -> None:
    async with httpx.AsyncClient(timeout=1200) as client:
        r = await client.get(f"{BASE}/database/connections")
        for c in (r.json() or []):
            cid = c["id"]
            print("Deleting old conn", cid)
            dr = await client.delete(f"{BASE}/database/connections/{cid}")
            print(" DELETE", cid, "HTTP", dr.status_code, dr.text[:100])

        print()
        print("=== Create NEW connection host=127.0.0.1 ===")
        r = await client.post(f"{BASE}/database/connect", json=NEW_BODY)
        print("HTTP", r.status_code)
        conn = r.json()
        print(json.dumps(conn, indent=2, default=str))
        CID = conn["connection_id"]

        print()
        print("=== Verify host is correct ===")
        lst = (await client.get(f"{BASE}/database/connections")).json()
        for c in lst:
            print(" - id=%s host=%s status=%s" % (c["id"], c["host"], c["status"]))

        print()
        print("=== POST /database/discover force=True ===")
        r = await client.post(
            f"{BASE}/database/discover",
            json={"connection_id": CID, "force": True},
            timeout=30,
        )
        print("HTTP", r.status_code, json.dumps(r.json(), indent=2))

        print()
        start = time.time()
        last_cs = -1
        final_st = {}
        for attempt in range(1800):
            r = await client.get(
                f"{BASE}/database/discovery/status",
                params={"connection_id": CID},
                timeout=30,
            )
            st = r.json()
            cs = st.get("completed_steps", 0)
            ts = st.get("total_steps", 19)
            status_val = st.get("status", "UNKNOWN")
            curr = st.get("current_step") or ""
            err = st.get("error_message")
            if cs != last_cs or attempt % 30 == 0:
                elapsed = int(time.time() - start)
                pct = int(100 * cs / max(1, ts))
                tag = ("step " + curr) if curr else ""
                print(
                    f"[{elapsed:>4}s] poll {attempt:>3}  "
                    f"status={status_val:<14}  steps={cs}/{ts} ({pct:>3}%) {tag}"
                )
                if err:
                    print("  LAST_ERROR:", err[:600])
                last_cs = cs
                sys.stdout.flush()
            final_st = st
            if status_val == "READY" or status_val == "ERROR":
                print("FINAL status:", status_val)
                break
            await asyncio.sleep(2)

        st = final_st
        print()
        print("=== Final knowledge-map ===")
        if st.get("status") == "READY":
            r = await client.get(
                f"{BASE}/database/knowledge-map",
                params={"connection_id": CID},
                timeout=120,
            )
            km = r.json()
            summary = {
                "context_version": km.get("context_version"),
                "domains": len(km.get("domains") or []),
                "entities": len(km.get("entities") or []),
                "concepts": len(km.get("concepts") or []),
                "metrics": len(km.get("metrics") or []),
                "dimensions": len(km.get("dimensions") or []),
                "tables": len(km.get("tables") or []),
                "terminology": len(km.get("terminology") or []),
                "relationships": len(km.get("relationships") or []),
            }
            print(json.dumps(summary, indent=2))
            print()
            print("=== Sample domains / entities / metrics ===")
            for d in (km.get("domains") or [])[:3]:
                print(" DOMAIN:", d.get("code"), d.get("name"), "tables=", d.get("tables"))
            for e in (km.get("entities") or [])[:3]:
                print(" ENTITY:", e.get("code"), e.get("name"), "primary_table=", e.get("primary_table"))
            for m in (km.get("metrics") or [])[:6]:
                print(" METRIC:", m.get("code"), m.get("name"), "agg=", m.get("aggregation"), "col=", m.get("source_column"))
            print()
            print("=== Context endpoint statuses ===")
            for path in ["domains", "entities", "concepts", "metrics", "dimensions"]:
                rp = await client.get(
                    f"{BASE}/context/{path}", params={"connection_id": CID}, timeout=30
                )
                items = rp.json()
                n = len(items) if isinstance(items, list) else "?"
                print(f" /context/{path}: HTTP {rp.status_code} count={n}")
            rp = await client.get(
                f"{BASE}/context/terminology", params={"connection_id": CID}, timeout=30
            )
            items = rp.json()
            n = len(items) if isinstance(items, list) else "?"
            print(f" /context/terminology: HTTP {rp.status_code} count={n}")
            rp = await client.get(
                f"{BASE}/context/versions", params={"connection_id": CID}, timeout=30
            )
            items = rp.json()
            n = len(items) if isinstance(items, list) else "?"
            print(f" /context/versions: HTTP {rp.status_code} count={n}")
            print()
            print("FINAL_CONNECTION_ID=" + CID)
        else:
            print("Status not READY:", st)


if __name__ == "__main__":
    asyncio.run(main())
