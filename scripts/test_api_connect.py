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
    print("=" * 60)
    print("POST /database/connect  (persistir conexion en metadata DB)")
    print("=" * 60)
    with httpx.Client(timeout=60) as client:
        r = client.post(BASE + "/database/connect", json=PAYLOAD)
        print("HTTP status:", r.status_code)
        print("x-request-id:", r.headers.get("x-request-id"))
        try:
            data = r.json()
            print("Body (JSON):")
            print(json.dumps(data, indent=2, ensure_ascii=False)[:4000])
        except Exception:
            print("Body (TEXT):")
            print(r.text[:4000])


if __name__ == "__main__":
    main()
