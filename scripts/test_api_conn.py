from __future__ import annotations

import json

import httpx

BASE = "http://127.0.0.1:8000/api/v1"
PAYLOAD = {
    "database_type": "postgresql",
    "host": "127.0.0.1",
    "port": 5433,
    "database_name": "lecatex",
    "username": "postgres",
    "password": "admin",
    "schema": "public",
    "ssl_mode": None,
}


def main() -> None:
    print("POST /database/test-connection to", BASE)
    print("  host=127.0.0.1 port=5433 db=lecatex user=postgres")
    with httpx.Client(timeout=30) as client:
        r = client.post(BASE + "/database/test-connection", json=PAYLOAD)
        print("\nHTTP status:", r.status_code)
        print("Headers:", dict(r.headers))
        try:
            data = r.json()
            print("Body (JSON):", json.dumps(data, indent=2, ensure_ascii=False)[:2000])
        except Exception:
            print("Body (TEXT):", r.text[:2000])


if __name__ == "__main__":
    main()
