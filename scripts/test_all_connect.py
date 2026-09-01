from __future__ import annotations

import json

import httpx

BASE = "http://127.0.0.1:8001/api/v1"
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
    print("STEP 1/3: test-connection")
    print("=" * 60)
    with httpx.Client(timeout=60) as client:
        r1 = client.post(BASE + "/database/test-connection", json=PAYLOAD)
        print("HTTP", r1.status_code, "· x-request-id:", r1.headers.get("x-request-id"))
        try:
            d1 = r1.json()
            print(json.dumps(d1, indent=2, ensure_ascii=False)[:1500])
        except Exception:
            print(r1.text[:1500])

        print()
        print("=" * 60)
        print("STEP 2/3: connect (persist)")
        print("=" * 60)
        r2 = client.post(BASE + "/database/connect", json=PAYLOAD)
        print("HTTP", r2.status_code, "· x-request-id:", r2.headers.get("x-request-id"))
        try:
            d2 = r2.json()
            print(json.dumps(d2, indent=2, ensure_ascii=False)[:2500])
        except Exception:
            print(r2.text[:2500])

        print()
        print("=" * 60)
        print("STEP 3/3: list connections")
        print("=" * 60)
        try:
            r3 = client.get(BASE + "/database/connections")
            print("HTTP", r3.status_code)
            print(json.dumps(r3.json(), indent=2, ensure_ascii=False)[:2000])
        except Exception as e:
            print("ERR", e)


if __name__ == "__main__":
    main()
