from __future__ import annotations

import asyncio
import json
import sys
import time

sys.path.insert(0, ".")

from fastapi.testclient import TestClient
from app.main import app


CID = "956323d6-3844-4240-93cb-addc5441712d"


def main() -> None:
    print("Starting TestClient(app) lifespan")
    with TestClient(app) as client:
        agent = app.state.agent
        print("Agent on state:", type(agent).__name__)
        print("agent.connection_service:", type(agent.connection_service).__name__)
        print("agent.discovery_pipeline:", type(agent.discovery_pipeline).__name__)
        print()

        # Pruebo start_discovery SÍNCRONO via API:
        print("POST /api/v1/database/discover body={connection_id: CID, force: True}")
        r = client.post(
            "/api/v1/database/discover",
            json={"connection_id": CID, "force": True},
        )
        print("HTTP", r.status_code, r.text[:300])
        print()

        print("GET /api/v1/database/discovery/status?connection_id=", CID)
        s0 = client.get("/api/v1/database/discovery/status", params={"connection_id": CID})
        print("HTTP", s0.status_code, json.dumps(s0.json(), indent=2, default=str)[:300])

        agent2 = app.state.agent
        print()
        print("Calling agent.start_discovery directly (await)...")
        import asyncio
        loop = asyncio.get_event_loop()
        started, status_state = loop.run_until_complete(agent2.start_discovery(CID, force=True))
        print(" DIRECT start_discovery ->", started, status_state)
        print()

        print("Now polling every 2s up to 3 minutes or READY/ERROR:")
        start = time.time()
        last_cs = -1
        for attempt in range(90):
            r = client.get("/api/v1/database/discovery/status", params={"connection_id": CID})
            st = r.json()
            cs = st.get("completed_steps", 0)
            ts = st.get("total_steps", 19)
            status_val = st.get("status", "UNKNOWN")
            curr = st.get("current_step") or ""
            err = st.get("error_message")
            if cs != last_cs or attempt % 15 == 0:
                elapsed = int(time.time() - start)
                pct = int(100 * cs / max(1, ts))
                tag = ("step " + curr) if curr else ""
                print(
                    f"[{elapsed:>4}s] poll {attempt:>3}  "
                    f"status={status_val:<14}  steps={cs}/{ts} ({pct:>3}%) {tag}"
                )
                if err:
                    print("  LAST_ERROR:", err[:700])
                last_cs = cs
                sys.stdout.flush()
            if status_val == "READY" or status_val == "ERROR":
                print("FINAL status:", status_val)
                break
            loop.run_until_complete(asyncio.sleep(2))

        print()
        print("=== Final connections / context endpoints ===")
        for path, params in [
            ("/api/v1/database/connections", None),
            ("/api/v1/database/knowledge-map", {"connection_id": CID}),
            ("/api/v1/context/versions", {"connection_id": CID}),
        ]:
            rp = client.get(path, params=params)
            print(path, "HTTP", rp.status_code)
            data = rp.json()
            if isinstance(data, list):
                print(" count list =", len(data))
            elif isinstance(data, dict):
                keys_show = {k: (len(v) if isinstance(v, list) else v) for k, v in list(data.items())[:12]}
                print(" keys:", keys_show)
            else:
                print(" resp:", str(data)[:150])


if __name__ == "__main__":
    main()
